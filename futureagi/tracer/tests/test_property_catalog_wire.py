from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

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
from tracer.services.clickhouse.v2.property_catalog.projection import project_definition
from tracer.services.clickhouse.v2.property_catalog.wire import (
    encode_envelope,
    parse_envelope,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
STREAM = "44444444-4444-4444-8444-444444444444"
BUILD = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _envelope(*, terminal: bool = False) -> PropertyCatalogEnvelope:
    rows = ()
    if not terminal:
        definition = PropertyDefinition(
            property_kind=PropertyKind.CUSTOM_ATTRIBUTE,
            source_key="customer.plan",
            category=PropertyCategory.CUSTOM_ATTRIBUTE,
            category_rank=3,
            source_rank=0,
            definition_source="span_attribute_value_catalog",
            primary_source="traces",
            source_tokens=("attribute",),
            value_adapter="span_attribute_value",
            name="customer.plan",
            display_name="Customer Plan",
            value_type="string",
            output_type="string",
            role=PropertyRole.DIMENSION,
        )
        rows = (
            project_definition(
                organization_id=ORG,
                workspace_id=WORKSPACE,
                catalog_epoch=1,
                catalog_revision=1,
                build_token=BUILD,
                projection_version=1,
                visibility=VisibilityBinding(VisibilityScope.PROJECT, PROJECT),
                definition=definition,
                source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                source_entity_id="customer.plan",
                source_version=1,
                source_fingerprint=_sha("source"),
                producer_stream_id=STREAM,
                producer_sequence=1,
                emitted_at=NOW,
            ),
        )
    return PropertyCatalogEnvelope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=1,
        build_token=BUILD,
        projection_version=1,
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=STREAM,
        sequence=1,
        previous_payload_sha256="0" * 64,
        source_version=1,
        source_fingerprint=_sha("source"),
        source_batch_digest=_sha("batch"),
        outcome=EnvelopeOutcome.COMMITTED,
        counts=EnvelopeCounts(
            source_count=0 if terminal else 1,
            definition_count=len(rows),
            value_count=0,
            tombstone_count=0,
            gap_count=0,
        ),
        definitions=rows,
        gap_reasons=(),
        terminal=terminal,
    )


def test_definition_and_empty_terminal_envelopes_round_trip_exactly() -> None:
    for envelope in (_envelope(), _envelope(terminal=True)):
        encoded = encode_envelope(envelope)
        parsed = parse_envelope(encoded.raw)

        assert parsed.raw == encoded.raw
        assert parsed.payload_sha256 == encoded.payload_sha256
        assert parsed.envelope_id == encoded.envelope_id
        assert encoded.raw.endswith(b"}")


def test_go_v1_cross_language_fixture_rederives_every_wire_identity() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "fi-collector/pkg/propertycatalog/testdata/wire_v1_fixtures.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["format"] == "futureagi.property-catalog-wire-fixtures"
    for case in fixture["cases"]:
        raw = base64.b64decode(case["wire_base64"])
        parsed = parse_envelope(raw)

        assert parsed.raw == raw
        assert parsed.payload_sha256 == case["payload_sha256"]
        assert parsed.envelope_id == case["envelope_id"]
