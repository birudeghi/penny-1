"""Every shape the HTTP API accepts or returns, and the ONE place a row becomes an `Event`.

Two model shapes exist in this repo, deliberately (see `docs/api-contract.md`):

* the LLM emits one **flat** `ExtractedEvent` — strict structured outputs reject a
  `discriminator` and reject a non-object root — and `extraction.merge._details` folds its
  kind-prefixed fields (`symptom_name`, `medication_action`, `note_category`) into the
  per-kind `details` object that gets stored in `events.details`;
* the API returns a **discriminated union** on `kind`, so `switch (event.kind)` is exhaustive
  in TypeScript and a new kind fails `tsc -b` at every render site.

`to_event()` below is the only bridge between a database row and that union. Every route that
returns an event goes through it, so a rename lands in one place instead of four.

Precision is spelled the CONTRACT's way (`exact`/`day`/…), never the model's (`datetime`/
`date`/…). The translation already exists in `extraction.runner.normalise_precision` and is
imported rather than rewritten: a second copy is a second thing to get out of step, and the
failure mode is a value the `events.occurred_at_precision` CHECK constraint rejects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, ValidationError

from app import care_profile
from app.care_profile import CareProfile
from app.extraction.runner import normalise_precision

logger = logging.getLogger(__name__)


def _iso_utc(value: datetime) -> str:
    """ISO 8601 in UTC with a trailing `Z`, never a local offset.

    The client renders every timestamp in `household.timezone` with `Intl`, so a response
    carrying `+01:00` is not wrong-looking, it is wrong twice: once in the raw value and
    again after the browser converts it.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]

# A stored JSONB string that the contract types non-null but the extraction model may have
# left null: `ExtractedEvent.symptom_name` and `medication_name` are `str | None`, and
# `merge._details` passes them straight through. Null there must cost that one field, not the
# whole feed page — an empty string renders as nothing, a 500 renders as nothing at all.
NullableStr = Annotated[str, BeforeValidator(lambda v: "" if v is None else v)]

EventKind = Literal["symptom", "appointment", "medication", "note"]
OccurredAtPrecision = Literal["exact", "day", "week", "month", "unknown"]
Severity = Literal["mild", "moderate", "severe", "unknown"]
AppointmentKind = Literal["gp", "specialist", "hospital", "test", "therapy", "other"]
AppointmentStatus = Literal["scheduled", "attended", "cancelled", "missed"]
MedicationAction = Literal[
    "started", "stopped", "changed", "missed", "refilled", "side_effect", "other"
]
NoteCategory = Literal["logistics", "mood", "finance", "equipment", "admin", "other"]

FEED_DEFAULT_LIMIT = 200
FEED_MAX_LIMIT = 500
UPCOMING_LIMIT = 50

# How many photographs ride along with one entry on the feed. A DISPLAY cap, not a fetch cap:
# `GET /api/events/{id}/photos` returns the whole set for one entry so the lightbox can page
# past it. It lives here because it bounds the feed response — 4 x `FEED_MAX_LIMIT` rows is the
# worst case a page can cost, and that number has to be a constant somebody can find.
FEED_PHOTOS_PER_EVENT = 4

# How far EITHER SIDE of an entry's most recent source message an UNCITED photograph may be
# picked up from. Cited photographs ignore this entirely (see `Photo.cited`): the model attached
# them to this entry and no clock may overrule that.
#
# Anchored on the most recent source rather than spanning all of them, and this is not a detail.
# `events.source_message_ids` grows on every re-extraction, so a long-running entry's span can
# reach across a week of unrelated pictures — and an entry drawn from a single message has a span
# of ZERO, which made the first version of this rule match nothing at all for the commonest shape
# of entry. See `app.photos` for the full argument.
PHOTO_WINDOW = timedelta(hours=24)
MIN_PASSWORD_CHARS = 12
TITLE_MAX_CHARS = 80
BODY_MAX_CHARS = 400


class SourceExcerpt(BaseModel):
    """Verbatim evidence. This is what makes the feed trustworthy enough to show a family."""

    model_config = ConfigDict(extra="ignore")

    message_id: UUID
    sent_at: UtcDatetime
    # The contract types this `string`; the extraction runner's excerpt allows None for a
    # message whose sender never resolved. Empty string renders as nothing, which is honest —
    # substituting a placeholder name would put a fabricated attribution next to a real quote.
    sender: NullableStr = ""
    quote: NullableStr = ""
    # This quote was spoken into a voice note and transcribed by a model, not typed. Defaults
    # to False so every event written before transcription existed still renders — and so an
    # excerpt that lost the key can only ever under-claim, never assert a transcript we do not
    # have. The UI labels it; the family can then check the words against the recording.
    transcribed: bool = False
    # This quote is a model's description of a photo or a document that was shared: NOBODY
    # typed it and nobody said it. A stronger claim than `transcribed` and a more important one
    # to render — evidence attributed to Sarah that Sarah never wrote is the way a family stops
    # trusting the record. Defaults to False for the same "can only under-claim" reason, and
    # the file itself is at `GET /api/media/{message_id}` so the description is checkable.
    derived_from_media: bool = False


class Photo(BaseModel):
    """A photograph shared in the conversation this entry was drawn from.

    NOT A `SourceExcerpt`, and the difference is the whole reason this model exists. An excerpt
    is evidence: the model read it and cited it. A photo may be evidence (`cited=True`) or it
    may be a picture from the same conversation that Penny judged to be about this person's
    care but did not attach to this entry. Putting the second kind into `source_excerpts` would
    make that array lie, and that array's honesty is what the feed is built on.

    `description` IS ALWAYS PENNY'S WORDS, never the family's, and that is an invariant of the
    ingest path rather than something checked here. `webhooks._classify` returns `(kind, None)`
    for every media message, so a WhatsApp caption is discarded at the door and a stored photo
    always arrives with `text IS NULL`. `media._skip_reason` then refuses to download anything
    that already HAS text, so a row carrying a human's words never gets bytes and therefore
    never gets a `media_content_type` — which is the column the photo query filters on. There
    is no reachable row with stored bytes and human-written text, so there is no flag here to
    tell the two apart. `test_media_derivation.py` asserts the download half of that.

    The bytes are at `GET /api/media/{message_id}`, same-origin and cookie-authenticated.
    """

    model_config = ConfigDict(extra="ignore")

    message_id: UUID
    sent_at: UtcDatetime
    sender: NullableStr = ""
    # Penny's reading of the picture, or empty when she could not produce one — over budget, an
    # unreadable type, a file too large to send. A CITED photo can reach the feed undescribed;
    # an uncited one cannot, because the marker meaning "this is about her care" is written in
    # the same transaction as the text and the query requires it.
    description: NullableStr = ""
    # This photo is in the entry's `source_message_ids`: the model attached it to this entry.
    # False is the weaker claim — shared nearby, judged relevant, not linked — and it is worded
    # differently everywhere it is shown, because overclaiming here is how a holiday snap ends
    # up reading as proof for a medical entry.
    cited: bool = False


class EventPhotos(BaseModel):
    """`GET /api/events/{id}/photos` — the uncapped set, for paging in the lightbox.

    A wrapper object and not a bare array, for the reason every list endpoint here is: an array
    at the root of a response has nowhere to grow. The day this needs a count, a cursor, or a
    "some of these are gone from the volume" flag, a wrapper takes a key and a bare array takes
    a breaking change.
    """

    photos: list[Photo] = Field(default_factory=list)


class EventActor(BaseModel):
    """Who did or reported it — the WhatsApp sender, not whoever is signed in."""

    member_id: UUID
    display_name: str


# --- per-kind `details` -------------------------------------------------------------------
#
# Every field has a default. `events.details` is JSONB written by the extraction pipeline, and
# a feed that 500s because one row is missing one key is a worse outcome than a feed with one
# thin card in it. `extra="ignore"` for the same reason: an older row written by an earlier
# prompt version must still render.


class SymptomDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symptom: NullableStr = ""
    severity: Severity = "unknown"
    body_site: str | None = None
    duration_text: str | None = None


class AppointmentDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    appointment_kind: AppointmentKind = "other"
    provider_name: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    outcome: str | None = None
    follow_up_actions: list[str] = Field(default_factory=list)
    # An appointment is ONE event for its whole life: it is created `scheduled` and gains an
    # outcome and `attended` when the family reports back, on the same row via `dedup_key`.
    status: AppointmentStatus = "scheduled"


class MedicationDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    medication_name: NullableStr = ""
    dose_text: str | None = None
    action: MedicationAction = "other"
    prescriber: str | None = None


class NoteDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: NoteCategory = "other"


class EventBase(BaseModel):
    id: UUID
    # NEVER null: it falls back to the earliest source message's `sent_at`, because Postgres
    # `DESC` sorts NULLS FIRST and undated events would otherwise pin to the top of the feed.
    occurred_at: UtcDatetime
    occurred_at_precision: OccurredAtPrecision
    title: str
    body: str | None = None
    actor: EventActor | None = None
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)
    # The photographs filed with this entry: cited ones first, then most recent. Capped at
    # `FEED_PHOTOS_PER_EVENT` for display. Always present, `[]` when there are none — a client
    # must never have to tell "no photos" apart from "this response predates photos".
    photos: list[Photo] = Field(default_factory=list)
    # How many more matched beyond `photos`. A count rather than a silence: this record's whole
    # argument is that an absence is never left looking like nothing happened. The UI says
    # "+N more" and the lightbox pages through them via `GET /api/events/{id}/photos`.
    photos_withheld: int = 0
    # Photographs in this entry's conversation whose files we do not hold, which in practice
    # means a `.txt` import — a WhatsApp export writes `<image omitted>` and ships no file, and
    # those rows carry no `provider_message_id` to fetch by, ever. Counted so the UI can say so
    # instead of leaving a gap the family has to explain to themselves.
    photos_unavailable: int = 0
    # Non-null means a human edited it and re-extraction will never overwrite it again.
    edited_at: UtcDatetime | None = None
    created_at: UtcDatetime


class SymptomEvent(EventBase):
    kind: Literal["symptom"] = "symptom"
    details: SymptomDetails


class AppointmentEvent(EventBase):
    kind: Literal["appointment"] = "appointment"
    details: AppointmentDetails


class MedicationEvent(EventBase):
    kind: Literal["medication"] = "medication"
    details: MedicationDetails


class NoteEvent(EventBase):
    kind: Literal["note"] = "note"
    details: NoteDetails


AnyEvent = Annotated[
    SymptomEvent | AppointmentEvent | MedicationEvent | NoteEvent,
    Field(discriminator="kind"),
]

_EVENT_MODEL: dict[str, type[EventBase]] = {
    "symptom": SymptomEvent,
    "appointment": AppointmentEvent,
    "medication": MedicationEvent,
    "note": NoteEvent,
}

# Used by PATCH to validate a merged `details` object before it is stored, so a typo'd key
# from the edit form is a 422 rather than a field that quietly stops rendering.
DETAILS_MODEL: dict[str, type[BaseModel]] = {
    "symptom": SymptomDetails,
    "appointment": AppointmentDetails,
    "medication": MedicationDetails,
    "note": NoteDetails,
}


def _coerce_details(model: type[BaseModel], raw: Any) -> dict[str, Any]:
    """Drop what a stored `details` object cannot legally contain, so the field falls back.

    Tolerant on READ only. `events.details` is JSONB written by whichever prompt version was
    live at the time, and a value that no longer parses — a null `symptom`, a `severity` an
    older prompt allowed — must cost that one field rather than 500 the whole feed page. The
    write path (PATCH) validates the same models strictly, because there a rejected value is a
    typo the person can still fix.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        field = model.model_fields.get(key)
        if field is None:
            continue  # a key from a newer or older shape; `extra="ignore"` would drop it anyway
        annotation = field.annotation
        if value is None and type(None) not in get_args(annotation):
            continue
        if get_origin(annotation) is Literal and value not in get_args(annotation):
            logger.warning("schemas.details_value_dropped", extra={"field": key})
            continue
        cleaned[key] = value
    return cleaned


@dataclass(frozen=True, slots=True)
class PhotoSet:
    """What one entry's photo lookup found: the ones to show, and what it did not show.

    A dataclass and not a dict because all three fields have to travel together — a caller that
    hands `to_event` a list of photos and forgets the two counts produces a card that quietly
    claims it is showing everything. Bundling them makes that omission impossible to express.
    """

    photos: list[dict[str, Any]] = field(default_factory=list)
    withheld: int = 0
    unavailable: int = 0


def _excerpts(raw: Any) -> list[dict[str, Any]]:
    """Keep the excerpts that parse, drop the ones that don't, never fail the request.

    An excerpt is evidence attached to an event, not the event. One malformed entry from an
    older prompt version must cost that quote, not the whole feed page.
    """
    if not isinstance(raw, list):
        return []
    kept: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            SourceExcerpt.model_validate(item)
        except ValidationError:
            logger.warning("schemas.excerpt_dropped")  # no text, ever — ids and counts only
            continue
        kept.append(item)
    return kept


def to_event(
    row: Any,
    actor_display_name: str | None = None,
    photos: PhotoSet | None = None,
) -> Any:
    """A database row -> the per-kind union the browser sees. The only conversion, on purpose.

    `row` is an `app.models.Event`; it is typed loosely so this module does not import the ORM
    just to annotate one parameter. `actor_display_name` comes from the caller's join, because
    doing it here would be a lazy load per row on an async session.

    `photos` DEFAULTS TO NONE AND MEANS "none were looked up", not "there are none". That
    distinction is why the parameter is optional rather than required: `routers/events.py` calls
    this after a PATCH and after a POST, where the entry's photographs have not been queried and
    are not what the caller asked about. Making it required would force those two call sites to
    invent an answer, and the answer they would invent is an empty list — which is a claim that
    this entry has no photographs. It says nothing instead, and renders as `[]` with both counts
    at zero, exactly as a genuinely photoless entry does.
    """
    kind = row.kind if row.kind in _EVENT_MODEL else "note"
    actor = None
    if row.actor_member_id is not None:
        actor = {
            "member_id": row.actor_member_id,
            "display_name": actor_display_name or "",
        }
    return _EVENT_MODEL[kind].model_validate(
        {
            "id": row.id,
            "kind": kind,
            "occurred_at": row.occurred_at,
            # The one spelling bridge, imported not rewritten.
            "occurred_at_precision": normalise_precision(row.occurred_at_precision),
            "title": row.title,
            "body": row.body,
            "actor": actor,
            "details": _coerce_details(DETAILS_MODEL[kind], row.details),
            "source_excerpts": _excerpts(row.source_excerpts),
            "photos": [] if photos is None else photos.photos,
            "photos_withheld": 0 if photos is None else photos.withheld,
            "photos_unavailable": 0 if photos is None else photos.unavailable,
            "edited_at": row.edited_at,
            "created_at": row.created_at,
        }
    )


# --- feed ---------------------------------------------------------------------------------


class PendingReread(BaseModel):
    """The stretch of conversation Penny has ingested but has NOT yet read, said out loud.

    THE FAILURE THIS PREVENTS. `messages.extracted_at` is the extraction cursor: NULL means
    "not yet fed to the model". Re-extraction is triggered by clearing it — the onboarding
    interview corrects the care profile and clears the whole household's cursor so the improved
    filter reaches the history that was read under the old one. That re-read costs money, and
    the monthly budget aborts it MID-FLIGHT by design, leaving the cursor part-drained and the
    rest to be picked up by a later tick.

    While that is true the feed is a mixture: some of it read under the corrected filter, some
    of it not read yet at all. And a stretch Penny has not read is INDISTINGUISHABLE ON SCREEN
    from a stretch where nothing happened. Both are simply an absence of cards. A family looking
    at last March and seeing nothing concludes that nothing was recorded in March — which is the
    exact "lost the thread" failure this product exists to prevent, arriving through the
    mechanism built to fix it. Nothing in the event stream can correct that impression, because
    the missing events are missing; only a statement about the CURSOR can.

    So this object is a statement about the cursor, not about events: how many messages are
    waiting, and the span of real time they were sent across. It is derived on every read from
    `extracted_at IS NULL` and there is no column behind it — a stored flag would need clearing
    by every one of the four things that stamp the cursor, and the one that forgot would leave a
    banner up forever on a household with nothing left to read.

    WHY A RANGE AND A COUNT RATHER THAN "everything before X". The unread set is not guaranteed
    contiguous. Voice notes waiting on a transcript are deliberately left NULL for a few minutes
    while the words are fetched, so a household mid-re-read can hold a handful of unread
    messages from yesterday and thousands from last March at the same time. A count plus the
    outer bounds is the strongest claim that is true of every possible shape of that set; "we
    have read everything after 4 March" would be a claim we cannot support, and a wrong
    reassurance is worse than no reassurance in a record a family makes decisions from.
    """

    # How many messages are still waiting. A number, never a list of ids: the client's job is
    # to say "there is more here than you can see", not to enumerate a family's chat.
    message_count: int
    # The `sent_at` bounds of exactly those messages. NOT the household's whole history — the
    # point of the banner is to name the part that is missing, and naming the whole history
    # would say "all of this is unread" the moment one old message came back into the set.
    first_sent_at: UtcDatetime
    last_sent_at: UtcDatetime

    @classmethod
    def from_aggregate(
        cls,
        message_count: int,
        first_sent_at: datetime | None,
        last_sent_at: datetime | None,
    ) -> PendingReread | None:
        """Build one from a `COUNT/MIN/MAX` row, or `None` when there is nothing to say.

        The empty case is the overwhelmingly common one — a household that is fully caught up
        is the steady state — so it gets the cheapest possible representation and the client
        gets one `null` check rather than a zero it has to remember to treat as absence.

        The `None` bounds guard is not defensive noise about SQL. `MIN`/`MAX` are NULL exactly
        when `COUNT` is zero, so in the query this class is fed by the two can never disagree.
        It is here because this constructor is the one place a future caller could feed the two
        halves from different queries, and the honest answer to "there are 40 unread messages
        but I do not know when they were sent" is to say nothing at all rather than to render a
        banner with an empty date range in it, which reads as a bug and destroys the trust the
        banner exists to protect.
        """
        if message_count <= 0 or first_sent_at is None or last_sent_at is None:
            return None
        return cls(
            message_count=message_count,
            first_sent_at=first_sent_at,
            last_sent_at=last_sent_at,
        )


class FeedPage(BaseModel):
    events: list[AnyEvent]
    # The `occurred_at` of the last event returned, or null when there is no next page. The
    # client stops paginating on null; returning the cursor unconditionally makes it loop.
    next_before: UtcDatetime | None = None
    # Null when the household is caught up. ALWAYS PRESENT AS A KEY, including on later pages:
    # a field that appears only when there is something to report trains a client to treat
    # "absent" as "unknown", and the one place that matters is the page where it is absent
    # because everything has been read. Explicit `null` is the difference between "caught up"
    # and "this response predates the field".
    pending_reread: PendingReread | None = None


class UpcomingPage(BaseModel):
    """An empty list is the common case and must render as an empty state, not a spinner."""

    events: list[AnyEvent]


class EventPatch(BaseModel):
    """All fields optional; `details` is MERGED key by key, not replaced.

    `extra="forbid"` so a typo'd field name is a 422 the client can render, rather than a 200
    that silently changed nothing — the worst possible outcome for an edit form.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_CHARS)
    body: str | None = Field(default=None, max_length=BODY_MAX_CHARS)
    occurred_at: datetime | None = None
    occurred_at_precision: OccurredAtPrecision | None = None
    details: dict[str, Any] | None = None


# --- household ----------------------------------------------------------------------------


class VitalOut(BaseModel):
    """One clinical fact and the day somebody last said it was still true.

    `confirmed_at` is a free string, not a date, and it crosses the wire as one — the value came
    from a WhatsApp reply through a language model, so "Jan 2026" and "last year's review" are
    as common as an ISO date. The band renders it verbatim; nothing parses it.
    """

    value: str
    confirmed_at: str | None = None


class CareProfileOut(BaseModel):
    """The part of `CareProfile` the record's own page reads.

    Not the whole model. `name`, `aliases` and `providers` are the extraction filter's business
    and nothing on screen uses them, so they are not published — a field on the wire is a field
    somebody will render, and the brief's readers and this page's readers want opposite things
    from the same silence.

    THE FOUR VITALS ARE ALWAYS PRESENT AS KEYS. Absent means null, never missing. "We do not know
    her allergies" and "she has no allergies" are different facts, only one is safe to act on,
    and a key that vanished when empty would let a client collapse the two. `care_profile.py`'s
    `_render_vitals` does the exact opposite for the model, and says why.
    """

    age: int | None = None
    place: str | None = None
    # Rendered as "Living with" and "Medications" beside the timeline. Lists, in the family's
    # own words, never expanded or tidied — the same values the brief carries.
    conditions: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    nhs_number: VitalOut | None = None
    allergies: VitalOut | None = None
    respect_form: VitalOut | None = None
    baseline: VitalOut | None = None


class HouseholdOut(BaseModel):
    id: UUID
    name: str
    care_recipient_name: str
    timezone: str
    care_profile: CareProfileOut


def to_household(row: Any) -> HouseholdOut:
    """The `household` object, identical in `GET /api/me` and `PATCH /api/household`.

    One function so the two can never drift — and so neither can ever include
    `password_hash` by reaching for `model_validate(row, from_attributes=True)`.

    The vitals are read through `care_profile.load`, which is what makes a malformed stored
    profile a household with four blanks rather than a 500 on the screen a family opens first.
    """
    profile = care_profile.load(row)
    return HouseholdOut(
        id=row.id,
        name=row.name,
        care_recipient_name=row.care_recipient_name,
        timezone=row.timezone,
        care_profile=CareProfileOut(
            age=profile.age,
            place=profile.place,
            conditions=list(profile.conditions),
            medications=list(profile.medications),
            **{
                field: VitalOut(value=vital.value, confirmed_at=vital.confirmed_at)
                for field in ("nhs_number", "allergies", "respect_form", "baseline")
                if (vital := getattr(profile, field)) and vital.value
            },
        ),
    )


class SessionCounts(BaseModel):
    events: int
    messages: int


class SessionOut(BaseModel):
    """`GET /api/me` — the session probe the app boots on. A 401 here is normal, not an error."""

    household: HouseholdOut
    counts: SessionCounts


class HouseholdPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    care_recipient_name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    # `CareProfile` itself, not a restatement of it. Its caps and its list-cleaning are what
    # keep the brief hash stable, and a parallel schema here would be a second set of rules to
    # keep in step — with a failure mode that is invisible until a profile saved through the
    # API hashes differently from the identical one saved by the interview.
    #
    # WHOLE-OBJECT, not per-field: the profile is one specification of what counts as relevant,
    # and a partial write would mean "aliases omitted" and "aliases cleared" arrive as the same
    # request. Clearing aliases has to be possible, and it has to be deliberate.
    care_profile: CareProfile | None = None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    # Length is checked in the handler, not here, so the 422 reads as a sentence a family
    # understands instead of pydantic's "String should have at least 12 characters".
    new_password: str


# --- members ------------------------------------------------------------------------------


class MemberOut(BaseModel):
    """A WhatsApp participant, not a login account: there is one shared family credential."""

    id: UUID
    display_name: str
    wa_jid: str | None = None
    wa_lid: str | None = None
    message_count: int = 0
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime


class MemberMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    into_member_id: UUID
