from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.property_catalog.models import (
    EnvelopeCounts,
    EnvelopeOutcome,
    PropertyCatalogEnvelope,
    PropertyCategory,
    PropertyDefinition,
    PropertyKind,
    PropertyRole,
    SourceAdapter,
    VisibilityBinding,
    VisibilityScope,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    DefinitionConflictError,
    VersionResolutionStatus,
    VisibilityContext,
    project_definition,
    resolve_binding_history,
    resolve_source_update,
    resolve_visible_definitions,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
STREAM = "44444444-4444-4444-8444-444444444444"
BUILD_TOKEN = "66666666-6666-4666-8666-666666666666"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _definition(
    *,
    name: str = "Customer.Plan",
    details: dict[str, object] | None = None,
) -> PropertyDefinition:
    return PropertyDefinition(
        property_kind=PropertyKind.CUSTOM_ATTRIBUTE,
        source_key="customer.plan",
        category=PropertyCategory.CUSTOM_ATTRIBUTE,
        category_rank=20,
        source_rank=4,
        definition_source="span_attribute",
        primary_source="traces",
        source_tokens=("span", "customer"),
        value_adapter="span_attribute_value",
        name=name,
        display_name="Customer plan",
        value_type="string",
        output_type="string",
        role=PropertyRole.DIMENSION,
        details=details or {"allowed_aggregations": ["count", "count_distinct"]},
    )


def _row(
    *,
    visibility: VisibilityBinding | None = None,
    definition: PropertyDefinition | None = None,
    source_adapter: SourceAdapter = SourceAdapter.SPAN_ATTRIBUTE,
    source_version: int = 1,
    revision: int = 1,
    sequence: int = 1,
    is_deleted: bool = False,
    deleted_at: datetime | None = None,
):
    return project_definition(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        catalog_revision=revision,
        build_token=BUILD_TOKEN,
        projection_version=1,
        visibility=visibility or VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
        definition=definition or _definition(),
        source_adapter=source_adapter,
        source_entity_id="customer.plan",
        source_version=source_version,
        source_fingerprint=_sha(f"source-{source_version}"),
        producer_stream_id=STREAM,
        producer_sequence=sequence,
        emitted_at=NOW + timedelta(seconds=sequence),
        first_seen=NOW,
        last_seen=NOW + timedelta(seconds=sequence),
        is_deleted=is_deleted,
        deleted_at=deleted_at,
    )


def test_projection_pins_definition_binding_and_state_golden_hashes() -> None:
    row = _row()

    assert row.property_id == "custom_attribute:customer.plan"
    assert row.definition.sort_name_folded == "customer.plan"
    assert row.definition.primary_source_folded == "traces"
    assert row.definition.search_text_folded == (
        "customer.plan customer plan traces span_attribute customer span"
    )
    assert row.definition.definition_sha256 == (
        "e28225e87d104b1d3eaf0fa3fcec6c665dd124e214f42261e650af939f935e21"
    )
    assert row.binding_id == (
        "02968171981845b617b75866346ee9475d13c63fe3ca3b2381fea7b78d31c89c"
    )
    assert row.state_sha256 == (
        "0ca7a16e2270cc85d633ba38275c63f1aa047242e2ec57a66da93d79fa37cc34"
    )

    normalized = replace(
        _definition(),
        source_tokens=("span", "customer", "span"),
    )
    assert normalized.source_tokens == ("customer", "span")


@pytest.mark.parametrize(
    ("source_version", "sequence"),
    [(0, 1), (1, 0)],
)
def test_projection_requires_positive_source_and_producer_sequences(
    source_version: int,
    sequence: int,
) -> None:
    with pytest.raises(ValueError, match="positive UInt64"):
        _row(source_version=source_version, sequence=sequence)


def test_tombstone_restore_stale_duplicate_and_conflict_resolution() -> None:
    live_v1 = _row(source_version=1, revision=1, sequence=1)
    tombstone_v2 = _row(
        source_version=2,
        revision=2,
        sequence=2,
        is_deleted=True,
        deleted_at=NOW + timedelta(seconds=2),
    )
    restore_v3 = _row(source_version=3, revision=3, sequence=3)

    assert resolve_source_update(restore_v3, tombstone_v2).status is (
        VersionResolutionStatus.STALE
    )
    duplicate = replace(restore_v3, catalog_revision=4, producer_sequence=9)
    duplicate_result = resolve_source_update(restore_v3, duplicate)
    assert duplicate_result.status is VersionResolutionStatus.DUPLICATE
    assert duplicate_result.current is duplicate

    conflict = _row(
        definition=_definition(details={"choices": ["free", "paid"]}),
        source_version=3,
        revision=4,
        sequence=4,
    )
    assert resolve_source_update(restore_v3, conflict).status is (
        VersionResolutionStatus.CONFLICT
    )
    with pytest.raises(DefinitionConflictError, match="conflicting source version 3"):
        resolve_binding_history([tombstone_v2, conflict, live_v1, restore_v3])

    resolved = resolve_binding_history([tombstone_v2, live_v1, duplicate, restore_v3])
    assert resolved.current is duplicate
    assert resolved.duplicate_count == 1
    assert resolved.stale_count == 2


def test_visibility_is_tenant_scoped_tombstone_safe_and_deduplicated() -> None:
    always = _row(
        visibility=VisibilityBinding(
            VisibilityScope.ALWAYS,
            "00000000-0000-0000-0000-000000000000",
        ),
        sequence=1,
    )
    workspace = _row(
        visibility=VisibilityBinding(VisibilityScope.WORKSPACE_DEFAULT, WORKSPACE),
        sequence=2,
    )
    project = _row(
        visibility=VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
        sequence=3,
    )
    deleted_project = _row(
        visibility=VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
        source_version=2,
        revision=2,
        sequence=4,
        is_deleted=True,
        deleted_at=NOW + timedelta(seconds=4),
    )
    other_tenant = project_definition(
        organization_id="99999999-9999-4999-8999-999999999999",
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        catalog_revision=1,
        build_token=BUILD_TOKEN,
        projection_version=1,
        visibility=VisibilityBinding(
            VisibilityScope.ALWAYS,
            "00000000-0000-0000-0000-000000000000",
        ),
        definition=_definition(),
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        source_entity_id="customer.plan",
        source_version=1,
        source_fingerprint=_sha("source-1"),
        producer_stream_id=STREAM,
        producer_sequence=1,
        emitted_at=NOW,
        first_seen=NOW,
        last_seen=NOW,
    )
    context = VisibilityContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=frozenset({PROJECT}),
    )

    revision_one = resolve_visible_definitions(
        [always, workspace, project, other_tenant],
        context=context,
        at_revision=1,
    )
    assert revision_one == (project,)

    revision_two = resolve_visible_definitions(
        [always, workspace, project, deleted_project, other_tenant],
        context=context,
        at_revision=2,
    )
    assert revision_two == (workspace,)

    with pytest.raises(ValueError, match="must equal workspace_id"):
        _row(
            visibility=VisibilityBinding(
                VisibilityScope.WORKSPACE_DEFAULT,
                "77777777-7777-4777-8777-777777777777",
            )
        )


def test_visible_same_property_with_different_definitions_fails_closed() -> None:
    base = _row(
        visibility=VisibilityBinding(
            VisibilityScope.ALWAYS,
            "00000000-0000-0000-0000-000000000000",
        )
    )
    different = _row(
        visibility=VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
        definition=_definition(details={"choices": ["enterprise"]}),
        source_adapter=SourceAdapter.EVAL_CONFIG,
    )
    context = VisibilityContext(ORG, WORKSPACE, frozenset({PROJECT}))

    with pytest.raises(DefinitionConflictError, match="conflicting visible"):
        resolve_visible_definitions([base, different], context=context, at_revision=1)


def test_envelope_counts_and_scope_are_validated() -> None:
    row = _row()
    envelope = PropertyCatalogEnvelope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        catalog_revision=1,
        build_token=BUILD_TOKEN,
        projection_version=1,
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=STREAM,
        sequence=1,
        previous_payload_sha256="0" * 64,
        source_version=1,
        source_fingerprint=_sha("source-1"),
        source_batch_digest=_sha("batch-1"),
        outcome=EnvelopeOutcome.COMMITTED,
        counts=EnvelopeCounts(1, 1, 0, 0, 0),
        definitions=(row,),
        gap_reasons=(),
        terminal=False,
    )
    assert envelope.counts.definition_count == 1

    terminal = replace(
        envelope,
        counts=EnvelopeCounts(0, 0, 0, 0, 0),
        definitions=(),
        terminal=True,
    )
    assert terminal.terminal
    with pytest.raises(ValueError, match="empty committed fence"):
        replace(envelope, terminal=True)

    with pytest.raises(ValueError, match="definition_count"):
        replace(envelope, counts=EnvelopeCounts(1, 2, 0, 0, 0))

    gap = replace(
        envelope,
        outcome=EnvelopeOutcome.GAP,
        counts=EnvelopeCounts(1, 1, 0, 0, 1),
        gap_reasons=("source_timeout",),
    )
    assert gap.outcome is EnvelopeOutcome.GAP

    with pytest.raises(ValueError, match="at least one reason"):
        replace(gap, counts=EnvelopeCounts(1, 1, 0, 0, 0), gap_reasons=())

    with pytest.raises(ValueError, match="positive UInt64"):
        replace(envelope, sequence=0)
    with pytest.raises(ValueError, match="positive UInt64"):
        replace(envelope, source_version=0)

    with pytest.raises(ValueError, match="another scope"):
        replace(envelope, definitions=(replace(row, producer_sequence=2),))
    with pytest.raises(ValueError, match="another scope"):
        replace(envelope, definitions=(_row(source_version=2),))


def test_definition_details_are_serializer_allowlisted_and_numeric_safe() -> None:
    definition = _definition(
        details={
            "unit": "score",
            "choices": [0.25, 1, "unknown"],
            "choice_options": [{"value": 0.25, "label": "Quarter"}],
            "allowed_aggregations": ["avg", "count"],
            "data_type": "number",
            "eval_template_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            "attribute_types": ["number", "string"],
            "attribute_types_exact": True,
        }
    )
    row = _row(definition=definition)
    assert '"choices":[0.25,1,"unknown"]' in row.definition.definition_json
    assert (
        '"eval_template_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"'
        in row.definition.definition_json
    )

    with pytest.raises(ValueError, match="unsupported or colliding fields"):
        _definition(details={"name": "would overwrite canonical name"})
