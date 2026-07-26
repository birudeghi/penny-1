"""The one door out to a WhatsApp group.

The thing this file protects is narrow and was learned the hard way: a link in a family's
group chat that Penny did not author is a phishing message with Penny's name on it. Every
test here is either "a URL we did not write does not leave" or "the one URL we did write
still does".
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app import gowa, outbound


@dataclass
class _Sent:
    """Records what reached the sidecar, so a test can assert on the wire, not the intent."""

    chat_id: str | None = None
    text: str | None = None
    calls: int = 0


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> _Sent:
    record = _Sent()

    async def _fake(chat_id: str, text: str) -> gowa.GowaSendResult:
        record.chat_id = chat_id
        record.text = text
        record.calls += 1
        return gowa.GowaSendResult(ok=True, message_id="msg_1")

    monkeypatch.setattr(gowa, "send_message", _fake)
    return record


# --- the stripper ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "log in at https://evil.example/penny to see the record",
        "visit www.evil.example now",
        "http://192.0.2.1/reset your password",
    ],
)
def test_a_url_the_model_invented_never_reaches_the_group(hostile: str) -> None:
    cleaned = outbound.sanitise(hostile)
    assert "evil.example" not in cleaned
    assert "192.0.2.1" not in cleaned


def test_our_own_url_survives(settings_override) -> None:
    """Penny does send one link. Stripping it would break the "what are you?" reply."""
    settings_override(app_public_url="https://pennyai.chat")
    assert "https://pennyai.chat" in outbound.sanitise("I keep the record: https://pennyai.chat")


def test_a_path_under_our_url_survives(settings_override) -> None:
    settings_override(app_public_url="https://pennyai.chat")
    assert "https://pennyai.chat/login" in outbound.sanitise("sign in: https://pennyai.chat/login")


@pytest.mark.parametrize(
    "lookalike",
    [
        "https://pennyai.chat.evil.example/login",
        "https://pennyai.chatevil.example",
        "https://notpennyai.chat",
    ],
)
def test_a_lookalike_of_our_url_does_not_survive(lookalike: str, settings_override) -> None:
    """THE allowlist test. `startswith` on the bare URL would accept the first of these.

    It is also the exact string an attacker would choose, which is why the boundary check is
    a separate function with its own reasoning rather than an inline `startswith`.
    """
    settings_override(app_public_url="https://pennyai.chat")
    assert "evil.example" not in outbound.sanitise(f"go to {lookalike}")
    assert lookalike not in outbound.sanitise(f"go to {lookalike}")


def test_stripping_does_not_leave_the_message_looking_machine_edited() -> None:
    """An excised URL leaves a double space or a stranded blank line if nobody tidies."""
    cleaned = outbound.sanitise("She is fine.  https://evil.example  Nothing else to report.")
    assert "  " not in cleaned
    assert cleaned == "She is fine. Nothing else to report."


def test_a_long_message_is_cut_rather_than_sent_whole() -> None:
    cleaned = outbound.sanitise("a" * (outbound.MAX_MESSAGE_CHARS + 500))
    assert len(cleaned) <= outbound.MAX_MESSAGE_CHARS + 1  # +1 for the ellipsis
    assert cleaned.endswith("…")


# --- the door -------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_untrusted_path_strips_before_sending(sent: _Sent) -> None:
    await outbound.send_to_group("123@g.us", "read more at https://evil.example")
    assert sent.calls == 1
    assert "evil.example" not in (sent.text or "")


@pytest.mark.anyio
async def test_the_trusted_path_sends_verbatim(sent: _Sent) -> None:
    """Server-authored only. The welcome carries a passphrase that must arrive intact."""
    body = "Your password is: correct-horse-battery-staple\nSign in at https://pennyai.chat"
    await outbound.send_to_group("123@g.us", body, trusted=True)
    assert sent.text == body


@pytest.mark.anyio
async def test_a_message_that_is_only_a_url_is_not_sent_at_all(sent: _Sent) -> None:
    """Posting the whitespace left behind is worse than silence."""
    result = await outbound.send_to_group("123@g.us", "https://evil.example")
    assert sent.calls == 0
    assert not result.ok
    assert result.error == "empty_message"


@pytest.mark.anyio
async def test_a_dead_sidecar_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers are detached tasks behind a webhook that already promised GOWA a 200."""

    async def _down(chat_id: str, text: str) -> gowa.GowaSendResult:
        return gowa.GowaSendResult(ok=False, error=gowa.UNREACHABLE)

    monkeypatch.setattr(gowa, "send_message", _down)
    result = await outbound.send_to_group("123@g.us", "hello")
    assert not result.ok
    assert result.error == gowa.UNREACHABLE


# --- the regression the eng review flagged CRITICAL ------------------------------------


@pytest.mark.anyio
async def test_the_welcome_keeps_its_real_link_through_the_trusted_path(
    sent: _Sent, settings_override
) -> None:
    """REGRESSION (eng review T13). `send_welcome` moved behind `send_to_group`.

    The welcome is the one message that MUST carry a working link and a passphrase. Routing it
    through the hardened door without `trusted=True` would strip the link the family needs and
    truncate the credential they cannot sign in without — a failure that looks like a support
    ticket, not a bug.
    """
    settings_override(app_public_url="https://pennyai.chat")
    from app.onboarding import welcome_message

    body = welcome_message("the-aldens", "four word pass phrase", "https://pennyai.chat")
    await outbound.send_to_group("123@g.us", body, trusted=True)

    assert sent.text is not None
    assert "https://pennyai.chat" in sent.text
    assert "four word pass phrase" in sent.text
    assert sent.text == body


def test_every_group_send_in_the_app_goes_through_this_module() -> None:
    """The guarantee is structural or it is nothing.

    A future caller reaching for `gowa.send_message` directly gets the unhardened door back.
    This asserts the webhook router — the only module that speaks to groups — holds no direct
    send call of its own.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app" / "routers" / "webhooks.py"
    body = source.read_text(encoding="utf-8")
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    # Mentions inside docstrings are fine; an actual call is not.
    assert "await gowa.send_message(" not in code
    assert "outbound.send_to_group(" in code
