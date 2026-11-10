"""Offline graph/list identity contracts; no runtime qualification claimed."""

import re
import socket
from copy import deepcopy
from datetime import timedelta

import pytest

from tracer.services.clickhouse import exact_graph_reads as graphs
from tracer.tests.test_exact_graph_relational_filters import (
    _eval_filter,
    _patch_eval_resolution,
)
from tracer.tests.test_session_annotation_membership import (
    END,
    LABEL,
    PROJECT,
    SECOND_LABEL,
    _annotation,
    _cte,
    _session_score_reads,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline graph compiler test attempted network access")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _query(filters):
    return graphs._session_aggregate_source_sql(
        project_id=PROJECT,
        filters=filters,
        start_date=END - timedelta(days=7),
        end_date=END,
        include_trace_ids=False,
        candidate_trace_ids_param="candidate_traces",
    )


@pytest.mark.parametrize(
    "kind,value",
    [
        ("text", "free-text"),
        ("number", 25),
        ("number", 4),
        ("categorical", "thumbs_up"),
        ("categorical", "alpha"),
        ("categorical", ["alpha", "beta"]),
    ],
)
def test_session_graph_reads_direct_session_scores(kind, value):
    leaf = _annotation(value)
    leaf["filter_config"]["filter_type"] = kind
    sql, params = _query([leaf])
    _session_score_reads(sql)
    assert params["project_id"] == PROJECT
    # No generated candidate placeholder is allowed to escape into the wire.
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys() | {"candidate_traces"}


def test_session_graph_score_scope_is_finite_and_remap_aware():
    sql, _ = _query([_annotation()])
    roots = _cte(sql, "resolved_root_sessions")
    assert "project_id = toUUID(%(project_id)s)" in roots
    assert "IN (SELECT session_id FROM candidate_sessions)" in roots
    assert "snapshot_roots.is_deleted = 0" in roots
    assert "snapshot_start_date_us" in roots and "snapshot_end_date_us" in roots
    mapping = _cte(sql, "candidate_relational_session_traces")
    assert "UNION DISTINCT" in mapping
    assert "roots.session_id = remap.survivor_id" in mapping
    for read, alias in _session_score_reads(sql):
        assert f"session_sp.project_id = {alias}.tracer_project_id" in read
        assert f"{alias}.deleted = false" in read
        assert f"{alias}._peerdb_is_deleted = 0" in read
        assert f"{alias}.created_at" not in read


@pytest.mark.parametrize("operation", ["is_null", "is_not_null", "is_not"])
def test_session_graph_annotation_complements_keep_the_same_score_relation(operation):
    sql, _ = _query([_annotation("alpha", operation=operation)])
    _session_score_reads(sql)
    assert "candidate_relational_session_traces" in sql


@pytest.mark.parametrize("complete", [True, False])
def test_session_graph_annotation_completeness_retains_configured_labels(
    monkeypatch, complete
):
    monkeypatch.setattr(
        graphs, "_annotation_label_ids_for_filters", lambda *args: (LABEL, SECOND_LABEL)
    )
    sql, params = _query(
        [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": complete,
                },
            }
        ]
    )
    _session_score_reads(sql)
    assert LABEL in params.values() and SECOND_LABEL in params.values()


def test_session_graph_combined_leaves_have_disjoint_parameter_names():
    second = deepcopy(_annotation("second"))
    second["column_id"] = SECOND_LABEL
    second["property_id"] = f"annotation:{SECOND_LABEL}"
    patch_configs, patch_templates = _patch_eval_resolution()
    with patch_configs, patch_templates:
        sql, params = _query([_eval_filter(), _annotation("first"), second])
    expected = {"first", "second", LABEL, SECOND_LABEL}
    assert expected <= {value for value in params.values() if isinstance(value, str)}
    assert any(name.startswith("session_relational_") for name in params)
    assert any(name.startswith("session_annotation_") for name in params)
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys() | {"candidate_traces"}


def test_session_graph_without_annotations_adds_no_score_mapping():
    sql, _ = _query([])
    assert "candidate_relational_session_traces" not in sql
    assert "resolved_root_sessions AS" not in sql
