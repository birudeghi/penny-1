"""Prompt text, loaded once, with a version derived from its own bytes.

The version goes into `llm_runs.prompt_version`, so any row in the audit trail can be traced
to the exact prompt that produced it. Deriving it from a hash rather than a hand-bumped
constant means it cannot drift from the file — editing the prompt IS bumping the version.

`care_brief_hash` answers the same provenance question about the OTHER half of what a call is
made of. A call is instructions plus input, and only the instructions live in a file here; the
care brief is assembled per household and goes up as input. This module owns both hashes so
they share one convention — same algorithm, same 12-character truncation — because the two
columns are read side by side and a reader should not have to ask whether they are comparable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_HERE = Path(__file__).parent


def _load(name: str) -> tuple[str, str]:
    text = (_HERE / name).read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


EXTRACT_PROMPT, EXTRACT_PROMPT_VERSION = _load("extract.md")
MERGE_PROMPT, MERGE_PROMPT_VERSION = _load("merge.md")


def care_brief_hash(brief: str) -> str:
    """The version of the RELEVANCE FILTER a run ran under, for `llm_runs.care_brief_hash`.

    `prompt_version` covers the instructions file and nothing else, and the care brief is not
    in the instructions file — it is wrapped in `<care_brief>` and sent as call input. So two
    runs made under completely different briefs record the SAME `prompt_version` and are
    indistinguishable in the audit trail. Once the brief is a full relevance specification
    rather than one sentence naming the care recipient, it is the single biggest determinant of
    what got extracted, and "why did the run on the 14th miss the fall?" is unanswerable
    afterwards if the only recorded provenance is a prompt file that never changed.

    Hashed, never stored. The brief names a real person and what is wrong with them, and
    `llm_runs` is the table an operator reads casually while chasing a cost spike — the same
    reason `request_fingerprint` is a hash rather than the prompt.

    Stripped before hashing because every caller strips the brief before rendering it, so the
    bytes that actually reached the model are the stripped ones; a brief that gained a trailing
    newline in an editor is the same filter and must not read as a new one in the audit trail.
    """
    return hashlib.sha256(brief.strip().encode()).hexdigest()[:12]
