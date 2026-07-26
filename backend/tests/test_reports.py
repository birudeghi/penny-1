"""Report generation, offline: the citation resolver, the prompt input, and the routes.

WHAT THESE TESTS ARE ACTUALLY DEFENDING

A report is the only thing Penny produces that a stranger reads and acts on. The property
under test is not "generation works" — it is **nothing reaches the family that they cannot
check, and nothing reaches the model that it could get wrong**:

    a handle that names no entry              -> dropped, and removed from the prose too
    a section whose every handle was invented  -> the whole section deleted
    the rendered prompt                        -> contains no UUID anywhere in it
    a month-precision entry                    -> printed as a month, never as a clock time
    the previous-period column                 -> absent when the record does not reach back
    a report belonging to another household    -> 404, byte-identical to a random uuid
    a report the model failed to write         -> a sentence, never an exception class name

NOTHING HERE NEEDS POSTGRES, AND THAT IS A CHOICE. The two places a mistake would be both
invisible and permanent — the citation resolver and the prompt renderer — are pure functions
over plain data, so they are tested directly and run on every checkout. The route tests use a
recording stub in place of a session, because what they are asking about is what the WHERE
clause CONTAINS, and that is settled before a connection exists. `FakeTransport` replays a
hand-built `Response` for the one end-to-end pass, exactly as `test_extraction_runner` does.

The gap this leaves, stated rather than hidden: the advisory lock, the `pending -> running ->
complete` transitions and the detached background task are only exercised against real
Postgres, and nothing in this file covers them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.errors import BudgetExceededError
from app.llm.gateway import LLMGateway
from app.llm.prompts import REPORT_PROMPT, REPORT_PROMPT_VERSION
from app.llm.transport import FakeTransport
from app.models import Household
from app.reports import (
    MIN_EVENTS_FOR_REPORT,
    REPORT_EFFORT,
    REPORT_MAX_OUTPUT_TOKENS,
    CitableEvent,
    Period,
    RecordStats,
    ReportDraft,
    ReportSectionDraft,
    build_call_spec,
    client_sentence,
    lock_key,
    period_title,
    render_report_input,
    resolve_sections,
)
from app.routers.reports import GENERATED_TODAY_DETAIL

TZ = ZoneInfo("Europe/London")
MODEL = "gpt-5.5-2026-04-23"
NOW = datetime(2026, 7, 25, 8, 40, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _pinned_settings(settings_override: Callable[..., Settings]) -> None:
    """Never read the developer's .env: the model id decides which price table applies."""
    settings_override(llm_model_extract=MODEL, llm_model_report=MODEL, openai_api_key="test-key")


# --- builders ------------------------------------------------------------------------


def entry(
    handle: str,
    *,
    kind: str = "symptom",
    when: datetime | None = None,
    precision: str = "day",
    title: str = "Poor sleep, up four times overnight",
    body: str | None = None,
    details: dict[str, Any] | None = None,
) -> CitableEvent:
    return CitableEvent(
        handle=handle,
        event_id=uuid4(),
        kind=kind,
        occurred_at=when or datetime(2026, 7, 14, 20, 4, tzinfo=UTC),
        occurred_at_precision=precision,
        title=title,
        body=body,
        details=details or {},
    )


def section(*handles: str, heading: str = "Sleep", body: str | None = None) -> ReportSectionDraft:
    inline = " ".join(f"[{handle}]" for handle in handles)
    return ReportSectionDraft(
        heading=heading,
        body_markdown=body if body is not None else f"She slept badly {inline}.",
        event_handles=list(handles),
    )


def stats(**overrides: Any) -> RecordStats:
    base: dict[str, Any] = {
        "events_in_period": 4,
        "events_rendered": 4,
        "by_kind": {"symptom": 2, "appointment": 1, "medication": 1},
        "by_kind_previous": None,
        "top_symptoms": [("poor sleep", 2)],
        "medication_actions": {"stopped": 1},
        "appointment_statuses": {"attended": 1},
    }
    return RecordStats(**(base | overrides))


def household() -> Household:
    return Household(
        id=UUID(int=7),
        username="the-doyles",
        password_hash="x",
        name="The Doyles",
        care_recipient_name="Margaret",
        timezone="Europe/London",
    )


def period(days: int = 365, *, truncated: bool = False) -> Period:
    return Period(start=NOW - timedelta(days=days), end=NOW, truncated_to_record=truncated)


def draft(**overrides: Any) -> ReportDraft:
    base: dict[str, Any] = {
        "summary": "Margaret's sleep worsened and the amlodipine was stopped.",
        "urgent_flag": False,
        "urgent_reason": None,
        "sections": [section("E1")],
        "questions_for_the_doctor": ["Should the review be brought forward?"],
        "watch_items": ["Appetite"],
        "data_gaps": ["No blood pressure recorded"],
    }
    return ReportDraft(**(base | overrides))


def completed(payload: dict[str, Any]) -> dict[str, Any]:
    """One `Response` the SDK will parse, carrying `payload` as the model's JSON answer."""
    return {
        "id": "resp_report01",
        "created_at": 1784275920.0,
        "model": MODEL,
        "object": "response",
        "output": [
            {
                "id": "msg_0001",
                "content": [
                    {"annotations": [], "text": json.dumps(payload), "type": "output_text"}
                ],
                "role": "assistant",
                "status": "completed",
                "type": "message",
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "status": "completed",
        "usage": {
            "input_tokens": 9_000,
            "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
            "output_tokens": 1_400,
            "output_tokens_details": {"reasoning_tokens": 600},
            "total_tokens": 10_400,
        },
    }


# --- resolving citations --------------------------------------------------------------


def test_a_handle_naming_a_real_entry_resolves_to_that_events_id() -> None:
    only = entry("E1")
    sections, dropped = resolve_sections([section("E1")], {"E1": only}, tz=TZ)

    assert dropped == 0
    assert sections[0]["citations"] == [
        {
            "handle": "E1",
            "event_id": str(only.event_id),
            "kind": "symptom",
            "occurred_at": "2026-07-14T20:04:00Z",
            "title": "Poor sleep, up four times overnight",
        }
    ]


def test_a_handle_that_names_no_entry_is_dropped_and_never_fails_the_report() -> None:
    real = entry("E1")
    sections, dropped = resolve_sections([section("E1", "E9")], {"E1": real}, tz=TZ)

    assert dropped == 1
    assert [citation["handle"] for citation in sections[0]["citations"]] == ["E1"]


def test_an_unresolved_handle_is_stripped_from_the_prose_as_well_as_the_citations() -> None:
    """A `[E9]` left standing with no chip under it is a dead citation the family will click."""
    real = entry("E1")
    drafted = section(
        "E1",
        "E9",
        body="She was up several times overnight [E1] [E9], and again on the 19th [E9].",
    )
    sections, _ = resolve_sections([drafted], {"E1": real}, tz=TZ)

    body = sections[0]["body_markdown"]
    assert "E9" not in body
    assert body == "She was up several times overnight [E1], and again on the 19th."


def test_a_section_whose_every_handle_was_invented_is_deleted_rather_than_shown() -> None:
    sections, dropped = resolve_sections([section("E8", "E9")], {"E1": entry("E1")}, tz=TZ)

    assert sections == []
    assert dropped == 2


def test_a_section_that_cites_nothing_at_all_is_deleted() -> None:
    """The prompt demands a handle behind every sentence; an empty list is that rule failing."""
    bare = ReportSectionDraft(heading="Background", body_markdown="She is 84.", event_handles=[])
    sections, _ = resolve_sections([bare], {"E1": entry("E1")}, tz=TZ)

    assert sections == []


def test_the_same_handle_cited_twice_becomes_one_citation() -> None:
    real = entry("E1")
    sections, _ = resolve_sections([section("E1", "E1")], {"E1": real}, tz=TZ)

    assert len(sections[0]["citations"]) == 1


@pytest.mark.parametrize("spelling", ["E1", "e1", " E1 ", "[E1]", "E1.", "E 1"])
def test_a_handle_is_recognised_however_the_model_spelled_it(spelling: str) -> None:
    """A whole summary lost because the model kept the brackets is not a trade worth making."""
    drafted = ReportSectionDraft(
        heading="Sleep", body_markdown="She slept badly [E1].", event_handles=[spelling]
    )
    sections, dropped = resolve_sections([drafted], {"E1": entry("E1")}, tz=TZ)

    assert dropped == 0
    assert sections[0]["citations"][0]["handle"] == "E1"


def test_a_citation_carries_the_title_and_date_it_had_when_the_report_was_written() -> None:
    """Denormalised on purpose: a later edit must not rewrite a document already handed over."""
    only = entry("E1", title="Amlodipine stopped", kind="medication")
    sections, _ = resolve_sections([section("E1")], {"E1": only}, tz=TZ)
    citation = sections[0]["citations"][0]

    only_title_changed = "Amlodipine stopped by Dr Aziz"
    assert citation["title"] == "Amlodipine stopped" != only_title_changed
    assert citation["kind"] == "medication"


# --- the draft's own rules ------------------------------------------------------------


def test_an_urgent_reason_under_a_false_flag_is_cleared_rather_than_stored() -> None:
    """The panel keys its red band off the flag and prints the reason: a mismatch is a warning
    with no warning attached to it."""
    parsed = draft(urgent_flag=False, urgent_reason="She has been short of breath since Friday")

    assert parsed.urgent_reason is None


def test_an_urgent_reason_survives_when_the_flag_is_actually_set() -> None:
    parsed = draft(urgent_flag=True, urgent_reason="Short of breath since Friday [E3]")

    assert parsed.urgent_reason == "Short of breath since Friday [E3]"


def test_a_list_the_model_over_filled_is_capped_and_blank_items_are_dropped() -> None:
    parsed = draft(watch_items=["a", "  ", "b", "c", "d", "e", "f", "g"])

    assert parsed.watch_items == ["a", "b", "c", "d", "e"]


def test_an_over_long_summary_is_truncated_rather_than_rejected() -> None:
    """Enforced by our validator, never as a JSON Schema keyword: strict structured outputs
    have rejected `maxLength`, and a 400 there loses the whole report instead of one field."""
    parsed = draft(summary="x" * 5_000)

    assert len(parsed.summary) == 700


# --- the rendered prompt --------------------------------------------------------------


def rendered(index: list[CitableEvent], record: RecordStats | None = None, **kwargs: Any) -> str:
    return render_report_input(
        household=household(),
        period=kwargs.get("period", period()),
        tz=TZ,
        index=index,
        stats=record or stats(),
    )


def test_no_uuid_ever_reaches_the_prompt() -> None:
    """The whole reason citations are handles. Models transpose long hex runs, and an id the
    model can retype is an id it can retype wrong."""
    text = rendered([entry("E1"), entry("E2", kind="appointment")])

    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text) is None


def test_every_entry_is_printed_with_the_handle_the_resolver_will_look_up() -> None:
    index = [entry("E1"), entry("E2", kind="medication", title="Amlodipine stopped")]
    text = rendered(index)

    assert "[E1]" in text
    assert "[E2] 2026-07-14 medication: Amlodipine stopped" in text


def test_a_month_precision_entry_is_printed_as_a_month_and_never_as_a_clock_time() -> None:
    """Printing 15:30 on an entry the extractor only placed to the month hands the model a
    certainty the original message never had — and it will repeat it to a doctor."""
    text = rendered([entry("E1", precision="month")])

    assert "[E1] 2026-07 symptom" in text
    assert "20:04" not in text


def test_an_exact_precision_entry_keeps_its_time_in_the_household_timezone() -> None:
    text = rendered([entry("E1", precision="exact")])

    # 20:04 UTC on 14 July is 21:04 in London, and London is what the family reads.
    assert "[E1] 2026-07-14 21:04 symptom" in text


def test_a_medication_entry_carries_its_dose_because_that_is_what_the_gp_asks_for() -> None:
    text = rendered(
        [
            entry(
                "E1",
                kind="medication",
                title="Ramipril reduced",
                details={"medication_name": "ramipril", "dose_text": "2.5 mg", "action": "changed"},
            )
        ]
    )

    assert "(ramipril, 2.5 mg, changed)" in text


def test_the_previous_period_column_is_absent_when_the_record_does_not_reach_back() -> None:
    """A zero would be read as "there was nothing before", which is a false comparison — and a
    false comparison is the sentence that changes a prescription."""
    text = rendered([entry("E1")], stats(by_kind_previous=None))

    assert "symptom: 2\n" in text
    assert "previous period" not in text


def test_the_previous_period_column_appears_when_the_record_does_reach_back() -> None:
    text = rendered([entry("E1")], stats(by_kind_previous={"symptom": 5}))

    assert "symptom: 2 (previous period of the same length: 5)" in text


def test_a_truncated_read_is_declared_in_the_counts_block() -> None:
    text = rendered([entry("E1")], stats(events_in_period=530, events_rendered=200))

    assert "entries shown below: 200 (the most recent)" in text


def test_upcoming_entries_are_rendered_in_their_own_block_not_mixed_into_the_record() -> None:
    """Cited like anything else, but a booked appointment is not evidence that something has
    already happened, and the prompt can only say so if the two blocks are separable."""
    past = entry("E1")
    ahead = entry("E2", kind="appointment", when=NOW + timedelta(days=6), title="District nurse")
    text = rendered([past, ahead])

    assert "<upcoming>" in text
    assert text.index("[E1]") < text.index("<upcoming>") < text.index("[E2]")


def test_the_care_recipient_is_named_so_the_model_never_writes_she_without_an_antecedent() -> None:
    text = rendered([entry("E1")])

    assert "Care recipient: Margaret." in text


# --- the call spec --------------------------------------------------------------------


def test_the_report_call_uses_the_report_model_and_not_the_extraction_default() -> None:
    """The gateway defaults to `llm_model_extract`; a report that forgets to say otherwise is
    silently billed and tuned as an extraction."""
    settings = Settings(_env_file=None, llm_model_report="report-model", llm_model_extract="other")
    spec = _spec_with(settings)

    assert spec.model == "report-model"


def test_the_prompt_version_is_derived_from_the_prompt_files_own_bytes() -> None:
    """Editing report.md IS bumping the version; a hand-kept constant drifts from the file."""
    import hashlib

    assert hashlib.sha256(REPORT_PROMPT.encode()).hexdigest()[:12] == REPORT_PROMPT_VERSION
    assert _spec_with(None).prompt_version == REPORT_PROMPT_VERSION


def test_the_call_declares_the_report_purpose_so_the_audit_trail_can_price_it() -> None:
    spec = _spec_with(None)

    assert spec.purpose == "report"
    assert spec.max_output_tokens == REPORT_MAX_OUTPUT_TOKENS
    assert spec.reasoning_effort == REPORT_EFFORT


def _spec_with(settings: Settings | None) -> Any:
    import app.reports

    if settings is not None:
        app.reports.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        return build_call_spec(
            household=household(),
            period=period(),
            tz=TZ,
            index=[entry("E1")],
            stats=stats(),
        )
    finally:
        if settings is not None:
            from app.config import get_settings

            app.reports.get_settings = get_settings  # type: ignore[assignment]


async def test_a_model_answer_round_trips_through_the_gateway_into_resolved_sections() -> None:
    """The one end-to-end pass with no database: schema out, answer in, citations resolved."""
    index = [entry("E1"), entry("E2", kind="medication", title="Amlodipine stopped")]
    answer = {
        "summary": "Sleep worsened and the amlodipine was stopped.",
        "urgent_flag": False,
        "urgent_reason": None,
        "sections": [
            {
                "heading": "Sleep",
                "body_markdown": "She was up overnight three times [E1], and again later [E7].",
                "event_handles": ["E1", "E7"],
            }
        ],
        "questions_for_the_doctor": ["How long before the dizziness settles?"],
        "watch_items": ["Appetite"],
        "data_gaps": ["No blood pressure recorded"],
    }
    transport = FakeTransport([completed(answer)])
    gateway = LLMGateway(transport=transport)

    result = await gateway.structured(
        build_call_spec(household=household(), period=period(), tz=TZ, index=index, stats=stats())
    )
    sections, dropped = resolve_sections(
        result.parsed.sections, {e.handle: e for e in index}, tz=TZ
    )

    assert transport.calls[0]["model"] == MODEL
    assert dropped == 1
    assert sections[0]["body_markdown"] == "She was up overnight three times [E1], and again later."
    assert sections[0]["citations"][0]["event_id"] == str(index[0].event_id)


# --- titles, locks and sentences ------------------------------------------------------


def test_a_truncated_read_is_declared_in_the_data_gaps_the_family_actually_sees() -> None:
    """The counts block tells the model; this line tells the family. Prepended, because it
    qualifies everything above it."""
    from app.reports import _data_gaps

    gaps = _data_gaps(
        ["No blood pressure recorded"], stats(events_in_period=530, events_rendered=200)
    )

    assert gaps[0] == "This summary read the 200 most recent entries of 530 in the period."
    assert gaps[1] == "No blood pressure recorded"


def test_an_untruncated_read_adds_no_line_the_family_did_not_need() -> None:
    from app.reports import _data_gaps

    assert _data_gaps(["No weight recorded"], stats()) == ["No weight recorded"]


def test_a_day_boundary_is_floored_in_the_households_zone_and_not_in_utc() -> None:
    """Local midnight in BST is 23:00 the previous day in UTC. Floored the other way round,
    an evening's entries file under the wrong day."""
    from app.reports import floor_local_day

    floored = floor_local_day(datetime(2026, 7, 14, 20, 4, tzinfo=UTC), TZ)

    assert floored == datetime(2026, 7, 13, 23, 0, tzinfo=UTC)


def test_a_full_twelve_month_period_is_titled_as_twelve_months() -> None:
    assert period_title(period(365), TZ) == "Twelve months to 25 July 2026"


def test_a_period_clamped_to_a_short_record_is_titled_with_its_real_span() -> None:
    """ "Twelve months to..." printed over a six-week record is a small lie at the top of a
    clinical document."""
    assert period_title(period(42, truncated=True), TZ) == "13 June 2026 to 25 July 2026"


def test_the_report_lock_never_collides_with_the_extraction_lock_for_one_household() -> None:
    """Same household, two namespaces: generating a summary must not block extracting messages."""
    from app.extraction.service import lock_key as extraction_lock_key

    household_id = uuid4()
    assert lock_key(household_id) != extraction_lock_key(household_id)


def test_the_lock_key_is_stable_across_processes_unlike_the_builtin_hash() -> None:
    household_id = UUID("0d4f8b2e-1c8a-4c1e-9f6a-3b7d2e5a91cc")

    assert lock_key(household_id) == lock_key(household_id)
    assert -(2**63) <= lock_key(household_id) < 2**63


def test_a_budget_refusal_reaches_the_family_as_its_own_sentence() -> None:
    """`reports.error` is rendered verbatim, so a refusal must say what it is."""
    assert client_sentence(BudgetExceededError()) == BudgetExceededError.detail


def test_any_other_failure_becomes_a_sentence_and_never_an_exception_class_name() -> None:
    sentence = client_sentence(RuntimeError("asyncpg.exceptions.ConnectionDoesNotExistError"))

    assert "asyncpg" not in sentence
    assert sentence == "The summary could not be written. Try again in a few minutes."


def test_a_summary_whose_every_citation_was_invented_is_a_failure_not_a_document() -> None:
    """What survives the resolver in that case is a lead paragraph with nothing under it — a
    page that LOOKS like a summary and cannot be checked anywhere. Storing that as `complete`
    is the one outcome this module exists to prevent."""
    from app.reports import UngroundedReportError

    sections, _ = resolve_sections([section("E8", "E9")], {"E1": entry("E1")}, tz=TZ)

    assert sections == []
    assert "show its working" in UngroundedReportError.detail


def test_a_thin_record_is_refused_before_anything_is_spent() -> None:
    from app.reports import ThinRecordError

    error = ThinRecordError()

    assert error.status_code == 422
    assert "not enough in the record" in error.detail
    assert MIN_EVENTS_FOR_REPORT == 3


# --- the routes -----------------------------------------------------------------------


def _flatten(router: Any) -> list[tuple[str, frozenset[str]]]:
    """Every route in registration order, with included routers unwrapped in place.

    FastAPI wraps `include_router` in an `_IncludedRouter` that holds the real routes behind
    `original_router`, so a flat read of `app.routes` sees a handful of opaque wrappers and
    one bare catch-all — which would make the ordering assertion below pass for the wrong
    reason, whatever the ordering actually was.
    """
    found: list[tuple[str, frozenset[str]]] = []
    for route in router.routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found.extend(_flatten(inner))
        elif hasattr(route, "path"):
            found.append((route.path, frozenset(getattr(route, "methods", ()) or ())))
    return found


def _routes() -> list[tuple[str, frozenset[str]]]:
    from app.main import app as penny_app

    return _flatten(penny_app)


def test_the_three_report_routes_from_the_contract_are_registered() -> None:
    paths = _routes()

    assert ("/api/reports", frozenset({"GET"})) in paths
    assert ("/api/reports/{report_id}", frozenset({"GET"})) in paths
    assert ("/api/reports/generate", frozenset({"POST"})) in paths


def test_the_reports_router_is_included_before_the_spa_catch_all() -> None:
    """`mount_spa` registers `GET /{path:path}`, so it MUST be the last thing main.py does.

    Asserted against main.py's source rather than against `app.routes`, because the catch-all
    is only installed when a built SPA is on disk — which it is in the container and is not in
    CI. A test that silently found no catch-all would pass for the rest of time while the
    routes it is guarding answered index.html in production.
    """
    from pathlib import Path

    import app.main

    source = Path(app.main.__file__).read_text(encoding="utf-8")

    assert source.index("include_router(reports.router)") < source.index("mount_spa(app)")
    assert source.rstrip().endswith("mount_spa(app)")


def test_generate_is_declared_ahead_of_the_id_route_so_it_can_never_be_read_as_an_id() -> None:
    """`generate` is not a UUID, so `/{report_id}` would 422 it rather than 404 it — a
    confusing answer to a route that exists. Declaring it first removes the question."""
    paths = [path for path, _ in _routes()]

    assert paths.index("/api/reports/generate") < paths.index("/api/reports/{report_id}")


def test_the_daily_limit_sentence_is_the_one_the_contract_promises() -> None:
    """Quoted to the family verbatim, so it is asserted rather than left to drift."""
    assert GENERATED_TODAY_DETAIL == "A report was already generated today."


def test_a_run_abandoned_by_a_redeploy_is_reported_as_failed_rather_than_polled_forever() -> None:
    """A `running` row nothing owns is indefinite, and the client's rule for a non-terminal
    status is to poll — so the panel would sit on "reading the record" for ever, every open tab
    asking every two seconds, for a summary that is not being written."""
    from app.routers.reports import STALE_RUN_AFTER, effective_status

    abandoned = NOW - STALE_RUN_AFTER - timedelta(seconds=1)

    assert effective_status("running", abandoned, NOW) == "failed"
    assert effective_status("pending", abandoned, NOW) == "failed"


def test_a_generation_still_in_flight_is_never_projected_as_failed() -> None:
    """`STALE_RUN_AFTER` is far past the tens of seconds a real call takes, on purpose."""
    from app.routers.reports import effective_status

    assert effective_status("running", NOW - timedelta(seconds=90), NOW) == "running"


def test_a_finished_report_is_reported_exactly_as_it_was_stored() -> None:
    """The projection is only ever about non-terminal rows: a complete report from last March
    must not become "failed" because it is old."""
    from app.routers.reports import effective_status

    assert effective_status("complete", NOW - timedelta(days=120), NOW) == "complete"


class _EmptyResult:
    """A query that matched nothing, which is what both tenancy tests below are asking about."""

    def scalar_one_or_none(self) -> None:
        return None

    def scalars(self) -> list[Any]:
        return []

    def first(self) -> None:
        return None


class _RecordingSession:
    """Enough `AsyncSession` to run a read handler and keep the statement it issued.

    A stub rather than a real session on purpose: the property under test is what the WHERE
    clause CONTAINS, and that is decided before a connection is involved. Asserting it here
    means the tenancy filter is covered on every run rather than only where Postgres is.
    """

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()


def _where(session: _RecordingSession) -> str:
    return str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))


def _ctx() -> Any:
    from app.deps import HouseholdCtx

    return HouseholdCtx(id=UUID(int=7), household=household())


async def test_another_households_report_is_a_404_identical_to_a_random_uuid() -> None:
    """A 403 would confirm the row exists somewhere, which is an existence oracle. The route
    cannot tell the two cases apart because it never asks: one query, both filters."""
    from app.errors import NOT_FOUND_DETAIL, NotFoundError
    from app.routers.reports import get_report

    session = _RecordingSession()
    with pytest.raises(NotFoundError) as caught:
        await get_report(session, _ctx(), uuid4())  # type: ignore[arg-type]

    assert caught.value.status_code == 404
    assert caught.value.detail == NOT_FOUND_DETAIL
    assert "reports.household_id" in _where(session)


async def test_listing_reports_filters_on_the_household_because_nothing_else_will() -> None:
    """There is no RLS behind this query. The `where` clause IS the tenant boundary."""
    from app.routers.reports import list_reports

    session = _RecordingSession()
    assert await list_reports(session, _ctx()) == []  # type: ignore[arg-type]
    assert "reports.household_id" in _where(session)


async def test_the_daily_limit_counts_from_midnight_in_the_households_own_timezone() -> None:
    """A family in Sydney pressing the button after breakfast must not be told they already
    had today's report because UTC still says yesterday."""
    from app.routers.reports import _already_generated_today

    session = _RecordingSession()
    # 22:00 UTC on the 25th is 08:00 on the 26th in Sydney, so "today" began at 14:00 UTC.
    await _already_generated_today(
        session,  # type: ignore[arg-type]
        UUID(int=7),
        timezone="Australia/Sydney",
        now=datetime(2026, 7, 25, 22, 0, tzinfo=UTC),
    )

    assert "'2026-07-25 14:00:00+00:00'" in _where(session)


async def test_a_failed_report_does_not_use_up_the_households_allowance_for_the_day() -> None:
    """A summary that died on a transport error cost the family nothing; being told to come
    back tomorrow because of it is the product punishing them for our own outage."""
    from app.routers.reports import _already_generated_today

    session = _RecordingSession()
    await _already_generated_today(
        session,  # type: ignore[arg-type]
        UUID(int=7),
        timezone="Europe/London",
        now=NOW,
    )

    assert "reports.status != 'failed'" in _where(session)


async def test_a_report_abandoned_by_a_dead_worker_stops_blocking_the_next_attempt() -> None:
    """Without this clause one crashed process locks a household out until midnight."""
    from app.routers.reports import STALE_RUN_AFTER, _already_generated_today

    session = _RecordingSession()
    await _already_generated_today(
        session,  # type: ignore[arg-type]
        UUID(int=7),
        timezone="Europe/London",
        now=NOW,
    )
    clause = _where(session)

    assert "IN ('pending', 'running')" in clause
    assert str((NOW - STALE_RUN_AFTER).isoformat(sep=" ")) in clause
