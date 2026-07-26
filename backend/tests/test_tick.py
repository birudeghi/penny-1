"""`POST /api/internal/tick` — the two things it must never get wrong.

The tick is the only path into extraction that no human triggers, which makes both of its
failure modes silent by construction.

**IT IS AN UNAUTHENTICATED-BY-DEFAULT DOOR ONTO THE OPENAI BILL.** No cookie, no HMAC over a
body, just a shared secret in a header — so the tests below are as interested in the calls that
must be refused as in the one that must work, and every refusal asserts that NOTHING RAN, not
merely that the status code was 401. A 401 with the work already done is the whole failure.

**ONE BAD HOUSEHOLD MUST NOT BE EVERY HOUSEHOLD'S PROBLEM.** The loop is a `for` over ids that
each open a session, call a model and commit. Before this endpoint existed nothing looped over
households at all, so nothing in the app had ever had to answer "what happens to household three
when household two raises?" — and the two wrong answers (a shared transaction that rolls them all
back, a shared `try` that ends the run) both look like a perfectly healthy 200.

NO DATABASE HERE, DELIBERATELY. What is under test is control flow: who is refused, what happens
after an exception, what the counts say. `households_due_for_extraction` and
`run_extraction_for_household` are stubbed at the module boundary and the sessionmaker is a
recording fake, so these run in CI without Postgres and a failure points at this file rather than
at a fixture. The DB-shaped half — that the bounded cursor really does drain a backlog across
repeated runs — is in `test_cadence.py`, against real Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.extraction.service import RunSummary
from app.routers import internal
from app.routers.internal import TICK_SECRET_HEADER

if TYPE_CHECKING:
    from app.config import Settings

SECRET = "a-real-tick-secret-not-the-published-default"


# --- plumbing ---------------------------------------------------------------------------


class _FakeSession:
    """As much of `AsyncSession` as the tick touches, and not one method more.

    It exists to record ORDER: a household whose run raised must have its transaction rolled
    back before the next household borrows a connection from the same pool, or asyncpg answers
    the next query with `InFailedSQLTransaction` several frames from anything that explains it.
    """

    def __init__(self, journal: list[str]) -> None:
        self.journal = journal

    async def __aenter__(self) -> _FakeSession:
        self.journal.append("open")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.journal.append("close")
        return False

    async def commit(self) -> None:
        self.journal.append("commit")

    async def rollback(self) -> None:
        self.journal.append("rollback")


@pytest.fixture
def journal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every session the tick opened, committed or rolled back, in order."""
    entries: list[str] = []
    monkeypatch.setattr(internal, "get_sessionmaker", lambda: lambda: _FakeSession(entries))
    return entries


class _SpyExtractor:
    """Stands in for `run_extraction_for_household`. Records ids; explodes for the ones told to.

    A call here is a household the tick decided to spend money on, so the list of calls is the
    assertion that matters — `ran=True` on a summary proves nothing about which households were
    reached.
    """

    def __init__(self, *, failing: set[UUID] | None = None, considered: int = 10) -> None:
        self.calls: list[UUID] = []
        self.failing = failing or set()
        self.considered = considered
        self.more_pending: set[UUID] = set()

    async def __call__(self, session: Any, household_id: UUID, **kwargs: Any) -> RunSummary:
        self.calls.append(household_id)
        if household_id in self.failing:
            raise RuntimeError("this household's timezone will not load")
        return RunSummary(
            household_id=household_id,
            ran=True,
            messages_pending=self.considered,
            messages_considered=self.considered,
            messages_unextracted=0,
            more_pending=household_id in self.more_pending,
        )


@pytest.fixture
def due(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    """The work list `households_due_for_extraction` would return. Mutable by each test."""
    households: list[UUID] = []

    async def fake_due(session: Any) -> list[UUID]:
        return list(households)

    monkeypatch.setattr(internal, "households_due_for_extraction", fake_due)
    return households


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _SpyExtractor]:
    """Install a spy in place of the real extraction, and hand it back for assertions."""

    def _install(**kwargs: Any) -> _SpyExtractor:
        spy = _SpyExtractor(**kwargs)
        monkeypatch.setattr(internal, "run_extraction_for_household", spy)
        return spy

    return _install


@pytest.fixture
def tick_client(
    client: TestClient, settings_override: Callable[..., Settings]
) -> Iterator[Callable[..., TestClient]]:
    """A client whose deployment has a tick secret configured, unless a test says otherwise."""

    def _make(**overrides: object) -> TestClient:
        settings_override(**{"env": "test", "internal_tick_secret": SECRET, **overrides})
        return client

    yield _make


def post_tick(client: TestClient, secret: str | None = SECRET) -> Any:
    headers = {} if secret is None else {TICK_SECRET_HEADER: secret}
    return client.post("/api/internal/tick", headers=headers)


# --- the secret -------------------------------------------------------------------------


def test_the_configured_secret_is_the_only_thing_that_makes_the_tick_run(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """The happy path, stated as a contract: the right secret, and the counts come back.

    The response body is asserted key by key because the caller is a `curl` in a container
    nobody watches — the only way a broken tick is ever noticed is a number in this body or a
    non-zero exit.
    """
    client = tick_client()
    due.extend([uuid.uuid4(), uuid.uuid4()])
    spy = extractor(considered=59)

    response = post_tick(client)

    assert response.status_code == 200
    assert response.json() == {
        "households_scanned": 2,
        "households_extracted": 2,
        "households_failed": 0,
        "households_remaining": 0,
        "messages_extracted": 118,
        "reports_generated": 0,
    }
    assert spy.calls == due


def test_a_wrong_secret_is_refused_before_any_household_is_touched(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """401, and — the half that matters — the work list is never even read.

    An endpoint that authenticated after doing the work would pass a status-code test and still
    let anyone on the internet spend this deployment's OpenAI budget at will.
    """
    client = tick_client()
    due.append(uuid.uuid4())
    spy = extractor()

    response = post_tick(client, secret="not-the-secret")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid tick secret."}
    assert spy.calls == []
    assert journal == []


def test_a_secret_that_is_a_prefix_of_the_real_one_is_refused(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
) -> None:
    """`compare_digest` over the whole string, not a `startswith` and not a truncated compare."""
    client = tick_client()
    spy = extractor()

    assert post_tick(client, secret=SECRET[:-1]).status_code == 401
    assert post_tick(client, secret=SECRET + "x").status_code == 401
    assert spy.calls == []


def test_a_missing_header_is_refused_rather_than_treated_as_an_internal_caller(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """The route is on the public internet: "no header" is a stranger, not the cron service.

    Reachability is the point — `/api/internal/` is a prefix, not a private network — so the
    absence of a credential can never be the same thing as having one.
    """
    client = tick_client()
    due.append(uuid.uuid4())
    spy = extractor()

    response = post_tick(client, secret=None)

    assert response.status_code == 401
    assert spy.calls == []
    assert journal == []


def test_an_unconfigured_deployment_refuses_every_caller_including_an_empty_one(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
) -> None:
    """INTERNAL_TICK_SECRET unset must fail CLOSED, not open.

    The tempting bug is a comparison that reads `configured == provided` and finds two empty
    strings agreeable, which turns "nobody set the secret" into "everybody knows the secret" on
    an endpoint that spends money. Production cannot boot in this state — `startup_checks` sees
    to that — but a dev container can, and a dev container with a public URL is a Friday.
    """
    client = tick_client(internal_tick_secret=None)
    due.append(uuid.uuid4())
    spy = extractor()

    assert post_tick(client, secret="").status_code == 401
    assert post_tick(client, secret=None).status_code == 401
    assert post_tick(client, secret=SECRET).status_code == 401
    assert spy.calls == []


# --- one household must not be able to end the run ---------------------------------------


def test_one_household_failing_does_not_stop_the_ones_after_it(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """THE RULE THIS ENDPOINT LIVES OR DIES BY, and the one no other code path has ever needed.

    Household two raises. Households three, four and five are strangers to it — different rows,
    different transactions, different families — and they must be extracted anyway. A shared
    `try` around the loop would leave every household after the failure permanently behind,
    hourly, while the tick kept answering 200 and the logs kept saying it ran.
    """
    client = tick_client()
    households = [uuid.uuid4() for _ in range(5)]
    due.extend(households)
    spy = extractor(failing={households[1]}, considered=4)

    response = post_tick(client)

    assert response.status_code == 200
    assert spy.calls == households, "every household after the failure must still be attempted"
    body = response.json()
    assert (body["households_scanned"], body["households_extracted"]) == (5, 4)
    assert body["households_failed"] == 1
    # 4 households x 4 messages. The one that raised contributes nothing, rather than the
    # messages it was about to consider.
    assert body["messages_extracted"] == 16


def test_a_failed_household_rolls_its_transaction_back_before_the_next_one_starts(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """A failed statement poisons the asyncpg transaction it happened in.

    Leaving it poisoned does not fail here — it fails in the NEXT household, as
    `InFailedSQLTransaction` on a query that has nothing wrong with it, which is how one bad row
    becomes an unreadable incident. So the rollback is asserted as an ordering, not as a call
    count: rolled back, closed, and only then does the next household open anything.
    """
    client = tick_client()
    first, second = uuid.uuid4(), uuid.uuid4()
    due.extend([first, second])
    extractor(failing={first})

    assert post_tick(client).status_code == 200

    # The first entry is the work-list session; then the failing household, then the healthy one.
    assert journal == [
        "open",
        "close",
        "open",
        "rollback",
        "close",
        "open",
        "commit",
        "close",
    ]


def test_every_household_gets_its_own_transaction_so_a_late_failure_keeps_early_work(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """Two commits for two households, not one commit for both.

    One transaction spanning the loop would be cheaper and would mean the fifth household's
    exception discards the events the first four already paid OpenAI for.
    """
    client = tick_client()
    due.extend([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    extractor()

    assert post_tick(client).status_code == 200

    assert journal.count("commit") == 3
    assert journal.count("rollback") == 0


# --- what the tick reports about work it did not finish ----------------------------------


def test_a_household_the_batch_cap_cut_short_is_reported_as_remaining(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    journal: list[str],
) -> None:
    """A truncated run is not a finished one, and the body has to say so.

    `extract_max_batch_messages` caps a run, so a household whose care profile just changed
    drains over several ticks. `households_remaining` staying above zero hour after hour is the
    only signal that one tick an hour has stopped being enough to keep up.
    """
    client = tick_client()
    backfilling, ordinary = uuid.uuid4(), uuid.uuid4()
    due.extend([backfilling, ordinary])
    spy = extractor()
    spy.more_pending.add(backfilling)

    body = post_tick(client).json()

    assert (body["households_extracted"], body["households_remaining"]) == (2, 1)


def test_running_out_of_time_leaves_the_rest_due_rather_than_dropping_them(
    tick_client: Callable[..., TestClient],
    due: list[UUID],
    extractor: Callable[..., _SpyExtractor],
    monkeypatch: pytest.MonkeyPatch,
    journal: list[str],
) -> None:
    """An hourly job that can outrun its own hour overlaps itself; this one stops instead.

    Nothing is lost by stopping: a household the loop never reached still has
    `extracted_at IS NULL` on its messages, so the same query returns it next hour. What must
    not happen is the tick reporting those households as scanned-and-done.
    """
    client = tick_client()
    due.extend([uuid.uuid4() for _ in range(3)])
    spy = extractor()
    monkeypatch.setattr(internal, "TICK_DEADLINE_SECONDS", 0.0)

    body = post_tick(client).json()

    assert spy.calls == []
    assert (body["households_scanned"], body["households_remaining"]) == (3, 3)
    assert body["households_extracted"] == 0
