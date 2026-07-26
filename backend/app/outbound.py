"""THE one door out to a WhatsApp group. Everything Penny says passes through `send_to_group`.

WHY THIS FILE EXISTS. There were two ways to speak. `send_welcome` posted server-authored
credentials straight to `gowa.send_message`; `send_reply` posted the assistant's answer after
`finish_reply` had stripped every URL the model emitted. Both were correct for what they sent
at the time, and the second one exists because of a real incident: a link in a family's group
chat that Penny did not author is a phishing message with Penny's name on it.

The onboarding interview is the first feature that sends MODEL-AUTHORED text on the welcome
path, and the welcome path has no stripping. Left alone, the next feature quietly walks
through the unhardened door — which is the same bug the security review closed, wearing a hat.

So the safety layer stops being something a caller remembers to apply and becomes a property
of sending. `trusted=False` is the default; getting the unsafe behaviour requires typing
`trusted=True` at the call site, where a reviewer can see it and ask why.

THE STRIPPER IS AN ALLOWLIST, NOT A BLANKET. Penny does send one URL — the app's own address,
in the "what are you?" reply and in the welcome. The old order of operations got this right by
accident: strip everything the model wrote, THEN append ours. That works only as long as every
future caller remembers to append last. An allowlist holds regardless of order: our own public
URL survives, every other URL does not, and a lookalike like `pennyai.chat.evil.com` is not
our URL and does not survive.

NOTHING HERE RAISES. Callers are detached background tasks spawned after a webhook has already
promised GOWA a 200. An exception escaping into that becomes a retry, and a retry becomes the
same message delivered to a family twice. `gowa.send_message` already returns a typed result
rather than raising; this preserves that all the way up.

NOT IN THIS FILE: rate limiting. It stays where it is, in front of the assistant call, because
it exists to stop a mention loop becoming an OpenAI bill — checking it here would mean paying
for a reply and then throwing it away. The limit guards the spend, not the speech.
"""

from __future__ import annotations

import logging
import re

from app import gowa
from app.config import get_settings

log = logging.getLogger(__name__)

# One WhatsApp message read on a phone, not a document. Deliberately a little above
# `assistant.MAX_REPLY_CHARS` (700): the assistant caps its own replies to a readable length,
# and this is the backstop for every other caller, so it should not silently re-trim text that
# a caller already shaped.
MAX_MESSAGE_CHARS = 1_000

# Scheme-ful and www URLs. It cannot catch a bare "evil.example" and is not trying to: it
# exists so the only link Penny ever sends is the one from our own settings.
_URL = re.compile(r"(https?://\S+|\bwww\.\S+)", re.IGNORECASE)


def _is_ours(url: str) -> bool:
    """True when this is the app's own public URL, and not merely something containing it.

    `startswith` on the bare public URL would accept `https://pennyai.chat.evil.com`, which is
    exactly the string an attacker would choose. The character after the match has to be a
    boundary — end of string, a path separator, or trailing punctuation the model wrote.
    """
    ours = get_settings().app_public_url.rstrip("/").rstrip()
    if not ours:
        return False
    candidate = url.rstrip(".,;:!?)")
    if candidate == ours:
        return True
    return candidate.startswith(f"{ours}/")


def sanitise(text: str) -> str:
    """Strip URLs we did not author, tidy what that leaves, and cap the length.

    Pure, so it is provable and testable without a network. The tidy-up is not cosmetic: an
    excised URL leaves a double space or a stranded blank line, and a message that reads like
    it was edited by a machine is a message a family trusts less.
    """
    body = _URL.sub(lambda m: m.group(0) if _is_ours(m.group(0)) else "", text or "")
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", body)).strip()
    if len(body) <= MAX_MESSAGE_CHARS:
        return body
    cut = body[:MAX_MESSAGE_CHARS].rstrip()
    return cut.rstrip(" ,;:") + "…"


async def send_to_group(
    chat_id: str,
    text: str,
    *,
    trusted: bool = False,
) -> gowa.GowaSendResult:
    """Send one message to a group. Never raises.

    `trusted=True` sends the text verbatim and is for SERVER-AUTHORED content only — today
    that is the welcome message, which carries a passphrase that must survive intact and would
    not survive a length cap. If you are reaching for it because a model wrote the text, the
    answer is no.

    An empty message after sanitising is not sent at all. A model that produced nothing but a
    URL has said nothing, and posting the whitespace it leaves behind is worse than silence.
    """
    body = text if trusted else sanitise(text)
    if not body.strip():
        log.info("outbound.empty_after_sanitise", extra={"chat_id": chat_id})
        return gowa.GowaSendResult(ok=False, error="empty_message")

    result = await gowa.send_message(chat_id, body)
    # Character count, never the text. The welcome carries a passphrase and a reply carries a
    # family's private business; neither belongs in a log line.
    log.info(
        "outbound.sent" if result.ok else "outbound.send_failed",
        extra={
            "chat_id": chat_id,
            "trusted": trusted,
            "char_count": len(body),
            "error": result.error,
        },
    )
    return result
