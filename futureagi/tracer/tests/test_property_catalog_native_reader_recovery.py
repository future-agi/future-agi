"""Real reader-control/adapter/native journal chain with simulated network I/O."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_TABLE,
    ActivationControlRequest,
    PropertyCatalogActivationControlPlane,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournal,
    NativeWriteJournalError,
    NativeWriteScope,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import _COLUMNS
from tracer.services.clickhouse.v2.property_catalog.reader_activation import (
    ReaderActivationClient,
)
from tracer.tests import test_property_catalog_native_write_proof as proof_fixture
from tracer.tests.test_property_catalog_control_receipt_recovery import pending
from tracer.tests.test_property_catalog_durable_native_writer import setup
from tracer.tests.test_property_catalog_initial_reader_publication import initial_event
from tracer.tests.test_property_catalog_native_write_proof import row_for
from tracer.tests.test_property_catalog_reader_activation import (
    AT,
    SCOPE,
    Client,
    Driver,
    service,
    target,
)
from tracer.tests.test_property_catalog_write_admission import installation


def harness(tmp_path, monkeypatch, *, initial=False):
    native_path = tmp_path / "native"
    native_path.mkdir(mode=0o700)
    (tmp_path / "reader").mkdir(mode=0o700)
    monkeypatch.setattr(
        proof_fixture,
        "installation",
        lambda environment: replace(
            installation(environment), catalog_epoch=7, projection_version=3
        ),
    )
    writer, proof, transport, _, _ = setup(native_path, monkeypatch)
    driver = Driver(writer.database, deployment="dev", replicated=True)
    driver.host, driver.port = writer.driver.host, writer.driver.port
    writer.driver = driver
    observed = Client(database=writer.database, deployment="dev")
    if initial:
        observed.targets = []
    state = SimpleNamespace(loss=False, prepared=False, sent=[])

    def send(*args, **kwargs):
        if state.prepared:
            raise TimeoutError("stopped before dispatch")
        kwargs["before_send"]()
        table = next(table for table in _COLUMNS if f".{table} " in kwargs["sql"])
        rows = [
            dict(zip(_COLUMNS[table], values, strict=True))
            for values in kwargs["values"]
        ]
        state.sent.append((table, kwargs["query_id"]))
        if table == ACTIVATION_CONTROL_TABLE:
            observed.events.extend(rows)
        if state.loss:
            raise TimeoutError("lost response after event visibility")
        return len(rows)

    transport.side_effect = send
    proof.agreed_read = Mock(
        side_effect=lambda sql, params, timeout_ms, agreement=None: observed.query(
            sql, params, timeout_ms=timeout_ms
        )
    )

    def restart():
        client = ReaderActivationClient(
            driver,
            database=writer.database,
            user=driver.user,
            expected_hostnames=("catalog-0",),
            deployment="dev",
            durable_writer=writer,
        )
        client.deployment = "dev"
        return service(client, tmp_path / "reader")

    return writer, proof, observed, state, restart


@pytest.mark.parametrize("initial", [False, True])
def test_real_chain_visible_lost_ack_does_not_complete_reader_intent(
    tmp_path, monkeypatch, initial
):
    writer, proof, observed, state, restart = harness(
        tmp_path, monkeypatch, initial=initial
    )

    def run(automatic):
        return (
            automatic.prepare_initial(target())
            if initial
            else automatic.reconcile(SCOPE)
        )

    automatic = restart()
    state.loss = True
    with pytest.raises(TimeoutError):
        run(automatic)
    original = pending(automatic)
    assert original is not None and len(observed.events) == 1
    state.loss = False
    restarted = restart()
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        run(restarted)
    assert pending(restarted) == original and len(state.sent) == 1
    proof.settled.return_value = True
    result = run(restarted)
    assert result.event == original and pending(restarted) is None
    assert len(state.sent) == 1


def test_confirmed_initial_follow_does_not_drain_prepared_active(tmp_path, monkeypatch):
    writer, proof, observed, state, restart = harness(
        tmp_path, monkeypatch, initial=True
    )
    first = restart().prepare_initial(target())
    active_table = "property_catalog_activations"
    row = row_for(active_table)
    row.update(
        catalog_epoch=7,
        projection_version=3,
        catalog_revision=target().catalog_revision,
        build_token=target().build_token,
    )
    state.prepared = True
    with pytest.raises(TimeoutError, match="before dispatch"):
        writer.insert(
            f"`{writer.database}`.`{active_table}`",
            (row,),
            columns=_COLUMNS[active_table],
            deduplication_token="prepared-active",
            timeout_ms=1000,
        )
    state.prepared = False
    assert restart().prepare_initial(target()).event == first.event
    assert len(state.sent) == 1  # Only FOLLOW; no ACTIVE authorization was assumed.
    scope = NativeWriteScope.from_rows(active_table, (row,), proof.identity)
    with (
        NativeWriteJournal(writer.directory) as journal,
        journal.scope(scope) as scoped,
    ):
        assert len(scoped.pending()) == 1
        with scoped.session(active_table, "prepared-active") as session:
            assert session.load().state == "prepared"


@pytest.mark.parametrize("action", ["disable", "rollback"])
def test_manual_lost_ack_replay_uses_original_event_scope(
    tmp_path, monkeypatch, action
):
    writer, proof, observed, state, restart = harness(tmp_path, monkeypatch)
    automatic = restart()
    first = automatic.reconcile(SCOPE)
    observed.targets.append(target(2))
    request = ActivationControlRequest(
        str(UUID(int=200)),
        target(2) if action == "disable" else target(),
        first.event.head,
    )
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    state.loss = True
    with pytest.raises(TimeoutError):
        getattr(plane, action)(request=request, now=AT)
    original = pending(automatic)
    state.loss = False
    with pytest.raises(NativeWriteUnresolved, match="still unresolved"):
        getattr(plane, action)(request=request, now=AT)
    assert len(state.sent) == 2 and pending(automatic) == original
    proof.settled.return_value = True
    result = getattr(plane, action)(request=request, now=AT)
    assert result.idempotent and result.event == original
    assert pending(automatic) is None
    settled = restart().reconcile(SCOPE)
    assert settled.selected_target == (None if action == "disable" else target())
    assert len(state.sent) == 2
    assert pending(automatic) is None


def test_visible_initial_event_without_native_journal_is_not_success(
    tmp_path, monkeypatch
):
    writer, proof, observed, state, restart = harness(
        tmp_path, monkeypatch, initial=True
    )
    observed.events.append(initial_event(database=writer.database).as_row())
    with pytest.raises((NativeWriteUnresolved, NativeWriteJournalError)):
        restart().prepare_initial(target())
    assert not state.sent
