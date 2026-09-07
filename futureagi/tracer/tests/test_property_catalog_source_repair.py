"""Independent source notices: no Kafka fabrication and no negative proof."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import candidate_repair
from tracer.services.clickhouse.v2.property_catalog import source_repair as subject
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CheckedInPropertyCatalogDevRuntime,
    PropertyCatalogDevRuntimeFactory,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    LifecycleRunMode,
)
from tracer.services.clickhouse.v2.property_catalog.repair_files import utc_micros
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    CanonicalSpanSourceReader,
    PropertyCatalogSpanSourceError,
)
from tracer.tests.test_property_catalog_candidate_repair import (
    request as candidate_request,
)
from tracer.tests.test_property_catalog_dev_rollout import (
    ATTESTED_AT,
    ORG,
    WORKSPACE,
    _project_bindings,
    _provenance_observation,
    _request,
    _runtime_settings,
)
from tracer.tests.test_property_catalog_managed_runtime_activation import Driver
from tracer.tests.test_property_catalog_span_source import _FixedDeadline

NOW = datetime(2026, 9, 6, 10, tzinfo=UTC)
EVENT = NOW - timedelta(hours=2)
PROJECT = "33333333-3333-4333-8333-333333333333"


def scope(tmp_path):
    return {
        "state_directory": str(tmp_path),
        "organization_id": ORG,
        "workspace_id": WORKSPACE,
        "source_database": "default",
    }


def record(tmp_path, *, seen_at=EVENT, observed_at=NOW):
    return subject.record_source_repair(
        **scope(tmp_path), seen_at=seen_at, observed_at=observed_at
    )


def pending(tmp_path):
    return subject.pending_source_repair(**scope(tmp_path))


def pending_candidate(tmp_path, *, now=NOW):
    return candidate_repair.pending_candidate_repair(
        drain_proof_file=str(tmp_path / "producer-drain-proof-v2.json"),
        organization_id=ORG,
        workspace_id=WORKSPACE,
        now=now,
    )


def record_candidate(tmp_path, *, seen_at=EVENT, observed_at=NOW):
    candidate_request(
        tmp_path,
        first_seen_us=utc_micros(seen_at),
        last_seen_us=utc_micros(seen_at),
    )
    return pending_candidate(tmp_path, now=observed_at)


def test_notice_survives_restart_and_old_build_cannot_acknowledge(tmp_path):
    assert pending(tmp_path) is None
    original = record(tmp_path)
    assert original.path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "candidate-receipts").exists()
    resumed = pending(tmp_path)
    assert resumed.raw == original.raw
    assert not resumed.acknowledge_replacement(
        since=EVENT, until=NOW - timedelta(seconds=1)
    )
    assert pending(tmp_path) is not None
    assert resumed.acknowledge_replacement(since=EVENT, until=NOW)
    assert pending(tmp_path) is None


def test_new_request_is_not_lost_to_old_ack_or_restart(tmp_path):
    first = record(tmp_path)
    latest = record(
        tmp_path,
        seen_at=EVENT - timedelta(hours=1),
        observed_at=NOW + timedelta(seconds=1),
    )
    assert first.acknowledge_replacement(since=EVENT - timedelta(days=1), until=NOW)
    assert pending(tmp_path).raw == latest.raw
    assert not latest.acknowledge_replacement(since=EVENT, until=NOW)
    assert latest.acknowledge_replacement(
        since=EVENT - timedelta(days=1), until=NOW + timedelta(seconds=1)
    )
    assert not first.acknowledge_replacement(since=EVENT, until=NOW)
    assert pending(tmp_path) is None


def test_completed_interval_does_not_accumulate_forever(tmp_path):
    first = record(tmp_path)
    # A verified full replacement discards all prior rows, including any event
    # which aged out of the supported history window while repair was pending.
    assert first.acknowledge_replacement(since=EVENT + timedelta(seconds=1), until=NOW)
    new = record(
        tmp_path,
        seen_at=EVENT + timedelta(minutes=1),
        observed_at=NOW + timedelta(seconds=1),
    )
    assert json.loads(new.raw)["first_seen_us"] == utc_micros(
        EVENT + timedelta(minutes=1)
    )
    assert json.loads(new.raw)["generation"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_database", "different"),
        ("source_table", "traces"),
        ("workspace_id", ORG),
        ("generation", True),
        ("generation", 0),
        ("version", 2),
        ("observed_at_us", 0),
        ("extra", "field"),
    ],
)
def test_corrupt_notice_cannot_be_acknowledged_or_overwritten(tmp_path, field, value):
    original = record(tmp_path)
    doc = json.loads(original.raw)
    doc[field] = value
    corrupt = subject._encode(doc)
    original.path.write_bytes(corrupt)
    with pytest.raises(ValueError):
        pending(tmp_path)
    with pytest.raises(ValueError):
        record(tmp_path)
    assert original.path.read_bytes() == corrupt


@pytest.mark.parametrize(
    "kind", ["symlink", "fifo", "oversized", "duplicate", "ack-ahead"]
)
def test_bad_durable_state_fails_without_blocking_or_following(tmp_path, kind):
    original = record(tmp_path)
    path = original.path
    if kind == "symlink":
        target = tmp_path / "original"
        target.write_bytes(original.raw)
        path.unlink()
        path.symlink_to(target)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "oversized":
        path.write_bytes(b"x" * 4097)
    elif kind == "duplicate":
        path.write_bytes(original.raw[:-2] + b',"version":1}\n')
    else:
        doc = json.loads(original.raw)
        doc["generation"] += 1
        Path(str(path) + ".ack").write_bytes(subject._encode(doc))
    with pytest.raises((ValueError, OSError)):
        pending(tmp_path)


def probe(rows):
    calls = []

    def query(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        if isinstance(rows, Exception):
            raise rows
        return rows

    deadline = _FixedDeadline(2100)
    reader = CanonicalSpanSourceReader(
        SimpleNamespace(source_database="default", query=query),
        source_database="default",
        catalog_database="property_catalog_dev",
        deadline=deadline,
    )
    return reader, calls, deadline


def run_probe(reader):
    return reader.newly_versioned_history(
        project_ids=(PROJECT,), since=EVENT, until=NOW
    )


def hit(**changes):
    return dict(
        project_id_text=PROJECT,
        seen_at_us=utc_micros(EVENT),
        source_version=utc_micros(NOW) * 1000,
        **changes,
    )


def test_probe_is_scoped_readonly_narrow_and_uses_shared_deadline():
    reader, calls, deadline = probe([hit()])
    assert run_probe(reader) == EVENT
    sql, params, limits = calls[0]
    assert "FROM `default`.`spans`" in sql and "PREWHERE project_id IN" in sql
    assert "_version >= %(catalog_history_version_floor)s" in sql
    assert "LIMIT 1" in sql and "FINAL" not in sql
    assert "attributes" not in sql and "is_deleted" not in sql
    assert params["catalog_project_ids"] == (PROJECT,)
    assert params["catalog_history_version_floor"] == utc_micros(NOW) * 1000
    assert limits["timeout_ms"] == 2100 and deadline.caps == [5000]
    assert limits["settings"]["readonly"] == 2
    assert limits["settings"]["read_overflow_mode"] == "throw"
    assert limits["settings"]["max_bytes_to_read"] > 0


@pytest.mark.parametrize(
    "changes",
    [
        {"project_id_text": ORG},
        {"seen_at_us": utc_micros(NOW)},
        {"seen_at_us": utc_micros(EVENT) - 1},
        {"source_version": 1},
        {"source_version": True},
        {"seen_at_us": "1"},
    ],
)
def test_probe_rejects_malformed_or_cross_scope_hits(changes):
    reader, _, _ = probe([{**hit(), **changes}])
    with pytest.raises((ValueError, PropertyCatalogSpanSourceError)):
        run_probe(reader)


def test_probe_empty_is_only_a_hint_and_query_failure_is_not_empty():
    reader, _, _ = probe([])
    assert run_probe(reader) is None
    reader, _, _ = probe([hit(), hit()])
    with pytest.raises(PropertyCatalogSpanSourceError):
        run_probe(reader)
    reader, _, _ = probe(RuntimeError("read limit"))
    with pytest.raises(RuntimeError, match="read limit"):
        run_probe(reader)


@pytest.mark.parametrize("project_ids", [(PROJECT,), ()], ids=["nonempty", "empty"])
@pytest.mark.parametrize("notices", ["source", "candidate", "both"])
def test_auto_preparation_uses_notices_only_for_nonempty_scope(
    tmp_path, monkeypatch, project_ids, notices
):
    config = _runtime_settings(str(tmp_path))
    config.PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST = project_ids
    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=config,
        native_client_factory=Driver,
        provenance_probe=lambda *_: _provenance_observation(),
        project_tenant_binding_probe=lambda ids, _: _project_bindings(ids),
        now=lambda: ATTESTED_AT,
    )
    runtime = factory(_request(execute=True))
    try:
        source_notice = candidate_notice = None
        if notices in {"source", "both"}:
            source_notice = subject.record_source_repair(
                **runtime._source_repair_scope(),
                seen_at=ATTESTED_AT - timedelta(hours=1),
                observed_at=ATTESTED_AT - timedelta(seconds=10),
            )
        if notices in {"candidate", "both"}:
            candidate_notice = record_candidate(
                tmp_path,
                seen_at=ATTESTED_AT - timedelta(hours=1),
                observed_at=ATTESTED_AT,
            )

        class Prepared(Exception):
            pass

        modes = []

        def prepare(**kwargs):
            modes.append(kwargs["mode"])
            assert kwargs["scope"].project_ids == project_ids
            raise Prepared

        def active(_runtime, bound):
            assert bound.project_ids, "empty notices must not inspect ACTIVE history"
            return SimpleNamespace(
                build_plan=SimpleNamespace(
                    source_scope=SimpleNamespace(
                        span_until_us=utc_micros(ATTESTED_AT - timedelta(minutes=30))
                    )
                )
            )

        monkeypatch.setattr(runtime.lifecycle, "prepare", prepare)
        monkeypatch.setattr(
            CheckedInPropertyCatalogDevRuntime, "_load_latest_active_retirement", active
        )
        monkeypatch.setattr(
            runtime.lifecycle, "load_fenced_scope_drift", lambda _: None
        )
        with pytest.raises(Prepared):
            runtime._prepare_revision(LifecycleRunMode.AUTO)
        assert modes == [
            LifecycleRunMode.FULL_REPAIR if project_ids else LifecycleRunMode.AUTO
        ]
        assert (runtime._source_repair is not None) == (source_notice is not None)
        assert (runtime._candidate_repair is not None) == (candidate_notice is not None)
        if source_notice is not None:
            assert (
                subject.pending_source_repair(**runtime._source_repair_scope()).raw
                == source_notice.raw
            )
            assert not Path(str(source_notice.path) + ".ack").exists()
        if candidate_notice is not None:
            assert pending_candidate(tmp_path).raw == candidate_notice.raw
            assert not Path(str(candidate_notice.path) + ".ack").exists()
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "mode", [LifecycleRunMode.INCREMENTAL, LifecycleRunMode.FULL_REPAIR]
)
def test_notice_probes_only_history_before_the_incremental_window(tmp_path, mode):
    calls = []
    runtime = SimpleNamespace(
        _source_capture=None,
        config=SimpleNamespace(span_since=EVENT - timedelta(days=1)),
        now=lambda: NOW,
        _source_repair_scope=lambda: scope(tmp_path),
        span_reader=SimpleNamespace(
            newly_versioned_history=lambda **kw: calls.append(kw) or EVENT
        ),
    )
    execution = SimpleNamespace(
        prepared=SimpleNamespace(
            mode=mode,
            scope=SimpleNamespace(project_ids=(PROJECT,)),
            cutoffs=SimpleNamespace(
                span_window=SimpleNamespace(since=NOW - timedelta(minutes=1), until=NOW)
            ),
        )
    )
    CheckedInPropertyCatalogDevRuntime._notice_historical_source_change(
        runtime, execution
    )
    if mode is LifecycleRunMode.INCREMENTAL:
        assert calls == [
            {
                "project_ids": (PROJECT,),
                "since": runtime.config.span_since,
                "until": NOW - timedelta(minutes=1),
            }
        ]
        assert pending(tmp_path) is not None
    else:
        assert calls == [] and pending(tmp_path) is None


@pytest.mark.parametrize(
    "mode", [LifecycleRunMode.INCREMENTAL, LifecycleRunMode.FULL_REPAIR]
)
@pytest.mark.parametrize("bad_readback", [False, True])
@pytest.mark.parametrize("project_ids", [(PROJECT,), ()], ids=["nonempty", "empty"])
@pytest.mark.parametrize("capture_state", ["legacy", "fresh", "resumed"])
def test_activation_persists_signal_first_and_only_verified_replacement_acks(
    tmp_path, monkeypatch, mode, bad_readback, project_ids, capture_state
):
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    notice = record(tmp_path, observed_at=NOW - timedelta(seconds=10))
    candidate_notice = record_candidate(
        tmp_path,
        seen_at=NOW - timedelta(seconds=15),
        observed_at=NOW - timedelta(seconds=10),
    )
    events = []
    bound = SimpleNamespace(project_ids=project_ids)
    record_fields = {
        "catalog_revision": 2,
        "build_token": "test-build",
        "projection_version": 1,
        "lifecycle_mode": mode,
        "activation_sequence": 2,
        "activation_sha256": "a" * 64,
        "source_manifest_sha256": "b" * 64,
        "live_definition_rows": 1,
        "tombstone_rows": 1,
        "value_rows": 1,
    }
    active = SimpleNamespace(
        **record_fields, build_plan=SimpleNamespace(sha256="c" * 64)
    )
    if bad_readback:
        active.activation_sha256 = "d" * 64
    execution = SimpleNamespace(
        activation=None,
        manifest=object(),
        fence=object(),
        lease=SimpleNamespace(build_lease_sha256="c" * 64),
        prepared=SimpleNamespace(
            mode=mode,
            scope=bound,
            resumed=capture_state == "resumed",
            cutoffs=SimpleNamespace(
                span_window=SimpleNamespace(
                    since=EVENT
                    if mode is LifecycleRunMode.FULL_REPAIR
                    else NOW - timedelta(seconds=20),
                    until=NOW - timedelta(seconds=5),
                )
            ),
        ),
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(span_since=EVENT),
        _source_capture=(
            None
            if capture_state == "legacy"
            else SimpleNamespace(
                retire=lambda prepared, *, completion_proof=None: events.append(
                    "capture_retired"
                )
            )
        ),
        now=lambda: NOW,
        _source_repair_scope=lambda: scope(tmp_path),
        _validate_mutation_request=lambda *_: None,
        _refresh_project_tenant_authorization=lambda: SimpleNamespace(
            authorization_contract_sha256="e" * 64
        ),
        _require_execution=lambda: execution,
        _activation_inventory=lambda _: object(),
        _notice_source_part_changes=lambda _: events.append("parts"),
        _load_latest_active_retirement=lambda _: active,
        _publish_producer_retirement=lambda *_: events.append("retirement"),
        reconcile_reader_selection=lambda: events.append("selection"),
        _authorized_build_binding_sha256=None,
        _candidate_repair=candidate_notice,
        _source_repair=notice,
        state_store=object(),
        coordinator=object(),
        span_reader=SimpleNamespace(
            newly_versioned_history=lambda **_: events.append("probe") or EVENT
        ),
    )
    runtime._notice_historical_source_change = lambda execution: (
        CheckedInPropertyCatalogDevRuntime._notice_historical_source_change(
            runtime, execution
        )
    )

    class Activator:
        def __init__(self, *args, **kwargs):
            pass

        def activate(self, **kwargs):
            observed = pending(tmp_path)
            assert observed is not None
            if mode is LifecycleRunMode.INCREMENTAL and project_ids:
                assert json.loads(observed.raw)["generation"] == 2
            else:
                assert observed.raw == notice.raw
            assert pending_candidate(tmp_path).raw == candidate_notice.raw
            assert not Path(str(notice.path) + ".ack").exists()
            assert not Path(str(candidate_notice.path) + ".ack").exists()
            events.append("activate")
            return SimpleNamespace(
                record=SimpleNamespace(**record_fields), idempotent=False
            )

    monkeypatch.setattr(dev_runtime, "PropertyCatalogActivator", Activator)
    runtime._activator = lambda guard: CheckedInPropertyCatalogDevRuntime._activator(
        runtime, guard
    )
    if bad_readback:
        with pytest.raises(
            dev_runtime.PropertyCatalogDevRuntimeError, match="durably reread"
        ):
            CheckedInPropertyCatalogDevRuntime.activate(runtime, object())
        assert pending(tmp_path) is not None
        assert "retirement" not in events and "selection" not in events
    else:
        assert CheckedInPropertyCatalogDevRuntime.activate(runtime, object())[
            "activated"
        ]
    can_ack = not bad_readback and bool(project_ids) and capture_state != "resumed"
    source_acked = can_ack and mode is LifecycleRunMode.FULL_REPAIR
    assert (pending(tmp_path) is None) == source_acked
    assert (pending_candidate(tmp_path) is None) == can_ack
    for original, acknowledged in (
        (notice, source_acked),
        (candidate_notice, can_ack),
    ):
        ack = Path(str(original.path) + ".ack")
        if acknowledged:
            assert ack.read_bytes() == original.raw
        else:
            assert not ack.exists()
    if not project_ids or capture_state == "resumed":
        assert pending_candidate(tmp_path).raw == candidate_notice.raw
        if not project_ids or mode is LifecycleRunMode.FULL_REPAIR:
            assert pending(tmp_path).raw == notice.raw
    assert events[:3] == (
        ["probe", "parts", "activate"]
        if mode is LifecycleRunMode.INCREMENTAL and project_ids
        else ["parts", "activate", "retirement"]
        if not bad_readback
        else ["parts", "activate"]
    )


@pytest.mark.parametrize("failure", ["query", "fsync"])
def test_pre_activation_signal_failure_propagates(tmp_path, monkeypatch, failure):
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    def fail(*args, **kwargs):
        raise OSError(failure)

    runtime = SimpleNamespace(
        _source_capture=None,
        config=SimpleNamespace(span_since=EVENT),
        now=lambda: NOW,
        _source_repair_scope=lambda: scope(tmp_path),
        span_reader=SimpleNamespace(
            newly_versioned_history=fail if failure == "query" else lambda **_: EVENT
        ),
    )
    if failure == "fsync":
        monkeypatch.setattr(dev_runtime, "record_source_repair", fail)
    execution = SimpleNamespace(
        prepared=SimpleNamespace(
            mode=LifecycleRunMode.INCREMENTAL,
            scope=SimpleNamespace(project_ids=(PROJECT,)),
            cutoffs=SimpleNamespace(
                span_window=SimpleNamespace(
                    since=NOW - timedelta(seconds=20), until=NOW
                )
            ),
        )
    )
    with pytest.raises(OSError, match=failure):
        CheckedInPropertyCatalogDevRuntime._notice_historical_source_change(
            runtime, execution
        )
    assert pending(tmp_path) is None
