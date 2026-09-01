from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)
from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    BuildPlanStream,
    ManifestStreamRole,
    RevisionBuildPlan,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_json_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.reader import _ACTIVATION_SQL
from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PropertyCatalogValueCursorError,
    encode_property_catalog_value_cursor,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    _ATTRIBUTE_TYPE_RANK,
    PropertyCatalogValueNotReady,
    PropertyCatalogValueReader,
    PropertyCatalogValueUnavailable,
    _definition_ctes,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
OTHER_WORKSPACE_ID = "22222222-2222-4222-8222-222222222223"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
OTHER_PROJECT_ID = "33333333-3333-4333-8333-333333333334"
ACTIVATION_SHA = "a" * 64
MANIFEST_SHA = "b" * 64
BUILD_TOKEN = "44444444-4444-4444-8444-444444444444"
ANCHOR_BUILD_TOKEN = "44444444-4444-4444-8444-444444444443"
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 14, tzinfo=UTC)
WINDOW_START_US = 1_785_542_400_000_000
WINDOW_END_US = 1_786_665_600_000_000


def test_clickhouse25_aggregate_inputs_are_raw_qualified() -> None:
    assert "FROM versioned AS versioned_rows" in _ACTIVATION_SQL
    assert (
        "argMax(versioned_rows.projection_version, versioned_rows._version)"
        in _ACTIVATION_SQL
    )
    assert "versioned_rows.status = 'active'" in _ACTIVATION_SQL
    assert "argMax(projection_version, _version)" not in _ACTIVATION_SQL
    assert "property_catalog_source_streams" in _ACTIVATION_SQL
    assert "reservation_states.build_plan_json" in _ACTIVATION_SQL
    assert "anchor_reservations.build_plan_json AS anchor_build_plan_json" in (
        _ACTIVATION_SQL
    )

    definitions_sql = _definition_ctes("property_catalog_dev_test")
    assert "FROM lineage_versioned AS versioned_rows" in definitions_sql
    assert "FROM latest_binding_rows AS binding" in definitions_sql
    assert "any(binding.property_id) AS property_id" in definitions_sql
    assert "binding.property_id," in definitions_sql
    assert "any(property_id) AS property_id" not in definitions_sql


def test_property_value_reader_accepts_isolated_production_database_namespace() -> None:
    with pytest.raises(ValueError, match="requires control selection"):
        PropertyCatalogValueReader(
            FakeExecutor([]),
            catalog_database="property_catalog",
        )
    reader = PropertyCatalogValueReader(
        FakeExecutor([]),
        catalog_database="property_catalog",
        activation_selector=SimpleNamespace(select_target=lambda **_kwargs: None),
    )

    assert "`property_catalog`" in reader._activation_sql
    assert "`property_catalog`" in reader._value_page_sql


def test_value_rank_casts_qualified_raw_enum8_before_string_alias_rewrite() -> None:
    schema_sql = (
        Path(__file__).resolve().parents[1]
        / "services/clickhouse/v2/schema/025_property_catalog_data.sql"
    ).read_text(encoding="utf-8")
    enum_start = schema_sql.index("attribute_type    Enum8(")
    enum_end = schema_sql.index("    ),", enum_start)
    enum_sql = schema_sql[enum_start:enum_end]
    for attribute_type, rank in _ATTRIBUTE_TYPE_RANK.items():
        assert f"'{attribute_type}' = {rank}" in enum_sql

    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    _read(_reader(executor), page_size=1)

    sql = executor.calls[-1]["query"]
    string_projection = "toString(grouped_value.attribute_type) AS attribute_type"
    rank_projection = "toInt8(grouped_value.attribute_type) AS attribute_type_rank"
    assert "FROM grouped_values AS grouped_value" in sql
    assert sql.index(string_projection) < sql.index(rank_projection)
    assert "toInt8(attribute_type)" not in sql
    assert "toInt8(toString(attribute_type))" not in sql


def _scope(*, project_ids=(PROJECT_ID,), workspace_scope=False):
    scope = {
        "principal_id": "user-1",
        "auth_type": "Token",
        "auth_id": "token-1",
        "organization_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "project_ids": project_ids,
    }
    if workspace_scope:
        scope["workspace_scope"] = True
    return scope


def _query(**overrides):
    values = {
        "property_id": "custom_attribute:customer.plan",
        "source": "traces",
        "attribute_type": "",
        "search": "",
    }
    values.update(overrides)
    return values


def _build_plan(
    row,
    *,
    covered_project_ids,
    workspace_id=WORKSPACE_ID,
    span_since_us=WINDOW_START_US,
    span_until_us=WINDOW_END_US,
):
    streams = []
    stream_index = 1
    for adapter in SourceAdapter:
        roles = (
            (
                ManifestStreamRole.DEFINITIONS,
                ManifestStreamRole.VALUES,
                ManifestStreamRole.HOT_VALUES,
                ManifestStreamRole.SOURCE_AUDIT,
            )
            if adapter is SourceAdapter.SPAN_ATTRIBUTE
            else (ManifestStreamRole.DEFINITIONS,)
        )
        for role in roles:
            streams.append(
                BuildPlanStream(
                    source_adapter=adapter,
                    role=role,
                    producer_stream_id=f"55555555-5555-4555-8555-{stream_index:012d}",
                    source_cutoff_label=f"{adapter.value}_{role.value}",
                    source_version_fence=stream_index,
                )
            )
            stream_index += 1
    return RevisionBuildPlan(
        organization_id=ORG_ID,
        workspace_id=workspace_id,
        catalog_epoch=row["catalog_epoch"],
        catalog_revision=row["catalog_revision"],
        build_token=row["build_token"],
        projection_version=row["projection_version"],
        source_scope=BuildPlanSourceScope(
            project_ids=tuple(covered_project_ids),
            span_since_us=span_since_us,
            span_until_us=span_until_us,
        ),
        streams=tuple(streams),
    )


def _activation_row(
    *,
    covered_project_ids=(PROJECT_ID,),
    build_plan_workspace_id=WORKSPACE_ID,
    source_span_since_us=WINDOW_START_US,
    source_span_until_us=WINDOW_END_US,
    anchor_span_since_us=None,
    anchor_span_until_us=None,
    **overrides,
):
    row = {
        "catalog_epoch": 3,
        "catalog_revision": 17,
        "build_token": BUILD_TOKEN,
        "projection_version": 1,
        "lifecycle_mode": "incremental",
        "lineage_anchor_revision": 1,
        "activation_sequence": 9,
        "source_manifest_sha256": MANIFEST_SHA,
        "activation_sha256": ACTIVATION_SHA,
        "status": "active",
        "qualified_at": "present",
        "state_version": 2,
        "latest_state_variants": 1,
        "active_builds": 1,
    }
    row.update(overrides)
    plan = _build_plan(
        row,
        covered_project_ids=covered_project_ids,
        workspace_id=build_plan_workspace_id,
        span_since_us=source_span_since_us,
        span_until_us=source_span_until_us,
    )
    row.setdefault("reservation_projection_version", row["projection_version"])
    row.setdefault("build_plan_json", plan.canonical_json)
    row.setdefault("build_lease_sha256", plan.sha256)
    row.setdefault("latest_reservation_variants", 1)
    anchor_revision = row["lineage_anchor_revision"]
    plan_anchor_revision = (
        anchor_revision
        if type(anchor_revision) is int
        and 1 <= anchor_revision <= row["catalog_revision"]
        else 1
    )
    anchor_build_token = (
        row["build_token"]
        if plan_anchor_revision == row["catalog_revision"]
        else ANCHOR_BUILD_TOKEN
    )
    anchor_row = {
        "catalog_epoch": row["catalog_epoch"],
        "catalog_revision": plan_anchor_revision,
        "build_token": anchor_build_token,
        "projection_version": row["projection_version"],
    }
    anchor_plan = _build_plan(
        anchor_row,
        covered_project_ids=covered_project_ids,
        workspace_id=build_plan_workspace_id,
        span_since_us=(
            source_span_since_us
            if anchor_span_since_us is None
            else anchor_span_since_us
        ),
        span_until_us=(
            source_span_until_us
            if anchor_span_until_us is None
            else anchor_span_until_us
        ),
    )
    row.setdefault("anchor_catalog_revision", anchor_revision)
    row.setdefault("anchor_build_token", anchor_build_token)
    row.setdefault("anchor_projection_version", row["projection_version"])
    row.setdefault(
        "anchor_lifecycle_mode",
        (
            row["lifecycle_mode"]
            if anchor_revision == row["catalog_revision"]
            else "initial_backfill"
        ),
    )
    row.setdefault("anchor_lineage_anchor_revision", anchor_revision)
    row.setdefault("anchor_latest_state_variants", 1)
    row.setdefault("anchor_latest_active_states", 1)
    row.setdefault("anchor_active_builds", 1)
    row.setdefault("anchor_reservation_projection_version", row["projection_version"])
    row.setdefault("anchor_build_plan_json", anchor_plan.canonical_json)
    row.setdefault("anchor_build_lease_sha256", anchor_plan.sha256)
    row.setdefault("anchor_latest_reservation_variants", 1)
    return row


def _definition_payload(
    *,
    property_id="custom_attribute:customer.plan",
    property_kind="custom_attribute",
    source_adapter="span_attribute",
    primary_source="traces",
    value_adapter="span_attribute_value",
    name="customer.plan",
    attribute_types=("string", "number", "array"),
):
    attribute_types = tuple(sorted(attribute_types))
    payload = {
        "category": "custom_attribute",
        "category_rank": 3,
        "definition_source": "span_attribute_value_catalog",
        "details": {
            "attribute_types": list(attribute_types),
            "attribute_types_exact": True,
            "data_type": "json",
        },
        "display_name": name,
        "name": name,
        "output_type": "json",
        "primary_source": primary_source,
        "property_id": property_id,
        "property_kind": property_kind,
        "role": "dimension",
        "source_rank": 0,
        "source_tokens": ["attribute", "span", "traces"],
        "value_adapter": value_adapter,
        "value_type": "json",
    }
    raw = canonical_json(payload)
    return raw, canonical_json_sha256(raw), source_adapter


def _definition_row(**overrides):
    raw, digest, source_adapter = _definition_payload(
        **{
            key: value
            for key, value in overrides.items()
            if key
            in {
                "property_id",
                "property_kind",
                "source_adapter",
                "primary_source",
                "value_adapter",
                "name",
                "attribute_types",
            }
        }
    )
    row = {
        "activation_state_conflicts": 0,
        "activation_lineage_conflicts": 0,
        "activation_projection_conflicts": 0,
        "activation_anchor_conflicts": 0,
        "binding_conflicts": 0,
        "definition_conflicts": 0,
        "property_rows": 1,
        "property_kind": overrides.get("property_kind", "custom_attribute"),
        "source_adapter": source_adapter,
        "primary_source": overrides.get("primary_source", "traces"),
        "value_adapter": overrides.get("value_adapter", "span_attribute_value"),
        "name": overrides.get("name", "customer.plan"),
        "definition_json": raw,
        "definition_sha256": digest,
        "live_binding_count": 1,
        "project_binding_count": 1,
        "property_definition_variants": 1,
    }
    row.update({key: value for key, value in overrides.items() if key in row})
    return row


def _value_row(value, *, attribute_type=None, **overrides):
    encoded = encode_catalog_scalar(value)
    attribute_type = attribute_type or encoded.kind
    row = {
        "attribute_type": attribute_type,
        "attribute_type_rank": {
            "string": 1,
            "number": 2,
            "boolean": 3,
            "array": 4,
        }[attribute_type],
        "value_fingerprint": encoded.fingerprint,
        "value_json": encoded.value_json,
        "value_search_text_folded": encoded.search_text.casefold(),
        "value_json_variants": 1,
        "value_search_folded_variants": 1,
        "first_seen": "2026-08-02T00:00:00+00:00",
        "last_seen": "2026-08-13T00:00:00+00:00",
    }
    row.update(overrides)
    return row


class FakeExecutor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, query, params, *, timeout_ms, settings):
        self.calls.append(
            {
                "query": query,
                "params": dict(params),
                "timeout_ms": timeout_ms,
                "settings": dict(settings),
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        # Existing fixtures describe the old value-conflict query followed by
        # the page query.  Adapt them to the single sentinel-bearing query so
        # each test continues to make its proof and page payload explicit.
        if "catalog_metadata_only" in query:
            if (
                not isinstance(response, list)
                or len(response) != 1
                or not isinstance(response[0], dict)
                or "value_conflicts" not in response[0]
            ):
                return SimpleNamespace(data=response)
            conflict_count = response[0]["value_conflicts"]
            page_rows = []
            if not conflict_count and self.responses:
                page_rows = self.responses.pop(0)
                if isinstance(page_rows, Exception):
                    raise page_rows
            response = [
                {
                    "catalog_metadata_only": 1,
                    "value_conflicts": conflict_count,
                },
                *[
                    {
                        **row,
                        "catalog_metadata_only": 0,
                        "value_conflicts": conflict_count,
                    }
                    for row in page_rows
                ],
            ]
        return SimpleNamespace(data=response)


def _reader(executor):
    return PropertyCatalogValueReader(
        executor,
        catalog_database="property_catalog_dev_test",
    )


def _read(reader, **overrides):
    values = {
        "scope": _scope(),
        "query": _query(),
        "page_size": 2,
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
    }
    values.update(overrides)
    return reader.read_page(**values)


def test_value_reader_returns_typed_signed_keyset_page_at_active_revision(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    rows = sorted(
        [_value_row("pro"), _value_row(1), _value_row("trial")],
        key=lambda row: (row["attribute_type_rank"], row["value_fingerprint"]),
    )
    executor = FakeExecutor(
        [[_activation_row()], [_definition_row()], [{"value_conflicts": 0}], rows]
    )

    page = _read(_reader(executor))

    assert len(page.values) == 2
    assert {(item.attribute_type, type(item.value)) for item in page.values} <= {
        ("string", str),
        ("number", int),
    }
    assert page.has_more is True
    assert page.next_cursor
    assert page.catalog_epoch == 3
    assert page.catalog_revision == 17
    assert page.activation_fingerprint == ACTIVATION_SHA
    assert page.window_start == WINDOW_START
    assert page.window_end == WINDOW_END
    assert page.attribute_types == ("array", "number", "string")
    assert page.query_count == 3
    value_call = executor.calls[-1]
    assert "catalog_revision <= %(catalog_revision)s" in value_call["query"]
    assert "catalog_revision, build_token" in executor.calls[0]["query"]
    assert "rows.build_token = lineage.build_token" in executor.calls[1]["query"]
    assert "value_rows.build_token = lineage.build_token" in value_call["query"]
    assert (
        "catalog_revision >= %(catalog_lineage_anchor_revision)s" in value_call["query"]
    )
    assert value_call["params"]["catalog_lineage_anchor_revision"] == 1
    assert (
        "`property_catalog_dev_test`.`span_attribute_value_catalog`"
        in value_call["query"]
    )
    assert " OFFSET " not in value_call["query"].upper()
    assert value_call["params"]["catalog_revision"] == 17
    assert value_call["params"]["catalog_project_uuid_ids"] == (uuid.UUID(PROJECT_ID),)
    project_prewhere = "OR value_rows.project_id IN %(catalog_project_uuid_ids)s"
    assert project_prewhere in value_call["query"]
    assert value_call["query"].index(project_prewhere) < value_call["query"].index(
        "WHERE (\n        %(catalog_source_kind)s = 'system_attribute'"
    )
    assert "toString(project_id)" not in value_call["query"]
    assert value_call["params"]["catalog_result_limit"] == 4
    assert value_call["settings"]["max_result_rows"] == 4


def test_value_reader_prefers_new_epoch_when_activation_sequences_restart(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [
                _activation_row(
                    catalog_epoch=4,
                    catalog_revision=1,
                    lifecycle_mode="initial_backfill",
                    lineage_anchor_revision=1,
                    activation_sequence=1,
                ),
                _activation_row(catalog_epoch=3, activation_sequence=1),
            ],
            [_definition_row()],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    page = _read(_reader(executor))

    assert page.catalog_epoch == 4
    assert "active_candidates.catalog_epoch DESC" in executor.calls[0]["query"]
    assert "WHERE latest_active_states > 0" in executor.calls[0]["query"]
    assert "WHERE latest_state_variants = 1" not in executor.calls[0]["query"]


def test_value_reader_rejects_duplicate_sequence_inside_one_epoch(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row(), _activation_row(catalog_revision=16)]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "activation_sequence_conflict"
    assert len(executor.calls) == 1


def test_value_window_filters_each_observation_before_cross_revision_dedupe(settings):
    """Jan + Mar observations must not bridge into a false February match."""

    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    _read(_reader(executor), page_size=10)

    sql = executor.calls[-1]["query"]
    source_start = sql.index("source_values AS")
    grouped_start = sql.index("grouped_values AS")
    per_row_overlap = sql.index(
        "AND first_seen < fromUnixTimestamp64Micro(%(catalog_window_end_us)s",
        source_start,
    )
    assert source_start < per_row_overlap < grouped_start


def test_value_cursor_continuation_pins_activation_window_and_last_typed_key(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    first_rows = sorted(
        [_value_row("pro"), _value_row("trial")],
        key=lambda row: (row["attribute_type_rank"], row["value_fingerprint"]),
    )
    first_executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            first_rows,
        ]
    )
    first = _read(_reader(first_executor), page_size=1)
    expected_after = first.values[-1].value_fingerprint

    second_executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [first_rows[-1]],
        ]
    )
    second = _reader(second_executor).read_page(
        scope=_scope(),
        query=_query(),
        page_size=1,
        cursor_token=first.next_cursor,
    )

    assert second.window_start == WINDOW_START
    assert second.window_end == WINDOW_END
    assert second_executor.calls[0]["params"]["catalog_exact_activation"] == 1
    assert second_executor.calls[0]["params"]["catalog_epoch"] == 3
    assert second_executor.calls[0]["params"]["catalog_revision"] == 17
    assert (
        second_executor.calls[-1]["params"]["catalog_after_value_fingerprint"]
        == expected_after
    )
    second_sql = second_executor.calls[-1]["query"]
    source_values_start = second_sql.index("source_values AS")
    grouped_values_start = second_sql.index("grouped_values AS")
    source_values_sql = second_sql[source_values_start:grouped_values_start]
    assert "toInt8(value_rows.attribute_type)" in source_values_sql
    assert "%(catalog_after_attribute_type_rank)s" in source_values_sql
    assert "%(catalog_after_value_fingerprint)s" in source_values_sql
    assert (
        second_sql.index("%(catalog_after_value_fingerprint)s", source_values_start)
        < grouped_values_start
    )


def test_value_reader_pins_first_page_to_activation_time_coverage(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    page = _read(
        _reader(executor),
        window_start=WINDOW_START - timedelta(days=1),
        window_end=WINDOW_END + timedelta(days=1),
        page_size=10,
    )

    assert page.window_start == WINDOW_START
    assert page.window_end == WINDOW_END
    value_params = executor.calls[-1]["params"]
    assert value_params["catalog_window_start_us"] == WINDOW_START_US
    assert value_params["catalog_window_end_us"] == WINDOW_END_US


def test_value_reader_retains_anchor_window_across_incremental_lineage(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    anchor_start = WINDOW_START - timedelta(days=30)
    anchor_start_us = WINDOW_START_US - (30 * 24 * 60 * 60 * 1_000_000)
    property_name = "conversation.transcript.45.message.role"
    property_id = f"custom_attribute:{property_name}"
    query = _query(property_id=property_id)
    rows = sorted(
        [
            _value_row(
                "assistant",
                first_seen=(anchor_start + timedelta(days=1)).isoformat(),
                last_seen=(anchor_start + timedelta(days=2)).isoformat(),
            ),
            _value_row(
                "user",
                first_seen=(anchor_start + timedelta(days=3)).isoformat(),
                last_seen=(anchor_start + timedelta(days=4)).isoformat(),
            ),
        ],
        key=lambda row: (row["attribute_type_rank"], row["value_fingerprint"]),
    )
    first_executor = FakeExecutor(
        [
            [
                _activation_row(
                    anchor_span_since_us=anchor_start_us,
                    anchor_span_until_us=WINDOW_START_US,
                )
            ],
            [
                _definition_row(
                    property_id=property_id,
                    name=property_name,
                    attribute_types=("string",),
                )
            ],
            [{"value_conflicts": 0}],
            rows,
        ]
    )

    first = _read(
        _reader(first_executor),
        query=query,
        window_start=anchor_start - timedelta(days=1),
        window_end=WINDOW_END + timedelta(days=1),
        page_size=1,
    )

    assert first.window_start == anchor_start
    assert first.window_end == WINDOW_END
    assert first.has_more is True
    assert first.next_cursor
    first_params = first_executor.calls[-1]["params"]
    assert first_params["catalog_window_start_us"] == anchor_start_us
    assert first_params["catalog_window_end_us"] == WINDOW_END_US

    second_executor = FakeExecutor(
        [
            [
                _activation_row(
                    anchor_span_since_us=anchor_start_us,
                    anchor_span_until_us=WINDOW_START_US,
                )
            ],
            [
                _definition_row(
                    property_id=property_id,
                    name=property_name,
                    attribute_types=("string",),
                )
            ],
            [{"value_conflicts": 0}],
            [rows[-1]],
        ]
    )
    second = _reader(second_executor).read_page(
        scope=_scope(),
        query=query,
        page_size=1,
        cursor_token=first.next_cursor,
    )

    assert {first.values[0].value, second.values[0].value} == {"assistant", "user"}
    assert second.window_start == anchor_start
    assert second.window_end == WINDOW_END
    second_params = second_executor.calls[-1]["params"]
    assert second_params["catalog_window_start_us"] == anchor_start_us
    assert second_params["catalog_window_end_us"] == WINDOW_END_US
    assert (
        second_params["catalog_after_value_fingerprint"]
        == first.values[0].value_fingerprint
    )


def test_value_reader_rejects_cursor_window_outside_activation_coverage(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    token = encode_property_catalog_value_cursor(
        scope=_scope(),
        query=_query(),
        page_size=2,
        catalog_epoch=3,
        catalog_revision=17,
        activation_fingerprint=ACTIVATION_SHA,
        window_start=WINDOW_START - timedelta(microseconds=1),
        window_end=WINDOW_END,
        order=(1, "0" * 64),
    )
    executor = FakeExecutor([[_activation_row()]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _reader(executor).read_page(
            scope=_scope(),
            query=_query(),
            page_size=2,
            cursor_token=token,
        )

    assert exc_info.value.reason == "activation_scope_incomplete"
    assert len(executor.calls) == 1


def test_value_cursor_mismatch_fails_before_clickhouse(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    rows = sorted(
        [_value_row("pro"), _value_row("trial")],
        key=lambda row: row["value_fingerprint"],
    )
    issuer = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            rows,
        ]
    )
    token = _read(_reader(issuer), page_size=1).next_cursor
    continuation = FakeExecutor([])

    with pytest.raises(PropertyCatalogValueCursorError) as exc_info:
        _reader(continuation).read_page(
            scope=_scope(project_ids=()),
            query=_query(),
            page_size=1,
            cursor_token=token,
        )

    assert exc_info.value.code == "cursor_mismatch"
    assert continuation.calls == []


def test_typed_filter_is_bound_and_does_not_collapse_heterogeneous_values(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    number = _value_row(1, attribute_type="number")
    array_member = _value_row(1, attribute_type="array")
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("number", "array"))],
            [{"value_conflicts": 0}],
            [number],
        ]
    )

    page = _read(
        _reader(executor),
        query=_query(attribute_type="number"),
        page_size=10,
    )

    assert [(item.attribute_type, item.value) for item in page.values] == [
        ("number", 1)
    ]
    assert number["value_fingerprint"] == array_member["value_fingerprint"]
    assert executor.calls[2]["params"]["catalog_attribute_types"] == ("number",)


def test_requested_type_absent_from_exact_definition_returns_no_value_query(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [[_activation_row()], [_definition_row(attribute_types=("string",))]]
    )

    page = _read(
        _reader(executor),
        query=_query(attribute_type="number"),
        page_size=10,
    )

    assert page.values == ()
    assert page.has_more is False
    assert page.attribute_types == ("string",)
    assert len(executor.calls) == 2


def test_unicode_search_rechecks_with_python_casefold(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [_value_row("Straße")],
        ]
    )

    page = _read(
        _reader(executor),
        query=_query(search="STRASSE"),
        page_size=10,
    )

    assert [item.value for item in page.values] == ["Straße"]
    assert executor.calls[-1]["params"]["catalog_search"] == "strasse"
    assert executor.calls[-1]["params"]["catalog_search_pattern"] == "%strasse%"
    sql = executor.calls[-1]["query"]
    source_values_start = sql.index("source_values AS")
    grouped_values_start = sql.index("grouped_values AS")
    source_values_sql = sql[source_values_start:grouped_values_start]
    assert "raw_value_search_text_folded LIKE" not in source_values_sql
    assert "%(catalog_search_pattern)s" not in source_values_sql
    assert "value_search_text_folded LIKE %(catalog_search_pattern)s" in sql
    assert sql.index(
        "value_search_text_folded LIKE %(catalog_search_pattern)s"
    ) > sql.index("checked_value_rows AS")
    assert "position(value_search_text_folded" not in sql
    assert "value_search_text AS" not in sql
    assert "length(value_search_text) != lengthUTF8" not in sql


def test_search_does_not_mask_conflicting_raw_variants(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 1}],
        ]
    )

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(
            _reader(executor),
            query=_query(search="matching variant"),
            page_size=10,
        )

    assert exc_info.value.reason == "value_conflict"
    assert len(executor.calls) == 3


def test_folded_search_payload_mismatch_fails_closed(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [_value_row("Straße", value_search_text_folded="unrelated")],
        ]
    )

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor), page_size=10)

    assert exc_info.value.reason == "value_payload_mismatch"


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"activation_state_conflicts": 1}, "definition_conflict"),
        ({"activation_lineage_conflicts": 1}, "definition_conflict"),
        ({"activation_projection_conflicts": 1}, "definition_conflict"),
        ({"activation_anchor_conflicts": 1}, "definition_conflict"),
        ({"binding_conflicts": 1}, "definition_conflict"),
        ({"definition_conflicts": 1}, "definition_conflict"),
        ({"property_rows": 0}, "definition_missing"),
        ({"property_definition_variants": 2}, "definition_conflict"),
        ({"project_binding_count": 0}, "definition_visibility_invalid"),
    ],
)
def test_value_reader_fails_closed_on_definition_proof_errors(settings, row, reason):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row()], [_definition_row(**row)]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == reason
    assert len(executor.calls) == 2


@pytest.mark.parametrize(
    "activation",
    [
        {"lifecycle_mode": "incremental", "lineage_anchor_revision": 17},
        {"lifecycle_mode": "full_repair", "lineage_anchor_revision": 1},
        {"lifecycle_mode": "incremental", "lineage_anchor_revision": 18},
    ],
)
def test_value_reader_rejects_invalid_lifecycle_anchor(settings, activation):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row(**activation)]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "activation_lineage_invalid"
    assert len(executor.calls) == 1


def test_value_reader_rejects_project_missing_from_immutable_build_scope(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [[_activation_row(covered_project_ids=(OTHER_PROJECT_ID,))]]
    )

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "activation_scope_incomplete"
    assert len(executor.calls) == 1


def test_value_reader_rejects_partial_workspace_activation_coverage(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row()]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(
            _reader(executor),
            scope=_scope(
                project_ids=(PROJECT_ID, OTHER_PROJECT_ID),
                workspace_scope=True,
            ),
        )

    assert exc_info.value.reason == "activation_scope_incomplete"
    assert len(executor.calls) == 1


def test_value_reader_accepts_workspace_scope_with_deleted_project_tombstones(
    settings,
):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [
                _activation_row(
                    covered_project_ids=(PROJECT_ID, OTHER_PROJECT_ID),
                )
            ],
            [_definition_row()],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    _read(
        _reader(executor),
        scope=_scope(project_ids=(PROJECT_ID,), workspace_scope=True),
    )

    assert executor.calls[1]["params"]["catalog_project_ids"] == (PROJECT_ID,)


def test_value_reader_workspace_scope_uses_authorized_project_ids(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row()],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    _read(
        _reader(executor),
        scope=_scope(project_ids=(PROJECT_ID,), workspace_scope=True),
    )

    params = executor.calls[1]["params"]
    assert params["catalog_project_ids"] == (PROJECT_ID,)
    assert params["catalog_include_all_projects"] == 0


def test_value_reader_rejects_unproven_empty_project_scope(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row()]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor), scope=_scope(project_ids=()))

    assert exc_info.value.reason == "activation_scope_incomplete"
    assert len(executor.calls) == 1


def test_value_reader_rejects_foreign_workspace_build_plan(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [[_activation_row(build_plan_workspace_id=OTHER_WORKSPACE_ID)]]
    )

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "activation_scope_invalid"
    assert len(executor.calls) == 1


def test_value_reader_rejects_conflicting_reservation(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row(latest_reservation_variants=2)]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "activation_scope_conflict"
    assert len(executor.calls) == 1


def test_value_reader_rejects_build_plan_lease_hash_mismatch(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([[_activation_row(build_lease_sha256="c" * 64)]])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "activation_scope_invalid"
    assert len(executor.calls) == 1


def test_value_definition_proof_rejects_full_binding_and_property_tuple_drift(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row()],
            [{"value_conflicts": 0}],
            [],
        ]
    )

    _read(_reader(executor), page_size=1)

    proof_query = executor.calls[1]["query"]
    assert "AS binding_variants" in proof_query
    assert "AS binding_is_conflicted" in proof_query
    assert "countIf(binding.binding_is_conflicted) AS binding_conflicts" in proof_query
    assert "uniqExact(tuple(" in proof_query
    assert "uniqExactIf(tuple(" in proof_query
    assert "binding.definition_sha256" in proof_query


def test_non_catalog_native_adapter_uses_only_typed_not_ready_signal(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [[_activation_row()], [_definition_row(value_adapter="system_traces")]]
    )

    with pytest.raises(PropertyCatalogValueNotReady) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "native_value_adapter"
    assert len(executor.calls) == 2


def test_value_reader_fails_closed_on_value_conflict_before_page_query(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row()],
            [{"value_conflicts": 1}],
        ]
    )

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "value_conflict"
    assert len(executor.calls) == 3


def test_invalid_second_value_discards_entire_page(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    invalid = _value_row("trial", value_fingerprint="c" * 64)
    rows = sorted(
        [_value_row("pro"), invalid],
        key=lambda row: row["value_fingerprint"],
    )
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            rows,
        ]
    )

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor), page_size=10)

    assert exc_info.value.reason == "value_payload_mismatch"


def test_system_hot_property_requires_exact_active_manifest_binding(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    raw, digest, _ = _definition_payload(
        property_id="system_attribute:traces:model",
        property_kind="system_attribute",
        source_adapter="system_manifest",
        primary_source="traces",
        name="model",
        attribute_types=("string",),
    )
    definition = {
        **_definition_row(),
        "property_kind": "system_attribute",
        "source_adapter": "system_manifest",
        "primary_source": "traces",
        "name": "model",
        "definition_json": raw,
        "definition_sha256": digest,
        "project_binding_count": 0,
    }
    executor = FakeExecutor(
        [
            [_activation_row()],
            [definition],
            [{"value_conflicts": 0}],
            [_value_row("gpt-5")],
        ]
    )

    page = _read(
        _reader(executor),
        query=_query(
            property_id="system_attribute:traces:model",
            source="traces",
        ),
        page_size=10,
    )

    assert [item.value for item in page.values] == ["gpt-5"]
    assert executor.calls[-1]["params"]["catalog_source_kind"] == "system_attribute"
    definition_sql = executor.calls[1]["query"]
    assert "AND (\n        rows.visibility_scope = 'always'" in definition_sql
    assert definition_sql.index("rows.visibility_scope = 'always'") < (
        definition_sql.index("), binding_maxima AS")
    )


def test_query_failure_is_sanitized_and_never_publishes_partial_values(settings):
    settings.SECRET_KEY = "property-value-reader-secret"
    executor = FakeExecutor([RuntimeError("driver details")])

    with pytest.raises(PropertyCatalogValueUnavailable) as exc_info:
        _read(_reader(executor))

    assert exc_info.value.reason == "query_failed"
    assert "driver details" not in str(exc_info.value)
