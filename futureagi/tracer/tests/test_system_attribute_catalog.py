"""System/custom observation isolation and retained native model semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.filter_value_reads import (
    FILTER_VALUE_CURSOR_MAX_QUERIES,
    read_span_system_filter_value_cursor_page,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.v2.attribute_catalog_codec import encode_catalog_scalar
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    system_property_value_adapter,
)
from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PropertyCatalogValueCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PROPERTY_CATALOG_VALUE_ADAPTER,
    PropertyCatalogValueReader,
)

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SEEN_AT = datetime(2026, 8, 13, 12, tzinfo=UTC)
SCOPE = {
    "organization_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "workspace_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "project_ids": [PROJECT_ID],
    "principal_id": "test-user",
}
CUSTOM_QUERY = {"property_id": "custom_attribute:model", "source": "traces"}
SYSTEM_QUERY = {"property_id": "system_attribute:traces:model", "source": "traces"}


def _row(value):
    encoded = encode_catalog_scalar(value)
    return {
        "attribute_type": "string",
        "value_fingerprint": encoded.fingerprint,
        "value_json": encoded.value_json,
        "value_search_text_folded": encoded.search_text.casefold(),
        "first_seen": SEEN_AT,
        "last_seen": SEEN_AT,
    }


class _Executor:
    def __init__(self, *pages):
        self.pages = iter(pages)
        self.calls = []

    def execute(self, sql, params, **kwargs):
        self.calls.append((sql, params.copy(), kwargs))
        return SimpleNamespace(data=next(self.pages))


def test_model_and_customer_model_have_disjoint_catalog_identities():
    for query, source_kind, value in (
        (CUSTOM_QUERY, "custom_attribute", "customer-value"),
        (SYSTEM_QUERY, "system_attribute", "gpt-4.1"),
    ):
        executor = _Executor([{"attribute_type": "string"}], [_row(value)])
        page = PropertyCatalogValueReader(
            executor, catalog_database="test_index"
        ).read_page(scope=SCOPE, query=query, page_size=10)

        assert page.values[0].value == value
        assert len(executor.calls) == 2
        for sql, params, _kwargs in executor.calls:
            assert "k.source_kind = %(source_kind)s" in sql
            assert params["source_kind"] == source_kind
            assert params["attribute_key"] == "model"
            assert params["organization_id"] == SCOPE["organization_id"]
            assert params["workspace_id"] == SCOPE["workspace_id"]
            assert params["project_ids"] == (PROJECT_ID,)

    assert system_property_value_adapter("traces", "model") == (
        PROPERTY_CATALOG_VALUE_ADAPTER
    )
    assert system_property_value_adapter("traces", "project") != (
        PROPERTY_CATALOG_VALUE_ADAPTER
    )


@pytest.mark.parametrize(
    ("query", "changed"), [(CUSTOM_QUERY, SYSTEM_QUERY), (SYSTEM_QUERY, CUSTOM_QUERY)]
)
def test_system_value_cursor_identity_binds_source_kind(query, changed):
    rows = sorted(
        [_row("gpt-4.1"), _row("customer-value")],
        key=lambda row: (row["value_fingerprint"], row["value_json"]),
    )
    executor = _Executor([{"attribute_type": "string"}], rows)
    reader = PropertyCatalogValueReader(executor, catalog_database="test_index")
    first = reader.read_page(scope=SCOPE, query=query, page_size=1)
    assert first.has_more and first.next_cursor

    with pytest.raises(PropertyCatalogValueCursorError) as error:
        reader.read_page(
            scope=SCOPE, query=changed, page_size=1, cursor_token=first.next_cursor
        )
    assert error.value.code == "cursor_mismatch"
    assert len(executor.calls) == 2  # Reject before either index query.


def test_legacy_value_schema_keeps_immutable_source_kind_and_revision_contract():
    schema = (
        Path(__file__).parents[1]
        / "services/clickhouse/v2/schema/025_property_catalog_data.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS span_attribute_value_catalog" in schema
    assert "catalog_revision  UInt64" in schema
    assert "'custom_attribute' = 1" in schema
    assert "'system_attribute' = 2" in schema
    assert "source_kind,\n    attribute_key,\n    attribute_type" in schema
    assert "span_attribute_catalog_" not in schema


def test_observed_schema_keeps_source_kind_and_exact_value_bytes_in_identity():
    schema = (
        Path(__file__).parents[1] / "services/clickhouse/v2/observed_catalog/schema.sql"
    ).read_text(encoding="utf-8")

    key_schema, value_schema = schema.split(
        "CREATE TABLE IF NOT EXISTS observed_attribute_values", 1
    )
    assert "CREATE TABLE IF NOT EXISTS observed_attribute_keys" in key_schema
    assert (
        "ORDER BY (organization_id, workspace_id, project_id, source_kind, "
        "attribute_key, attribute_type);"
    ) in key_schema
    assert (
        "ORDER BY (organization_id, workspace_id, project_id, source_kind, "
        "attribute_key, attribute_type, value_fingerprint, value_json);"
    ) in value_schema
    for table in (key_schema, value_schema):
        assert "'custom_attribute', 'system_attribute'" in table
        assert "SimpleAggregateFunction(min, DateTime64" in table
        assert "SimpleAggregateFunction(max, DateTime64" in table


@pytest.mark.parametrize("cursor", [False, True])
def test_native_model_values_exclude_empty_and_nil_uuid(cursor):
    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return SimpleNamespace(data=[])

    analytics = SimpleNamespace(execute_ch_query=execute)
    if cursor:
        result = read_span_system_filter_value_cursor_page(
            analytics,
            project_ids=[PROJECT_ID],
            metric_name="model",
            page_size=10,
            window_start=SEEN_AT,
            window_end=SEEN_AT + timedelta(minutes=1),
        )
    else:
        result = read_span_system_filter_values(
            analytics, project_ids=[PROJECT_ID], metric_name="model", now=SEEN_AT
        )

    assert result.values == ()
    # Cursor reads grow empty time slices within their per-request query cap.
    assert 1 <= len(calls) <= (FILTER_VALUE_CURSOR_MAX_QUERIES if cursor else 1)
    for sql, params, _kwargs in calls:
        assert "raw_picker_value IS NOT NULL" in sql
        assert "'', '00000000-0000-0000-0000-000000000000'" in sql
        assert "latest_is_deleted = 0" in sql
        assert params["project_ids"] == (PROJECT_ID,)
