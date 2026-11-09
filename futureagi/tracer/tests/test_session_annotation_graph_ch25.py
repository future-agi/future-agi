"""Execute graph/list annotation parity on the owned isolated CH fixture."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse import exact_graph_reads as graphs
from tracer.tests import test_session_annotation_membership_ch25 as fixtures

pytestmark = pytest.mark.integration
ch_client = fixtures.ch_client
score_filter_tables = fixtures.score_filter_tables


@pytest.fixture
def remap_table(ch_client, score_filter_tables):
    spans, _ = score_filter_tables
    remap = f"{spans}_remap"
    ch_client.execute(
        f"CREATE TABLE {remap} (old_id UUID, new_id UUID, "
        "version DateTime64(6, 'UTC')) ENGINE=ReplacingMergeTree(version) "
        "ORDER BY old_id"
    )
    return remap


def _graph_query(tables, remap, leaf, *, anchor=False, aggregate=False):
    spans, scores = tables
    sql, params = graphs._session_aggregate_source_sql(
        project_id=fixtures.PROJECT_ID,
        filters=[leaf],
        # The current Score predates this window by 90 days. Relation age is
        # not the Session's age; only the source roots obey the time window.
        start_date=fixtures.STARTED_AT - timedelta(minutes=1),
        end_date=fixtures.STARTED_AT + timedelta(minutes=1),
        include_trace_ids=False,
        anchor_by_session_start=anchor,
        candidate_trace_ids_param=None if anchor else "candidate_traces",
    )
    sql = (
        sql.replace("model_hub_score", scores)
        .replace("FROM spans ", f"FROM {spans} ")
        .replace("trace_session_id_remap", remap)
    )
    params["candidate_traces"] = (fixtures.TRACE_ID,)
    columns = "toString(session_id)"
    if aggregate:
        columns += ", session_traces, session_avg_latency"
    return f"SELECT {columns} FROM ({sql}) ORDER BY session_id", params


@pytest.mark.parametrize("source", ["trace", "span", "session"])
@pytest.mark.parametrize("anchor", [False, True], ids=["candidate", "session-start"])
@pytest.mark.parametrize(
    "deleted,hard_deleted", [(True, 0), (False, 1)], ids=["soft", "cdc"]
)
def test_session_graph_matches_current_annotation_source(
    ch_client, score_filter_tables, remap_table, source, anchor, deleted, hard_deleted
):
    leaf = fixtures._leaf("approved")
    graph_query, params = _graph_query(
        score_filter_tables, remap_table, leaf, anchor=anchor
    )
    list_sql, list_params = fixtures._query(score_filter_tables, leaf)
    assert ch_client.execute(graph_query, params) == []
    fixtures._score(ch_client, score_filter_tables, {"text": "approved"}, source=source)
    expected = [(fixtures.SESSION_ID,)]
    assert ch_client.execute(list_sql, list_params) == expected
    assert ch_client.execute(graph_query, params) == expected
    fixtures._score(
        ch_client, score_filter_tables, {"text": "corrected"}, source=source, version=2
    )
    assert ch_client.execute(list_sql, list_params) == []
    assert ch_client.execute(graph_query, params) == []
    corrected = fixtures._leaf("corrected")
    graph_query, params = _graph_query(
        score_filter_tables, remap_table, corrected, anchor=anchor
    )
    list_sql, list_params = fixtures._query(score_filter_tables, corrected)
    assert ch_client.execute(list_sql, list_params) == expected
    assert ch_client.execute(graph_query, params) == expected
    fixtures._score(
        ch_client,
        score_filter_tables,
        {"text": "corrected"},
        source=source,
        version=3,
        deleted=deleted,
        hard_deleted=hard_deleted,
    )
    assert ch_client.execute(list_sql, list_params) == []
    assert ch_client.execute(graph_query, params) == []


@pytest.mark.parametrize("remapped", [False, True], ids=["old-id", "new-id"])
def test_graph_session_scores_resolve_remaps_without_foreign_membership(
    ch_client, score_filter_tables, remap_table, remapped
):
    ch_client.execute(
        f"INSERT INTO {remap_table} (old_id, new_id, version) VALUES",
        [
            (fixtures.SESSION_ID, fixtures.SESSION_ALIAS, fixtures.STARTED_AT),
            (fixtures.SECOND_SCORE, fixtures.SESSION_ALIAS, fixtures.STARTED_AT),
        ],
    )
    # The candidate trace selects a Session, not just that one root. Keep the
    # sibling's aggregate, but exclude a separate same-project Session.
    ch_client.execute(
        f"INSERT INTO {score_filter_tables[0]} "
        "(id, project_id, trace_id, trace_session_id, start_time, created_at, "
        "latency_ms, _version) VALUES",
        [
            (
                "sibling-root",
                fixtures.PROJECT_ID,
                fixtures.OTHER_TRACE,
                fixtures.SESSION_ID,
                fixtures.STARTED_AT,
                fixtures.STARTED_AT,
                2000,
                1,
            ),
            (
                "unrelated-root",
                fixtures.PROJECT_ID,
                "unrelated-trace",
                fixtures.OTHER_SESSION,
                fixtures.STARTED_AT,
                fixtures.STARTED_AT,
                9000,
                1,
            ),
        ],
    )
    sql, params = _graph_query(
        score_filter_tables, remap_table, fixtures._leaf("approved"), aggregate=True
    )
    fixtures._score(
        ch_client,
        score_filter_tables,
        {"text": "approved"},
        project=fixtures.OTHER_PROJECT,
        remapped=remapped,
    )
    assert ch_client.execute(sql, params) == []
    fixtures._score(
        ch_client,
        score_filter_tables,
        {"text": "approved"},
        version=2,
        remapped=remapped,
    )
    assert ch_client.execute(sql, params) == [(fixtures.SESSION_ID, 2, 1500.0)]


def test_graph_annotation_presence_and_completeness_follow_latest_scores(
    ch_client, score_filter_tables, remap_table, monkeypatch
):
    monkeypatch.setattr(
        graphs,
        "_annotation_label_ids_for_filters",
        lambda *args: (fixtures.LABEL_ID, fixtures.SECOND_LABEL),
    )
    present = _graph_query(
        score_filter_tables, remap_table, fixtures._leaf(None, operation="is_not_null")
    )
    absent = _graph_query(
        score_filter_tables, remap_table, fixtures._leaf(None, operation="is_null")
    )

    def completeness(value):
        return _graph_query(
            score_filter_tables,
            remap_table,
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": value,
                },
            },
        )

    complete, incomplete = completeness(True), completeness(False)
    expected = [(fixtures.SESSION_ID,)]

    def check(has_value, all_labels):
        assert ch_client.execute(*present) == (expected if has_value else [])
        assert ch_client.execute(*absent) == ([] if has_value else expected)
        assert ch_client.execute(*complete) == (expected if all_labels else [])
        assert ch_client.execute(*incomplete) == ([] if all_labels else expected)

    check(False, False)
    fixtures._score(ch_client, score_filter_tables, {"text": "session"})
    check(True, False)
    fixtures._score(
        ch_client,
        score_filter_tables,
        {"text": "trace"},
        source="trace",
        score_id=fixtures.SECOND_SCORE,
        label_id=fixtures.SECOND_LABEL,
    )
    check(True, True)
    fixtures._score(
        ch_client, score_filter_tables, {"text": "session"}, version=2, deleted=True
    )
    check(False, False)
