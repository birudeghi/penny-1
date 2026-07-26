"""A photo arriving, becoming a file and a sentence, and costing exactly one call.

`tests/test_media.py` proves the rules offline and `tests/test_media_boundary.py` proves nobody
else can read the result. This is the middle: the real `derive_media_text` against real
Postgres, pulling real bytes over real HTTP from a fake sidecar, writing a real file to a real
directory and a real `llm_runs` row — with ONE thing stubbed, the OpenAI call, because that is
the single place money is spent and the only part of the path we are forbidden to exercise.

It is stubbed as a MODULE ATTRIBUTE (`app.media.OpenAIMediaReader`), not injected, for the same
reason `tests/test_voice_end_to_end.py` does it that way: production calls
`derive_media_text(session, message_id)` with two positional arguments and constructs its own
reader, so patching the attribute is what makes these tests cover the production call rather
than a test-only overload of it.

THE PROPERTY RUNNING THROUGH ALL OF IT: the file and the words succeed and fail SEPARATELY. A
photo we kept but could not describe is still a photo the family can open; a description we
could not keep the bytes for is still the sentence that gets the event into the record. Several
tests below exist only to hold those two apart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.media as media
from app.config import Settings
from app.db import to_asyncpg_url
from app.media import (
    DERIVATION_META_KEY,
    DERIVE_PURPOSE,
    DERIVED_MARKER,
    RawDerivation,
    derive_media_text,
    media_path,
)
from app.models import Household, LlmRun, Message

pytestmark = pytest.mark.db

# The bytes of the photo. Never decoded — the reader is stubbed — but a real length, because
# the byte count reaches the `messages` row, the size cap and the log line.
PHOTO = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 16
# The sentence a family would read in their feed, in the register the prompt asks for:
# what is visible, no diagnosis.
DESCRIPTION = "A red patch about 4cm across on the outside of the left lower leg."


# --- the fake sidecar ------------------------------------------------------------------------


class _FakeGowa:
    """A real HTTP server on 127.0.0.1 speaking GOWA's media route.

    A real socket rather than a patched client because the likeliest thing to be wrong here is
    not our logic but the HTTP shape — the URL, the auth header, whether a `content-type` with
    parameters survives, whether a streamed body is read to the end. A mock that answers
    whatever it is asked cannot fail any of those.
    """

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.status = 200
        self.content_type = "image/jpeg"
        self.body = PHOTO
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # stdlib's spelling, not ours
                server.requests.append(self.path)
                if server.status != 200:
                    payload = json.dumps({"code": "NOT_FOUND", "message": "gone"}).encode()
                    self.send_response(server.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("Content-Type", server.content_type)
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)

            def log_message(self, *_: Any) -> None:
                """Silence. The stdlib handler writes every request to stderr."""

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def downloads(self) -> list[str]:
        return [path for path in self.requests if path.endswith("/download")]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def gowa_server() -> Iterator[_FakeGowa]:
    server = _FakeGowa()
    yield server
    server.close()


# --- the one stub ------------------------------------------------------------------------------


class _StubReader:
    """`OpenAIMediaReader` without OpenAI. Records what it was handed."""

    calls: list[dict[str, Any]] = []  # class-level: production instantiates the class itself
    text: str = DESCRIPTION

    async def read(self, *, model: str, kind: str, content_type: str, data: bytes) -> RawDerivation:
        type(self).calls.append(
            {"model": model, "kind": kind, "content_type": content_type, "byte_count": len(data)}
        )
        # A plausible bill for one photo: an image is ~1,000 input tokens at this size.
        return RawDerivation(
            text=type(self).text, input_tokens=1_000, output_tokens=100, reasoning_tokens=40
        )


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_StubReader]]:
    _StubReader.calls = []
    _StubReader.text = DESCRIPTION
    monkeypatch.setattr(media, "OpenAIMediaReader", _StubReader)
    yield _StubReader
    _StubReader.calls = []


# --- database plumbing ---------------------------------------------------------------------------


def query(db_url: str, fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    """One statement on its OWN engine, in its own loop."""

    async def run() -> Any:
        engine = create_async_engine(to_asyncpg_url(db_url))
        try:
            async with AsyncSession(engine) as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
def volume(tmp_path: Path) -> Path:
    return tmp_path / "media"


@pytest.fixture
def settings(
    db_url: str,
    volume: Path,
    gowa_server: _FakeGowa,
    settings_override: Callable[..., Settings],
) -> Settings:
    return settings_override(
        env="test",
        database_url=db_url,
        test_database_url=db_url,
        session_secret="test-session-secret-value-32-chars-min",
        gowa_url=gowa_server.url,
        gowa_basic_auth="penny:secret",
        openai_api_key="sk-test-not-a-real-key",
        media_root=str(volume),
        derive_media_text=True,
        onboarding_enabled=False,
    )


@pytest.fixture
def household_id(db_url: str, settings: Settings) -> Iterator[uuid.UUID]:
    new_id = uuid.uuid4()

    async def create(session: AsyncSession) -> None:
        session.add(
            Household(
                id=new_id,
                username=f"media-{new_id.hex[:8]}",
                password_hash="x",
                name="Media",
                care_recipient_name="Margaret",
                timezone="Europe/London",
            )
        )
        await session.commit()

    async def drop(session: AsyncSession) -> None:
        await session.execute(sa.text("SET LOCAL lock_timeout = '15s'"))
        await session.execute(sa.delete(Household).where(Household.id == new_id))
        await session.commit()

    query(db_url, create)
    yield new_id
    query(db_url, drop)


@pytest.fixture
def photo_message(db_url: str, household_id: uuid.UUID) -> uuid.UUID:
    """An ingested image with no text — exactly what `webhooks._classify` produces."""
    return _add_message(db_url, household_id, message_type="image")


def _add_message(
    db_url: str,
    household_id: uuid.UUID,
    *,
    message_type: str,
    text: str | None = None,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    message_id = uuid.uuid4()

    async def create(session: AsyncSession) -> None:
        session.add(
            Message(
                id=message_id,
                household_id=household_id,
                provider="gowa",
                provider_message_id=f"3EB0{message_id.hex[:10].upper()}",
                content_hash=hashlib.sha256(str(message_id).encode()).digest(),
                sender_display_name="Sarah",
                sent_at=datetime.now(UTC),
                message_type=message_type,
                text=text,
                payload=payload or {},
            )
        )
        await session.commit()

    query(db_url, create)
    return message_id


def derive(db_url: str, message_id: uuid.UUID) -> Any:
    """The production call, in a transaction this test commits — as the webhook's task does."""

    async def run(session: AsyncSession) -> Any:
        result = await derive_media_text(session, message_id)
        await session.commit()
        return result

    return query(db_url, run)


def stored_message(db_url: str, message_id: uuid.UUID) -> Message:
    return query(db_url, lambda s: s.get(Message, message_id))


def runs(db_url: str, household_id: uuid.UUID) -> list[LlmRun]:
    return list(
        query(
            db_url,
            lambda s: s.scalars(
                sa.select(LlmRun).where(
                    LlmRun.household_id == household_id, LlmRun.purpose == DERIVE_PURPOSE
                )
            ),
        ).all()
    )


# --- the whole path -------------------------------------------------------------------------------


def test_a_photo_is_kept_described_and_billed_exactly_once(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    gowa_server: _FakeGowa,
    reader: type[_StubReader],
    volume: Path,
) -> None:
    """The feature, end to end, in one run — because it IS one run.

    Splitting this would re-prove the arrival four times and prove the SEQUENCE none of them:
    the bytes come from the sidecar, land on the volume, are recorded on the row, are sent to a
    model, and come back as words on the same message with a marker beside them and a cost in
    the ledger.
    """
    derivation = derive(db_url, photo_message)

    assert derivation is not None
    assert derivation.text == DESCRIPTION
    assert derivation.stored is True

    # (1) The bytes really came from the fake sidecar, on the documented route, and only once.
    assert len(gowa_server.downloads) == 1
    # ...and the encrypted mmg.whatsapp.net URL was never fetched: nothing else could have
    # produced these bytes.
    [call] = reader.calls
    assert call["byte_count"] == len(PHOTO)
    assert call["kind"] == "image"
    assert call["content_type"] == "image/jpeg"

    # (2) D12: the file is on the volume, under the household, and it is the whole photo.
    path = media_path(household_id, photo_message)
    assert path.read_bytes() == PHOTO
    assert path.parent.name == str(household_id)

    # (3) The row knows there are bytes — which is what the serving route reads.
    message = stored_message(db_url, photo_message)
    assert message.media_content_type == "image/jpeg"
    assert message.media_byte_count == len(PHOTO)
    assert message.media_stored_at is not None

    # (4) T9: the words are in `messages.text` with NO prefix on them, and the marker is what
    # says whose words they are. A "[photo] " in front would land in the content hash, the
    # prompt and the feed.
    assert message.text == DESCRIPTION
    assert not message.text.startswith("[")
    assert message.payload[DERIVED_MARKER] is True
    assert message.payload[DERIVATION_META_KEY]["kind"] == "image"
    # The metadata never contains the description itself.
    assert DESCRIPTION not in json.dumps(message.payload[DERIVATION_META_KEY])

    # (5) One audited run with a real cost: 1,000 in at $5/M and 100 out at $30/M.
    [run] = runs(db_url, household_id)
    assert run.status == "ok"
    assert run.cost_usd == Decimal("0.008000")
    assert run.input_tokens == 1_000
    assert run.output_tokens == 100
    assert run.household_id == household_id, "the budget guard sums by household and nothing else"


def test_a_second_attempt_on_the_same_photo_costs_nothing(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    gowa_server: _FakeGowa,
    reader: type[_StubReader],
) -> None:
    """GOWA re-delivers, tasks get retried, backfills get run twice. The marker plus the text
    already being set are what make the second attempt free BEFORE it downloads anything."""
    derive(db_url, photo_message)

    assert derive(db_url, photo_message) is None
    assert len(reader.calls) == 1, "a replay must not reach the model"
    assert len(gowa_server.downloads) == 1, "a replay must not re-download the photo"
    assert len(runs(db_url, household_id)) == 1, "a replay must not be billed"


def test_an_expired_media_file_leaves_the_message_exactly_as_it_was(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    gowa_server: _FakeGowa,
    reader: type[_StubReader],
) -> None:
    """GOWA 404s once WhatsApp has aged the file out. Everything must be as it was before this
    feature existed: no text, no marker, no file, no charge, and above all no exception — the
    webhook behind this owes GOWA a 2xx."""
    gowa_server.status = 404

    assert derive(db_url, photo_message) is None

    message = stored_message(db_url, photo_message)
    assert message.text is None
    assert DERIVED_MARKER not in message.payload
    assert message.media_content_type is None
    assert reader.calls == []
    assert runs(db_url, household_id) == []


def test_a_photo_we_cannot_read_is_still_kept(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    gowa_server: _FakeGowa,
    reader: type[_StubReader],
) -> None:
    """AN iPhone HEIC. No model call — it would come back a 400 — but the family can still open
    their own photo, which is the half of this feature that does not depend on a model at all.
    """
    gowa_server.content_type = "image/heic"

    assert derive(db_url, photo_message) is None

    message = stored_message(db_url, photo_message)
    assert message.media_content_type == "image/heic"
    assert media_path(household_id, photo_message).read_bytes() == PHOTO
    assert message.text is None
    assert DERIVED_MARKER not in message.payload
    assert reader.calls == []
    assert runs(db_url, household_id) == []


def test_a_household_over_its_monthly_budget_keeps_the_photo_and_buys_no_words(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    reader: type[_StubReader],
) -> None:
    """Photos are the cheapest way to spend a household's allowance — forty of them is a
    plausible afternoon and, unlike extraction, nothing batches them. So the same guard
    extraction runs under is asked BEFORE the call, and the refusal is audited rather than
    silent: an operator reading `llm_runs` can see why a family's photos stopped being
    described instead of inferring it from an absence.
    """

    async def overspend(session: AsyncSession) -> None:
        session.add(
            LlmRun(
                household_id=household_id,
                purpose="extract",
                status="ok",
                model="gpt-5.5-2026-04-23",
                cost_usd=Decimal("999"),
            )
        )
        await session.commit()

    query(db_url, overspend)

    assert derive(db_url, photo_message) is None

    assert reader.calls == [], "an over-budget household must not reach the model"
    assert media_path(household_id, photo_message).read_bytes() == PHOTO, (
        "the file is not what costs money and must still be kept"
    )
    [denial] = [r for r in runs(db_url, household_id) if r.status == "budget_denied"]
    assert denial.cost_usd == Decimal("0")


def test_an_attachment_with_nothing_in_it_is_paid_for_and_left_as_a_photo(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    reader: type[_StubReader],
) -> None:
    """A meme, a screenshot, a photo of a dog. The instructions ask for an empty answer, and an
    empty answer must NOT set the marker — the marker means "this message carries a reading of
    an attachment". The call still happened, so it is still billed: a row that only appeared
    when the model said something useful would understate a household's spend.
    """
    _StubReader.text = "   "

    assert derive(db_url, photo_message) is None

    message = stored_message(db_url, photo_message)
    assert message.text is None
    assert DERIVED_MARKER not in message.payload
    [run] = runs(db_url, household_id)
    assert run.status == "empty"
    assert run.cost_usd > 0


def test_a_captioned_photo_keeps_the_senders_own_words(
    db_url: str,
    household_id: uuid.UUID,
    reader: type[_StubReader],
    gowa_server: _FakeGowa,
) -> None:
    """`messages.text` holds one thing, and when a person has already written something it is
    theirs. Overwriting "does this look infected?" with a machine's reading of the picture
    beside it would put a model's sentence in a relative's mouth in a health record."""
    captioned = _add_message(
        db_url, household_id, message_type="image", text="does this look infected?"
    )

    assert derive(db_url, captioned) is None

    assert stored_message(db_url, captioned).text == "does this look infected?"
    assert gowa_server.downloads == [], "a captioned photo must not even be downloaded"
    assert runs(db_url, household_id) == []


def test_with_no_volume_configured_the_words_still_reach_the_record(
    db_url: str,
    household_id: uuid.UUID,
    photo_message: uuid.UUID,
    reader: type[_StubReader],
    settings_override: Callable[..., Settings],
    gowa_server: _FakeGowa,
) -> None:
    """The other half of the independence, and the state every deploy without a volume is in.

    Keeping nothing must not mean reading nothing: the description is what gets a rash into the
    care record, and it costs no disk at all.
    """
    settings_override(
        env="test",
        database_url=db_url,
        test_database_url=db_url,
        gowa_url=gowa_server.url,
        gowa_basic_auth="penny:secret",
        openai_api_key="sk-test-not-a-real-key",
        media_root=None,
        derive_media_text=True,
    )

    derivation = derive(db_url, photo_message)

    assert derivation is not None
    assert derivation.stored is False
    message = stored_message(db_url, photo_message)
    assert message.text == DESCRIPTION
    assert message.media_content_type is None, "nothing was kept, so nothing may claim to be"
