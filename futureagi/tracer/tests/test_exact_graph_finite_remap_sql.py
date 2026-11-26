from datetime import datetime

import pytest

from tracer.services.clickhouse.exact_graph_reads import (
    _finite_survivor_map_ctes,
    _session_aggregate_source_sql,
    _user_aggregate_source_sql,
)


def _compact(sql: str) -> str:
    return " ".join(sql.split())


@pytest.mark.unit
def test_finite_survivor_map_probes_remaps_with_a_candidate_relation():
    sql = _compact(
        _finite_survivor_map_ctes(
            remap_table="trace_session_id_remap",
            candidate_relation="candidate_physical_session_ids",
            candidate_column="physical_session_id",
            prefix="candidate_session_remap",
            map_name="ts_survivor_map",
        )
    )

    assert (
        "candidate_session_remap_candidate_ids AS ( SELECT DISTINCT "
        "assumeNotNull(physical_session_id) AS physical_session_id FROM "
        "candidate_physical_session_ids WHERE isNotNull(physical_session_id) )" in sql
    )
    assert (
        "PREWHERE old_id IN ( SELECT physical_session_id FROM "
        "candidate_session_remap_candidate_ids )" in sql
    )
    assert (
        "UNION DISTINCT SELECT physical_session_id AS new_id FROM "
        "candidate_session_remap_candidate_ids" in sql
    )
    assert (
        "WHERE new_id IN (SELECT new_id FROM "
        "candidate_session_remap_target_new_ids)" in sql
    )
    assert "PREWHERE old_id IN candidate_session_remap_candidate_ids" not in sql
    assert "groupUniqArray(assumeNotNull(physical_session_id))" not in sql


@pytest.mark.unit
def test_session_graph_consumes_the_finite_candidate_relation_directly():
    sql, _params = _session_aggregate_source_sql(
        project_id="22222222-2222-4222-8222-222222222222",
        filters=[],
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 2, 1),
        include_trace_ids=False,
        candidate_trace_ids_param="candidate_trace_ids",
    )
    compact_sql = _compact(sql)

    assert (
        "FROM candidate_session_remap_candidate_ids AS candidate_session_ids"
        in compact_sql
    )
    assert "arrayJoin(candidate_session_remap_candidate_ids)" not in compact_sql
    assert (
        "PREWHERE old_id IN ( SELECT physical_session_id FROM "
        "candidate_session_remap_candidate_ids )" in compact_sql
    )


@pytest.mark.unit
def test_user_graph_consumes_the_finite_candidate_relation_directly():
    sql, _params, needs_eval = _user_aggregate_source_sql(
        project_id="22222222-2222-4222-8222-222222222222",
        filters=[],
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 2, 1),
        include_trace_ids=False,
        candidate_trace_ids_param="candidate_trace_ids",
    )
    compact_sql = _compact(sql)

    assert needs_eval is False
    assert (
        "FROM candidate_end_user_remap_candidate_ids AS candidate_end_user_ids"
        in compact_sql
    )
    assert (
        "SELECT physical_end_user_id FROM "
        "candidate_end_user_remap_candidate_ids UNION DISTINCT" in compact_sql
    )
    assert "arrayJoin(candidate_end_user_remap_candidate_ids)" not in compact_sql
    assert (
        "PREWHERE old_id IN ( SELECT physical_end_user_id FROM "
        "candidate_end_user_remap_candidate_ids )" in compact_sql
    )
