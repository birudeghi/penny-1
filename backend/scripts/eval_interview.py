"""Pre-ship eval for the interview prompt: pacing, no re-asking, mention answering, closing.

    uv run python -m scripts.eval_interview            # live model, ~15 calls, cents
    uv run python -m scripts.eval_interview --verbose  # …and print every reply

RUN THIS BEFORE SHIPPING ANY CHANGE TO `interview.md`. It is deliberately NOT in CI: live
model calls are nondeterministic and need a key, and a canned replay of structured output
tests JSON plumbing, not the prompt. This is the same trade `eval_extraction` makes — a
script a human runs on purpose, with thresholds written before the run.

WHAT IS BEING MEASURED — the behaviours unit tests cannot reach, because they mock the model:

* ONE question per turn, driven by what is still missing, never a re-ask of what the record
  already holds. This is the welcome-split contract: `welcome_question_message` asks the
  first question and promises the interview paces the rest.
* An addressed message (`addressed_to_penny="true"`) always gets a reply — answered from the
  record and timeline, never invented.
* `nothing_new` for ordinary chat, `declined` acknowledged once, corrections replace.
* The four clinical fields never gain an invented `confirmed_at` just because the timeline
  block now shows the model some dates.

THE THRESHOLD IS FIXED BEFORE THE RUN: at most 1 of the cases may fail. A model having an
off day and a broken prompt look the same in a single case; they do not look the same in
fourteen.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.care_profile import CareProfile
from app.interview import (
    INTERVIEW_EFFORT,
    INTERVIEW_MAX_OUTPUT_TOKENS,
    INTERVIEW_PROMPT_VERSION,
    INTERVIEW_PURPOSE,
    InterviewResult,
    _render,
)
from app.llm.gateway import CallSpec, LLMGateway
from app.llm.prompts import INTERVIEW_PROMPT

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_FAILURES = 1

Check = tuple[str, Callable[[InterviewResult], bool]]


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    profile: CareProfile
    latest: str
    mentioned: bool = False
    recent: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    checks: Sequence[Check] = ()


def _reply(result: InterviewResult) -> str:
    return (result.reply or "").lower()


def one_question_at_most(result: InterviewResult) -> bool:
    return _reply(result).count("?") <= 1


def has_reply(result: InterviewResult) -> bool:
    return bool((result.reply or "").strip())


def no_reply(result: InterviewResult) -> bool:
    return not (result.reply or "").strip()


def kind_in(*kinds: str) -> Check:
    return (f"kind in {kinds}", lambda r: r.kind in kinds)


def reply_mentions(*needles: str) -> Check:
    return (
        f"reply mentions one of {needles}",
        lambda r: any(needle in _reply(r) for needle in needles),
    )


def reply_avoids(*needles: str) -> Check:
    return (
        f"reply avoids {needles}",
        lambda r: not any(needle in _reply(r) for needle in needles),
    )


ESTABLISHED = CareProfile(
    name="Juno Alden",
    aliases=["Mum"],
    age=84,
    place="lives with her daughter in Croydon",
    conditions=["heart failure", "arthritis"],
)

CASES: tuple[Case, ...] = (
    Case(
        name="first answer gets one follow-up, not a re-ask",
        profile=CareProfile(),
        latest="It's for mum, Juno Alden. She's 84.",
        checks=(
            kind_in("captured"),
            ("one question at most", one_question_at_most),
            ("reply exists", has_reply),
            reply_avoids("how old", "her name", "what's her name", "what is her name"),
        ),
    ),
    Case(
        name="second answer moves on to what is still missing",
        profile=CareProfile(name="Juno Alden", aliases=["Mum"], age=84),
        recent=("Liz: it's for mum, Juno Alden, she's 84",),
        latest="She lives with me in Croydon, has done since the fall last year.",
        checks=(
            kind_in("captured"),
            ("one question at most", one_question_at_most),
            reply_avoids("how old", "her name", "where does she live"),
        ),
    ),
    Case(
        name="a complete record stops asking",
        profile=ESTABLISHED,
        latest="Her GP is Dr Aziz at the Brookfield surgery, that's everything really.",
        checks=(
            kind_in("captured"),
            ("nothing still missing", lambda r: r.still_missing == []),
            ("no question left", lambda r: "?" not in _reply(r)),
        ),
    ),
    Case(
        name="ordinary chat is silence",
        profile=CareProfile(name="Juno Alden", age=84),
        latest="shall we do lunch on saturday instead of sunday?",
        checks=(kind_in("nothing_new"), ("reply is null", no_reply)),
    ),
    Case(
        name="logistics between family members is silence",
        profile=CareProfile(name="Juno Alden", age=84),
        latest="I can take the early shift tomorrow if someone swaps me thursday",
        checks=(kind_in("nothing_new"), ("reply is null", no_reply)),
    ),
    Case(
        name="a decline is acknowledged once",
        profile=CareProfile(),
        latest="please stop asking questions, we'd rather not do this in the chat",
        checks=(kind_in("declined"), ("reply exists", has_reply)),
    ),
    Case(
        name="a correction replaces",
        profile=CareProfile(name="Juno Alden", aliases=["Mum"], age=84),
        recent=("Penny: Got it — Juno, 84.",),
        latest="It's June actually, not Juno.",
        checks=(
            kind_in("correction", "captured"),
            ("name is June", lambda r: (r.profile.name or "").lower().startswith("june")),
            ("reply exists", has_reply),
        ),
    ),
    Case(
        name="an addressed question is answered from the timeline",
        profile=ESTABLISHED,
        events=("2026-08-02 appointment: Cardiology, Salford Royal (upcoming)",),
        latest="@Penny when is mum's next appointment?",
        mentioned=True,
        checks=(
            kind_in("answered", "captured"),
            ("reply exists", has_reply),
            reply_mentions("2 august", "august", "02", "cardiology", "salford"),
        ),
    ),
    Case(
        name="an addressed question outside the record says so, invents nothing",
        profile=CareProfile(name="Juno Alden"),
        latest="@Penny what's her NHS number?",
        mentioned=True,
        checks=(
            ("reply exists", has_reply),
            (
                "no digits invented",
                lambda r: not any(ch.isdigit() for ch in (r.reply or "")),
            ),
            ("nhs number still empty", lambda r: r.profile.nhs_number is None),
        ),
    ),
    Case(
        name="an addressed greeting is greeted",
        profile=CareProfile(),
        latest="@Penny hi",
        mentioned=True,
        checks=(("reply exists", has_reply), ("one question at most", one_question_at_most)),
    ),
    Case(
        name="an addressed answer is captured AND answered in one message",
        profile=CareProfile(name="Juno Alden", aliases=["Mum"], age=84),
        latest="@Penny she's on apixaban now, did you get that?",
        mentioned=True,
        checks=(
            kind_in("captured"),
            ("reply exists", has_reply),
            (
                "apixaban recorded",
                lambda r: any("apixaban" in m.lower() for m in r.profile.medications),
            ),
            reply_mentions("apixaban"),
        ),
    ),
    Case(
        name="a nearly full record asks only for the one gap",
        profile=ESTABLISHED,
        latest="Oh and she takes apixaban in the mornings.",
        checks=(
            kind_in("captured"),
            ("one question at most", one_question_at_most),
            reply_avoids("how old", "her name", "where does she live", "what conditions"),
        ),
    ),
    Case(
        name="timeline dates never become confirmed_at",
        profile=CareProfile(name="Juno Alden"),
        events=("2026-07-20 symptom: Bad night, up until 3am",),
        latest="She's allergic to penicillin by the way.",
        checks=(
            kind_in("captured"),
            (
                "allergy recorded",
                lambda r: (
                    r.profile.allergies is not None
                    and "penicillin" in (r.profile.allergies.value or "").lower()
                ),
            ),
            (
                "no invented confirmed_at",
                lambda r: r.profile.allergies is None or r.profile.allergies.confirmed_at is None,
            ),
        ),
    ),
    Case(
        name="the person typing is not the person cared for",
        profile=CareProfile(name="Juno Alden", aliases=["Mum"], age=84),
        latest="I've got a chest infection myself so Sarah is covering Tuesday.",
        checks=(
            kind_in("nothing_new"),
            (
                "no condition filed",
                lambda r: not any("chest" in c.lower() for c in r.profile.conditions),
            ),
        ),
    ),
)


async def run_case(gateway: LLMGateway, case: Case) -> list[str]:
    """The failed check descriptions, empty on a clean pass."""
    result = await gateway.structured(
        CallSpec(
            purpose=INTERVIEW_PURPOSE,
            instructions=INTERVIEW_PROMPT,
            input=_render(
                case.profile,
                case.recent,
                case.events,
                case.latest,
                mentioned=case.mentioned,
            ),
            schema=InterviewResult,
            max_output_tokens=INTERVIEW_MAX_OUTPUT_TOKENS,
            reasoning_effort=INTERVIEW_EFFORT,
            prompt_version=INTERVIEW_PROMPT_VERSION,
        )
    )
    parsed = result.parsed
    failures = [description for description, check in case.checks if not check(parsed)]
    if "--verbose" in sys.argv or failures:
        print(f"    kind={parsed.kind!r} still_missing={parsed.still_missing!r}")
        print(f"    reply={parsed.reply!r}")
    return failures


async def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_known_args()
    gateway = LLMGateway()
    failed_cases = 0
    for case in CASES:
        print(f"► {case.name}")
        try:
            failures = await run_case(gateway, case)
        except Exception as error:  # a broken run is a failed case, not a crashed eval
            print(f"    ERROR {type(error).__name__}: {error}")
            failed_cases += 1
            continue
        if failures:
            failed_cases += 1
            for description in failures:
                print(f"    FAIL {description}")
        else:
            print("    ok")
    passed = len(CASES) - failed_cases
    print(f"\n{passed}/{len(CASES)} cases passed (max failures allowed: {MAX_FAILURES})")
    return 0 if failed_cases <= MAX_FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
