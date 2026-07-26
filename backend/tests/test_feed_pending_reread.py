"""THE BANNER THAT TELLS A FAMILY THE RECORD IS NOT FINISHED YET.

Clearing `messages.extracted_at` is how a re-read is started — the onboarding interview does it
for the whole household once the family finally answers, so the corrected care filter reaches
history that was already read under the wrong one. The re-read costs money and the monthly
budget aborts it mid-flight by design, so between the abort and the next tick the feed is a
mixture: part re-read, part not read at all.

The part not read yet renders as NOTHING. So does a fortnight in which the family genuinely
said nothing about care. Those two are byte-identical on screen, and one of them is a lie about
a medical record. Everything in this file is a way that distinction could stop being true:

    nothing pending                 -> the field is null, and PRESENT (never omitted)
    something pending               -> the exact count and the outer sent_at bounds
    a count with no bounds          -> say nothing rather than render an empty range
    another household's backlog     -> never counted here, however large
    system lines only               -> null, because that banner could never drain
    the last message gets stamped   -> null, immediately, on the very next read

The DB half needs real Postgres and skips without `PENNY_TEST_DATABASE_URL`: the whole thing is
one aggregate over a partial index that SQLite does not have, and stubbing it would test the
stub. Those tests call `get_feed` directly rather than through a signed cookie — what is under
test is the SQL and the tenant filter, and a `HouseholdCtx` built in the test is the same object
`require_household` would have handed the route. The pure half needs no database at all, which
is the point of `PendingReread.from_aggregate` existing as a separate constructor.

NO MESSAGE TEXT IS ASSERTED ANYWHERE HERE, and that is not incidental to the feature: the whole
design of the banner is a count and two timestamps precisely so that nothing a family said has
to leave the database to explain that Penny has not read it yet.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db import to_asyncpg_url
from app.deps import HouseholdCtx
from app.models import Base, Household, Message
from app.routers.feed import get_feed
from app.schemas import FEED_DEFAULT_LIMIT, FeedPage, PendingReread

# A fixed instant, so a range assertion is a statement about the data and never about the clock.
MARCH = datetime(2026, 3, 4, 9, 30, tzinfo=UTC)


# --- the shape, with no database in sight -------------------------------------------------


def test_a_household_with_nothing_left_to_read_reports_null_and_not_a_zero() -> None:
    """Caught up is the steady state, and it has to be one `null` check on the client.

    A `{message_count: 0}` object would render a banner reading "0 messages not yet read" in
    any client that checked for presence rather than for a zero — which is the natural thing to
    write, and which turns the reassurance into a permanent nag on the households that have
    nothing wrong with them at all.
    """
    assert PendingReread.from_aggregate(0, None, None) is None


def test_a_pending_reread_carries_the_count_and_the_outer_bounds_of_what_is_unread() -> None:
    """The count and the range are about the UNREAD set, not about the household's history.

    Naming the whole history would say "all of this is unread" the instant one old message came
    back into the set — for instance a voice note from last March whose transcript arrived late
    and put a single row back on the wrong side of the cursor.
    """
    pending = PendingReread.from_aggregate(412, MARCH, MARCH + timedelta(days=137))

    assert pending is not None
    assert pending.message_count == 412
    assert pending.first_sent_at == MARCH
    assert pending.last_sent_at == MARCH + timedelta(days=137)


def test_a_count_with_no_range_says_nothing_rather_than_rendering_an_empty_range() -> None:
    """`MIN`/`MAX` are NULL exactly when `COUNT` is zero, so today the two cannot disagree.

    This is the guard for the day somebody feeds the two halves from different queries. A
    banner that says "40 messages not yet read, from  to " reads as a bug, and a record a
    family makes medical decisions from cannot afford to look broken at the exact moment it is
    admitting to being incomplete. Saying nothing is the safe failure.
    """
    assert PendingReread.from_aggregate(40, None, None) is None
    assert PendingReread.from_aggregate(40, MARCH, None) is None
    assert PendingReread.from_aggregate(40, None, MARCH) is None


def test_a_negative_count_is_treated_as_nothing_pending_rather_than_trusted() -> None:
    """Impossible from `COUNT(*)`, and the banner still must not render on it.

    The rule is that this constructor never produces a banner it cannot justify. A negative
    count could only come from arithmetic somebody did on the way in, and arithmetic that went
    wrong is not evidence that a family's history is missing.
    """
    assert PendingReread.from_aggregate(-1, MARCH, MARCH) is None


def test_the_bounds_serialise_as_utc_with_a_trailing_z_like_every_other_timestamp() -> None:
    """The client renders every instant in `household.timezone`; a local offset is wrong twice.

    Once in the raw value and again after the browser converts it — and here the value IS the
    sentence on screen ("4 March to 19 July"), so an offset that shifted a boundary by a day
    would put a wrong date in front of a family with no way for them to tell.
    """
    page = FeedPage(
        events=[],
        pending_reread=PendingReread.from_aggregate(2, MARCH, MARCH + timedelta(hours=6)),
    )

    dumped = page.model_dump(mode="json")

    assert dumped["pending_reread"]["first_sent_at"] == "2026-03-04T09:30:00Z"
    assert dumped["pending_reread"]["last_sent_at"] == "2026-03-04T15:30:00Z"


def test_a_caught_up_feed_page_still_carries_the_key_with_an_explicit_null() -> None:
    """An absent field and a caught-up household must not be the same wire shape.

    If the field were omitted when there is nothing pending, a client could only ever learn
    "absent", and "absent" is also what an older backend returns. The one reading it cannot
    tell "Penny has read everything" from "this server has never heard of the question" — and
    those want opposite treatment, because the second one must not be presented as reassurance.
    """
    dumped = FeedPage(events=[]).model_dump(mode="json")

    assert "pending_reread" in dumped
    assert dumped["pending_reread"] is None


# --- the query, against real Postgres -----------------------------------------------------


@pytest.fixture
async def session(db_url: str) -> AsyncIterator[AsyncSession]:
    """Rolls back, so a run of this file leaves the database exactly as it found it."""
    engine = create_async_engine(to_asyncpg_url(db_url))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine) as db:
            yield db
            await db.rollback()
    finally:
        await engine.dispose()


async def make_household(session: AsyncSession, name: str = "The Shaws") -> Household:
    household = Household(
        username=f"reread-{uuid.uuid4().hex[:12]}",
        password_hash="$argon2id$not-a-real-hash",
        name=name,
        care_recipient_name="Margaret",
    )
    session.add(household)
    await session.flush()
    return household


async def add_message(
    session: AsyncSession,
    household: Household,
    *,
    sent_at: datetime,
    extracted: bool,
    message_type: str = "text",
) -> Message:
    """One row. `content_hash` is unique per household, so it is derived from a fresh uuid.

    The seam computes a real content hash from the text; here the hash only has to be distinct,
    and inventing one keeps this file from depending on the ingest pipeline to set up a test
    about a cursor.
    """
    message = Message(
        household_id=household.id,
        provider="gowa",
        provider_message_id=uuid.uuid4().hex,
        content_hash=uuid.uuid4().bytes,
        sent_at=sent_at,
        message_type=message_type,
        text="…",
        extracted_at=datetime.now(UTC) if extracted else None,
    )
    session.add(message)
    await session.flush()
    return message


async def read_feed(session: AsyncSession, household: Household) -> FeedPage:
    """The real route function, with the context `require_household` would have built."""
    return await get_feed(
        HouseholdCtx(id=household.id, household=household),
        session,
        limit=FEED_DEFAULT_LIMIT,
        before=None,
    )


@pytest.mark.db
async def test_the_banner_counts_exactly_the_messages_this_household_has_not_read(
    session: AsyncSession,
) -> None:
    """The state a budget abort leaves behind: some of the cursor drained, the rest not.

    The bounds must come from the UNREAD rows alone. Taking them from the household's messages
    generally is the tempting simplification and it is wrong in the direction that matters — it
    would report the read half as unread and tell a family the whole record is provisional.
    """
    household = await make_household(session)
    for day in range(4):
        await add_message(session, household, sent_at=MARCH + timedelta(days=day), extracted=True)
    for day in range(10, 13):
        await add_message(session, household, sent_at=MARCH + timedelta(days=day), extracted=False)

    page = await read_feed(session, household)

    assert page.pending_reread is not None
    assert page.pending_reread.message_count == 3
    assert page.pending_reread.first_sent_at == MARCH + timedelta(days=10)
    assert page.pending_reread.last_sent_at == MARCH + timedelta(days=12)


@pytest.mark.db
async def test_another_households_unread_backlog_never_reaches_this_households_banner(
    session: AsyncSession,
) -> None:
    """`household_id` is the whole tenancy mechanism and this query reads raw `messages`.

    No text would cross even if the filter were missing — the response is a count and two
    dates. That is exactly why this is worth a test: the leak would be invisible in the payload
    and would still tell one family how much another family talks and over what months.
    """
    ours = await make_household(session, name="The Shaws")
    theirs = await make_household(session, name="The Doyles")
    for day in range(50):
        await add_message(session, theirs, sent_at=MARCH + timedelta(days=day), extracted=False)
    await add_message(session, ours, sent_at=MARCH, extracted=False)

    page = await read_feed(session, ours)

    assert page.pending_reread is not None
    assert page.pending_reread.message_count == 1
    assert page.pending_reread.last_sent_at == MARCH


@pytest.mark.db
async def test_unread_system_lines_alone_never_raise_a_banner_that_could_not_drain(
    session: AsyncSession,
) -> None:
    """A join notice is not something a person said, and no run will ever read it for content.

    `extraction.service._stamp_inert` clears these with a plain UPDATE the first time any run
    happens. Counted here, a household whose only unread rows are system lines would carry a
    banner reading "2 messages not yet read" that nothing could clear until an unrelated
    message happened to arrive — a promise of progress that does not progress, on a household
    that has in fact read every word the family wrote.
    """
    household = await make_household(session)
    await add_message(session, household, sent_at=MARCH, extracted=True)
    await add_message(session, household, sent_at=MARCH, extracted=False, message_type="system")
    await add_message(
        session,
        household,
        sent_at=MARCH + timedelta(days=1),
        extracted=False,
        message_type="system",
    )

    page = await read_feed(session, household)

    assert page.pending_reread is None


@pytest.mark.db
async def test_the_banner_disappears_on_the_next_read_once_the_cursor_drains(
    session: AsyncSession,
) -> None:
    """The banner is orientation and orientation that outlives its cause is just noise.

    Derived on every read is what buys this: nothing has to remember to clear a flag, so the
    four separate places that stamp `extracted_at` cannot each be a place that forgets. The
    banner goes when the last message is stamped, and not one read later.
    """
    household = await make_household(session)
    message = await add_message(session, household, sent_at=MARCH, extracted=False)
    assert (await read_feed(session, household)).pending_reread is not None

    message.extracted_at = datetime.now(UTC)
    await session.flush()

    assert (await read_feed(session, household)).pending_reread is None
