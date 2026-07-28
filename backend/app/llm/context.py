"""What Penny's conversational calls read, built once and shared.

`assistant.py` (the @-mention reply) and `interview.py` (the onboarding conversation) grew
the same four helpers independently — prompt loading, a recent-messages read, an events
read, a word-boundary truncate — plus the same gateway wiring, drifting only in limits.
This module is the single copy. It exists because the two callers are one product surface:
Penny speaking in a family's group chat.

    assistant.answer_mention ─┬─ message_lines(tz-stamped, skips system rows)
                              ├─ event_lines
                              ├─ truncate
                              └─ make_gateway
    interview.capture_answer ─┬─ message_lines(sender-only — see the tz note below)
                              ├─ event_lines  (so a question mid-interview is answerable)
                              ├─ truncate
                              └─ make_gateway

THE TZ ASYMMETRY IS DELIBERATE. The assistant's lines carry local timestamps because "when
was the GP appointment" is its bread and butter. The interview's lines carry none, because
its prompt promises the model is never told today's date — that promise is what stops an
invented `confirmed_at` on the four clinical fields — and the newest message's timestamp IS
today, to the minute. Callers choose by passing `tz` or not; neither choice is an accident.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

# `_HouseholdBudget` is private to the extraction service by name only: it is a `BudgetGuard`
# over `llm_runs.cost_usd` for one household, and a chat reply spends from the same $15/month
# pot as everything else — which is what stops a mention loop in a busy group becoming a bill.
# This is the ONE import site; both conversational modules get their gateway from here.
from app.extraction.service import _HouseholdBudget
from app.llm.gateway import LLMGateway
from app.llm.recorder import DbRunRecorder
from app.models import Event, Message

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID
    from zoneinfo import ZoneInfo

    from sqlalchemy.ext.asyncio import AsyncSession

EVENT_BODY_CHARS = 220

_DETAIL_KEYS = ("status", "provider_name", "location", "outcome", "dose_text", "action", "severity")
_UNINFORMATIVE = frozenset({None, "", "unknown", "other"})


def make_gateway(session: AsyncSession, household_id: UUID) -> LLMGateway:
    """A gateway wired to this household's `llm_runs` audit rows and monthly budget."""
    return LLMGateway(
        recorder=DbRunRecorder(session, household_id),
        budget=_HouseholdBudget(session),
    )


def truncate(text: str, limit: int) -> str:
    """Cut at a word boundary and say so. A silently halved sentence reads as a bug."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    head, _, _tail = cut.rpartition(" ")
    # Back off to the last word boundary, unless doing so would throw away half the excerpt —
    # one very long token should still be cut mid-token rather than reduced to nothing.
    if head and len(head) >= limit // 2:
        cut = head
    return cut.rstrip(" ,;:") + "…"


async def message_lines(
    session: AsyncSession,
    household_id: UUID,
    *,
    limit: int,
    excerpt_chars: int,
    tz: ZoneInfo | None = None,
    require_text: bool = False,
) -> tuple[str, ...]:
    """The tail of the conversation, oldest first, sender-attributed.

    `tz` present stamps each line with the local time and skips system rows (the assistant's
    shape); `tz` absent renders bare `sender: text` lines (the interview's — see the module
    docstring for why it must never see a timestamp). `require_text` drops rows whose text is
    NULL, for the caller that has nothing to learn from "(image, no text)".
    """
    conditions = [Message.household_id == household_id]
    if tz is not None:
        conditions.append(Message.message_type != "system")
    if require_text:
        conditions.append(Message.text.is_not(None))
    rows = (
        await session.execute(
            sa.select(
                Message.sent_at,
                Message.sender_display_name,
                Message.text,
                Message.message_type,
            )
            .where(*conditions)
            .order_by(Message.sent_at.desc(), Message.source_ordinal.desc(), Message.id.desc())
            .limit(limit)
        )
    ).all()
    return tuple(
        _message_line(
            sent_at=row.sent_at,
            sender=row.sender_display_name,
            text=row.text,
            message_type=row.message_type,
            tz=tz,
            excerpt_chars=excerpt_chars,
        )
        for row in reversed(rows)
    )


async def event_lines(
    session: AsyncSession,
    household_id: UUID,
    tz: ZoneInfo,
    now: datetime,
    *,
    limit: int,
) -> tuple[str, ...]:
    """The newest events, oldest first, one compact line each.

    Fetched newest-first and then reversed: "the last N" has to be chosen from the top of the
    feed, but a model reading a timeline reads it forwards. Future-dated rows sort to the top
    and are labelled, which is what makes "when is her next appointment" answerable at all.
    """
    rows = (
        await session.execute(
            sa.select(
                Event.kind,
                Event.occurred_at,
                Event.occurred_at_precision,
                Event.title,
                Event.body,
                Event.details,
            )
            .where(Event.household_id == household_id, Event.deleted_at.is_(None))
            .order_by(Event.occurred_at.desc(), Event.id.desc())
            .limit(limit)
        )
    ).all()
    return tuple(
        _event_line(
            kind=row.kind,
            occurred_at=row.occurred_at,
            precision=row.occurred_at_precision,
            title=row.title,
            body=row.body,
            details=row.details or {},
            tz=tz,
            now=now,
        )
        for row in reversed(rows)
    )


def _event_line(
    *,
    kind: str,
    occurred_at: datetime,
    precision: str,
    title: str,
    body: str | None,
    details: dict[str, object],
    tz: ZoneInfo,
    now: datetime,
) -> str:
    line = f"{_event_date(occurred_at, precision, tz)} {kind}: {title.strip()}"
    if occurred_at > now:
        line += " (upcoming)"
    extras = ", ".join(
        f"{key}: {details[key]}"
        for key in _DETAIL_KEYS
        if isinstance(details.get(key), str) and details[key] not in _UNINFORMATIVE
    )
    if extras:
        line += f" [{extras}]"
    if body:
        line += f" — {truncate(' '.join(body.split()), EVENT_BODY_CHARS)}"
    return line


def _event_date(occurred_at: datetime, precision: str, tz: ZoneInfo) -> str:
    """Print the date to the precision we actually have, so the model cannot borrow certainty.

    `events.occurred_at` is NOT NULL and falls back to a source message's timestamp, so an
    undated event still carries a full timestamp that means nothing. Rendering `week`, `month`
    and `unknown` as themselves is what stops "sometime in August" being answered as the 3rd.
    """
    local = occurred_at.astimezone(tz)
    if precision == "exact":
        return local.strftime("%Y-%m-%d %H:%M")
    if precision == "week":
        return f"week of {local:%Y-%m-%d}"
    if precision == "month":
        return f"{local:%Y-%m} (month only)"
    if precision == "unknown":
        return f"{local:%Y-%m-%d} (date uncertain)"
    return local.strftime("%Y-%m-%d")


def _message_line(
    *,
    sent_at: datetime,
    sender: str | None,
    text: str | None,
    message_type: str,
    tz: ZoneInfo | None,
    excerpt_chars: int,
) -> str:
    who = (sender or "someone").strip()
    said = " ".join((text or "").split())
    if not said:
        # Media is stored as a type and nothing else (auto-download is off), so this is the
        # whole truth about the message rather than a summary of something we dropped.
        said = f"({message_type}, no text)"
    body = f"{who}: {truncate(said, excerpt_chars)}"
    if tz is None:
        return body
    return f"{sent_at.astimezone(tz):%Y-%m-%d %H:%M} {body}"
