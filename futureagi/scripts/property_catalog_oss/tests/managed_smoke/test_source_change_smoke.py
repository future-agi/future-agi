"""Offline fixture guard tests; mocked external I/O is not live qualification."""

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import source_change_smoke as smoke

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    CheckedInPropertyCatalogDevRuntime as Runtime,
)
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeSourceClient,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    LifecycleRunMode,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    DurableSourceCapture,
    SourceCaptureError,
    SourceCaptureSpec,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    AuthoritativeSpanReconciler,
    AuthoritativeSpanRole,
)


def uid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


@pytest.fixture
def lane(tmp_path, monkeypatch):
    fixture = {
        "project_id": uid(1),
        "workspace_id": uid(2),
        "organization_id": uid(3),
        "since": "2026-09-06T00:00:00+00:00",
    }
    run = NS(
        directory=tmp_path,
        manifest={"application": True, "run_id": "0123456789abcdef"},
        owned=Mock(return_value=["owned"]),
    )
    smoke.publish(tmp_path / "source-fixture.json", fixture)
    smoke.publish(
        tmp_path / "source-change-arm.json",
        {
            "run_id": run.manifest["run_id"],
            "fixture": fixture,
            "deadline": time.monotonic() + smoke.COMPLETION_TIMEOUT_SECONDS,
        },
    )
    since = datetime.fromisoformat(fixture["since"])
    frozen = NS(since=since, until=since + timedelta(hours=2), project_ids=(uid(1),))
    lease = NS(
        organization_id=uid(3),
        workspace_id=uid(2),
        build_token=uid(4),
        catalog_epoch=1,
        catalog_revision=9,
    )
    spec = SourceCaptureSpec(
        uid(5),
        uid(3),
        uid(2),
        uid(4),
        uid(6),
        "default",
        "property_catalog_dev_app_" + run.manifest["run_id"],
        uid(7),
        "a" * 64,
        "b" * 64,
    )
    before = {
        "versions": 1,
        "deleted": 0,
        "value": "initial",
        "version": 123000,
        "seen_us": int(since.timestamp() * 1e6),
    }
    after = {**before, "value": "midscan_updated", "version": 124000}
    reader = NS(
        _source_table=lambda: f"`{spec.capture_database}`.`{spec.capture_table}`",
        _query=Mock(return_value=[{**before, "server_uuid": uid(6)}]),
        parts_snapshot=Mock(return_value=(("part", "checksum"),)),
    )
    checkpoint = NS(
        **vars(lease),
        terminal=True,
        status="complete",
        producer_stream_id=uid(8),
        source_count=4,
        source_digest="c" * 64,
        gap_count=0,
        poison_count=0,
        conflict_count=0,
    )
    execution = NS(
        lease=lease,
        prepared=NS(
            scope=NS(
                organization_id=uid(3), workspace_id=uid(2), project_ids=(uid(1),)
            ),
            mode=LifecycleRunMode.FULL_REPAIR,
            resumed=False,
        ),
        frozen=frozen,
        capture_binding=NS(spec=spec, reader=reader),
        authoritative=NS(
            values=checkpoint,
            source_audit=NS(**{**vars(checkpoint), "producer_stream_id": uid(9)}),
        ),
        activation=NS(record=NS(**vars(lease), activation_sha256="d" * 64)),
    )
    runtime = NS(_require_execution=lambda: execution, _source_repair=None)
    reconciler = NS(_reader=reader)
    order, statements = [], []

    def stream(*args, **kwargs):
        order.append("values_finished")
        return checkpoint

    def authoritative(*args):
        return AuthoritativeSpanReconciler._run_stream(
            reconciler,
            frozen=frozen,
            build=lease,
            role=AuthoritativeSpanRole.VALUES,
            dry_run=False,
        )

    originals = {}
    for name, implementation in {
        "_run_authoritative_span": authoritative,
        "reconcile_non_postgres": lambda *a, **k: {
            "final_span_audit_count": 4,
            "final_span_audit_digest": "c" * 64,
        },
        "_notice_source_part_changes": lambda *a: None,
        "activate": lambda *a, **k: {"activated": True},
    }.items():
        originals[name] = Mock(side_effect=implementation)
        monkeypatch.setattr(Runtime, name, originals[name])
    originals["stream"] = Mock(side_effect=stream)
    monkeypatch.setattr(AuthoritativeSpanReconciler, "_run_stream", originals["stream"])

    def query(_run, sql):
        statements.append(sql)
        if "system.columns" in sql:
            return "attrs_string\n_version\nupdated_at\nid\n"
        if sql.startswith("INSERT"):
            order.append("source_update")
            return ""
        assert "property_catalog_source_streams" in sql
        return json.dumps({"status": "open", "lease_sha256": "a" * 64})

    monkeypatch.setattr(smoke, "query", Mock(side_effect=query))
    monkeypatch.setattr(smoke, "latest", Mock(side_effect=[before, after]))
    repair = Mock(return_value={"request": None, "ack": None})
    monkeypatch.setattr(smoke, "repair_state", repair)
    trigger = Mock()
    monkeypatch.setattr(smoke, "trigger_repair", trigger)
    retirement = Mock(return_value={"fixture_unit_test": "cleanup observed"})
    monkeypatch.setattr(smoke, "capture_retirement", retirement)
    smoke.install_fault(run)
    return NS(
        run=run,
        fixture=fixture,
        frozen=frozen,
        execution=execution,
        runtime=runtime,
        reconciler=reconciler,
        checkpoint=checkpoint,
        reader=reader,
        before=before,
        after=after,
        repair=repair,
        originals=originals,
        order=order,
        statements=statements,
        trigger=trigger,
        retirement=retirement,
    )


def load(lane, name):
    return json.loads((lane.run.directory / f"source-change-{name}.json").read_text())


def inject(lane):
    return Runtime._run_authoritative_span(lane.runtime, lane.execution)


def notice(lane):
    return {
        "format": "futureagi.property-catalog-source-repair",
        "version": 1,
        "generation": 1,
        "organization_id": uid(3),
        "workspace_id": uid(2),
        "source_database": "default",
        "source_table": "spans",
        "first_seen_us": lane.before["seen_us"],
        "last_seen_us": lane.before["seen_us"],
        "observed_at_us": lane.before["seen_us"] + 1000,
    }


def initial(lane):
    inject(lane)
    Runtime.reconcile_non_postgres(lane.runtime)
    lane.repair.return_value = {"request": notice(lane), "ack": None}
    Runtime._notice_source_part_changes(lane.runtime, lane.execution)
    smoke.publish(
        lane.run.directory / "source-change-initial-api.json",
        {"build_token": uid(4), "revision": 9, "epoch": 1, "value": "initial"},
    )
    Runtime.activate(lane.runtime)
    return load(lane, "initial-activated")


def replacement(lane):
    lane.runtime._source_repair = NS(raw=json.dumps(notice(lane)).encode())
    lane.execution.lease.build_token = uid(10)
    lane.execution.lease.catalog_revision = 10
    spec = replace(lane.execution.capture_binding.spec, build_token=uid(10))
    lane.execution.capture_binding.spec = spec
    lane.reader._source_table = lambda: (
        f"`{spec.capture_database}`.`{spec.capture_table}`"
    )
    lane.reader._query.return_value = [{**lane.after, "server_uuid": uid(6)}]
    for checkpoint in (
        lane.execution.authoritative.values,
        lane.execution.authoritative.source_audit,
    ):
        checkpoint.build_token, checkpoint.catalog_revision = uid(10), 10
    lane.execution.activation.record = NS(
        **vars(lane.execution.lease), activation_sha256="e" * 64
    )


def test_real_calls_order_single_source_write_and_stable_capture(lane):
    assert inject(lane) is lane.checkpoint
    assert inject(lane) is lane.checkpoint
    assert lane.order == ["values_finished", "source_update", "values_finished"]
    writes = [sql for sql in lane.statements if sql.startswith("INSERT")]
    assert len(writes) == 1
    assert "INSERT INTO default.spans" in writes[0] and "LIMIT 1" in writes[0]
    assert "name='otlp_live'" in writes[0]
    injected = load(lane, "injected")
    assert injected["capture_before"] == injected["capture_after"]
    assert injected["capture"]["spec"]["build_token"] == uid(4)
    assert injected["no_notification_for_mutation"] is True
    lane.trigger.assert_not_called()


@pytest.mark.parametrize("remaining", [-1, 0, 181, float("inf"), float("nan")])
def test_arm_rejects_expired_or_unbounded_correctness_wait_before_mutation(
    lane, monkeypatch, remaining
):
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 1000)
    path = lane.run.directory / "source-change-arm.json"
    request = json.loads(path.read_text())
    request["deadline"] = 1000 + remaining
    path.write_text(json.dumps(request))
    with pytest.raises(RuntimeError, match="arm scope/deadline"):
        inject(lane)
    assert not lane.statements
    assert not (lane.run.directory / "source-change-claimed.json").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("terminal", False),
        ("source_digest", "bad"),
        ("build_token", uid(99)),
        ("gap_count", 1),
    ],
)
def test_incomplete_or_wrong_checkpoint_claims_but_never_mutates(lane, field, value):
    setattr(lane.checkpoint, field, value)
    with pytest.raises(RuntimeError):
        inject(lane)
    inject(lane)
    assert not lane.statements
    assert (lane.run.directory / "source-change-claimed.json").exists()


def test_unrelated_scope_unarmed_and_dry_run_do_not_inject(lane):
    lane.frozen.project_ids = (uid(99),)
    inject(lane)
    lane.frozen.project_ids = (uid(1),)
    AuthoritativeSpanReconciler._run_stream(
        lane.reconciler,
        frozen=lane.frozen,
        build=lane.execution.lease,
        role=AuthoritativeSpanRole.VALUES,
        dry_run=True,
    )
    (lane.run.directory / "source-change-arm.json").unlink()
    inject(lane)
    assert not lane.statements


@pytest.mark.parametrize(
    "method",
    ["stream", "reconcile_non_postgres", "activate", "_notice_source_part_changes"],
)
def test_original_errors_propagate(lane, method):
    lane.originals[method].side_effect = RuntimeError("original proof failed")
    with pytest.raises(RuntimeError, match="original proof failed"):
        if method == "stream":
            inject(lane)
        elif method == "_notice_source_part_changes":
            Runtime._notice_source_part_changes(lane.runtime, lane.execution)
        else:
            getattr(Runtime, method)(lane.runtime)


@pytest.mark.parametrize("fault", ["scope", "missing", "reader", "node", "changed"])
def test_wrong_capture_rejected(lane, fault):
    if fault == "scope":
        lane.execution.capture_binding.spec = replace(
            lane.execution.capture_binding.spec, workspace_id=uid(99)
        )
    elif fault == "missing":
        lane.execution.capture_binding = None
    elif fault == "reader":
        lane.reconciler._reader = object()
    elif fault == "node":
        lane.reader._query.return_value[0]["server_uuid"] = uid(99)
    else:
        lane.reader.parts_snapshot.side_effect = [
            (("part", "before"),),
            (("part", "after"),),
        ]
    with pytest.raises(RuntimeError):
        inject(lane)
    assert not (lane.run.directory / "source-change-injected.json").exists()


def test_refuses_unowned_run(lane):
    lane.run.owned.return_value = []
    with pytest.raises(RuntimeError, match="owned local"):
        smoke.install_fault(lane.run)


@pytest.mark.parametrize("fault", ["digest", "same_stream", "final"])
def test_real_audits_must_agree(lane, fault):
    inject(lane)
    if fault == "digest":
        lane.execution.authoritative.source_audit.source_digest = "f" * 64
    elif fault == "same_stream":
        lane.execution.authoritative.source_audit.producer_stream_id = (
            lane.checkpoint.producer_stream_id
        )
    else:
        lane.originals["reconcile_non_postgres"].side_effect = lambda *a: {
            "final_span_audit_count": 5,
            "final_span_audit_digest": "c" * 64,
        }
    with pytest.raises(RuntimeError, match="proofs disagree"):
        Runtime.reconcile_non_postgres(lane.runtime)


def test_positive_both_real_audits_activation_and_exact_ack(lane):
    stable = initial(lane)
    assert stable["repair"]["request"] != stable["repair"]["ack"]
    replacement(lane)
    Runtime.reconcile_non_postgres(lane.runtime)
    lane.repair.return_value = {"request": notice(lane), "ack": notice(lane)}
    Runtime.activate(lane.runtime)
    repaired = load(lane, "repaired")
    assert repaired["claimed_repair"] == stable["repair"]["request"]
    assert repaired["capture"]["table_uuid"] != stable["capture"]["table_uuid"]
    assert lane.originals["activate"].call_count == 2
    assert lane.retirement.call_count == 2
    assert repaired["capture_retirement"] == lane.retirement.return_value


def test_cleanup_checked_after_real_activation_before_evidence_or_rendezvous(lane):
    def reject(run, fixture, execution, capture):
        lane.originals["activate"].assert_called_once()
        assert capture == load(lane, "initial-audit")["capture"]
        assert not (run.directory / "source-change-initial-activated.json").exists()
        raise RuntimeError("cleanup not proved")

    lane.retirement.side_effect = reject
    with pytest.raises(RuntimeError, match="cleanup not proved"):
        initial(lane)
    assert not (lane.run.directory / "source-change-initial-activated.json").exists()


@pytest.mark.parametrize("fault", ["acked", "stale", "uncovered"])
def test_new_notice_must_cover_mutation_and_remain_pending(lane, fault):
    inject(lane)
    request = notice(lane)
    state = {"request": request, "ack": None}
    if fault == "acked":
        state["ack"] = request
    elif fault == "stale":
        request["generation"] = 0
    else:
        request["last_seen_us"] -= 1
    lane.repair.return_value = state
    with pytest.raises(RuntimeError, match="new unacknowledged"):
        Runtime._notice_source_part_changes(lane.runtime, lane.execution)


@pytest.mark.parametrize(
    "fault", ["unclaimed", "other_notice", "incremental", "resumed", "wrong_value"]
)
def test_repair_requires_exact_notice_fresh_capture_full_build(lane, fault):
    initial(lane)
    replacement(lane)
    if fault == "unclaimed":
        lane.runtime._source_repair = None
    elif fault == "other_notice":
        lane.runtime._source_repair.raw = json.dumps({**notice(lane), "generation": 2})
    elif fault == "incremental":
        lane.execution.prepared.mode = LifecycleRunMode.INCREMENTAL
    elif fault == "resumed":
        lane.execution.prepared.resumed = True
    else:
        lane.reader._query.return_value[0]["value"] = "initial"
    with pytest.raises(RuntimeError, match="exact new notice/capture"):
        Runtime.reconcile_non_postgres(lane.runtime)


def test_missing_exact_repair_ack_rejects_completion(lane):
    initial(lane)
    replacement(lane)
    Runtime.reconcile_non_postgres(lane.runtime)
    with pytest.raises(RuntimeError, match="acknowledge its exact"):
        Runtime.activate(lane.runtime)
    assert not (lane.run.directory / "source-change-repaired.json").exists()


def api(value, revision=9):
    return {
        "values": [{"value": value}],
        "catalog_revision": revision,
        "catalog_epoch": 1,
    }, 0


def test_verifier_requires_initial_exact_api_then_actual_repair(lane, monkeypatch):
    stable = initial(lane)
    (lane.run.directory / "source-change-initial-api.json").unlink()
    reads = 0

    def get(endpoint, params):
        nonlocal reads
        if params["property_id"] == "custom_attribute:plan":
            return api("Pro")
        reads += 1
        if reads <= 2:
            return api("initial", 8 if reads == 1 else 9)
        assert load(lane, "initial-api")["build_token"] == stable["build_token"]
        replacement(lane)
        Runtime.reconcile_non_postgres(lane.runtime)
        lane.repair.return_value = {"request": notice(lane), "ack": notice(lane)}
        Runtime.activate(lane.runtime)
        return api("midscan_updated", 10)

    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    result = smoke.verify(lane.run, lane.fixture, object(), get)
    assert result["status"] == "passed"
    assert result["stable_capture_api"]["revision"] == 9
    assert result["repair_activation"]["revision"] == 10
    lane.trigger.assert_called_once()


def test_fresh_values_alone_cannot_pass(lane):
    get = Mock(side_effect=[api("initial", 8), api("midscan_updated", 10), api("Pro")])
    with pytest.raises(RuntimeError, match="preceded stable captured API proof"):
        smoke.verify(lane.run, lane.fixture, object(), get)
    assert (
        json.loads((lane.run.directory / "catalog-source-change.json").read_text())[
            "status"
        ]
        == "failed"
    )


def test_api_failure_is_never_hidden_by_retry(lane):
    get = Mock(side_effect=[api("initial"), RuntimeError("HTTP 503")])
    with pytest.raises(RuntimeError, match="HTTP 503"):
        smoke.verify(lane.run, lane.fixture, object(), get)
    assert get.call_count == 2


def test_real_repair_parser_readonly_and_corruption(tmp_path):
    from tracer.services.clickhouse.v2.property_catalog.source_repair import (
        record_source_repair,
    )

    fixture = {"organization_id": uid(3), "workspace_id": uid(2)}
    runtime = tmp_path / "application-runtime"
    runtime.mkdir()
    seen = datetime.fromisoformat("2026-09-06T00:00:00+00:00")
    record_source_repair(
        state_directory=str(runtime),
        **fixture,
        source_database="default",
        seen_at=seen,
        observed_at=seen + timedelta(seconds=1),
    )
    run = NS(directory=tmp_path)
    paths = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    state = smoke.repair_state(run, fixture)
    assert state["request"]["generation"] == 1 and state["ack"] is None
    assert paths == {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    path = next(p for p in paths if p.suffix == ".json")
    path.write_text("{}")
    with pytest.raises(ValueError, match="noncanonical"):
        smoke.repair_state(run, fixture)


def test_application_keeps_post_repair_read_and_bounded_correctness_wait():
    source = (Path(__file__).parent / "application_smoke.py").read_text()
    stage = source[
        source.index('[python, script, "source-change"') : source.index(
            '[python, script, "old-version"'
        )
    ]
    assert "timeout=240" in stage
    assert '[python, script, "read", str(run.directory)], timeout=60' in stage
    assert '"application-api-after-source-change.json"' in stage


@pytest.fixture
def retired_capture(tmp_path):
    # Real durable journal/index, with explicit fake DDL only in unit setup.
    from tracer.tests.test_property_catalog_source_capture import Backend

    run = NS(directory=tmp_path, manifest={"run_id": "0123456789abcdef"})
    fixture = {"organization_id": uid(3), "workspace_id": uid(2)}
    spec = SourceCaptureSpec(
        uid(5),
        uid(3),
        uid(2),
        uid(4),
        uid(6),
        "default",
        "property_catalog_dev_app_" + run.manifest["run_id"],
        uid(7),
        "a" * 64,
        "b" * 64,
    )
    directory = tmp_path / "application-runtime"
    directory.mkdir(mode=0o700)
    backend = Backend()
    manager = DurableSourceCapture(str(directory), backend, can_retire=lambda _: True)
    backend.manager = manager
    manager.acquire(spec)
    index = manager._journal.load_record(manager._reservations._key(spec))
    manager.retire(spec)
    driver = NS(
        database=spec.capture_database,
        server_enforced_readonly=True,
        execute_read=Mock(
            return_value=(
                [(spec.source_server_uuid, 0)],
                [("server_uuid", "String"), ("tables", "UInt64")],
                0,
            )
        ),
    )
    reader = NS(
        _client=NativeSourceClient(
            driver,
            source_database=spec.capture_database,
            source_table=spec.capture_table,
            catalog_database=spec.catalog_database,
        ),
        _source_table=lambda: f"`{spec.capture_database}`.`{spec.capture_table}`",
        _deadline=NS(remaining_ms=Mock(return_value=1500)),
    )
    execution = NS(
        lease=NS(build_token=spec.build_token),
        capture_binding=NS(spec=spec, reader=reader),
    )
    capture = smoke.capture_description(run, fixture, execution)
    return NS(
        run=run,
        fixture=fixture,
        spec=spec,
        manager=manager,
        backend=backend,
        directory=directory,
        driver=driver,
        reader=reader,
        execution=execution,
        capture=capture,
        index=index,
    )


def assert_retirement(case):
    return smoke.capture_retirement(
        case.run, case.fixture, case.execution, case.capture
    )


def test_cleanup_uses_exact_pinned_readonly_source_and_durable_retirement(
    retired_capture,
):
    case = retired_capture
    files = {p: p.read_bytes() for p in case.directory.rglob("*") if p.is_file()}
    events = tuple(case.backend.events)
    result = assert_retirement(case)
    assert result == {
        "source_server_uuid": case.spec.source_server_uuid,
        "table": case.capture["table"],
        "table_uuid": case.spec.capture_table_uuid,
        "table_absent": True,
        "journal_phase": "retired",
        "reservation_released": True,
    }
    args, kwargs = case.driver.execute_read.call_args
    assert args == (
        "SELECT toString(serverUUID()) AS server_uuid, count() AS tables "
        "FROM system.tables WHERE database=%(database)s AND name=%(table)s",
        {"database": case.spec.capture_database, "table": case.spec.capture_table},
    )
    assert kwargs["timeout_ms"] == 1500
    assert kwargs["settings"]["readonly"] == 2
    assert kwargs["settings"]["max_result_rows"] == 1
    assert kwargs["settings"]["max_result_bytes"] == 4096
    case.reader._deadline.remaining_ms.assert_called_once_with(cap_ms=2000)
    assert tuple(case.backend.events) == events
    assert files == {
        p: p.read_bytes() for p in case.directory.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [(uid(99), 0)],
        [(uid(6), 1)],
        [(uid(6), False)],
        [(uid(6), "0")],
        [(uid(6), 0), (uid(6), 0)],
    ],
)
def test_cleanup_absence_requires_exact_member_and_native_result(retired_capture, rows):
    retired_capture.driver.execute_read.return_value = (
        rows,
        ["server_uuid", "tables"],
        0,
    )
    with pytest.raises(RuntimeError, match="exact source member"):
        assert_retirement(retired_capture)


@pytest.mark.parametrize("fault", ["missing", "not_retired", "wrong_spec", "index"])
def test_cleanup_requires_exact_retired_journal_and_unreleased_index_rejects(
    retired_capture, fault
):
    case = retired_capture
    key = case.manager._key(case.spec)
    if fault == "missing":
        case.manager._journal._path(key).unlink()
    elif fault == "not_retired":
        case.manager._save(case.spec, "retiring")
    elif fault == "wrong_spec":
        state = case.manager._load(case.spec)
        state["spec"]["source_table_uuid"] = uid(99)
        case.manager._journal.save_record(key, state)
    else:
        case.manager._journal.save_record(
            case.manager._reservations._key(case.spec), case.index
        )
        # The public inventory intentionally filters this leftover entry.
        assert case.manager.reservation_snapshot(case.spec) == ()
    with pytest.raises((RuntimeError, SourceCaptureError)):
        assert_retirement(case)


def test_cleanup_does_not_require_other_build_reservations_to_be_empty(retired_capture):
    case = retired_capture
    other = replace(case.spec, build_token=uid(10))
    case.manager.acquire(other)
    assert assert_retirement(case)["reservation_released"] is True
    assert case.manager.reservation_snapshot(other)[0].spec == other


def test_cleanup_rejects_changed_recorded_spec_before_any_read(retired_capture):
    case = retired_capture
    case.capture["table_uuid"] = uid(99)
    with pytest.raises(RuntimeError, match="exact recorded capture"):
        assert_retirement(case)
    case.driver.execute_read.assert_not_called()
