"""The relevance specification: who this household is caring for, in enough detail to tell
signal from noise in a group chat.

THIS MODULE OWNS THE BRIEF. Before it existed, `build_care_brief` lived in
`extraction/service.py` and every consumer rendered its own `<care_brief>` block — the
extraction runner at one call site, the assistant at another. Two renderers drift, and drift
here is silent and specific: extraction filters for one person while the assistant answers
about a subtly different one, and nothing fails. `render_brief` is the only renderer. Anything
that needs the brief imports it from here.

WHY A PROFILE AND NOT A NAME. `households.care_recipient_name` is one string, and one string
is not a filter. In a family group the care recipient is almost never named — she is "Mum",
"Nan", "her", "she". A brief that says `Care recipient: Margaret` gives the model nothing to
resolve "she's been off her food since Tuesday" against, in a chat where three women get
discussed. `aliases` is the highest-value field in this file for exactly that reason: it is
what the family actually types.

THE HASH IS OVER RENDERED TEXT, NOT A FIELD LIST. `brief_hash` hashes the lines
`_profile_lines` produced, so a field added to `CareProfile` and printed into the brief
changes the hash without anybody remembering to add it to a tuple somewhere. The alternative —
hashing an explicit list of relevance-bearing fields — has a failure mode that is both silent
and permanent: add `conditions_v2`, forget the list, and profile edits stop triggering
re-extraction forever, with no error and no symptom except a feed that quietly stops
improving. A hash you cannot forget to update is worth the handful of wasted bytes.

IT COVERS THE PROFILE ONLY — not the whole brief. `render_brief` appends two context lines
(who else talks, and the timezone) that the model needs and the hash must not see. Both change
for reasons that have nothing to do with what counts as relevant: a member joins, somebody
fixes a timezone typo in settings. Hashing those would re-read a household's entire history
and re-bill it for an edit that changed no judgement.

WHAT THE HASH IS FOR. `messages.extracted_at IS NULL` is the extraction cursor: a stamped
message is never reconsidered. Every message stamped under the placeholder profile was judged
by a filter that did not know the care recipient's name, so a family who backfilled before
answering the interview has a record filtered against nothing. Comparing this hash on write is
what decides whether to clear the cursor and re-read the history. It is deliberately NOT
"any write to the profile" — a settings save that changes the timezone renders the same brief,
hashes the same, and costs nothing.

VITALS ARE CLINICAL SAFETY DATA. `allergies` and `respect_form` are read by people making
decisions in a hurry. A blank allergies line is honest and useful; a stale one is dangerous,
which is why every vital carries `confirmed_at` and the renderer prints the date beside the
value. Penny does not decide these are still true — the family does, and the record says when.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Member

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Household

# Rendered into the brief when the interview has not run. It is deliberately not a name: a
# plausible-looking placeholder ("Mum") would filter *something*, and filtering the wrong
# something is worse than filtering nothing, because it looks like it worked.
UNKNOWN = "not yet established"

# Caps exist so one pasted essay in a WhatsApp reply cannot push the transcript out of a
# chunk. They are generous — a real answer to "what is she living with?" runs long.
MAX_ALIASES = 8
MAX_LIST_ITEMS = 20
MAX_ITEM_CHARS = 120
MAX_NOTES_CHARS = 1_000


class Vital(BaseModel):
    """One clinical fact, with the date a human last confirmed it.

    `confirmed_at` is not `updated_at`: it records when somebody said "yes, still true", not
    when a row was written. An allergies line carried forward untouched for two years is
    exactly the case the date exists to expose.
    """

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=MAX_ITEM_CHARS)
    # ISO date string, not a datetime: this is set from a WhatsApp reply, and a date is the
    # most precision that claim ever honestly has.
    confirmed_at: str | None = None


class CareProfile(BaseModel):
    """What the onboarding interview establishes, and what extraction filters by.

    Every field here is rendered into the brief, and therefore into the hash, and therefore
    can trigger a re-read of the history. Nothing that does not change what counts as relevant
    belongs in this model — timezone and display name live on `households` precisely because
    changing them should cost nothing.
    """

    model_config = ConfigDict(extra="forbid")

    # --- identity: the part that resolves pronouns ---
    name: str | None = Field(default=None, max_length=MAX_ITEM_CHARS)
    # THE most valuable field in this model. "Mum", "Nan", "Gran" — what the family types.
    aliases: list[str] = Field(default_factory=list)
    age: int | None = Field(default=None, ge=0, le=130)
    place: str | None = Field(default=None, max_length=MAX_ITEM_CHARS)

    # --- what makes a message a care event for THIS person ---
    conditions: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    # "Dr Aziz", "Salford Royal" — so a bare place name parses as an appointment.
    providers: list[str] = Field(default_factory=list)

    # --- vitals: read first, by people in a hurry ---
    nhs_number: Vital | None = None
    allergies: Vital | None = None
    respect_form: Vital | None = None
    baseline: Vital | None = None

    # --- free text the interview could not slot anywhere else ---
    notes: str | None = Field(default=None, max_length=MAX_NOTES_CHARS)

    @field_validator("aliases", "conditions", "medications", "providers")
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        """Strip, drop empties, dedupe case-insensitively, cap, and SORT.

        THE OUTPUT IS INDEPENDENT OF INPUT ORDER, and that is the whole point. This feeds
        `brief_hash`, and the hash decides whether to re-read a household's entire history at
        its own expense. An interview that captures the same three conditions in a different
        order, or a family that types "Nan" one week and "nan" the next, must not read as a
        changed filter.

        Two things make that true: the sort, and picking `min()` among the case-variants of a
        duplicate rather than whichever spelling happened to arrive first. Keeping first-seen
        looks harmless and is not — it makes the stored value a function of message order, so
        the same answers re-captured in a different sequence would hash differently and bill
        the family for a re-read that changed nothing.
        """
        variants: dict[str, list[str]] = {}
        for item in value:
            cleaned = " ".join(item.split())[:MAX_ITEM_CHARS].strip()
            if cleaned:
                variants.setdefault(cleaned.casefold(), []).append(cleaned)
        chosen = [min(spellings) for spellings in variants.values()]
        return sorted(chosen, key=str.casefold)[:MAX_LIST_ITEMS]

    @field_validator("aliases")
    @classmethod
    def _cap_aliases(cls, value: list[str]) -> list[str]:
        return value[:MAX_ALIASES]

    def is_established(self) -> bool:
        """True once the interview has produced something worth filtering by.

        A name alone is enough — it is the field that turns "she" into a person. Everything
        else sharpens a filter that already works.
        """
        return bool(self.name)


def load(household: Household) -> CareProfile:
    """The household's profile, or an empty one. Never raises on bad stored JSON.

    A profile that will not parse is treated as absent rather than fatal: the cost of an
    empty brief is a noisier feed, and the cost of raising here is that a single malformed
    row takes down extraction, the assistant and the feed together.
    """
    raw = household.care_profile or {}
    if not isinstance(raw, dict):
        return CareProfile()
    try:
        return CareProfile.model_validate(raw)
    except Exception:  # see the docstring: absent beats fatal.
        return CareProfile()


def render_brief(
    profile: CareProfile,
    *,
    participants: list[str],
    timezone: str,
    fallback_name: str | None = None,
) -> str:
    """THE renderer. Every consumer calls this one; nobody formats a brief of their own.

    `fallback_name` is `households.care_recipient_name`, which is NOT NULL and holds a
    placeholder until the interview runs. It is used only when the profile has no name — the
    profile wins whenever it has an opinion, because it is the thing the family actually told
    Penny.

    Line order is deliberate and stable: identity first (it is what resolves the pronouns),
    then the exclusion rule, then the vocabulary that makes a bare drug or place name
    recognisable, then who is in the room. Reordering the PROFILE lines changes the hash and
    re-reads every household's history, so treat that order as part of the contract.
    """
    lines = _profile_lines(profile, fallback_name=fallback_name)
    # CONTEXT, NOT SPECIFICATION — and therefore deliberately outside the hash. Both belong in
    # the prompt: the model needs to know who else talks and what timezone the dates are in.
    # Neither changes what counts as relevant, so neither is worth re-reading a household's
    # entire history for. Somebody fixing a timezone typo in settings must not trigger a bill.
    lines.append(f"People in this chat: {_join(participants) or 'unknown'}.")
    lines.append(f"Household timezone: {timezone}.")
    return "\n".join(lines)


def brief_hash(profile: CareProfile, *, fallback_name: str | None = None) -> str:
    """sha256 of the profile's rendered lines, first 32 hex chars.

    Over the RENDERED lines rather than a list of field names — see the module docstring. A
    field added to `CareProfile` and printed by `_profile_lines` changes this hash without
    anybody remembering to update a tuple. A field added and never rendered does not, which is
    correct: a value the model never sees cannot have changed what it extracted.

    Takes the profile rather than the finished brief so that participants and timezone, which
    `render_brief` appends afterwards, stay out of it. `households.care_brief_hash` stores
    this; a profile write compares against it to decide whether to clear the extraction cursor.
    """
    rendered = "\n".join(_profile_lines(profile, fallback_name=fallback_name))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:32]


def _profile_lines(profile: CareProfile, *, fallback_name: str | None = None) -> list[str]:
    """The hashed half of the brief: everything the interview establishes, and nothing else."""
    name = profile.name or fallback_name or UNKNOWN
    lines = [f"Care recipient: {name}."]

    if profile.aliases:
        lines.append(
            f"The family calls her {_join(profile.aliases)} — treat these, and unattributed "
            '"she"/"her" in this chat, as referring to her.'
        )

    about = _describe(profile)
    if about:
        lines.append(f"She is {about}.")

    lines.append("Messages about anyone else are not care events unless that person is the actor.")

    if profile.conditions:
        lines.append(f"Living with: {_join(profile.conditions)}.")
    if profile.medications:
        lines.append(f"Current medications: {_join(profile.medications)}.")
    if profile.providers:
        lines.append(f"Her clinicians and services: {_join(profile.providers)}.")

    vitals = _render_vitals(profile)
    if vitals:
        lines.append(vitals)

    if profile.notes:
        lines.append(f"Also worth knowing: {profile.notes.strip()}")

    return lines


async def participants_for(session: AsyncSession, household_id: Any) -> list[str]:
    """Display names of everyone who has spoken in this household's chat, sorted.

    Sorted for the same reason the profile's lists are: a member joining must change the
    brief, but the ORDER the database happened to return them in must not.
    """
    names = (
        (
            await session.execute(
                sa.select(Member.display_name)
                .where(Member.household_id == household_id)
                .order_by(Member.display_name)
            )
        )
        .scalars()
        .all()
    )
    return [name for name in names if name]


async def build_brief(session: AsyncSession, household: Household) -> str:
    """The brief for a household, rendered. The one call every consumer makes.

    Replaces `extraction.service.build_care_brief`, which is now a thin alias kept only so
    the import in older call sites keeps working.
    """
    return render_brief(
        load(household),
        participants=await participants_for(session, household.id),
        timezone=household.timezone,
        fallback_name=household.care_recipient_name,
    )


def _describe(profile: CareProfile) -> str:
    """ "84, in Croydon" — the half-sentence the design's person header prints."""
    parts: list[str] = []
    if profile.age is not None:
        parts.append(str(profile.age))
    if profile.place:
        parts.append(f"in {profile.place}")
    return ", ".join(parts)


def _render_vitals(profile: CareProfile) -> str:
    """Vitals as one line, each carrying the date it was last confirmed.

    Absent vitals are omitted rather than printed as "unknown": the model does not need to be
    told what nobody has said, and a line of blanks is prompt weight for no signal. The web
    UI does the opposite and keeps the blank rows visible, because there a missing allergies
    line is information a reader should see.
    """
    named = (
        ("NHS number", profile.nhs_number),
        ("Allergies", profile.allergies),
        ("RESPECT form", profile.respect_form),
        ("Baseline", profile.baseline),
    )
    parts = [
        f"{label}: {vital.value}"
        + (f" (confirmed {vital.confirmed_at})" if vital.confirmed_at else "")
        for label, vital in named
        if vital and vital.value
    ]
    return "; ".join(parts) + "." if parts else ""


def _join(items: list[str]) -> str:
    """Oxford-comma-free list. Plain prose reads better in a prompt than a JSON array."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"
