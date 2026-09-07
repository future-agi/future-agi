"""Runtime ordering and reader preparation; not transport or browser E2E proof."""

import fcntl
import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    PropertyCatalogDevRuntimeError,
    PropertyCatalogDevRuntimeFactory,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    LifecycleRunMode,
)
from tracer.tests.test_property_catalog_fenced_scope_recovery import RecoveryHarness
from tracer.tests.test_property_catalog_managed_runtime_activation import (
    publication_runtime,
)


@pytest.mark.parametrize("prior_active", [False, True])
def test_recovery_precedes_fenced_resume_selection_and_allocation(
    tmp_path, monkeypatch, prior_active
):
    h = RecoveryHarness(tmp_path)
    steps = []
    repairs = ((object(), object()),)

    def recover(self, scope):
        assert self is h.runtime
        assert scope == h.scope
        steps.append("native_recovery")
        return repairs

    monkeypatch.setattr(type(h.runtime), "_recover_native_writes", recover)
    monkeypatch.setattr(
        type(h.runtime),
        "_recover_fenced_scope_drift",
        lambda *_: steps.append("fenced") or False,
    )
    monkeypatch.setattr(
        type(h.runtime), "reconcile_reader_selection", lambda _: steps.append("reader")
    )
    monkeypatch.setattr(
        h.base.reader,
        "load_latest_active",
        lambda _: object() if prior_active else None,
    )

    class ReachedAllocation(Exception):
        pass

    def allocate(**kwargs):
        assert h.runtime._native_repairs == repairs
        assert steps == ["native_recovery", "fenced", "reader"]
        assert kwargs["mode"] is (
            LifecycleRunMode.FULL_REPAIR
            if prior_active
            else LifecycleRunMode.INITIAL_BACKFILL
        )
        raise ReachedAllocation

    monkeypatch.setattr(h.runtime.lifecycle, "prepare", allocate)
    with pytest.raises(ReachedAllocation):
        h.runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert h.runtime._execution is None


def test_pending_native_repair_blocks_reader_and_new_allocation(tmp_path, monkeypatch):
    h = RecoveryHarness(tmp_path)

    def pending(*_):
        raise RuntimeError("terminal write remains pending")

    monkeypatch.setattr(type(h.runtime), "_recover_native_writes", pending)
    monkeypatch.setattr(
        type(h.runtime),
        "_recover_fenced_scope_drift",
        lambda *_: pytest.fail("fenced resume before native recovery"),
    )
    monkeypatch.setattr(
        type(h.runtime),
        "reconcile_reader_selection",
        lambda _: pytest.fail("reader selection before native recovery"),
    )
    monkeypatch.setattr(
        h.runtime.lifecycle,
        "prepare",
        lambda **_: pytest.fail("allocated around uncertain repair"),
    )
    with pytest.raises(RuntimeError, match="terminal write remains pending"):
        h.runtime._prepare_revision(LifecycleRunMode.AUTO)
    assert h.runtime._execution is None


@pytest.mark.parametrize("has_publication", [False, True])
def test_replacement_initial_preserves_history_until_postpublication(
    tmp_path, monkeypatch, has_publication
):
    runtime, _, client, steps = publication_runtime(monkeypatch, tmp_path)
    # This test checks runtime routing only. Receipt validation is owned by the
    # real terminal executor and the reader's separate proof-consumer tests.
    runtime._native_repairs = (
        (
            SimpleNamespace(publication_document={} if has_publication else None),
            object(),
        ),
    )
    runtime.reconcile_reader_selection(prepare_initial=True)
    assert steps == ([] if has_publication else ["prepare_follow"])
    assert len(client.events) == (0 if has_publication else 1)


def test_repair_routing_does_not_bypass_fence_qualification(tmp_path, monkeypatch):
    runtime, execution, client, steps = publication_runtime(monkeypatch, tmp_path)
    runtime._native_repairs = ((SimpleNamespace(publication_document={}), object()),)
    execution.qualification.qualified = False
    with pytest.raises(PropertyCatalogDevRuntimeError):
        runtime.reconcile_reader_selection(prepare_initial=True)
    assert not steps and not client.events


@pytest.fixture
def factory_repair(tmp_path, monkeypatch):
    from tracer.services.clickhouse.v2.property_catalog import reader_activation
    from tracer.services.clickhouse.v2.property_catalog.activation_control import (
        ActivationControlScope,
    )
    from tracer.services.clickhouse.v2.property_catalog.publisher import (
        SharedCatalogDeadline,
    )
    from tracer.services.clickhouse.v2.property_catalog.terminal_repair_execution import (
        NativeTerminalRepairExecutor,
    )
    from tracer.tests.test_property_catalog_dev_rollout import (
        _request,
        _unit_runtime_config,
    )
    from tracer.tests.test_property_catalog_terminal_repair_pair import PairHarness

    pair = PairHarness(tmp_path, monkeypatch)
    # An earlier recovery produced this exact pair. Factory selection must not
    # substitute this cached result for a new all-member proof.
    previous = pair.executor.apply(pair.intent, timeout_ms=5000)
    pair.transport.reset_mock()
    pair.coverage_reads.clear()
    config = _unit_runtime_config(str(tmp_path))
    config = replace(
        config,
        catalog=replace(config.catalog, database=pair.writer.database),
        catalog_epoch=pair.identity.catalog_epoch,
        projection_version=pair.identity.projection_version,
    )
    scope = ActivationControlScope(pair.scope.organization_id, pair.scope.workspace_id)
    state = SimpleNamespace(
        pair=pair,
        previous=previous,
        drivers=[],
        proofs=[],
        selections=[],
        steps=[],
        selection_failure=None,
    )

    def workspace_free():
        filename = hashlib.sha256(pair.workspace_key.encode()).hexdigest() + ".lock"
        with (pair.harness.base.directory / filename).open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def driver_factory(connection):
        assert (connection.host, connection.port) == (
            pair.writer.driver.host,
            pair.writer.driver.port,
        )
        driver = SimpleNamespace(
            database=connection.database,
            user=connection.user,
            host=connection.host,
            port=connection.port,
            server_enforced_readonly=False,
            closed=0,
        )

        def close():
            workspace_free()
            driver.closed += 1
            state.steps.append("close")

        driver.close = close
        state.drivers.append(driver)
        return driver

    class Guarded:
        # Control transport is deliberately not exercised here: factory routing
        # ends at selection. Terminal proof, writer and journals below are real.
        def __init__(self, driver, **kwargs):
            assert kwargs["durable_writer"].driver is driver
            assert kwargs["durable_writer"].proof is pair.writer.proof

    class Automatic:
        def __init__(self, client, **kwargs):
            self.options = kwargs
            self.control = reader_activation.FileActivationControlCoordinator(
                kwargs["state_directory"], database=kwargs["database"], deployment="dev"
            )

        def reconcile(self, selected_scope, *, terminal_repairs=()):
            assert selected_scope == scope
            if terminal_repairs:
                pair.assert_workspace_locked()
            else:
                workspace_free()

            def select():
                if terminal_repairs:
                    pair.assert_workspace_locked()
                # Acquiring this lock proves fresh terminal confirmation has
                # released BUILD before the inner reader-control operation.
                with pair.writer._journal() as journal, journal.scope(pair.scope):
                    assert self.options["authorize_scope"](selected_scope)
                state.selections.append(terminal_repairs)
                state.steps.append("select")
                if state.selection_failure is not None:
                    raise state.selection_failure
                return "selected"

            return self.control.serialize(selected_scope, select)

    original = NativeTerminalRepairExecutor._serialized

    def prove(executor, intent, key, remaining, *, allow_dispatch):
        assert (
            executor.writer is pair.writer and executor.coordinator is pair.coordinator
        )
        pair.assert_workspace_locked()
        assert key == pair.workspace_key and allow_dispatch is False
        assert intent == pair.intent and intent is not pair.intent
        state.steps.append("prove")
        state.proofs.append((intent, key, allow_dispatch))
        return original(executor, intent, key, remaining, allow_dispatch=allow_dispatch)

    monkeypatch.setattr(NativeTerminalRepairExecutor, "_serialized", prove)
    monkeypatch.setattr(reader_activation, "ReaderActivationClient", Guarded)
    monkeypatch.setattr(reader_activation, "AutomaticReaderActivation", Automatic)
    state.runtime = SimpleNamespace(
        config=config,
        bound_request=_request(
            execute=True,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        ),
        catalog_client=SimpleNamespace(_durable_writer=pair.writer),
        coordinator=pair.coordinator,
        deadline=SharedCatalogDeadline(wall_ms=30_000),
        _native_repairs=((pair.intent, previous),),
        _refresh_project_tenant_authorization=lambda: state.steps.append("authorize"),
    )
    state.factory = PropertyCatalogDevRuntimeFactory(
        settings_object=SimpleNamespace(),
        native_client_factory=driver_factory,
    )
    state.select = lambda: state.factory._reconcile_reader_activation(state.runtime)
    state.workspace_free = workspace_free
    yield state
    pair.transport.assert_not_called()
    pair.assert_original_unchanged()
    assert all(driver.closed == 1 for driver in state.drivers)
    workspace_free()


def test_factory_reproves_each_current_pair_once_before_locked_selection(
    factory_repair,
):
    h = factory_repair
    for call in range(1, 3):
        before = len(h.pair.coverage_reads)
        assert h.select() == "selected"
        assert len(h.proofs) == len(h.selections) == call
        intent, receipt = h.selections[-1][0]
        assert intent == h.pair.intent and intent is not h.pair.intent
        assert receipt == h.previous and receipt is not h.previous
        # Both terminal rows are freshly covered on both native members; the
        # executor's final pair-wide pass is retained as well.
        reads = h.pair.coverage_reads[before:]
        assert len(reads) == 8
        assert {(member, table) for member, table, _ in reads} == {
            (member.name, write.table)
            for member in h.pair.admission.members
            for write in h.pair.intent.binding.writes
        }
        assert h.runtime._native_repairs == ((h.pair.intent, h.previous),)
    assert h.steps == ["prove", "authorize", "select", "close"] * 2


@pytest.mark.parametrize("mismatch", ["receipt", "workspace", "admission"])
def test_factory_repair_mismatch_blocks_selector_without_sql(factory_repair, mismatch):
    from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
        NativeWriteUnresolved,
    )

    h = factory_repair
    if mismatch == "receipt":
        changed = replace(h.previous, attempt_record_sha256s=("e" * 64, "f" * 64))
        h.runtime._native_repairs = ((h.pair.intent, changed),)
        expected = "reader repair receipt changed"
    elif mismatch == "workspace":
        h.runtime.bound_request = replace(
            h.runtime.bound_request, workspace_id="99999999-9999-4999-8999-999999999999"
        )
        expected = "reader repair crossed workspace"
    else:
        h.pair.writer.admission_sha256 = "f" * 64
        expected = "native recovery intent differs"
    with pytest.raises(
        (PropertyCatalogDevRuntimeError, NativeWriteUnresolved), match=expected
    ):
        h.select()
    assert not h.selections
    assert len(h.proofs) == int(mismatch == "receipt")
    assert h.steps[-1] == "close"


def test_cached_recovery_pair_cannot_hide_current_member_coverage_loss(factory_repair):
    from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
        NativeWriteProofError,
    )
    from tracer.tests.test_property_catalog_terminal_repair_pair import ACTIVE

    h = factory_repair
    h.pair.missing_coverage.add((ACTIVE, h.pair.coverage_rounds[ACTIVE] + 1))
    with pytest.raises(NativeWriteProofError):
        h.select()
    assert len(h.proofs) == 1 and not h.selections
    assert h.runtime._native_repairs == ((h.pair.intent, h.previous),)
    # A subsequent call must prove again, not retain the failed or earlier pass.
    assert h.select() == "selected"
    assert len(h.proofs) == 2 and len(h.selections) == 1


def test_factory_releases_workspace_and_client_on_selector_failure(factory_repair):
    h = factory_repair
    h.selection_failure = RuntimeError("reader selection unresolved")
    with pytest.raises(RuntimeError, match="reader selection unresolved"):
        h.select()
    assert h.steps == ["prove", "authorize", "select", "close"]
    assert len(h.proofs) == len(h.selections) == 1


def test_factory_without_repairs_keeps_normal_unlocked_selection(factory_repair):
    h = factory_repair
    h.runtime._native_repairs = ()
    assert h.select() == "selected"
    assert h.selections == [()] and not h.proofs and not h.pair.coverage_reads
    assert h.steps == ["authorize", "select", "close"]


@pytest.mark.parametrize("failure", [None, "reader", "replacement_completion"])
def test_native_repair_acknowledgement_follows_reader_and_fresh_completion(
    tmp_path, monkeypatch, failure
):
    from tracer.services.clickhouse.v2.property_catalog import (
        accepted_scope_proofs,
        native_publication,
        native_recovery,
    )

    runtime, execution, client, steps = publication_runtime(monkeypatch, tmp_path)
    assert runtime.activate(object())["activated"] is True
    # Exercise the cached ACTIVE retry: no second ACTIVE or initial FOLLOW is
    # sent, but publication completion and reader reconciliation remain required.
    steps.clear()
    attempts = list(client.attempts)
    writer = object()
    runtime.catalog_client = SimpleNamespace(_durable_writer=writer)
    runtime._native_repairs = ((object(), object()),)
    window = SimpleNamespace(
        since=runtime.now(), until=runtime.now() + timedelta(hours=1)
    )
    execution.prepared.cutoffs = SimpleNamespace(span_window=window)
    state = SimpleNamespace(completing=False, rounds=0, acknowledgements=[])

    class Barrier:
        def __init__(self, actual_writer):
            assert actual_writer is writer

        @contextmanager
        def completion(self, record):
            assert record is execution.activation.record and not state.completing
            state.rounds += 1
            steps.append("completion_enter")
            if failure == "replacement_completion" and state.rounds == 2:
                raise RuntimeError("replacement completion unresolved")
            state.completing = True
            try:
                yield
            finally:
                state.completing = False
                steps.append("completion_exit")

    callback = runtime._reader_activation_callback

    def select(current_runtime, **kwargs):
        assert not state.completing
        if failure == "reader":
            steps.append("reconcile_follow")
            raise RuntimeError("reader unresolved")
        return callback(current_runtime, **kwargs)

    def register(**kwargs):
        assert state.completing and state.rounds == 2
        assert kwargs["runtime"] is runtime and kwargs["execution"] is execution
        steps.append("register_active")

    def acknowledge(**kwargs):
        assert state.completing and state.rounds == 2
        assert steps[-2:] == ["completion_enter", "register_active"]
        assert kwargs == {
            "writer": writer,
            "repairs": runtime._native_repairs,
            "record": execution.activation.record,
            "since": window.since,
            "until": window.until,
        }
        state.acknowledgements.append(kwargs)
        steps.append("ack_repair")

    runtime._reader_activation_callback = select
    monkeypatch.setattr(native_publication, "NativePublicationBarrier", Barrier)
    monkeypatch.setattr(accepted_scope_proofs, "register_active_proof", register)
    monkeypatch.setattr(native_recovery, "acknowledge_replacement", acknowledge)
    if failure:
        with pytest.raises(RuntimeError, match="unresolved"):
            runtime.activate(object())
        assert not state.acknowledgements
    else:
        assert runtime.activate(object())["activated"] is True
        assert len(state.acknowledgements) == 1
    prefix = [
        "completion_enter",
        "retire_producer",
        "completion_exit",
        "reconcile_follow",
    ]
    assert steps == prefix + (
        []
        if failure == "reader"
        else ["completion_enter"]
        if failure == "replacement_completion"
        else ["completion_enter", "register_active", "ack_repair", "completion_exit"]
    )
    assert not state.completing and client.attempts == attempts
