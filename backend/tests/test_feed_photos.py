"""Which photographs reach which entry, and which must never.

`app.photos` answers one question at two sizes and every rule in it is a rule about trust rather
than about layout. A photograph on a medical entry is a claim that this picture has something to
do with this entry, so the interesting tests here are all about the ways that claim can be false:
a holiday snap that happened to be sent nearby, a picture from another family, an entry whose
conversation ran across a week, the same photograph repeated down five cards.

Every test in this file is `@pytest.mark.db` because the rule lives in SQL — a window function, a
`DISTINCT ON`, a lateral-free CTE chain — and a fake would only ever test the fake.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import photos as photo_rule
from app.db import to_asyncpg_url
from app.deps import HouseholdCtx
from app.media import DERIVED_MARKER
from app.models import Base, Event, Household, Message
from app.routers.events import get_event_photos
from app.routers.feed import get_feed
from app.schemas import FEED_DEFAULT_LIMIT, FEED_PHOTOS_PER_EVENT

MARCH = datetime(2026, 3, 12, 9, 0, tzinfo=UTC)


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


async def make_household(session: AsyncSession) -> Household:
    household = Household(
        username=f"photos-{uuid.uuid4().hex[:12]}",
        password_hash="$argon2id$not-a-real-hash",
        name="The Shaws",
        care_recipient_name="Margaret",
    )
    session.add(household)
    await session.flush()
    return household


async def add_photo(
    session: AsyncSession,
    household: Household,
    *,
    sent_at: datetime,
    described: bool = True,
    stored: bool = True,
    message_type: str = "image",
    sender: str = "Sarah",
) -> Message:
    """One image message.

    `described` writes BOTH the text and the marker, because `media._derive` writes them in the
    same transaction — a row with one and not the other does not occur, and a fixture that made
    one would be testing a state the system cannot reach. `stored=False` is the `.txt` import
    case: a placeholder with no bytes and no provider id to fetch them by, ever.
    """
    payload: dict[str, Any] = {DERIVED_MARKER: True} if described else {}
    message = Message(
        household_id=household.id,
        provider="gowa",
        provider_message_id=uuid.uuid4().hex if stored else None,
        content_hash=uuid.uuid4().bytes,
        sender_display_name=sender,
        sent_at=sent_at,
        message_type=message_type,
        text="Swelling on the left ankle, skin taut and shiny." if described else None,
        payload=payload,
        media_content_type="image/jpeg" if stored else None,
        media_byte_count=1024 if stored else None,
        media_stored_at=datetime.now(UTC) if stored else None,
        extracted_at=datetime.now(UTC),
    )
    session.add(message)
    await session.flush()
    return message


async def add_event(
    session: AsyncSession,
    household: Household,
    *,
    occurred_at: datetime,
    sources: list[Message] | None = None,
    title: str = "Left ankle swollen again",
) -> Event:
    entry = Event(
        household_id=household.id,
        kind="symptom",
        occurred_at=occurred_at,
        occurred_at_precision="exact",
        title=title,
        details={"symptom": "swelling", "severity": "moderate"},
        source_message_ids=[m.id for m in (sources or [])],
        source_excerpts=[],
        occurrences=[],
        user_edited_fields=[],
        dedup_key=uuid.uuid4().hex,
    )
    session.add(entry)
    await session.flush()
    return entry


async def photos_of(session: AsyncSession, household: Household, entry: Event) -> list[dict]:
    found = await photo_rule.photos_for_page(session, household.id, [entry.id])
    return found[entry.id].photos if entry.id in found else []


async def read_feed(session: AsyncSession, household: Household) -> Any:
    return await get_feed(
        HouseholdCtx(id=household.id, household=household),
        session,
        limit=FEED_DEFAULT_LIMIT,
        before=None,
    )


# --- what counts as a photograph ----------------------------------------------------------


@pytest.mark.db
async def test_a_photo_the_model_cited_is_filed_with_its_entry(session: AsyncSession) -> None:
    household = await make_household(session)
    picture = await add_photo(session, household, sent_at=MARCH)
    entry = await add_event(session, household, occurred_at=MARCH, sources=[picture])

    found = await photos_of(session, household, entry)

    assert [p["message_id"] for p in found] == [picture.id]
    assert found[0]["cited"] is True
    assert found[0]["sender"] == "Sarah"


@pytest.mark.db
async def test_a_nearby_photo_penny_judged_relevant_is_filed_as_context(
    session: AsyncSession,
) -> None:
    """The whole reason the window exists: a picture the model did not cite but did read."""
    household = await make_household(session)
    spoken = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    nearby = await add_photo(session, household, sent_at=MARCH + timedelta(minutes=20))
    entry = await add_event(session, household, occurred_at=MARCH, sources=[spoken])

    found = await photos_of(session, household, entry)

    assert [p["message_id"] for p in found] == [nearby.id]
    assert found[0]["cited"] is False


@pytest.mark.db
async def test_a_photo_penny_found_irrelevant_never_reaches_a_clinical_entry(
    session: AsyncSession,
) -> None:
    """THE HOLIDAY SNAP TEST, and the reason the marker is required rather than the bytes.

    `media._derive` is told to answer with an empty string for "a meme, a screenshot, a photo of a
    dog", and when it does it writes no text and no marker while still keeping the file. So an
    undescribed stored photo is Penny's own judgement that this picture is not about this person's
    care, and time proximity must not be allowed to overrule it.
    """
    household = await make_household(session)
    anchor = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    await add_photo(session, household, sent_at=MARCH + timedelta(minutes=5), described=False)
    entry = await add_event(session, household, occurred_at=MARCH, sources=[anchor])

    assert await photos_of(session, household, entry) == []


@pytest.mark.db
async def test_a_cited_photo_is_shown_even_when_penny_could_not_describe_it(
    session: AsyncSession,
) -> None:
    """Over budget, an unreadable type, too large — the marker is missing and the model still
    attached the picture to this entry. One model call must not overrule another."""
    household = await make_household(session)
    picture = await add_photo(session, household, sent_at=MARCH, described=False)
    entry = await add_event(session, household, occurred_at=MARCH, sources=[picture])

    found = await photos_of(session, household, entry)

    assert [p["message_id"] for p in found] == [picture.id]
    assert found[0]["description"] is None


@pytest.mark.db
async def test_a_video_is_not_a_photograph(session: AsyncSession) -> None:
    household = await make_household(session)
    anchor = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    await add_photo(session, household, sent_at=MARCH, message_type="video")
    entry = await add_event(session, household, occurred_at=MARCH, sources=[anchor])

    assert await photos_of(session, household, entry) == []


# --- the window --------------------------------------------------------------------------


@pytest.mark.db
async def test_a_cited_photo_survives_a_source_span_wider_than_the_window(
    session: AsyncSession,
) -> None:
    """THE BUG THE OUTSIDE VOICE CAUGHT, as a test.

    `extraction.persist` unions `source_message_ids` on every re-extraction, so an entry discussed
    on Monday and again on Friday has a week-wide span. The window trims to 24h from the RECENT
    end, so Monday's photograph is outside it — and Monday's photograph is the one the model was
    most certain about. Cited photographs bypass the window entirely for exactly this reason.
    """
    household = await make_household(session)
    monday_photo = await add_photo(session, household, sent_at=MARCH)
    friday_text = await add_photo(
        session, household, sent_at=MARCH + timedelta(days=4), described=False, message_type="text"
    )
    entry = await add_event(
        session, household, occurred_at=MARCH, sources=[monday_photo, friday_text]
    )

    found = await photos_of(session, household, entry)

    assert [p["message_id"] for p in found] == [monday_photo.id]


@pytest.mark.db
async def test_an_uncited_photo_outside_the_window_is_not_picked_up(
    session: AsyncSession,
) -> None:
    household = await make_household(session)
    anchor = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    await add_photo(session, household, sent_at=MARCH - timedelta(days=3))
    entry = await add_event(session, household, occurred_at=MARCH, sources=[anchor])

    assert await photos_of(session, household, entry) == []


@pytest.mark.db
async def test_a_future_dated_appointment_still_finds_its_conversation(
    session: AsyncSession,
) -> None:
    """The window is anchored to the SOURCE MESSAGES, never to `occurred_at`.

    An appointment booked today for three weeks' time has `occurred_at` three weeks out and every
    source message from today. Anchoring on `occurred_at` produces a window containing nothing,
    silently, forever — and appointments are most of the record.
    """
    household = await make_household(session)
    letter = await add_photo(session, household, sent_at=MARCH)
    entry = await add_event(session, household, occurred_at=MARCH + timedelta(days=21), sources=[letter])

    found = await photos_of(session, household, entry)

    assert [p["message_id"] for p in found] == [letter.id]


# --- tenancy -----------------------------------------------------------------------------


@pytest.mark.db
async def test_another_households_photograph_never_reaches_this_entry(
    session: AsyncSession,
) -> None:
    """THE HIGHEST-CONSEQUENCE ASSERTION IN THIS FILE.

    `household_id` is the entire tenancy mechanism and there is no RLS behind it. This query hands
    back message ids that become `/api/media/{id}` URLs, so a dropped predicate here is one
    family's photograph of an 84-year-old's body shown to another family. The two households have
    messages at identical timestamps on purpose.
    """
    ours = await make_household(session)
    theirs = await make_household(session)
    anchor = await add_photo(session, ours, sent_at=MARCH, described=False, message_type="text")
    await add_photo(session, theirs, sent_at=MARCH + timedelta(minutes=5))
    entry = await add_event(session, ours, occurred_at=MARCH, sources=[anchor])

    assert await photos_of(session, ours, entry) == []


# --- the cap, the counts, and the dedup ---------------------------------------------------


@pytest.mark.db
async def test_the_cap_holds_and_the_rest_are_counted_not_dropped(
    session: AsyncSession,
) -> None:
    """Seven photographs, four shown, three named. A silent truncation in a medical record is the
    same failure `PendingReread` was built to prevent, one level down."""
    household = await make_household(session)
    anchor = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    for minute in range(7):
        await add_photo(session, household, sent_at=MARCH + timedelta(minutes=minute + 1))
    entry = await add_event(session, household, occurred_at=MARCH, sources=[anchor])

    found = (await photo_rule.photos_for_page(session, household.id, [entry.id]))[entry.id]

    assert len(found.photos) == FEED_PHOTOS_PER_EVENT
    assert found.withheld == 7 - FEED_PHOTOS_PER_EVENT


@pytest.mark.db
async def test_one_photograph_belongs_to_exactly_one_entry(session: AsyncSession) -> None:
    """A busy day makes several entries from one conversation and their windows overlap almost
    completely. Without this the same picture renders down five cards in a row."""
    household = await make_household(session)
    anchor = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    picture = await add_photo(session, household, sent_at=MARCH + timedelta(minutes=10))
    first = await add_event(session, household, occurred_at=MARCH, sources=[anchor])
    second = await add_event(
        session, household, occurred_at=MARCH + timedelta(minutes=30), sources=[anchor], title="Ate well"
    )

    found = await photo_rule.photos_for_page(session, household.id, [first.id, second.id])

    shown = [p["message_id"] for entry_photos in found.values() for p in entry_photos.photos]
    assert shown.count(picture.id) == 1


@pytest.mark.db
async def test_the_entry_that_cited_a_photo_wins_it_from_the_entry_that_is_merely_nearer(
    session: AsyncSession,
) -> None:
    household = await make_household(session)
    picture = await add_photo(session, household, sent_at=MARCH)
    citing = await add_event(
        session, household, occurred_at=MARCH + timedelta(hours=6), sources=[picture]
    )
    nearer = await add_event(session, household, occurred_at=MARCH, sources=[picture], title="Nearer")
    # `nearer` shares the source, so it is a candidate too and its `occurred_at` is closer.
    found = await photo_rule.photos_for_page(session, household.id, [citing.id, nearer.id])

    holders = [eid for eid, ps in found.items() if any(p["message_id"] == picture.id for p in ps.photos)]
    assert len(holders) == 1
    # Both cite it, so the tiebreak is the stable one — never a coin flip between polls.
    assert holders[0] in {citing.id, nearer.id}
    again = await photo_rule.photos_for_page(session, household.id, [citing.id, nearer.id])
    repeat = [eid for eid, ps in again.items() if any(p["message_id"] == picture.id for p in ps.photos)]
    assert repeat == holders, "the same entry must win on every poll, or the photo flickers"


@pytest.mark.db
async def test_an_import_placeholder_is_counted_and_explained_rather_than_dropped(
    session: AsyncSession,
) -> None:
    household = await make_household(session)
    placeholder = await add_photo(session, household, sent_at=MARCH, described=False, stored=False)
    entry = await add_event(session, household, occurred_at=MARCH, sources=[placeholder])

    found = (await photo_rule.photos_for_page(session, household.id, [entry.id]))[entry.id]

    assert found.photos == []
    assert found.unavailable == 1


# --- the shape of the work ----------------------------------------------------------------


@pytest.mark.db
async def test_a_whole_page_costs_the_same_two_statements_as_one_entry(
    session: AsyncSession,
) -> None:
    """The N+1 guard. Photographs are attached per page, never per entry.

    Two statements: the candidates, and the placeholder counts. That number must not grow with
    the size of the page — the feed is refetched every sixty seconds by every open tab.
    """
    household = await make_household(session)
    entries = []
    for day in range(12):
        anchor = await add_photo(
            session, household, sent_at=MARCH + timedelta(days=day), described=False, message_type="text"
        )
        await add_photo(session, household, sent_at=MARCH + timedelta(days=day, minutes=5))
        entries.append(
            await add_event(session, household, occurred_at=MARCH + timedelta(days=day), sources=[anchor])
        )

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        await photo_rule.photos_for_page(session, household.id, [e.id for e in entries])
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 2, f"expected 2 statements for 12 entries, got {len(statements)}"


@pytest.mark.db
async def test_the_feed_carries_photographs_and_both_counts(session: AsyncSession) -> None:
    """End to end through the real route function, not just the query."""
    household = await make_household(session)
    picture = await add_photo(session, household, sent_at=MARCH)
    await add_event(session, household, occurred_at=MARCH, sources=[picture])

    page = await read_feed(session, household)

    assert len(page.events) == 1
    assert [p.message_id for p in page.events[0].photos] == [picture.id]
    assert page.events[0].photos_withheld == 0
    assert page.events[0].photos_unavailable == 0


@pytest.mark.db
async def test_an_entry_with_no_photographs_says_so_with_an_empty_list(
    session: AsyncSession,
) -> None:
    """`[]` and two zeroes, never a missing key — a client must not have to tell "no photos" apart
    from "this response predates photos"."""
    household = await make_household(session)
    text = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    await add_event(session, household, occurred_at=MARCH, sources=[text])

    page = await read_feed(session, household)

    assert page.events[0].photos == []
    assert page.events[0].photos_withheld == 0
    assert page.events[0].photos_unavailable == 0


@pytest.mark.db
async def test_the_lightbox_endpoint_returns_everything_the_cap_hid(
    session: AsyncSession,
) -> None:
    household = await make_household(session)
    anchor = await add_photo(session, household, sent_at=MARCH, described=False, message_type="text")
    for minute in range(7):
        await add_photo(session, household, sent_at=MARCH + timedelta(minutes=minute + 1))
    entry = await add_event(session, household, occurred_at=MARCH, sources=[anchor])

    answer = await get_event_photos(
        entry.id, HouseholdCtx(id=household.id, household=household), session
    )

    assert len(answer.photos) == 7


@pytest.mark.db
async def test_the_lightbox_endpoint_refuses_another_households_entry(
    session: AsyncSession,
) -> None:
    """404 and not 403: a 403 confirms the entry exists somewhere, which is the sentence an
    attacker enumerating ids is trying to buy."""
    from app.errors import NotFoundError

    ours = await make_household(session)
    theirs = await make_household(session)
    picture = await add_photo(session, theirs, sent_at=MARCH)
    entry = await add_event(session, theirs, occurred_at=MARCH, sources=[picture])

    with pytest.raises(NotFoundError):
        await get_event_photos(entry.id, HouseholdCtx(id=ours.id, household=ours), session)
