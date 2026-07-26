"""Household settings: rename, edit who Penny is caring for, change the passphrase, delete it all.

There is no users table. One household is one tenant, one shared credential and one care
recipient, so "account settings" and "household settings" are the same screen.

One field on that screen is unlike the others. The care profile is not a display preference,
it is the specification extraction filters by, so editing it can put a household's entire
message history back on the extraction cursor to be re-read against the sharper filter. That
decision is not made here — `app.care_profile.write_profile` owns it, and this module's job is
to make sure every edit that could change the brief goes through it.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from zoneinfo import available_timezones

from fastapi import APIRouter, Response, status

from app.care_profile import load, write_profile
from app.deps import CurrentHousehold, SessionDep
from app.errors import UnauthorizedError, ValidationError
from app.media import schedule_purge_after_delete
from app.schemas import (
    MIN_PASSWORD_CHARS,
    HouseholdOut,
    HouseholdPatch,
    PasswordChange,
    to_household,
)
from app.security import (
    clear_session_cookie,
    hash_password,
    set_session_cookie,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["household"])


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    """`available_timezones()` walks the tzdata tree; it is stable for the process lifetime."""
    return frozenset(available_timezones())


@router.patch("/household", response_model=HouseholdOut)
async def patch_household(
    patch: HouseholdPatch,
    ctx: CurrentHousehold,
    session: SessionDep,
) -> HouseholdOut:
    """All fields optional; send only what changed.

    Optional means `model_fields_set`, not "not None": a field the client omitted and a field
    it sent as null are different requests, and only the first one must leave the stored value
    alone. Reading `patch.timezone is not None` alone would make every partial save a full
    overwrite with defaults.
    """
    household = ctx.household
    provided = patch.model_fields_set

    if "timezone" in provided and patch.timezone is not None:
        # Checked HERE as well as in the model validator, because the model validator raises
        # ValueError, which surfaces as a 500. A typo'd timezone is a user mistake and has to
        # read like one. Rejecting at the write is the whole point: `ZoneInfo(bad)` only
        # explodes at render time, deep inside a request unrelated to the one that stored it.
        if patch.timezone not in _known_timezones():
            raise ValidationError(f"{patch.timezone!r} is not a known timezone.")
        household.timezone = patch.timezone
    if "name" in provided and patch.name is not None:
        household.name = patch.name

    renamed = "care_recipient_name" in provided and patch.care_recipient_name is not None
    if renamed:
        household.care_recipient_name = patch.care_recipient_name

    # THE ONLY EDIT ON THIS SCREEN THAT CAN COST MONEY, and the reason it is not three more
    # attribute assignments. `write_profile` compares the rendered brief against the stored
    # hash and clears `messages.extracted_at` only when the filter genuinely changed, so a
    # sharpened profile reaches the history it was meant to fix and an idle save does not
    # re-read six months of chat.
    #
    # The rename goes through it too. `care_recipient_name` is the brief's fallback name, so
    # for a household whose interview has not run it IS the filter, and correcting a
    # placeholder to "Mum" changes what counts as relevant. It runs AFTER the assignment above
    # for that reason: hashing first would render the old brief, match the stored hash, and
    # leave the history filtered by the placeholder.
    #
    # Neither `name` nor `timezone` is in this condition, and that is the point of the hash
    # living where it does — `render_brief` appends the timezone after the hashed lines, so a
    # family fixing a typo in their timezone never pays for a re-read. `test_care_profile_write`
    # asserts that from this handler rather than trusting the hash to keep excluding it.
    if renamed or ("care_profile" in provided and patch.care_profile is not None):
        # A rename with no profile in the request re-writes the stored profile through
        # `load()`. That round trip is lossless for anything Penny can read, and a blob it
        # cannot read is already treated as absent by extraction, the assistant and the feed —
        # so nothing that has ever reached a family is lost by normalising it here.
        profile = patch.care_profile if patch.care_profile is not None else load(household)
        await write_profile(session, household, profile)

    await session.flush()  # never commit: get_session owns the transaction
    return to_household(household)


@router.post("/household/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    ctx: CurrentHousehold,
    session: SessionDep,
) -> Response:
    """Change the shared passphrase. **Every other device is signed out.**

    This used to leave existing sessions alone, on the grounds that the cookie carries
    `household_id` and not the password. That made a password change useless as a remedy: the
    reason a family changes a shared passphrase is that someone has it who shouldn't, and a
    30-day cookie in that someone's browser outlived the change. Bumping `session_version`
    revokes every cookie minted before this moment.

    The device doing the change keeps working: its cookie is re-minted here at the new version.
    Signing out the person who just proved they know the current password would only teach
    families to avoid the button.
    """
    household = ctx.household
    if not verify_password(body.current_password, household.password_hash):
        raise UnauthorizedError("That password is not correct.")
    if len(body.new_password) < MIN_PASSWORD_CHARS:
        raise ValidationError(
            f"Your new password must be at least {MIN_PASSWORD_CHARS} characters."
        )
    household.password_hash = hash_password(body.new_password)
    household.session_version += 1
    await session.flush()  # never commit: get_session owns the transaction
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_session_cookie(response, household.id, household.session_version)
    return response


@router.post("/household/sign-out-everywhere", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_everywhere(ctx: CurrentHousehold, session: SessionDep) -> Response:
    """Revoke every session this household has, including the one making the request.

    The button for "I left it signed in on the hospital computer" / "Dad's phone was stolen".
    Unlike the password change this signs the caller out too — "everywhere" that quietly meant
    "everywhere except here" would be a lie about a security control, and re-logging in with a
    passphrase you already know costs one screen.

    The passphrase is unchanged, so anyone who knows it can sign back in. Changing it is the
    other button, and the UI says so.
    """
    household = ctx.household
    household.session_version += 1
    await session.flush()
    logger.info(
        "household.sessions_revoked",
        extra={"household_id": str(household.id), "session_version": household.session_version},
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.delete("/household", status_code=status.HTTP_204_NO_CONTENT)
async def delete_household(ctx: CurrentHousehold, session: SessionDep) -> Response:
    """Irreversible. Every message, event, member, import, report AND FILE goes with it.

    The cascade is `ON DELETE CASCADE` in Postgres, declared on each child's `household_id`,
    so nothing here has to enumerate the tables — a table added later is covered by its own
    foreign key rather than by remembering to edit this function.

    THE FILES ARE THE PART A FOREIGN KEY CANNOT REACH. A household's photographs live on a
    volume at `<media_root>/<household_id>/`, and a cascade that takes every row would have
    left every image of an identifiable person exactly where it was — unreachable, since each
    read path needs a session for a household that no longer exists, but present. That is not
    what "delete everything" means to the person clicking the button, and it is not what the
    law they may be exercising means either.

    The purge is scheduled rather than done here, and it WAITS FOR THIS TRANSACTION TO COMMIT
    before it removes anything: `get_session` commits after this handler returns, so deleting
    the files inline would mean a request that then failed had destroyed a living family's
    photographs. See `app.media.purge_after_delete`.
    """
    household_id = ctx.id
    await session.delete(ctx.household)
    await session.flush()
    schedule_purge_after_delete(household_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    # The session now points at a household that does not exist. Leaving the cookie in place
    # would give every subsequent request a 401 that looks exactly like a broken login.
    clear_session_cookie(response)
    return response
