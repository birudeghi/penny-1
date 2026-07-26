"""Report generation: a year of the family's record, read back as one page for a doctor.

This is the second thing in the product that costs money and the first that a stranger reads.
A family opens the panel in the car park, prints it or reads it off a phone, and hands the
sentences in it to somebody with fifteen minutes and no other view of the last twelve months.
So three properties are load-bearing, and each one is a mechanism here rather than a line in
the prompt:

**EVERY CLAIM POINTS AT AN ENTRY THE FAMILY CAN OPEN.** The model writes chunk-local handles
(`E1`, `E2`) and never a UUID — models transpose long hex runs, and `docs/api-contract.md`
spells out why the field is called a *handle*. `resolve_sections` maps them back to real
`events.id` values on the way out, DROPS the ones that do not resolve, and — this is the part
that is easy to forget — also strips the dropped handle from `body_markdown`, because a `[E7]`
left in the prose with no chip under it is a dead citation the family cannot check. A section
left with no resolvable citation at all is deleted: an uncheckable claim in a document a GP
will act on is worse than a shorter document.

**NUMBERS ARE COUNTED IN SQL AND HANDED TO THE MODEL.** `_count_record` does the group-bys and
renders them into `<counts>`; the prompt forbids the model from counting anything itself. A
language model asked to count sixty entries will produce a plausible number, and "the fourth
week running" is exactly the kind of sentence a doctor changes a prescription over.

**NOTHING HERE DIAGNOSES.** That one genuinely is the prompt's job, but the shape helps: the
model is given extracted events — third-person, factual, already stripped of interpretation by
`extract.md` — and never the raw messages, so it has nothing to over-read.

WHY THE WORK IS DETACHED FROM THE REQUEST

The call takes tens of seconds at `medium` effort over a year of events. `POST
/api/reports/generate` therefore writes a `pending` row, answers 202, and the generation runs
in a task that outlives the request — the same shape, and for the same non-obvious reason, as
`app.routers.imports._spawn_extraction`: a `yield` dependency's commit happens *after* the
response's background tasks, so a task that works inline opens a second connection and cannot
see the row it was spawned for. `_await_visible` is what closes that.

A RECORD TOO THIN TO SUMMARISE IS REFUSED BEFORE IT COSTS ANYTHING. `ThinRecordError` is
raised at creation, so there is no row to poll, no spend, and the panel gets a sentence
immediately instead of a spinner that ends in an empty document.

NEVER LOG THE REPORT. Headings, prose, questions and quotes are the family's health
information; this module logs ids, counts and money, exactly like `app.llm.gateway`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings
from app.errors import PennyError, ValidationError

# `_HouseholdBudget` is the SAME budget guard extraction uses, imported rather than re-derived.
# It sums `llm_runs.cost_usd` over one rolling 30-day window, so sharing it is what makes a
# report and an extraction spend out of ONE household allowance instead of two ceilings nobody
# reconciled. Reaching for a private name is the smaller sin here than copying the query.
from app.extraction.service import _HouseholdBudget
from app.llm.gateway import CallSpec, Effort, LLMGateway
from app.llm.prompts import REPORT_PROMPT, REPORT_PROMPT_VERSION
from app.llm.recorder import DbRunRecorder
from app.models import Event, Household, Report

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# How far back an on-demand summary reads. Twelve months, because that is the span a GP asks
# about ("how has she been this year?") and the span the designer's panel is headed with. The
# period is CLAMPED to the first entry in the record, so a household three weeks old is titled
# for three weeks rather than being promised a year it does not have.
REPORT_PERIOD = timedelta(days=365)

# The most entries one call reads. 200 entries is roughly 7k tokens of `<record>`; past that
# the marginal entry is a line the model skims and pays for. When the period holds more, the
# most RECENT 200 are read and the truncation is written into `data_gaps` — an unstated
# truncation is the failure mode here, not the truncation itself.
REPORT_MAX_EVENTS = 200

# Scheduled appointments are why a family books the appointment they are walking into, so the
# ones still ahead are rendered too — cited like anything else, capped because a household that
# books six months out must not push its own history out of the prompt.
REPORT_MAX_UPCOMING = 20

# Below this there is nothing to read back. Three entries is not a year of care; it is a chat
# that has just been connected, and a summary of it would be a page of `data_gaps` presented as
# a clinical document.
MIN_EVENTS_FOR_REPORT = 3

# `medium`, unlike extraction's `low`. Extraction is span-finding over one afternoon of chat;
# this is synthesis across a year, choosing what a doctor needs first out of a hundred entries.
# Reasoning tokens bill at the output rate, which is why the ceiling below is stated too.
REPORT_EFFORT: Effort = "medium"
# 6,000 output tokens is $0.18 at the $30/M output rate — the whole cost of one summary, and
# the number the 1/day/household limit in the router is protecting.
REPORT_MAX_OUTPUT_TOKENS = 6_000

# Namespaced like `app.extraction.service._LOCK_NAMESPACE`, and NOT the same namespace: a
# household generating a report must not block its own extraction, and vice versa.
_LOCK_NAMESPACE = b"penny.report:"

SUMMARY_MAX_CHARS = 700
HEADING_MAX_CHARS = 48
SECTION_BODY_MAX_CHARS = 1_400
LIST_ITEM_MAX_CHARS = 240
MAX_SECTIONS = 8
MAX_LIST_ITEMS = 5

# `[E12]` as it appears inside `body_markdown`, with the space in front of it, so removing an
# unresolved citation does not leave a double space in the middle of a sentence.
_INLINE_HANDLE = re.compile(r"[ \t]*\[([A-Za-z]\d{1,4})\]")
# What the model may have typed around a handle in `event_handles`: brackets, whitespace, a
# lowercase prefix. Normalised rather than rejected — the citation is real, the spelling is not
# worth losing it over, and the alternative is an empty report from one stray bracket.
_HANDLE_SHAPE = re.compile(r"^\W*([A-Za-z])\s*(\d{1,4})\W*$")


class ThinRecordError(ValidationError):
    """There is not enough in the record to write a summary from. 422, before any spend.

    Not a 404 and not a 500: the request was well-formed and the household exists, the record
    simply has nothing in it yet. The sentence is rendered to the family verbatim, so it says
    what will make it go away rather than naming a threshold they cannot see.
    """

    detail = (
        "There is not enough in the record yet to write a summary. "
        "Penny needs a few more entries first."
    )


# One section as the model writes it: handles, not ids. Kept as a comment rather than a
# docstring — pydantic copies a docstring into the schema's `description`, and that description
# is sent to the model on every single call, so the reasoning behind a shape becomes something
# we pay tokens for and the model tries to obey.
class UngroundedReportError(PennyError):
    """The model cited nothing that exists, so there is no document — only prose.

    A 500-class failure and not a 422: the record was fine, the request was fine, and what went
    wrong is ours. It surfaces on the `reports` row rather than in an HTTP response (generation
    is detached from the request), so the sentence is written for the family and says the one
    useful thing, which is that pressing the button again is worth doing.
    """

    detail = "Penny could not write a summary it could show its working for. Try again."


class ReportSectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = Field(
        description="Two or three words, e.g. 'Medication change'. No trailing full stop."
    )
    body_markdown: str = Field(
        description=(
            "Two to five sentences of prose. Repeat each handle inline in square brackets "
            "immediately after the clause it supports, e.g. '... on three occasions [E1] [E2].' "
            "No headings, no bullet lists, no bold."
        )
    )
    event_handles: list[str] = Field(
        description=(
            "The handles this section was written from, exactly as printed in the record: "
            "'E3', not 'e3', not '[E3]'. Never invent one."
        )
    )


# Object root and every field required: OpenAI strict structured outputs reject a non-object
# root and reject optionality-by-omission. Kept as a comment rather than a docstring because
# pydantic copies a docstring into the schema `description`, which is sent on every call.
class ReportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="At most four sentences. What gets read if nothing else does.")
    urgent_flag: bool = Field(
        description=(
            "True ONLY when the record itself describes something as needing urgent or same-day "
            "attention and nothing later resolves it. Not your own judgement of severity."
        )
    )
    urgent_reason: str | None = Field(
        description="One sentence with its handle. Null whenever urgent_flag is false."
    )
    sections: list[ReportSectionDraft]
    questions_for_the_doctor: list[str] = Field(
        description="At most four, each a single line, phrased to be read aloud."
    )
    watch_items: list[str] = Field(
        description="At most four observations already in the record. Never instructions."
    )
    data_gaps: list[str] = Field(
        description="At most four. What the record does NOT contain. Never empty."
    )

    @model_validator(mode="after")
    def _enforce_shape(self) -> ReportDraft:
        """The length and count rules, applied here rather than as JSON Schema keywords.

        Strict structured outputs have historically rejected `maxLength`/`maxItems`, and a 400
        on schema validation loses the whole report rather than one over-long list item. Same
        division of labour as `app.llm.schemas`: the descriptions steer, these make it true.

        `urgent_reason` is cleared when the flag is false because the two disagreeing is a
        rendered contradiction — the panel keys the red band off the flag and prints the
        reason, so a stale reason under a false flag is a warning with no warning.
        """
        self.summary = self.summary.strip()[:SUMMARY_MAX_CHARS].rstrip()
        self.sections = self.sections[:MAX_SECTIONS]
        for section in self.sections:
            section.heading = section.heading.strip()[:HEADING_MAX_CHARS].rstrip().rstrip(".")
            section.body_markdown = section.body_markdown.strip()[:SECTION_BODY_MAX_CHARS].rstrip()
        self.questions_for_the_doctor = _clean_list(self.questions_for_the_doctor)
        self.watch_items = _clean_list(self.watch_items)
        self.data_gaps = _clean_list(self.data_gaps)
        if not self.urgent_flag:
            self.urgent_reason = None
        elif self.urgent_reason is not None:
            self.urgent_reason = self.urgent_reason.strip()[:LIST_ITEM_MAX_CHARS].rstrip()
        return self


def _clean_list(items: list[str]) -> list[str]:
    """Trim, drop blanks, cap the count. Order is the model's; it ranked them for a reason."""
    cleaned = [item.strip()[:LIST_ITEM_MAX_CHARS].rstrip() for item in items]
    return [item for item in cleaned if item][:MAX_LIST_ITEMS]


@dataclass(frozen=True, slots=True)
class CitableEvent:
    """One entry as the model sees it, and everything the citation needs on the way back.

    The handle is assigned by `_index_events` and never derived twice: the label printed into
    the prompt and the label resolved out of the answer must be produced by the same code, or
    every citation silently misses by one.
    """

    handle: str
    event_id: UUID
    kind: str
    occurred_at: datetime
    occurred_at_precision: str
    title: str
    body: str | None
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Period:
    """The window a report covers, already clamped to what the record actually holds."""

    start: datetime
    end: datetime
    # True when `start` is the first entry in the record rather than a full `REPORT_PERIOD`
    # back, which is what decides both the title and whether a previous-period comparison is
    # offered to the model at all.
    truncated_to_record: bool

    @property
    def span(self) -> timedelta:
        return self.end - self.start


@dataclass(slots=True)
class RecordStats:
    """Everything in `<counts>`. Counted in Postgres; see the module docstring for why."""

    events_in_period: int
    events_rendered: int
    by_kind: dict[str, int] = field(default_factory=dict)
    # None when the record does not reach back a second full period — an absent column is how
    # the prompt is told not to compare, which is safer than handing it a zero it will read as
    # "there was nothing before".
    by_kind_previous: dict[str, int] | None = None
    top_symptoms: list[tuple[str, int]] = field(default_factory=list)
    medication_actions: dict[str, int] = field(default_factory=dict)
    appointment_statuses: dict[str, int] = field(default_factory=dict)


# --- creating the row -------------------------------------------------------------------


async def create_report(
    session: AsyncSession, household: Household, *, now: datetime | None = None
) -> Report:
    """Write the `pending` row the client polls, or refuse a record too thin to read back.

    The refusal happens HERE, before the row exists, so a thin record costs nothing and leaves
    nothing behind: no id to poll, no `failed` report in the family's list to explain, and no
    LLM call. `MIN_EVENTS_FOR_REPORT` is measured over the period the report would cover, not
    over the household's whole history — a family with two hundred entries from last year and
    nothing since has nothing to hand a doctor today either.

    The title is computed here rather than asked of the model. It is displayed in the reports
    list while the report is still `pending`, so it has to exist before the model has written
    a word — and it is a fact about the period, which is not a thing worth a hallucination.
    """
    now = now or datetime.now(UTC)
    tz = ZoneInfo(household.timezone)
    period = await plan_period(session, household.id, tz=tz, now=now)
    if period is None:
        raise ThinRecordError()

    record = Report(
        household_id=household.id,
        status="pending",
        title=period_title(period, tz),
        period_start=period.start,
        period_end=period.end,
    )
    session.add(record)
    await session.flush()
    log.info(
        "report queued household=%s report=%s days=%d", household.id, record.id, period.span.days
    )
    return record


async def plan_period(
    session: AsyncSession, household_id: UUID, *, tz: ZoneInfo, now: datetime
) -> Period | None:
    """The window to read, or None when the record cannot support a summary.

    Clamped to the first entry INSIDE the window rather than to the household's oldest event:
    the clamp exists so the title does not promise twelve months of a three-week-old record,
    and the first entry in the window is exactly the day that promise becomes true.
    """
    floor = now - REPORT_PERIOD
    row = (
        await session.execute(
            sa.select(sa.func.min(Event.occurred_at), sa.func.count()).where(
                Event.household_id == household_id,
                Event.deleted_at.is_(None),
                Event.occurred_at >= floor,
                Event.occurred_at <= now,
            )
        )
    ).one()
    first, total = row[0], row[1] or 0
    if total < MIN_EVENTS_FOR_REPORT or first is None:
        return None
    start = max(floor, floor_local_day(first, tz))
    return Period(start=start, end=now, truncated_to_record=start > floor)


def period_title(period: Period, tz: ZoneInfo) -> str:
    """What the reports list shows. A fact about the window, in the household's own timezone.

    Two forms, because "Twelve months to 25 July 2026" over a record that began in June is a
    small lie printed at the top of a clinical document.
    """
    end = _long_date(period.end, tz)
    if not period.truncated_to_record:
        return f"Twelve months to {end}"
    return f"{_long_date(period.start, tz)} to {end}"


def _long_date(value: datetime, tz: ZoneInfo) -> str:
    """`25 July 2026`. Built by hand because `%-d` is a platform extension, not a promise."""
    local = value.astimezone(tz)
    return f"{local.day} {local:%B %Y}"


def floor_local_day(value: datetime, tz: ZoneInfo) -> datetime:
    """Midnight of `value`'s day in the household's timezone, back in UTC.

    Two callers, one rule, and both of them are day boundaries a family would recognise: the
    start of a report's period, and the "today" the once-a-day generation limit is counted
    from. Floored in the HOUSEHOLD's zone and never in UTC — local midnight in BST is 23:00 the
    previous day, so a boundary computed in UTC files an evening's entries under the wrong day
    and tells a family in Sydney they already had today's report before they woke up.
    """
    local = value.astimezone(tz)
    return datetime(local.year, local.month, local.day, tzinfo=tz).astimezone(UTC)


# --- generating -------------------------------------------------------------------------


async def generate_report(session: AsyncSession, household_id: UUID, report_id: UUID) -> None:
    """Read the period, call the model once, resolve the citations, write the row.

    Never commits — `app.db.get_session` and the background job own the transaction boundary.
    Raises on a gateway failure so the caller can record it against the row; every failure
    mode the gateway has already wrote its own `llm_runs` row before raising, which is why the
    caller marks the failure in the SAME transaction rather than a fresh one.
    """
    report = (
        await session.execute(
            sa.select(Report).where(Report.id == report_id, Report.household_id == household_id)
        )
    ).scalar_one_or_none()
    if report is None:
        # The household was deleted, or the request that queued this rolled back after all.
        log.warning(
            "report vanished before generation household=%s report=%s", household_id, report_id
        )
        return
    household = await session.get(Household, household_id)
    if household is None:
        return

    tz = ZoneInfo(household.timezone)
    period = Period(
        start=report.period_start,
        end=report.period_end,
        truncated_to_record=False,  # only the title cares, and the title is already written
    )

    events = await _read_events(session, household_id, period)
    if len(events) < MIN_EVENTS_FOR_REPORT:
        # The record emptied out between queueing and running — everything in the period was
        # deleted. Refusing is the same answer `create_report` would have given, and it is a
        # cheaper one than a summary of nothing.
        raise ThinRecordError()
    upcoming = await _read_upcoming(session, household_id, period.end)
    index = _index_events(events, upcoming)
    stats = await _count_record(session, household_id, period, rendered=len(events))

    recorder = DbRunRecorder(session, household_id)
    gateway = LLMGateway(recorder=recorder, budget=_HouseholdBudget(session))
    spec = build_call_spec(household=household, period=period, tz=tz, index=index, stats=stats)
    # Once per RUN, and before the call rather than after it — the same shape as
    # `extraction.runner._Run.execute`, for the same reason: a check that happens after the
    # request is in flight cannot stop the money it is already spending.
    await gateway.check_budget(household_id, purpose="report", model=spec.model)
    result = await gateway.structured(spec)

    by_handle = {entry.handle: entry for entry in index}
    sections, dropped = resolve_sections(result.parsed.sections, by_handle, tz=tz)
    if not sections:
        # Every citation the model wrote was invented, so the resolver deleted every section and
        # what is left is a lead paragraph with nothing under it. Storing that as `complete` would
        # hand a family a page that LOOKS like a summary and cannot be checked anywhere — which is
        # the one outcome this whole module is built to prevent. Fail loudly and let them retry.
        log.warning(
            "report had no resolvable citations household=%s report=%s dropped=%d",
            household_id,
            report_id,
            dropped,
        )
        raise UngroundedReportError()

    report.status = "complete"
    report.summary = result.parsed.summary
    report.urgent_flag = result.parsed.urgent_flag
    report.urgent_reason = result.parsed.urgent_reason
    report.sections = sections
    report.questions_for_the_doctor = result.parsed.questions_for_the_doctor
    report.watch_items = result.parsed.watch_items
    report.data_gaps = _data_gaps(result.parsed.data_gaps, stats)
    report.error = None
    report.generated_at = datetime.now(UTC)

    log.info(
        "report generated household=%s report=%s entries=%d sections=%d dropped_citations=%d "
        "cost=%s ms=%d",
        household_id,
        report_id,
        len(events),
        len(sections),
        dropped,
        result.cost_usd,
        result.latency_ms,
    )


def build_call_spec(
    *,
    household: Household,
    period: Period,
    tz: ZoneInfo,
    index: list[CitableEvent],
    stats: RecordStats,
) -> CallSpec[ReportDraft]:
    """The whole request, as a value. Public so it can be inspected without a database.

    Split out of `generate_report` deliberately: everything that decides what this call COSTS
    and what it is allowed to see — the model, the effort, the output ceiling, the prompt
    version, the rendered input — is now one pure function over plain data, so a test can
    assert on it (and a future dry-run estimate can price it) without a session, a household
    row or a network. The alternative is a second renderer that drifts from this one, which is
    the failure `extraction.runner.render_call_input` is public to avoid.

    `model` is `llm_model_report`, passed EXPLICITLY. The gateway defaults to
    `llm_model_extract` because extraction is the overwhelming majority of calls, so a report
    that forgets to say so is silently billed and tuned as an extraction.
    """
    return CallSpec(
        purpose="report",
        instructions=REPORT_PROMPT,
        input=render_report_input(
            household=household, period=period, tz=tz, index=index, stats=stats
        ),
        schema=ReportDraft,
        max_output_tokens=REPORT_MAX_OUTPUT_TOKENS,
        reasoning_effort=REPORT_EFFORT,
        household_id=household.id,
        model=get_settings().llm_model_report,
        prompt_version=REPORT_PROMPT_VERSION,
    )


def _data_gaps(written: list[str], stats: RecordStats) -> list[str]:
    """The model's gaps, plus the one only the server knows about.

    A truncated read is a fact about how much of the record this page reflects, and it belongs
    with the other things the summary cannot tell you. Prepended, not appended: it qualifies
    everything above it, so burying it under four model-written lines would be the wrong order.
    """
    if stats.events_rendered >= stats.events_in_period:
        return written
    return [
        f"This summary read the {stats.events_rendered} most recent entries of "
        f"{stats.events_in_period} in the period.",
        *written,
    ]


# --- resolving citations ----------------------------------------------------------------


def resolve_sections(
    drafts: list[ReportSectionDraft],
    by_handle: dict[str, CitableEvent],
    *,
    tz: ZoneInfo,
) -> tuple[list[dict[str, Any]], int]:
    """Handles to event ids. Unknown handles are dropped; they never fail the report.

    Three things happen here and only the first is obvious:

    1. **A handle that names no entry is dropped.** The model hallucinated a citation, and a
       report is worth more than the sentence that cited it.
    2. **The dropped handle is removed from `body_markdown` too.** This is the one that is easy
       to miss and the one the family would see: `[E7]` left standing in the prose with no chip
       under it reads as evidence, points at nothing, and cannot be told apart from a working
       citation until somebody tries to click it.
    3. **A section with nothing left is deleted.** An uncheckable claim in a document a GP acts
       on is worse than a shorter document. Counted and returned so the caller can log the
       rate — a rising drop rate is the earliest signal of prompt rot available here, just as
       `invalid_handle_rate` is for extraction.

    Returns the JSONB-ready sections and the number of handles dropped.
    """
    sections: list[dict[str, Any]] = []
    dropped = 0
    for draft in drafts:
        resolved: list[CitableEvent] = []
        seen: set[str] = set()
        for raw in draft.event_handles:
            handle = _normalise_handle(raw)
            entry = by_handle.get(handle) if handle else None
            if entry is None:
                dropped += 1
                continue
            if handle in seen:
                continue
            seen.add(handle)
            resolved.append(entry)

        body = _strip_unresolved(draft.body_markdown, seen)
        if not resolved or not body or not draft.heading:
            dropped += len(resolved)
            continue
        sections.append(
            {
                "heading": draft.heading,
                "body_markdown": body,
                "citations": [_citation(entry, tz) for entry in resolved],
            }
        )
    return sections, dropped


def _citation(entry: CitableEvent, tz: ZoneInfo) -> dict[str, Any]:
    """One resolved citation, stored as it will be served — see `docs/api-contract.md`.

    Denormalised on purpose. The title and date are copied in rather than joined at read time
    so that opening a six-month-old report is one row, and so a later edit to the event does
    not silently rewrite the document a family already printed and took to a consultation.
    """
    return {
        "handle": entry.handle,
        "event_id": str(entry.event_id),
        "kind": entry.kind,
        "occurred_at": entry.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "title": entry.title,
    }


def _normalise_handle(raw: str) -> str | None:
    """`[e3]`, ` E3 `, `E3.` -> `E3`. Anything else is not a handle at all.

    Tolerant on purpose, and only about SPELLING. The alternative is a whole report thrown
    away because the model wrapped its handles in the brackets the prompt shows them in — a
    real failure that costs a family their summary and teaches us nothing.
    """
    match = _HANDLE_SHAPE.match(raw.strip())
    if match is None:
        return None
    return f"{match.group(1).upper()}{match.group(2)}"


def _strip_unresolved(body: str, kept: set[str]) -> str:
    """Remove inline `[E#]` markers that did not resolve, leaving the prose readable."""

    def replace(match: re.Match[str]) -> str:
        handle = _normalise_handle(match.group(1))
        return match.group(0) if handle in kept else ""

    return _INLINE_HANDLE.sub(replace, body).strip()


# --- reading the record -----------------------------------------------------------------


async def _read_events(
    session: AsyncSession, household_id: UUID, period: Period
) -> list[CitableEvent]:
    """The period's entries, most recent first for the cap, then handed back oldest first.

    The cap has to bite at the RECENT end — a year-long record truncated from the front would
    hand a doctor last August and drop the medication change three weeks ago. Ordered ascending
    afterwards so `[E1]` is the oldest entry and the handles read like a timeline.
    """
    rows = (
        await session.execute(
            sa.select(
                Event.id,
                Event.kind,
                Event.occurred_at,
                Event.occurred_at_precision,
                Event.title,
                Event.body,
                Event.details,
            )
            .where(
                Event.household_id == household_id,
                Event.deleted_at.is_(None),
                Event.occurred_at >= period.start,
                Event.occurred_at <= period.end,
            )
            # `created_at` before `id`, the same tiebreak the feed uses and for the same
            # reason: ids are `uuid4`, so day-precision entries — which all sit at midnight —
            # were ordered at random. Here that also decides which ones survive the
            # REPORT_MAX_EVENTS cut and what order the model reads them in, so a summary of a
            # busy day was assembled from an arbitrary subset in an arbitrary sequence.
            .order_by(Event.occurred_at.desc(), Event.created_at.desc(), Event.id.desc())
            .limit(REPORT_MAX_EVENTS)
        )
    ).all()
    return [_as_citable(row) for row in reversed(rows)]


async def _read_upcoming(
    session: AsyncSession, household_id: UUID, after: datetime
) -> list[CitableEvent]:
    """What is already booked. A GP appointment is usually booked because of another one."""
    rows = (
        await session.execute(
            sa.select(
                Event.id,
                Event.kind,
                Event.occurred_at,
                Event.occurred_at_precision,
                Event.title,
                Event.body,
                Event.details,
            )
            .where(
                Event.household_id == household_id,
                Event.deleted_at.is_(None),
                Event.occurred_at > after,
            )
            .order_by(Event.occurred_at.asc(), Event.created_at.asc(), Event.id.asc())
            .limit(REPORT_MAX_UPCOMING)
        )
    ).all()
    return [_as_citable(row) for row in rows]


def _as_citable(row: Any) -> CitableEvent:
    """A selected row, with its handle still unassigned — `_index_events` owns that."""
    return CitableEvent(
        handle="",
        event_id=row.id,
        kind=row.kind,
        occurred_at=row.occurred_at,
        occurred_at_precision=row.occurred_at_precision,
        title=row.title,
        body=row.body,
        details=row.details or {},
    )


def _index_events(events: list[CitableEvent], upcoming: list[CitableEvent]) -> list[CitableEvent]:
    """Assign `E1`… across BOTH blocks, once, in the order they will be printed.

    One numbering across past and upcoming, because a handle is only meaningful if it is unique
    in the document the model is reading — two `[E4]`s in one prompt is a citation that
    resolves to whichever the dict saw last.
    """
    return [
        CitableEvent(
            handle=f"E{position}",
            event_id=entry.event_id,
            kind=entry.kind,
            occurred_at=entry.occurred_at,
            occurred_at_precision=entry.occurred_at_precision,
            title=entry.title,
            body=entry.body,
            details=entry.details,
        )
        for position, entry in enumerate([*events, *upcoming], 1)
    ]


async def _count_record(
    session: AsyncSession, household_id: UUID, period: Period, *, rendered: int
) -> RecordStats:
    """Every number the prose is allowed to contain, counted in Postgres.

    The previous-period column is omitted rather than zeroed when the record does not reach
    back far enough. A zero would be read as "there was nothing before", which is a false
    comparison, and a false comparison is the exact sentence that changes a prescription.
    """
    by_kind = await _kind_counts(session, household_id, period.start, period.end)
    total = sum(by_kind.values())

    # The comparison is only offered when the record COVERS the whole previous window, not when
    # it merely pokes into it. A record that begins three days before the period would otherwise
    # be compared against a year that is 362 days of nothing — arithmetically true and read as
    # "she was fine last year and is not now", which is a claim nobody made.
    previous_start = period.start - period.span
    first_ever = (
        await session.execute(
            sa.select(sa.func.min(Event.occurred_at)).where(
                Event.household_id == household_id, Event.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    covers_previous = first_ever is not None and first_ever <= previous_start
    previous = (
        await _kind_counts(session, household_id, previous_start, period.start)
        if covers_previous
        else None
    )

    return RecordStats(
        events_in_period=total,
        events_rendered=rendered,
        by_kind=by_kind,
        by_kind_previous=previous,
        top_symptoms=await _detail_counts(
            session, household_id, period, kind="symptom", key="symptom", limit=6
        ),
        medication_actions=dict(
            await _detail_counts(
                session, household_id, period, kind="medication", key="action", limit=8
            )
        ),
        appointment_statuses=dict(
            await _detail_counts(
                session, household_id, period, kind="appointment", key="status", limit=8
            )
        ),
    )


async def _kind_counts(
    session: AsyncSession, household_id: UUID, start: datetime, end: datetime
) -> dict[str, int]:
    rows = (
        await session.execute(
            sa.select(Event.kind, sa.func.count())
            .where(
                Event.household_id == household_id,
                Event.deleted_at.is_(None),
                Event.occurred_at >= start,
                Event.occurred_at <= end,
            )
            .group_by(Event.kind)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _detail_counts(
    session: AsyncSession,
    household_id: UUID,
    period: Period,
    *,
    kind: str,
    key: str,
    limit: int,
) -> list[tuple[str, int]]:
    """Group by one JSONB key inside `events.details`, biggest first.

    `details` is written by whichever prompt version was live at the time, so a NULL at `key`
    is normal and is excluded rather than counted as its own category — "12 entries with no
    symptom name" is not a fact anybody wants in a clinical summary.
    """
    value = Event.details[key].astext
    rows = (
        await session.execute(
            sa.select(value, sa.func.count())
            .where(
                Event.household_id == household_id,
                Event.deleted_at.is_(None),
                Event.kind == kind,
                Event.occurred_at >= period.start,
                Event.occurred_at <= period.end,
                value.isnot(None),
                value != "",
            )
            .group_by(value)
            .order_by(sa.func.count().desc(), value)
            .limit(limit)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


# --- the one user message ---------------------------------------------------------------


def render_report_input(
    *,
    household: Household,
    period: Period,
    tz: ZoneInfo,
    index: list[CitableEvent],
    stats: RecordStats,
) -> str:
    """Everything the model is given, and nothing else. NO UUIDs anywhere in it.

    Public so a cost estimate — or a test — can price the exact text a real run would send,
    for the same reason `extraction.runner.render_call_input` is public: an estimate built from
    a second, similar-looking renderer prices a prompt nobody uses.
    """
    upcoming = [entry for entry in index if entry.occurred_at > period.end]
    past = [entry for entry in index if entry.occurred_at <= period.end]

    parts = [
        "<care_brief>\n"
        f"Care recipient: {household.care_recipient_name}.\n"
        f"Household timezone: {household.timezone}.\n"
        f"Period: {_long_date(period.start, tz)} to {_long_date(period.end, tz)}.\n"
        "</care_brief>",
        f"<counts>\n{_render_counts(stats)}\n</counts>",
        "<record>\n" + "\n".join(_render_entry(entry, tz) for entry in past) + "\n</record>",
    ]
    if upcoming:
        parts.append(
            "<upcoming>\n"
            + "\n".join(_render_entry(entry, tz) for entry in upcoming)
            + "\n</upcoming>"
        )
    return "\n".join(parts)


def _render_counts(stats: RecordStats) -> str:
    lines = [f"entries in this period: {stats.events_in_period}"]
    if stats.events_rendered < stats.events_in_period:
        lines.append(f"entries shown below: {stats.events_rendered} (the most recent)")
    for kind in ("symptom", "appointment", "medication", "note"):
        current = stats.by_kind.get(kind, 0)
        if stats.by_kind_previous is None:
            lines.append(f"{kind}: {current}")
        else:
            lines.append(
                f"{kind}: {current} (previous period of the same length: "
                f"{stats.by_kind_previous.get(kind, 0)})"
            )
    if stats.top_symptoms:
        lines.append(
            "most recorded symptoms: "
            + ", ".join(f"{name} {count}" for name, count in stats.top_symptoms)
        )
    if stats.medication_actions:
        lines.append(
            "medication entries: "
            + ", ".join(f"{action} {count}" for action, count in stats.medication_actions.items())
        )
    if stats.appointment_statuses:
        lines.append(
            "appointments: "
            + ", ".join(f"{status} {count}" for status, count in stats.appointment_statuses.items())
        )
    return "\n".join(lines)


def _render_entry(entry: CitableEvent, tz: ZoneInfo) -> str:
    """One line per entry: handle, date, kind, title, the kind's own facts, then the body."""
    bits = [f"[{entry.handle}] {_render_when(entry, tz)} {entry.kind}: {entry.title}"]
    detail = _render_detail(entry)
    if detail:
        bits.append(f"({detail})")
    if entry.body:
        bits.append(f"— {entry.body}")
    return " ".join(bits)


def _render_when(entry: CitableEvent, tz: ZoneInfo) -> str:
    """Only as much of the timestamp as the precision justifies.

    Printing `15:30` on an entry the extractor only placed to the month hands the model a
    certainty the original message never had — and the model will repeat it to a doctor.
    """
    local = entry.occurred_at.astimezone(tz)
    match entry.occurred_at_precision:
        case "exact":
            return f"{local:%Y-%m-%d %H:%M}"
        case "week":
            return f"week of {local:%Y-%m-%d}"
        case "month":
            return f"{local:%Y-%m}"
        case "unknown":
            return f"{local:%Y-%m-%d} (date uncertain)"
        case _:
            return f"{local:%Y-%m-%d}"


def _render_detail(entry: CitableEvent) -> str:
    """The kind-specific facts a doctor asks for: the dose, the provider, what the outcome was.

    Dropping these was the first version of this renderer and it produced a summary that said
    "her blood pressure medicine was changed" — true, useless, and exactly the sentence the
    appointment exists to get past.
    """
    details = entry.details
    parts: list[str] = []
    match entry.kind:
        case "symptom":
            parts = [
                details.get("severity"),
                details.get("body_site"),
                details.get("duration_text"),
            ]
        case "appointment":
            parts = [
                details.get("appointment_kind"),
                details.get("provider_name"),
                details.get("status"),
                details.get("outcome"),
                ", ".join(details.get("follow_up_actions") or []) or None,
            ]
        case "medication":
            parts = [
                details.get("medication_name"),
                details.get("dose_text"),
                details.get("action"),
                details.get("prescriber"),
            ]
        case "note":
            parts = [details.get("category")]
    return ", ".join(str(part) for part in parts if part)


# --- the lock ---------------------------------------------------------------------------


def lock_key(household_id: UUID) -> int:
    """A stable signed 64-bit advisory-lock key, in this module's own namespace.

    Held for the length of the request that CHECKS the daily limit and inserts the row, so two
    people in one family tapping "Generate" at the same moment cannot both read "none today"
    and both buy a summary. `hash()` would not do: it is salted per process, so two workers
    would compute two different keys and neither would ever block the other.
    """
    digest = hashlib.blake2b(_LOCK_NAMESPACE + household_id.bytes, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def acquire_lock(session: AsyncSession, household_id: UUID) -> bool:
    """TRY, not the blocking form: a second simultaneous request has nothing to add."""
    return bool(
        (
            await session.execute(
                sa.select(sa.func.pg_try_advisory_xact_lock(lock_key(household_id)))
            )
        ).scalar_one()
    )


# --- failure ----------------------------------------------------------------------------


def client_sentence(exc: Exception) -> str:
    """`reports.error` is rendered to the family verbatim, so it can never be a traceback.

    A `PennyError` already carries a sentence written for them — the budget refusal names the
    allowance, the thin-record refusal says what will fix it. Everything else collapses to one
    line that says what happens next, because "ResponseParseError" is not information a family
    can act on and the request id they would need is not in front of them.
    """
    if isinstance(exc, PennyError):
        return exc.detail
    return "The summary could not be written. Try again in a few minutes."
