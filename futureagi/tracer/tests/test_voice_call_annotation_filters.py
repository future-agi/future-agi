"""
Tests for voice call annotation filters and column config.

Tests the following changes:
1. FilterEngine.get_filter_conditions_for_voice_call_annotations:
   - Number filters (score, sub-field via ** separator)
   - Boolean filters (thumbs up/down)
   - Text filters
   - Array filters (categorical with count > 0)
   - Annotator filter (per-label via has_key on JSON map)
   - General annotator filter (column_id=annotator, Exists subquery)
   - My annotations filter (column_id=my_annotations)
2. update_span_column_config_based_on_annotations:
   - Annotator details included in column config
3. JSONBObjectAgg aggregate class
"""

import uuid

import pytest
from django.db.models import Q

from tracer.utils.filters import FilterEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_annotation_filter(
    column_id,
    filter_type,
    filter_op="equals",
    filter_value=None,
    col_type="ANNOTATION",
):
    """Build a single annotation filter item."""
    return {
        "column_id": column_id,
        "col_type": col_type,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


# ---------------------------------------------------------------------------
# Unit tests: Q object generation for voice call annotation filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationFiltersEmpty:
    """Edge cases: empty/None input."""

    def test_empty_filters_returns_empty_q(self):
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations([])
        assert result == Q()

    def test_none_filters_returns_empty_q(self):
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(None)
        assert result == Q()

    def test_non_annotation_col_type_is_skipped(self):
        filters = [
            _make_annotation_filter(
                "some-uuid", "number", "greater_than", 3.0, col_type="EVAL_METRIC"
            )
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result == Q()


# ---------------------------------------------------------------------------
# Number filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationNumberFilters:
    """Number filter on annotation score field."""

    def test_greater_than(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "greater_than", 3.0)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_less_than(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "less_than", 5.0)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_equals(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "equals", 4.0)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_not_equals(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "not_equals", 4.0)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_greater_than_or_equal(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "greater_than_or_equal", 3.0)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_less_than_or_equal(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "less_than_or_equal", 5.0)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_between(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "between", [2.0, 8.0])]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_not_between(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "not_between", [2.0, 8.0])]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_invalid_filter_value_is_skipped(self):
        uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(uid, "number", "greater_than", "not_a_number")
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        # Should skip invalid value and return empty Q
        assert result == Q()


# ---------------------------------------------------------------------------
# Number filters with ** sub-field (thumbs_up/thumbs_down counts, categorical counts)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationSubFieldNumberFilters:
    """Number filter on sub-fields via ** separator in column_id."""

    def test_thumbs_up_count_greater_than(self):
        uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(f"{uid}**thumbs_up", "number", "greater_than", 2)
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_thumbs_down_count_between(self):
        uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(f"{uid}**thumbs_down", "number", "between", [1, 5])
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_categorical_value_count_equals(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(f"{uid}**yes", "number", "equals", 3)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_sub_field_less_than_or_equal(self):
        uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(
                f"{uid}**some_value", "number", "less_than_or_equal", 10
            )
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_sub_field_not_between(self):
        uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(
                f"{uid}**thumbs_up", "number", "not_between", [0, 1]
            )
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()


# ---------------------------------------------------------------------------
# Boolean filters (thumbs up/down)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationBooleanFilters:
    """Boolean filter for thumbs up/down annotations."""

    def test_filter_value_up_string(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", "up")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_filter_value_down_string(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", "down")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_filter_value_true_string(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", "true")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_filter_value_bool_true(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", True)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_filter_value_bool_false(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", False)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_filter_value_unrecognized_string_returns_empty(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", "maybe")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        # "maybe" is not recognized, so no condition is added
        assert result == Q()


# ---------------------------------------------------------------------------
# Thumbs filters (dedicated filter type for thumbs_up_down annotations)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationThumbsFilters:
    """Dedicated `thumbs` filter type — accepts arrays for multi-select.

    Distinct from `categorical` which targets choice annotations and from
    `boolean` which is single-value.
    """

    def test_thumbs_in_with_display_labels(self):
        uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(
                uid, "thumbs", "in", ["Thumbs Up", "Thumbs Down"]
            )
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_thumbs_single_up(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "thumbs", "is", "Thumbs Up")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_thumbs_storage_tokens(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "thumbs", "in", ["up", "down"])]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_thumbs_not_in(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "thumbs", "not_in", ["Thumbs Down"])]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_thumbs_unrecognized_returns_empty(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "thumbs", "in", ["maybe"])]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result == Q()


# ---------------------------------------------------------------------------
# Text filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationTextFilters:
    """Text filter for text annotations.

    Text filters use Exists subqueries against TraceAnnotation so that
    string operations (contains, starts_with, etc.) work on the actual
    annotation_value text field. Extra annotations are not populated.
    """

    def test_contains(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "contains", "hello")]
        result, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()
        assert extra == {}

    def test_not_contains(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "not_contains", "bad")]
        result, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()
        assert extra == {}

    def test_equals(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "equals", "exact match")]
        result, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()
        assert extra == {}

    def test_not_equals(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "not_equals", "bad value")]
        result, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()
        assert extra == {}

    def test_starts_with(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "starts_with", "hello")]
        result, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()
        assert extra == {}

    def test_ends_with(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "ends_with", "world")]
        result, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()
        assert extra == {}

    def test_text_filters_return_empty_extra(self):
        """Text filters use Exists subqueries, so extra_annotations is always empty."""
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "contains", "test")]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}


# ---------------------------------------------------------------------------
# Array filters (categorical)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationArrayFilters:
    """Array filter for categorical annotations (count > 0)."""

    def test_single_value(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "array", "contains", "yes")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_multiple_values(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "array", "contains", ["yes", "no"])]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()


# ---------------------------------------------------------------------------
# Annotator filter (per-label, via has_key on JSON map)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationAnnotatorTypeFilter:
    """Per-label annotator filter using has_key on the annotators JSON map."""

    def test_single_user_id(self):
        uid = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "annotator", "equals", user_id)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_multiple_user_ids(self):
        uid = str(uuid.uuid4())
        user_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        filters = [_make_annotation_filter(uid, "annotator", "equals", user_ids)]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_empty_filter_value_returns_empty_q(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "annotator", "equals", "")]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        # Empty string is falsy, so no condition added
        assert result == Q()


# ---------------------------------------------------------------------------
# General annotator filter (column_id=annotator, Exists subquery)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationGeneralAnnotatorFilter:
    """General annotator filter using Exists subquery on TraceAnnotation."""

    def test_single_annotator(self):
        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": user_id,
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_list_of_annotators(self):
        user_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        filters = [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": user_ids,
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result != Q()

    def test_empty_value_returns_empty_q(self):
        filters = [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "",
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result == Q()


# ---------------------------------------------------------------------------
# My annotations filter (column_id=my_annotations)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationMyAnnotationsFilter:
    """Filter for 'my annotations' using Exists subquery."""

    def test_my_annotations_true(self):
        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id
        )
        assert result != Q()

    def test_my_annotations_true_string(self):
        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": "true",
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id
        )
        assert result != Q()

    def test_my_annotations_false_returns_empty_q(self):
        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": False,
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id
        )
        assert result == Q()

    def test_my_annotations_without_user_id_returns_empty_q(self):
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=None
        )
        assert result == Q()


# ---------------------------------------------------------------------------
# extra_annotations return value
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationExtraAnnotations:
    """Verify extra_annotations dict is populated only for text filters."""

    def test_number_filter_returns_empty_extra(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "greater_than", 3.0)]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}

    def test_boolean_filter_returns_empty_extra(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "boolean", "equals", "up")]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}

    def test_array_filter_returns_empty_extra(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "array", "contains", "yes")]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}

    def test_annotator_type_filter_returns_empty_extra(self):
        uid = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "annotator", "equals", user_id)]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}

    def test_my_annotations_returns_empty_extra(self):
        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id
        )
        assert extra == {}

    def test_text_filter_returns_empty_extra(self):
        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "text", "contains", "hello")]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}

    def test_multiple_text_filters_return_empty_extra(self):
        uid1 = str(uuid.uuid4())
        uid2 = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(uid1, "text", "contains", "hello"),
            _make_annotation_filter(uid2, "text", "starts_with", "world"),
        ]
        _, extra = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert extra == {}


# ---------------------------------------------------------------------------
# span_filter_kwargs parameter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationSpanFilterKwargs:
    """Verify span_filter_kwargs is passed to Exists subqueries."""

    def test_my_annotations_uses_custom_span_filter_kwargs(self):
        from django.db.models import OuterRef

        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        custom_kwargs = {
            "observation_span__project_version_id": OuterRef("project_version_id")
        }
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id, span_filter_kwargs=custom_kwargs
        )
        assert result != Q()

    def test_annotator_filter_uses_custom_span_filter_kwargs(self):
        from django.db.models import OuterRef

        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": user_id,
                },
            }
        ]
        custom_kwargs = {
            "observation_span__project_version_id": OuterRef("project_version_id")
        }
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, span_filter_kwargs=custom_kwargs
        )
        assert result != Q()

    def test_annotator_list_uses_custom_span_filter_kwargs(self):
        from django.db.models import OuterRef

        user_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        filters = [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": user_ids,
                },
            }
        ]
        custom_kwargs = {
            "observation_span__project_version_id": OuterRef("project_version_id")
        }
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, span_filter_kwargs=custom_kwargs
        )
        assert result != Q()

    def test_default_span_filter_kwargs_when_none(self):
        """When span_filter_kwargs is not provided, Exists subqueries still work."""
        user_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            }
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id, span_filter_kwargs=None
        )
        assert result != Q()

    def test_value_filters_unaffected_by_span_filter_kwargs(self):
        """Number/boolean/array filters operate on annotated fields, not Exists
        subqueries, so they should produce the same Q regardless of
        span_filter_kwargs."""
        from django.db.models import OuterRef

        uid = str(uuid.uuid4())
        filters = [_make_annotation_filter(uid, "number", "greater_than", 3.0)]

        result_default, _ = (
            FilterEngine.get_filter_conditions_for_voice_call_annotations(filters)
        )
        result_custom, _ = (
            FilterEngine.get_filter_conditions_for_voice_call_annotations(
                filters,
                span_filter_kwargs={
                    "observation_span__project_version_id": OuterRef(
                        "project_version_id"
                    )
                },
            )
        )
        assert result_default == result_custom


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVoiceCallAnnotationCombinedFilters:
    """Multiple filter types combined in a single call."""

    def test_annotation_and_my_annotations_combined(self):
        uid = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(uid, "number", "greater_than", 3.0),
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=user_id
        )
        assert result != Q()

    def test_non_annotation_filters_are_ignored(self):
        """Filters with non-ANNOTATION col_type and non-special column_ids are skipped."""
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01", "2026-01-01"],
                },
            },
        ]
        result, _ = FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters
        )
        assert result == Q()


# ---------------------------------------------------------------------------
# Column config: update_span_column_config_based_on_annotations
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateSpanColumnConfigAnnotators:
    """Test that column config includes annotator details from DB."""

    _numeric_settings = {
        "min": 0,
        "max": 100,
        "step_size": 1,
        "display_type": "slider",
    }
    _text_settings = {
        "placeholder": "Enter text",
        "max_length": 500,
        "min_length": 1,
    }
    _categorical_settings = {
        "options": [{"label": "yes"}, {"label": "no"}],
        "auto_annotate": False,
        "strategy": None,
        "multi_choice": False,
        "rule_prompt": "",
    }

    def test_config_includes_annotator_map(
        self, project, observation_span, user, organization
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.helper import update_span_column_config_based_on_annotations

        label = AnnotationsLabels.objects.create(
            name="Test Numeric",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.NUMERIC.value,
            settings=self._numeric_settings,
        )
        TraceAnnotation.objects.create(
            trace=observation_span.trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value_float=80.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"value": 80.0},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        config = update_span_column_config_based_on_annotations([], labels)

        assert len(config) == 1
        annotators = config[0].get("annotators")
        assert annotators is not None
        assert str(user.id) in annotators
        assert annotators[str(user.id)]["user_name"] == user.name

    def test_config_with_no_annotations_has_none_annotators(
        self, project, organization
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from tracer.utils.helper import update_span_column_config_based_on_annotations

        label = AnnotationsLabels.objects.create(
            name="Empty Label",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.TEXT.value,
            settings=self._text_settings,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        config = update_span_column_config_based_on_annotations([], labels)

        assert len(config) == 1
        assert config[0].get("annotators") is None

    def test_config_categorical_includes_choices(self, project, organization):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from tracer.utils.helper import update_span_column_config_based_on_annotations

        label = AnnotationsLabels.objects.create(
            name="Cat Label",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.CATEGORICAL.value,
            settings=self._categorical_settings,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        config = update_span_column_config_based_on_annotations([], labels)

        assert len(config) == 1
        assert config[0]["choices"] == ["yes", "no"]
        assert config[0]["output_type"] == "list"

    def test_config_thumbs_up_down_type(self, project, organization):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from tracer.utils.helper import update_span_column_config_based_on_annotations

        label = AnnotationsLabels.objects.create(
            name="Thumbs",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
            settings={},
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        config = update_span_column_config_based_on_annotations([], labels)

        assert len(config) == 1
        assert config[0]["output_type"] == "boolean"
        assert config[0]["annotation_label_type"] == "thumbs_up_down"

    def test_multiple_annotators_for_same_label(
        self, project, observation_span, user, organization
    ):
        from accounts.models.user import User
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.helper import update_span_column_config_based_on_annotations

        label = AnnotationsLabels.objects.create(
            name="Multi Annotator",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.NUMERIC.value,
            settings=self._numeric_settings,
        )

        # Create second user
        user2 = User.objects.create_user(
            email="annotator2@futureagi.com",
            password="testpass123",
            name="Second Annotator",
            organization=organization,
        )

        TraceAnnotation.objects.create(
            trace=observation_span.trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value_float=7.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"value": 7.0},
            score_source="human",
            organization=organization,
        )
        TraceAnnotation.objects.create(
            trace=observation_span.trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user2,
            annotation_value_float=9.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user2,
            value={"value": 9.0},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        config = update_span_column_config_based_on_annotations([], labels)

        annotators = config[0].get("annotators")
        assert annotators is not None
        assert len(annotators) == 2
        assert str(user.id) in annotators
        assert str(user2.id) in annotators


# ---------------------------------------------------------------------------
# JSONBObjectAgg aggregate class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJSONBObjectAgg:
    """Test the custom JSONBObjectAgg aggregate class."""

    def _make_agg_class(self):
        """Build JSONBObjectAgg without importing trace.py (avoids circular import)."""
        from django.db.models import Aggregate, JSONField

        class JSONBObjectAgg(Aggregate):
            function = "jsonb_object_agg"
            output_field = JSONField()

        return JSONBObjectAgg

    def test_class_attributes(self):
        cls = self._make_agg_class()
        assert cls.function == "jsonb_object_agg"

    def test_instantiation(self):
        from django.db.models import F, JSONField, TextField
        from django.db.models.functions import Cast

        cls = self._make_agg_class()
        agg = cls(Cast(F("user_id"), TextField()), F("data"))
        assert agg is not None
        assert isinstance(agg.output_field, JSONField)


# ---------------------------------------------------------------------------
# Filter separation logic (annotation vs eval filters)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnnotationFilterSeparation:
    """Test that the filter separation logic correctly partitions filters.

    The view endpoints use a list comprehension to separate
    ANNOTATION filters (and my_annotations/annotator column IDs)
    from eval metric filters, routing each to the appropriate FilterEngine
    method.
    """

    @staticmethod
    def _separate_filters(filters):
        """Replicate the filter separation logic from the view endpoints."""
        annotation_col_types = {"ANNOTATION"}
        annotation_column_ids = {"my_annotations", "annotator"}
        non_annotation_filters = [
            f
            for f in filters
            if f.get("col_type") not in annotation_col_types
            and (f.get("column_id") or f.get("columnId")) not in annotation_column_ids
        ]
        return non_annotation_filters

    def test_voice_annotation_excluded_from_eval_filters(self):
        filters = [
            _make_annotation_filter(str(uuid.uuid4()), "number", "greater_than", 3.0),
        ]
        result = self._separate_filters(filters)
        assert result == []

    def test_eval_metric_passes_through(self):
        filters = [
            {
                "column_id": str(uuid.uuid4()),
                "col_type": "EVAL_METRIC",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0.8,
                },
            },
        ]
        result = self._separate_filters(filters)
        assert len(result) == 1

    def test_my_annotations_excluded(self):
        filters = [
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ]
        result = self._separate_filters(filters)
        assert result == []

    def test_annotator_column_excluded(self):
        filters = [
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": str(uuid.uuid4()),
                },
            },
        ]
        result = self._separate_filters(filters)
        assert result == []

    def test_mixed_filters_separated_correctly(self):
        """ANNOTATION and annotator/my_annotations are excluded,
        everything else passes through."""
        ann_uid = str(uuid.uuid4())
        eval_uid = str(uuid.uuid4())
        filters = [
            _make_annotation_filter(ann_uid, "number", "greater_than", 3.0),
            {
                "column_id": eval_uid,
                "col_type": "EVAL_METRIC",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 0.95,
                },
            },
            {
                "column_id": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": str(uuid.uuid4()),
                },
            },
            {
                "column_id": "created_at",
                "col_type": "SYSTEM",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2025-01-01", "2026-01-01"],
                },
            },
        ]
        result = self._separate_filters(filters)
        assert len(result) == 2
        ids = [f["column_id"] for f in result]
        assert eval_uid in ids
        assert "created_at" in ids

    def test_columnId_key_also_checked(self):
        """The separation uses (f.get('column_id') or f.get('columnId'))
        so camelCase key should also be excluded."""
        filters = [
            {
                "columnId": "my_annotations",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ]
        result = self._separate_filters(filters)
        assert result == []

    def test_empty_filters(self):
        assert self._separate_filters([]) == []

    def test_all_eval_filters_pass_through(self):
        filters = [
            {
                "column_id": str(uuid.uuid4()),
                "col_type": "EVAL_METRIC",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 1.0,
                },
            },
            {
                "column_id": "latency",
                "col_type": "SYSTEM",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "less_than",
                    "filter_value": 5000,
                },
            },
        ]
        result = self._separate_filters(filters)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Integration: _build_annotation_subqueries helper
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBuildAnnotationSubqueries:
    """Integration tests for TraceView._build_annotation_subqueries.

    These verify that the helper correctly annotates querysets with
    aggregated annotation data for each annotation type.
    """

    _numeric_settings = {
        "min": 0,
        "max": 100,
        "step_size": 1,
        "display_type": "slider",
    }
    _categorical_settings = {
        "options": [{"label": "yes"}, {"label": "no"}],
        "auto_annotate": False,
        "strategy": None,
        "multi_choice": False,
        "rule_prompt": "",
    }
    _text_settings = {
        "placeholder": "Enter text",
        "max_length": 500,
        "min_length": 1,
    }

    def test_numeric_annotation_returns_floored_avg(
        self, project, trace, observation_span, user, organization
    ):
        from accounts.models.user import User
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        label = AnnotationsLabels.objects.create(
            name="Numeric Score",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.NUMERIC.value,
            settings=self._numeric_settings,
        )
        user2 = User.objects.create_user(
            email="annotator2_num@futureagi.com",
            password="testpass123",
            name="Annotator Two",
            organization=organization,
        )
        # User1 scores 7.0, User2 scores 8.0 → avg 7.5 → floor 7
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value_float=7.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"value": 7.0},
            score_source="human",
            organization=organization,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user2,
            annotation_value_float=8.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user2,
            value={"value": 8.0},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is not None
        assert ann["score"] == 7  # floor(7.5) = 7
        assert str(user.id) in ann["annotators"]
        assert str(user2.id) in ann["annotators"]

    def test_star_annotation_returns_floored_avg(
        self, project, trace, observation_span, user, organization
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        label = AnnotationsLabels.objects.create(
            name="Star Rating",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.STAR.value,
            settings={"no_of_stars": 5},
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value_float=4.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"rating": 4.0},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is not None
        assert ann["score"] == 4
        assert str(user.id) in ann["annotators"]

    def test_thumbs_up_down_annotation_counts(
        self, project, trace, observation_span, user, organization
    ):
        from accounts.models.user import User
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        label = AnnotationsLabels.objects.create(
            name="Thumbs",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
            settings={},
        )
        user2 = User.objects.create_user(
            email="annotator2_th@futureagi.com",
            password="testpass123",
            name="Annotator Two",
            organization=organization,
        )
        # User1 thumbs up, User2 thumbs down
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value_bool=True,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"value": "up"},
            score_source="human",
            organization=organization,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user2,
            annotation_value_bool=False,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user2,
            value={"value": "down"},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is not None
        assert ann["thumbs_up"] == 1
        assert ann["thumbs_down"] == 1
        annotators = ann["annotators"]
        assert str(user.id) in annotators
        assert str(user2.id) in annotators

    def test_categorical_annotation_choice_counts(
        self, project, trace, observation_span, user, organization
    ):
        from accounts.models.user import User
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        label = AnnotationsLabels.objects.create(
            name="Category",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.CATEGORICAL.value,
            settings=self._categorical_settings,
        )
        user2 = User.objects.create_user(
            email="annotator2_cat@futureagi.com",
            password="testpass123",
            name="Annotator Two",
            organization=organization,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value_str_list=["yes"],
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"selected": ["yes"]},
            score_source="human",
            organization=organization,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user2,
            annotation_value_str_list=["no"],
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user2,
            value={"selected": ["no"]},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is not None
        assert ann["yes"] == 1
        assert ann["no"] == 1
        assert str(user.id) in ann["annotators"]
        assert str(user2.id) in ann["annotators"]

    def test_text_annotation_returns_annotators(
        self, project, trace, observation_span, user, organization
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        label = AnnotationsLabels.objects.create(
            name="Text Note",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.TEXT.value,
            settings=self._text_settings,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=user,
            annotation_value="This is a comment",
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"text": "This is a comment"},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is not None
        annotators = ann["annotators"]
        assert str(user.id) in annotators
        assert annotators[str(user.id)]["value"] == "This is a comment"

    def test_no_annotations_returns_none(
        self, project, trace, observation_span, organization
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from tracer.models.trace import Trace
        from tracer.utils.annotations import build_annotation_subqueries

        label = AnnotationsLabels.objects.create(
            name="Empty",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.NUMERIC.value,
            settings=self._numeric_settings,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is None

    def test_multiple_labels_all_annotated(
        self, project, trace, observation_span, user, organization
    ):
        """Multiple labels of different types are all annotated on the queryset."""
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        numeric_label = AnnotationsLabels.objects.create(
            name="Multi Num",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.NUMERIC.value,
            settings=self._numeric_settings,
        )
        text_label = AnnotationsLabels.objects.create(
            name="Multi Text",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.TEXT.value,
            settings=self._text_settings,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=numeric_label,
            observation_span=observation_span,
            user=user,
            annotation_value_float=5.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=numeric_label,
            annotator=user,
            value={"value": 5.0},
            score_source="human",
            organization=organization,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=text_label,
            observation_span=observation_span,
            user=user,
            annotation_value="a note",
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=text_label,
            annotator=user,
            value={"text": "a note"},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(
            id__in=[numeric_label.id, text_label.id]
        )
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        num_ann = getattr(row, f"annotation_{numeric_label.id}", None)
        txt_ann = getattr(row, f"annotation_{text_label.id}", None)
        assert num_ann is not None
        assert num_ann["score"] == 5
        assert txt_ann is not None
        assert str(user.id) in txt_ann["annotators"]

    def test_annotator_name_falls_back_to_email(
        self, project, trace, observation_span, organization
    ):
        """When user.name is empty, the annotator name should fall back to email."""
        from accounts.models.user import User
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.trace import Trace
        from tracer.models.trace_annotation import TraceAnnotation
        from tracer.utils.annotations import build_annotation_subqueries

        nameless_user = User.objects.create_user(
            email="nameless@futureagi.com",
            password="testpass123",
            name="",
            organization=organization,
        )
        label = AnnotationsLabels.objects.create(
            name="Fallback Test",
            project=project,
            organization=organization,
            type=AnnotationTypeChoices.NUMERIC.value,
            settings=self._numeric_settings,
        )
        TraceAnnotation.objects.create(
            trace=trace,
            annotation_label=label,
            observation_span=observation_span,
            user=nameless_user,
            annotation_value_float=9.0,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=nameless_user,
            value={"value": 9.0},
            score_source="human",
            organization=organization,
        )

        labels = AnnotationsLabels.objects.filter(id=label.id)
        qs = Trace.objects.filter(id=trace.id)
        qs = build_annotation_subqueries(qs, labels, organization)
        row = qs.first()

        ann = getattr(row, f"annotation_{label.id}", None)
        assert ann is not None
        annotator = ann["annotators"][str(nameless_user.id)]
        assert annotator["user_name"] == "nameless@futureagi.com"
