"""The write path: when a profile edit puts a household's history back on the cursor.

`messages.extracted_at IS NULL` is the extraction cursor and a stamped message is never
reconsidered, so exactly one thing decides whether a family's six months of backfilled chat
ever gets re-read against a sharper filter: whether `write_profile` clears it. Both ways of
getting that wrong are silent and neither shows up as an error:

    cleared when it should not be  -> the whole history re-read and re-billed for an edit that
                                      changed no judgement, against a $15/month pot
    not cleared when it should be  -> six months filtered by a brief that did not know her
                                      name, and a family who sees a thin record rather than a
                                      failure

So every test here states the rule in its name, and asserts on the statement the session was
actually asked to run.

WHY A FAKE SESSION AND NOT POSTGRES. What is under test is a DECISION — issue the UPDATE or do
not — and the negative half of it ("no cost") is a statement that must never be built. A real
database can only be asked what rows look like afterwards, and "no rows changed" is equally
true of the UPDATE that ran and matched nothing. Recording the statements makes the difference
visible, and it makes these tests run in the default suite rather than skipping wherever
`PENNY_TEST_DATABASE_URL` is unset — which is where the expensive mistake would be made.

The statements are compiled against the real Postgres dialect before being asserted on, so the
tenant filter and the `extracted_at IS NOT NULL` guard are checked as SQL rather than as
SQLAlchemy objects that happen to be constructed nearby.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app import care_profile as cp
from app.deps import HouseholdCtx
from app.models import Household
from app.routers.household import patch_household
from app.schemas import HouseholdPatch

PLACEHOLDER = "your loved one"


class FakeResult:
    """Just the one attribute `write_profile` reads off an UPDATE."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeSession:
    """Records what it was asked to execute. The absence of a statement IS an assertion here."""

    def __init__(self, rowcount: int = 0) -> None:
        self._rowcount = rowcount
        self.statements: list[Any] = []
        self.flushes = 0

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self._rowcount)

    async def flush(self) -> None:
        self.flushes += 1


def make_household(
    *,
    care_recipient_name: str = PLACEHOLDER,
    care_profile: dict[str, Any] | None = None,
    care_brief_hash: str | None = None,
) -> Household:
    """A real `Household`, unattached to any session.

    The real model rather than a stub on purpose: it is what makes these tests fail if
    `care_profile` or `care_brief_hash` is renamed out from under the write path.
    """
    return Household(
        id=uuid4(),
        username="pennytest",
        password_hash="x",
        name="The Alden family",
        care_recipient_name=care_recipient_name,
        timezone="Europe/London",
        session_version=1,
        care_profile=care_profile or {},
        care_brief_hash=care_brief_hash,
    )


def settled(profile: cp.CareProfile, household: Household) -> Household:
    """A household whose stored hash matches `profile` — i.e. one that has already extracted.

    Written by hand rather than by calling `write_profile` first, so a bug that stores the
    wrong hash cannot make the "nothing changed" tests pass by agreeing with itself.
    """
    household.care_profile = profile.model_dump(mode="json")
    household.care_brief_hash = cp.brief_hash(profile, fallback_name=household.care_recipient_name)
    return household


def sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def params(statement: Any) -> dict[str, Any]:
    return statement.compile(dialect=postgresql.dialect()).params


def established() -> cp.CareProfile:
    return cp.CareProfile(
        name="Juno Alden",
        aliases=["Mum", "Nan"],
        conditions=["Heart failure"],
    )


# --- the brief did not change: write it, charge nothing --------------------------------


async def test_a_profile_that_renders_the_same_brief_never_touches_the_cursor() -> None:
    """THE COST TEST. Re-saving the same answers must not re-read six months of chat.

    "Same" is decided by the rendered brief, not by the JSON: the aliases here arrive in a
    different order and one of them twice in two casings, which `CareProfile` normalises
    precisely so that a family re-confirming what they already told Penny is free.
    """
    household = settled(established(), make_household())
    session = FakeSession(rowcount=4_000)

    result = await cp.write_profile(
        session,  # type: ignore[arg-type]
        household,
        cp.CareProfile(
            name="Juno Alden", aliases=["Nan", "mum", "Mum"], conditions=["Heart failure"]
        ),
    )

    assert result == cp.ProfileWrite(brief_changed=False, messages_recalled=0)
    assert session.statements == [], "an unchanged brief must not issue a single statement"


async def test_an_unchanged_brief_still_stores_what_the_family_typed() -> None:
    """Costing nothing is not the same as doing nothing.

    The renderer strips `notes`, so an edit that only adds trailing space says exactly the same
    thing to the model and must still be stored as the family left it. An edit somebody watched
    themselves make and that quietly did not persist is the worst outcome an edit form has.
    """
    profile = established()
    profile.notes = "Prefers appointments in the morning"
    household = settled(profile, make_household())
    session = FakeSession()

    respaced = profile.model_copy(update={"notes": "Prefers appointments in the morning  "})
    result = await cp.write_profile(session, household, respaced)  # type: ignore[arg-type]

    assert result.brief_changed is False
    assert session.statements == []
    assert household.care_profile["notes"] == "Prefers appointments in the morning  "


# --- the brief changed: recall the history ---------------------------------------------


async def test_a_new_condition_clears_the_cursor_for_the_whole_household() -> None:
    """A sharpened filter is worth nothing if it only ever sees tomorrow's messages.

    The count comes back to the caller because it is what the family is told: "re-reading 412
    messages" is a number a progress bar can use, and it is also the size of the bill.
    """
    household = settled(established(), make_household())
    session = FakeSession(rowcount=412)

    sharper = established()
    sharper.conditions = [*sharper.conditions, "Osteoarthritis"]
    result = await cp.write_profile(session, household, sharper)  # type: ignore[arg-type]

    assert result == cp.ProfileWrite(brief_changed=True, messages_recalled=412)
    assert len(session.statements) == 1
    assert "UPDATE messages SET extracted_at=" in sql(session.statements[0])


async def test_learning_her_name_recalls_the_history_the_placeholder_filtered() -> None:
    """THE CASE THIS FUNCTION EXISTS FOR: import on day one, interview on day three.

    Everything stamped in between was judged by a brief whose care recipient was
    `households.care_recipient_name`, a placeholder nobody in the chat has ever typed. The
    interview's first write is what has to reach back for it.
    """
    household = make_household(
        care_brief_hash=cp.brief_hash(cp.CareProfile(), fallback_name=PLACEHOLDER)
    )
    session = FakeSession(rowcount=6_218)

    result = await cp.write_profile(session, household, cp.CareProfile(name="Juno Alden"))  # type: ignore[arg-type]

    assert result.brief_changed is True
    assert result.messages_recalled == 6_218
    assert household.care_brief_hash == cp.brief_hash(cp.CareProfile(name="Juno Alden"))


async def test_the_stored_hash_is_the_one_the_next_write_is_compared_against() -> None:
    """Two identical writes in a row must charge for the first and not the second.

    The failure this rules out is a clear that stores no hash: every subsequent save would then
    look like a change and re-read the history every time anyone touched the settings screen.
    """
    household = make_household()
    session = FakeSession(rowcount=90)

    first = await cp.write_profile(session, household, established())  # type: ignore[arg-type]
    second = await cp.write_profile(session, household, established())  # type: ignore[arg-type]

    assert (first.brief_changed, second.brief_changed) == (True, False)
    assert len(session.statements) == 1


# --- what the clear is allowed to touch -------------------------------------------------


async def test_the_cursor_clear_is_scoped_to_the_household_that_was_edited() -> None:
    """`household_id` is the tenant key on the write path too.

    Without the filter this one statement is every family's history, re-read and re-billed
    because one of them corrected a spelling. There is no row-level security underneath to
    catch it.
    """
    household = settled(established(), make_household())
    session = FakeSession(rowcount=1)

    await cp.write_profile(session, household, cp.CareProfile(name="Someone Else"))  # type: ignore[arg-type]

    statement = session.statements[0]
    assert "messages.household_id = " in sql(statement)
    assert household.id in params(statement).values()


async def test_the_first_write_cannot_recall_a_history_that_was_never_extracted() -> None:
    """A brand-new household has nothing behind the cursor, and must not pay to find that out.

    The guard is in the statement rather than in a special case for "no stored hash", because
    a NULL hash is ALSO what an imported-then-interviewed household has, and that one has six
    months waiting for exactly this clear. `extracted_at IS NOT NULL` separates them without
    the write path having to guess which kind of household it is looking at.
    """
    household = make_household()
    session = FakeSession(rowcount=0)

    result = await cp.write_profile(session, household, cp.CareProfile(name="Juno Alden"))  # type: ignore[arg-type]

    assert result == cp.ProfileWrite(brief_changed=True, messages_recalled=0)
    assert "messages.extracted_at IS NOT NULL" in sql(session.statements[0])


# --- through the settings screen --------------------------------------------------------


def ctx_for(household: Household) -> HouseholdCtx:
    return HouseholdCtx(id=household.id, household=household)


async def test_editing_only_the_timezone_never_clears_the_extraction_cursor() -> None:
    """THE SETTINGS-PAGE TEST, asserted from the handler rather than from the hash.

    `brief_hash` excludes the timezone because `render_brief` appends it after the hashed
    lines, and `test_care_profile` proves that about the hash. This proves it about the thing a
    family actually touches: the PATCH they send when they fix a typo in a dropdown. The two
    could come apart — a handler that decided to re-hash on every save, or that folded the
    timezone into the profile — and no test of the hash alone would notice.
    """
    household = settled(established(), make_household())
    stored_hash = household.care_brief_hash
    session = FakeSession(rowcount=5_000)

    await patch_household(HouseholdPatch(timezone="America/New_York"), ctx_for(household), session)  # type: ignore[arg-type]

    assert household.timezone == "America/New_York"
    assert household.care_brief_hash == stored_hash
    assert session.statements == [], "a timezone edit must not re-read a single message"


async def test_renaming_the_household_is_not_a_change_to_who_penny_is_caring_for() -> None:
    """`households.name` is the family's label for themselves and appears in no brief."""
    household = settled(established(), make_household())
    session = FakeSession(rowcount=5_000)

    await patch_household(HouseholdPatch(name="The Aldens"), ctx_for(household), session)  # type: ignore[arg-type]

    assert household.name == "The Aldens"
    assert session.statements == []


async def test_a_care_profile_edit_goes_through_the_write_path() -> None:
    """The wiring test: the handler must not store the profile by assigning it.

    An assignment would leave `care_brief_hash` stale, and stale-in-that-direction is the
    expensive silence — the filter sharpens, the stored hash still describes the old one, and
    every later save re-reads the history because it can never agree with itself again.
    """
    household = settled(established(), make_household())
    session = FakeSession(rowcount=311)

    sharper = established()
    sharper.medications = ["Ramipril 2.5mg"]
    await patch_household(HouseholdPatch(care_profile=sharper), ctx_for(household), session)  # type: ignore[arg-type]

    assert household.care_profile["medications"] == ["Ramipril 2.5mg"]
    assert household.care_brief_hash == cp.brief_hash(sharper, fallback_name=PLACEHOLDER)
    assert len(session.statements) == 1


async def test_correcting_the_placeholder_name_before_the_interview_recalls_the_history() -> None:
    """A rename is a filter change when the profile has no name of its own.

    Until the interview runs, `care_recipient_name` IS the care recipient line of the brief, so
    a family who signs in and replaces "your loved one" with "Mum" has just told Penny the one
    thing that resolves every "she" in the chat. The handler must hash AFTER the new name is on
    the row, or the change renders the old brief and reaches nothing.
    """
    empty = cp.CareProfile()
    household = settled(empty, make_household())
    session = FakeSession(rowcount=780)

    await patch_household(HouseholdPatch(care_recipient_name="Mum"), ctx_for(household), session)  # type: ignore[arg-type]

    assert household.care_recipient_name == "Mum"
    assert household.care_brief_hash == cp.brief_hash(empty, fallback_name="Mum")
    assert len(session.statements) == 1, "the rename changed the brief and must re-read"


async def test_a_rename_after_the_interview_costs_nothing() -> None:
    """The mirror image, and the reason the rename is not simply treated as a clear.

    Once the profile carries a name the fallback is never rendered, so editing it changes no
    instruction the model ever sees. Families do this — the placeholder sits in the settings
    field looking like it wants tidying up — and it must be free.
    """
    household = settled(established(), make_household())
    session = FakeSession(rowcount=5_000)

    await patch_household(HouseholdPatch(care_recipient_name="Mum"), ctx_for(household), session)  # type: ignore[arg-type]

    assert household.care_recipient_name == "Mum"
    assert session.statements == []


async def test_an_unreadable_stored_profile_is_not_a_500_on_the_settings_screen() -> None:
    """A rename with no profile in the request re-writes the profile through `load()`.

    `load()` treats a blob it cannot parse as absent, which is what extraction, the assistant
    and the feed already do with it — so normalising it here loses nothing that has ever
    reached a family, and the alternative is a settings screen that 500s forever for the one
    household with a bad row.
    """
    household = make_household(care_profile={"aliases": "not a list"})
    session = FakeSession(rowcount=0)

    await patch_household(HouseholdPatch(care_recipient_name="Mum"), ctx_for(household), session)  # type: ignore[arg-type]

    assert household.care_profile["aliases"] == []


@pytest.mark.parametrize(
    "patch",
    [
        HouseholdPatch(),
        HouseholdPatch(timezone="Europe/London"),
        HouseholdPatch(name="The Aldens"),
    ],
)
async def test_a_patch_that_omits_the_profile_leaves_the_stored_one_alone(
    patch: HouseholdPatch,
) -> None:
    """Omitted is not null. A partial save must never blank the interview's answers."""
    profile = established()
    household = settled(profile, make_household())
    session = FakeSession()

    await patch_household(patch, ctx_for(household), session)  # type: ignore[arg-type]

    assert household.care_profile == profile.model_dump(mode="json")


def test_the_patch_schema_accepts_the_profile_model_itself_not_a_copy_of_it() -> None:
    """Caps and list-cleaning are what keep the hash stable; a parallel schema would drift.

    The failure mode is specific: a profile saved through the API hashing differently from the
    identical one saved by the interview, so one of the two re-reads the history every time.
    """
    patch = HouseholdPatch.model_validate(
        {"care_profile": {"name": "Juno", "aliases": ["Nan", "  nan ", "Mum"]}}
    )
    assert patch.care_profile is not None
    assert isinstance(patch.care_profile, cp.CareProfile)
    assert patch.care_profile.aliases == ["Mum", "Nan"]


def test_a_typo_in_a_profile_field_is_a_422_and_not_a_silently_ignored_save() -> None:
    """`extra="forbid"` all the way down: a misspelled field must not read as a saved edit."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError, via the model
        HouseholdPatch.model_validate({"care_profile": {"name": "Juno", "condtions": ["x"]}})


def test_the_household_id_on_the_context_is_the_one_the_clear_is_scoped_to() -> None:
    """Belt and braces on tenancy: the ctx id and the row id are the same value, always.

    `write_profile` filters on `household.id` rather than on `ctx.id`, and the two being the
    same is a property of `require_household` rather than of anything in this file.
    """
    household = make_household()
    ctx = ctx_for(household)
    assert isinstance(ctx.id, UUID)
    assert ctx.id == ctx.household.id
