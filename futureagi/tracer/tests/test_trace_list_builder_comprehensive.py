"""
Comprehensive tests for TraceListQueryBuilder methods NOT covered by
``test_trace_list_ch.py``.

Covers the content/attribute/user-id/annotation/count query builders and
the annotation pivot helper. All pure query-string / pivot logic — no DB.

Focus areas (gaps):
- build_content_query
- build_span_attributes_query
- build_span_count_query project scoping + deletion predicate
- build_user_id_query + resolve_user_ids (mocked analytics)
- build_annotation_query + pivot_annotation_results
- build_count_query filter/search/project_version fragments + total expr
- deletion predicate divergence (is_deleted vs _peerdb_is_deleted) locked in
"""

import uuid
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder


@pytest.fixture
def project_id():
    return str(uuid.uuid4())


@pytest.fixture
def trace_ids():
    return [str(uuid.uuid4()) for _ in range(3)]


# ---------------------------------------------------------------------------
# build_content_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildContentQuery:
    def test_empty_trace_ids_returns_empty(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_content_query([])
        assert query == ""
        assert params == {}

    def test_heavy_columns_selected(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_content_query(trace_ids)
        assert "input" in query
        assert "output" in query
        assert "attrs_string" in query
        assert "attrs_number" in query
        assert "attrs_bool" in query
        assert "attributes_extra" in query
        assert "toJSONString(metadata) AS metadata" in query
        assert "trace_dict" in query and "trace_tags" in query

    def test_prewhere_and_params(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_content_query(trace_ids)
        assert "PREWHERE trace_id IN %(content_trace_ids)s" in query
        assert params["content_trace_ids"] == tuple(trace_ids)

    def test_project_scoping_and_deletion_predicate(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_content_query(trace_ids)
        assert "project_id = %(project_id)s" in query
        assert params["project_id"] == project_id
        # content query uses the v2 is_deleted column
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query

    def test_root_span_only(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_content_query(trace_ids)
        assert "parent_span_id IS NULL OR parent_span_id = ''" in query
        assert "LIMIT 1 BY trace_id" in query

    def test_multi_project_scoping(self, trace_ids):
        pids = [str(uuid.uuid4()), str(uuid.uuid4())]
        builder = TraceListQueryBuilder(project_ids=pids)
        query, params = builder.build_content_query(trace_ids)
        assert "project_id IN %(project_ids)s" in query
        assert params["project_ids"] == tuple(pids)

    def test_start_time_window_after_build(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, params = builder.build_content_query(trace_ids)
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in query
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in query
        assert params["start_date"] == builder.start_date
        assert params["end_date"] == builder.end_date

    def test_no_window_standalone(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_content_query(trace_ids)
        assert "start_time" not in query


# ---------------------------------------------------------------------------
# build_span_attributes_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSpanAttributesQuery:
    def test_empty_trace_ids_returns_empty(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_span_attributes_query([])
        assert query == ""
        assert params == {}

    def test_selects_trace_id_and_attributes_extra(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_span_attributes_query(trace_ids)
        assert "trace_id" in query
        assert "attributes_extra" in query
        # actual SQL has no GROUP BY / groupArrayDistinct despite docstring
        assert "GROUP BY" not in query
        assert "groupArrayDistinct" not in query

    def test_prewhere_and_params(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_span_attributes_query(trace_ids)
        assert "PREWHERE trace_id IN %(attr_trace_ids)s" in query
        assert params["attr_trace_ids"] == tuple(trace_ids)

    def test_empty_attribute_guards(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_span_attributes_query(trace_ids)
        # f-string '{{}}' renders to '{}'
        assert "attributes_extra != '{}'" in query
        assert "attributes_extra != ''" in query

    def test_project_scoping_and_deletion_predicate(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_span_attributes_query(trace_ids)
        assert "project_id = %(project_id)s" in query
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query

    def test_start_time_window_after_build(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, params = builder.build_span_attributes_query(trace_ids)
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in query
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in query
        assert params["start_date"] == builder.start_date

    def test_no_window_standalone(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_span_attributes_query(trace_ids)
        assert "start_time" not in query


# ---------------------------------------------------------------------------
# build_span_count_query — project scoping + deletion predicate (gap)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSpanCountScoping:
    def test_project_scoping_and_deletion(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_span_count_query(trace_ids)
        assert "project_id = %(project_id)s" in query
        assert params["project_id"] == project_id
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        assert "trace_id IN %(sc_trace_ids)s" in query

    def test_multi_project_scoping(self, trace_ids):
        pids = [str(uuid.uuid4())]
        builder = TraceListQueryBuilder(project_ids=pids)
        query, params = builder.build_span_count_query(trace_ids)
        assert "project_id IN %(project_ids)s" in query
        assert params["project_ids"] == tuple(pids)


# ---------------------------------------------------------------------------
# build_user_id_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildUserIdQuery:
    def test_empty_trace_ids_returns_empty(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_user_id_query([])
        assert query == ""
        assert params == {}

    def test_enduser_dict_lookup(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_user_id_query(trace_ids)
        assert "dictGetOrDefault('enduser_dict', 'user_id'" in query
        assert "PREWHERE trace_id IN %(user_trace_ids)s" in query
        assert params["user_trace_ids"] == tuple(trace_ids)

    def test_uses_peerdb_is_deleted_not_is_deleted(self, project_id, trace_ids):
        # user-id query targets the CDC mirror soft-delete column, unlike
        # the content/attribute queries which use is_deleted.
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_user_id_query(trace_ids)
        assert "_peerdb_is_deleted = 0" in query
        # bare `is_deleted = 0` must NOT appear (only the _peerdb_ alias form)
        assert "AND is_deleted = 0" not in query

    def test_project_scoping(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_user_id_query(trace_ids)
        assert "project_id = %(project_id)s" in query

    def test_filters_nil_and_null_end_user(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_user_id_query(trace_ids)
        assert "end_user_id IS NOT NULL" in query
        assert "00000000-0000-0000-0000-000000000000" in query
        assert "GROUP BY trace_id" in query
        assert "user_id != ''" in query

    def test_start_time_window_after_build(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, params = builder.build_user_id_query(trace_ids)
        assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in query
        assert "start_time < %(end_date)s + INTERVAL 1 DAY" in query
        assert params["start_date"] == builder.start_date

    def test_no_window_standalone(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_user_id_query(trace_ids)
        assert "start_time" not in query


# ---------------------------------------------------------------------------
# resolve_user_ids — mock analytics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveUserIds:
    def test_empty_trace_ids_returns_empty_dict(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        analytics = Mock()
        assert builder.resolve_user_ids([], analytics) == {}
        analytics.execute_ch_query.assert_not_called()

    def test_maps_trace_to_user(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        analytics = Mock()
        analytics.execute_ch_query.return_value = Mock(
            data=[
                {"trace_id": "t1", "user_id": "alice"},
                {"trace_id": "t2", "user_id": "bob"},
            ]
        )
        result = builder.resolve_user_ids(trace_ids, analytics)
        assert result == {"t1": "alice", "t2": "bob"}

    def test_drops_falsy_user_ids(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        analytics = Mock()
        analytics.execute_ch_query.return_value = Mock(
            data=[
                {"trace_id": "t1", "user_id": "alice"},
                {"trace_id": "t2", "user_id": ""},
                {"trace_id": "t3", "user_id": None},
            ]
        )
        result = builder.resolve_user_ids(trace_ids, analytics)
        assert result == {"t1": "alice"}

    def test_passes_timeout(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        analytics = Mock()
        analytics.execute_ch_query.return_value = Mock(data=[])
        builder.resolve_user_ids(trace_ids, analytics)
        _, kwargs = analytics.execute_ch_query.call_args
        assert kwargs["timeout_ms"] == 10000


# ---------------------------------------------------------------------------
# build_annotation_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAnnotationQuery:
    def test_empty_trace_ids_returns_empty(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_annotation_query([], ["l1"])
        assert query == ""
        assert params == {}

    def test_empty_label_ids_returns_empty(self, project_id, trace_ids):
        # uses the passed-in arg, NOT self.annotation_label_ids
        builder = TraceListQueryBuilder(
            project_id=project_id, annotation_label_ids=["l1"]
        )
        query, params = builder.build_annotation_query(trace_ids, [])
        assert query == ""
        assert params == {}

    def test_params_only_trace_and_label(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_annotation_query(trace_ids, ["l1", "l2"])
        assert set(params.keys()) == {"trace_ids", "label_ids"}
        assert params["trace_ids"] == tuple(trace_ids)
        assert params["label_ids"] == ("l1", "l2")
        # scoping is via trace_id membership only, no project_id
        assert "project_id" not in query

    def test_annotation_table_and_group_by(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_annotation_query(trace_ids, ["l1"])
        assert "model_hub_score" in query
        assert "GROUP BY trace_id, label_id" in query
        assert "trace_id IN %(trace_ids)s" not in query  # join-derived alias form
        assert "IN %(trace_ids)s" in query
        assert "s.label_id IN %(label_ids)s" in query

    def test_deletion_predicates(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_annotation_query(trace_ids, ["l1"])
        assert "s._peerdb_is_deleted = 0" in query
        assert "s.deleted = false" in query
        assert "sp._peerdb_is_deleted = 0" in query

    def test_spans_join_side_bounded_on_start_time(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, params = builder.build_annotation_query(trace_ids, ["l1"])
        # The spans-JOIN side is bounded on the sp alias so it prunes the
        # scan of every page trace's spans.
        assert "sp.start_time >= %(start_date)s - INTERVAL 1 DAY" in query
        assert "sp.start_time < %(end_date)s + INTERVAL 1 DAY" in query
        assert params["start_date"] == builder.start_date

    def test_trailing_score_not_dropped(self, project_id, trace_ids):
        """A score/annotation can be created arbitrarily later than the span
        it targets. The score (s) side must therefore carry no time predicate
        at all, so a row whose created_at falls after the window end still
        resolves; only the spans (sp) join side is time-bounded."""
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, _ = builder.build_annotation_query(trace_ids, ["l1"])
        # spans side bounded ...
        assert "sp.start_time <" in query
        # ... but the score side has no created_at bound of any kind, so a
        # late-created score is never filtered out by time.
        assert "s.created_at" not in query
        assert "s.start_time" not in query

    def test_no_window_standalone(self, project_id, trace_ids):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, params = builder.build_annotation_query(trace_ids, ["l1"])
        assert "start_time" not in query
        assert set(params.keys()) == {"trace_ids", "label_ids"}


# ---------------------------------------------------------------------------
# pivot_annotation_results
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPivotAnnotationResults:
    def _row(self, trace_id="t1", label_id="l1", value="{}"):
        return {"trace_id": trace_id, "label_id": label_id, "value": value}

    def test_empty(self):
        assert TraceListQueryBuilder.pivot_annotation_results([]) == {}

    def test_numeric(self):
        rows = [self._row(value='{"value": 42}')]
        out = TraceListQueryBuilder.pivot_annotation_results(rows, {"l1": "numeric"})
        assert out["t1"]["l1"] == 42

    def test_star_uses_rating(self):
        rows = [self._row(value='{"rating": 4}')]
        out = TraceListQueryBuilder.pivot_annotation_results(rows, {"l1": "star"})
        assert out["t1"]["l1"] == 4

    def test_thumbs_up_down_true(self):
        rows = [self._row(value='{"value": "up"}')]
        out = TraceListQueryBuilder.pivot_annotation_results(
            rows, {"l1": "thumbs_up_down"}
        )
        assert out["t1"]["l1"] is True

    def test_thumbs_up_down_false(self):
        rows = [self._row(value='{"value": "down"}')]
        out = TraceListQueryBuilder.pivot_annotation_results(
            rows, {"l1": "thumbs_up_down"}
        )
        assert out["t1"]["l1"] is False

    def test_categorical_selected(self):
        rows = [self._row(value='{"selected": ["a", "b"]}')]
        out = TraceListQueryBuilder.pivot_annotation_results(
            rows, {"l1": "categorical"}
        )
        assert out["t1"]["l1"] == ["a", "b"]

    def test_text(self):
        rows = [self._row(value='{"text": "hello"}')]
        out = TraceListQueryBuilder.pivot_annotation_results(rows, {"l1": "text"})
        assert out["t1"]["l1"] == "hello"

    def test_unknown_label_type_passthrough_raw_dict(self):
        rows = [self._row(value='{"foo": "bar"}')]
        out = TraceListQueryBuilder.pivot_annotation_results(rows, {"l1": "weird"})
        assert out["t1"]["l1"] == {"foo": "bar"}

    def test_no_label_types_defaults_to_raw(self):
        rows = [self._row(value='{"foo": "bar"}')]
        out = TraceListQueryBuilder.pivot_annotation_results(rows)
        assert out["t1"]["l1"] == {"foo": "bar"}

    def test_bad_json_string_becomes_empty_dict(self):
        rows = [self._row(value="not-json")]
        out = TraceListQueryBuilder.pivot_annotation_results(rows, {"l1": "weird"})
        assert out["t1"]["l1"] == {}

    def test_value_already_dict(self):
        rows = [self._row(value={"value": 7})]
        out = TraceListQueryBuilder.pivot_annotation_results(rows, {"l1": "numeric"})
        assert out["t1"]["l1"] == 7

    def test_multiple_labels_same_trace(self):
        rows = [
            self._row(label_id="l1", value='{"value": 1}'),
            self._row(label_id="l2", value='{"text": "x"}'),
        ]
        out = TraceListQueryBuilder.pivot_annotation_results(
            rows, {"l1": "numeric", "l2": "text"}
        )
        assert out["t1"]["l1"] == 1
        assert out["t1"]["l2"] == "x"


# ---------------------------------------------------------------------------
# build_count_query — filter/search/project_version fragments
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildCountQuery:
    def test_uniq_total_expr(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()  # sets start_date/end_date in self.params
        query, params = builder.build_count_query()
        assert "uniq(trace_id) AS total" in query
        assert "parent_span_id IS NULL OR parent_span_id = ''" in query
        assert params["project_id"] == project_id

    def test_project_version_fragment(self, project_id):
        pv = str(uuid.uuid4())
        builder = TraceListQueryBuilder(project_id=project_id, project_version_id=pv)
        builder.build()
        query, params = builder.build_count_query()
        assert "project_version_id = %(project_version_id)s" in query
        assert params["project_version_id"] == pv

    def test_project_version_absent(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, params = builder.build_count_query()
        assert "project_version_id" not in query

    def test_search_fragment(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id, search="boom")
        builder.build()
        query, params = builder.build_count_query()
        assert "trace_name ILIKE %(search)s" in query
        assert params["search"] == "%boom%"

    def test_start_time_window_no_created_at_skew(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        builder.build()
        query, _ = builder.build_count_query()
        assert "start_time >= %(start_date)s" in query
        assert "start_time < %(end_date)s" in query
        assert "created_at" not in query

    def test_multi_project_scoping(self):
        pids = [str(uuid.uuid4()), str(uuid.uuid4())]
        builder = TraceListQueryBuilder(project_ids=pids)
        builder.build()
        query, params = builder.build_count_query()
        assert "project_id IN %(project_ids)s" in query
        assert params["project_ids"] == tuple(pids)


# ---------------------------------------------------------------------------
# Outer window bounds on start_time (Card A / T0 + T1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOuterWindowStartTime:
    def test_build_bounds_start_time_no_created_at(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build()
        assert "start_time >= %(start_date)s" in query
        assert "start_time < %(end_date)s" in query
        assert "created_at" not in query

    def test_id_query_bounds_start_time_no_created_at(self, project_id):
        builder = TraceListQueryBuilder(project_id=project_id)
        query, _ = builder.build_id_query()
        assert "start_time >= %(start_date)s" in query
        assert "start_time < %(end_date)s" in query
        assert "created_at" not in query


# ---------------------------------------------------------------------------
# build_eval_query — rewrite-safe deletion predicate (fix #1) guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvalQueryDeletionPredicate:
    def test_rewrite_safe_deleted_predicate(self, project_id):
        builder = TraceListQueryBuilder(
            project_id=project_id, eval_config_ids=["ec1"]
        )
        query, _ = builder.build_eval_query(["t1"])
        # legacy tracer_eval_logger uses `deleted`, not `is_deleted` — the v2
        # rewriter must leave this form untouched.
        assert "(deleted = 0 OR deleted IS NULL)" in query
        assert "_peerdb_is_deleted = 0" in query

    def test_created_at_pruning_only_after_build(self, project_id):
        # build_eval_query guards the created_at fragment on self.start_date
        builder = TraceListQueryBuilder(
            project_id=project_id, eval_config_ids=["ec1"]
        )
        # no prior build(): start_date is None → no created_at fragment
        query_no_build, _ = builder.build_eval_query(["t1"])
        assert "created_at >= %(start_date)s" not in query_no_build

        builder.build()  # sets start_date
        query_after, params = builder.build_eval_query(["t1"])
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in query_after
        assert "start_date" in params
