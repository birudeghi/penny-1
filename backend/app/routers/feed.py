"""The two read routes the app is built around: the feed, and what is coming up.

"Upcoming" is a QUERY (`occurred_at > now()`), not an entity. No table, no column, no flag —
which is why an event that slips from future to past needs no migration, no cron and no
write: it simply stops matching one predicate and starts matching the other.

The pending re-read is a query too, for the same reason and with more at stake. It is a
statement about `messages.extracted_at`, derived on every read, never stored. See
`app.schemas.PendingReread` for the failure it exists to prevent; the short version is that a
stretch of history Penny has not read yet looks exactly like a stretch where nothing happened,
and only the cursor can tell those apart.

All three filter on `ctx.id`. That filter is the entire tenancy mechanism; there is no RLS
behind it to catch a query that forgets — and the pending re-read reads `messages`, which is
the family's raw conversation, so a missing filter there would leak the shape of another
household's chat (how much they talk, and over what dates) even though no text ever crosses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy import select

from app import photos as photo_rule
from app.deps import CurrentHousehold, SessionDep
from app.models import Event, Member, Message
from app.schemas import (
    FEED_DEFAULT_LIMIT,
    FEED_MAX_LIMIT,
    UPCOMING_LIMIT,
    FeedPage,
    PendingReread,
    PhotoSet,
    UpcomingPage,
    to_event,
)

router = APIRouter(prefix="/api", tags=["feed"])


def _rows_to_events(
    rows: list[tuple[Event, str | None]],
    photos: dict[UUID, PhotoSet] | None = None,
) -> list:
    """`photos=None` means nobody looked, which is not the same as "there are none".

    `/api/upcoming` passes nothing, on purpose: an event still in the future has no conversation
    behind it to draw photographs from, so running the query there would spend a statement to
    prove a point already known.
    """
    return [
        to_event(event, display_name, (photos or {}).get(event.id))
        for event, display_name in rows
    ]


async def _pending_reread(session: SessionDep, household_id: UUID) -> PendingReread | None:
    """`COUNT/MIN/MAX` over the messages this household has ingested and not yet read.

    ONE aggregate, not a count followed by a bounds query: two statements could straddle a
    concurrent extraction commit and report a count that no longer matches the range, which is
    how a banner ends up claiming "3 messages, 4 March to 4 March" about messages that were
    read a moment ago. One statement sees one snapshot.

    System lines are excluded, and that is a correctness fix rather than tidiness. A join
    notice or the encryption banner is a group event, not conversation; no model will ever read
    one for content, and `extraction.service._stamp_inert` clears them with a plain UPDATE the
    first time any run happens for the household. Counted, they would put a banner reading
    "2 messages not yet read" on a household that has genuinely read everything a person
    said — and it would sit there until some unrelated message arrived to trigger a run, which
    is a banner that cannot be made to go away by waiting. The whole promise of this thing is
    that it drains.

    Voice notes still waiting on a transcript ARE counted, deliberately, even though the
    extraction cursor skips them for a few minutes while the words are fetched. They are
    genuinely unread, the banner is genuinely right about them, and the count falls on its own
    within the transcription grace window. Excluding them would mean copying a private
    predicate out of the extraction service and owning a second copy of a rule that already has
    one home.

    WHY THIS IS AFFORDABLE ON EVERY PAGE. `ix_messages_household_sent_at_unextracted` is
    `(household_id, sent_at) WHERE extracted_at IS NULL`, which is the whole selective part of
    this predicate; `message_type` is a recheck over whatever that returns and never widens the
    scan. In the steady state — nothing pending — the index range is empty and the query is a
    no-op. The expensive moment is right after the interview clears a household's cursor, where
    it visits one index entry per message: that is precisely the moment the answer matters most,
    it shrinks with every tick, and it is still an index range scan rather than a table scan.

    Computing it only for the first page was the alternative and it is worse. Later pages would
    carry `null`, and a client that read the most recently fetched page — the obvious thing to
    write — would watch the banner vanish the moment somebody scrolled, which is the failure
    this feature exists to prevent, reintroduced as an optimisation.
    """
    count, first_sent_at, last_sent_at = (
        await session.execute(
            sa.select(
                sa.func.count(Message.id),
                sa.func.min(Message.sent_at),
                sa.func.max(Message.sent_at),
            ).where(
                Message.household_id == household_id,
                Message.extracted_at.is_(None),
                Message.message_type != "system",
            )
        )
    ).one()
    return PendingReread.from_aggregate(count, first_sent_at, last_sent_at)


@router.get("/feed", response_model=FeedPage)
async def get_feed(
    ctx: CurrentHousehold,
    session: SessionDep,
    limit: int = Query(FEED_DEFAULT_LIMIT, ge=1, le=FEED_MAX_LIMIT),
    before: datetime | None = Query(None),
) -> FeedPage:
    """Newest first, deleted rows excluded, one page at a time — plus what has not been read.

    `before` is exclusive on `occurred_at` only. Known and accepted: events sharing the exact
    boundary timestamp can be skipped across a page edge. With a few hundred events per
    household and a day-grouped feed, real keyset pagination protects nothing here.

    `pending_reread` rides along on the SAME response rather than living at its own endpoint,
    because the number is only meaningful next to the events it qualifies. A separate route
    could be fetched a minute apart from the page it annotates, or fail on its own and leave
    the feed silently over-claiming completeness — and "the record looks complete when it is
    not" is precisely the failure the field exists to prevent, so it must not have a way to go
    missing that the feed itself survives.
    """
    stmt = (
        # LEFT join: `actor_member_id` is nullable and an unattributable event must still
        # appear in the feed. An inner join here silently hides events, which is the kind of
        # bug nobody reports because the row never shows up to be doubted.
        select(Event, Member.display_name)
        .outerjoin(Member, Event.actor_member_id == Member.id)
        .where(Event.household_id == ctx.id, Event.deleted_at.is_(None))
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(limit)
    )
    if before is not None:
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        stmt = stmt.where(Event.occurred_at < before)

    rows = list((await session.execute(stmt)).all())
    # Photographs are attached AFTER the page is chosen, never as a join onto the page query.
    # A join would multiply the event rows by their photo count and quietly break `limit`, which
    # is the pagination cursor — a page of 25 entries would come back as 25 rows or 80 depending
    # on how many pictures the family sent that week. See `app.photos` for the rule itself.
    photos = await photo_rule.photos_for_page(session, ctx.id, [event.id for event, _ in rows])
    events = _rows_to_events(rows, photos)
    # Null when a partial page came back: that is the client's stop signal, and returning a
    # cursor unconditionally makes it fetch the same empty tail forever.
    next_before = events[-1].occurred_at if len(events) == limit else None
    return FeedPage(
        events=events,
        next_before=next_before,
        pending_reread=await _pending_reread(session, ctx.id),
    )


@router.get("/upcoming", response_model=UpcomingPage)
async def get_upcoming(ctx: CurrentHousehold, session: SessionDep) -> UpcomingPage:
    """Everything still to come, soonest first. Ascending, unlike the feed, and capped at 50."""
    stmt = (
        select(Event, Member.display_name)
        .outerjoin(Member, Event.actor_member_id == Member.id)
        .where(
            Event.household_id == ctx.id,
            Event.deleted_at.is_(None),
            Event.occurred_at > datetime.now(UTC),
        )
        .order_by(Event.occurred_at.asc(), Event.id.asc())
        .limit(UPCOMING_LIMIT)
    )
    rows = list((await session.execute(stmt)).all())
    return UpcomingPage(events=_rows_to_events(rows))
