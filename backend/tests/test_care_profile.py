"""The relevance specification, and the hash that decides when it changed enough to matter.

The expensive thing in this file is `brief_hash`. A hash that changes when it should not
re-reads a household's entire history and bills it against a $15/month pot; a hash that does
not change when it should leaves six months of chat filtered by a placeholder, and the family
sees a thin record rather than an error. Both failures are silent, so both get tests that
state the rule in their name.
"""

from __future__ import annotations

import pytest

from app import care_profile as cp

# --- the renderer ---------------------------------------------------------------------


def _full() -> cp.CareProfile:
    return cp.CareProfile(
        name="Juno Alden",
        aliases=["Mum", "Nan"],
        age=84,
        place="Croydon",
        conditions=["Heart failure", "Hypertension"],
        medications=["Ramipril 2.5mg"],
        providers=["Dr Chen"],
        allergies=cp.Vital(value="Penicillin", confirmed_at="2026-07-25"),
    )


def test_the_brief_names_her_and_the_words_the_family_actually_types() -> None:
    """Aliases are the point of this file: "Mum" is what appears in the chat, not "Juno"."""
    brief = cp.render_brief(_full(), participants=["Liz"], timezone="Europe/London")
    assert "Care recipient: Juno Alden." in brief
    assert "Mum and Nan" in brief
    assert '"she"/"her"' in brief


def test_an_empty_profile_still_renders_a_usable_brief() -> None:
    """Before the interview runs there is still a household, and extraction still runs."""
    brief = cp.render_brief(cp.CareProfile(), participants=[], timezone="Europe/London")
    assert cp.UNKNOWN in brief
    assert "People in this chat: unknown." in brief
    assert "Household timezone: Europe/London." in brief


def test_the_placeholder_name_is_used_only_until_the_profile_has_one() -> None:
    """`households.care_recipient_name` is NOT NULL and holds a placeholder; the profile wins."""
    empty = cp.render_brief(
        cp.CareProfile(), participants=[], timezone="UTC", fallback_name="the placeholder"
    )
    assert "Care recipient: the placeholder." in empty

    named = cp.render_brief(
        cp.CareProfile(name="Juno"), participants=[], timezone="UTC", fallback_name="placeholder"
    )
    assert "Care recipient: Juno." in named
    assert "placeholder" not in named


def test_absent_vitals_are_omitted_rather_than_printed_as_unknown() -> None:
    """Prompt weight for no signal. The web UI does the opposite, deliberately."""
    brief = cp.render_brief(cp.CareProfile(name="Juno"), participants=[], timezone="UTC")
    assert "Allergies" not in brief
    assert "NHS number" not in brief


def test_a_vital_carries_the_date_a_human_last_confirmed_it() -> None:
    """A stale allergies line is more dangerous than a missing one, so the date rides along."""
    brief = cp.render_brief(_full(), participants=[], timezone="UTC")
    assert "Allergies: Penicillin (confirmed 2026-07-25)." in brief


# --- list cleaning --------------------------------------------------------------------


def test_cleaned_lists_do_not_depend_on_the_order_the_answers_arrived_in() -> None:
    """THE hash-stability test. Same answers, any order, same stored value.

    A family types "Nan" one week and "nan" the next. Keeping whichever spelling arrived
    first would make the stored value a function of message order, so re-capturing the same
    answers in a different sequence would hash differently and bill them for a re-read that
    changed nothing.
    """
    assert cp.CareProfile(aliases=["  nan ", "Mum", "MUM", "Nan"]).aliases == ["MUM", "Nan"]
    assert cp.CareProfile(aliases=["Nan", "mum", "MUM", "nan"]).aliases == ["MUM", "Nan"]

    a = cp.CareProfile(name="J", conditions=["Heart failure", "hypertension", "Hypertension"])
    b = cp.CareProfile(name="J", conditions=["Hypertension", "Heart failure", "hypertension"])
    assert a.conditions == b.conditions
    assert cp.brief_hash(a) == cp.brief_hash(b)


def test_list_fields_are_capped_so_one_pasted_essay_cannot_eat_the_chunk() -> None:
    profile = cp.CareProfile(conditions=[f"condition {i}" for i in range(50)])
    assert len(profile.conditions) == cp.MAX_LIST_ITEMS


def test_empty_and_whitespace_only_entries_are_dropped() -> None:
    assert cp.CareProfile(medications=["", "   ", "Ramipril"]).medications == ["Ramipril"]


# --- the hash: what MUST change it ----------------------------------------------------


def test_the_same_profile_hashes_the_same() -> None:
    assert cp.brief_hash(_full()) == cp.brief_hash(_full())


def test_a_new_condition_changes_the_hash() -> None:
    """A sharpened filter must reach the history it was meant to fix."""
    before = cp.brief_hash(_full())
    after = _full()
    after.conditions = [*after.conditions, "Osteoarthritis"]
    assert cp.brief_hash(after) != before


def test_learning_her_name_changes_the_hash() -> None:
    """The placeholder-to-real transition is the whole reason the cursor gets cleared."""
    placeholder = cp.brief_hash(cp.CareProfile(), fallback_name="not yet known")
    named = cp.brief_hash(cp.CareProfile(name="Juno Alden"))
    assert placeholder != named


def test_a_new_alias_changes_the_hash() -> None:
    """Aliases decide pronoun resolution, so they decide what got extracted."""
    before = cp.brief_hash(cp.CareProfile(name="Juno", aliases=["Mum"]))
    after = cp.brief_hash(cp.CareProfile(name="Juno", aliases=["Mum", "Nan"]))
    assert before != after


def test_a_vital_changes_the_hash() -> None:
    before = cp.brief_hash(cp.CareProfile(name="Juno"))
    after = cp.brief_hash(cp.CareProfile(name="Juno", allergies=cp.Vital(value="Penicillin")))
    assert before != after


# --- the hash: what MUST NOT change it ------------------------------------------------


def test_reordering_aliases_does_not_change_the_hash() -> None:
    """Same three answers in a different order is not a changed filter, and must not re-bill."""
    a = cp.brief_hash(cp.CareProfile(name="Juno", aliases=["Mum", "Nan", "Gran"]))
    b = cp.brief_hash(cp.CareProfile(name="Juno", aliases=["Gran", "Mum", "Nan"]))
    assert a == b


def test_reordering_conditions_does_not_change_the_hash() -> None:
    a = cp.brief_hash(cp.CareProfile(name="J", conditions=["Heart failure", "Hypertension"]))
    b = cp.brief_hash(cp.CareProfile(name="J", conditions=["Hypertension", "Heart failure"]))
    assert a == b


@pytest.mark.parametrize("timezone", ["Europe/London", "America/New_York", "UTC"])
def test_the_timezone_never_changes_the_hash(timezone: str) -> None:
    """THE settings-page test. Somebody fixing a timezone typo must not re-bill six months.

    The timezone IS in the brief — the model needs it to resolve "yesterday" — but it is
    appended by `render_brief` after the hashed lines, precisely so that this holds.
    """
    profile = _full()
    assert cp.brief_hash(profile) == cp.brief_hash(_full())
    brief = cp.render_brief(profile, participants=["Liz"], timezone=timezone)
    assert f"Household timezone: {timezone}." in brief


def test_a_new_member_joining_the_chat_does_not_change_the_hash() -> None:
    """Participants are context, not specification. A cousin joining is not a new filter."""
    profile = _full()
    before = cp.brief_hash(profile)
    small = cp.render_brief(profile, participants=["Liz"], timezone="UTC")
    large = cp.render_brief(profile, participants=["Liz", "Owen", "Yuval"], timezone="UTC")
    assert small != large  # the brief changed
    assert cp.brief_hash(profile) == before  # the hash did not


# --- the regression the eng review flagged CRITICAL -----------------------------------


def test_extraction_and_the_assistant_render_byte_identical_briefs() -> None:
    """REGRESSION (eng review T14). `build_care_brief` moved out of extraction/service.py.

    Both modules used to format their own `<care_brief>` block. Drift between them is silent
    and specific: extraction filters for one person while the assistant answers about another,
    and no test fails. There is one renderer now, and this asserts both doors reach it.
    """
    import app.assistant as assistant_module
    import app.extraction.service as extraction_module

    # Both doors must resolve to the ONE renderer. Asserting on identity rather than on
    # output is what makes this a regression test: a second implementation that happens to
    # produce the same string today would pass an output comparison and drift tomorrow.
    assert extraction_module.care_profile.build_brief_and_hash is cp.build_brief_and_hash
    assert assistant_module.build_brief_and_hash is cp.build_brief_and_hash


def test_a_malformed_stored_profile_is_treated_as_absent_not_fatal() -> None:
    """One bad row must not take down extraction, the assistant and the feed together."""

    class _Household:
        care_profile = {"aliases": "not a list", "age": "eighty-four"}

    assert cp.load(_Household()).name is None  # type: ignore[arg-type]

    class _Worse:
        care_profile = ["not", "a", "dict"]

    assert cp.load(_Worse()).name is None  # type: ignore[arg-type]


def test_the_hash_carries_none_of_the_brief() -> None:
    """`llm_runs` is the table an operator reads casually while chasing a cost spike.

    The brief names a real person and what is wrong with them. This property used to live in
    `test_llm_schemas.py` against the hasher that has since been deleted; it moved here with
    the function rather than being dropped with it.
    """
    digest = cp.brief_hash(_full())
    for word in ("juno", "alden", "croydon", "penicillin", "hypertension", "chen"):
        assert word not in digest.lower()


@pytest.mark.anyio
async def test_the_two_columns_named_care_brief_hash_hold_the_same_value() -> None:
    """THE point of unifying the hashers.

    `households.care_brief_hash` gates re-extraction; `llm_runs.care_brief_hash` records what a
    run ran under. They were briefly two different functions producing two different values
    under one column name, which is worse than no audit trail: it looks like one and dead-ends.
    "Which filter produced this event?" has to be a join.
    """

    class _Household:
        id = None
        timezone = "Europe/London"
        care_recipient_name = "the placeholder"
        care_profile = _full().model_dump(mode="json")

    household = _Household()
    stored = cp.brief_hash(cp.load(household), fallback_name=household.care_recipient_name)  # type: ignore[arg-type]

    class _Session:
        async def execute(self, *_a, **_k):
            class _R:
                def scalars(self):
                    class _S:
                        def all(self):
                            return ["Liz", "Owen"]

                    return _S()

            return _R()

    _, recorded = await cp.build_brief_and_hash(_Session(), household)  # type: ignore[arg-type]
    assert recorded == stored


def test_is_established_turns_true_on_a_name_alone() -> None:
    """A name is what turns "she" into a person; everything else only sharpens."""
    assert not cp.CareProfile().is_established()
    assert not cp.CareProfile(conditions=["Heart failure"]).is_established()
    assert cp.CareProfile(name="Juno").is_established()


def test_closing_the_interview_does_not_change_the_hash() -> None:
    """`interview_closed` says something about the CONVERSATION, not about who is cared for.

    It is set the moment Penny stops asking, on a household whose history may be six months
    long. If it were in the hash, that flag flipping would clear the extraction cursor and
    re-read every message the family ever sent, against a $15/month pot, because a chat ended.

    This holds for the reason the module docstring gives: the hash covers the lines
    `_profile_lines` prints, and this field is never printed.
    """
    open_ = cp.CareProfile(name="Juno Alden", conditions=["Heart failure"])
    closed = open_.model_copy(update={"interview_closed": True})
    assert cp.brief_hash(open_) == cp.brief_hash(closed)
    assert "interview" not in cp.render_brief(closed, participants=[], timezone="UTC").lower()


# --- the four on their way to the screen -----------------------------------------------


def _row(**profile: object):
    """A household row as `to_household` sees it — attributes, not a model."""

    class _Household:
        id = "0d4f8b2e-1c8a-4c1e-9f6a-3b7d2e5a91cc"
        name = "The Aldens"
        care_recipient_name = "Juno"
        timezone = "Europe/London"
        care_profile = profile

    return _Household()


def test_the_four_vitals_reach_the_api_with_their_dates() -> None:
    """The interview captures these with great care; this is the only thing that shows them.

    They were captured and never exposed for a while, which is the quiet version of not having
    built the feature at all: the prompt refuses to guess an NHS number, the profile stores it,
    and the band a paramedic would read rendered four blanks regardless.
    """
    from app.schemas import to_household

    out = to_household(
        _row(
            allergies={"value": "Penicillin", "confirmed_at": "2026-03-14"},
            baseline={"value": "Mobile with a frame indoors"},
        )
    )

    assert out.care_profile.allergies is not None
    assert out.care_profile.allergies.value == "Penicillin"
    assert out.care_profile.allergies.confirmed_at == "2026-03-14"
    # Given without a date, and it stays without one rather than borrowing today's.
    assert out.care_profile.baseline is not None
    assert out.care_profile.baseline.confirmed_at is None


def test_an_unanswered_vital_is_null_and_its_key_is_still_there() -> None:
    """ "Nobody has told us" and "she has no allergies" are different facts.

    Only one of them is safe to act on, and a key that disappeared when empty would let a client
    turn the first into the second by accident. All four are always present.
    """
    from app.schemas import to_household

    serialised = to_household(_row()).model_dump()["care_profile"]

    assert {"nhs_number", "allergies", "respect_form", "baseline"} <= set(serialised)
    vitals = ("nhs_number", "allergies", "respect_form", "baseline")
    assert all(serialised[name] is None for name in vitals)


def test_a_malformed_profile_costs_the_vitals_and_not_the_session() -> None:
    """`GET /api/me` is the request the whole app boots on: it must not 500 over a bad row."""
    from app.schemas import to_household

    class _Bad:
        id = "0d4f8b2e-1c8a-4c1e-9f6a-3b7d2e5a91cc"
        name = "The Aldens"
        care_recipient_name = "Juno"
        timezone = "Europe/London"
        care_profile = ["not", "a", "dict"]

    assert to_household(_Bad()).care_profile.allergies is None
