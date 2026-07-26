"""What order the record comes back in, and where the future goes.

Two rules, both found on a live deployment with fifteen real entries in it.

THE FEED IS THE PAST. `/api/feed` had no upper bound on `occurred_at`, so a district nurse booked
for next Friday sorted above everything that had already happened — the first thing a family saw
scrolling their record was something that had not occurred. The same row also came back from
`/api/upcoming`, so it rendered twice on one screen.

TIES ARE BROKEN BY WHEN PENNY RECORDED IT, not by a random number. Event ids are `uuid4`
(`models.py:54`), so `ORDER BY occurred_at DESC, id DESC` shuffles every event sharing a
timestamp — and day-precision events ALL share one, because they are stamped midnight. A day on
which a family reported a fall, then a temperature, then a hospital trip came back in an order
with no relationship to the order they said any of it.

`@pytest.mark.db` throughout: both rules are SQL, and a fake would only test the fake.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db import to_asyncpg_url
from app.deps import HouseholdCtx
from app.models import Base, Event, Household
from app.routers.feed import get_feed, get_upcoming
from app.schemas import FEED_DEFAULT_LIMIT

pytestmark = pytest.mark.db


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


async def a_household(session: AsyncSession) -> Household:
    household = Household(
        username=f"order-{uuid.uuid4().hex[:12]}",
        password_hash="$argon2id$not-a-real-hash",
        name="The Shaws",
        care_recipient_name="Margaret",
    )
    session.add(household)
    await session.flush()
    return household


async def an_entry(
    session: AsyncSession,
    household: Household,
    *,
    title: str,
    occurred_at: datetime,
    created_at: datetime | None = None,
    precision: str = "exact",
) -> Event:
    entry = Event(
        household_id=household.id,
        kind="symptom",
        title=title,
        occurred_at=occurred_at,
        occurred_at_precision=precision,
        details={"symptom": title},
        # NOT NULL, and unique per household — it is the merge key a re-extraction lands on.
        dedup_key=f"{title}-{uuid.uuid4().hex[:8]}",
    )
    session.add(entry)
    await session.flush()
    if created_at is not None:
        # Set after the insert: `created_at` has a server default, and the point of these tests
        # is the order Penny recorded things in, which a test has to be able to choose.
        entry.created_at = created_at
        await session.flush()
    return entry


async def feed_titles(session: AsyncSession, household: Household) -> list[str]:
    page: Any = await get_feed(
        HouseholdCtx(id=household.id, household=household),
        session,
        limit=FEED_DEFAULT_LIMIT,
        before=None,
    )
    return [event.title for event in page.events]


# --- the feed is the past -------------------------------------------------------------------


async def test_a_future_appointment_is_not_at_the_top_of_the_history(
    session: AsyncSession,
) -> None:
    """THE BUG, as seen on the live deployment.

    A district nurse booked for next Friday sorted above every entry that had already happened,
    because newest-first with no upper bound puts the furthest-away booking highest. The nav
    calls this list History.
    """
    household = await a_household(session)
    now = datetime.now(UTC)
    await an_entry(session, household, title="Fall", occurred_at=now - timedelta(hours=3))
    await an_entry(
        session, household, title="District nurse visit", occurred_at=now + timedelta(days=5)
    )

    titles = await feed_titles(session, household)

    assert "District nurse visit" not in titles
    assert titles == ["Fall"]


async def test_the_future_entry_is_still_reachable_on_upcoming(session: AsyncSession) -> None:
    """Excluded from the record, NOT deleted from the product.

    This is the whole justification for the bound: the row is the other half of a split, not a
    row nobody can see. If this test ever fails the feed bound has to come back out, because a
    booking that appears nowhere is worse than one that appears twice.
    """
    household = await a_household(session)
    now = datetime.now(UTC)
    await an_entry(
        session, household, title="District nurse visit", occurred_at=now + timedelta(days=5)
    )

    page: Any = await get_upcoming(HouseholdCtx(id=household.id, household=household), session)

    assert [event.title for event in page.events] == ["District nurse visit"]


async def test_an_entry_from_a_moment_ago_is_in_the_record(session: AsyncSession) -> None:
    """The bound is `<= now()`, and the boundary is where an off-by-one would hide today."""
    household = await a_household(session)
    await an_entry(
        session,
        household,
        title="Mum fell this morning",
        occurred_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    assert await feed_titles(session, household) == ["Mum fell this morning"]


# --- ties are broken by when Penny recorded it ----------------------------------------------


async def test_entries_sharing_a_timestamp_come_back_newest_recorded_first(
    session: AsyncSession,
) -> None:
    """THE ORDERING BUG. Day-precision entries all sit at midnight, so ties are the common case.

    Ordered by `id`, these three came back shuffled — `uuid4` has no relationship to time. The
    family reported the fall first, then the temperature, then the hospital trip; newest-first
    means the hospital trip is on top.
    """
    household = await a_household(session)
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    recorded = midnight + timedelta(hours=8)

    for offset, title in enumerate(["Fall", "Temperature of 38", "Hospital check"]):
        await an_entry(
            session,
            household,
            title=title,
            occurred_at=midnight,
            created_at=recorded + timedelta(minutes=offset),
            precision="day",
        )

    assert await feed_titles(session, household) == [
        "Hospital check",
        "Temperature of 38",
        "Fall",
    ]


async def test_the_occurred_at_order_still_wins_over_the_recorded_order(
    session: AsyncSession,
) -> None:
    """`created_at` is the TIEBREAK, not the sort.

    A family importing an old export records yesterday's entries after today's. If the recorded
    time outranked the real one, an import would scramble the whole history into the order the
    parser happened to walk the file.
    """
    household = await a_household(session)
    now = datetime.now(UTC)
    await an_entry(
        session,
        household,
        title="Yesterday, recorded last",
        occurred_at=now - timedelta(days=1),
        created_at=now,
    )
    await an_entry(
        session,
        household,
        title="Today, recorded first",
        occurred_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
    )

    assert await feed_titles(session, household) == [
        "Today, recorded first",
        "Yesterday, recorded last",
    ]
