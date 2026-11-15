"""Span Tags availability comes from the API, not persisted view preferences."""

from copy import deepcopy

import pytest

from tracer.models.saved_view import SavedView
from tracer.serializers.observation_span import SpanObserveListResponseSerializer
from tracer.serializers.saved_view import (
    SavedViewDetailSerializer,
    SavedViewListSerializer,
    SavedViewUpdateSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    canonical_system_definitions,
    system_property_value_adapter,
)
from tracer.utils.helper import get_default_span_config
from tracer.utils.property_registry import validate_property_filter_binding

# The pre-Tags Observe configuration, deliberately independent of the helper.
OLD_SPAN_IDS = (
    "span_name",
    "status",
    "input",
    "output",
    "latency_ms",
    "total_tokens",
    "cost",
    "model",
    "start_time",
    "span_id",
    "trace_id",
    "prompt_tokens",
    "completion_tokens",
    "provider",
    "user_id",
    "user_id_type",
    "user_id_hash",
)


def _tags_column(config):
    tags = [column for column in config if column["id"] == "tags"]
    assert len(tags) == 1
    return tags[0]


@pytest.mark.parametrize("include_user_fields", [False, True])
def test_fresh_span_config_offers_hidden_system_tags(include_user_fields):
    config = get_default_span_config(include_user_fields=include_user_fields)
    tags = _tags_column(config)

    assert tags["name"] == "Tags"
    assert tags["is_visible"] is False
    assert tags["group_by"] is None
    assert tags["property_id"] == "system_attribute:spans:tags"
    assert tags["property_kind"] == "system_attribute"
    assert tags["property_source"] == "traces"
    assert len({column["id"] for column in config}) == len(config)
    old_ids = OLD_SPAN_IDS if include_user_fields else OLD_SPAN_IDS[:14]
    assert tuple(column["id"] for column in config if column["id"] != "tags") == old_ids

    wire = SpanObserveListResponseSerializer(
        data={
            "status": True,
            "result": {"config": config, "metadata": {"total_rows": 0}, "table": []},
        }
    )
    assert wire.is_valid(), wire.errors
    assert (
        _tags_column(wire.validated_data["result"]["config"])["property_id"]
        == tags["property_id"]
    )


def test_span_tags_is_one_registered_array_dimension_on_trace_transport():
    definitions = [
        definition
        for definition in canonical_system_definitions()
        if definition.property_id == "system_attribute:spans:tags"
    ]
    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "tags"
    assert definition.display_name == "Tags"
    assert definition.value_type == "array"
    assert definition.role == "dimension"
    assert definition.primary_source == "spans"
    assert definition.value_adapter == "system_traces"
    assert system_property_value_adapter("spans", "tags") == "system_traces"
    decoded = validate_property_filter_binding(
        definition.property_id,
        column_id="tags",
        column_type="SYSTEM_METRIC",
        source="traces",
    )
    assert decoded["metric_name"] == "tags"
    with pytest.raises(ValueError, match="does not match column_id"):
        validate_property_filter_binding(
            definition.property_id,
            column_id="tag",
            column_type="SYSTEM_METRIC",
            source="traces",
        )


@pytest.mark.parametrize(
    "serializer_class", [SavedViewListSerializer, SavedViewDetailSerializer]
)
@pytest.mark.parametrize("tags_visible", [None, False, True])
def test_saved_span_preferences_survive_new_authoritative_tags_config(
    serializer_class, tags_visible
):
    # Saved views persist AG Grid state rather than the authoritative definitions.
    # Reorder/hide old fields and retain a custom column to guard against resets.
    columns = [
        {"colId": field_id, "hide": index % 2 == 0, "width": 130 + index}
        for index, field_id in enumerate(reversed(OLD_SPAN_IDS))
    ]
    columns.insert(3, {"colId": "custom_attribute:customer.segment", "hide": False})
    if tags_visible is not None:
        columns.insert(2, {"colId": "tags", "hide": not tags_visible, "width": 310})
    config = {
        "display": {
            "columnState": columns,
            "cellHeight": "compact",
            "customColumns": [
                {
                    "id": "custom_attribute:customer.segment",
                    "name": "Customer segment",
                    "groupBy": "Custom Columns",
                    "isVisible": True,
                }
            ],
        },
        "filters": [],
    }
    original = deepcopy(config)
    view = SavedView(name="Legacy spans", tab_type="spans", config=config)

    for _ in range(3):
        persisted = serializer_class(view).data["config"]
        assert persisted == original
        update = SavedViewUpdateSerializer(data={"config": persisted}, partial=True)
        assert update.is_valid(), update.errors
        assert update.validated_data["config"] == original
        assert view.config == original
        # The API's independently refreshed schema supplies the missing field;
        # saved visibility/order remain overrides consumed by the existing UI merge.
        tags = _tags_column(get_default_span_config(include_user_fields=True))
        assert tags["is_visible"] is False
        assert tags["property_id"] == "system_attribute:spans:tags"


def test_span_default_reads_are_independent_and_do_not_accumulate_tags():
    first = get_default_span_config(include_user_fields=True)
    expected = deepcopy(first)
    _tags_column(first)["is_visible"] = True
    first.reverse()
    for _ in range(3):
        current = get_default_span_config(include_user_fields=True)
        assert current == expected
        assert _tags_column(current)["is_visible"] is False
