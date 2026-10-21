"""Numeric graph witnesses stay all-history until exact classification."""
# Reusable pytest fixtures are injected by name.
# ruff: noqa: F811
from datetime import timedelta
from time import monotonic
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse import exact_graph_reads as graphs
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_trace_primary_prefix import trace_engine as trace_engine


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["zero", "late-child", "early-child", "superseded", "deleted", "wrong-type", "tied"])
def test_numeric_candidate_and_exact_membership(trace_engine, scenario):
    run, insert = trace_engine
    filters = [time_filter(start=START, end=START + timedelta(days=7)), {
        "column_id": "agent.duration_s", "filter_config": {
            "col_type": "SPAN_ATTRIBUTE", "filter_type": "number",
            "filter_op": "greater_than", "filter_value": 1,
        },
    }]
    insert(id="root", trace_id="in-window", attrs_number={})
    # An outside-window positive stays in the all-history raw candidate set;
    # the unchanged classifier must reject it, never narrow child history.
    insert(id="old-root", trace_id="outside", start_time=START-timedelta(days=10),
           attrs_number={"agent.duration_s": 2})
    insert(id="foreign", trace_id="in-window", project_id=OTHER_PROJECT,
           attrs_number={"agent.duration_s": 99})
    if scenario != "zero":
        child = {"id": "child", "trace_id": "in-window", "parent_span_id": "root",
                     "attrs_number": {"agent.duration_s": 2}}
        if scenario == "late-child":
            child["start_time"] = START + timedelta(days=90)
        if scenario == "early-child":
            child["start_time"] = START - timedelta(days=90)
        if scenario == "wrong-type":
            child.update(attrs_number={}, attrs_string={"agent.duration_s": "2"})
        insert(**child)
        if scenario == "superseded":
            insert(**{**child, "_version": 2, "attrs_number": {"agent.duration_s": 0}})
        if scenario == "deleted":
            insert(**{**child, "_version": 2, "is_deleted": 1})
        if scenario == "tied":
            # All possible independent argMax choices remain >1, so full
            # membership is deterministic despite tied physical values.
            insert(**{**child, "attrs_number": {"agent.duration_s": 3}})
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=filters,
        bounded_internal_scan=True, bounded_identity_only=True,
        bounded_bulk_scan=True, bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True)
    assert builder.exact_graph_candidate_witness_has_numeric_value_proof()
    assert not builder.exact_graph_candidate_witness_has_deployed_value_index()
    sql, params = builder.build_exact_graph_candidate_witness_probe(limit=1001)
    assert "span_attr_num" not in sql and "attrs_number" in sql
    candidates = run(sql, params)
    expected_candidates = ["outside"] if scenario in {"zero", "wrong-type"} else ["in-window", "outside"]
    assert [r["trace_id"] for r in candidates] == expected_candidates
    calls = []
    class Analytics:
        def execute_ch_query(self, query, params, **kwargs):
            calls.append(query)
            return SimpleNamespace(data=run(query, params), query_time_ms=0)
    ids, _, _ = graphs._enumerate_exact_trace_ids(analytics=Analytics(), project_id=PROJECT,
        filters=filters, annotation_label_ids=None, started=monotonic())
    assert ids == (["in-window"] if scenario in {"late-child", "early-child", "tied"} else [])
    assert len(calls) == 2  # candidate plus classifier, including exact zero
