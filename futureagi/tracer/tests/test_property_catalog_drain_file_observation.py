"""Incomplete shared-file observations never become publication proof."""

from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    SharedVolumeHotDrainProofSource,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_contract import (
    ProducerDrainProofError,
    parse_producer_drain_proof,
)


def source(tmp_path):
    path = tmp_path / "producer-drain-proof-v2.json"
    return path, SharedVolumeHotDrainProofSource(str(path), Mock(), sleeper=Mock())


def test_truncated_stable_metadata_observation_is_not_parsed(tmp_path):
    path, reader = source(tmp_path)
    truncated = b'{"format":"futureagi.property-catalog-drain-proof","gap_acknowled'
    path.write_bytes(truncated)
    assert reader._read_once() is None
    with pytest.raises(ProducerDrainProofError):
        parse_producer_drain_proof(truncated)
    complete = (
        b'{"format":"futureagi.property-catalog-drain-proof","version":2,"proofs":[]}\n'
    )
    path.write_bytes(complete)
    assert reader._read_once() == complete
    assert parse_producer_drain_proof(complete) == ()


def test_permanently_incomplete_proof_exhausts_original_deadline(tmp_path):
    path, reader = source(tmp_path)
    path.write_bytes(b'{"incomplete":')
    reader.deadline.remaining_ms.side_effect = [
        100,
        100,
        TimeoutError("original deadline"),
    ]
    with pytest.raises(TimeoutError, match="original deadline"):
        reader.wait_for(
            assignment=object(),
            producer_stream_id="00000000-0000-4000-8000-000000000001",
            phase="prepared",
        )
    reader.sleeper.assert_called_once()


def test_complete_invalid_proof_still_fails_immediately(tmp_path):
    path, reader = source(tmp_path)
    path.write_bytes(b'{"invalid":true}\n')
    reader.deadline.remaining_ms.return_value = 100
    with pytest.raises(ProducerDrainProofError):
        reader.wait_for(
            assignment=object(),
            producer_stream_id="00000000-0000-4000-8000-000000000001",
            phase="prepared",
        )
    reader.sleeper.assert_not_called()
