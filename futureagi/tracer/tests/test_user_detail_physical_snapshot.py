"""User-detail graph truth from complete physical winners, not candidate SQL."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserDetailTimeSeriesQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,  # noqa: F401
    PROJECT,
    START,
    engine,  # noqa: F401 - importing registers the pytest engine fixture.
    time_filter,
)
from tracer.tests.test_users_physical_latest_native import (  # noqa: F401
    uid,
    users_engine,
)


@pytest.mark.integration
@pytest.mark.parametrize("filter_kind", [None, "cost", "boolean"])
@pytest.mark.parametrize("replacement", [
    {"start_time": START + timedelta(minutes=10)},
    {"start_time": START + timedelta(minutes=40)},
    {"end_user_id": uid(20)},
    {"end_user_id": None},
    {"is_deleted": 1},
    {"cost": 0, "prompt_tokens": 0, "completion_tokens": 0},
    {"attrs_bool": {}},
])
def test_user_detail_graph_uses_complete_physical_winners(users_engine, replacement, filter_kind):  # noqa: F811
    run, insert = users_engine
    run("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    run("CREATE TABLE trace_session_id_remap (old_id UUID, new_id UUID, version DateTime64(6, 'UTC')) "
        "ENGINE=ReplacingMergeTree(version) ORDER BY old_id")
    run("INSERT INTO trace_session_id_remap VALUES (%(a)s,%(new)s,%(at)s),(%(b)s,%(new)s,%(at)s)",
        {"a": uid(310), "b": uid(350), "new": uid(340), "at": START})
    start, end = START + timedelta(minutes=15), START + timedelta(minutes=30)
    base = {"end_user_id": uid(40), "cost": 10, "prompt_tokens": 4,
                "completion_tokens": 6, "trace_session_id": uid(340), "attrs_bool": {"ready": 0}}
    insert(**base, id="stable", trace_id="stable")
    old = dict(base, id="moved", trace_id="moved", cost=90)
    insert(**old)
    insert(**{**old, **replacement, "_version": 2})
    # Different service/type are different replacement keys even at the same time.
    insert(**{**base, "id": "stable", "trace_id": "stable", "service_name": "service-b", "cost": 17})
    insert(**{**base, "id": "stable", "trace_id": "stable", "observation_type": "tool", "cost": 13})
    insert(**{**base, "id": "child", "trace_id": "stable", "parent_span_id": "parent", "cost": 3})
    insert(**{**base, "id": "foreign", "project_id": OTHER_PROJECT, "cost": 999})
    insert(**{**base, "id": "at-start", "trace_id": "at-start", "start_time": start, "cost": 2})
    insert(**{**base, "id": "at-end", "trace_id": "at-end", "start_time": end, "cost": 999})
    for session in (None, uid(0)):
        insert(**{**base, "id": "missing-session-" + str(session), "trace_id": "stable",
                  "trace_session_id": session, "cost": 0})
    # A version can also move INTO the requested interval in its identity hour.
    insert(**{**base, "id": "reverse", "trace_id": "reverse", "start_time": START + timedelta(minutes=10)})
    insert(**{**base, "id": "reverse", "trace_id": "reverse", "_version": 2, "cost": 5})
    winners = run("SELECT project_id,trace_id,start_time,is_deleted,end_user_id,trace_session_id,"
                  "cost,prompt_tokens,completion_tokens,attrs_bool FROM spans FINAL")
    selected = [r for r in winners if r["project_id"] == PROJECT and not r["is_deleted"]
                and start <= r["start_time"] < end and r["end_user_id"] in {uid(10), uid(40), uid(50)}]
    assert len(selected) >= 8
    filters = [time_filter(start, end)]
    if filter_kind == "cost":
        filters.append({"column_id": "cost", "filter_config": {
            "col_type": "SYSTEM_METRIC", "filter_type": "number",
            "filter_op": "greater_than", "filter_value": 1}})
        selected = [r for r in selected if r["cost"] > 1]
    elif filter_kind == "boolean":
        filters.append({"column_id": "ready", "filter_config": {
            "col_type": "SPAN_ATTRIBUTE", "filter_type": "boolean",
            "filter_op": "equals", "filter_value": False}})
        selected = [r for r in selected if "ready" in r["attrs_bool"] and r["attrs_bool"]["ready"] == 0]
    target = UserDetailTimeSeriesQueryBuilderV2(
        project_id=PROJECT, organization_id=PROJECT, end_user_id=uid(10),
        filters=filters, interval="hour")
    actual = run(*target.build())
    assert len(actual) == 1
    assert actual[0]["trace_count"] == len({r["trace_id"] for r in selected})
    # All three session aliases are exactly one canonical session.
    assert actual[0]["session_count"] == 1
    for output, column in (("cost", "cost"), ("input_tokens", "prompt_tokens"),
                           ("output_tokens", "completion_tokens")):
        assert actual[0][output] == sum(r[column] for r in selected)
