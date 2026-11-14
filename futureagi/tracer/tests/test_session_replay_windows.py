"""SQL-construction contracts for discovery versus physical winner replay."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = str(UUID(int=1))
SESSION = str(UUID(int=2))
START = datetime(2026, 10, 20, 12, tzinfo=UTC)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def cte(sql, name):
    start = sql.index(f"{name} AS (") + len(f"{name} AS (")
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if not depth:
            return sql[start:end]
    raise AssertionError(f"unclosed CTE: {name}")


def date_filter(operation="between"):
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": operation,
            "filter_value": [
                START.isoformat(),
                (START + timedelta(days=1)).isoformat(),
            ],
        },
    }


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("arrival", [False, True])
def test_id_usage_window_follows_discovery_policy(cls, arrival):
    builder = cls(project_id=PROJECT, filters=[date_filter()])
    sql, params = builder.build_id_query(
        **(
            {"created_at_floor": START, "created_at_ceiling": START + timedelta(days=1)}
            if arrival
            else {}
        )
    )
    identities = cte(sql, "candidate_member_identities")
    members = cte(sql, "session_members")
    assert ("%(start_date_us)s" in identities) is not arrival
    assert ("latest_start_time >=" in members) is not arrival
    assert "SELECT project_id, session_id FROM member_session_scope" in identities
    assert "SELECT project_id, session_id FROM member_session_scope" in members
    assert ("%(created_at_floor_us)s" in cte(sql, "legacy_roots")) is arrival
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
def test_arrival_usage_retains_explicit_date_exclusions(cls):
    sql, _ = cls(
        project_id=PROJECT, filters=[date_filter("not_between")]
    ).build_id_query(created_at_floor=START)
    members = cte(sql, "session_members")
    assert "session_aggregate_member_time" in members
    assert "%(start_date_us)s" not in members


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize("shape", ["roots", "scalar", "user", "hydration"])
def test_replay_keeps_complete_storage_keys_without_widening_discovery(cls, shape):
    filters = [date_filter()]
    if shape == "scalar":
        filters.append(
            {
                "column_id": "company",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "acme",
                },
            }
        )
    builder = cls(
        project_id=PROJECT,
        filters=filters,
        user_id=str(UUID(int=3)) if shape == "user" else None,
    )
    sql, params = (
        builder.build_page_hydration_query([SESSION])
        if shape == "hydration"
        else builder.build_candidate_page_query()
    )
    name = {"scalar": "latest_candidate_scalar_spans", "user": "latest_user_spans"}.get(
        shape, "latest_roots"
    )
    replay = cte(sql, name)
    before_key = replay.split("PREWHERE", 1)[1].split(" IN (", 1)[0]
    if cls is SessionListQueryBuilder:
        assert "start_time >=" not in before_key
        assert "toDate(created_at)" in before_key
        assert "GROUP BY project_id, toDate(created_at), trace_id, id" in replay
    else:
        assert "start_time >= toStartOfHour(" in before_key
        assert (
            "observation_type, service_name, toStartOfHour(start_time), trace_id, id"
            in replay
        )
        assert "created_day" not in sql
    assert "project_id = %(project_id)s" in before_key
    assert "latest_start_time >=" in sql
    assert "start_time >=" in cte(sql, "candidate_root_identities")
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
def test_replay_change_preserves_finite_page_limit(cls):
    builder = cls(project_id=PROJECT, filters=[date_filter()])
    with pytest.raises(ValueError, match="bounded limit"):
        builder.build_page_hydration_query([str(UUID(int=i + 1)) for i in range(201)])
    with pytest.raises(ValueError, match="bounded limit"):
        builder.build_filter_match_query([str(UUID(int=i + 1)) for i in range(201)])
