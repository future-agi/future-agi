"""PG-free settle state-machine and indexed queue contract tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.derived_metrics.changes import (
    MetricChangeReadError,
    MetricReadBudget,
    UnitKey,
    ns,
)
from tracer.services.derived_metrics.settle import (
    UnitSnapshot,
    UnitState,
    evaluate_settle,
    settle_due_page,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
KEY = UnitKey("conversation", "session-a")
ARRIVAL = NOW - timedelta(minutes=30)
ORG, PROJECT, ATTEMPT = (str(uuid4()) for _ in range(3))


def snapshot(*, key=KEY, arrival=ARRIVAL, **kwargs):
    return UnitSnapshot(key, ns(arrival), NOW - timedelta(days=30), **kwargs)


def evaluate(value=None, *, previous=None, now=NOW, minutes=30):
    return evaluate_settle(
        value or snapshot(), previous=previous, now=now, settle_minutes=minutes
    )


@pytest.mark.parametrize(
    "seconds,expected", [(-1, "open"), (0, "settled"), (1, "settled")]
)
def test_idle_threshold_is_inclusive(seconds, expected):
    result = evaluate(now=NOW + timedelta(seconds=seconds))
    assert result.status == expected
    assert result.state.is_deleted == (expected == "settled")
    assert result.state.due_at == NOW
    assert result.settled_at == (
        NOW + timedelta(seconds=seconds) if expected == "settled" else None
    )


@pytest.mark.parametrize("ended", [False, True])
def test_trace_requires_ended_root_even_after_a_long_outage(ended):
    value = snapshot(key=UnitKey("trace", "trace-a"), root_ended=ended)
    result = evaluate(value, now=NOW + timedelta(days=4))
    assert result.status == ("settled" if ended else "open")


@pytest.mark.parametrize("ended", [False, True])
def test_span_settles_on_end_not_idle_timer(ended):
    result = evaluate(
        snapshot(key=UnitKey("span", "span-a"), arrival=NOW, span_ended=ended)
    )
    assert result.status == ("settled" if ended else "open")


def test_renewed_arrival_prevents_premature_settle_and_moves_due():
    previous = evaluate(now=NOW - timedelta(seconds=1)).state
    result = evaluate(snapshot(arrival=NOW), previous=previous)
    assert result.status == "open"
    assert result.state.last_arrival_ns == ns(NOW)
    assert result.state.due_at == NOW + timedelta(minutes=30)


def test_reopen_resettle_and_repeated_ticks_do_not_resurrect_tombstone():
    settled = evaluate().state
    assert evaluate(previous=settled, now=NOW + timedelta(days=7)) is None
    reopened = evaluate(snapshot(arrival=NOW), previous=settled)
    assert reopened.status == "open" and reopened.state.reopened
    assert not reopened.state.is_deleted
    assert evaluate(snapshot(arrival=NOW), previous=reopened.state) is None
    resettled = evaluate(
        snapshot(arrival=NOW), previous=reopened.state, now=NOW + timedelta(minutes=30)
    )
    assert resettled.status == "settled" and resettled.state.reopened
    assert (
        evaluate(
            snapshot(arrival=NOW),
            previous=resettled.state,
            now=NOW + timedelta(days=10),
        )
        is None
    )


def test_missing_source_publishes_queue_tombstone_and_value_deletion():
    opened = evaluate(snapshot(arrival=NOW)).state
    result = evaluate(snapshot(exists=False), previous=opened)
    assert result.status == "deleted" and result.state.is_deleted
    assert result.state.last_arrival_ns == opened.last_arrival_ns
    assert result.settled_at is None
    assert evaluate(snapshot(exists=False), previous=result.state) is None


def test_lower_source_arrival_keeps_the_committed_arrival_as_a_floor():
    # A lagging replica (or a deleted newest span) reports an older arrival.
    # The committed arrival still decides due_at; nothing raises or settles early.
    previous = evaluate(snapshot(arrival=NOW)).state
    assert evaluate(snapshot(), previous=previous) is None
    later = evaluate(snapshot(), previous=previous, now=NOW + timedelta(minutes=29))
    assert later is None
    due = evaluate(snapshot(), previous=previous, now=NOW + timedelta(minutes=30))
    assert due.status == "settled"
    assert due.state.last_arrival_ns == ns(NOW)
    assert due.state.due_at == NOW + timedelta(minutes=30)


def test_deleting_the_newest_span_does_not_stall_or_reopen_a_unit():
    newest = NOW - timedelta(minutes=5)
    opened = evaluate(snapshot(arrival=newest)).state
    assert opened.due_at == newest + timedelta(minutes=30)
    # The writer's live-span arrival now comes from the older span only.
    after_delete = snapshot(arrival=NOW - timedelta(hours=2))
    assert evaluate(after_delete, previous=opened) is None
    settled = evaluate(
        after_delete, previous=opened, now=newest + timedelta(minutes=30)
    )
    assert settled.status == "settled" and not settled.state.reopened
    assert (
        evaluate(after_delete, previous=settled.state, now=NOW + timedelta(days=2))
        is None
    )


@pytest.mark.parametrize("minutes", [0, -1, 1441])
def test_invalid_settle_minutes_rejected(minutes):
    with pytest.raises(ValueError):
        evaluate(minutes=minutes)


def test_mismatched_source_key_rejected():
    previous = evaluate().state
    with pytest.raises(ValueError, match="keys differ"):
        evaluate(snapshot(key=UnitKey("conversation", "other")), previous=previous)


class QueueAnalytics:
    supports_per_query_read_settings = True

    def __init__(self, nominees, states):
        self.nominees, self.states = nominees, states
        self.calls = []

    def execute_ch_query(self, query, params, **kwargs):
        self.calls.append((query, params, kwargs))
        if "argMax" not in query:
            after = (
                (params["due_after_0"], params["due_after_1"])
                if "due_after_0" in params
                else None
            )
            keys = [
                k
                for k in self.nominees
                if after is None or (k.level, k.unit_id) > after
            ]
            rows = [
                {"level": k.level, "unit_id": k.unit_id}
                for k in keys[: params["page_limit"]]
            ]
        else:
            rows = [
                {**s.payload(), "last_arrival_ns": s.last_arrival_ns}
                for s in self.states
                if (s.key.level, s.key.unit_id) in params["unit_keys"]
            ]
        return SimpleNamespace(data=rows)


def due(analytics, **kwargs):
    return settle_due_page(
        analytics,
        org_id=ORG,
        project_id=PROJECT,
        committed_attempts=(ATTEMPT,),
        now=NOW,
        settle_minutes=30,
        **kwargs,
    )


def state(key=KEY, **kwargs):
    return UnitState(key, ns(ARRIVAL), NOW - timedelta(days=30), NOW, **kwargs)


def test_raw_superset_is_verified_then_latest_source_is_rechecked():
    stale, renewed, excluded = (UnitKey("conversation", v) for v in ("b", "c", "d"))
    analytics = QueueAnalytics(
        [KEY, stale, renewed, excluded],
        [
            state(),
            state(stale, is_deleted=True),
            replace(state(renewed), due_at=NOW + timedelta(minutes=1)),
        ],
    )
    seen = []

    def load(keys, budget):
        seen.extend(keys)
        return {KEY: snapshot()}

    page = due(analytics, load_latest=load)
    assert seen == [KEY]
    assert page.nominated == 4 and page.verified == 1
    assert [u.status for u in page.updates] == ["settled"]
    nominate, collapse = (call[0] for call in analytics.calls)
    assert "is_deleted = 0 AND due_at <=" in nominate
    assert "due_at >=" not in nominate and "FINAL" not in nominate
    assert "DISTINCT" not in nominate and "ORDER BY level, unit_id" in nominate
    assert analytics.calls[0][2]["settings"]["max_block_size"] == 1000
    assert "attempt_id IN" in collapse and "argMax" in collapse
    assert "is_deleted = 0" not in collapse and "due_at <=" not in collapse
    assert analytics.calls[1][1]["committed_attempts"] == (ATTEMPT,)


def test_fresh_arrival_from_loader_reschedules_a_verified_due_unit():
    analytics = QueueAnalytics([KEY], [state()])
    page = due(analytics, load_latest=lambda keys, budget: {KEY: snapshot(arrival=NOW)})
    assert page.updates[0].status == "open"
    assert page.updates[0].state.due_at == NOW + timedelta(minutes=30)


def test_all_overdue_units_are_drained_after_outage_in_finite_pages():
    keys = [UnitKey("conversation", f"unit-{i}") for i in range(7)]
    states = [replace(state(k), due_at=NOW - timedelta(days=9)) for k in keys]
    analytics = QueueAnalytics(keys, states)
    after, settled, pages = None, [], 0
    budget = MetricReadBudget()
    while True:
        page = due(
            analytics,
            load_latest=lambda ids, budget: {k: snapshot(key=k) for k in ids},
            after=after,
            page_size=2,
            budget=budget,
        )
        settled.extend(u.state.key for u in page.updates)
        pages += 1
        if page.exhausted:
            break
        after = page.after
    assert pages == 4 and settled == keys
    assert budget.attempts == 8
    # Later pages use the key-prunable expanded keyset, not a tuple compare.
    later = [q for q, p, _ in analytics.calls if "due_after_0" in p]
    assert later and all(
        "(level > %(due_after_0)s OR (level = %(due_after_0)s AND unit_id > "
        "%(due_after_1)s))" in q
        for q in later
    )
    tombstones = [replace(s, is_deleted=True) for s in states]
    again = QueueAnalytics(keys, tombstones)
    assert (
        due(
            again,
            load_latest=lambda ids, budget: pytest.fail("must not load tombstones"),
        ).updates
        == ()
    )


@pytest.mark.parametrize("failure", ["missing", "wrong_key"])
def test_loader_must_prove_every_due_unit(failure):
    analytics = QueueAnalytics([KEY], [state()])
    result = (
        {}
        if failure == "missing"
        else {KEY: snapshot(key=UnitKey("conversation", "wrong"))}
    )
    with pytest.raises(MetricChangeReadError):
        due(analytics, load_latest=lambda keys, budget: result)


def test_late_read_failure_returns_no_partial_settles():
    analytics = QueueAnalytics([KEY], [state()])
    with pytest.raises(MetricChangeReadError, match="query budget"):
        due(
            analytics,
            load_latest=lambda keys, budget: {KEY: snapshot()},
            budget=MetricReadBudget(max_queries=1),
        )


def test_payload_contains_only_storage_lifecycle_fields():
    result = evaluate().state.payload()
    assert set(result) == {
        "level",
        "unit_id",
        "last_arrival_ns",
        "first_seen",
        "due_at",
        "reopened",
        "is_deleted",
    }
    assert result["is_deleted"] is True


def test_source_loader_consumes_the_same_budget_and_deadline():
    analytics = QueueAnalytics([KEY], [state()])
    shared = MetricReadBudget()

    def loader(keys, budget):
        assert budget is shared and budget.attempts == 2
        budget.deadline = 0
        return {KEY: snapshot()}

    with pytest.raises(MetricChangeReadError, match="source deadline"):
        due(analytics, load_latest=loader, budget=shared)


def test_duplicate_generation_rows_count_as_one_nominee():
    analytics = QueueAnalytics([KEY, KEY, KEY], [state()])
    page = due(analytics, load_latest=lambda keys, budget: {KEY: snapshot()})
    assert page.nominated == 1 and page.verified == 1
    assert [u.status for u in page.updates] == ["settled"]


def test_committed_states_read_only_the_pinned_allowlist_in_bounded_batches():
    from tracer.services.derived_metrics import changes
    from tracer.services.derived_metrics.settle import committed_unit_states

    keys = [UnitKey("conversation", f"u-{i:04d}") for i in range(changes.KEY_BATCH + 3)]
    analytics = QueueAnalytics([], [state(k) for k in keys])
    result = committed_unit_states(
        analytics,
        org_id=ORG,
        project_id=PROJECT,
        committed_attempts=(ATTEMPT,),
        keys=keys,
        budget=MetricReadBudget(),
    )
    assert set(result) == set(keys)
    assert [len(p["unit_keys"]) for _, p, _ in analytics.calls] == [
        changes.KEY_BATCH,
        3,
    ]
    assert (
        committed_unit_states(
            analytics,
            org_id=ORG,
            project_id=PROJECT,
            committed_attempts=(),
            keys=keys,
            budget=MetricReadBudget(),
        )
        == {}
    )
