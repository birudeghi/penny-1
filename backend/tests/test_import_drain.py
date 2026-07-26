"""Draining an import across bounded batches, and knowing when to stop trying.

`extract_max_batch_messages` caps what ONE extraction run reads, which turns a six-month export
into several passes. The tick can afford to take those passes an hour apart because nobody is
watching a backfill; an import cannot, because a family is watching a progress bar that reads
`extracted_count / inserted_count` and an import that is genuinely working must not look stuck
for a day. So `_drain` comes straight round again — and the interesting question is not that it
loops, it is when it STOPS:

    the cursor is empty                    -> done, the import is complete
    the spend ceiling aborted the run      -> stop; the tick picks the remainder up later
    a pass got nothing through             -> stop; the next pass would buy the same failure
    MAX_DRAIN_BATCHES passes               -> stop; a backlog that will not shrink is not ours

Every one of those leaves the messages `extracted_at IS NULL`, which is the entire retry
mechanism, and every one of them must leave the import in "extracting" rather than "complete" —
a finished import over an empty feed is the failure with no error message anywhere.

No database: what is under test is the loop, and the transaction it opens per pass is a
recording fake. The cursor behaviour it depends on has its own tests against real Postgres in
`test_cadence.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.extraction.service import RunSummary
from app.routers import imports


class _FakeSession:
    """Enough of `AsyncSession` for one pass. Commits are counted; nothing else happens."""

    def __init__(self, journal: list[str]) -> None:
        self.journal = journal

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        self.journal.append("commit")


class _ScriptedExtractor:
    """Returns a prepared summary per pass, then repeats the last one forever.

    Repeating rather than running out is deliberate: the ceiling tests need a household that
    keeps insisting there is more to do, which is exactly the shape `MAX_DRAIN_BATCHES` exists
    to survive.
    """

    def __init__(self, script: list[RunSummary]) -> None:
        self.script = script
        self.calls = 0

    async def __call__(self, session: Any, household_id: uuid.UUID, **kwargs: Any) -> RunSummary:
        self.calls += 1
        return self.script[min(self.calls - 1, len(self.script) - 1)]


def summary(**kwargs: Any) -> RunSummary:
    """A successful run, with only the fields a stopping rule reads spelled out."""
    return RunSummary(
        household_id=uuid.uuid4(),
        ran=True,
        messages_considered=kwargs.pop("considered", 100),
        messages_unextracted=kwargs.pop("unextracted", 0),
        messages_stamped=kwargs.pop("stamped", 100),
        **kwargs,
    )


@pytest.fixture
def maker() -> Any:
    """A sessionmaker whose sessions do nothing but record their commits."""
    journal: list[str] = []

    def _make() -> _FakeSession:
        return _FakeSession(journal)

    _make.journal = journal  # type: ignore[attr-defined]
    return _make


async def drain(maker: Any, extractor: _ScriptedExtractor, monkeypatch: Any) -> list[RunSummary]:
    monkeypatch.setattr(imports, "run_extraction_for_household", extractor)
    return await imports._drain(maker, uuid.uuid4(), Decimal("25"))


async def test_an_import_keeps_going_until_the_cursor_is_empty(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three capped passes and a fourth that finds nothing left. One commit each.

    Without the loop this import would extract its first 2,400 messages, report itself finished,
    and leave the rest of the family's history to an hourly tick — a six-month export would take
    most of a day to appear, with a progress bar that said it was done.
    """
    extractor = _ScriptedExtractor(
        [
            summary(more_pending=True),
            summary(more_pending=True),
            summary(more_pending=True),
            summary(more_pending=False, considered=40, stamped=40),
        ]
    )

    passes = await drain(maker, extractor, monkeypatch)

    assert extractor.calls == 4
    assert [p.more_pending for p in passes] == [True, True, True, False]
    assert maker.journal == ["commit"] * 4, "each pass must commit on its own"


async def test_a_pass_that_got_nothing_through_stops_rather_than_buying_it_again(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE PAID INFINITE LOOP THIS GUARD EXISTS TO PREVENT.

    A chunk whose call errors or whose response will not parse leaves its messages
    `extracted_at IS NULL` — so the very next pass selects the SAME rows, in the same order, and
    sends them to the same model. Looping on that is forty paid attempts at one poisonous chunk
    inside a single import job. Once nothing is moving, the hourly tick is the right retry:
    slower, and with whatever broke given time to stop being broken.
    """
    extractor = _ScriptedExtractor(
        [
            summary(more_pending=True),
            summary(more_pending=True, considered=100, unextracted=100, stamped=0),
        ]
    )

    passes = await drain(maker, extractor, monkeypatch)

    assert extractor.calls == 2
    assert passes[-1].messages_unextracted == 100


async def test_a_spend_abort_ends_the_import_job_and_leaves_the_rest_to_the_tick(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`IMPORT_MAX_SPEND_USD` aborts mid-flight, and coming straight round again would spend
    past a ceiling the family was quoted before they pressed the button."""
    extractor = _ScriptedExtractor(
        [summary(more_pending=True), summary(more_pending=True, aborted_reason="spend_cap")]
    )

    passes = await drain(maker, extractor, monkeypatch)

    assert extractor.calls == 2
    assert passes[-1].aborted_reason == "spend_cap"


async def test_a_backlog_that_never_shrinks_cannot_pin_the_job_for_ever(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling. A household that reports progress AND more work on every single pass is not
    a case anyone expects; it is a case that must end anyway, with the import left "extracting"
    so the tick keeps at it."""
    extractor = _ScriptedExtractor([summary(more_pending=True)])

    passes = await drain(maker, extractor, monkeypatch)

    assert extractor.calls == imports.MAX_DRAIN_BATCHES
    assert passes[-1].more_pending is True


async def test_losing_the_advisory_lock_is_retried_and_then_reported_honestly(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ran=False` means a webhook or the tick is extracting these messages right now.

    It is worth waiting for — that run started before this import's messages landed, so it may
    not cover them — but not forever, and an import that gave up waiting must not claim to have
    finished. `passes[-1].ran` is False, which is what keeps it "extracting".
    """
    monkeypatch.setattr(imports, "LOCK_RETRY_SECONDS", 0)
    extractor = _ScriptedExtractor([RunSummary(household_id=uuid.uuid4(), ran=False)])

    passes = await drain(maker, extractor, monkeypatch)

    assert extractor.calls == imports.LOCK_RETRIES
    assert len(passes) == 1
    assert passes[-1].ran is False
