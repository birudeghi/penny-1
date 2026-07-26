"""THE TESTS FOR THE LEAK THAT WOULD END THIS PRODUCT.

`tests/test_join_safety.py` exists because a boundary failed in production and put a login
password into strangers' group chats. This file is written the same way, for a boundary that has
not failed yet and must not: **`GET /api/media/{message_id}` hands back a photograph of an
84-year-old's body.**

Every other cross-tenant leak in this API costs a family a title or a date. This one costs them
a picture their daughter took of their leg to show a district nurse — and there is no version of
"we have fixed it" that gives that back. So the property under test is not "media works". It is
**a household can read its own attachments and nothing else, and cannot even learn that anything
else exists**:

    our own message, with a file          -> 200, the exact bytes, correct type
    ANOTHER household's message id        -> 404, byte-identical to a random uuid
    a random uuid                         -> 404
    our own message with no file          -> 404, the same one
    no session at all                     -> 401, no photo, no hint
    a session we revoked                  -> 401
    a "photo" whose type says text/html   -> served as an octet-stream download, never inline
    the household is deleted              -> the files are GONE from the volume
    ...and the other household's are not

These run against REAL Postgres through the REAL app with a REAL signed session cookie, and skip
without `PENNY_TEST_DATABASE_URL`. Bytes on disk and HTTP responses are what is asserted — never
log lines, and never an internal function's return value, because the thing being protected is
the thing on the wire.

WHY THE TWO 404s ARE COMPARED TO EACH OTHER RATHER THAN JUST CHECKED FOR "404". A status code is
the least of it. If another household's id came back with a different body, a different length,
or a different header, the route would be an existence oracle — "that message is real, it just
isn't yours" — and an attacker with a list of UUIDs could sort them into "yours" and "someone
else's" without ever seeing a photo. So the assertion is equality of the whole response, and
that is deliberately a stronger statement than `status_code == 404`.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.db
from app.config import Settings
from app.db import to_asyncpg_url
from app.main import app as penny_app
from app.media import media_path, store_media
from app.models import Household, Message
from app.security import SESSION_COOKIE_NAME, hash_password

pytestmark = pytest.mark.db

SESSION_SECRET = "test-session-secret-value-32-chars-min"
PASSWORD = "a-shared-family-passphrase"

# The photograph. Never decoded by anything — but a real length and a JPEG's magic number, so
# what is compared on the way out is a payload and not a token.
PHOTO = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 12
# A second one, so "the right household got the right bytes" is a real claim rather than both
# families being handed the same buffer.
OTHER_PHOTO = b"\xff\xd8\xff\xe1" + bytes(range(255, -1, -1)) * 12


# --- plumbing ---------------------------------------------------------------------------


def query(db_url: str, fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    """Run one statement on its OWN engine, in its own loop.

    The app's engine lives in the TestClient's portal thread and its asyncpg connections are
    bound to that loop; borrowing it from the test thread gives "attached to a different loop"
    at random.
    """

    async def run() -> Any:
        engine = create_async_engine(to_asyncpg_url(db_url))
        try:
            async with AsyncSession(engine) as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def wait_for(predicate: Callable[[], bool], timeout: float = 25.0) -> bool:
    """The purge is detached from the request that asked for it. Poll for it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@dataclass(frozen=True)
class Family:
    """One household, its login, and the one photo it has sent."""

    household_id: UUID
    username: str
    message_id: UUID
    photo: bytes
    # A message of theirs with NO file: a text message, which is the overwhelming majority of
    # rows and must 404 exactly like a stranger's id.
    text_message_id: UUID


@pytest.fixture
def volume(tmp_path: Path) -> Path:
    """MEDIA_ROOT for this test. A directory, not a Railway volume, and empty to start with."""
    return tmp_path / "media"


@pytest.fixture
def settings(
    db_url: str, volume: Path, settings_override: Callable[..., Settings]
) -> Iterator[Settings]:
    """The real app's configuration, pointed at the test database and this temporary volume."""
    built = settings_override(
        env="test",
        database_url=db_url,
        test_database_url=db_url,
        session_secret=SESSION_SECRET,
        media_root=str(volume),
        # Nothing here goes near WhatsApp or a model; onboarding off so no path can provision.
        onboarding_enabled=False,
    )
    app.db.get_engine.cache_clear()
    app.db.get_sessionmaker.cache_clear()
    yield built
    app.db.get_engine.cache_clear()
    app.db.get_sessionmaker.cache_clear()


def _make_family(db_url: str, label: str, photo: bytes) -> Family:
    """A household with one image message that has a file, and one text message that has none.

    The row is written directly rather than driven through the GOWA webhook on purpose: what is
    under test is the READ boundary, and going through ingestion would put a sidecar, a model
    and a background task between the test and the thing it is asserting. `tests/test_media.py`
    and `tests/test_voice_end_to_end.py` cover the write path.
    """
    household_id, message_id, text_message_id = uuid4(), uuid4(), uuid4()
    username = f"media-{label}-{household_id.hex[:8]}"

    async def create(session: AsyncSession) -> None:
        session.add(
            Household(
                id=household_id,
                username=username,
                password_hash=hash_password(PASSWORD),
                name=f"The {label} family",
                care_recipient_name="Margaret",
                timezone="Europe/London",
            )
        )
        for index, (row_id, message_type, content_type) in enumerate(
            [(message_id, "image", "image/jpeg"), (text_message_id, "text", None)]
        ):
            session.add(
                Message(
                    id=row_id,
                    household_id=household_id,
                    provider="gowa",
                    provider_message_id=f"3EB0{label.upper()}{index}",
                    content_hash=hashlib.sha256(f"{row_id}".encode()).digest(),
                    sender_display_name="Sarah",
                    sent_at=datetime.now(UTC),
                    message_type=message_type,
                    text=None if message_type == "image" else "on my way",
                    payload={},
                    media_content_type=content_type,
                    media_byte_count=len(photo) if content_type else None,
                    media_stored_at=datetime.now(UTC) if content_type else None,
                )
            )
        await session.commit()

    query(db_url, create)
    # Written through the real store, so the layout the route resolves and the layout the
    # writer uses can never be two different opinions in this test.
    assert asyncio.run(store_media(household_id, message_id, photo, "image/jpeg"))
    return Family(household_id, username, message_id, photo, text_message_id)


def _delete_family(db_url: str, household_id: UUID) -> None:
    async def drop(session: AsyncSession) -> None:
        # Timed: a background task still holding row locks would hang the suite rather than
        # fail it.
        await session.execute(sa.text("SET LOCAL lock_timeout = '15s'"))
        await session.execute(sa.delete(Household).where(Household.id == household_id))
        await session.commit()

    query(db_url, drop)


@pytest.fixture
def ours(db_url: str, settings: Settings) -> Iterator[Family]:
    family = _make_family(db_url, "ours", PHOTO)
    yield family
    _delete_family(db_url, family.household_id)


@pytest.fixture
def theirs(db_url: str, settings: Settings) -> Iterator[Family]:
    """A completely unrelated household. It exists in this file only to be unreachable."""
    family = _make_family(db_url, "theirs", OTHER_PHOTO)
    yield family
    _delete_family(db_url, family.household_id)


class Browser:
    """One family's signed-in session, carried as a cookie rather than as a client.

    Every request re-sends the cookie explicitly instead of relying on a jar, so two of these
    can interleave over the same underlying client without either one inheriting the other's
    session — which is the whole point of the symmetry test below.
    """

    def __init__(self, client: TestClient, cookie: str) -> None:
        self._client = client
        self._cookie = cookie

    @property
    def cookies(self) -> dict[str, str]:
        return {SESSION_COOKIE_NAME: self._cookie}

    def get(self, url: str) -> Any:
        return self._client.get(url, cookies=self.cookies)

    def post(self, url: str) -> Any:
        return self._client.post(url, cookies=self.cookies)

    def delete(self, url: str) -> Any:
        return self._client.delete(url, cookies=self.cookies)


@pytest.fixture
def sign_in(settings: Settings) -> Iterator[Callable[[Family], Browser]]:
    """A signed-in browser for one family, through the REAL login route.

    The cookie is minted by `POST /api/auth/login` rather than forged here, because the claim
    this file makes is about the session mechanism every other read uses — a hand-built cookie
    would be testing a mechanism nothing in production has.

    ONE `TestClient`, MANY BROWSERS, and that is a constraint rather than a preference. Each
    `TestClient` runs the app on an event loop of its own, while the connection pool underneath
    is created once and bound to whichever loop got there first — so a second client that
    actually reaches Postgres dies on "attached to a different loop" before the test can say
    anything about tenancy. Two families therefore share one client and are told apart by the
    cookie on each request, which is closer to the truth anyway: one process, two sessions.
    """
    with TestClient(penny_app) as shared:

        def _sign_in(family: Family) -> Browser:
            response = shared.post(
                "/api/auth/login", json={"username": family.username, "password": PASSWORD}
            )
            assert response.status_code == 204, response.text
            cookie = shared.cookies.get(SESSION_COOKIE_NAME)
            assert cookie
            # Off the shared jar immediately: a cookie left on the client would ride along on
            # the NEXT family's requests and quietly make every refusal below pass for the
            # wrong reason.
            shared.cookies.delete(SESSION_COOKIE_NAME)
            return Browser(shared, cookie)

        yield _sign_in


def fingerprint(response: Any) -> tuple[int, bytes, str | None]:
    """Everything a caller could learn from a refusal: the status, the body, the type.

    Two refusals with the same fingerprint are indistinguishable to anyone on the other end of
    the socket, which is the property this file is really about.
    """
    return (response.status_code, response.content, response.headers.get("content-type"))


# --- it has to work, or none of the refusals below prove anything -------------------------


def test_a_household_can_open_its_own_photo(
    ours: Family, sign_in: Callable[[Family], Browser]
) -> None:
    """The feature. A family opens the picture they sent, gets those exact bytes back, and the
    browser is told what it is — a photo that will not display is the same as no photo.

    The cache headers are asserted here rather than in a test of their own because they are
    part of what "correct" means for this response: a photograph of somebody's body must not be
    left in the disk cache of the shared family laptop after they sign out.
    """
    client = sign_in(ours)

    response = client.get(f"/api/media/{ours.message_id}")

    assert response.status_code == 200
    assert response.content == PHOTO
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    # From the app-wide middleware, and load-bearing here: without it a browser may decide for
    # itself what these bytes are, which is exactly what the content-type table exists to stop.
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "inline" in response.headers["content-disposition"]


# --- the boundary --------------------------------------------------------------------------


def test_another_households_message_is_indistinguishable_from_one_that_never_existed(
    ours: Family,
    theirs: Family,
    sign_in: Callable[[Family], Browser],
) -> None:
    """THE TEST THIS FILE EXISTS FOR.

    A real message id belonging to a real other household, and a UUID that names nothing at
    all, must produce the SAME BYTES. Not both 404 — the same response, so that an attacker
    holding a list of ids cannot sort it into "exists" and "does not" by watching the answers.

    A 403 would be the classic version of this mistake and is impossible by construction here:
    `app.errors` collapses 403 to 404 and renders one fixed sentence for every 404.

    The last assertion is the one that says the leak did not happen: their photograph is still
    on the volume, so this is a refusal to serve rather than a lookup that missed.
    """
    client = sign_in(ours)

    stranger = client.get(f"/api/media/{theirs.message_id}")
    nowhere = client.get(f"/api/media/{uuid4()}")

    assert stranger.status_code == 404
    assert fingerprint(stranger) == fingerprint(nowhere)
    assert stranger.json() == {"detail": "Not found."}
    assert OTHER_PHOTO not in stranger.content
    assert media_path(theirs.household_id, theirs.message_id).read_bytes() == OTHER_PHOTO


def test_each_household_is_handed_its_own_photograph_and_not_the_other_one(
    ours: Family,
    theirs: Family,
    sign_in: Callable[[Family], Browser],
) -> None:
    """Both directions at once, with two live sessions in the same process.

    Symmetry matters: a bug that pinned the household to the first request the process served —
    a cached context, a module-level id — would pass every single-tenant test in this suite and
    hand the second family the first family's picture.
    """
    us, them = sign_in(ours), sign_in(theirs)

    assert us.get(f"/api/media/{ours.message_id}").content == PHOTO
    assert them.get(f"/api/media/{theirs.message_id}").content == OTHER_PHOTO
    assert us.get(f"/api/media/{theirs.message_id}").status_code == 404
    assert them.get(f"/api/media/{ours.message_id}").status_code == 404


def test_our_own_message_with_no_file_is_the_same_404_as_a_strangers(
    ours: Family,
    theirs: Family,
    sign_in: Callable[[Family], Browser],
) -> None:
    """Nearly every message is a text message with nothing to serve, and the answer for one of
    those must not be a different shape from the answer for someone else's photo. Otherwise the
    route answers "that id is one of yours" for free, which is half of what an attacker wanted.
    """
    client = sign_in(ours)

    mine_without_a_file = client.get(f"/api/media/{ours.text_message_id}")

    assert mine_without_a_file.status_code == 404
    assert fingerprint(mine_without_a_file) == fingerprint(
        client.get(f"/api/media/{theirs.message_id}")
    )
    assert fingerprint(mine_without_a_file) == fingerprint(client.get(f"/api/media/{uuid4()}"))


def test_a_row_whose_file_has_vanished_from_the_volume_is_the_same_404(
    ours: Family, sign_in: Callable[[Family], Browser]
) -> None:
    """The database and the volume CAN disagree — a restored snapshot, a reattached volume, a
    manual clean-up. The row still says "there are bytes", and the honest answer is the same
    404 rather than a 500 that confirms the message exists.
    """
    client = sign_in(ours)
    media_path(ours.household_id, ours.message_id).unlink()

    gone = client.get(f"/api/media/{ours.message_id}")

    assert gone.status_code == 404
    assert fingerprint(gone) == fingerprint(client.get(f"/api/media/{uuid4()}"))


def test_without_a_session_there_is_no_photo_and_no_information_either(
    ours: Family,
    theirs: Family,
) -> None:
    """No cookie, a forged cookie and a cookie for a household that is gone are all 401.

    Not 404: the caller is not being told anything about the id, they are being told to sign
    in — and the id in the path is never even looked up, because `require_household` runs
    first. What must not happen is a 401 for one id and a 404 for another, which would answer
    the question without a session at all.
    """
    anonymous = TestClient(penny_app)

    with anonymous:
        no_cookie = anonymous.get(f"/api/media/{ours.message_id}")
        anonymous.cookies.set(SESSION_COOKIE_NAME, "not.a.real.signature")
        forged = anonymous.get(f"/api/media/{theirs.message_id}")
        unknown_id = anonymous.get(f"/api/media/{uuid4()}")

    assert no_cookie.status_code == 401
    assert fingerprint(no_cookie) == fingerprint(forged) == fingerprint(unknown_id)
    assert PHOTO not in no_cookie.content


def test_signing_out_everywhere_closes_the_photo_too(
    ours: Family, sign_in: Callable[[Family], Browser]
) -> None:
    """ "Dad's phone was stolen" has to cover the photographs, and it does — but only because
    this route takes the same `CurrentHousehold` dependency as every other read rather than
    checking the cookie in its own way. A bespoke check here would keep working after a
    revocation, which is precisely the button a family pressed to stop it.
    """
    client = sign_in(ours)
    assert client.get(f"/api/media/{ours.message_id}").status_code == 200

    assert client.post("/api/household/sign-out-everywhere").status_code == 204

    assert client.get(f"/api/media/{ours.message_id}").status_code == 401


# --- what a browser is told these bytes are -------------------------------------------------


def test_a_file_that_claims_to_be_html_is_served_as_a_download_and_never_rendered(
    db_url: str,
    ours: Family,
    sign_in: Callable[[Family], Browser],
) -> None:
    """The content type came from a stranger's phone by way of a group chat.

    Honouring it would let anyone in a family's WhatsApp group serve HTML from Penny's own
    origin — same-site, session cookie right there, CSP `script-src 'self'` no help at all
    because the script would BE 'self'. So the recorded type is mapped through a fixed table
    and anything unknown becomes an `application/octet-stream` attachment.
    """

    async def relabel(session: AsyncSession) -> None:
        await session.execute(
            sa.update(Message)
            .where(Message.id == ours.message_id)
            .values(media_content_type="text/html")
        )
        await session.commit()

    query(db_url, relabel)
    client = sign_in(ours)

    response = client.get(f"/api/media/{ours.message_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    # The file itself is unchanged: we relabel what we SAY it is, never what we stored.
    assert response.content == PHOTO


# --- retention -------------------------------------------------------------------------------


def test_deleting_a_household_takes_its_photographs_off_the_volume(
    db_url: str,
    ours: Family,
    theirs: Family,
    sign_in: Callable[[Family], Browser],
) -> None:
    """ "Delete everything" has to mean the files too.

    A cascade in Postgres takes every row and cannot touch a volume, so before this the
    photographs survived the household — unreachable, but present, which is not what the family
    was told and not what the law they may be exercising means. The purge is detached and waits
    for the delete to COMMIT, so this polls rather than asserting instantly.

    The second half is as important as the first: the other family's directory is untouched.
    A deletion that over-reaches is a worse bug than one that under-reaches, and "keyed by
    household id" is the property that makes over-reaching impossible.
    """
    client = sign_in(ours)
    mine = media_path(ours.household_id, ours.message_id)
    yours = media_path(theirs.household_id, theirs.message_id)
    assert mine.exists() and yours.exists()

    assert client.delete("/api/household").status_code == 204

    assert wait_for(lambda: not mine.exists()), "the photographs outlived the household"
    assert not mine.parent.exists(), "the household's directory is still there"
    assert yours.read_bytes() == OTHER_PHOTO, "another household's photos were swept up"

    # And the rows are gone with them, which is what the purge waited for.
    assert query(db_url, lambda s: s.get(Household, ours.household_id)) is None


def test_a_failed_delete_never_takes_the_photographs_with_it(
    ours: Family,
    settings: Settings,
) -> None:
    """The reason the purge waits for the commit instead of running inline.

    `get_session` commits AFTER the handler returns, so a purge that ran during the request
    would already have destroyed a living family's photographs by the time a later failure
    rolled the transaction back. Here the household simply never goes away — the request that
    would have deleted it was never made — and `purge_after_delete` must give up rather than
    delete on the strength of having been asked.
    """
    from app import media

    mine = media_path(ours.household_id, ours.message_id)
    assert mine.exists()

    # The real task, run against a household that is still very much there. It waits, times
    # out, and refuses; shortened so the test does not sit through the production ceiling.
    original_tries = media._DELETE_WAIT_TRIES
    media._DELETE_WAIT_TRIES = 2
    try:
        asyncio.run(media.purge_after_delete(ours.household_id))
    finally:
        media._DELETE_WAIT_TRIES = original_tries

    assert mine.read_bytes() == PHOTO, "a household that still exists lost its photographs"
