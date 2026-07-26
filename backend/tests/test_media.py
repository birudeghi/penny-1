"""What `app.media` does with a family's photographs, proved without a database.

`tests/test_media_boundary.py` is the file that matters most — it drives the real route through
the real app on real Postgres and asserts that one household can never read another's photo.
This one covers everything that is decidable from a temporary directory and a few dataclasses:
the path layout that MAKES that boundary, the content type we are willing to hand a browser,
and the rules that decide whether a model is ever called.

They are separate files because they fail for different reasons. A boundary test failing means
the tenancy is broken; one of these failing means the mechanism the tenancy is built on stopped
being what the boundary test assumes.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.extraction.chunker import ChunkMessage, build_chunk, render_transcript
from app.media import (
    DERIVED_MARKER,
    INSTRUCTIONS,
    _content_blocks,
    _household_dirs,
    _refuse_reason,
    _skip_reason,
    derive_media_text,
    household_dir,
    media_path,
    purge_household,
    served_as,
    store_media,
)

LONDON = ZoneInfo("Europe/London")

# A JPEG's first bytes, then noise. Never decoded — the reader is stubbed everywhere — but a
# real length and a real magic number, because "the bytes" are what the size cap, the store and
# the byte count all measure.
PHOTO = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8

# What a model gives back for a photo of a leg. The sentence a family would read in their feed.
DESCRIPTION = "A red patch about 4cm across on the outside of the left lower leg."


@pytest.fixture
def volume(tmp_path: Path, settings_override: Callable[..., Settings]) -> Iterator[Path]:
    """A configured MEDIA_ROOT that is not a real Railway volume."""
    root = tmp_path / "media"
    settings_override(media_root=str(root), derive_media_text=True)
    yield root


@pytest.fixture
def no_volume(settings_override: Callable[..., Settings]) -> None:
    """A deployment that keeps no files — the default, and local development."""
    settings_override(media_root=None, derive_media_text=True)


@dataclass
class _Row:
    """The columns `_skip_reason` reads off a message, without a database to read them from."""

    message_type: str = "image"
    text: str | None = None
    provider: str = "gowa"
    provider_message_id: str | None = "3EB0PHOTO001"


# --- the path layout, which IS the tenancy ---------------------------------------------------


def test_two_households_never_share_a_directory_even_for_the_same_message_id(
    volume: Path,
) -> None:
    """The property the whole serving route rests on, stated as paths.

    Household comes FIRST in the layout so that "which tenant's files are these?" is answered
    by the directory rather than by a column somebody has to remember to filter on. Two
    households holding the same message id is not a real case — ids are v4 UUIDs — but it is
    the one that would collapse the layout, so it is the one worth asserting.
    """
    message_id = uuid4()
    ours, theirs = uuid4(), uuid4()

    mine = media_path(ours, message_id)
    yours = media_path(theirs, message_id)

    assert mine != yours
    assert mine.parent != yours.parent
    assert mine.parent.name == str(ours)
    assert yours.parent.name == str(theirs)


def test_a_media_path_can_never_leave_the_root_because_it_is_made_of_uuids(
    volume: Path,
) -> None:
    """There is no attacker-controlled text anywhere in the path, and that is by TYPE.

    `household_dir` and `media_path` take `UUID`, not `str`, so every segment is `[0-9a-f-]`
    and a `..`, a `/` or a NUL is unrepresentable rather than merely unlikely. The alternative
    — a stored path string on the row — is a directory traversal waiting for one bad write.
    """
    for _ in range(50):
        path = media_path(uuid4(), uuid4())
        assert path.resolve().is_relative_to(volume.resolve())
        assert set(path.parent.name) <= set("0123456789abcdef-")
        assert set(path.name) <= set("0123456789abcdef-")


def test_with_no_volume_configured_there_is_no_path_and_nothing_is_written(
    no_volume: None,
) -> None:
    """Unset MEDIA_ROOT means we keep NOTHING, and it must say so rather than improvise.

    A default of "somewhere under the working directory" would put photographs of an
    identifiable person on container disk that the next deploy wipes — all of the exposure,
    none of the durability. So the answer is None, everywhere, and the callers degrade.
    """
    assert media_path(uuid4(), uuid4()) is None
    assert household_dir(uuid4()) is None


# --- writing the bytes ------------------------------------------------------------------------


async def test_a_stored_photo_is_readable_afterwards_and_leaves_no_half_written_file(
    volume: Path,
) -> None:
    """Written to a temporary name and renamed, so a crash cannot leave a truncated photo.

    The failure being prevented is not a lost file — it is a file that is HALF there under the
    name the serving route reads, which would be handed to a family as if it were the whole
    photograph.
    """
    household_id, message_id = uuid4(), uuid4()

    assert await store_media(household_id, message_id, PHOTO, "image/jpeg") is True

    path = media_path(household_id, message_id)
    assert path.read_bytes() == PHOTO
    assert [p.name for p in path.parent.iterdir()] == [str(message_id)]


async def test_storing_the_same_message_twice_replaces_it_rather_than_accumulating(
    volume: Path,
) -> None:
    """A retried task and a re-delivered webhook must not cost two files."""
    household_id, message_id = uuid4(), uuid4()

    await store_media(household_id, message_id, PHOTO, "image/jpeg")
    await store_media(household_id, message_id, PHOTO + b"tail", "image/jpeg")

    assert len(list(household_dir(household_id).iterdir())) == 1
    assert media_path(household_id, message_id).read_bytes() == PHOTO + b"tail"


async def test_with_no_volume_configured_storing_returns_false_instead_of_raising(
    no_volume: None,
) -> None:
    """Local development, and any deploy without a volume. Not an error — a mode."""
    assert await store_media(uuid4(), uuid4(), PHOTO, "image/jpeg") is False


async def test_an_unwritable_volume_costs_the_file_and_nothing_else(
    tmp_path: Path, settings_override: Callable[..., Settings]
) -> None:
    """The webhook is behind this. A full disk, a read-only mount or a root that is not a
    directory must degrade to "no file kept", never to an exception — an exception here is a
    non-2xx, and a non-2xx is five GOWA retries and duplicate messages in a family's chat.

    A plain file standing where the root should be is the version of this that fails the same
    way for root as for anybody else, which a chmod would not.
    """
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("this is a file")
    settings_override(media_root=str(blocked))

    assert await store_media(uuid4(), uuid4(), PHOTO, "image/jpeg") is False


# --- what a browser is allowed to be told -----------------------------------------------------


def test_the_bytes_never_decide_what_the_content_type_is() -> None:
    """The recorded type maps through a fixed table; anything unknown becomes a download.

    `image/svg+xml` is the case worth naming: it is an image by every intuition and a script
    document by specification, so serving it inline would be running attacker-supplied script
    on our own origin with the session cookie beside it. It is not in the table, so it is an
    `application/octet-stream` attachment like every other unknown.
    """
    message_id = uuid4()

    for hostile in ("text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", None, ""):
        served = served_as(hostile, message_id)
        assert served.content_type == "application/octet-stream"
        assert served.disposition == "attachment"


def test_the_four_image_types_we_render_are_served_as_themselves() -> None:
    """The feature has to work: a photo the family sent must display as a photo."""
    message_id = uuid4()

    for good, extension in (
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
        ("image/gif", "gif"),
    ):
        served = served_as(good, message_id)
        assert served.content_type == good
        assert served.disposition == "inline"
        assert served.filename == f"{message_id}.{extension}"


def test_a_content_type_with_parameters_still_matches_its_bare_type() -> None:
    """GOWA passes WhatsApp's own header through, and `image/jpeg; charset=binary` is a real
    thing to receive. Failing to match it would turn every such photo into a download."""
    served = served_as("IMAGE/JPEG; charset=binary", uuid4())

    assert served.content_type == "image/jpeg"
    assert served.disposition == "inline"


def test_a_pdf_is_named_correctly_and_still_never_rendered_in_the_page() -> None:
    """Correct type — it really is a PDF and calling it otherwise would be a lie the family
    trips over — but an attachment. A same-origin PDF viewer is a large attack surface for a
    file that arrived from a stranger's phone, and the family's own reader opens it either way.
    """
    served = served_as("application/pdf", uuid4())

    assert served.content_type == "application/pdf"
    assert served.disposition == "attachment"


def test_the_filename_we_send_is_ours_and_contains_nothing_the_sender_chose() -> None:
    """`Content-Disposition` is a header, and a WhatsApp filename is attacker-chosen text.
    A quote or a newline in it is a header injection; the sender's name for the file is worth
    nothing to the family here, so it is never echoed at all."""
    message_id = uuid4()

    served = served_as("image/png", message_id)

    assert served.filename == f"{message_id}.png"
    assert set(served.filename) <= set("0123456789abcdef-.png")


# --- deletion ----------------------------------------------------------------------------------


async def test_purging_one_household_leaves_every_other_household_untouched(
    volume: Path,
) -> None:
    """ "Delete my account" is one `rmtree` of one directory named by one tenant id.

    No list of files to enumerate and get wrong, and no reachable spelling of the argument that
    lands in somebody else's directory — which is the second reason the layout is keyed on the
    household rather than on the message.
    """
    leaving, staying = uuid4(), uuid4()
    for household_id in (leaving, staying):
        for _ in range(3):
            await store_media(household_id, uuid4(), PHOTO, "image/jpeg")

    assert await purge_household(leaving) is True

    assert not household_dir(leaving).exists()
    assert len(list(household_dir(staying).iterdir())) == 3


async def test_purging_a_household_that_stored_nothing_is_a_quiet_no_op(volume: Path) -> None:
    """Most households never send a photo, and every deletion calls this. It must not raise,
    and it must not report having deleted something it did not."""
    assert await purge_household(uuid4()) is False


async def test_purging_is_idempotent_so_a_retried_deletion_is_free(volume: Path) -> None:
    household_id = uuid4()
    await store_media(household_id, uuid4(), PHOTO, "image/jpeg")

    assert await purge_household(household_id) is True
    assert await purge_household(household_id) is False


def test_the_orphan_sweep_only_ever_considers_directories_that_are_household_ids(
    volume: Path,
) -> None:
    """The sweep deletes directories whose household is gone, so what it is willing to LOOK at
    is the whole safety argument. A name that is not a UUID was not written by us; something
    else is using this volume, and deleting a stranger's data because it was in our way is not
    a repair. A file is not a household either."""
    volume.mkdir(parents=True)
    mine = uuid4()
    (volume / str(mine)).mkdir()
    (volume / "lost+found").mkdir()
    (volume / "backups").mkdir()
    (volume / "notes.txt").write_text("x")

    assert _household_dirs(volume) == [mine]


# --- when a model is called, and when it is not -------------------------------------------------


def test_a_photo_with_no_words_yet_is_the_one_case_worth_downloading(volume: Path) -> None:
    assert _skip_reason(_Row(), {}) is None
    assert _skip_reason(_Row(message_type="document"), {}) is None


def test_a_message_that_already_carries_words_is_never_described(volume: Path) -> None:
    """A captioned photo already holds the sender's OWN words, and `messages.text` holds one
    thing. Overwriting a person's sentence with a machine's reading of their picture is the
    single most misleading thing this feature could do."""
    assert _skip_reason(_Row(text="does this look infected?"), {}) == "already_has_text"


def test_a_second_attempt_at_an_already_described_photo_costs_nothing(volume: Path) -> None:
    """GOWA retries five times, tasks get retried and backfills get run twice. The marker is
    what makes every one of those free, BEFORE a byte is downloaded or a token is spent."""
    assert _skip_reason(_Row(), {DERIVED_MARKER: True}) == "already_derived"


def test_a_video_or_a_sticker_is_not_something_we_read(volume: Path) -> None:
    """Deliberately unhandled: a video is minutes of frames nobody has costed."""
    for kind in ("video", "sticker", "audio", "text", "location"):
        assert _skip_reason(_Row(message_type=kind), {}) == "not_derivable"


def test_an_imported_media_line_is_never_downloaded_because_the_file_never_existed(
    volume: Path,
) -> None:
    """A `.txt` export's "<Media omitted>" line refers to a file that was never uploaded
    anywhere we can reach — there is no provider message id to fetch it by, and GOWA holds the
    only key to WhatsApp's encrypted blob."""
    export_line = _Row(provider="whatsapp_export", provider_message_id=None)

    assert _skip_reason(export_line, {}) == "no_downloadable_media"


def test_with_both_switches_off_the_attachment_is_not_even_fetched(
    settings_override: Callable[..., Settings],
) -> None:
    """Nowhere to keep it AND nothing to say about it: the download would be pure cost."""
    settings_override(media_root=None, derive_media_text=False)

    assert _skip_reason(_Row(), {}) == "nothing_to_do"


def test_the_kill_switch_stops_the_model_and_not_the_filing(
    settings_override: Callable[..., Settings], tmp_path: Path
) -> None:
    """`DERIVE_MEDIA_TEXT=false` is the thing to reach for when the vision bill misbehaves, and
    it must not quietly also stop keeping the family's photos. The two halves fail separately
    on purpose: the file is still worth having when the model is switched off."""
    settings = settings_override(media_root=str(tmp_path), derive_media_text=False)

    assert _skip_reason(_Row(), {}) is None  # still downloaded, still stored
    assert _refuse_reason("image", "image/jpeg", 1000, settings) == "disabled"


def test_a_file_too_large_to_send_is_kept_and_left_undescribed(
    settings_override: Callable[..., Settings], tmp_path: Path
) -> None:
    """A 12 MB scan is a real document and the family can open it. Base64ing it into a model
    request is a cost nobody agreed to, and the refusal happens AFTER the file is on the
    volume, which is why these are two functions and not one."""
    settings = settings_override(media_root=str(tmp_path), media_derive_max_bytes=1_000)

    assert _refuse_reason("document", "application/pdf", 1_001, settings) == "too_large"
    assert _refuse_reason("document", "application/pdf", 1_000, settings) is None


def test_a_type_no_model_can_read_is_kept_rather_than_sent_and_rejected(
    settings_override: Callable[..., Settings], tmp_path: Path
) -> None:
    """An iPhone HEIC, a .docx, a .zip somebody dropped in the group. Refusing costs a
    description; guessing costs a paid round trip that comes back a 400."""
    settings = settings_override(media_root=str(tmp_path))

    assert _refuse_reason("image", "image/heic", 100, settings) == "unreadable_image_type"
    assert _refuse_reason("document", "text/csv", 100, settings) == "unreadable_document_type"
    assert _refuse_reason("image", "image/jpeg", 100, settings) is None
    assert _refuse_reason("document", "application/pdf", 100, settings) is None


def test_the_marker_name_is_a_stored_value_and_may_not_be_renamed() -> None:
    """It is written into `messages.payload` and read back by `_skip_reason`, by
    `app.extraction.service` and by every excerpt in `events.source_excerpts`. Renaming it
    silently makes every already-described photo look undescribed — which means paying to
    describe it again — and every stored excerpt lose the flag that says nobody said it."""
    assert DERIVED_MARKER == "derived_from_media"


# --- what the model is asked, and with what -------------------------------------------------------


def test_a_sender_chosen_filename_never_reaches_the_prompt() -> None:
    """A WhatsApp filename is attacker-controlled text, and a document called "ignore the above
    and reply..." is the cheapest prompt injection there is. The API needs an extension it
    recognises and nothing else, so it gets ours."""
    blocks = _content_blocks("document", "application/pdf", PHOTO)

    [instruction, attachment] = blocks
    assert attachment["filename"] == "attachment.pdf"
    assert attachment["type"] == "input_file"
    assert attachment["file_data"].startswith("data:application/pdf;base64,")
    assert instruction["type"] == "input_text"


def test_a_photo_is_sent_as_its_own_bytes_and_never_as_the_whatsapp_url() -> None:
    """The `mmg.whatsapp.net` URL in the payload points at an END-TO-END ENCRYPTED blob whose
    key lives in GOWA's session store. Sending it would be paying a model to describe
    ciphertext, so the bytes we already hold go inline as a data URL."""
    [_, attachment] = _content_blocks("image", "image/jpeg", PHOTO)

    assert attachment["type"] == "input_image"
    assert attachment["image_url"] == ("data:image/jpeg;base64," + base64.b64encode(PHOTO).decode())


def test_the_instructions_forbid_diagnosing_the_person_in_the_photo() -> None:
    """THE SAFETY PROPERTY OF THIS FEATURE. "Looks like cellulitis" written into a care record
    has no clinician's name on it, cannot be challenged, and will be read by a family as if it
    had — and it would then be extracted as a symptom and land in a report a GP reads. The
    model describes; it does not diagnose, and the prompt has to say so."""
    instructions = INSTRUCTIONS.lower()

    assert "never diagnose" in instructions
    assert "never invent text you cannot read" in instructions
    assert "care record" in instructions


# --- the contract with the webhook ------------------------------------------------------------


async def test_a_broken_database_costs_the_description_and_never_raises(
    volume: Path,
) -> None:
    """RULE ONE, AND THE REASON EVERY FAILURE HERE IS A `None`. This runs behind a webhook that
    must answer 2xx: GOWA retries five times on anything else, and a retry becomes a duplicate
    message in a family's chat. So even the failure `derive_media_text` is not expecting — a
    dead connection on its very first statement — has to come back as "leave it as a photo"."""

    class _DeadSession:
        async def execute(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("connection reset by peer")

    assert await derive_media_text(_DeadSession(), uuid4()) is None


async def test_a_message_that_is_not_there_is_not_an_error(volume: Path) -> None:
    """A row deleted between scheduling and running — a household removed while a photo was in
    flight. Nothing to describe, nothing to complain about."""

    class _EmptySession:
        async def execute(self, *_: Any, **__: Any) -> Any:
            class _Result:
                @staticmethod
                def one_or_none() -> None:
                    return None

            return _Result()

    assert await derive_media_text(_EmptySession(), uuid4()) is None


# --- how a description reaches the model that extracts it --------------------------------------


def _photo(**overrides: Any) -> ChunkMessage:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "sent_at": datetime(2026, 7, 14, 20, 4, tzinfo=UTC),
        "sender_display_name": "Sarah",
        "text": None,
        "message_type": "image",
    }
    return ChunkMessage(**(fields | overrides))


def _rendered(message: ChunkMessage) -> str:
    return render_transcript(build_chunk([message], ()), LONDON)


def test_a_described_photo_reaches_the_prompt_as_words_that_nobody_said() -> None:
    """The whole point of deriving text, and the whole point of not prefixing it.

    The description prints as ordinary content — hiding it behind "[photo]" would throw the
    message away exactly as hiding a transcript would — and the ATTRIBUTION carries the
    warning. "Sarah (photo, described by Penny)" is the model's only cue that it is reading a
    machine's reading of a picture rather than Sarah's own report of what she saw.
    """
    line = _rendered(_photo(text=DESCRIPTION, derived_from_media=True))

    assert line == f"[m1] Tue 2026-07-14 21:04 — Sarah (photo, described by Penny): {DESCRIPTION}"
    assert "[photo]:" not in line


def test_a_document_says_which_it_is_when_it_is_read_out() -> None:
    """A discharge letter and a photo of a leg are different kinds of evidence, and the label
    is where that difference survives into the prompt."""
    line = _rendered(
        _photo(message_type="document", text="Discharged 14 July.", derived_from_media=True)
    )

    assert "Sarah (document, described by Penny): Discharged 14 July." in line


def test_a_photo_nobody_could_read_is_still_the_placeholder_it_always_was() -> None:
    """The degrade path, and it must be EXACTLY the pre-feature behaviour: GOWA down, the media
    expired, an HEIC, an over-budget household. No attribution either — nothing was read, so
    nothing may be attributed."""
    line = _rendered(_photo())

    assert line.endswith("— Sarah: [photo]")
    assert "described by Penny" not in line


def test_an_empty_description_is_not_treated_as_words() -> None:
    """A meme or a screenshot comes back empty. The marker means "carries a reading of an
    attachment", so a blank one must not turn the placeholder into nothing at all."""
    line = _rendered(_photo(text="   ", derived_from_media=True))

    assert line.endswith("— Sarah: [photo]")


def test_a_captioned_photo_is_never_attributed_to_the_machine() -> None:
    """The reason the flag is carried explicitly instead of inferred from "it is an image and
    it has text": a caption really is the sender's own words, and labelling them as Penny's
    reading would be the same lie in the other direction."""
    line = _rendered(_photo(text="does this look infected?"))

    assert "described by Penny" not in line
    assert line.endswith("— Sarah: [photo] does this look infected?")
