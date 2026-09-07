import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.property_catalog import candidate_repair as subject

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 9, 6, 10, tzinfo=UTC)


def request(tmp_path, **changes):
    doc = {
        "candidate_id": "a" * 64,
        "candidate_topic": "repair-candidates-test",
        "first_seen_us": subject._micros(NOW - timedelta(hours=2)),
        "last_seen_us": subject._micros(NOW),
        "format": subject._FORMAT,
        "generation": 1,
        "organization_id": ORG,
        "version": 1,
        "workspace_id": WORKSPACE,
    }
    doc.update(changes)
    directory = tmp_path / "candidate-receipts"
    directory.mkdir(exist_ok=True)
    path = directory / f"repair-{ORG}-{WORKSPACE}.json"
    raw = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return path, raw


def pending(tmp_path):
    return subject.pending_candidate_repair(
        drain_proof_file=str(tmp_path / "producer-drain-proof-v2.json"),
        organization_id=ORG,
        workspace_id=WORKSPACE,
        now=NOW,
    )


def test_missing_and_acknowledged_requests_are_idle(tmp_path):
    assert pending(tmp_path) is None
    path, raw = request(tmp_path)
    repair = pending(tmp_path)
    assert repair.needs_history(prior_until=NOW - timedelta(hours=1), now=NOW)
    assert repair.acknowledge_covered(
        since=NOW - timedelta(days=3), until=NOW + timedelta(seconds=1)
    )
    assert Path(str(path) + ".ack").read_bytes() == raw
    assert pending(tmp_path) is None


@pytest.mark.parametrize(
    "since,until",
    [
        (NOW - timedelta(hours=1), NOW + timedelta(seconds=1)),
        (NOW - timedelta(days=3), NOW),  # half-open upper boundary
        (NOW - timedelta(days=3), NOW - timedelta(seconds=1)),
    ],
)
def test_incomplete_scan_does_not_acknowledge(tmp_path, since, until):
    path, _ = request(tmp_path)
    assert not pending(tmp_path).acknowledge_covered(since=since, until=until)
    assert not Path(str(path) + ".ack").exists()


def test_resumed_old_snapshot_cannot_acknowledge_late_commit(tmp_path):
    request(tmp_path, last_seen_us=subject._micros(NOW - timedelta(hours=1)))
    assert not pending(tmp_path).acknowledge_covered(
        since=NOW - timedelta(days=3), until=NOW - timedelta(minutes=30)
    )


def test_concurrent_arrival_survives_old_ack_and_restart(tmp_path):
    _, old = request(tmp_path)
    observed = pending(tmp_path)
    path, new = request(tmp_path, candidate_id="b" * 64, generation=2)
    assert observed.acknowledge_covered(
        since=NOW - timedelta(days=3), until=NOW + timedelta(seconds=1)
    )
    assert Path(str(path) + ".ack").read_bytes() == old
    assert pending(tmp_path).raw == new


def test_claim_preserves_old_interval_across_crash_with_new_arrivals(tmp_path):
    path, old = request(tmp_path)
    assert pending(tmp_path).raw == old
    assert Path(str(path) + ".claim").read_bytes() == old
    # Mirrors Go's next request after observing a durable lifecycle claim.
    request(
        tmp_path,
        generation=2,
        candidate_id="b" * 64,
        first_seen_us=subject._micros(NOW),
    )
    resumed = pending(tmp_path)
    assert resumed.first_seen_us == subject._micros(NOW - timedelta(hours=2))
    assert json.loads(resumed.raw)["generation"] == 2


def test_new_arrivals_do_not_repeat_acknowledged_historical_interval(tmp_path):
    request(tmp_path)
    old = pending(tmp_path)
    request(
        tmp_path,
        generation=2,
        candidate_id="b" * 64,
        first_seen_us=subject._micros(NOW),
        last_seen_us=subject._micros(NOW),
    )
    assert old.acknowledge_covered(
        since=NOW - timedelta(days=3), until=NOW + timedelta(seconds=1)
    )
    fresh = pending(tmp_path)
    assert fresh.first_seen_us == subject._micros(NOW)
    assert not fresh.needs_history(prior_until=NOW, now=NOW)


def test_stale_completion_cannot_replace_newer_acknowledgement(tmp_path):
    request(tmp_path)
    old = pending(tmp_path)
    path, _ = request(tmp_path, generation=2, candidate_id="b" * 64)
    newer = pending(tmp_path)
    assert newer.acknowledge_covered(
        since=NOW - timedelta(days=3), until=NOW + timedelta(seconds=1)
    )
    assert not old.acknowledge_covered(
        since=NOW - timedelta(days=3), until=NOW + timedelta(seconds=1)
    )
    assert Path(str(path) + ".ack").read_bytes() == newer.raw


def test_same_generation_conflict_does_not_erase_claim(tmp_path):
    path, original = request(tmp_path)
    pending(tmp_path)
    request(tmp_path, candidate_id="b" * 64)
    with pytest.raises(ValueError, match="conflicting identities"):
        pending(tmp_path)
    assert Path(str(path) + ".claim").read_bytes() == original


def test_fresh_only_candidate_does_not_force_historical_scan(tmp_path):
    request(tmp_path, first_seen_us=subject._micros(NOW))
    assert not pending(tmp_path).needs_history(prior_until=NOW, now=NOW)


def test_runtime_supports_separate_persistent_control_and_spool_volumes(tmp_path):
    from dataclasses import replace

    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        PropertyCatalogDevRuntimeError,
    )
    from tracer.tests.test_property_catalog_dev_rollout import _unit_runtime_config

    control = tmp_path / "control"
    spool = tmp_path / "spool"
    control.mkdir()
    spool.mkdir()
    config = _unit_runtime_config(str(control))
    separated = replace(
        config,
        drain_proof_file=str(spool / "producer-drain-proof-v2.json"),
        producer_retirement_file=str(spool / "producer-state-retirements-v1.json"),
    )
    assert Path(separated.revision_fence_file).parent == control
    assert Path(separated.drain_proof_file).parent == spool
    with pytest.raises(PropertyCatalogDevRuntimeError, match="share the exact"):
        replace(separated, producer_retirement_file=config.producer_retirement_file)
    with pytest.raises(
        PropertyCatalogDevRuntimeError, match="revision fence file must be inside"
    ):
        replace(separated, revision_fence_file=str(spool / "revision-fence-v2.json"))


@pytest.mark.parametrize(
    "changes",
    [
        {"workspace_id": ORG},
        {"organization_id": WORKSPACE},
        {"generation": True},
        {"generation": 0},
        {"version": 2},
        {"extra": "field"},
        {"first_seen_us": 2**64},
        {"candidate_topic": "bad topic"},
    ],
)
def test_corrupt_or_cross_scope_request_fails_closed(tmp_path, changes):
    request(tmp_path, **changes)
    with pytest.raises(ValueError):
        pending(tmp_path)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversized", "duplicate"])
def test_bounded_nofollow_nonblocking_state_reader(tmp_path, kind):
    path, raw = request(tmp_path)
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(raw)
        path.unlink()
        path.symlink_to(target)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "duplicate":
        path.write_bytes(raw[:-2] + b',"version":1}\n')
    else:
        path.write_bytes(b"x" * 4097)
    with pytest.raises((ValueError, OSError)):
        pending(tmp_path)
