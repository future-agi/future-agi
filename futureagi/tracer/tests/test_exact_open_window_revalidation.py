"""An open-window exact graph hit revalidates its own identity once it is old.

The Observe system-metric charts (``observe-system-graph``,
``observe-session-system-graph``, ``observe-user-system-graph``) and the Agent
Graph (``observe-agent-graph``, which reads the same toolbar window) serve a
cached exact snapshot immediately. When the snapshot's window was still open when it
was computed (window end later than ``completed_at``), spans that arrived since
are missing from it. Once such a hit is older than the revalidation floor, and
no refresh of the same identity is running or recently failed, the read claims
one refresh of the SAME identity and serves the hit marked
``query_refreshing``. It never carries a snapshot across windows, never marks a
hit refreshing without a claim, never takes the last per-project admission
slot, and never makes a Temporal status call.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.test import override_settings

from tracer.services import exact_aggregation_cache as eac
from tracer.services.clickhouse import graph_dispatch, session_graph
from tracer.services.clickhouse.graph_dispatch import (
    OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
)
from tracer.services.clickhouse.graph_metric_statistic import (
    snapshot_names_its_statistic,
)

pytestmark = pytest.mark.unit

PROJECT = str(uuid4())
ORG = str(uuid4())
SESSION_NS = "observe-session-system-graph"
AGENT_NS = "observe-agent-graph"
SCOPED = ["observe-system-graph", SESSION_NS, "observe-user-system-graph", AGENT_NS]
_VALUE = 42.5

_MODEL_FILTER = {
    "column_id": "model",
    "filter_config": {
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gpt-4",
        "col_type": "SYSTEM_METRIC",
    },
}


class _Analytics:
    supports_per_query_read_settings = True


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _window(*, end_offset: timedelta) -> dict:
    now = datetime.now(UTC)
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [_iso(now - timedelta(days=7)), _iso(now + end_offset)],
        },
    }


OPEN = timedelta(days=1)  # window ends tomorrow: still open
CLOSED = timedelta(hours=-1)  # window ended an hour ago


def _identity(*, end_offset: timedelta = OPEN, metric_id: str = "latency") -> dict:
    return {
        "project_id": PROJECT,
        "organization_id": ORG,
        "filters": [_window(end_offset=end_offset), _MODEL_FILTER],
        "interval": "day",
        "metric_id": metric_id,
        "payload_version": OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
    }


def _payload(metric_id: str = "latency", **extra) -> dict:
    return {
        "metric_name": metric_id,
        "data": [{"timestamp": "2026-06-01T00:00:00Z", "value": _VALUE}],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "metric_statistic": "mean",
        **extra,
    }


def _pending(metric_id: str = "latency") -> dict:
    return graph_dispatch._pending_graph_payload(metric_id)


def _seed(namespace: str, identity: dict, *, age: timedelta, payload=None) -> str:
    """Store a snapshot as a worker would have, ``age`` ago."""

    frozen = eac.normalize_exact_observe_identity(identity)
    completed_at = (datetime.now(UTC) - age).isoformat()
    cache.set(
        eac.snapshot_cache_key(namespace, frozen),
        {
            "v": eac._CACHE_VERSION,
            "completed_at": completed_at,
            "payload": payload if payload is not None else _payload(),
        },
        timeout=None,
    )
    return completed_at


OLD = timedelta(minutes=30)
YOUNG = timedelta(seconds=20)


def _read(namespace, identity, *, schedule_on_miss=True, revalidate=True, **extra):
    metric_id = identity.get("metric_id")
    return eac.read_or_schedule_exact_snapshot(
        namespace,
        identity,
        refresh=extra.pop("refresh", False),
        pending_payload=_pending(metric_id),
        schedule_on_miss=schedule_on_miss,
        accept_snapshot=lambda payload: snapshot_names_its_statistic(
            namespace, metric_id, payload
        ),
        revalidate_open_window=revalidate,
        **extra,
    )


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def queue(monkeypatch):
    """A configured queue whose worker accepted the job but has not published."""

    from tracer.tasks import exact_aggregation

    enqueued: list[dict] = []
    monkeypatch.setattr(
        eac, "_configured_exact_aggregation_task_queue", lambda: "tasks_xl"
    )

    def apply_async(**call):
        enqueued.append(call["kwargs"])
        return SimpleNamespace(id=f"workflow-{len(enqueued)}")

    monkeypatch.setattr(
        exact_aggregation.refresh_exact_aggregation_snapshot,
        "apply_async",
        apply_async,
    )
    return enqueued


@pytest.fixture
def temporal_status(monkeypatch):
    """Record every Temporal status call; revalidation must never make one."""

    from tfc.temporal.common import client

    calls: list[str] = []

    def status(workflow_id, **_kwargs):
        calls.append(workflow_id)
        return {"status_name": "RUNNING"}

    monkeypatch.setattr(client, "get_workflow_status_sync", status)
    return calls


@pytest.fixture
def no_carry(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("revalidation must never carry another window")

    monkeypatch.setattr(eac, "_carry_exact_snapshot_to_refreshed_identity", refuse)


# ---------------------------------------------------------------------------
# The rule, at the cache layer
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("no_carry")
@pytest.mark.parametrize("namespace", SCOPED)
def test_old_open_window_hit_claims_one_refresh_of_the_same_identity(
    namespace, queue, temporal_status
):
    identity = _identity()
    completed_at = _seed(namespace, identity, age=OLD)

    served = _read(namespace, identity)

    assert served["query_status"] == "complete"
    assert served["query_cached"] is True
    assert served["query_completed_at"] == completed_at
    assert served["query_refreshing"] is True
    assert served["query_refresh_failed"] is False
    assert [point["value"] for point in served["data"]] == [_VALUE]
    (job,) = queue
    assert job["namespace"] == namespace
    # The SAME frozen identity is refreshed: nothing moves to another window.
    assert job["identity"] == eac.normalize_exact_observe_identity(identity)
    assert eac.exact_refresh_state(namespace, job["identity"]) == "running"
    assert temporal_status == []


def test_young_open_window_hit_is_served_plain(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=YOUNG)

    served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is False
    assert queue == []


def test_closed_window_hit_never_revalidates(queue):
    # The window ended an hour ago and the snapshot was computed after that:
    # nothing can have arrived inside it since.
    identity = _identity(end_offset=CLOSED)
    _seed(SESSION_NS, identity, age=timedelta(minutes=30))

    served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is False
    assert queue == []


def test_running_refresh_is_polled_without_a_claim_or_temporal_call(
    queue, temporal_status
):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)
    frozen = eac.normalize_exact_observe_identity(identity)
    token = eac.begin_exact_refresh(SESSION_NS, frozen)
    assert token
    eac.record_exact_refresh_dispatch(SESSION_NS, frozen, token, "workflow-running")

    polls = [_read(SESSION_NS, identity) for _ in range(5)]

    assert all(poll["query_status"] == "complete" for poll in polls)
    assert all(poll["query_refreshing"] is True for poll in polls)
    assert queue == []
    assert temporal_status == []


def test_failed_refresh_state_is_the_backoff(queue, temporal_status):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)
    frozen = eac.normalize_exact_observe_identity(identity)
    token = eac.begin_exact_refresh(SESSION_NS, frozen)
    eac.finish_exact_refresh(SESSION_NS, frozen, token, succeeded=False)

    served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refresh_failed"] is True
    assert served["query_refreshing"] is False
    assert queue == []
    assert temporal_status == []
    # No new claim was taken while the failed state stands.
    assert cache.get(eac._refresh_lock_key(SESSION_NS, frozen)) is None


@pytest.mark.parametrize("marker", [{}, {"metric_statistic": "median"}])
def test_guard_rejected_hit_is_a_miss_not_a_revalidation(queue, marker):
    identity = _identity()
    payload = _payload()
    payload.pop("metric_statistic")
    payload.update(marker)
    _seed(SESSION_NS, identity, age=OLD, payload=payload)

    probed = _read(SESSION_NS, identity, schedule_on_miss=False)

    # A cache-only probe of a miss schedules nothing and serves no old value.
    assert probed["query_status"] == "pending"
    assert probed["data"] == []
    assert queue == []


@pytest.mark.parametrize(
    "namespace",
    ["dashboard-widget", "eval-usage", "attribute-detail"],
)
def test_namespaces_outside_the_three_graphs_never_revalidate(namespace, queue):
    identity = _identity()
    _seed(namespace, identity, age=OLD)

    served = eac.read_or_schedule_exact_snapshot(
        namespace,
        identity,
        refresh=False,
        pending_payload=_pending(),
        revalidate_open_window=True,
    )

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is False
    assert queue == []


def test_callers_that_do_not_opt_in_keep_the_plain_hit(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)

    served = eac.read_or_schedule_exact_snapshot(
        SESSION_NS,
        identity,
        refresh=False,
        pending_payload=_pending(),
    )

    assert served["query_refreshing"] is False
    assert queue == []


def test_cache_only_probe_schedules_when_it_marks_refreshing(queue):
    identity = _identity()
    _seed("observe-system-graph", identity, age=OLD)

    probed = _read("observe-system-graph", identity, schedule_on_miss=False)

    assert probed["query_status"] == "complete"
    assert probed["query_refreshing"] is True
    assert len(queue) == 1


def test_revalidation_never_takes_the_last_admission_slot(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)
    frozen = eac.normalize_exact_observe_identity(identity)
    # A user's fresh chart for the same project already holds one of the two
    # per-project slots; the remaining one stays free for foreground work.
    assert eac._claim_exact_refresh_admission(
        {**frozen, "metric_id": "tokens"}, "foreground", lease_seconds=600
    )

    served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is False
    assert served["query_refresh_failed"] is False
    assert queue == []
    assert eac.exact_refresh_state(SESSION_NS, frozen) is None
    assert cache.get(eac._refresh_lock_key(SESSION_NS, frozen)) is None


def test_full_scope_never_writes_a_running_state_for_a_refused_revalidation(
    monkeypatch, queue
):
    # Capacity is checked BEFORE the claim: a refused revalidation must not
    # write a "running" state, even for the few ms before the refusal, or a
    # concurrent poll reports refreshing for a job that never exists and an
    # explicit Reload in that window finds the claim taken and is dropped.
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)
    frozen = eac.normalize_exact_observe_identity(identity)
    assert eac._claim_exact_refresh_admission(
        {**frozen, "metric_id": "tokens"}, "foreground", lease_seconds=600
    )
    claims: list[str] = []
    real_begin = eac.begin_exact_refresh

    def spy(namespace, identity):
        claims.append(namespace)
        return real_begin(namespace, identity)

    monkeypatch.setattr(eac, "begin_exact_refresh", spy)

    served = _read(SESSION_NS, identity)

    assert served["query_refreshing"] is False
    assert claims == []
    assert queue == []

    # The explicit Reload right after still takes the free foreground slot.
    reloaded = _read(SESSION_NS, identity, refresh=True)
    assert reloaded["query_refreshing"] is True
    assert len(queue) == 1


def test_foreground_cold_miss_still_gets_the_slot_revalidation_left(queue):
    revalidated = _identity()
    _seed(SESSION_NS, revalidated, age=OLD)
    assert _read(SESSION_NS, revalidated)["query_refreshing"] is True

    cold = _identity(metric_id="tokens")
    pending = _read(SESSION_NS, cold)

    assert pending["query_status"] == "pending"
    assert pending["query_refreshing"] is True
    assert [job["identity"]["metric_id"] for job in queue] == ["latency", "tokens"]


def test_concurrent_revisits_claim_once(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)

    with ThreadPoolExecutor(max_workers=8) as pool:
        served = list(pool.map(lambda _i: _read(SESSION_NS, identity), range(16)))

    assert len(queue) == 1
    assert all(item["query_status"] == "complete" for item in served)


def test_no_task_queue_serves_the_plain_hit_not_a_failure(monkeypatch):
    monkeypatch.setattr(eac, "_configured_exact_aggregation_task_queue", lambda: None)
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)

    served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is False
    assert served["query_refresh_failed"] is False


def test_enqueue_failure_keeps_the_same_window_snapshot(monkeypatch):
    from tracer.tasks import exact_aggregation

    monkeypatch.setattr(
        eac, "_configured_exact_aggregation_task_queue", lambda: "tasks_xl"
    )

    def broken(**_call):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        exact_aggregation.refresh_exact_aggregation_snapshot, "apply_async", broken
    )
    identity = _identity()
    completed_at = _seed(SESSION_NS, identity, age=OLD)

    served = _read(SESSION_NS, identity)
    again = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_completed_at"] == completed_at
    # Nobody asked for this refresh: its failure is not shown as a failed
    # read above a valid chart. The hit is served plain, with its age.
    assert served["query_refresh_failed"] is False
    assert served["query_refreshing"] is False
    assert again["query_refresh_failed"] is False
    assert again["query_refreshing"] is False
    # The failed state is still the backoff: the next visit does not retry.
    frozen = eac.normalize_exact_observe_identity(identity)
    assert eac.exact_refresh_state(SESSION_NS, frozen) == "failed"
    assert cache.get(eac._refresh_lock_key(SESSION_NS, frozen)) is None
    stored = cache.get(eac.snapshot_cache_key(SESSION_NS, frozen))
    assert stored["completed_at"] == completed_at


@pytest.mark.parametrize("namespace", SCOPED)
def test_failed_automatic_revalidation_serves_the_hit_plain_to_every_viewer(
    namespace, queue, temporal_status
):
    identity = _identity()
    completed_at = _seed(namespace, identity, age=OLD)
    assert _read(namespace, identity)["query_refreshing"] is True
    (job,) = queue
    # The worker's exact read fails.
    eac.finish_exact_refresh(
        namespace, job["identity"], job["refresh_token"], succeeded=False
    )

    viewers = [_read(namespace, identity) for _ in range(5)]

    assert all(view["query_status"] == "complete" for view in viewers)
    assert all(view["query_completed_at"] == completed_at for view in viewers)
    assert all(view["query_refresh_failed"] is False for view in viewers)
    assert all(view["query_refreshing"] is False for view in viewers)
    # Backoff: no second claim while the failed state stands.
    assert len(queue) == 1
    assert eac.exact_refresh_state(namespace, job["identity"]) == "failed"
    assert temporal_status == []


def test_claimer_whose_own_revalidation_already_failed_gets_the_hit_plain(
    monkeypatch, temporal_status
):
    """A worker that fails before the claimer's re-read is still automatic.

    Temporal can accept the start and a fast (or eager) worker can fail the
    exact read before ``_revalidate_open_window_hit`` re-reads the state. The
    request that claimed the refresh must see the same plain hit every other
    viewer sees, not a "query failed" nobody asked for.
    """

    from tracer.tasks import exact_aggregation

    monkeypatch.setattr(
        eac, "_configured_exact_aggregation_task_queue", lambda: "tasks_xl"
    )
    enqueued: list[dict] = []

    def fails_before_returning(**call):
        job = call["kwargs"]
        enqueued.append(job)
        eac.finish_exact_refresh(
            job["namespace"], job["identity"], job["refresh_token"], succeeded=False
        )
        return SimpleNamespace(id="workflow-fast-failure")

    monkeypatch.setattr(
        exact_aggregation.refresh_exact_aggregation_snapshot,
        "apply_async",
        fails_before_returning,
    )
    identity = _identity()
    completed_at = _seed(SESSION_NS, identity, age=OLD)

    claimer = _read(SESSION_NS, identity)
    viewer = _read(SESSION_NS, identity)

    assert len(enqueued) == 1
    frozen = eac.normalize_exact_observe_identity(identity)
    assert eac.exact_refresh_state(SESSION_NS, frozen) == "failed"
    for served in (claimer, viewer):
        assert served["query_status"] == "complete"
        assert served["query_completed_at"] == completed_at
        assert served["query_refresh_failed"] is False
        assert served["query_refreshing"] is False
    assert temporal_status == []


class _TokenCacheFailure:
    """The Django cache, except that one operation on the revalidation token key raises."""

    def __init__(self, operation: str):
        self._operation = operation

    def __getattr__(self, name):
        real = getattr(cache, name)
        if name != self._operation:
            return real

        def failing(key, *args, **kwargs):
            if str(key).endswith(":revalidate-token"):
                raise ConnectionError("cache unavailable")
            return real(key, *args, **kwargs)

        return failing


@pytest.mark.parametrize(
    ("operation", "event"),
    [
        ("get", "exact_aggregation_revalidation_token_read_failed"),
        ("set", "exact_aggregation_revalidation_token_write_failed"),
    ],
)
def test_unknown_revalidation_provenance_reports_the_failure_and_logs(
    monkeypatch, queue, operation, event
):
    """Without its token a failed refresh may be the user's: it is shown."""

    import structlog

    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)
    monkeypatch.setattr(eac, "cache", _TokenCacheFailure(operation))
    with structlog.testing.capture_logs() as records:
        assert _read(SESSION_NS, identity)["query_refreshing"] is True
        (job,) = queue
        eac.finish_exact_refresh(
            SESSION_NS, job["identity"], job["refresh_token"], succeeded=False
        )
        served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refresh_failed"] is True
    assert any(
        record["event"] == event and record["log_level"] == "warning"
        for record in records
    )


def test_failed_explicit_refresh_still_reports_the_failure(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)
    # An automatic revalidation ran and succeeded earlier...
    assert _read(SESSION_NS, identity)["query_refreshing"] is True
    (auto,) = queue
    eac.finish_exact_refresh(
        SESSION_NS, auto["identity"], auto["refresh_token"], succeeded=True
    )
    # ...then the user's Reload fails: that failure is theirs to see.
    assert _read(SESSION_NS, identity, refresh=True)["query_refreshing"] is True
    explicit = queue[-1]
    assert explicit["refresh_token"] != auto["refresh_token"]
    eac.finish_exact_refresh(
        SESSION_NS, explicit["identity"], explicit["refresh_token"], succeeded=False
    )

    served = _read(SESSION_NS, identity)

    assert served["query_status"] == "complete"
    assert served["query_refresh_failed"] is True
    assert served["query_refreshing"] is False


@pytest.mark.parametrize("disabled", [0, -1, None])
def test_floor_setting_can_turn_revalidation_off(queue, disabled):
    identity = _identity()
    _seed(SESSION_NS, identity, age=OLD)

    with override_settings(EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS=disabled):
        served = _read(SESSION_NS, identity)

    assert served["query_refreshing"] is False
    assert queue == []


def test_floor_setting_is_honoured_and_clamped(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=timedelta(minutes=10))

    with override_settings(EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS=3600):
        assert _read(SESSION_NS, identity)["query_refreshing"] is False
    assert queue == []

    # A floor below the minimum is clamped up, so a 1 s setting cannot turn
    # every poll into a claim.
    young = _identity(metric_id="tokens")
    _seed(SESSION_NS, young, age=timedelta(seconds=5), payload=_payload("tokens"))
    with override_settings(EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS=1):
        assert _read(SESSION_NS, young)["query_refreshing"] is False
    assert queue == []


def test_default_floor_is_the_documented_five_minutes():
    assert eac._DEFAULT_REVALIDATE_AFTER_SECONDS == 300
    assert eac._revalidation_floor_seconds() == 300


def test_floor_is_a_registered_runtime_setting_documented_for_operators():
    # Declared once in runtime_setting_specs.py like every numeric knob, so
    # a blank value means the default (not a crash at settings import) and an
    # out-of-range one is refused with a named error.
    from pathlib import Path

    from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

    spec = RUNTIME_NUMERIC_SETTING_SPECS["EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS"]
    name = "EXACT_AGGREGATION_REVALIDATE_AFTER_SECONDS"
    assert spec.parse(name) == eac._DEFAULT_REVALIDATE_AFTER_SECONDS
    assert spec.parse(name, "") == eac._DEFAULT_REVALIDATE_AFTER_SECONDS
    assert spec.parse(name, "  ") == eac._DEFAULT_REVALIDATE_AFTER_SECONDS
    assert spec.parse(name, "0") == 0  # off
    with pytest.raises(ValueError, match=name):
        spec.parse(name, "-1")
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    assert f"\n{name}=300\n" in env_example.read_text()


def test_explicit_refresh_is_unchanged(queue):
    identity = _identity()
    _seed(SESSION_NS, identity, age=YOUNG)

    served = _read(SESSION_NS, identity, refresh=True)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is True
    assert len(queue) == 1


# ---------------------------------------------------------------------------
# The three graph entry points
# ---------------------------------------------------------------------------


def _unaffordable(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_raw_graph_seed",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_user_graph_read",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )


def _trace(filters, refresh):
    return graph_dispatch.fetch_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="latency",
        organization_id=ORG,
        refresh=refresh,
    )


def _users(filters, refresh):
    return graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="latency",
        organization_id=ORG,
        refresh=refresh,
    )


def _sessions(filters, refresh):
    return session_graph.fetch_session_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=filters,
        interval="day",
        req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
        organization_id=ORG,
        refresh=refresh,
    )


_ENTRY_POINTS = [
    pytest.param("observe-system-graph", _trace, id="trace"),
    pytest.param("observe-user-system-graph", _users, id="users"),
    pytest.param(SESSION_NS, _sessions, id="sessions"),
]


def _entry_identity(namespace: str, filters: list[dict]) -> dict:
    identity = {
        "project_id": PROJECT,
        "filters": filters,
        "interval": "day",
        "metric_id": "latency",
        "payload_version": OBSERVE_SYSTEM_GRAPH_PAYLOAD_VERSION,
        "organization_id": ORG,
    }
    if namespace == "observe-system-graph":
        identity["observe_type"] = "trace"
    return identity


@pytest.mark.usefixtures("no_carry")
@pytest.mark.parametrize(("namespace", "fetch"), _ENTRY_POINTS)
def test_entry_point_revalidates_an_old_open_hit_once(
    monkeypatch, queue, temporal_status, namespace, fetch
):
    _unaffordable(monkeypatch)
    filters = [_window(end_offset=OPEN), _MODEL_FILTER]
    completed_at = _seed(namespace, _entry_identity(namespace, filters), age=OLD)

    first = fetch(filters, False)
    poll = fetch(filters, False)

    assert first["query_status"] == "complete"
    assert first["query_completed_at"] == completed_at
    assert first["query_refreshing"] is True
    assert poll["query_refreshing"] is True
    assert [job["namespace"] for job in queue] == [namespace]
    assert temporal_status == []


@pytest.mark.parametrize(("namespace", "fetch"), _ENTRY_POINTS)
def test_entry_point_serves_the_hit_plain_after_its_revalidation_fails(
    monkeypatch, queue, temporal_status, namespace, fetch
):
    _unaffordable(monkeypatch)
    filters = [_window(end_offset=OPEN), _MODEL_FILTER]
    completed_at = _seed(namespace, _entry_identity(namespace, filters), age=OLD)
    assert fetch(filters, False)["query_refreshing"] is True
    (job,) = queue
    eac.finish_exact_refresh(
        namespace, job["identity"], job["refresh_token"], succeeded=False
    )

    served = fetch(filters, False)

    assert served["query_status"] == "complete"
    assert served["query_completed_at"] == completed_at
    assert served["query_refresh_failed"] is False
    assert served["query_refreshing"] is False
    assert len(queue) == 1
    assert temporal_status == []


@pytest.mark.parametrize(("namespace", "fetch"), _ENTRY_POINTS)
def test_entry_point_serves_a_young_hit_plain(monkeypatch, queue, namespace, fetch):
    _unaffordable(monkeypatch)
    filters = [_window(end_offset=OPEN), _MODEL_FILTER]
    _seed(namespace, _entry_identity(namespace, filters), age=YOUNG)

    served = fetch(filters, False)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is False
    assert queue == []


@pytest.mark.parametrize(("namespace", "fetch"), _ENTRY_POINTS)
def test_explicit_refresh_of_an_old_hit_enqueues_exactly_once(
    monkeypatch, queue, temporal_status, namespace, fetch
):
    # The probe must not revalidate ahead of the explicit refresh: the refresh
    # call would then find its own claim and reconcile it against Temporal.
    _unaffordable(monkeypatch)
    filters = [_window(end_offset=OPEN), _MODEL_FILTER]
    _seed(namespace, _entry_identity(namespace, filters), age=OLD)

    served = fetch(filters, True)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is True
    assert len(queue) == 1
    assert temporal_status == []


# ---------------------------------------------------------------------------
# Agent Graph shares the toolbar window, so it shares the rule
# ---------------------------------------------------------------------------


def _agent_body() -> dict:
    return {
        "nodes": [{"id": "agent"}],
        "edges": [],
        "path_edges": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }


def _agent_graph(filters, refresh):
    return graph_dispatch.fetch_agent_graph_ch(
        project_id=PROJECT,
        filters=filters,
        refresh=refresh,
        organization_id=ORG,
    )


def _seed_agent_graph(filters, *, age):
    from tracer.services.clickhouse.graph_dispatch import AGENT_GRAPH_PAYLOAD_VERSION

    return _seed(
        AGENT_NS,
        {
            "project_id": PROJECT,
            "filters": filters,
            "payload_version": AGENT_GRAPH_PAYLOAD_VERSION,
            "organization_id": ORG,
        },
        age=age,
        payload=_agent_body(),
    )


@pytest.mark.usefixtures("no_carry")
def test_agent_graph_revalidates_an_old_open_hit_once(queue, temporal_status):
    # The toolbar window is hour-stable, so a revisit replays the same Agent
    # Graph identity; an old open-window hit must not be served as current.
    filters = [_window(end_offset=OPEN)]
    completed_at = _seed_agent_graph(filters, age=OLD)

    first = _agent_graph(filters, False)
    poll = _agent_graph(filters, False)

    assert first["query_status"] == "complete"
    assert first["query_completed_at"] == completed_at
    assert first["nodes"] == [{"id": "agent"}]
    assert first["query_refreshing"] is True
    assert poll["query_refreshing"] is True
    assert [job["namespace"] for job in queue] == [AGENT_NS]
    assert temporal_status == []


def test_agent_graph_young_or_closed_hit_is_served_plain(queue):
    young = [_window(end_offset=OPEN)]
    _seed_agent_graph(young, age=YOUNG)
    closed = [_window(end_offset=CLOSED)]
    _seed_agent_graph(closed, age=OLD)

    assert _agent_graph(young, False)["query_refreshing"] is False
    assert _agent_graph(closed, False)["query_refreshing"] is False
    assert queue == []


def test_agent_graph_explicit_refresh_enqueues_exactly_once(queue, temporal_status):
    filters = [_window(end_offset=OPEN)]
    _seed_agent_graph(filters, age=OLD)

    served = _agent_graph(filters, True)

    assert served["query_status"] == "complete"
    assert served["query_refreshing"] is True
    assert len(queue) == 1
    assert temporal_status == []
