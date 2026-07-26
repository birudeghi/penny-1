"""The photographs and documents a family sends: the bytes we keep, and the words we derive.

A care conversation is not only typed. "Does this look infected to you?" arrives as a photo of
a shin; "here's what the hospital gave us" arrives as a PDF. Before this module both reached
the record as an EMPTY ROW — `webhooks._classify` returns `(kind, None)` for every media type,
so the feed showed "photo (not stored)", the extraction prompt was told a photo existed and
deliberately told nothing about it, and the one message in the thread that actually carried the
news was the one the product could not read. `app.transcription` fixed exactly this shape for
voice notes; this is the same fix for the other two attachments that carry care signal.

Two jobs, deliberately independent of each other:

  **The bytes** (`store_media`) are written to a volume so the family can look again — and so a
  clinician can be shown the photo from three weeks ago rather than a family's memory of it.
  Governed by `MEDIA_ROOT` alone. Unset means we keep nothing, which is the right default for
  a container with no volume: photographs of an identifiable person must not be scattered
  across ephemeral disks that a deploy wipes.

  **The words** (`derive_media_text`) are a model's description of the attachment, written into
  `messages.text` so the feed, the prompt and the report can all read it. Governed by
  `DERIVE_MEDIA_TEXT` and by having a priced model. One of these failing must never take the
  other with it: a photo we stored but could not describe is still a photo the family can open,
  and a description we derived without keeping the file is still the sentence that gets the
  event into the record.

THE RULES THIS MODULE IS BUILT AROUND — the same four `app.transcription` is built around,
because it hangs off the same webhook and inherits the same contract.

1. **It never raises.** `derive_media_text` returns `None` for every failure, and `None` means
   "leave it as a photo". Its caller is a background task behind the GOWA webhook, which has to
   answer 2xx: a non-2xx makes GOWA retry the same delivery five times and the family gets
   duplicate messages in their chat. A dead sidecar, an expired media file, a model error, an
   unwritable volume and a disabled setting are therefore all indistinguishable from outside.

2. **Every paid call is audited with a real cost, against the same budget.** The description is
   an ordinary Responses API call billed in tokens, so it writes its own `llm_runs` row with
   `purpose="derive_media"` and it CHECKS `_HouseholdBudget` before spending. Transcription only
   records; this also checks, because the shape of the risk is different — a family sharing
   forty holiday photos into the care group is a plausible afternoon, and unlike extraction
   there is no cadence gate batching these into one call.

3. **Nothing here logs text.** Not the description, not the prompt, not a filename the sender
   chose, not a byte of the image. Ids, byte counts, token counts and costs only.

4. **A replay is free.** GOWA re-delivers, tasks get retried, backfills get run twice. The
   payload marker (`DERIVED_MARKER`) plus `messages.text` already being set are what make the
   second attempt return `None` before it downloads or pays for anything.

WHY THE MARKER, AND NOT A PREFIX ON THE TEXT. This is `app.transcription`'s rule and the reason
is stronger here, not weaker. A description is not what anybody SAID: nobody typed those words
and nobody spoke them, a machine looked at a picture and wrote a sentence. That has to be
visible to a family reading their own record, and it has to be visible to the extraction prompt,
which should weigh "a red patch about the size of a 50p on the left shin" differently from a
person's own report. Both of those need a FLAG, not a decoration: writing "[photo] a red patch"
into `messages.text` would put our editorial into the content hash, the prompt, the search index
and the report, where a writer can trip over it and a reader cannot act on it.
`payload["derived_from_media"] is True` says the same thing in the one place that is safe.

WHAT THE MODEL IS ALLOWED TO SAY. It describes; it does not diagnose. A sentence like "this
looks like cellulitis" in a care record has no clinician's name on it, cannot be challenged, and
will be read by a family as if it did — and it would then be extracted as a symptom event and
end up in a report a GP reads. The instructions below forbid diagnosis, cause and severity
judgements, and forbid inventing text that cannot be read. Describing is the whole job.

THE PATH LAYOUT IS A TENANCY DECISION, NOT A FILENAME CONVENTION.

    <MEDIA_ROOT>/<household_id>/<message_id>

Household FIRST, and never a stored path string. Three properties follow, and each one is a
failure that cannot happen:

  * **Deleting a tenant is one call.** `shutil.rmtree(<root>/<household_id>)` removes every
    photograph a household ever sent, with no query, no list of ids to get wrong, and no way to
    touch another household's directory. That is what makes "delete my account" actually delete
    (see `purge_after_delete`).
  * **A path can never cross a tenant.** Every segment is `str(UUID)`, which is `[0-9a-f-]` by
    construction — there is no `..`, no separator and no attacker-controlled text anywhere in
    it. The serving route builds the path from the SESSION's household id, so even a lookup
    that forgot its `WHERE household_id = ...` could only ever resolve to the caller's own
    directory.
  * **A stored path column would undo both.** It would be a string on a row, and a bug that
    wrote the wrong one would hand out somebody else's photograph. So the location is derived
    every time and stored nowhere.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import sqlalchemy as sa

from app import gowa
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.errors import BudgetExceededError
from app.llm.pricing import PRICES, UnpricedModelError, cost_usd
from app.models import Household, LlmRun, Message
from app.openai_client import get_openai_client
from app.transcription import DOWNLOADABLE_PROVIDER

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# The payload key. True once the message carries a model's reading of an attachment instead of
# a placeholder — read it to know that NOBODY said these words, and to know not to pay twice.
# Sits beside `app.transcription.TRANSCRIBED_MARKER`; the two are mutually exclusive in practice
# (audio is transcribed, images and documents are described) but nothing depends on that.
DERIVED_MARKER = "derived_from_media"
# Sibling block holding how the text was produced. Underscore-prefixed like `_device_id` and
# `_transcription`, so it is visibly ours rather than something GOWA sent. NEVER contains the
# description, and never the sender's filename.
DERIVATION_META_KEY = "_derivation"

# `llm_runs.purpose` is free text on purpose ("the gateway owns this vocabulary and adding a
# value must not need a migration"), and this is not a gateway call — the schema-less shape of
# a description does not fit `CallSpec` — so the row is written here under its own name.
DERIVE_PURPOSE = "derive_media"

# What `webhooks.MEDIA_FIELDS` normalises the two attachment kinds to. Videos and stickers are
# deliberately absent: a video is minutes of frames nobody has costed, and a sticker is a joke.
IMAGE_MESSAGE_TYPES = frozenset({"image"})
DOCUMENT_MESSAGE_TYPES = frozenset({"document"})
DERIVABLE_MESSAGE_TYPES = IMAGE_MESSAGE_TYPES | DOCUMENT_MESSAGE_TYPES

# What we are willing to send a model, keyed on the content type GOWA reported. Anything else
# is stored and left undescribed rather than base64'd into a request that would 400: an HEIC
# from an iPhone, a .docx, a .zip somebody dropped in the group. Refusing costs a description;
# guessing costs a paid round trip that fails.
READABLE_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
READABLE_DOCUMENT_TYPES = frozenset({"application/pdf"})

# What a BROWSER may be told a file is. Deliberately much narrower than what we store, and
# never derived from the bytes: `served_as` maps a recorded content type through this table and
# falls back to `application/octet-stream` + `attachment` for everything else. Sniffing
# attacker-controlled bytes into a content type is how a "photo" becomes an HTML document
# executing script on our own origin, with the session cookie right there.
_INLINE_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
# Correct type, but never rendered in the page. A PDF viewer is a large attack surface and this
# one would be running same-origin; the family gets the file, their own reader opens it.
_ATTACHMENT_TYPES: dict[str, str] = {"application/pdf": "pdf"}
FALLBACK_CONTENT_TYPE = "application/octet-stream"
FALLBACK_EXTENSION = "bin"

# The description's ceiling. A four-page discharge letter is worth reading out in full — that is
# the case this feature exists for — so this is generous, while still bounding what one photo
# can cost (600 output tokens at $30/M is $0.018).
MAX_OUTPUT_TOKENS = 600
# Matches the gateway's default. A description is a reading task, not a reasoning task.
REASONING_EFFORT = "low"

# Permissions on what we write. The volume is ours alone, but a health photograph is the last
# file in this deployment that should be world-readable if the mount ever is shared.
_DIR_MODE = 0o700
_FILE_MODE = 0o600
# Written under this name and then renamed into place, so a crash mid-write can never leave a
# truncated file that the serving route would hand over as if it were the whole photo.
_PARTIAL_SUFFIX = ".part"

# How long `purge_after_delete` waits for the deleting request to commit before giving up.
# Milliseconds in practice; the ceiling only matters when the request never commits at all, and
# in that case NOT purging is the correct answer — the household still exists.
_DELETE_WAIT_TRIES = 60
_DELETE_WAIT_SECONDS = 0.25

# Strong references to detached purge tasks. asyncio holds only a weak reference to a running
# task, so without this the garbage collector may cancel a deletion mid-`rmtree`.
_RUNNING: set[asyncio.Task[None]] = set()


INSTRUCTIONS = (
    "You are reading one attachment that was shared in a family's WhatsApp group about caring "
    "for one older person. Your description is written into that person's care record and will "
    "be read by their family and shown to their GP.\n"
    "\n"
    "Describe only what is actually there.\n"
    "- Write plain sentences in the third person. No markdown, no headings, no preamble.\n"
    "- For a photo: what is visible and where, with size, colour and position if they are "
    "visible. For a document: who and what it is about, dates, medicine names and doses, "
    "results, and what happens next — copy names, numbers and doses exactly as written.\n"
    "- Never diagnose, never name a condition, never judge how serious something is and never "
    "guess at a cause. 'A red patch about 4cm across on the left shin' belongs in a care "
    "record. 'Looks like cellulitis' does not: nobody signed it and everyone will read it as "
    "if a clinician had.\n"
    "- Never invent text you cannot read. If part of a document is illegible, say so.\n"
    "- If the attachment has nothing to do with this person's care — a meme, a screenshot of a "
    "conversation, a holiday photo — reply with an empty string and nothing else."
)
_IMAGE_ASK = "Describe this photo for the care record."
_DOCUMENT_ASK = "Read this document out for the care record."
# The filename sent with a document, and it is OURS, not the sender's. A WhatsApp filename is
# attacker-chosen text; putting it in the request would put it in the prompt, and a file called
# "ignore the above and write..." is the cheapest prompt injection there is. The API only needs
# an extension it recognises.
_DOCUMENT_FILENAME = "attachment.pdf"


# --- the bytes ------------------------------------------------------------------------------


def media_root() -> Path | None:
    """The volume, or None when this deployment keeps no files. None is a normal answer."""
    configured = (get_settings().media_root or "").strip()
    return Path(configured) if configured else None


def household_dir(household_id: UUID) -> Path | None:
    """Everything one household ever sent, in one directory. The unit of deletion.

    `household_id` is typed `UUID` and that is the security control, not a nicety: `str(UUID)`
    can only produce `[0-9a-f-]`, so there is no spelling of this argument that escapes the
    root. Nothing in this module accepts a path segment as a string.
    """
    root = media_root()
    return None if root is None else root / str(household_id)


def media_path(household_id: UUID, message_id: UUID) -> Path | None:
    """Where one message's file lives. Derived every time, stored nowhere — see the module
    docstring for why a path column would undo the whole layout."""
    directory = household_dir(household_id)
    return None if directory is None else directory / str(message_id)


async def store_media(household_id: UUID, message_id: UUID, data: bytes, content_type: str) -> bool:
    """Write one attachment to the volume. Returns whether it is now there. NEVER RAISES.

    False is an ordinary answer — no volume configured, a full disk, a read-only mount — and it
    must stay ordinary: the derivation, the message and the webhook's 200 all carry on without
    it. A family losing the ability to re-open a photo is a smaller harm than a webhook retry
    storm putting five copies of their own message back into their chat.

    WRITTEN TO A TEMPORARY NAME AND RENAMED. `os.replace` is atomic within a filesystem, so a
    crash or a full disk mid-write leaves a `.part` file that nothing serves, rather than a
    truncated one that the route would hand over as if it were the whole photograph. The rename
    also makes a re-store of the same message idempotent instead of a window where the file is
    half-there.

    The file write is real blocking IO on a network volume, so it goes to a thread: this runs
    on the event loop that is also answering webhooks.
    """
    path = media_path(household_id, message_id)
    if path is None:
        log.info("media.store_skipped", extra={"message_id": str(message_id), "why": "no_root"})
        return False
    try:
        await asyncio.to_thread(_write_file, path, data)
    except Exception as exc:
        # Bare, and deliberately: an unwritable volume is an operations problem, not a reason
        # for a family's message to fail to be ingested.
        log.warning(
            "media.store_failed",
            extra={
                "message_id": str(message_id),
                "byte_count": len(data),
                "exc_type": type(exc).__name__,
            },
        )
        return False
    log.info(
        "media.stored",
        extra={
            "household_id": str(household_id),
            "message_id": str(message_id),
            "byte_count": len(data),
            "content_type": content_type,
        },
    )
    return True


def _write_file(path: Path, data: bytes) -> None:
    """The blocking half of `store_media`. Runs in a worker thread."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
    partial = path.with_name(path.name + _PARTIAL_SUFFIX)
    partial.write_bytes(data)
    os.chmod(partial, _FILE_MODE)
    os.replace(partial, path)


async def stored_file(household_id: UUID, message_id: UUID) -> Path | None:
    """The file, if it is really on disk. None when it is not — never raises.

    Checked rather than assumed, because the database and the volume can disagree: a restored
    database snapshot, a volume that was reattached empty, a downgrade. The route turns None
    into the same 404 as a message that never existed.
    """
    path = media_path(household_id, message_id)
    if path is None:
        return None
    try:
        return path if await asyncio.to_thread(path.is_file) else None
    except Exception as exc:
        log.warning("media.stat_failed", extra={"exc_type": type(exc).__name__})
        return None


@dataclass(frozen=True, slots=True)
class ServedMedia:
    """Exactly what the HTTP response is allowed to say this file is."""

    content_type: str
    # "inline" for the image types a browser renders safely, "attachment" for everything else.
    disposition: str
    # OURS, built from the message id. Never the sender's filename: that is attacker-chosen
    # text, and a quote or a newline in it is a header injection in `Content-Disposition`.
    filename: str


def served_as(recorded: str | None, message_id: UUID) -> ServedMedia:
    """Map the PROVIDER's content type to one we are willing to hand a browser.

    THE BYTES ARE NEVER CONSULTED. Sniffing is how a file uploaded as a photo gets served as
    `text/html` and executes script on our origin, next to the session cookie — and the bytes
    here came from a stranger's phone by way of a group chat. So the decision is made from a
    fixed table keyed on what the sidecar declared, and anything not in it becomes
    `application/octet-stream` as an attachment: still downloadable, never rendered.

    `nosniff` from `app.headers` is what makes this stick — without it a browser is entitled to
    render `octet-stream` as whatever it thinks the bytes look like.
    """
    declared = (recorded or "").split(";")[0].strip().lower()
    extension = _INLINE_TYPES.get(declared)
    if extension is not None:
        return ServedMedia(declared, "inline", f"{message_id}.{extension}")
    extension = _ATTACHMENT_TYPES.get(declared)
    if extension is not None:
        return ServedMedia(declared, "attachment", f"{message_id}.{extension}")
    return ServedMedia(FALLBACK_CONTENT_TYPE, "attachment", f"{message_id}.{FALLBACK_EXTENSION}")


# --- retention ------------------------------------------------------------------------------


def schedule_purge_after_delete(household_id: UUID) -> None:
    """Detach the deletion of one household's files from the request that asked for it.

    Called by `DELETE /api/household`. It cannot be done inline, and the ordering is the whole
    point: `get_session` owns the transaction and commits AFTER the handler returns (and, on
    this Starlette, after its background tasks run). Deleting the files first would mean a
    request that then rolled back left a household whose photographs are gone — silent,
    irreversible data loss on a failed request. So the task below waits until the household row
    is really gone from a fresh connection, and only then removes anything.
    """
    task = asyncio.create_task(purge_after_delete(household_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def purge_after_delete(household_id: UUID) -> None:
    """Wait for the delete to commit, then remove the files. NOTHING ESCAPES.

    A cascade in Postgres takes the rows and cannot take the volume, so without this a deleted
    household's photographs sit on disk indefinitely — the one part of "delete everything" that
    a foreign key cannot keep. Unreachable, since every read path needs a session for a
    household that no longer exists, but present, which is not what a family was promised.

    THE SWEEP AT THE END IS THE CRASH PATH. This task can die with the process — a deploy, an
    OOM — between the commit and the `rmtree`, and nothing would ever come back for those bytes.
    So every deletion also looks for directories belonging to households that no longer exist
    and removes those too; whatever an earlier crash stranded is collected by the next family
    that leaves.
    """
    try:
        if not await _await_household_gone(household_id):
            # The request rolled back, or is still running long after we gave up. Either way
            # the household may still exist, and deleting a living family's photographs
            # because we were impatient is the one outcome worse than leaving files behind.
            log.error(
                "media.purge_abandoned",
                extra={"household_id": str(household_id), "why": "household_still_present"},
            )
            return
        await purge_household(household_id)
        await sweep_orphans()
    except Exception:
        log.exception("media.purge_failed", extra={"household_id": str(household_id)})


async def purge_household(household_id: UUID) -> bool:
    """Remove one household's entire media directory. Idempotent, and never raises.

    One `rmtree` of one directory named by one tenant id: there is no list of files to
    enumerate and get wrong, and no way for this to reach another household's bytes.
    """
    directory = household_dir(household_id)
    if directory is None:
        return False
    try:
        removed = await asyncio.to_thread(_rmtree_if_present, directory)
    except Exception as exc:
        log.warning(
            "media.purge_error",
            extra={"household_id": str(household_id), "exc_type": type(exc).__name__},
        )
        return False
    log.info(
        "media.purged" if removed else "media.purge_nothing_to_do",
        extra={"household_id": str(household_id)},
    )
    return removed


def _rmtree_if_present(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    shutil.rmtree(directory)
    return True


async def sweep_orphans() -> int:
    """Delete media directories whose household no longer exists. Returns how many.

    The backstop for a purge that never finished: a process killed between the commit and the
    `rmtree` leaves a directory nothing will otherwise revisit. Cheap enough to run on every
    deletion — one `listdir` and one `SELECT id ... WHERE id IN (...)`.

    THE ORDER OF THE TWO READS IS LOAD-BEARING. The directories are listed FIRST and the
    households are looked up SECOND, so a household created in between cannot be in the list
    and cannot be deleted; a household deleted in between is correctly swept. The other order
    would race the wrong way.

    A directory whose name is not a UUID is left strictly alone. Nothing here writes such a
    name, so its presence means something else is using this volume, and deleting a stranger's
    data because it was in our way is not a repair.
    """
    root = media_root()
    if root is None:
        return 0
    try:
        candidates = await asyncio.to_thread(_household_dirs, root)
    except Exception as exc:
        log.warning("media.sweep_list_failed", extra={"exc_type": type(exc).__name__})
        return 0
    if not candidates:
        return 0

    # Its OWN session: this runs detached from any request, so there is no caller's
    # transaction to borrow, and the deletion's transaction is exactly the one whose outcome
    # is being read.
    try:
        async with get_sessionmaker()() as session:
            alive = set(
                (
                    await session.execute(
                        sa.select(Household.id).where(Household.id.in_(list(candidates)))
                    )
                )
                .scalars()
                .all()
            )
    except Exception as exc:
        # A failed lookup means we do not know which of these are orphans, and "do not know"
        # must mean "delete nothing".
        log.warning("media.sweep_lookup_failed", extra={"exc_type": type(exc).__name__})
        return 0

    swept = 0
    for household_id in candidates:
        if household_id in alive:
            continue
        if await purge_household(household_id):
            swept += 1
    if swept:
        log.info("media.swept_orphans", extra={"count": swept})
    return swept


def _household_dirs(root: Path) -> list[UUID]:
    """Every directory under the root whose name is a household id."""
    if not root.is_dir():
        return []
    found: list[UUID] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            found.append(UUID(entry.name))
        except ValueError:
            continue  # not ours; see `sweep_orphans`
    return found


async def _await_household_gone(household_id: UUID) -> bool:
    """Block until the household is unreadable from a fresh connection. False if it never is."""
    maker = get_sessionmaker()
    for _ in range(_DELETE_WAIT_TRIES):
        async with maker() as session:
            found = (
                await session.execute(sa.select(Household.id).where(Household.id == household_id))
            ).first()
        if found is None:
            return True
        await asyncio.sleep(_DELETE_WAIT_SECONDS)
    return False


# --- the words ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawDerivation:
    """What the model said, and what it counted. Before we decide what it costs."""

    text: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Derivation:
    """One attachment, read. `stored` says whether the bytes are also on the volume — the two
    halves succeed and fail independently, and a caller logging this should not conflate them."""

    text: str
    model: str
    kind: str
    cost_usd: Decimal
    stored: bool


class MediaReader(Protocol):
    """The no-network seam, the same shape as `app.transcription.AudioTranscriber`: one method,
    and everything above it is ours and is tested offline."""

    async def read(
        self, *, model: str, kind: str, content_type: str, data: bytes
    ) -> RawDerivation: ...


class OpenAIMediaReader:
    """The Responses API, with the file inline. NOT `app.llm.gateway`.

    The gateway is the right door for every call that has a JSON schema and a text prompt, and
    this call has neither: `CallSpec.input` is a `str`, and vision and document input are
    content BLOCKS. Forcing them through it would mean widening the one type in this codebase
    whose narrowness is a safety property. So this is a direct call, with the same guarantees
    rebuilt around it where they matter — `store=False`, a budget check, an `llm_runs` row.

    `store=False` matters more here than anywhere else in the app: the payload is a photograph
    of an identifiable person's body, and it is not ours to leave on someone else's server.

    NO RETRIES, on purpose. A failed description leaves the message exactly as it was and the
    family's photo is still in the record as a photo. Retrying inside a background task that
    the webhook has already answered buys a second chance at a description and risks a second
    charge; the gateway's retry ladder exists for extraction, where the work cannot be skipped.
    """

    async def read(self, *, model: str, kind: str, content_type: str, data: bytes) -> RawDerivation:
        client = get_openai_client()
        response = await client.responses.create(
            model=model,
            instructions=INSTRUCTIONS,
            input=[{"role": "user", "content": _content_blocks(kind, content_type, data)}],
            max_output_tokens=MAX_OUTPUT_TOKENS,
            reasoning={"effort": REASONING_EFFORT},
            # Family health media: we do not retain it server-side.
            store=False,
        )
        usage = getattr(response, "usage", None)
        return RawDerivation(
            text=getattr(response, "output_text", "") or "",
            input_tokens=_count(usage, "input_tokens"),
            cached_input_tokens=_count(
                getattr(usage, "input_tokens_details", None), "cached_tokens"
            ),
            output_tokens=_count(usage, "output_tokens"),
            reasoning_tokens=_count(
                getattr(usage, "output_tokens_details", None), "reasoning_tokens"
            ),
        )


def _content_blocks(kind: str, content_type: str, data: bytes) -> list[dict[str, Any]]:
    """The one user turn: an instruction, and the file itself as a data URL.

    A data URL rather than a link, because the only URL we have is the `mmg.whatsapp.net` one
    in the payload and it points at an END-TO-END ENCRYPTED blob — the key lives in GOWA's
    session store. Fetching it would send OpenAI ciphertext to describe.
    """
    url = f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
    if kind in DOCUMENT_MESSAGE_TYPES:
        return [
            {"type": "input_text", "text": _DOCUMENT_ASK},
            # `filename` is ours; see `_DOCUMENT_FILENAME`.
            {"type": "input_file", "filename": _DOCUMENT_FILENAME, "file_data": url},
        ]
    return [
        {"type": "input_text", "text": _IMAGE_ASK},
        {"type": "input_image", "image_url": url},
    ]


def _count(source: Any, field: str) -> int:
    """A usage number, or 0. Defensive because the SDK's usage shape has moved before and a
    missing attribute must cost us an accurate token count, not the whole description."""
    value = getattr(source, field, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def derive_media_text(
    session: AsyncSession,
    message_id: UUID,
    *,
    reader: MediaReader | None = None,
) -> Derivation | None:
    """Fetch one attachment from GOWA, keep the bytes, and write a description onto the message.

    Returns the `Derivation` on success and `None` for every "leave it as a photo" case: the
    message is not an image or a document, it already has text, it is already marked, there is
    nothing downloadable, GOWA is unreachable, the media has expired, the file is too big to
    send, its type is one we cannot read, the model has no price, the household is over budget,
    or the description came back empty.

    NEVER RAISES and NEVER COMMITS. The caller's session owns the transaction; the writes here
    happen inside SAVEPOINTs so that a database failure on this optional enrichment cannot
    poison the transaction the webhook still has to commit.

    THE BYTES ARE KEPT BEFORE THE MODEL IS CALLED, and the two are not conditional on each
    other. Storage answers "can the family look at this again?", description answers "can the
    record read it?", and the download is the expensive part they share — so it happens once,
    the file is written, and only then do the gates that govern spending get consulted.

    `reader` exists for tests. Production passes nothing.
    """
    try:
        return await _derive(session, message_id, reader or OpenAIMediaReader())
    except Exception as exc:
        # The bare catch IS the contract (rule 1): a problem reading an attachment must never
        # become a non-2xx on the webhook, because that is five GOWA retries and duplicate
        # messages in a family's chat.
        log.warning(
            "media.derive_failed",
            extra={"message_id": str(message_id), "exc_type": type(exc).__name__},
        )
        return None


async def _derive(
    session: AsyncSession, message_id: UUID, reader: MediaReader
) -> Derivation | None:
    row = (
        await session.execute(
            sa.select(
                Message.household_id,
                Message.provider,
                Message.provider_message_id,
                Message.message_type,
                Message.text,
                Message.payload,
            ).where(Message.id == message_id)
        )
    ).one_or_none()
    if row is None:
        log.info("media.skipped", extra={"message_id": str(message_id), "why": "no_message"})
        return None

    payload: dict[str, Any] = row.payload or {}
    settings = get_settings()

    # (a) The free exits, all of them before anything is downloaded or paid for.
    why = _skip_reason(row, payload)
    if why is not None:
        log.info("media.skipped", extra={"message_id": str(message_id), "why": why})
        return None

    # (b) The bytes. GOWA holds the mediaKey, so this is the only path to them — the URL in the
    # payload is an encrypted blob. `download_media` is bounded by `transcription_max_bytes`
    # and, like everything in that module, returns None rather than raising.
    media = await gowa.download_media(row.provider_message_id)
    if media is None:
        log.info("media.no_media", extra={"message_id": str(message_id), "why": "gowa_unavailable"})
        return None
    data, content_type = media
    # One spelling from here down. `_skip_reason` accepted this row on the lowercased type, so
    # everything that follows — the readable-type tables, the prompt, the `llm_runs` row — uses
    # the same one rather than whatever case the provider happened to send.
    kind = (row.message_type or "").lower()

    # (c) D12: keep them. Best effort, and its failure changes nothing below.
    stored = await store_media(row.household_id, message_id, data, content_type)
    if stored:
        async with session.begin_nested():
            await session.execute(
                sa.update(Message)
                .where(Message.id == message_id)
                .values(
                    media_content_type=content_type,
                    media_byte_count=len(data),
                    media_stored_at=datetime.now(UTC),
                )
            )

    # (d) The gates that govern SPENDING, which is why they are below the storage.
    why = _refuse_reason(kind, content_type, len(data), settings)
    if why is not None:
        log.info(
            "media.not_described",
            extra={"message_id": str(message_id), "why": why, "byte_count": len(data)},
        )
        return None

    model = settings.media_model
    if model not in PRICES:
        # Loud, because this is a configuration mistake that would otherwise spend money the
        # budget guard could not see. Refusing costs a family a description; guessing a price
        # costs them the guard.
        log.error("media.unpriced_model", extra={"model": model})
        return None

    if not await _within_budget(session, row.household_id, model):
        return None

    # (e) The one paid call.
    started = datetime.now(UTC)
    result = await reader.read(model=model, kind=kind, content_type=content_type, data=data)
    latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    try:
        cost = cost_usd(
            model, result.input_tokens, result.output_tokens, result.cached_input_tokens
        )
    except UnpricedModelError:
        # Unreachable via the `PRICES` check above, and still handled: the call has already
        # happened, so the row must be written rather than the spend disappearing.
        log.error("media.unpriced_model", extra={"model": model})
        cost = Decimal("0")

    text = (result.text or "").strip()

    # (f) The writes, in the caller's transaction, inside a savepoint.
    async with session.begin_nested():
        await _record_run(
            session,
            household_id=row.household_id,
            provider_message_id=row.provider_message_id,
            model=model,
            status="ok" if text else "empty",
            cost=cost,
            latency_ms=latency_ms,
            result=result,
        )
        if not text:
            # A meme, a screenshot, a photo of a dog. We pay for the call either way, which is
            # why the row above is written first — but the marker is NOT set, because the
            # marker's meaning is "this message carries a reading of an attachment".
            log.info("media.empty", extra={"message_id": str(message_id)})
            return None

        updated = await session.execute(
            sa.update(Message)
            # `text IS NULL` makes a concurrent second attempt a no-op instead of a
            # last-writer-wins overwrite of the first description.
            .where(Message.id == message_id, Message.text.is_(None))
            .values(
                text=text,
                payload={
                    **payload,
                    DERIVED_MARKER: True,
                    DERIVATION_META_KEY: {
                        "model": model,
                        "kind": kind,
                        "content_type": content_type,
                        "byte_count": len(data),
                    },
                },
            )
        )

    if updated.rowcount == 0:
        # Someone else described it between the SELECT and the UPDATE. Their text stands; our
        # cost is still recorded above, because we still spent it.
        log.info("media.raced", extra={"message_id": str(message_id)})
        return None

    log.info(
        "media.described",
        extra={
            "message_id": str(message_id),
            "model": model,
            "kind": kind,
            "byte_count": len(data),
            "char_count": len(text),
            "cost_usd": str(cost),
            "latency_ms": latency_ms,
            "stored": stored,
        },
    )
    return Derivation(text=text, model=model, kind=kind, cost_usd=cost, stored=stored)


def _skip_reason(row: Any, payload: dict[str, Any]) -> str | None:
    """Why there is nothing to do for this message at all, or None.

    Everything here is free to evaluate and is checked before a byte moves. Note what is NOT
    here: `derive_media_text` being off is not one of these, because the file is still worth
    keeping when the model is switched off — only having nowhere to keep it AND nothing to say
    about it makes the whole exercise pointless.
    """
    settings = get_settings()
    if not settings.derive_media_text and media_root() is None:
        return "nothing_to_do"
    if (row.message_type or "").lower() not in DERIVABLE_MESSAGE_TYPES:
        return "not_derivable"
    if row.text:
        return "already_has_text"
    if payload.get(DERIVED_MARKER):
        return "already_derived"
    if row.provider != DOWNLOADABLE_PROVIDER or not row.provider_message_id:
        # A `.txt` export line reading "<Media omitted>" refers to a file that was never
        # uploaded anywhere we can reach.
        return "no_downloadable_media"
    return None


def _refuse_reason(
    message_type: str, content_type: str, byte_count: int, settings: Settings
) -> str | None:
    """Why these bytes will not be sent to a model, or None. Storage has already happened."""
    if not settings.derive_media_text:
        return "disabled"
    if byte_count > settings.media_derive_max_bytes:
        # Stored, not described. A 12 MB scan is a real document and the family can open it;
        # base64ing it into a request is a cost nobody agreed to.
        return "too_large"
    declared = content_type.split(";")[0].strip().lower()
    if message_type in DOCUMENT_MESSAGE_TYPES:
        return None if declared in READABLE_DOCUMENT_TYPES else "unreadable_document_type"
    return None if declared in READABLE_IMAGE_TYPES else "unreadable_image_type"


async def _within_budget(session: AsyncSession, household_id: UUID, model: str) -> bool:
    """The same monthly guard extraction runs under, asked before we spend.

    Imported inside the function because `app.extraction.service` reads `DERIVED_MARKER` from
    THIS module to know which messages are still waiting for their words — a module-level
    import here would close that loop and neither file would load. `_HouseholdBudget` is
    private by name only: it is a `BudgetGuard` over `llm_runs.cost_usd` and this is exactly
    the arithmetic it exists to do, as `app.assistant` already notes.

    A refusal writes a `budget_denied` row of its own — the shape the gateway writes for the
    same refusal — so an operator reading `llm_runs` can see that a family's photos stopped
    being described and why, rather than inferring it from an absence.

    THE TWO FAILURES ARE NOT THE SAME AND ARE NOT TREATED THE SAME. A household that has spent
    its allowance is a decision, and it is recorded. A budget query that could not run at all is
    an unknown, and an unknown means NO — a guard we failed to consult is not a guard that
    passed — but it must not be filed as a spend decision that nobody made.
    """
    from app.extraction.service import _HouseholdBudget

    try:
        await _HouseholdBudget(session).check(household_id)
    except BudgetExceededError as exc:
        log.warning(
            "media.budget_denied",
            extra={"household_id": str(household_id), "exc_type": type(exc).__name__},
        )
        async with session.begin_nested():
            await session.execute(
                sa.insert(LlmRun).values(
                    household_id=household_id,
                    purpose=DERIVE_PURPOSE,
                    status="budget_denied",
                    model=model,
                    attempts=0,
                    cost_usd=Decimal("0"),
                    error=type(exc).__name__,
                    finished_at=datetime.now(UTC),
                )
            )
        return False
    except Exception as exc:
        log.warning(
            "media.budget_unavailable",
            extra={"household_id": str(household_id), "exc_type": type(exc).__name__},
        )
        return False
    return True


async def _record_run(
    session: AsyncSession,
    *,
    household_id: UUID,
    provider_message_id: str,
    model: str,
    status: str,
    cost: Decimal,
    latency_ms: int,
    result: RawDerivation,
) -> None:
    """One `llm_runs` row per paid description, so the budget guard stays true.

    Written for the empty answer too: an attachment with nothing in it still costs a call, and
    a row that only appears when the model said something useful would make a household's
    spend look smaller than it is on exactly the days it was largest.
    """
    await session.execute(
        sa.insert(LlmRun).values(
            household_id=household_id,
            purpose=DERIVE_PURPOSE,
            status=status,
            model=model,
            reasoning_effort=REASONING_EFFORT,
            # Identifies a repeated reading of the same attachment without storing anything
            # about it, exactly as the gateway's prompt fingerprint does.
            request_fingerprint=hashlib.sha256(provider_message_id.encode()).hexdigest()[:32],
            attempts=1,
            input_tokens=result.input_tokens,
            cached_input_tokens=result.cached_input_tokens,
            output_tokens=result.output_tokens,
            reasoning_tokens=result.reasoning_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            error=None if status == "ok" else "empty_description",
            finished_at=datetime.now(UTC),
        )
    )
    log.info(
        "media.recorded",
        extra={"model": model, "status": status, "cost_usd": str(cost)},
    )
