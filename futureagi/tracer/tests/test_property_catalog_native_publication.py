"""Real publication/coordinator/native journals; network and inventory are fakes."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import fields, replace
from datetime import timedelta
from types import MethodType, SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation import (
    PropertyCatalogActivator,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_publication import (
    NativeCompletionProof,
    NativePublicationBarrier,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournal,
    NativeWriteJournalBusy,
    NativeWriteJournalError,
    NativeWriteScope,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _COLUMNS,
    NativeWriteProofError,
)
from tracer.tests import test_property_catalog_native_write_proof as proof_fixture
from tracer.tests.test_property_catalog_durable_native_writer import (
    setup as writer_setup,
)
from tracer.tests.test_property_catalog_hot_drain import _CoordinatorClient
from tracer.tests.test_property_catalog_publication_journal import PublicationHarness
from tracer.tests.test_property_catalog_source_capture import specification
from tracer.tests.test_property_catalog_write_admission import DATABASE, installation

ACTIVE = "property_catalog_activations"


def harness(tmp_path, monkeypatch):
    native, lifecycle = tmp_path / "native", tmp_path / "lifecycle"
    native.mkdir(mode=0o700)
    lifecycle.mkdir(mode=0o700)
    monkeypatch.setattr(_CoordinatorClient, "catalog_database", DATABASE)
    h = PublicationHarness(lifecycle)
    monkeypatch.setattr(
        proof_fixture,
        "installation",
        lambda environment: replace(
            installation(environment),
            catalog_epoch=h.lease.catalog_epoch,
            projection_version=h.lease.projection_version,
        ),
    )
    writer, proof, transport, _, options = writer_setup(native, monkeypatch)
    row = options["rows"][0]
    for field in (
        "organization_id",
        "workspace_id",
        "catalog_epoch",
        "catalog_revision",
        "build_token",
        "projection_version",
    ):
        row[field] = getattr(h.lease, field)
    scope = NativeWriteScope.from_rows(
        "property_definition_catalog", (row,), proof.identity
    )
    with NativeWriteJournal(native) as journal, journal.scope(scope, create=True):
        pass
    state = SimpleNamespace(loss=False, dispatch=[], guard=None, on_send=lambda: None)

    def send(*args, **kwargs):
        kwargs["before_send"]()
        state.on_send()
        state.dispatch.append(kwargs["query_id"])
        if f".{ACTIVE} " in kwargs["sql"]:
            rows = [
                dict(zip(_COLUMNS[ACTIVE], values, strict=True))
                for values in kwargs["values"]
            ]
            h.rows.extend(deepcopy(rows))
            h.sent.append(kwargs["query_id"])
        if state.loss:
            raise TimeoutError("lost full native ACK")
        return len(kwargs["values"])

    transport.side_effect = send

    def insert(client, table, rows, *, write_scope=None, **kwargs):
        assert table == f"`{writer.database}`.`{ACTIVE}`"
        state.guard = write_scope
        return writer.insert(table, rows, write_scope=write_scope, **kwargs)

    h.client.insert = MethodType(insert, h.client)
    barrier = NativePublicationBarrier(writer, timeout_ms=10_000)

    def wire():
        h.activator = PropertyCatalogActivator(
            h.store,
            coordinator=h.base.coordinator,
            completion_probe=barrier.confirm,
            publication_barrier=barrier.publication,
        )

    wire()
    return h, writer, proof, state, scope, options, wire


def test_complete_native_scope_precedes_publication_resolution_and_replay(
    tmp_path, monkeypatch
):
    h, writer, proof, state, scope, _, wire = harness(tmp_path, monkeypatch)
    observed = []
    storage = h.base.coordinator._recovery_journal
    save = storage.save_record

    def checked_save(key, document):
        if document.get("phase") == "resolved":
            assert state.guard._closed is not None
            state.guard._scoped.require_closed(state.guard._closed)
            observed.append("closure-before-resolve")
        return save(key, document)

    monkeypatch.setattr(storage, "save_record", checked_save)
    first = h.activate()
    assert observed == ["closure-before-resolve"]
    assert h.document()["phase"] == "resolved"
    assert len(state.dispatch) == 1 and len(h.rows) == 1
    with pytest.raises(NativeWriteUnresolved, match="closed"):
        state.guard.require(writer, scope)
    h.restart()
    wire()
    assert h.activate().idempotent
    assert len(state.dispatch) == 1  # Positive replay never sends another INSERT.
    assert barrier_confirm(writer, first.record)


def barrier_confirm(writer, record):
    return NativePublicationBarrier(writer).confirm(record)


def capture_spec(writer, record):
    return specification(
        installation_id=writer.proof.identity.producer_stream_id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        build_token=record.build_token,
        catalog_database=writer.database,
    )


def test_completion_yields_capture_proof_without_queries_locks_or_journal_writes(
    tmp_path, monkeypatch
):
    h, writer, proof, state, _, _, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record
    spec = capture_spec(writer, record)
    with NativePublicationBarrier(writer).completion(record) as completion:
        assert type(completion) is NativeCompletionProof
        scoped = completion._scoped
        before = {p.name: p.read_bytes() for p in writer.directory.rglob("*.json")}

        def forbidden(*args, **kwargs):
            pytest.fail("capture proof attempted a query, write, or another lock")

        with monkeypatch.context() as patch:
            for operation in ("attest", "cover", "settled"):
                patch.setattr(proof, operation, forbidden)
            for operation in ("query", "insert", "_journal"):
                patch.setattr(writer, operation, forbidden)
            patch.setattr(scoped, "pending", forbidden)
            patch.setattr(scoped, "session", forbidden)
            patch.setattr(scoped._journal, "scope", forbidden)
            patch.setattr(scoped._journal, "_session", forbidden)
            assert completion.require_capture(spec) is True
            assert completion.require_capture(spec) is True
        assert before == {
            p.name: p.read_bytes() for p in writer.directory.rglob("*.json")
        }
    assert len(state.dispatch) == 1
    with pytest.raises(NativeWriteUnresolved, match="open owning thread"):
        completion.require_capture(spec)


@pytest.mark.parametrize(
    "field",
    [
        "installation_id",
        "organization_id",
        "workspace_id",
        "build_token",
        "catalog_database",
    ],
)
def test_completion_capture_proof_rejects_other_installation_build_or_catalog(
    tmp_path, monkeypatch, field
):
    h, writer, _, _, _, _, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record
    spec = capture_spec(writer, record)
    different = (
        "another_catalog"
        if field == "catalog_database"
        else "ffffffff-ffff-4fff-8fff-ffffffffffff"
    )
    with NativePublicationBarrier(writer).completion(record) as completion:
        with pytest.raises(NativeWriteUnresolved, match="differs from native"):
            completion.require_capture(replace(spec, **{field: different}))
        with pytest.raises(TypeError, match="typed capture spec"):
            completion.require_capture(SimpleNamespace(**spec.__dict__))
        assert completion.require_capture(spec) is True


def test_completion_capture_proof_rejects_other_thread(tmp_path, monkeypatch):
    h, writer, _, _, _, _, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record
    spec = capture_spec(writer, record)
    with NativePublicationBarrier(writer).completion(record) as completion:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(NativeWriteUnresolved, match="open owning thread"):
                pool.submit(completion.require_capture, spec).result(timeout=2)
        assert completion.require_capture(spec) is True


@pytest.mark.parametrize(
    "change", ["generation", "pending", "session", "identity", "database"]
)
def test_completion_capture_proof_rechecks_held_scope(tmp_path, monkeypatch, change):
    h, writer, proof, _, _, _, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record
    spec = capture_spec(writer, record)
    with NativePublicationBarrier(writer).completion(record) as completion:
        scoped = completion._scoped
        with monkeypatch.context() as patch:
            if change in {"generation", "pending"}:
                read_index = scoped._read_index

                def changed_index():
                    index = read_index()
                    if change == "generation":
                        index["generation"] += 1
                    else:
                        index["pending"] = {"unresolved": object()}
                    return index

                patch.setattr(scoped, "_read_index", changed_index)
            elif change == "session":
                patch.setattr(scoped, "_open", False)
            elif change == "identity":
                patch.setattr(
                    proof,
                    "identity",
                    replace(
                        proof.identity,
                        producer_stream_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
                    ),
                )
            else:
                patch.setattr(writer, "database", "another_catalog")
            with pytest.raises((NativeWriteUnresolved, NativeWriteJournalError)):
                completion.require_capture(spec)
        assert completion.require_capture(spec) is True


@pytest.mark.parametrize("failure", ["body", "deadline", "exit_generation"])
def test_completion_capture_proof_closes_on_every_exception(
    tmp_path, monkeypatch, failure
):
    from tracer.services.clickhouse.v2.property_catalog import native_write_proof

    h, writer, _, _, _, _, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record
    spec = capture_spec(writer, record)
    clock = [0.0]
    monkeypatch.setattr(native_write_proof, "monotonic", lambda: clock[0])
    with pytest.raises((RuntimeError, NativeWriteUnresolved, NativeWriteProofError)):
        with NativePublicationBarrier(writer, timeout_ms=1000).completion(
            record
        ) as completion:
            assert completion.require_capture(spec) is True
            if failure == "body":
                raise RuntimeError("retirement failed")
            if failure == "deadline":
                clock[0] = 2.0
                completion.require_capture(spec)
                pytest.fail("expired capture proof was accepted")
            else:
                scoped = completion._scoped
                read_index = scoped._read_index

                def changed_index():
                    return {**read_index(), "generation": completion._generation + 1}

                monkeypatch.setattr(scoped, "_read_index", changed_index)
    with pytest.raises(NativeWriteUnresolved, match="open owning thread"):
        completion.require_capture(spec)


def test_completion_proof_cannot_be_minted_without_barrier():
    with pytest.raises(TypeError, match="requires the native completion barrier"):
        NativeCompletionProof(None, None, None, 0, None, _authority=object())


def recovery_candidates(writer, scope):
    with NativeWriteJournal(writer.directory) as journal:
        return journal.recovery_scopes(
            **{
                field: getattr(scope, field)
                for field in (
                    "organization_id",
                    "workspace_id",
                    "catalog_epoch",
                    "projection_version",
                )
            }
        )


def test_publication_tracks_scope_before_dependency_proof_or_active_insert(
    tmp_path, monkeypatch
):
    h, writer, proof, state, scope, _, _ = harness(tmp_path, monkeypatch)
    assert recovery_candidates(writer, scope) == ()

    def fail(scoped, **kwargs):
        assert recovery_candidates(writer, scope) == (scope,)
        assert scoped.pending() == ()
        assert not state.dispatch
        raise NativeWriteProofError("dependency proof unavailable")

    monkeypatch.setattr(writer, "_recover_scoped", fail)
    with pytest.raises(NativeWriteProofError, match="dependency proof unavailable"):
        h.activate()
    assert recovery_candidates(writer, scope) == (scope,)
    assert h.document()["phase"] == "armed"
    assert not state.dispatch


@pytest.mark.parametrize("entry", ["publication", "completion"])
def test_barrier_pointer_fsync_failure_precedes_all_native_proof(
    tmp_path, monkeypatch, entry
):
    h, writer, proof, state, scope, _, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record if entry == "completion" else None
    sent = tuple(state.dispatch)
    for operation in (proof.attest, proof.cover, proof.settled):
        operation.reset_mock()
    monkeypatch.setattr(
        NativeWriteJournal,
        "track_recovery_scope",
        lambda *a: (_ for _ in ()).throw(OSError("recovery pointer fsync")),
    )
    with pytest.raises(OSError, match="recovery pointer fsync"):
        if record is None:
            h.activate()
        else:
            NativePublicationBarrier(writer).confirm(record)
    for operation in (proof.attest, proof.cover, proof.settled):
        operation.assert_not_called()
    assert tuple(state.dispatch) == sent


def test_pending_dependency_blocks_active_even_with_complete_checkpoint_view(
    tmp_path, monkeypatch
):
    h, writer, proof, state, scope, options, _ = harness(tmp_path, monkeypatch)
    state.loss = True
    with pytest.raises(TimeoutError):
        writer.insert(**options)
    state.loss = False
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        h.activate()
    assert not h.rows and h.document()["phase"] == "armed"
    assert len(state.dispatch) == 1  # Only the uncertain dependency was sent.
    proof.settled.return_value = True
    h.activate()
    assert len(state.dispatch) == 2 and len(h.rows) == 1
    with (
        NativeWriteJournal(writer.directory) as journal,
        journal.scope(scope) as scoped,
    ):
        assert scoped.pending() == ()


def test_visible_active_after_lost_ack_cannot_resolve_until_original_settlement(
    tmp_path, monkeypatch
):
    h, writer, proof, state, _, _, wire = harness(tmp_path, monkeypatch)
    state.loss = True
    with pytest.raises(TimeoutError):
        h.activate()
    assert len(h.rows) == 1 and h.document()["phase"] == "armed"
    original = deepcopy(h.rows)
    h.restart()
    wire()
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        h.activate()
    assert h.document()["phase"] == "armed"
    proof.settled.return_value = True
    result = h.activate()
    assert result.idempotent and h.document()["phase"] == "resolved"
    assert h.rows == original and len(state.dispatch) == 1


def test_scope_registration_cannot_race_active_dispatch(tmp_path, monkeypatch):
    h, writer, proof, state, scope, _, _ = harness(tmp_path, monkeypatch)
    observed = []

    def race():
        with NativeWriteJournal(writer.directory) as other:
            with pytest.raises(NativeWriteJournalBusy):
                with other.scope(scope):
                    pytest.fail("concurrent scope entered during ACTIVE")
        observed.append("blocked")

    state.on_send = race
    h.activate()
    assert observed == ["blocked"]


def test_unconfirmed_replica_coverage_keeps_publication_armed(tmp_path, monkeypatch):
    h, writer, proof, state, _, _, wire = harness(tmp_path, monkeypatch)
    proof.cover.side_effect = RuntimeError("replica lost row")
    with pytest.raises(RuntimeError, match="replica lost row"):
        h.activate()
    assert len(h.rows) == 1 and h.document()["phase"] == "armed"
    proof.cover.side_effect = None
    h.restart()
    wire()
    h.activate()
    assert h.document()["phase"] == "resolved" and len(state.dispatch) == 1


def test_prepared_dependency_is_not_dispatched_by_recovery_or_activation(
    tmp_path, monkeypatch
):
    h, writer, proof, state, scope, options, _ = harness(tmp_path, monkeypatch)
    proof.attest.side_effect = RuntimeError("no admission")
    with pytest.raises(RuntimeError, match="no admission"):
        writer.insert(**options)
    proof.attest.side_effect = None
    for operation in (lambda: writer.recover_scope(scope, timeout_ms=5000), h.activate):
        with pytest.raises(NativeWriteUnresolved, match="current write authorization"):
            operation()
    assert state.dispatch == [] and h.rows == []


def test_frozen_prepared_active_resumes_only_through_current_publication(
    tmp_path, monkeypatch
):
    h, writer, proof, state, _, _, wire = harness(tmp_path, monkeypatch)
    original_insert = h.client.insert
    injected = []

    def before_dispatch(client, *args, **kwargs):
        proof.attest.side_effect = RuntimeError("no admission before dispatch")
        injected.append(kwargs["rows"] if "rows" in kwargs else args[1])
        return original_insert(*args, **kwargs)

    h.client.insert = MethodType(before_dispatch, h.client)
    with pytest.raises(RuntimeError, match="before dispatch"):
        h.activate()
    assert state.dispatch == [] and h.document()["phase"] == "armed"
    h.client.insert = original_insert
    proof.attest.side_effect = None
    h.restart()
    wire()
    h.activate()
    assert len(state.dispatch) == 1 and len(injected) == 1
    assert h.rows[0] == injected[0][0]


def test_new_dependency_after_closure_must_settle_before_positive_active_replay(
    tmp_path, monkeypatch
):
    h, writer, proof, state, _, options, wire = harness(tmp_path, monkeypatch)
    h.activate()
    state.loss = True
    with pytest.raises(TimeoutError):
        writer.insert(**options)
    h.restart()
    wire()
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        h.activate()
    proof.settled.return_value = True
    h.activate()
    assert len(state.dispatch) == 2


def test_confirmation_cannot_use_another_active_payload_or_missing_scope(
    tmp_path, monkeypatch
):
    h, writer, proof, state, _, _, _ = harness(tmp_path, monkeypatch)
    result = h.activate()
    with pytest.raises(NativeWriteUnresolved, match="exact native receipt"):
        barrier_confirm(
            writer, replace(result.record, value_rows=result.record.value_rows + 1)
        )
    with pytest.raises(NativeWriteJournalError, match="missing initialized"):
        barrier_confirm(
            writer,
            replace(result.record, build_token="ffffffff-ffff-4fff-8fff-ffffffffffff"),
        )


def test_untyped_scope_cannot_bypass_the_held_writer_context(tmp_path, monkeypatch):
    h, writer, proof, state, _, options, _ = harness(tmp_path, monkeypatch)
    with pytest.raises(TypeError, match="exact live scope"):
        writer.insert(**options, write_scope=object())
    assert state.dispatch == []


@pytest.mark.parametrize("change", ["table", "token", "payload", "owner", "thread"])
def test_publication_lease_cannot_send_another_intent(tmp_path, monkeypatch, change):
    h, writer, proof, state, scope, options, _ = harness(tmp_path, monkeypatch)
    original_insert = h.client.insert

    def inspect(client, table, rows, **kwargs):
        bad = dict(table=table, rows=deepcopy(rows), **kwargs)
        target = writer
        if change == "table":
            bad = {**options, "write_scope": kwargs["write_scope"]}
        elif change == "token":
            bad["deduplication_token"] += "-different"
        elif change == "payload":
            bad["rows"][0]["value_rows"] += 1
        elif change == "owner":
            target = DurableNativeCatalogWriter(
                writer.driver,
                directory=writer.directory,
                proof=proof,
                member_name=writer.member.name,
            )
        if change == "thread":
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(NativeWriteJournalError, match="owning thread"):
                    pool.submit(target.insert, **bad).result(timeout=2)
        else:
            with pytest.raises(NativeWriteUnresolved):
                target.insert(**bad)
        assert not state.dispatch
        return original_insert(table, rows, **kwargs)

    h.client.insert = MethodType(inspect, h.client)
    h.activate()
    assert len(state.dispatch) == 1


@pytest.mark.parametrize("same_active", [False, True])
def test_confirmed_lease_cannot_dispatch_before_publication_resolves(
    tmp_path, monkeypatch, same_active
):
    h, writer, proof, state, scope, options, _ = harness(tmp_path, monkeypatch)
    storage = h.base.coordinator._recovery_journal
    save = storage.save_record
    checked = []

    def checked_save(key, document):
        if document.get("phase") == "resolved":
            guard = state.guard
            if same_active:
                from tracer.services.clickhouse.v2.property_catalog.state_store import (
                    _activation_row,
                )

                bad = {
                    "table": f"`{writer.database}`.`{ACTIVE}`",
                    "rows": [_activation_row(guard._record)],
                    "columns": _COLUMNS[ACTIVE],
                    "timeout_ms": 5000,
                    "deduplication_token": guard._binding.deduplication_token,
                }
            else:
                bad = options
            with pytest.raises(NativeWriteUnresolved, match="unconfirmed exact ACTIVE"):
                writer.insert(**bad, write_scope=guard)
            guard._scoped.require_closed(guard._closed)
            assert len(state.dispatch) == 1
            checked.append(True)
        return save(key, document)

    monkeypatch.setattr(storage, "save_record", checked_save)
    h.activate()
    assert checked == [True]


@pytest.mark.parametrize("operation", ["confirm", "recover", "insert"])
def test_receipts_cannot_be_recovered_using_a_different_installation_proof(
    tmp_path, monkeypatch, operation
):
    h, writer, proof, state, scope, options, _ = harness(tmp_path, monkeypatch)
    record = h.activate().record
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir(mode=0o700)
    monkeypatch.setattr(
        proof_fixture,
        "installation",
        lambda _: replace(
            proof.identity, producer_stream_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        ),
    )
    _, foreign_proof, _, _, _ = writer_setup(foreign_dir, monkeypatch)
    foreign = DurableNativeCatalogWriter(
        writer.driver,
        directory=writer.directory,
        proof=foreign_proof,
        member_name=writer.member.name,
    )
    # Same tenant/build, epoch, DB, member labels and routes, different persisted
    # installation descriptor. Reject before any proof or scope-index mutation.
    before = {p.name: p.read_bytes() for p in writer.directory.rglob("*.json")}
    call = {
        "confirm": lambda: NativePublicationBarrier(foreign).confirm(record),
        "recover": lambda: foreign.recover_scope(scope, timeout_ms=5000),
        "insert": lambda: foreign.insert(**options),
    }[operation]
    with pytest.raises(NativeWriteUnresolved, match="writer installation/admission"):
        call()
    foreign_proof.attest.assert_not_called()
    foreign_proof.cover.assert_not_called()
    foreign_proof.settled.assert_not_called()
    assert before == {p.name: p.read_bytes() for p in writer.directory.rglob("*.json")}


def test_runtime_cannot_ack_repair_after_a_new_uncertain_reader_control_write(
    tmp_path, monkeypatch
):
    from tracer.services.clickhouse.v2.property_catalog import dev_runtime

    h, writer, proof, state, scope, options, _ = harness(tmp_path, monkeypatch)
    result = h.activate()
    record = result.record
    active = SimpleNamespace(
        **{field.name: getattr(record, field.name) for field in fields(record)},
        build_plan=h.lease.build_plan,
    )
    window = SimpleNamespace(
        since=h.base.clock() - timedelta(hours=1), until=h.base.clock()
    )
    execution = SimpleNamespace(
        activation=result,
        lease=h.lease,
        prepared=SimpleNamespace(
            scope=SimpleNamespace(
                project_ids=h.lease.build_plan.source_scope.project_ids
            ),
            mode=dev_runtime.LifecycleRunMode.FULL_REPAIR,
            cutoffs=SimpleNamespace(span_window=window),
        ),
    )
    steps = []
    control = proof_fixture.row_for("property_catalog_activation_control_events")
    control.update(
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        catalog_epoch=record.catalog_epoch,
        projection_version=record.projection_version,
        target_catalog_revision=record.catalog_revision,
        target_build_token=record.build_token,
    )
    control_options = {
        "table": f"`{writer.database}`.`property_catalog_activation_control_events`",
        "rows": [control],
        "columns": _COLUMNS["property_catalog_activation_control_events"],
        "deduplication_token": "later-reader-control",
        "timeout_ms": 5000,
    }
    inject = [True]

    def selection():
        steps.append("selection")
        if inject.pop() if inject else False:
            state.loss = True
            # Simulate another control publisher whose ACK was lost while this
            # runtime was reconciling selection. Visible ACTIVE remains cached.
            with pytest.raises(TimeoutError):
                writer.insert(**control_options)
            state.loss = False

    def acknowledge(**kwargs):
        assert kwargs == {"since": window.since, "until": window.until}
        with NativeWriteJournal(writer.directory) as journal:
            with pytest.raises(NativeWriteJournalBusy):
                with journal.scope(scope):
                    pytest.fail("repair acknowledgement released native scope")
        steps.append("ack")

    runtime = SimpleNamespace(
        catalog_client=SimpleNamespace(_durable_writer=writer),
        _source_capture=None,
        _validate_mutation_request=lambda *_: None,
        _refresh_project_tenant_authorization=lambda: SimpleNamespace(
            authorization_contract_sha256="c" * 64
        ),
        _require_execution=lambda: execution,
        _load_latest_active_retirement=lambda _: active,
        _publish_producer_retirement=lambda *_: steps.append("retirement"),
        reconcile_reader_selection=selection,
        _candidate_repair=SimpleNamespace(acknowledge_covered=acknowledge),
        _source_repair=SimpleNamespace(acknowledge_replacement=acknowledge),
        _authorized_build_binding_sha256=None,
    )
    activate = MethodType(
        dev_runtime.CheckedInPropertyCatalogDevRuntime.activate, runtime
    )
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        activate(object())
    assert steps == ["retirement", "selection"]
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        activate(object())
    assert steps == ["retirement", "selection"]  # Cache cannot skip confirmation.
    proof.settled.return_value = True
    assert activate(object())["activated"] is True
    assert steps == ["retirement", "selection", "retirement", "selection", "ack", "ack"]
    assert len(state.dispatch) == 2  # ACTIVE and original control; never replayed.
