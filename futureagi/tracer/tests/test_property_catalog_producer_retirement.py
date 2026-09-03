from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    CatalogLifecycleMode,
    RevisionBuildPlan,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    ActiveStreamEvidence,
    FrozenLifecycleCutoffs,
    LifecycleRunMode,
    LineageAnchorEvidence,
    PriorActiveEvidence,
    SourceWindow,
    WorkspaceCatalogScope,
    _planned_streams,
)
from tracer.services.clickhouse.v2.property_catalog.producer_retirement import (
    AtomicProducerStateRetirementFile,
    ProducerRetirementError,
    ProducerStateRetirement,
    decode_producer_retirements,
    encode_producer_retirements,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
HOT_STREAM = "55555555-5555-4555-8555-555555555555"
BUILD = "66666666-6666-4666-8666-666666666666"
SHA_A = "a" * 64
SHA_B = "b" * 64
AT = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _active() -> PriorActiveEvidence:
    scope = WorkspaceCatalogScope(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        projection_version=1,
        project_ids=(PROJECT,),
    )
    cutoffs = FrozenLifecycleCutoffs(
        snapshot_upper=datetime(2026, 8, 14, 12, tzinfo=UTC),
        span_window=SourceWindow(
            datetime(2026, 8, 14, 11, tzinfo=UTC),
            datetime(2026, 8, 14, 12, tzinfo=UTC),
        ),
        span_audit_generation=7,
    )
    plan = RevisionBuildPlan(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        catalog_revision=17,
        build_token=BUILD,
        projection_version=1,
        source_scope=BuildPlanSourceScope(
            project_ids=(PROJECT,),
            span_since_us=1_786_705_200_000_000,
            span_until_us=1_786_708_800_000_000,
        ),
        streams=_planned_streams(
            scope=scope,
            mode=LifecycleRunMode.INITIAL_BACKFILL,
            build_token=BUILD,
            hot_producer_stream_id=HOT_STREAM,
            cutoffs=cutoffs,
            prior_active_revision=None,
        ),
    )
    streams = tuple(
        ActiveStreamEvidence(
            source_adapter=value.source_adapter,
            role=value.role,
            producer_stream_id=value.producer_stream_id,
            source_version_fence=value.source_version_fence,
            watermark=f"watermark:{value.source_adapter}:{value.role}",
            checkpoint_state_sha256=SHA_A,
        )
        for value in plan.streams
    )
    return PriorActiveEvidence(
        catalog_revision=17,
        build_token=BUILD,
        projection_version=1,
        lifecycle_mode=CatalogLifecycleMode.INITIAL_BACKFILL,
        activation_sequence=1,
        activation_sha256=SHA_B,
        source_manifest_sha256=SHA_A,
        build_plan=plan,
        streams=streams,
        qualified_at=AT,
        lineage_anchor=LineageAnchorEvidence(
            catalog_revision=17,
            build_token=BUILD,
            mode=LifecycleRunMode.INITIAL_BACKFILL,
            qualified_at=AT,
            activation_sequence=1,
            activation_sha256=SHA_B,
            active_revisions_since=0,
        ),
    )


def _retirement(*, emitted_at: datetime = AT) -> ProducerStateRetirement:
    return ProducerStateRetirement.from_active(
        _active(),
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=3,
        emitted_at=emitted_at,
    )


def test_python_retirement_contract_round_trips_canonical_activation() -> None:
    value = _retirement()
    raw = encode_producer_retirements((value,))

    fixture = (
        Path(__file__).resolve().parents[3]
        / "fi-collector/pkg/propertycatalog/testdata/producer_retirement_v1.json"
    ).read_bytes()

    assert decode_producer_retirements(raw) == (value,)
    assert raw == fixture
    assert raw.endswith(b"\n") and b"\n" not in raw[:-1]
    assert value.hot_producer_stream_id == HOT_STREAM
    assert (
        value.build_lease_sha256
        == hashlib.sha256(value.build_plan_json.encode("utf-8")).hexdigest()
    )


def test_atomic_retirement_is_private_idempotent_and_crash_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "producer-state-retirements-v1.json"
    sink = AtomicProducerStateRetirementFile(path)
    first = _retirement()
    sink.publish(first)
    original = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    # A replay of the same durable activation may have a later emission clock;
    # the existing proof remains byte-identical.
    sink.publish(
        replace(
            first,
            emitted_at=datetime(2026, 8, 14, 12, 1, tzinfo=UTC),
            retirement_sha256="0" * 64,
        )
    )
    assert path.read_bytes() == original

    conflicting = replace(
        first,
        activation_sha256="c" * 64,
        lineage_anchor_activation_sha256="c" * 64,
        retirement_sha256="0" * 64,
    )
    with pytest.raises(ProducerRetirementError, match="conflicts"):
        sink.publish(conflicting)
    assert path.read_bytes() == original

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", fail_replace)
    other_path = tmp_path / "other" / "producer-state-retirements-v1.json"
    other_path.parent.mkdir()
    other = AtomicProducerStateRetirementFile(other_path)
    with pytest.raises(OSError, match="simulated crash"):
        other.publish(first)
    assert not other_path.exists()
    assert list(other_path.parent.iterdir()) == []


def test_retirement_rejects_tamper_scope_and_unsafe_existing_file(
    tmp_path: Path,
) -> None:
    value = _retirement()
    raw = encode_producer_retirements((value,))
    with pytest.raises(ProducerRetirementError, match="digest"):
        decode_producer_retirements(
            raw.replace(value.retirement_sha256.encode(), ("c" * 64).encode(), 1)
        )

    with pytest.raises(ProducerRetirementError, match="scope"):
        ProducerStateRetirement.from_active(
            _active(),
            organization_id=ORG,
            workspace_id="77777777-7777-4777-8777-777777777777",
            catalog_epoch=3,
            emitted_at=AT,
        )

    path = tmp_path / "producer-state-retirements-v1.json"
    path.write_bytes(raw)
    path.chmod(0o622)
    with pytest.raises(ProducerRetirementError, match="unsafe"):
        AtomicProducerStateRetirementFile(path).publish(value)
