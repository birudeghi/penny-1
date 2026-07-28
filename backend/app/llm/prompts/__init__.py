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
REPORT_PROMPT, REPORT_PROMPT_VERSION = _load("report.md")
ASSISTANT_PROMPT, ASSISTANT_PROMPT_VERSION = _load("assistant.md")
INTERVIEW_PROMPT, INTERVIEW_PROMPT_VERSION = _load("interview.md")
