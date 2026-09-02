from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from tracer.services.clickhouse.v2 import attribute_catalog_backfill as backfill
from tracer.services.clickhouse.v2.attribute_catalog_builder import (
    CatalogBuildLimits,
    CatalogScope,
    build_catalog_rows,
)
from tracer.services.clickhouse.v2.attribute_catalog_cutover import (
    CATALOG_SYSTEM_PROJECTION_VERSION,
    CATALOG_SYSTEM_VALUE_METRICS,
)
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    AttributeCatalogReader,
)

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SEEN_AT = datetime(2026, 8, 13, 12, tzinfo=UTC)
LIMITS = CatalogBuildLimits(8, 8, 64 * 1024)


def _build(source_kind: str, value: str):
    return build_catalog_rows(
        scope=CatalogScope(
            project_id=PROJECT_ID,
            seen_at=SEEN_AT,
            catalog_epoch=302,
            source_kind=source_kind,
        ),
        attrs_string={"model": value},
        attrs_number={},
        attrs_bool={},
        attributes_extra={},
        limits=LIMITS,
    )


def test_model_and_customer_model_have_disjoint_catalog_identities() -> None:
    custom = _build("custom_attribute", "customer-value")
    system = _build("system_attribute", "gpt-4.1")

    assert custom.key_rows[0].attribute_key == system.key_rows[0].attribute_key
    assert custom.key_rows[0].source_kind == "custom_attribute"
    assert system.key_rows[0].source_kind == "system_attribute"
    assert custom.value_rows[0].value_search_text == "customer-value"
    assert system.value_rows[0].value_search_text == "gpt-4.1"


def test_system_value_query_and_cursor_identity_bind_source_kind() -> None:
    reader = AttributeCatalogReader(
        object(),  # SQL is not executed by this pure identity check.
        project_ids=(PROJECT_ID,),
        catalog_epoch=302,
        window_start=SEEN_AT,
        window_end=datetime(2026, 8, 14, 12, tzinfo=UTC),
        required_projection_version=CATALOG_SYSTEM_PROJECTION_VERSION,
    )
    custom = reader._value_query_fingerprint(  # noqa: SLF001
        attribute_key="model",
        attribute_types=("string",),
        normalized_search="",
        page_size=10,
        source_kind="custom_attribute",
    )
    system = reader._value_query_fingerprint(  # noqa: SLF001
        attribute_key="model",
        attribute_types=("string",),
        normalized_search="",
        page_size=10,
        source_kind="system_attribute",
    )
    assert custom != system
    assert "source_kind = %(catalog_source_kind)s" in reader._value_page_sql  # noqa: SLF001
    assert CATALOG_SYSTEM_VALUE_METRICS == frozenset({"model"})


def test_value_catalog_pins_source_kind_and_revision_in_clean_schema() -> None:
    from tracer.services.clickhouse.v2.property_catalog import reader

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
    # Definition cursors resolve against the matching activated revision; this
    # is what prevents a later Kafka write from moving an issued page.
    assert "catalog_revision = %(catalog_revision)s" in reader._ACTIVATION_SQL


def test_model_projection_matches_authoritative_nil_uuid_exclusion() -> None:
    nil_uuid = "00000000-0000-0000-0000-000000000000"
    assert nil_uuid in backfill._SOURCE_PAYLOAD_SQL_TEMPLATE
    source = backfill._parse_source_row(  # noqa: SLF001
        {
            "observation_type": "span",
            "service_name": "svc",
            "trace_id": "trace",
            "span_id": "span",
            "seen_at": SEEN_AT,
            "source_attribute_entries": 0,
            "source_attribute_bytes": 0,
            "attrs_string": {},
            "attrs_number": {},
            "attrs_bool": {},
            "attributes_extra": "{}",
            "system_model": "",
            "system_model_complete": 1,
        },
        SimpleNamespace(
            max_source_attribute_entries=1,
            max_source_attribute_bytes=1,
        ),
    )
    assert source.system_attributes == {}
    assert source.gap_reasons == ()
