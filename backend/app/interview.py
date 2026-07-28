"""The onboarding interview: Penny asks who she is caring for, and listens to the answer.

THE HOLE THIS FILLS. `welcome_message` has asked its questions since M6, and until now
nothing read the replies. `households.care_recipient_name` kept its provisioning placeholder,
`build_brief` rendered that placeholder into every extraction call, and the assistant — on the
mention path only — said *"it has been noted and will be used from now on"* about an answer
that was stored nowhere. A family who answered got a warm acknowledgement and a record filtered
against a stranger.

ONE PENNY, ONE CALL, WHILE THE INTERVIEW IS OPEN. Nobody @-mentions a bot to answer its own
question — they just reply — so the interview listens to ORDINARY messages in a linked group.
But the reverse also happens: someone @-mentions Penny mid-onboarding, to ask a question or
while answering one. Both land HERE, in one call, while `should_listen` says the interview is
open. The model answers what was asked, records what was offered, and says both in one message
(`kind="answered"` when there was a question and nothing new to record). Routing a mention to
the assistant during onboarding would mean answering from a profile the same message was in
the middle of establishing; splitting it across two calls would mean two replies or a
suppressed read-back — and the read-back is the family's only channel for correcting a
mis-parsed record. The webhook still falls back to the assistant if a mentioned turn produces
nothing (see `run_interview`), so a mention is never silently swallowed. Speaking unprompted
in a family's chat is the thing this product has already got wrong once, so it is fenced
accordingly (see THE GATES).

WRITE, THEN SAY IT BACK. The alternative — propose, wait for "yes", then write — needs a
pending state, a timeout, and an answer to "what if they never confirm". It also fails the
common case: a family who answers and goes quiet would leave the placeholder in place, which
is the exact failure this module exists to remove. So the record is written and read back in
the same breath, and a correction is just the next message. `write_profile` is hash-gated, so
a correction that changes nothing costs nothing.

That read-back is not a courtesy. A mis-parsed answer writes the filter every later reading of
this chat runs through, and it is the one error here that is invisible from the outside — the
feed simply comes back thin. Saying it in the group is what makes it correctable by the only
people who can tell it is wrong.

NOTHING RAISES, AND NONE MEANS SAY NOTHING. Same contract as `app.assistant`: no key, no
budget, a model that refused, a database that fell over — all of it comes out as None and the
group hears nothing. This module speaks unprompted in a family's chat, which is the thing this
product has already got wrong once, so every doubt resolves to silence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app import care_profile
from app.care_profile import CareProfile
from app.llm.context import event_lines, make_gateway, message_lines, truncate
from app.llm.gateway import CallSpec, LLMGateway, Purpose
from app.llm.prompts import INTERVIEW_PROMPT, INTERVIEW_PROMPT_VERSION

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Household

log = logging.getLogger(__name__)

INTERVIEW_PURPOSE: Purpose = "interview"

# One short WhatsApp message and a small structured record. The ceiling is for reasoning
# tokens, which bill at the output rate.
INTERVIEW_MAX_OUTPUT_TOKENS = 2_000
INTERVIEW_EFFORT = "low"

# HOW LONG PENNY KEEPS LISTENING FOR AN ANSWER, measured from when the household was created.
#
# It exists because the alternative is an LLM call on every message forever in a household that
# never answered. Two weeks is long enough for a family who meant to reply and got distracted,
# and short enough that a group which ignored the questions entirely stops costing anything.
# After it, the website's settings page is the way in — and the welcome message says so.
INTERVIEW_WINDOW = timedelta(days=14)

# Recent conversation the model reads to find an answer. Larger than the assistant's 20 because
# an answer to the background question often arrives split across several messages, and smaller
# than a chunk because this is a reply, not an extraction run.
RECENT_MESSAGES = 30
MESSAGE_EXCERPT_CHARS = 400

# Timeline events, so a question asked mid-interview ("@Penny when is mum's next appointment?")
# is answerable from the same call that captures answers. Same window as the assistant's.
RECENT_EVENTS = 20

# Same ceiling as an assistant reply: this is one message someone reads on a phone.
MAX_REPLY_CHARS = 700

# Below this there is nothing to read. "ok", "👍" and "thanks" are the three most common
# messages in any group and none of them answers a question about somebody's medication.
# Applies to UNMENTIONED messages only: "@Penny hi" is short and still an act of address,
# and an addressed message that earns silence reads as a broken bot.
MIN_ANSWER_CHARS = 8

InterviewKind = Literal["captured", "nothing_new", "correction", "declined", "answered"]


class InterviewResult(BaseModel):
    """What the model made of the conversation so far."""

    model_config = ConfigDict(extra="forbid")

    profile: CareProfile = Field(
        description="The complete record after this turn's additions, not just what changed."
    )
    kind: InterviewKind
    reply: str | None = Field(
        description=(
            "The message to post in the group, or null when kind is nothing_new. Never null "
            "when the latest message addressed Penny directly."
        )
    )
    still_missing: list[str] = Field(
        description="Short field names still worth asking for. Empty when there is nothing left."
    )


@dataclass(frozen=True, slots=True)
class InterviewOutcome:
    """What one interview turn did. `reply` is None when the group should hear nothing."""

    reply: str | None
    kind: InterviewKind
    brief_changed: bool = False
    messages_recalled: int = 0


def should_listen(household: Household) -> bool:
    """Is the interview still open for this household? Pure, and deliberately so.

    No session, no IO, no await: both gates are attribute reads on a row the caller already
    holds. That is what lets the webhook ask this question on the hot path, before scheduling
    anything, for every ordinary message in every linked group — the common case is an
    established household going about its day, and it must not cost a query to say no.
    """
    if care_profile.load(household).interview_closed:
        # Penny has stopped asking — either she has what she needs or the family said no.
        # Questions about the record from here are the assistant's job.
        #
        # NOT `is_established()`. Knowing her name is the START of the interview, not the end:
        # "no, it's June not Juno" and the answers to questions two and three all arrive after
        # the name does, and gating on a name would close the conversation while the family is
        # still having it.
        return False
    created = household.created_at
    if created is not None:
        age = datetime.now(UTC) - created.astimezone(UTC)
        if age > INTERVIEW_WINDOW:
            return False
    return True


async def capture_answer(
    session: AsyncSession,
    household: Household,
    message_text: str,
    *,
    mentioned: bool = False,
    gateway: LLMGateway | None = None,
) -> InterviewOutcome | None:
    """Read the group's recent conversation, record what it says, and say it back.

    Never raises. None means the group hears nothing, and None is the answer to every doubt —
    no key, no budget, a model that would not parse, a database that fell over mid-write.

    `mentioned` marks a message that explicitly @-addressed Penny. It relaxes the length gate
    ("@Penny hi" is short and still an address), tells the model to answer what was asked in
    the same reply that records what was offered, and turns "nothing to say" into None so the
    caller can fall back to the assistant — a mention must never be silently swallowed.

    `gateway` is a test seam and nothing else; the real caller passes nothing.
    """
    if not mentioned and len(message_text.strip()) < MIN_ANSWER_CHARS:
        return None
    try:
        if not should_listen(household):
            return None

        current = care_profile.load(household)
        recent = await message_lines(
            session,
            household.id,
            limit=RECENT_MESSAGES,
            excerpt_chars=MESSAGE_EXCERPT_CHARS,
            require_text=True,
        )
        now = datetime.now(UTC)
        events = await event_lines(
            session, household.id, ZoneInfo(household.timezone), now, limit=RECENT_EVENTS
        )
        gateway = gateway or make_gateway(session, household.id)
        result = await gateway.structured(
            CallSpec(
                purpose=INTERVIEW_PURPOSE,
                instructions=INTERVIEW_PROMPT,
                input=_render(current, recent, events, message_text, mentioned=mentioned),
                schema=InterviewResult,
                max_output_tokens=INTERVIEW_MAX_OUTPUT_TOKENS,
                reasoning_effort=INTERVIEW_EFFORT,
                household_id=household.id,
                prompt_version=INTERVIEW_PROMPT_VERSION,
                # The filter as it stood BEFORE this turn. A run is recorded under the brief it
                # read, not the one it produced — otherwise the row says the interview already
                # knew what it was in the middle of learning.
                care_brief_hash=care_profile.brief_hash(
                    current, fallback_name=household.care_recipient_name
                ),
            )
        )
        parsed = result.parsed

        if parsed.kind == "nothing_new":
            # For an addressed message this is a broken response — the prompt requires an
            # answer — and None is what sends the caller to the assistant fallback.
            return None if mentioned else InterviewOutcome(reply=None, kind=parsed.kind)

        # CLOSING IS THE SERVER'S CALL, NOT THE MODEL'S. `still_missing` is the model's opinion
        # about what is left to ask; whether that means stop asking is a product decision, and
        # leaving it to the model means one hallucinated empty list ends the interview for good.
        # `declined` closes it too: a family who said "we'd rather not" must not be asked again
        # by the next message that happens to be long enough.
        profile = parsed.profile.model_copy(
            update={
                "interview_closed": parsed.kind == "declined"
                or (not parsed.still_missing and parsed.profile.is_established())
            }
        )

        # THE WRITE AND THE READ-BACK ARE ONE STEP. Writing without saying it back would put an
        # unverifiable filter under months of extraction; saying it back without writing is
        # exactly the bug this module was built to fix.
        written = await care_profile.write_profile(session, household, profile)

        reply = _finish(parsed)
        if reply is None:
            # A kind that promised words and produced none is a broken response, not an
            # instruction to improvise. The record still stands; the group just hears nothing.
            log.info(
                "interview.recorded_without_reply",
                extra={"household_id": str(household.id), "kind": parsed.kind},
            )
        log.info(
            "interview.captured",
            extra={
                "household_id": str(household.id),
                "kind": parsed.kind,
                "brief_changed": written.brief_changed,
                "messages_recalled": written.messages_recalled,
                "still_missing": len(parsed.still_missing),
            },
        )
        return InterviewOutcome(
            reply=reply,
            kind=parsed.kind,
            brief_changed=written.brief_changed,
            messages_recalled=written.messages_recalled,
        )
    except Exception as exc:
        # Class only. The message text is a family talking about a relative's health.
        log.warning(
            "interview.failed",
            extra={"household_id": str(household.id), "error": type(exc).__name__},
        )
        return None


def _finish(parsed: InterviewResult) -> str | None:
    """Trim what the model returned to one message. Pure, so it is provable.

    No URL stripping here: `outbound.send_to_group` does that at the door, for every caller,
    and a second copy of that rule is a second thing to keep in sync with the first.
    """
    body = " ".join((parsed.reply or "").split("\n\n"))
    body = body.strip()
    if not body:
        return None
    if len(body) <= MAX_REPLY_CHARS:
        return body
    return truncate(body, MAX_REPLY_CHARS)


def _render(
    current: CareProfile,
    recent: tuple[str, ...],
    events: tuple[str, ...],
    message_text: str,
    *,
    mentioned: bool,
) -> str:
    """What the model is allowed to know: the record, the timeline, and what the group said.

    The current record goes in as JSON rather than as the rendered brief, because the model is
    being asked to RETURN one of these — showing it the prose form invites it to answer in
    prose. The timeline block is what makes a question asked mid-interview answerable; its
    lines carry event dates, never today's date, which the prompt still withholds on purpose
    (see "The date on each one" there). The `addressed_to_penny` attribute is how the model
    knows a reply is mandatory rather than optional. Nothing is fetched from anywhere else.
    """
    conversation = "\n".join(recent) or "(no earlier messages)"
    timeline = "\n".join(events) or "(the timeline is empty)"
    addressed = "true" if mentioned else "false"
    return (
        f"<record_so_far>\n{current.model_dump_json(indent=2, exclude_defaults=True)}\n"
        "</record_so_far>\n"
        f'<timeline events="{len(events)}">\n{timeline}\n</timeline>\n'
        f"<recent_messages>\n{conversation}\n</recent_messages>\n"
        f'<latest_message addressed_to_penny="{addressed}">\n{message_text.strip()}\n'
        "</latest_message>"
    )
