"""The three report routes: list, read, ask for one. The generation itself is `app.reports`.

WHAT THIS FILE IS RESPONSIBLE FOR, AND WHAT IT DELIBERATELY IS NOT

It owns the tenancy filter, the daily limit and the 202-then-poll shape. It owns no prompt, no
citation resolution and no arithmetic — all of that is `app.reports`, so the cron tick that
`docs/api-contract.md` specifies (and which no router implements yet) can generate the weekly
report by calling the same two functions without going through HTTP.

THREE THINGS WORTH READING BEFORE EDITING

**404, NEVER 403.** `GET /api/reports/{id}` filters on `household_id` in the same query that
filters on the id, so another household's report and a random UUID produce one byte-identical
response. There is no branch here that could tell them apart, which is the point: a 403 is an
existence oracle, and the only reliable way not to leak one is to never compute the fact.

**THE DAILY LIMIT IS CHECKED UNDER AN ADVISORY LOCK.** One shared family credential means two
people signed in on two phones, and "Generate" is a button they will both press. Without the
lock both requests read "none today" and the household buys two summaries; with it the second
request finds the lock held and gets the same 429 as the second attempt an hour later. Failed
reports do not consume the day's allowance — a family whose summary died on a transport error
must not be told to come back tomorrow.

**GENERATION IS DETACHED FROM THE REQUEST, AND IT HAS TO BE.** The full reasoning is in
`app.routers.imports._spawn_extraction`: on this Starlette version a `yield` dependency's
commit runs AFTER the response's background tasks, so a task that does its work inline opens a
second connection and cannot see the row it was spawned for. It would report cheerful success
against nothing. `_await_visible` waits for the insert to become readable before starting.

Nothing in here logs a heading, a sentence or a question. Ids, counts and money only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db import get_sessionmaker
from app.deps import CurrentHousehold, SessionDep
from app.errors import NotFoundError
from app.models import Report
from app.reports import (
    acquire_lock,
    client_sentence,
    create_report,
    floor_local_day,
    generate_report,
)
from app.schemas import UtcDatetime

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])

# The contract's limit: one on-demand report per household per day. The number is quoted to the
# family in the 429 body, so changing it changes what the app promises.
GENERATED_TODAY_DETAIL = "A report was already generated today."
IN_FLIGHT_DETAIL = "A summary is already being written. It will be ready in a minute."
# What a family is told about a run whose worker died. Written for them, not for an operator:
# there is nothing they did wrong and nothing to look up, only something to press again.
ABANDONED_DETAIL = "That summary was interrupted before it finished. Ask for a new one."

# How long a spawned job waits for the request that queued it to commit. Milliseconds in
# practice; the ceiling only matters when the request failed and will never commit at all.
# Same constants and the same reason as `app.routers.imports`.
COMMIT_WAIT_TRIES = 60
COMMIT_WAIT_SECONDS = 0.25

# A `running` report older than this was abandoned by a process that died mid-call, and must
# stop blocking the household's next attempt. Generation is tens of seconds; fifteen minutes is
# far past any real run and short enough that a family is not locked out for the afternoon.
STALE_RUN_AFTER = timedelta(minutes=15)

# Strong references to detached generation tasks. asyncio holds only a WEAK reference to a
# running task, so without this the collector may cancel a summary mid-call, at random.
_RUNNING: set[asyncio.Task[None]] = set()


class ReportListItem(BaseModel):
    """`GET /api/reports`. Deliberately not the full document: the list is a menu."""

    id: UUID
    title: str
    period_start: UtcDatetime
    period_end: UtcDatetime
    status: str
    # Null until the report is complete, which is exactly what the client polls on.
    generated_at: UtcDatetime | None = None


class ReportCitationOut(BaseModel):
    """Resolved server-side. A handle that did not resolve never reaches this model."""

    model_config = ConfigDict(extra="ignore")

    handle: str
    event_id: UUID
    kind: str
    occurred_at: UtcDatetime
    title: str


class ReportSectionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: str
    body_markdown: str
    citations: list[ReportCitationOut] = Field(default_factory=list)


class ReportOut(ReportListItem):
    summary: str = ""
    urgent_flag: bool = False
    urgent_reason: str | None = None
    sections: list[ReportSectionOut] = Field(default_factory=list)
    questions_for_the_doctor: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    error: str | None = None


class ReportAccepted(BaseModel):
    """202. The client polls `GET /api/reports/{report_id}` until the status is terminal."""

    report_id: UUID
    status: str


@router.post("/generate", status_code=202, response_model=ReportAccepted)
async def generate(
    session: SessionDep, ctx: CurrentHousehold, background: BackgroundTasks
) -> ReportAccepted:
    """Queue one summary. 202 immediately; the model call takes tens of seconds.

    The order here is not arbitrary. The lock comes first so the check and the insert are one
    atomic decision; the daily limit second so a refused request never reaches `create_report`;
    and `create_report` last because it is the thing that can raise `ThinRecordError` — a 422
    that must not be preceded by anything the family would have to undo.
    """
    if not await acquire_lock(session, ctx.id):
        raise HTTPException(status_code=429, detail=IN_FLIGHT_DETAIL)
    if await _already_generated_today(session, ctx.id, timezone=ctx.household.timezone):
        raise HTTPException(status_code=429, detail=GENERATED_TODAY_DETAIL)

    record = await create_report(session, ctx.household)
    background.add_task(_spawn_generation, ctx.id, record.id)
    return ReportAccepted(report_id=record.id, status=record.status)


@router.get("", response_model=list[ReportListItem])
async def list_reports(session: SessionDep, ctx: CurrentHousehold) -> list[ReportListItem]:
    """Newest first. Every row filtered on `ctx.id`; there is no RLS behind this."""
    rows = (
        await session.execute(
            sa.select(Report)
            .where(Report.household_id == ctx.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
        )
    ).scalars()
    return [
        ReportListItem(
            id=row.id,
            title=row.title,
            period_start=row.period_start,
            period_end=row.period_end,
            status=effective_status(row.status, row.created_at),
            generated_at=row.generated_at,
        )
        for row in rows
    ]


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(session: SessionDep, ctx: CurrentHousehold, report_id: UUID) -> ReportOut:
    """404 for a report that is missing OR belongs to another household — indistinguishably."""
    record = (
        await session.execute(
            sa.select(Report).where(Report.id == report_id, Report.household_id == ctx.id)
        )
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError()
    status = effective_status(record.status, record.created_at)
    return ReportOut(
        id=record.id,
        title=record.title,
        period_start=record.period_start,
        period_end=record.period_end,
        status=status,
        generated_at=record.generated_at,
        summary=record.summary or "",
        urgent_flag=record.urgent_flag,
        urgent_reason=record.urgent_reason,
        sections=_sections(record.sections),
        questions_for_the_doctor=_strings(record.questions_for_the_doctor),
        watch_items=_strings(record.watch_items),
        data_gaps=_strings(record.data_gaps),
        error=record.error or (ABANDONED_DETAIL if status != record.status else None),
    )


def effective_status(status: str, created_at: datetime, now: datetime | None = None) -> str:
    """Report an abandoned run as `failed`, because that is what it is.

    A redeploy in the middle of a generation leaves a `running` row that nothing will ever
    finish or fail — the process that owned it is gone. To the client that row is indefinite,
    and the client's rule for a non-terminal status is to POLL: the panel would sit on "reading
    the record" forever, every open tab asking every two seconds, for a summary that is not
    being written. So the row is projected, not mutated: a read at 03:00 must not perform a
    write, and the moment a straggler does finish it reports itself complete without anything
    having to undo a `failed` we wrote on its behalf.

    Read-time only, and `STALE_RUN_AFTER` is far past the tens of seconds a real generation
    takes, so a run in flight can never be projected as failed while it is still working.
    """
    if status not in ("pending", "running"):
        return status
    now = now or datetime.now(UTC)
    return "failed" if created_at < now - STALE_RUN_AFTER else status


def _sections(raw: Any) -> list[ReportSectionOut]:
    """Keep the sections that parse, drop the ones that do not, never fail the request.

    Tolerant on READ for the same reason `app.schemas._excerpts` is: `reports.sections` is
    JSONB written by whichever prompt version was live when the report was generated, and a
    family opening a summary from three months ago must get the six sections that still parse
    rather than a 500 for the one that does not.
    """
    if not isinstance(raw, list):
        return []
    kept: list[ReportSectionOut] = []
    for item in raw:
        try:
            kept.append(ReportSectionOut.model_validate(item))
        except ValidationError:
            log.warning("reports.section_dropped")  # no prose, ever — counts only
    return kept


def _strings(raw: Any) -> list[str]:
    """The three lists are JSONB, so a non-list or a non-string entry is possible in principle."""
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


async def _already_generated_today(
    session: SessionDep, household_id: UUID, *, timezone: str, now: datetime | None = None
) -> bool:
    """One per day, counted from midnight in the HOUSEHOLD's timezone, not UTC.

    A family in Sydney pressing the button after breakfast must not be told they already had
    today's report because UTC still says yesterday.

    `failed` reports are excluded: a summary that died on a transport error cost the family
    nothing and must not cost them the day. A `pending` or `running` one older than
    `STALE_RUN_AFTER` is excluded for the mirror-image reason — it belongs to a worker that is
    gone, and without that clause one crashed process locks a household out until midnight.
    """
    now = now or datetime.now(UTC)
    midnight = floor_local_day(now, ZoneInfo(timezone))
    abandoned = sa.and_(
        Report.status.in_(("pending", "running")),
        Report.created_at < now - STALE_RUN_AFTER,
    )
    return (
        await session.execute(
            sa.select(Report.id)
            .where(
                Report.household_id == household_id,
                Report.created_at >= midnight,
                Report.status != "failed",
                sa.not_(abandoned),
            )
            .limit(1)
        )
    ).first() is not None


async def _spawn_generation(household_id: UUID, report_id: UUID) -> None:
    """Detach the work from the request. See the module docstring — this is not gratuitous."""
    task = asyncio.create_task(_generate_in_background(household_id, report_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def _generate_in_background(household_id: UUID, report_id: UUID) -> None:
    """Mark it running, generate it, and record the outcome whatever happens.

    THE FAILURE PATH WRITES IN THE SAME TRANSACTION AS THE WORK, on purpose. Every way the
    gateway fails — a transport error, a truncated response, a content filter — writes its
    `llm_runs` row before raising, and those rows are what the monthly budget is a SUM over.
    Rolling them back would mean a household's most expensive day looked free. So the failure
    is marked on the session that already holds them and committed together with them.

    A DATABASE error poisons that session, and the mark cannot be written on it. That is what
    the second attempt at the bottom is for: without it the report sits at `running` and the
    panel polls a row that will never change.
    """
    maker = get_sessionmaker()
    if not await _await_visible(maker, household_id, report_id):
        # The request rolled back after all, so there is no report and nothing to generate.
        log.warning("report never committed household=%s report=%s", household_id, report_id)
        return

    async with maker() as session:
        await _set_status(session, household_id, report_id, "running")
        await session.commit()

    sentence: str | None = None
    async with maker() as session:
        try:
            await generate_report(session, household_id, report_id)
            await session.commit()
            return
        except Exception as exc:  # the job has no caller to raise to
            log.exception(
                "report generation failed household=%s report=%s", household_id, report_id
            )
            sentence = client_sentence(exc)
            try:
                await _set_status(session, household_id, report_id, "failed", error=sentence)
                await session.commit()
                return
            except Exception:
                await session.rollback()

    async with maker() as session:
        await _set_status(session, household_id, report_id, "failed", error=sentence)
        await session.commit()


async def _set_status(
    session: Any, household_id: UUID, report_id: UUID, status: str, *, error: str | None = None
) -> None:
    """Both filters, always. A background job is still tenant-scoped code."""
    await session.execute(
        sa.update(Report)
        .where(Report.id == report_id, Report.household_id == household_id)
        .values(status=status, error=error)
    )


async def _await_visible(maker: Any, household_id: UUID, report_id: UUID) -> bool:
    """Block until the `reports` row is readable from a fresh connection.

    Seeing the row is proof the whole transaction that wrote it committed, which is the
    precondition this job has and the one thing it must not assume.
    """
    for _ in range(COMMIT_WAIT_TRIES):
        async with maker() as session:
            found = (
                await session.execute(
                    sa.select(Report.id).where(
                        Report.id == report_id, Report.household_id == household_id
                    )
                )
            ).first()
        if found is not None:
            return True
        await asyncio.sleep(COMMIT_WAIT_SECONDS)
    return False
