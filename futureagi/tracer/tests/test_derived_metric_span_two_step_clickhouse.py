# ruff: noqa: F401, F811 -- shared isolated native fixtures
"""Revision-complete closure diagnostics; no production snapshot qualification."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.span_two_step import diagnostic_span_closure
from tracer.services.derived_metrics.unit_reads import _source_plan
from tracer.tests.test_derived_metric_clickhouse import metric_ch
from tracer.tests.test_derived_metric_publication_clickhouse import publication_store
from tracer.tests.test_derived_metric_unit_reads_clickhouse import seed_typed


def winners(client, scope, request, ceiling, *, plan=None, predicate="1"):
    ctes = "WITH " + ", ".join(plan.ctes) if plan else ""
    params = {**scope.parameters(), "request": str(request), "ceiling": ceiling}
    if plan:
        params.update(plan.parameters)
        predicate = plan.predicate
    return client.execute(
        ctes + " SELECT trace_id, id, start_time, _version, is_deleted FROM ("
        "SELECT trace_id, id, trace_session_id, start_time, _version, is_deleted FROM spans "
        "PREWHERE project_id = %(project_id)s AND " + predicate + " "
        "WHERE (org_id = %(org_id)s OR isNull(org_id)) AND created_at <= %(ceiling)s AND updated_at <= %(ceiling)s "
        "ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id) "
        "WHERE trace_session_id = toUUID(%(request)s) AND is_deleted = 0 ORDER BY trace_id, id",
        params,
    )


@pytest.mark.parametrize("days", [0, 1, 32])
@pytest.mark.parametrize("deleted", [0, 1])
def test_session_and_hour_move_never_resurrects_old_revision(metric_ch, days, deleted):
    scope = Scope(uuid4(), uuid4(), uuid4())
    first, other, trace = uuid4(), uuid4(), uuid4()
    h1 = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
    h2 = h1 + timedelta(days=days, hours=2)
    seed_typed(metric_ch, scope, first, h1, trace=trace, version=1)
    seed_typed(metric_ch, scope, other, h2, trace=trace, version=2, deleted=deleted)
    plan = diagnostic_span_closure(scope, [str(first)])
    assert (
        winners(metric_ch, scope, first, h2 + timedelta(days=1), plan=plan)
        == winners(metric_ch, scope, first, h2 + timedelta(days=1))
        == []
    )
    hours = metric_ch.execute(
        "WITH "
        + ", ".join(plan.ctes)
        + " SELECT source_hour FROM closure_hours ORDER BY source_hour",
        plan.parameters,
    )
    assert len(hours) == 2
    # This incorrect session-only-hour shortcut would resurrect the first row.
    unsafe = "toStartOfHour(start_time) IN (SELECT toStartOfHour(start_time) FROM spans WHERE project_id = %(project_id)s AND trace_session_id = toUUID(%(request)s))"
    assert (
        len(winners(metric_ch, scope, first, h2 + timedelta(days=1), predicate=unsafe))
        == 1
    )
    assert not plan.production_qualified


def test_explicit_identity_closes_over_all_hours_and_duplicate_span_ids(metric_ch):
    scope = Scope(uuid4(), uuid4(), uuid4())
    requested, unrelated = uuid4(), uuid4()
    trace, foreign_trace = uuid4(), uuid4()
    now = datetime(2026, 10, 1, tzinfo=UTC)
    seed_typed(metric_ch, scope, requested, now, trace=trace, span_id="same", version=1)
    seed_typed(
        metric_ch,
        scope,
        unrelated,
        now + timedelta(hours=3),
        trace=foreign_trace,
        span_id="same",
        version=10,
    )
    plan = diagnostic_span_closure(
        scope, [str(requested)], source_identities=[(trace, "same")]
    )
    result = winners(metric_ch, scope, requested, now + timedelta(days=1), plan=plan)
    assert result == winners(metric_ch, scope, requested, now + timedelta(days=1))
    assert len(result) == 1 and result[0][0] == str(trace)


def test_nomination_is_projection_covered_and_closure_is_not_session_filtered():
    scope = Scope(uuid4(), uuid4(), uuid4())
    plan = diagnostic_span_closure(scope, [str(uuid4())])
    nomination, closure = plan.ctes[-2:]
    assert "SELECT trace_id FROM spans" in nomination
    assert all(
        column not in nomination
        for column in ["org_id", "_version", "updated_at", "service_name", "is_deleted"]
    )
    assert "trace_session_id" not in closure and "source_hour" in closure
    assert "toDate(start_time)" in plan.predicate
    # The production planner still nominates immutable pairs and never uses the
    # unqualified physical-hour restriction, even when the projection exists.
    _, predicate, _ = _source_plan(scope, (str(uuid4()),), None, ())
    assert "closure_hours" not in predicate
    assert plan.fallback_reason == "common_source_snapshot_unproven"


def test_oversized_identity_input_fails_without_truncation():
    scope = Scope(uuid4(), uuid4(), uuid4())
    with pytest.raises(ValueError, match="item cap"):
        diagnostic_span_closure(
            scope,
            [str(uuid4())],
            source_identities=[("t", str(i)) for i in range(1001)],
        )


def test_dependency_only_trace_nomination(publication_store):
    client = publication_store.client
    scope = Scope(uuid4(), uuid4(), uuid4())
    requested, current, trace, attempt = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2026, 10, 1, tzinfo=UTC)
    seed_typed(client, scope, current, now, trace=trace, span_id="span", version=2)
    client.execute(
        "INSERT INTO metric_source_dependencies (org_id,project_id,trace_id,span_id,canonical_session_id,generation,attempt_id,_version) "
        "SELECT toUUID(%(org_id)s),toUUID(%(project_id)s),%(trace)s,'span',%(session)s,7,toUUID(%(attempt)s),1",
        {
            **scope.parameters(),
            "trace": str(trace),
            "session": str(requested),
            "attempt": str(attempt),
        },
    )
    manifest = SimpleNamespace(
        project_id=scope.project,
        checkpoint_attempt_id=attempt,
        delta_attempt_ids=[],
        allowed_attempts=lambda: [(7, attempt)],
    )
    plan = diagnostic_span_closure(scope, [str(requested)], manifest=manifest)
    nominated = client.execute(
        "WITH " + ", ".join(plan.ctes) + " SELECT trace_id FROM closure_hours",
        plan.parameters,
    )
    assert nominated == [(str(trace),)]
    assert winners(client, scope, requested, now + timedelta(days=1), plan=plan) == []
