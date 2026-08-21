from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.codec import (
    canonical_json,
    canonical_json_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.models import (
    PropertyCategory,
    PropertyDefinition,
    PropertyKind,
    PropertyRole,
    canonicalize_definition,
)
from tracer.services.clickhouse.v2.property_catalog.reader import (
    _ACTIVATION_SQL,
    PropertyCatalogReader,
    PropertyCatalogUnavailable,
    _definition_ctes,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
ACTIVATION_SHA = "a" * 64
MANIFEST_SHA = "b" * 64
BUILD_TOKEN = "44444444-4444-4444-8444-444444444444"


def test_clickhouse25_aggregate_inputs_are_raw_qualified() -> None:
    assert "FROM versioned AS versioned_rows" in _ACTIVATION_SQL
    assert (
        "argMax(versioned_rows.projection_version, versioned_rows._version)"
        in _ACTIVATION_SQL
    )
    assert "versioned_rows.status = 'active'" in _ACTIVATION_SQL
    assert "argMax(projection_version, _version)" not in _ACTIVATION_SQL

    definitions_sql = _definition_ctes("th7247_catalog_dev_test")
    assert "FROM lineage_versioned AS versioned_rows" in definitions_sql
    assert "FROM latest_binding_rows AS binding" in definitions_sql
    assert "any(binding.property_id) AS property_id" in definitions_sql
    assert "binding.property_id," in definitions_sql
    assert "any(property_id) AS property_id" not in definitions_sql


def _scope(*, project_ids=(PROJECT_ID,)):
    return {
        "principal_id": "user-1",
        "auth_type": "Token",
        "auth_id": "token-1",
        "organization_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "project_ids": project_ids,
        "agent_definition_id": "",
        "dataset_id": "",
    }


QUERY = {
    "category": "custom_attribute",
    "source": "traces",
    "property_kind": "custom_attribute",
    "per_eval_config": False,
    "search": "customer",
}


def _activation_row(**overrides):
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
    return row


def _conflict_row(**overrides):
    row = {
        "activation_state_conflicts": 0,
        "activation_lineage_conflicts": 0,
        "activation_projection_conflicts": 0,
        "activation_anchor_conflicts": 0,
        "binding_conflicts": 0,
        "definition_conflicts": 0,
        "catalog_count_all": 2,
        "catalog_count_system_metric": 0,
        "catalog_count_eval_metric": 0,
        "catalog_count_annotation_metric": 0,
        "catalog_count_custom_attribute": 2,
        "catalog_count_custom_column": 0,
    }
    row.update(overrides)
    return row


def _property_row(name, rank):
    definition = canonicalize_definition(
        PropertyDefinition(
            property_kind=PropertyKind.CUSTOM_ATTRIBUTE,
            source_key=name,
            category=PropertyCategory.CUSTOM_ATTRIBUTE,
            category_rank=3,
            source_rank=rank,
            definition_source="traces",
            primary_source="traces",
            source_tokens=("traces",),
            value_adapter="span_attribute",
            name=name,
            display_name=name,
            value_type="text",
            output_type="",
            role=PropertyRole.DIMENSION,
        )
    )
    return {
        "property_id": definition.property_id,
        "property_kind": definition.property_kind.value,
        "category": definition.category.value,
        "category_rank": definition.category_rank,
        "source_rank": definition.source_rank,
        "definition_source": definition.definition_source,
        "primary_source": definition.primary_source,
        "primary_source_folded": definition.primary_source_folded,
        "source_tokens": list(definition.source_tokens),
        "value_adapter": definition.value_adapter,
        "name": definition.name,
        "display_name": definition.display_name,
        "sort_name_folded": definition.sort_name_folded,
        "search_text_folded": definition.search_text_folded,
        "role": definition.role.value,
        "definition_json": definition.definition_json,
        "definition_sha256": definition.definition_sha256,
        "payload_binding_id": canonical_json_sha256(
            canonical_json({"binding": name})
        ),
        "payload_catalog_revision": 17,
        "payload_build_token": BUILD_TOKEN,
        "payload_source_version": 1,
    }


class FakeExecutor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.page_rows = []

    def execute(self, query, params, *, timeout_ms, settings):
        self.calls.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        if "catalog_payload_keys" in params:
            requested = set(params["catalog_payload_keys"])
            response = []
            for row in self.page_rows:
                key = (
                    row["property_id"],
                    row["payload_binding_id"],
                    row["payload_catalog_revision"],
                    row["payload_build_token"],
                    row["payload_source_version"],
                )
                if key not in requested:
                    continue
                response.append(
                    {
                        "property_id": row["property_id"],
                        "payload_binding_id": row["payload_binding_id"],
                        "payload_catalog_revision": row[
                            "payload_catalog_revision"
                        ],
                        "payload_build_token": row["payload_build_token"],
                        "payload_source_version": row["payload_source_version"],
                        "payload_definition_json": row["definition_json"],
                        "payload_definition_sha256": row["definition_sha256"],
                        "payload_variants": 1,
                        "payload_deleted": 0,
                    }
                )
            return SimpleNamespace(data=response)

        response = self.responses.pop(0)
        if (
            "catalog_metadata_only" in query
            and response
            and "catalog_metadata_only" not in response[0]
            and "activation_state_conflicts" in response[0]
        ):
            metadata = {**response[0], "catalog_metadata_only": 1}
            properties = self.responses.pop(0) if self.responses else []
            self.page_rows = [
                {
                    **property_row,
                    "payload_catalog_revision": params["catalog_revision"],
                }
                for property_row in properties
            ]
            response = [
                metadata,
                *(
                    {**property_row, "catalog_metadata_only": 0}
                    for property_row in self.page_rows
                ),
            ]
        return SimpleNamespace(data=response)


def test_catalog_reader_returns_signed_keyset_page(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [_property_row("customer.plan", 1), _property_row("customer.tier", 1)],
        ]
    )
    reader = PropertyCatalogReader(executor, catalog_database="th7247_catalog_dev_test")

    page = reader.read_page(scope=_scope(), query=QUERY, page_size=1, cursor_token=None)

    assert [item["property_id"] for item in page.metrics] == [
        "custom_attribute:customer.plan"
    ]
    assert page.has_more is True
    assert page.next_cursor
    assert page.total is None
    assert page.total_is_exact is False
    assert page.category_counts == {
        "all": 2,
        "system_metric": 0,
        "eval_metric": 0,
        "annotation_metric": 0,
        "custom_attribute": 2,
        "custom_column": 0,
    }
    assert page.category_counts_exact is True
    assert len(executor.calls) == 3
    assert executor.calls[0]["params"]["catalog_exact_activation"] == 0
    assert executor.calls[1]["settings"]["max_result_rows"] == 3
    assert executor.calls[1]["params"]["catalog_search"] == "customer"
    assert executor.calls[1]["params"]["catalog_search_pattern"] == "%customer%"
    assert "search_text_folded LIKE %(catalog_search_pattern)s" in executor.calls[1][
        "query"
    ]
    assert "position(search_text_folded" not in executor.calls[1]["query"]
    assert " OFFSET " not in executor.calls[1]["query"].upper()
    assert "catalog_revision, build_token" in executor.calls[0]["query"]
    assert "rows.build_token = lineage.build_token" in executor.calls[1]["query"]
    assert (
        "catalog_revision >= %(catalog_lineage_anchor_revision)s"
        in executor.calls[1]["query"]
    )
    assert executor.calls[1]["params"]["catalog_lineage_anchor_revision"] == 1
    assert "AS binding_variants" in executor.calls[1]["query"]
    assert "AS catalog_count_custom_attribute" in executor.calls[1]["query"]
    assert "AS binding_is_conflicted" in executor.calls[1]["query"]
    assert (
        "sum(binding_conflicts) OVER () AS catalog_binding_conflicts"
        in executor.calls[1]["query"]
    )
    assert "uniqExact(tuple(" in executor.calls[1]["query"]
    assert executor.calls[1]["query"].count("FROM resolved_bindings") == 1
    assert executor.calls[1]["query"].count("FROM resolved_properties") == 1
    assert (
        "`th7247_catalog_dev_test`.`property_definition_catalog`"
        in executor.calls[1]["query"]
    )
    assert "any(binding.definition_json)" not in executor.calls[1]["query"]
    assert "property_id IN %(catalog_payload_property_ids)s" in executor.calls[2][
        "query"
    ]
    assert executor.calls[2]["settings"]["max_result_rows"] == 1


def test_catalog_reader_rejects_inconsistent_category_counts(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row(catalog_count_all=3)],
        ]
    )

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=20)

    assert exc_info.value.reason == "category_count_mismatch"
    assert len(executor.calls) == 2


def test_span_catalog_includes_trace_defined_attributes(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [],
        ]
    )

    PropertyCatalogReader(
        executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(scope=_scope(), query={**QUERY, "source": "spans"}, page_size=20)

    call = executor.calls[1]
    assert "%(catalog_source)s = 'spans'" in call["query"]
    assert "primary_source = 'traces'" in call["query"]
    assert call["params"]["catalog_source"] == "spans"


def test_voice_call_catalog_bridges_shared_and_trace_derived_families(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [],
        ]
    )

    PropertyCatalogReader(
        executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(scope=_scope(), query={**QUERY, "source": "voice_calls"}, page_size=20)

    call = executor.calls[1]
    query = call["query"]
    assert "%(catalog_source)s = 'voice_calls'" in query
    assert "primary_source IN ('all', 'both')" in query
    assert "'annotation_metric'" in query
    assert "'custom_attribute'" in query
    assert "primary_source = 'traces'" in query
    assert call["params"]["catalog_source"] == "voice_calls"


def test_catalog_reader_scopes_counts_and_page_membership_by_role(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [_property_row("customer.plan", 1)],
        ]
    )

    PropertyCatalogReader(
        executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(
        scope=_scope(),
        query={**QUERY, "role": "dimension"},
        page_size=20,
    )

    call = executor.calls[1]
    assert call["params"]["catalog_role"] == "dimension"
    assert "role = %(catalog_role)s" in call["query"]


def test_catalog_reader_rejects_pages_above_cursor_contract(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor([])

    with pytest.raises(ValueError, match="between 1 and 50"):
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=51)

    assert executor.calls == []


def test_catalog_reader_prefers_new_epoch_when_activation_sequences_restart(settings):
    settings.SECRET_KEY = "property-reader-secret"
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
            [_conflict_row()],
            [_property_row("customer.plan", 1)],
        ]
    )

    page = PropertyCatalogReader(
        executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(scope=_scope(), query=QUERY, page_size=1)

    assert page.catalog_epoch == 4
    assert (
        "ORDER BY catalog_epoch DESC, catalog_revision DESC, activation_sequence DESC"
        in executor.calls[0]["query"]
    )
    assert "WHERE latest_active_states > 0" in executor.calls[0]["query"]
    assert "WHERE latest_state_variants = 1" not in executor.calls[0]["query"]


def test_catalog_reader_rejects_duplicate_sequence_inside_one_epoch(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor([[_activation_row(), _activation_row(catalog_revision=16)]])

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=1)

    assert exc_info.value.reason == "activation_sequence_conflict"
    assert len(executor.calls) == 1


def test_catalog_cursor_continuation_pins_activation_and_last_tuple(settings):
    settings.SECRET_KEY = "property-reader-secret"
    first_executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [_property_row("customer.plan", 1), _property_row("customer.tier", 2)],
        ]
    )
    first = PropertyCatalogReader(
        first_executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(scope=_scope(), query=QUERY, page_size=1)

    second_executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [_property_row("customer.tier", 2)],
        ]
    )
    second = PropertyCatalogReader(
        second_executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(
        scope=_scope(),
        query=QUERY,
        page_size=1,
        cursor_token=first.next_cursor,
    )

    assert [item["property_id"] for item in second.metrics] == [
        "custom_attribute:customer.tier"
    ]
    assert second.has_more is False
    activation_params = second_executor.calls[0]["params"]
    assert activation_params["catalog_exact_activation"] == 1
    assert activation_params["catalog_epoch"] == 3
    assert activation_params["catalog_revision"] == 17
    page_params = second_executor.calls[1]["params"]
    assert page_params["catalog_after_property_id"] == (
        "custom_attribute:customer.plan"
    )
    assert page_params["catalog_after_sort_name"] == "customer.plan"


def test_catalog_cursor_mismatch_fails_before_clickhouse(settings):
    settings.SECRET_KEY = "property-reader-secret"
    issuer = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [_property_row("customer.plan", 1), _property_row("customer.tier", 2)],
        ]
    )
    token = (
        PropertyCatalogReader(issuer, catalog_database="th7247_catalog_dev_test")
        .read_page(scope=_scope(), query=QUERY, page_size=1)
        .next_cursor
    )
    continuation = FakeExecutor([])

    with pytest.raises(PropertyCatalogCursorError) as exc_info:
        PropertyCatalogReader(
            continuation, catalog_database="th7247_catalog_dev_test"
        ).read_page(
            scope=_scope(project_ids=()),
            query=QUERY,
            page_size=1,
            cursor_token=token,
        )

    assert exc_info.value.code == "cursor_mismatch"
    assert continuation.calls == []


@pytest.mark.parametrize(
    "conflicts",
    [
        {"binding_conflicts": 1, "definition_conflicts": 0},
        {"binding_conflicts": 0, "definition_conflicts": 1},
        {"activation_state_conflicts": 1},
        {"activation_lineage_conflicts": 1},
        {"activation_projection_conflicts": 1},
        {"activation_anchor_conflicts": 1},
    ],
)
def test_catalog_reader_rejects_any_qualified_state_conflict(settings, conflicts):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor([[_activation_row()], [_conflict_row(**conflicts)]])

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=50)

    assert exc_info.value.reason == "definition_conflict"
    assert len(executor.calls) == 2


def test_catalog_reader_rejects_inactive_or_conflicting_activation(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [[_activation_row(status="disabled", latest_state_variants=2)]]
    )

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=50)

    assert exc_info.value.reason == "activation_conflict"


def test_catalog_reader_rejects_multiple_active_builds_for_one_revision(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor([[_activation_row(active_builds=2)]])

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor,
            catalog_database="th7247_catalog_dev_test",
        ).read_page(scope=_scope(), query=QUERY, page_size=50)

    assert exc_info.value.reason == "activation_conflict"


@pytest.mark.parametrize(
    ("activation", "reason"),
    [
        (
            {"lifecycle_mode": "incremental", "lineage_anchor_revision": 17},
            "activation_lineage_invalid",
        ),
        (
            {"lifecycle_mode": "full_repair", "lineage_anchor_revision": 1},
            "activation_lineage_invalid",
        ),
        (
            {"lifecycle_mode": "incremental", "lineage_anchor_revision": 18},
            "activation_lineage_invalid",
        ),
        (
            {"lifecycle_mode": "incremental", "lineage_anchor_revision": 0},
            "activation_lineage_invalid",
        ),
        (
            {"lifecycle_mode": "incremental", "lineage_anchor_revision": -2032},
            "invalid_lineage_anchor_revision",
        ),
    ],
)
def test_catalog_reader_rejects_invalid_lifecycle_anchor(settings, activation, reason):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor([[_activation_row(**activation)]])

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=50)

    assert exc_info.value.reason == reason
    assert len(executor.calls) == 1


def test_catalog_reader_rejects_definition_identity_drift(settings):
    settings.SECRET_KEY = "property-reader-secret"
    row = _property_row("customer.plan", 1)
    payload = json.loads(row["definition_json"])
    payload["property_id"] = "custom_attribute:other"
    row["definition_json"] = canonical_json(payload)
    row["definition_sha256"] = canonical_json_sha256(row["definition_json"])
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [row],
        ]
    )

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=50)

    assert exc_info.value.reason == "definition_identity_mismatch"


def test_catalog_reader_rejects_folded_order_drift(settings):
    settings.SECRET_KEY = "property-reader-secret"
    row = _property_row("Straße", 1)
    row["sort_name_folded"] = "straße"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [row],
        ]
    )

    with pytest.raises(PropertyCatalogUnavailable) as exc_info:
        PropertyCatalogReader(
            executor, catalog_database="th7247_catalog_dev_test"
        ).read_page(scope=_scope(), query=QUERY, page_size=50)

    assert exc_info.value.reason == "definition_fold_mismatch"


def test_catalog_reader_visibility_params_are_authorization_owned(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [],
        ]
    )
    PropertyCatalogReader(
        executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(scope=_scope(project_ids=()), query=QUERY, page_size=50)

    params = executor.calls[1]["params"]
    assert params["catalog_include_workspace_default"] == 1
    assert params["catalog_include_all_projects"] == 1
    assert params["catalog_project_ids"] == ()


def test_catalog_reader_hides_workspace_defaults_for_project_scope(settings):
    settings.SECRET_KEY = "property-reader-secret"
    executor = FakeExecutor(
        [
            [_activation_row()],
            [_conflict_row()],
            [],
        ]
    )

    PropertyCatalogReader(
        executor, catalog_database="th7247_catalog_dev_test"
    ).read_page(scope=_scope(project_ids=(PROJECT_ID,)), query=QUERY, page_size=50)

    params = executor.calls[1]["params"]
    assert params["catalog_include_workspace_default"] == 0
    assert params["catalog_include_all_projects"] == 0
    assert params["catalog_project_ids"] == (PROJECT_ID,)
    sql = executor.calls[1]["query"]
    assert "AND (\n        rows.visibility_scope = 'always'" in sql
    assert sql.index("rows.visibility_scope = 'always'") < sql.index(
        "), binding_maxima AS"
    )
