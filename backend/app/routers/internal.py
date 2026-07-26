"""`POST /api/internal/tick` — the clock the rest of the app does not have.

Every other route into extraction is driven by somebody doing something: a webhook delivery, an
upload. That is enough right up until it is not, and the two cases where it is not are the whole
reason this endpoint exists.

**A GROUP THAT GOES QUIET NEVER FINISHES EXTRACTING.** The cadence gate in
`app.extraction.service` lets a run through on `extract_min_unextracted` waiting messages OR an
oldest waiting message older than `extract_max_age_hours`, and both halves are only ever
evaluated when a caller arrives. On a chatty group the age trigger fires on the next inbound
message. On a group whose last thirty messages were about tomorrow's hospital transport and then
everyone went to bed, there IS no next message — so the age trigger has nothing to fire it, and
the conversation missing from the family's feed is the most recent one.

**A CARE PROFILE CHANGE CLEARS `extracted_at` FOR THE WHOLE HOUSEHOLD.** That is the mechanism
for "re-read all of it, now that you know who Margaret is", and what it produces is a backlog
nobody is watching. `extract_max_batch_messages` bounds each run, so draining it is a matter of
coming back — which is this endpoint, and nothing else.

**IT IS NOT A WORKER.** No queue, no scheduler, no second process, no thresholds of its own. It
is one POST that runs the SAME `run_extraction_for_household` a webhook runs, over the ids the
SAME `households_due_for_extraction` returns. If a number ever appears in this file that decides
whether a household is due, the tick and the live path have started disagreeing about what "due"
means, and the tick will either wake households to do nothing (paying ~1,400 tokens of fixed
prompt overhead per wake-up) or leave the quiet ones stranded, which is the bug it was built for.

THREE RULES, EACH OF WHICH IS A WAY THIS GOES WRONG SILENTLY.

1. **ONE HOUSEHOLD'S FAILURE ENDS THAT HOUSEHOLD AND NOTHING ELSE.** Every household gets its own
   session and its own transaction, and every one is wrapped. A timezone that will not load, a
   row a half-run migration left behind, an OpenAI outage: inside a shared transaction any of
   those rolls back the households that already succeeded, and inside a shared `try` it ends the
   run at whichever household happens to sort first — so a single bad row would quietly stop
   every other family's feed from updating, and the tick would keep answering 200.

2. **AN UNSET SECRET REFUSES EVERYTHING, AND A SUPPLIED ONE IS COMPARED IN CONSTANT TIME.** This
   door is on the public internet with no cookie in front of it, and behind it is an
   unauthenticated way to make this deployment spend money at OpenAI. `startup_checks` owns
   whether the configured secret is strong enough; this file owns whether the caller knows it.

3. **IT IS BOUNDED IN TIME.** `TICK_DEADLINE_SECONDS` stops the loop *starting* new households.
   Whatever is left is still due, still returned by the same query, and taken by the next tick —
   an hourly job that can run longer than an hour overlaps itself, and while the per-household
   advisory lock makes that safe it also makes it pointless.

The caller is the `tick` service in `infra/provision.py`: a pinned curl image whose start command
POSTs here on a loop. The schedule lives in that file — see there for why it is in the repository
rather than in a dashboard field.
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import UnauthorizedError
from app.extraction.service import (
    RunSummary,
    households_due_for_extraction,
    run_extraction_for_household,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["internal"])

# `docs/api-contract.md` spells the header; the cron service sends it verbatim. NOT `Authorization`,
# which would invite a proxy or a browser to helpfully cache or forward it.
TICK_SECRET_HEADER = "X-Penny-Tick-Secret"

# How long the loop may keep starting households. Ten minutes against an hourly schedule leaves
# an enormous margin — a tick over a handful of households is milliseconds plus whatever OpenAI
# takes — and its only job is to make the pathological case (a backlog across hundreds of
# households after an outage) end rather than run into the next tick. Nothing is lost by
# stopping: every household not reached is still due, and `households_remaining` says how many.
TICK_DEADLINE_SECONDS = 600.0


class TickResult(BaseModel):
    """What one tick did. Counts only — no household is named, and no message text exists here.

    `reports_generated` is in the contract and is always 0: the weekly report is specified in
    `docs/api-contract.md` and not built. Reported as a zero rather than omitted so the shape the
    cron service sees does not change on the day it lands.
    """

    households_scanned: int
    households_extracted: int
    households_failed: int
    households_remaining: int
    messages_extracted: int
    reports_generated: int


@router.post("/tick", response_model=TickResult)
async def tick(request: Request) -> TickResult:
    """Do the scheduled work. 401 for a wrong or missing secret, 200 with counts otherwise."""
    _authenticate(request.headers.get(TICK_SECRET_HEADER))
    return await run_tick()


def _authenticate(provided: str | None) -> None:
    """Constant-time comparison against `INTERNAL_TICK_SECRET`. Raises; never returns a bool.

    The same rule as `webhooks.verify_signature`, for the same reason: a function returning a
    bool gets called as `if verify(...)` instead of `if not verify(...)` exactly once, in a diff
    nobody reads twice, and the result is an open endpoint that passes every happy-path test.

    An unset or blank secret refuses EVERYTHING rather than waving callers through. In production
    `startup_checks` will not boot on a secret that is missing, short, or a placeholder published
    in `.env.example`; in dev the same misconfiguration means the tick does not run, which is a
    quiet feature outage and not an open door onto the OpenAI bill.

    `hmac.compare_digest` and not `==`: the comparison is against a caller-supplied string on an
    unauthenticated path, and `==` returns as soon as two bytes differ.
    """
    configured = (get_settings().internal_tick_secret or "").strip()
    if not configured:
        # Loud, because the effect is invisible: quiet households simply stop being drained.
        log.error("tick.secret_misconfigured")
        raise UnauthorizedError("Invalid tick secret.")
    if not provided or not hmac.compare_digest(configured.encode(), provided.encode()):
        log.warning("tick.secret_invalid")
        raise UnauthorizedError("Invalid tick secret.")


async def run_tick() -> TickResult:
    """Find the households the cadence gate would let through, and run each of them.

    Separate from the route on purpose: what is worth testing here is the loop's behaviour when
    one household explodes, and a test of that should not have to forge a header to reach it.

    THE WORK LIST IS READ IN ITS OWN SESSION, WHICH IS THEN CLOSED. Holding one connection open
    across the whole run would keep a transaction (and a snapshot) alive for as long as the
    slowest OpenAI call in the batch, on a pool of five. If that read itself fails there is
    nothing to loop over and the exception is allowed out: the cron's non-2xx is the only place
    "the tick could not run at all" is visible, and turning it into a cheerful `{"scanned": 0}`
    would make a database outage look like an idle hour.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        due = await households_due_for_extraction(session)

    deadline = time.monotonic() + TICK_DEADLINE_SECONDS
    extracted = failed = messages = remaining = 0

    for index, household_id in enumerate(due):
        if time.monotonic() >= deadline:
            left = len(due) - index
            remaining += left
            # A number an operator can act on: it is how you find out that one tick an hour has
            # stopped being enough, before families notice their feeds lagging.
            log.warning(
                "tick.deadline_reached",
                extra={"households_left": left, "deadline_seconds": TICK_DEADLINE_SECONDS},
            )
            break

        summary = await _extract_one(maker, household_id)
        if summary is None:
            failed += 1
            continue
        if summary.ran:
            extracted += 1
        # What got THROUGH, not what was looked at: a chunk whose call errored leaves its
        # messages `extracted_at IS NULL` for the next tick, and counting them here would report
        # a tick that achieved nothing as a tick that extracted hundreds.
        messages += summary.messages_considered - summary.messages_unextracted
        if summary.more_pending:
            # The batch cap cut this household short. Still due, taken next hour.
            remaining += 1

    log.info(
        "tick.done",
        extra={
            "households_scanned": len(due),
            "households_extracted": extracted,
            "households_failed": failed,
            "households_remaining": remaining,
            "messages_extracted": messages,
        },
    )
    return TickResult(
        households_scanned=len(due),
        households_extracted=extracted,
        households_failed=failed,
        households_remaining=remaining,
        messages_extracted=messages,
        reports_generated=0,
    )


async def _extract_one(
    maker: async_sessionmaker[AsyncSession], household_id: UUID
) -> RunSummary | None:
    """One household, in its own transaction. `None` means it failed and was skipped.

    THE ONLY PLACE IN THE TICK THAT SWALLOWS AN EXCEPTION, and it swallows every one of them.
    There is no client to fail and no work to abandon: the messages this household did not get
    through keep `extracted_at IS NULL`, which is the entire retry mechanism and needs no help
    from us — the next tick finds the household by the same query and tries again.

    The rollback before the re-raise is not decoration. A failed statement poisons the asyncpg
    transaction, and leaving it poisoned would make the NEXT household's session — borrowed from
    the same pool — fail with `InFailedSQLTransaction` several frames from anything that
    explains it. This is exactly the pattern `webhooks.extract_in_background` uses, for the same
    reason.
    """
    try:
        async with maker() as session:
            try:
                summary = await run_extraction_for_household(session, household_id)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
    except Exception:
        log.exception("tick.household_failed", extra={"household_id": str(household_id)})
        return None
    return summary
