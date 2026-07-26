"""The four vitals: what reaches the brief, what reaches the hash, and what reaches a log line.

`nhs_number`, `allergies`, `respect_form` and `baseline` are the facts somebody asks for before
anything else, and they are the only fields in `CareProfile` where being wrong is a clinical
problem rather than a thin feed. Three separate failures are covered here, all of them silent:

    a vital that renders but is not hashed   -> the family states an allergy, the brief the next
                                                extraction run reads is the same one that ran
                                                before, and nothing anywhere records that the
                                                filter was supposed to have moved
    a vital that is hashed but says nothing  -> a blank the model filed, or the same answer
                                                re-typed with a trailing space, clears the
                                                extraction cursor and re-reads six months of a
                                                household's chat against a $15/month pot
    a vital that reaches a log line          -> an NHS number and a named allergy in the
                                                application log, which is the one place in this
                                                system nobody thinks of as holding medical data

`test_care_profile.py` covers the renderer and the hash in general and touches `allergies`
once. This file is about the four of them specifically, because they are the four that get read
in a hurry.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import pytest

from app import care_profile as cp
from app.models import Household

# The order they are rendered in, which is also the order the web band prints them in and the
# order `brief_hash` sees. Kept as data here so the ordering test cannot drift from the field
# list it is asserting about.
FOUR_VITALS = ("nhs_number", "allergies", "respect_form", "baseline")

LABELS = {
    "nhs_number": "NHS number",
    "allergies": "Allergies",
    "respect_form": "RESPECT form",
    "baseline": "Baseline",
}


def brief_of(**vitals: cp.Vital | None) -> str:
    """A brief for a named household carrying only the vitals under test."""
    return cp.render_brief(
        cp.CareProfile(name="Juno Alden", **vitals),
        participants=["Liz"],
        timezone="Europe/London",
    )


# --- what the brief says ---------------------------------------------------------------


def test_all_four_vitals_render_with_the_label_a_clinician_would_ask_for() -> None:
    """A value with no label is a number in a paragraph, and one of them is an NHS number."""
    brief = brief_of(
        nhs_number=cp.Vital(value="485 777 3456"),
        allergies=cp.Vital(value="Penicillin, adhesive plasters"),
        respect_form=cp.Vital(value="On file"),
        baseline=cp.Vital(value="Mobile with frame, breathless on one flight of stairs"),
    )
    for label in LABELS.values():
        assert f"{label}: " in brief
    assert "Mobile with frame, breathless on one flight of stairs" in brief


def test_the_four_vitals_render_in_the_order_they_are_asked_for() -> None:
    """ORDER IS PART OF THE HASH CONTRACT, not a matter of taste.

    `brief_hash` is taken over the rendered lines, so shuffling these four would re-read and
    re-bill every household's entire history for a brief that says exactly the same thing. The
    web band prints them in this order too, so the two can be read side by side.
    """
    brief = brief_of(**{field: cp.Vital(value=f"v-{field}") for field in FOUR_VITALS})
    positions = [brief.index(LABELS[field]) for field in FOUR_VITALS]
    assert positions == sorted(positions)


def test_the_vitals_render_on_one_line_so_they_are_read_together() -> None:
    """They are four halves of one answer to "who am I about to treat?", not four facts."""
    brief = brief_of(
        nhs_number=cp.Vital(value="485 777 3456"),
        allergies=cp.Vital(value="Penicillin"),
    )
    line = next(row for row in brief.splitlines() if "NHS number" in row)
    assert "Allergies: Penicillin" in line


@pytest.mark.parametrize("field", FOUR_VITALS)
def test_a_vital_carries_the_date_it_was_confirmed_into_the_brief(field: str) -> None:
    """A stale vital is more dangerous than a missing one, and the date is the only tell.

    Parametrised over all four rather than asserted once on allergies: `confirmed_at` is on
    `Vital`, so a renderer that printed the date for one field and not another would be a
    one-word omission with no symptom except a fact that looks fresher than it is.
    """
    brief = brief_of(**{field: cp.Vital(value="stated", confirmed_at="2026-07-25")})
    assert f"{LABELS[field]}: stated (confirmed 2026-07-25)" in brief


def test_a_vital_nobody_dated_renders_without_inventing_a_date() -> None:
    """No date is a true statement about the record. A guessed one is a false one about care.

    The interview is never told today's date (see `interview._render`), so a `confirmed_at` it
    filled in would be invented. The brief has to be able to say "this was stated, and nobody
    knows when" without that reading as "confirmed today".
    """
    brief = brief_of(allergies=cp.Vital(value="Penicillin"))
    assert "Allergies: Penicillin" in brief
    assert "confirmed" not in brief


def test_absent_vitals_are_omitted_from_the_brief_and_the_web_band_shows_them_anyway() -> None:
    """The two renderers disagree ON PURPOSE, and this pins the half that lives here.

    A filter cannot act on an absence — "NHS number: unknown" cannot make a message more or
    less relevant, it can only take room a transcript wanted. The person header prints all four
    labels blank because ITS reader is a human, and a hidden allergies row is indistinguishable
    from "she has no allergies". If a change makes this test fail, the question to answer first
    is which of the two readers it was made for.
    """
    brief = brief_of(allergies=cp.Vital(value="Penicillin"))
    assert "Allergies: Penicillin" in brief
    for absent in ("NHS number", "RESPECT form", "Baseline"):
        assert absent not in brief
    assert "unknown" not in brief.replace("People in this chat: unknown.", "")


def test_a_blank_vital_never_puts_a_dangling_label_in_front_of_the_model() -> None:
    """`Allergies:` with nothing after it reads as an allergy nobody could make out.

    Which is a different claim from "no allergy was stated", and the wrong one. A model that
    files a vital with an empty or whitespace value — which nothing stops it doing — must
    produce the same brief as one that files no vital at all.
    """
    for empty in ("", "   ", "\n\t"):
        brief = brief_of(allergies=cp.Vital(value=empty))
        assert "Allergies" not in brief


# --- what the hash sees ----------------------------------------------------------------


@pytest.mark.parametrize("field", FOUR_VITALS)
def test_learning_any_one_of_the_four_vitals_changes_the_hash(field: str) -> None:
    """THE coverage test. A vital that renders but does not hash is a filter that never lands.

    `households.care_brief_hash` is what decides whether a sharpened profile ever reaches the
    history it was meant to fix. A field printed into the brief but missing from the hash would
    leave a family who finally answered "she's allergic to penicillin" with six months of chat
    still filtered by the brief that did not know it — and no error, anywhere, ever.
    """
    before = cp.brief_hash(cp.CareProfile(name="Juno"))
    after = cp.brief_hash(cp.CareProfile(name="Juno", **{field: cp.Vital(value="stated")}))
    assert after != before


@pytest.mark.parametrize("field", FOUR_VITALS)
def test_correcting_a_vitals_value_changes_the_hash(field: str) -> None:
    """A correction is the case with the most at stake: the old value was wrong AND acted on."""
    was = cp.brief_hash(cp.CareProfile(name="Juno", **{field: cp.Vital(value="first")}))
    now = cp.brief_hash(cp.CareProfile(name="Juno", **{field: cp.Vital(value="corrected")}))
    assert now != was


def test_re_confirming_a_vital_changes_the_hash_and_that_cost_is_accepted() -> None:
    """PINS A TRADEOFF, so that changing it is a decision rather than an accident.

    Re-confirming an unchanged allergy moves `confirmed_at`, which is rendered, which moves the
    hash, which clears the extraction cursor and re-reads the household's history. The filter
    did not change in substance, so this is money spent for no new judgement — the same shape
    of waste the timezone is deliberately kept out of the hash to avoid.

    It is accepted rather than fixed, for two reasons. Re-confirmation happens a handful of
    times in a household's life, where a settings save happens weekly. And the alternative —
    excluding `confirmed_at` from the hash while still printing it — is exactly the "hash an
    explicit list of fields" design the module docstring rejects: it puts back a way to render
    something into the brief that the hash cannot see, and the next person to add a field would
    have a precedent for doing it again.
    """
    stated = cp.CareProfile(name="Juno", allergies=cp.Vital(value="Penicillin"))
    confirmed = cp.CareProfile(
        name="Juno", allergies=cp.Vital(value="Penicillin", confirmed_at="2026-07-25")
    )
    assert cp.brief_hash(confirmed) != cp.brief_hash(stated)


def test_the_same_vital_retyped_with_stray_whitespace_does_not_re_read_the_history() -> None:
    """A family re-stating an allergy in the group must not cost them a re-extraction.

    The interview writes whatever the model returns, and a model asked to carry a record
    forward returns `"Penicillin "` as readily as `"Penicillin"`. Unnormalised, that trailing
    space is a different rendered brief, a different hash, and six months of chat re-read for a
    filter that is word-for-word the one that already ran.
    """
    tidy = cp.CareProfile(
        name="Juno", allergies=cp.Vital(value="Penicillin", confirmed_at="2026-07-25")
    )
    untidy = cp.CareProfile(
        name="Juno",
        allergies=cp.Vital(value="  Penicillin  ", confirmed_at=" 2026-07-25 "),
    )
    assert untidy.allergies is not None
    assert untidy.allergies.value == "Penicillin"
    assert cp.brief_hash(untidy) == cp.brief_hash(tidy)


def test_a_vital_the_model_filed_blank_does_not_re_read_the_history() -> None:
    """Filing an empty allergies field is not new information and must not be billed as such."""
    nothing = cp.brief_hash(cp.CareProfile(name="Juno"))
    blank = cp.brief_hash(cp.CareProfile(name="Juno", allergies=cp.Vital(value="   ")))
    assert blank == nothing


def test_an_overlong_confirmation_date_is_cut_to_fit_rather_than_rejected() -> None:
    """A raise here would discard the whole turn's record, not just the date.

    `Vital` is parsed inside `interview.capture_answer`, where every exception resolves to
    silence and an unwritten profile. A model that answers `confirmed_at` with a sentence must
    cost the sentence, never the name and the conditions that came with it.
    """
    vital = cp.Vital(value="Penicillin", confirmed_at="the week after her fall, " * 10)
    assert vital.confirmed_at is not None
    assert len(vital.confirmed_at) == cp.MAX_CONFIRMED_AT_CHARS


# --- what never leaves the process ------------------------------------------------------


def test_the_hash_carries_no_trace_of_an_nhs_number_or_an_allergy() -> None:
    """`llm_runs.care_brief_hash` is read casually, by an operator chasing a cost spike.

    It is a column of opaque hex and it has to stay that way: the vitals are the most
    identifying and the most sensitive fields in this system, and a digest that leaked any part
    of one would put an NHS number into a table nobody treats as medical data.
    """
    digest = cp.brief_hash(
        cp.CareProfile(
            name="Juno Alden",
            nhs_number=cp.Vital(value="4857773456"),
            allergies=cp.Vital(value="Penicillin"),
            respect_form=cp.Vital(value="On file at Salford Royal"),
            baseline=cp.Vital(value="Mobile with frame"),
        )
    )
    assert len(digest) == 32
    for secret in ("4857773456", "485", "penicillin", "salford", "frame", "juno"):
        assert secret not in digest.lower()


@pytest.mark.anyio
async def test_writing_a_profile_logs_ids_and_counts_but_never_a_vital(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one log line on the profile write path, checked against the fields it stands next to.

    `write_profile` logs because `messages_recalled` is the number that predicts the next
    extraction bill and an operator needs it. That line sits inside the function holding the
    care recipient's NHS number and allergy list, which is the arrangement where a debugging
    `%s` gets added one afternoon and nobody notices for a year. Ids and counts only.
    """

    class FakeResult:
        rowcount = 7

    class FakeSession:
        async def execute(self, _statement: Any) -> FakeResult:
            return FakeResult()

    household = Household(
        id=uuid4(),
        username="pennytest",
        password_hash="x",
        name="The Alden family",
        care_recipient_name="your loved one",
        timezone="Europe/London",
        session_version=1,
        care_profile={},
        care_brief_hash="a-stale-hash",
    )
    profile = cp.CareProfile(
        name="Juno Alden",
        nhs_number=cp.Vital(value="4857773456"),
        allergies=cp.Vital(value="Penicillin", confirmed_at="2026-07-25"),
    )

    with caplog.at_level(logging.DEBUG, logger="app.care_profile"):
        written = await cp.write_profile(FakeSession(), household, profile)  # type: ignore[arg-type]

    assert written.brief_changed and written.messages_recalled == 7
    logged = "\n".join(record.getMessage() for record in caplog.records).lower()
    assert "messages_recalled=7" in logged
    for secret in ("4857773456", "penicillin", "juno", "alden"):
        assert secret not in logged
