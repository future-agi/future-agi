from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from tracer.services.clickhouse.v2.property_catalog.models import SourceAdapter
from tracer.services.clickhouse.v2.property_catalog.qualification import (
    CatalogCheckpoint,
    CheckpointStatus,
    RevisionRequirement,
    StreamRequirement,
    qualify_revision,
)

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
STREAM = "44444444-4444-4444-8444-444444444444"
OTHER_STREAM = "55555555-5555-4555-8555-555555555555"
BUILD_TOKEN = "66666666-6666-4666-8666-666666666666"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _requirement() -> RevisionRequirement:
    return RevisionRequirement(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=4,
        catalog_revision=7,
        build_token=BUILD_TOKEN,
        projection_version=2,
        streams=(
            StreamRequirement(
                source_adapter=SourceAdapter.DATASET_COLUMN,
                producer_stream_id=STREAM,
                source_version_fence=91,
                expected_source_count=8,
                expected_definition_count=10,
                expected_value_count=0,
                expected_tombstone_count=2,
                expected_source_digest=_sha("source"),
                expected_emitted_digest=_sha("emitted"),
                expected_first_sequence=1,
                expected_last_sequence=3,
                expected_terminal_payload_sha256=_sha("terminal"),
            ),
        ),
    )


def _checkpoint() -> CatalogCheckpoint:
    return CatalogCheckpoint(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=4,
        catalog_revision=7,
        build_token=BUILD_TOKEN,
        projection_version=2,
        source_adapter=SourceAdapter.DATASET_COLUMN,
        producer_stream_id=STREAM,
        source_version_fence=91,
        status=CheckpointStatus.COMPLETE,
        terminal=True,
        source_count=8,
        definition_count=10,
        value_count=0,
        tombstone_count=2,
        gap_count=0,
        poison_count=0,
        conflict_count=0,
        first_sequence=1,
        last_sequence=3,
        last_issued_sequence=3,
        fenced_sequence=3,
        terminal_payload_sha256=_sha("terminal"),
        delivery_count=3,
        source_digest=_sha("source"),
        emitted_digest=_sha("emitted"),
    )


def test_exact_terminal_contiguous_revision_qualifies_with_golden_digest() -> None:
    result = qualify_revision(_requirement(), [_checkpoint()])

    assert result.qualified
    assert result.issues == ()
    assert result.activation_sha256 == (
        "5886f25f4da871529c34c64f3828608fff91176ce30ee1b8e3a837e1c2e19f60"
    )


def test_missing_unexpected_or_duplicate_streams_fail_closed() -> None:
    requirement = _requirement()
    checkpoint = _checkpoint()
    assert qualify_revision(requirement, []).issues == (
        f"missing_stream:dataset_column:{STREAM}",
    )

    duplicate = qualify_revision(requirement, [checkpoint, checkpoint])
    assert not duplicate.qualified
    assert duplicate.activation_sha256 is None
    assert duplicate.issues == (f"duplicate_checkpoint:dataset_column:{STREAM}",)

    unexpected = replace(
        checkpoint,
        source_adapter=SourceAdapter.ANNOTATION_LABEL,
        producer_stream_id=OTHER_STREAM,
    )
    result = qualify_revision(requirement, [checkpoint, unexpected])
    assert result.issues == (f"unexpected_stream:annotation_label:{OTHER_STREAM}",)


def test_incomplete_counts_digests_gaps_and_sequence_holes_all_fail() -> None:
    checkpoint = replace(
        _checkpoint(),
        workspace_id="99999999-9999-4999-8999-999999999999",
        status=CheckpointStatus.GAP,
        terminal=False,
        source_count=9,
        definition_count=11,
        value_count=1,
        tombstone_count=3,
        gap_count=1,
        poison_count=1,
        conflict_count=1,
        source_version_fence=92,
        first_sequence=2,
        last_sequence=4,
        delivery_count=2,
        source_digest=_sha("wrong-source"),
        emitted_digest=_sha("wrong-emitted"),
    )

    result = qualify_revision(_requirement(), [checkpoint])
    assert not result.qualified
    assert result.activation_sha256 is None
    assert result.issues == (
        f"scope_mismatch:dataset_column:{STREAM}",
        f"source_version_fence_mismatch:dataset_column:{STREAM}",
        f"not_complete:dataset_column:{STREAM}",
        f"not_terminal:dataset_column:{STREAM}",
        f"not_fenced_at_terminal:dataset_column:{STREAM}",
        f"gaps:dataset_column:{STREAM}",
        f"poison:dataset_column:{STREAM}",
        f"conflicts:dataset_column:{STREAM}",
        f"source_count_mismatch:dataset_column:{STREAM}",
        f"definition_count_mismatch:dataset_column:{STREAM}",
        f"value_count_mismatch:dataset_column:{STREAM}",
        f"tombstone_count_mismatch:dataset_column:{STREAM}",
        f"source_digest_mismatch:dataset_column:{STREAM}",
        f"emitted_digest_mismatch:dataset_column:{STREAM}",
        f"sequence_fence_mismatch:dataset_column:{STREAM}",
        f"non_contiguous_delivery:dataset_column:{STREAM}",
    )


def test_missing_quiet_stream_terminal_delivery_is_rejected() -> None:
    with pytest.raises(ValueError, match="terminal delivery sequence"):
        replace(
            _requirement().streams[0],
            expected_first_sequence=None,
            expected_last_sequence=None,
        )

    with pytest.raises(ValueError, match="both be set"):
        replace(_checkpoint(), first_sequence=1, last_sequence=None)


def test_forged_self_consistent_checkpoint_cannot_override_manifest_expectation() -> (
    None
):
    forged = replace(
        _checkpoint(),
        source_count=7,
        source_digest=_sha("forged-source"),
    )
    result = qualify_revision(_requirement(), [forged])
    assert not result.qualified
    assert result.issues == (
        f"source_count_mismatch:dataset_column:{STREAM}",
        f"source_digest_mismatch:dataset_column:{STREAM}",
    )
