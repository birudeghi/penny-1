"""Who answers one message: the routing that decides Penny's one visible reply.

`_route_reply` is the single owner-picker — mention vs interview vs silence — and this file
is its contract. The cases that matter most are the ones that went wrong before it existed:
a mention during onboarding earning silence (the interview used to own every message), an
emergency phrased at Penny going to a language model instead of the fixed words, and one
message earning two replies.

No database and no network. The household is a stub `should_listen` can read, the sidecar
lookup and background plumbing are monkeypatched, and both rate-limit ledgers are cleared
around every test.

    _route_reply
      ├─ mention + interview open ──── spawn_interview(mentioned=True)   [one owner]
      ├─ mention + interview closed ── spawn_reply
      ├─ mention + no text ─────────── silence (photo @-pick; caption dropped at ingest)
      ├─ mention + budget spent ────── silence, and NO interview fallback
      ├─ no mention + open + long ──── spawn_interview(mentioned=False)
      └─ no mention otherwise ──────── silence
    run_interview(mentioned=True)
      ├─ emergency text ── EMERGENCY_REPLY, no model call
      ├─ capture → None ── send_reply fallback (a mention is never swallowed)
      └─ capture → reply ─ ONE outbound send, no fallback
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app import gowa
from app.routers import webhooks

HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
CHAT_ID = "12036302@g.us"
OWN_JID = "17473209317@s.whatsapp.net"


@dataclass
class _Household:
    """Just enough of the row for `should_listen`: the profile dict and an age."""

    id: UUID = HOUSEHOLD_ID
    timezone: str = "Europe/London"
    care_recipient_name: str = "the person you're looking after"
    care_profile: dict[str, Any] = field(default_factory=dict)
    care_brief_hash: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _closed_household() -> _Household:
    return _Household(care_profile={"interview_closed": True})


class _Background:
    """Records what `_route_reply` scheduled instead of running it."""

    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, fn: Any, *args: Any) -> None:
        self.tasks.append((fn, args))


@pytest.fixture(autouse=True)
def _clean_budgets() -> Any:
    for ledger in webhooks._reply_hits.values():
        ledger.clear()
    yield
    for ledger in webhooks._reply_hits.values():
        ledger.clear()


@pytest.fixture
def household(monkeypatch: pytest.MonkeyPatch) -> _Household:
    home = _Household()

    async def _fake_household(_household_id: UUID) -> _Household:
        return home

    async def _fake_jid() -> str:
        return OWN_JID

    monkeypatch.setattr(webhooks, "_session_household", _fake_household)
    monkeypatch.setattr(webhooks, "resolve_own_jid", _fake_jid)
    return home


async def _route(
    background: _Background,
    text: str | None,
    payload: dict[str, Any] | None = None,
) -> str:
    return await webhooks._route_reply(
        background,  # type: ignore[arg-type]
        household_id=HOUSEHOLD_ID,
        chat_id=CHAT_ID,
        provider_message_id="wamid.1",
        text=text,
        raw_payload=payload or {},
    )


# --- who owns the message ----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_mention_during_the_open_interview_goes_to_the_interview_as_a_mention(
    household: _Household,
) -> None:
    """THE BUG THIS BRANCH FIXES: onboarding used to swallow mentions into silence."""
    background = _Background()
    reason = await _route(background, "@Penny what did the GP say yesterday?")
    assert reason == "interview_mention"
    ((fn, args),) = background.tasks
    assert fn is webhooks.spawn_interview
    assert args[-1] is True  # mentioned


@pytest.mark.anyio
async def test_a_mention_after_the_interview_closes_goes_to_the_assistant(
    household: _Household, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = _closed_household()

    async def _fake_household(_household_id: UUID) -> _Household:
        return closed

    monkeypatch.setattr(webhooks, "_session_household", _fake_household)
    background = _Background()
    reason = await _route(background, "@Penny when is the next appointment?")
    assert reason == "scheduled"
    ((fn, _args),) = background.tasks
    assert fn is webhooks.spawn_reply


@pytest.mark.anyio
async def test_an_unmentioned_answer_still_reaches_the_open_interview(
    household: _Household,
) -> None:
    """Nobody @-mentions a bot to answer its own question — the original interview premise."""
    background = _Background()
    reason = await _route(background, "It's for mum, Juno. She's 84.")
    assert reason == "interview"
    ((fn, args),) = background.tasks
    assert fn is webhooks.spawn_interview
    assert args[-1] is False  # not mentioned


@pytest.mark.anyio
async def test_a_short_unmentioned_message_is_nobodys(household: _Household) -> None:
    background = _Background()
    assert await _route(background, "ok") == "not_mentioned"
    assert background.tasks == []


@pytest.mark.anyio
async def test_a_short_mention_is_still_an_address_and_still_answered(
    household: _Household,
) -> None:
    """ "@Penny hi" is under MIN_ANSWER_CHARS once stripped; an address never earns silence."""
    background = _Background()
    reason = await _route(background, "@Penny hi")
    assert reason == "interview_mention"
    assert len(background.tasks) == 1


@pytest.mark.anyio
async def test_a_payload_only_mention_with_no_text_stays_silent(
    household: _Household,
) -> None:
    """A photo with Penny picked from the @-list: the caption was dropped at ingest, so there
    is no question to answer — pinned to silence until answer-after-derivation exists
    (TODOS.md)."""
    background = _Background()
    payload = {"mentioned_jid": [OWN_JID]}
    assert await _route(background, None, payload) == "not_mentioned"
    assert background.tasks == []


@pytest.mark.anyio
async def test_a_message_with_no_mention_and_a_closed_interview_is_silence(
    household: _Household, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = _closed_household()

    async def _fake_household(_household_id: UUID) -> _Household:
        return closed

    monkeypatch.setattr(webhooks, "_session_household", _fake_household)
    background = _Background()
    assert await _route(background, "she had a rough night again") == "not_mentioned"
    assert background.tasks == []


# --- the two budgets ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_interview_turns_cannot_starve_a_mention_of_its_answer(
    household: _Household,
) -> None:
    """The coupling the split exists to remove: onboarding in full flow used to spend the
    same slots a mention needed."""
    background = _Background()
    for _ in range(webhooks.INTERVIEW_REPLIES_PER_HOUSEHOLD):
        assert await _route(background, "another answer about mum's medication") == "interview"
    assert await _route(background, "one more unprompted answer here") == "rate_limited"
    assert await _route(background, "@Penny are you still there?") == "interview_mention"


@pytest.mark.anyio
async def test_the_mention_budget_has_a_ceiling_and_refuses_silently(
    household: _Household,
) -> None:
    background = _Background()
    for _ in range(webhooks.MENTION_REPLIES_PER_HOUSEHOLD):
        assert await _route(background, "@Penny hello again") == "interview_mention"
    before = len(background.tasks)
    assert await _route(background, "@Penny hello once more") == "rate_limited"
    # Refused means refused: no interview fallback for a rate-limited mention — "we already
    # decided not to speak" must not become a second chance to speak.
    assert len(background.tasks) == before


@pytest.mark.anyio
async def test_a_rate_limited_mention_does_not_leak_into_the_interview_path(
    household: _Household,
) -> None:
    background = _Background()
    for _ in range(webhooks.MENTION_REPLIES_PER_HOUSEHOLD):
        await _route(background, "@Penny hi")
    background.tasks.clear()
    # Long enough to qualify for the interview were it unmentioned; the mention verdict owns it.
    assert await _route(background, "@Penny it's mum, Juno, she's 84 and has arthritis") == (
        "rate_limited"
    )
    assert background.tasks == []


# --- one visible reply per mentioned interview turn --------------------------------------


def _sent(ok: bool = True) -> gowa.GowaSendResult:
    return gowa.GowaSendResult(ok=ok, error=None if ok else "boom")


class _Sends:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    async def __call__(self, chat_id: str, text: str, *, trusted: bool = False) -> Any:
        self.calls.append((chat_id, text, trusted))
        return _sent()


@pytest.fixture
def interview_turn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire `run_interview` to fakes: committed message, one household, recorded sends."""
    seen: dict[str, Any] = {"sends": _Sends(), "fallbacks": [], "captures": []}

    async def _committed(_household_id: UUID, _pmid: str) -> UUID:
        return uuid4()

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, _model: Any, _pk: Any) -> _Household:
            return _Household()

        async def commit(self) -> None:
            seen["committed"] = True

    async def _fallback(household_id: UUID, chat_id: str, pmid: str, text: str) -> None:
        seen["fallbacks"].append(text)

    monkeypatch.setattr(webhooks, "_await_commit", _committed)
    monkeypatch.setattr(webhooks, "get_sessionmaker", lambda: lambda: _Session())
    monkeypatch.setattr(webhooks.outbound, "send_to_group", seen["sends"])
    monkeypatch.setattr(webhooks, "send_reply", _fallback)
    return seen


@pytest.mark.anyio
async def test_an_emergency_mention_gets_the_fixed_words_and_no_model(
    interview_turn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2: "@Penny mum collapsed" during onboarding used to head for a language model."""

    async def _never(*_a: Any, **_k: Any) -> None:
        raise AssertionError("capture_answer must not run for an emergency")

    monkeypatch.setattr(webhooks, "capture_answer", _never)
    await webhooks.run_interview(HOUSEHOLD_ID, CHAT_ID, "wamid.1", "@Penny mum collapsed", True)
    ((_chat, text, trusted),) = interview_turn["sends"].calls
    assert text == webhooks.EMERGENCY_REPLY
    assert trusted is False
    assert interview_turn["fallbacks"] == []


@pytest.mark.anyio
async def test_an_unmentioned_turn_never_triggers_the_emergency_words(
    interview_turn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "she collapsed last month" as an ANSWER is history, not a 999 moment; the fixed words
    are reserved for messages addressed to Penny, same as the assistant path."""

    async def _quiet(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(webhooks, "capture_answer", _quiet)
    await webhooks.run_interview(HOUSEHOLD_ID, CHAT_ID, "wamid.1", "she collapsed at home", False)
    assert interview_turn["sends"].calls == []
    assert interview_turn["fallbacks"] == []


@pytest.mark.anyio
async def test_a_mentioned_turn_the_capture_declines_falls_back_to_the_assistant(
    interview_turn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mention is never swallowed: capture says nothing → the assistant answers instead."""

    async def _nothing(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(webhooks, "capture_answer", _nothing)
    await webhooks.run_interview(HOUSEHOLD_ID, CHAT_ID, "wamid.1", "@Penny who are you?", True)
    assert interview_turn["sends"].calls == []
    assert interview_turn["fallbacks"] == ["@Penny who are you?"]


@pytest.mark.anyio
async def test_a_mentioned_turn_with_a_reply_sends_exactly_one_message(
    interview_turn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION for the (f1) rule: a family who @-mentions Penny while answering her
    question gets ONE reply, not an interview read-back plus an assistant answer."""
    from app.interview import InterviewOutcome

    async def _captured(*_a: Any, **_k: Any) -> InterviewOutcome:
        return InterviewOutcome(reply="Got it — Juno, 84. And her GP?", kind="captured")

    monkeypatch.setattr(webhooks, "capture_answer", _captured)
    await webhooks.run_interview(
        HOUSEHOLD_ID, CHAT_ID, "wamid.1", "@Penny it's mum, Juno, she's 84", True
    )
    assert len(interview_turn["sends"].calls) == 1
    assert interview_turn["fallbacks"] == []
    assert interview_turn["committed"] is True


@pytest.mark.anyio
async def test_an_unmentioned_quiet_turn_stays_quiet_with_no_fallback(
    interview_turn: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _nothing(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(webhooks, "capture_answer", _nothing)
    await webhooks.run_interview(HOUSEHOLD_ID, CHAT_ID, "wamid.1", "morning all", False)
    assert interview_turn["sends"].calls == []
    assert interview_turn["fallbacks"] == []


# --- the two-send welcome ----------------------------------------------------------------


@pytest.fixture
def welcome(monkeypatch: pytest.MonkeyPatch) -> _Sends:
    """`send_welcome` with the burst gate open and the household committed."""
    sends = _Sends()

    async def _there(_household_id: UUID) -> UUID:
        return uuid4()

    async def _alone(_household_id: UUID, _chat_id: str) -> bool:
        return True

    monkeypatch.setattr(webhooks, "_await_household", _there)
    monkeypatch.setattr(webhooks, "_was_a_solitary_join", _alone)
    monkeypatch.setattr(webhooks.outbound, "send_to_group", sends)
    return sends


@pytest.mark.anyio
async def test_the_welcome_is_two_sends_credentials_first_and_trusted(
    welcome: _Sends,
) -> None:
    await webhooks.send_welcome(HOUSEHOLD_ID, CHAT_ID, "cedar-thistle", "briar mole atlas kite")
    first, second = welcome.calls
    # REGRESSION: the passphrase must ride the trusted path — it would not survive sanitise().
    assert first[2] is True
    assert "briar mole atlas kite" in first[1]
    assert second[2] is False
    assert "who are you looking after" in second[1]
    assert "briar mole atlas kite" not in second[1]


@pytest.mark.anyio
async def test_a_failed_credentials_send_asks_no_question(
    welcome: _Sends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Question-without-credentials is a stranger interrogating a group chat."""

    async def _failing(chat_id: str, text: str, *, trusted: bool = False) -> Any:
        welcome.calls.append((chat_id, text, trusted))
        return _sent(ok=False)

    monkeypatch.setattr(webhooks.outbound, "send_to_group", _failing)
    await webhooks.send_welcome(HOUSEHOLD_ID, CHAT_ID, "cedar-thistle", "briar mole atlas kite")
    assert len(welcome.calls) == 1


@pytest.mark.anyio
async def test_the_burst_window_gates_both_sends(
    welcome: _Sends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: the last gate before speaking holds the whole welcome, question included —
    a burst-suppressed join must produce zero messages, not a stray question."""

    async def _not_alone(_household_id: UUID, _chat_id: str) -> bool:
        return False

    monkeypatch.setattr(webhooks, "_was_a_solitary_join", _not_alone)
    await webhooks.send_welcome(HOUSEHOLD_ID, CHAT_ID, "cedar-thistle", "briar mole atlas kite")
    assert welcome.calls == []
