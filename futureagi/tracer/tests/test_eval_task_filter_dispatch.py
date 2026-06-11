"""
Tests for parsing_evaltask_filters dispatcher.

Verifies the eval-task path now routes each ``col_type`` through the right
FilterEngine handler — mirroring ``list_spans_observe``
(``tracer/views/observation_span.py:1755-1826``) — and silently ignores
unrecognised col_types. Structural assertions: we check the shape and
``repr`` of the returned ``Q`` (and the annotation dict) rather than
running queries against a DB, because the underlying handlers each have
their own integration tests.

The bug this guards against: until 2026-06-04 the dispatcher only handled
``SPAN_ATTRIBUTE`` and silently dropped every other col_type, including the
``ANNOTATION`` annotator filter behind the prod task
``f8481965-cd74-44b1-9b6d-f4bb66ec2218``. See plan
``~/.claude/plans/shift-to-fix-nightly-dev-27-05-and-zazzy-eagle.md``.
"""

import uuid

import pytest
from django.db.models import Q

from tracer.utils.eval_tasks import parsing_evaltask_filters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap(items, key="filters"):
    """Wrap a list of filter items in the eval-task top-level filters dict."""
    return {key: items}


def _span_attr_item(column_id="ended_reason", value="completed"):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _annotator_item(user_uuid="c65a0f3c-8a72-432a-987f-ddbd8391df29"):
    """Mirrors the prod bug task's annotator filter shape (camelCase, top-level
    col_type-by-config). The dispatcher tolerates either case."""
    return {
        "columnId": "annotator",
        "filterConfig": {
            "colType": "ANNOTATION",
            "filterOp": "equals",
            "filterType": "text",
            "filterValue": user_uuid,
        },
    }


def _label_value_item(label_uuid, value):
    return {
        "column_id": label_uuid,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": value,
        },
    }


def _system_metric_item(column_id="cost", op="greater_than", value=0.5):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _eval_metric_item(eval_template_id, op="greater_than", value=0.8):
    return {
        "column_id": eval_template_id,
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


# ---------------------------------------------------------------------------
# Empty / None handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmpty:
    def test_none_returns_empty_tuple(self):
        q, anns = parsing_evaltask_filters(None)
        assert q == Q()
        assert anns == {}

    def test_empty_dict_returns_empty_tuple(self):
        q, anns = parsing_evaltask_filters({})
        assert q == Q()
        assert anns == {}

    def test_empty_filters_list_returns_empty_tuple(self):
        q, anns = parsing_evaltask_filters({"filters": []})
        assert q == Q()
        assert anns == {}


# ---------------------------------------------------------------------------
# Legacy `span_attributes_filters` key still parsed (transition)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLegacyKey:
    def test_legacy_key_with_span_attribute_filter(self):
        items = [_span_attr_item()]
        q, anns = parsing_evaltask_filters(
            _wrap(items, key="span_attributes_filters")
        )
        # Legacy key produces the same Q as the canonical key.
        q_canonical, _ = parsing_evaltask_filters(_wrap(items, key="filters"))
        assert repr(q) == repr(q_canonical)
        assert anns == {}

    def test_legacy_key_with_annotator_filter(self):
        # The specific shape that motivated the fix: prod task f8481965.
        items = [_annotator_item()]
        q, anns = parsing_evaltask_filters(
            _wrap(items, key="span_attributes_filters")
        )
        assert q != Q()
        assert "Exists" in repr(q)  # routed through the voice annotation handler


# ---------------------------------------------------------------------------
# SPAN_ATTRIBUTE — regression guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpanAttribute:
    def test_span_attribute_only(self):
        q, anns = parsing_evaltask_filters(_wrap([_span_attr_item()]))
        assert q != Q()
        # SPAN_ATTRIBUTE filters resolve via the span_attributes JSONB
        # (`has_key` + `contains`) — verify by string match on the Q repr.
        assert "span_attributes" in repr(q)
        assert anns == {}


# ---------------------------------------------------------------------------
# ANNOTATION — the original bug
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnnotation:
    def test_annotator_filter_produces_exists_clause(self):
        items = [_annotator_item()]
        q, anns = parsing_evaltask_filters(_wrap(items))
        assert q != Q()
        # The annotator branch builds an Exists(Score.objects...) subquery.
        assert "Exists" in repr(q)
        # The annotator path is pure-Q — no annotate() kwargs.
        assert anns == {}

    def test_per_label_score_filter_builds_annotation_field_q(self):
        # A per-label ANNOTATION filter (e.g. "quality > 3") builds a Q
        # referencing the `annotation_<uuid>__score` field. The actual
        # `.annotate(annotation_<uuid>=...)` step is contributed by
        # `build_annotation_subqueries` (observation_span.py:1167-1169),
        # which `process_eval_task` does not yet call — see the dispatcher
        # docstring. So `extra_anns` remains empty here; this test guards
        # the Q shape only. When `build_annotation_subqueries` is wired in
        # for eval tasks, replace this with an integration test that asserts
        # the queryset actually returns the right rows.
        label_uuid = str(uuid.uuid4())
        items = [_label_value_item(label_uuid, 3.0)]
        q, anns = parsing_evaltask_filters(_wrap(items))
        assert q != Q()
        assert f"annotation_{label_uuid}__score" in repr(q)
        assert anns == {}

    def test_annotation_does_not_leak_into_non_system_handler(self):
        # Per list_spans_observe (observation_span.py:1769-1777), ANNOTATION
        # items and the annotation-special column_ids must be excluded from
        # the eval-metrics handler. Mixing both should still produce a single
        # well-formed Q without raising.
        items = [
            _annotator_item(),
            _eval_metric_item(str(uuid.uuid4())),
        ]
        q, anns = parsing_evaltask_filters(_wrap(items))
        assert q != Q()


# ---------------------------------------------------------------------------
# SYSTEM_METRIC
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemMetric:
    def test_system_metric_filter(self):
        items = [_system_metric_item("cost", "greater_than", 0.1)]
        q, anns = parsing_evaltask_filters(_wrap(items))
        # The system-metrics handler maps `cost` to `row_avg_cost`
        # (FilterEngine.DEFAULT_FIELD_MAP) and emits a Q referencing it.
        assert q != Q()
        assert anns == {}


# ---------------------------------------------------------------------------
# EVAL_METRIC
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvalMetric:
    def test_eval_metric_filter(self):
        items = [_eval_metric_item(str(uuid.uuid4()), "greater_than", 0.8)]
        q, anns = parsing_evaltask_filters(_wrap(items))
        assert q != Q()


# ---------------------------------------------------------------------------
# Mixed — all four colTypes at once
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMixed:
    def test_all_four_col_types_combine(self):
        items = [
            _span_attr_item(),
            _system_metric_item("cost", "greater_than", 0.1),
            _eval_metric_item(str(uuid.uuid4())),
            _annotator_item(),
        ]
        q, anns = parsing_evaltask_filters(_wrap(items))
        assert q != Q()
        # Each handler contributes its own clause; combined Q should reference
        # all the underlying mechanisms.
        rep = repr(q)
        assert "span_attributes" in rep   # SPAN_ATTRIBUTE
        assert "Exists" in rep             # ANNOTATION (annotator) Exists subquery


# ---------------------------------------------------------------------------
# Sibling keys at the top level
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSiblingKeys:
    def test_date_range_still_applied(self):
        q, _ = parsing_evaltask_filters(
            {"date_range": ["2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z"]}
        )
        assert q != Q()
        assert "created_at" in repr(q)

    def test_project_id_still_applied(self):
        q, _ = parsing_evaltask_filters({"project_id": str(uuid.uuid4())})
        assert q != Q()
        assert "project_id" in repr(q)

    def test_observation_type_still_applied(self):
        q, _ = parsing_evaltask_filters({"observation_type": ["llm", "tool"]})
        assert q != Q()
        assert "observation_type" in repr(q)


# ---------------------------------------------------------------------------
# Unrecognised col_type — silent skip (no raise)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnrecognisedColType:
    def test_unknown_col_type_does_not_raise(self):
        items = [
            {
                "column_id": "some_id",
                "filter_config": {
                    "col_type": "TOTALLY_MADE_UP",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "x",
                },
            }
        ]
        # Must not raise. The handlers' own col_type gates skip items they
        # don't recognise; the dispatcher itself doesn't enumerate types.
        q, anns = parsing_evaltask_filters(_wrap(items))
        assert isinstance(q, Q)
        assert anns == {}
