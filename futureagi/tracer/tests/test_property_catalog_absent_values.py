"""Qualified absence of a custom attribute is not an unavailable catalog."""

import pytest

from tracer.tests import test_unified_property_catalog_value_reader as subject


def absent_row(**overrides):
    row = subject._definition_row()
    row.update(
        property_rows=0,
        property_definition_variants=0,
        live_binding_count=0,
        project_binding_count=0,
        property_kind="",
        source_adapter="",
        primary_source="",
        value_adapter="",
        name="",
        definition_json="",
        definition_sha256="",
    )
    row.update(overrides)
    return row


@pytest.mark.parametrize("workspace_scope", [False, True])
@pytest.mark.parametrize("attribute_type", ["", "string", "number", "array"])
def test_qualified_absent_custom_attribute_has_no_hot_scan(
    settings, workspace_scope, attribute_type
):
    settings.SECRET_KEY = "absent-values-unit-only"
    executor = subject.FakeExecutor([[subject._activation_row()], [absent_row()]])
    page = subject._read(
        subject._reader(executor),
        scope=subject._scope(workspace_scope=workspace_scope),
        query=subject._query(attribute_type=attribute_type),
    )
    assert page.values == () and page.attribute_types == ()
    assert page.has_more is False and page.next_cursor is None
    assert page.activation_fingerprint == subject.ACTIVATION_SHA
    assert page.window_start == subject.WINDOW_START
    assert page.window_end == subject.WINDOW_END
    assert page.query_count == len(executor.calls) == 2
    proof = executor.calls[1]
    assert proof["params"]["catalog_build_token"] == subject.BUILD_TOKEN
    assert "build_token = %(catalog_build_token)s" in proof["query"]
    assert "AS selected_activation_rows" in proof["query"]
    assert "AS anchor_activation_rows" in proof["query"]
    # The stored digest is FixedString(64). Aggregate a String so an absent
    # match produces "", not 64 NUL bytes which resemble a corrupt payload.
    assert "toString(resolved_property.definition_sha256)" in proof["query"]


@pytest.mark.parametrize(
    "contradiction",
    [
        {"selected_activation_rows": 0},
        {"selected_activation_rows": 2},
        {"anchor_activation_rows": 0},
        {"anchor_activation_rows": 2},
        {"activation_state_conflicts": 1},
        {"activation_lineage_conflicts": 1},
        {"activation_projection_conflicts": 1},
        {"activation_anchor_conflicts": 1},
        {"definition_conflicts": 1},
        {"binding_conflicts": 1},
        {"property_definition_variants": 1},
        {"live_binding_count": 1},
        {"project_binding_count": 1},
        {"definition_json": "{}"},
        {"definition_sha256": "a" * 64},
        {"property_kind": "custom_attribute"},
        {"source_adapter": "span_attribute"},
        {"primary_source": "traces"},
        {"value_adapter": "span_attribute_value"},
        {"name": "customer.plan"},
    ],
)
def test_missing_attribute_does_not_hide_conflicting_or_partial_proof(
    settings, contradiction
):
    settings.SECRET_KEY = "absent-values-unit-only"
    executor = subject.FakeExecutor(
        [[subject._activation_row()], [absent_row(**contradiction)]]
    )
    with pytest.raises(subject.PropertyCatalogValueUnavailable):
        subject._read(subject._reader(executor))
    assert len(executor.calls) == 2


def test_missing_system_definition_is_still_unavailable(settings):
    settings.SECRET_KEY = "absent-values-unit-only"
    executor = subject.FakeExecutor([[subject._activation_row()], [absent_row()]])
    with pytest.raises(subject.PropertyCatalogValueUnavailable) as error:
        subject._read(
            subject._reader(executor),
            query=subject._query(property_id="system_attribute:traces:model"),
        )
    assert error.value.reason == "definition_missing"
    assert len(executor.calls) == 2


def test_cursor_binding_cannot_disappear_as_a_complete_empty_page(settings):
    settings.SECRET_KEY = "absent-values-unit-only"
    token = subject.encode_property_catalog_value_cursor(
        scope=subject._scope(),
        query=subject._query(),
        page_size=10,
        catalog_epoch=3,
        catalog_revision=17,
        activation_fingerprint=subject.ACTIVATION_SHA,
        window_start=subject.WINDOW_START,
        window_end=subject.WINDOW_END,
        order=(1, "0" * 64),
    )
    executor = subject.FakeExecutor([[subject._activation_row()], [absent_row()]])
    with pytest.raises(subject.PropertyCatalogValueUnavailable) as error:
        subject._reader(executor).read_page(
            scope=subject._scope(),
            query=subject._query(),
            page_size=10,
            cursor_token=token,
        )
    assert error.value.reason == "definition_missing"
    assert len(executor.calls) == 2
