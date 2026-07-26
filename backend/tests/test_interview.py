"""The onboarding interview: what Penny records, what she says back, and when she says nothing.

Two halves, and the second matters more. The first is that an answer reaches
`households.care_profile` at all — the hole this module was built to fill, where Penny said
"it has been noted" about something stored nowhere. The second is that she stays quiet the rest
of the time: this is the only code in the product that speaks in a family's group without being
addressed, and speaking wrongly is the failure that has already happened once here.

No database. The gateway is a recording fake and the household is a stub, so every one of these
runs in the default suite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app import care_profile, interview
from app.care_profile import CareProfile
from app.interview import InterviewResult, capture_answer, should_listen

HOUSEHOLD_ID = UUID("11111111-1111-1111-1111-111111111111")
PLACEHOLDER = "the person you're looking after"


@dataclass
class _Household:
    """Just enough of the row for the interview. Mutated by `write_profile`, as the real one is."""

    id: UUID = HOUSEHOLD_ID
    timezone: str = "Europe/London"
    care_recipient_name: str = PLACEHOLDER
    care_profile: dict[str, Any] = field(default_factory=dict)
    care_brief_hash: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class _Session:
    """Records the UPDATE that clears the cursor, and answers the two SELECTs the module makes."""

    messages: list[tuple[str, str]] = field(default_factory=list)
    statements: list[Any] = field(default_factory=list)
    committed: bool = False

    async def execute(self, statement: Any, *_a: Any, **_k: Any) -> Any:
        self.statements.append(statement)
        rows = [
            type("Row", (), {"sender_display_name": who, "text": text})()
            for who, text in self.messages
        ]
        session = self

        class _Result:
            def all(self) -> list[Any]:
                return rows

            def scalars(self) -> Any:
                class _S:
                    def all(self_inner) -> list[str]:
                        return [who for who, _ in session.messages]

                return _S()

            rowcount = 3

        return _Result()

    async def commit(self) -> None:
        self.committed = True


class _Gateway:
    """One canned `InterviewResult`, and a record of the CallSpec it was asked for."""

    def __init__(self, result: InterviewResult | Exception) -> None:
        self._result = result
        self.specs: list[Any] = []

    async def structured(self, spec: Any) -> Any:
        self.specs.append(spec)
        if isinstance(self._result, Exception):
            raise self._result
        return type("R", (), {"parsed": self._result, "run_id": uuid4()})()


def _result(
    *,
    profile: CareProfile | None = None,
    kind: str = "captured",
    reply: str | None = "Got it — Juno, 84, in Croydon.",
    still_missing: list[str] | None = None,
) -> InterviewResult:
    return InterviewResult(
        profile=profile or CareProfile(name="Juno Alden", aliases=["Mum"], age=84),
        kind=kind,  # type: ignore[arg-type]
        reply=reply,
        still_missing=still_missing or [],
    )


ANSWER = "It's for mum, Juno Alden. She's 84 and lives with me since the fall."


# --- the hole this fills -----------------------------------------------------------------


@pytest.mark.anyio
async def test_an_answer_in_the_group_reaches_the_care_profile() -> None:
    """THE bug. Penny asked three questions and said "noted" about something stored nowhere."""
    household, session = _Household(), _Session()
    outcome = await capture_answer(session, household, ANSWER, gateway=_Gateway(_result()))

    assert outcome is not None
    assert household.care_profile["name"] == "Juno Alden"
    assert household.care_profile["aliases"] == ["Mum"]
    assert care_profile.load(household).is_established()


@pytest.mark.anyio
async def test_what_was_recorded_is_said_back_in_the_group() -> None:
    """A mis-parse writes the filter under months of extraction and is invisible from outside.

    The read-back is the only thing that makes it correctable by the people who can tell.
    """
    outcome = await capture_answer(_Session(), _Household(), ANSWER, gateway=_Gateway(_result()))
    assert outcome is not None
    assert outcome.reply is not None
    assert "Juno" in outcome.reply


@pytest.mark.anyio
async def test_learning_her_name_recalls_the_history_the_placeholder_filtered() -> None:
    """The point of writing through `write_profile`: the sharpened filter reaches the past."""
    household, session = _Household(), _Session()
    outcome = await capture_answer(session, household, ANSWER, gateway=_Gateway(_result()))

    assert outcome is not None
    assert outcome.brief_changed
    assert household.care_brief_hash is not None


@pytest.mark.anyio
async def test_a_correction_replaces_what_was_recorded() -> None:
    """ "No, it's June not Juno" is the reason the read-back exists."""
    household = _Household(care_profile=CareProfile(name="Juno Alden").model_dump(mode="json"))
    corrected = _result(
        profile=CareProfile(name="June Alden"), kind="correction", reply="Sorry — June it is."
    )
    outcome = await capture_answer(
        _Session(), household, "no she's June not Juno", gateway=_Gateway(corrected)
    )

    assert outcome is not None
    assert household.care_profile["name"] == "June Alden"


# --- when Penny says nothing -------------------------------------------------------------


@pytest.mark.anyio
async def test_ordinary_family_chat_is_read_and_not_answered() -> None:
    """`nothing_new` is the common case. Penny reads quietly the rest of the time."""
    outcome = await capture_answer(
        _Session(),
        _Household(),
        "shall we do lunch on saturday instead",
        gateway=_Gateway(_result(kind="nothing_new", reply=None)),
    )
    assert outcome is not None
    assert outcome.reply is None


@pytest.mark.anyio
async def test_a_short_message_never_reaches_the_model() -> None:
    """ "ok", "thanks" and a thumbs-up are most of a group chat and answer nothing."""
    gateway = _Gateway(_result())
    for noise in ("ok", "👍", "thanks!", "  "):
        assert await capture_answer(_Session(), _Household(), noise, gateway=gateway) is None
    assert gateway.specs == []


@pytest.mark.anyio
async def test_a_closed_interview_is_never_reopened() -> None:
    """Once Penny has stopped asking, the record is the assistant's to answer questions about.

    Closed is NOT the same as established: knowing her name is the start of the interview,
    not the end, which is what the two tests below this one are about.
    """
    household = _Household(
        care_profile=CareProfile(name="Juno Alden", interview_closed=True).model_dump(mode="json")
    )
    gateway = _Gateway(_result())
    assert await capture_answer(_Session(), household, ANSWER, gateway=gateway) is None
    assert gateway.specs == []


@pytest.mark.anyio
async def test_the_interview_stops_asking_after_the_window() -> None:
    """Otherwise a household that never answered pays for an LLM call on every message forever."""
    stale = _Household(
        created_at=datetime.now(UTC) - interview.INTERVIEW_WINDOW - timedelta(days=1)
    )
    gateway = _Gateway(_result())
    assert await capture_answer(_Session(), stale, ANSWER, gateway=gateway) is None
    assert gateway.specs == []


@pytest.mark.anyio
async def test_a_failure_anywhere_degrades_to_silence_not_a_raise() -> None:
    """The caller is a detached task behind a webhook that already promised GOWA a 200."""
    boom = _Gateway(RuntimeError("the model fell over"))
    assert await capture_answer(_Session(), _Household(), ANSWER, gateway=boom) is None


@pytest.mark.anyio
async def test_a_declined_interview_is_acknowledged_once_and_recorded_as_nothing() -> None:
    """A family that says "not now" gets one reply, not a nag on every later message."""
    household = _Household()
    declined = _result(
        profile=CareProfile(), kind="declined", reply="No problem — I'll keep out of the way."
    )
    outcome = await capture_answer(
        _Session(), household, "we'd rather not go into that", gateway=_Gateway(declined)
    )
    assert outcome is not None
    assert outcome.kind == "declined"
    assert not care_profile.load(household).is_established()


# --- what the model is allowed to see -----------------------------------------------------


@pytest.mark.anyio
async def test_the_model_sees_the_record_so_far_so_it_can_carry_it_forward() -> None:
    """Returning a complete profile is only safe if the model was shown the incomplete one."""
    household = _Household(
        care_profile=CareProfile(name="Juno", conditions=["Heart failure"]).model_dump(mode="json")
    )
    gateway = _Gateway(_result())
    await capture_answer(_Session(), household, ANSWER, gateway=gateway)

    (spec,) = gateway.specs
    assert "Heart failure" in spec.input
    assert "<record_so_far>" in spec.input


@pytest.mark.anyio
async def test_the_conversation_is_sender_attributed() -> None:
    """ "she's 84" and "that's my mum not his" is a correction only if you can see two people."""
    session = _Session(messages=[("Liz", "she's 84 next month"), ("Owen", "that's my mum")])
    gateway = _Gateway(_result())
    await capture_answer(session, _Household(), ANSWER, gateway=gateway)

    (spec,) = gateway.specs
    assert "Liz: she's 84 next month" in spec.input
    assert "Owen: that's my mum" in spec.input


@pytest.mark.anyio
async def test_the_run_is_recorded_under_the_filter_it_read_not_the_one_it_wrote() -> None:
    """Otherwise the audit row claims the interview already knew what it was learning."""
    household = _Household()
    before = care_profile.brief_hash(CareProfile(), fallback_name=PLACEHOLDER)
    gateway = _Gateway(_result())
    await capture_answer(_Session(), household, ANSWER, gateway=gateway)

    (spec,) = gateway.specs
    assert spec.care_brief_hash == before
    assert household.care_brief_hash != before  # the write moved it on


@pytest.mark.anyio
async def test_every_turn_lands_in_the_cost_audit() -> None:
    """It spends from the same $15/month pot as extraction and the assistant."""
    gateway = _Gateway(_result())
    await capture_answer(_Session(), _Household(), ANSWER, gateway=gateway)

    (spec,) = gateway.specs
    assert spec.purpose == "interview"
    assert spec.household_id == HOUSEHOLD_ID
    assert spec.prompt_version == interview.INTERVIEW_PROMPT_VERSION
    assert spec.max_output_tokens <= 2_000


# --- the reply itself ---------------------------------------------------------------------


def test_a_long_read_back_is_cut_to_one_message() -> None:
    long = _result(reply="x" * (interview.MAX_REPLY_CHARS + 400))
    finished = interview._finish(long)
    assert finished is not None
    assert len(finished) <= interview.MAX_REPLY_CHARS + 1
    assert finished.endswith("…")


def test_a_kind_that_promised_words_and_produced_none_is_silence() -> None:
    """A broken response is not an instruction to improvise."""
    assert interview._finish(_result(reply="   ")) is None


def test_the_interview_does_not_strip_urls_itself() -> None:
    """`outbound.send_to_group` does that at the door, for every caller.

    A second copy of the rule here is a second thing to keep in sync with the first, and the
    one that drifts is the one nobody is looking at.
    """
    assert "evil.example" in (interview._finish(_result(reply="see https://evil.example")) or "")


def test_should_listen_is_pure_so_the_webhook_can_ask_on_the_hot_path() -> None:
    """Every ordinary message in every linked group asks this. It must not cost a query."""
    assert should_listen(_Household())
    assert not should_listen(
        _Household(
            care_profile=CareProfile(name="Juno", interview_closed=True).model_dump(mode="json")
        )
    )


def test_knowing_her_name_does_not_close_the_interview() -> None:
    """THE bug this gate replaced: corrections and answers two and three all arrive later."""
    assert should_listen(
        _Household(care_profile=CareProfile(name="Juno Alden").model_dump(mode="json"))
    )


def test_the_prompt_still_forbids_the_things_the_record_depends_on() -> None:
    """The rules that keep a guess out of a clinical field are prompt text, so assert on it."""
    prompt = interview.INTERVIEW_PROMPT.lower()
    assert "only what they said" in prompt
    assert "not the person typing" in prompt
    assert "do not diagnose" in prompt
    assert "carry forward" in prompt


def test_the_result_schema_is_strict_so_an_invented_field_fails_loudly() -> None:
    with pytest.raises(ValidationError):
        InterviewResult.model_validate(
            json.loads('{"profile":{},"kind":"captured","reply":"hi","still_missing":[],"x":1}')
        )
