"""Reader-control success requires proof, not just a visible event.

These use real coordinators/services with a controlled transport. The separate
native receipt tests cover the actual disk journal and all-member proof path.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlRejected,
    ActivationControlRequest,
    PropertyCatalogActivationControlPlane,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    NativeWriteUnresolved,
)
from tracer.tests.test_property_catalog_initial_reader_publication import empty_client
from tracer.tests.test_property_catalog_reader_activation import (
    AT,
    SCOPE,
    Client,
    service,
    target,
)


def pending(automatic):
    return automatic.coordinator.serialize(
        SCOPE, lambda: automatic.coordinator.pending(SCOPE)
    )


@pytest.mark.parametrize("initial", [False, True])
def test_visible_unconfirmed_event_keeps_intent_across_restart(tmp_path, initial):
    client = empty_client() if initial else Client()
    client.confirmation_failure = NativeWriteUnresolved("original write unresolved")

    def run(automatic):
        return (
            automatic.prepare_initial(target())
            if initial
            else automatic.reconcile(SCOPE)
        )

    automatic = service(client, tmp_path)
    with pytest.raises(NativeWriteUnresolved):
        run(automatic)
    original = pending(automatic)
    assert original is not None
    assert len(client.events) == len(client.attempts) == 1
    restarted = service(client, tmp_path, now=lambda: AT + timedelta(days=2))
    with pytest.raises(NativeWriteUnresolved):
        run(restarted)
    assert pending(restarted) == original
    assert len(client.attempts) == 1

    client.confirmation_failure = None
    result = run(restarted)
    assert result.event == original
    assert result.status == ("prepared" if initial else "recovered")
    assert pending(restarted) is None
    assert len(client.attempts) == 1
    assert client.confirmations[-1] == client.attempts[0]


@pytest.mark.parametrize("initial", [False, True])
def test_already_selected_or_prepared_head_is_reconfirmed(tmp_path, initial):
    client = empty_client() if initial else Client()
    automatic = service(client, tmp_path)

    def operation():
        return (
            automatic.prepare_initial(target())
            if initial
            else automatic.reconcile(SCOPE)
        )

    operation()
    assert pending(automatic) is None
    client.confirmation_failure = NativeWriteUnresolved("receipt disappeared")
    with pytest.raises(NativeWriteUnresolved):
        operation()
    assert len(client.attempts) == 1


@pytest.mark.parametrize("action", ["disable", "rollback"])
@pytest.mark.parametrize("initial", [False, True])
def test_sticky_manual_head_is_confirmed_without_reenabling(tmp_path, action, initial):
    client = Client()
    automatic = service(client, tmp_path)
    first = automatic.reconcile(SCOPE)
    client.targets.append(target(2))
    chosen = target(2) if action == "disable" else target()
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    manual = getattr(plane, action)(
        request=ActivationControlRequest(str(UUID(int=100)), chosen, first.event.head),
        now=AT,
    )
    client.confirmation_failure = NativeWriteUnresolved("manual receipt unresolved")
    restarted = service(client, tmp_path)
    with pytest.raises(NativeWriteUnresolved):
        if initial:
            restarted.prepare_initial(target(3))
        else:
            restarted.reconcile(SCOPE)
    assert len(client.events) == len(client.attempts) == 2
    assert client.events[-1] == manual.event.as_row()


@pytest.mark.parametrize("action", ["activate", "disable", "rollback"])
def test_manual_exact_replay_cannot_skip_receipt_confirmation(tmp_path, action):
    client = Client()
    automatic = service(client, tmp_path)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    if action == "activate":
        request = ActivationControlRequest(str(UUID(int=100)), target(), None)
    else:
        first = automatic.reconcile(SCOPE)
        client.targets.append(target(2))
        request = ActivationControlRequest(
            str(UUID(int=100)),
            target(2) if action == "disable" else target(),
            first.event.head,
        )
    first = getattr(plane, action)(request=request, now=AT)
    attempts = len(client.attempts)
    client.confirmation_failure = NativeWriteUnresolved("exact replay lacks proof")
    with pytest.raises(NativeWriteUnresolved):
        getattr(plane, action)(request=request, now=AT + timedelta(days=3))
    assert len(client.attempts) == attempts
    client.confirmation_failure = None
    replay = getattr(plane, action)(request=request, now=AT + timedelta(days=3))
    assert replay.idempotent and replay.event == first.event
    assert len(client.attempts) == attempts


def test_store_exact_replay_and_pending_completion_require_proof(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    first = automatic.reconcile(SCOPE)
    client.confirmation_failure = NativeWriteUnresolved("missing receipt")
    with pytest.raises(NativeWriteUnresolved):
        automatic.store.append_control_event(first.event, expected_head=None)
    # Simulate loss of the coordinator's completion after the write was visible.
    automatic.coordinator.serialize(
        SCOPE, lambda: automatic.coordinator.prepare(first.event)
    )
    with pytest.raises(NativeWriteUnresolved):
        automatic.store.append_control_event(first.event, expected_head=None)
    assert pending(automatic) == first.event
    assert len(client.attempts) == 1


def test_replaying_old_manual_request_also_confirms_returned_selection(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    old_request = ActivationControlRequest(str(UUID(int=100)), target(), None)
    first = plane.activate(request=old_request, now=AT)
    client.targets.append(target(2))
    latest = plane.activate(
        request=ActivationControlRequest(
            str(UUID(int=101)), target(2), first.event.head
        ),
        now=AT,
    )
    original = client.confirm_receipt

    def confirm(table, rows, **kwargs):
        if rows[0]["request_id"] == latest.event.request_id:
            raise NativeWriteUnresolved("latest selection is not confirmed")
        return original(table, rows, **kwargs)

    client.confirm_receipt = confirm
    with pytest.raises(NativeWriteUnresolved, match="latest selection"):
        plane.activate(request=old_request, now=AT)
    assert len(client.attempts) == 2


def test_old_manual_replay_rejects_a_lagging_view_of_the_durable_head(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    old_request = ActivationControlRequest(str(UUID(int=100)), target(), None)
    first = plane.activate(request=old_request, now=AT)
    client.targets.append(target(2))
    plane.activate(
        request=ActivationControlRequest(
            str(UUID(int=101)), target(2), first.event.head
        ),
        now=AT,
    )
    original_query = client.query

    def query(sql, params, **kwargs):
        rows = original_query(sql, params, **kwargs)
        return rows[:1] if rows and "control_sequence" in rows[0] else rows

    client.query = query
    confirmations = len(client.confirmations)
    with pytest.raises(
        ActivationControlRejected, match="control_durable_head_not_visible"
    ):
        plane.activate(request=old_request, now=AT)
    assert len(client.confirmations) == confirmations
    assert len(client.attempts) == 2


def test_manual_replay_keeps_the_coordinator_lock_during_confirmation(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    request = ActivationControlRequest(str(UUID(int=100)), target(), None)
    first = plane.activate(request=request, now=AT)
    original_confirm = client.confirm_receipt
    original_read = client.query
    observed = []

    def checked_read(sql, params, **kwargs):
        # This is deliberately a direct call, not serialize(): it fails unless
        # manual history lookup already holds the same workspace coordinator.
        automatic.coordinator.pending(SCOPE)
        observed.append("read-under-lock")
        return original_read(sql, params, **kwargs)

    def confirm(*args, **kwargs):
        assert automatic.coordinator.pending(SCOPE) is None
        observed.append("confirm-under-lock")
        return original_confirm(*args, **kwargs)

    client.query, client.confirm_receipt = checked_read, confirm
    result = plane.activate(request=request, now=AT)
    assert result.idempotent and result.event == first.event
    assert observed == ["read-under-lock", "confirm-under-lock"]


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("direct_store", [False, True])
def test_pending_recovery_does_not_complete_before_later_head_confirmation(
    tmp_path, initial, direct_store
):
    client = empty_client() if initial else Client()
    automatic = service(client, tmp_path)
    first = (
        automatic.prepare_initial(target()) if initial else automatic.reconcile(SCOPE)
    )
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    latest = plane.disable(
        request=ActivationControlRequest(
            str(UUID(int=100)), target(), first.event.head
        ),
        now=AT,
    )
    # A durable older intent can coexist with a newer observed control head.
    automatic.coordinator.serialize(
        SCOPE, lambda: automatic.coordinator.prepare(first.event)
    )
    original = client.confirm_receipt

    def confirm(table, rows, **kwargs):
        if rows[0]["request_id"] == latest.event.request_id:
            raise NativeWriteUnresolved("later head unconfirmed")
        return original(table, rows, **kwargs)

    client.confirm_receipt = confirm
    with pytest.raises(NativeWriteUnresolved, match="later head"):
        if direct_store:
            automatic.store.append_control_event(first.event, expected_head=None)
        elif initial:
            automatic.prepare_initial(target())
        else:
            automatic.reconcile(SCOPE)
    assert pending(automatic) == first.event
    assert len(client.attempts) == 2


@pytest.mark.parametrize("action", ["activate", "disable", "rollback"])
def test_fresh_manual_append_cannot_extend_an_unconfirmed_predecessor(tmp_path, action):
    client = Client()
    automatic = service(client, tmp_path)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    first = plane.activate(
        request=ActivationControlRequest(str(UUID(int=100)), target(), None), now=AT
    )
    client.targets.append(target(2))
    head = first.event
    chosen = target() if action == "disable" else target(2)
    if action == "rollback":
        head = plane.activate(
            request=ActivationControlRequest(str(UUID(int=101)), target(2), head.head),
            now=AT,
        ).event
        chosen = target()
    original = client.confirm_receipt

    def confirm(table, rows, **kwargs):
        if rows[0]["request_id"] == head.request_id:
            raise NativeWriteUnresolved("unconfirmed predecessor")
        return original(table, rows, **kwargs)

    client.confirm_receipt = confirm
    attempts = len(client.attempts)
    with pytest.raises(NativeWriteUnresolved, match="unconfirmed predecessor"):
        getattr(plane, action)(
            request=ActivationControlRequest(str(UUID(int=102)), chosen, head.head),
            now=AT,
        )
    assert len(client.attempts) == attempts and pending(automatic) is None


def test_old_manual_replay_does_not_hide_an_unobserved_pending_disable(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    request = ActivationControlRequest(str(UUID(int=100)), target(), None)
    first = plane.activate(request=request, now=AT)
    client.failure = "before"
    with pytest.raises(TimeoutError):
        plane.disable(
            request=ActivationControlRequest(
                str(UUID(int=101)), target(), first.event.head
            ),
            now=AT,
        )
    original = pending(automatic)
    with pytest.raises(ActivationControlRejected, match="control_append_uncertain"):
        plane.activate(request=request, now=AT)
    assert pending(automatic) == original and len(client.attempts) == 2


@pytest.mark.parametrize("failure", ["absent", "false", "nonboolean", "no_method"])
def test_missing_receipt_capability_is_rejected_before_intent_or_insert(
    tmp_path, failure
):
    client = Client()
    if failure == "absent":

        class WithoutReceiptClient:
            def __getattr__(self, name):
                if name == "receipt_confirmation_available":
                    raise AttributeError(name)
                return getattr(client, name)

        controlled = WithoutReceiptClient()
    else:
        controlled = client
        if failure == "no_method":
            controlled.confirm_receipt = None
        else:
            controlled.receipt_confirmation_available = (
                False if failure == "false" else 1
            )
    automatic = service(controlled, tmp_path)
    with pytest.raises(
        ActivationControlRejected, match="control_native_receipt_required"
    ):
        automatic.reconcile(SCOPE)
    assert not client.events and not client.attempts
    assert pending(automatic) is None


def test_confirmation_holds_reader_coordinator_through_complete(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    original = client.confirm_receipt
    observations = []

    def confirm(*args, **kwargs):
        # pending() deliberately requires the existing workspace lock. The
        # native wrapper is entered below it, never the other way around.
        observations.append(automatic.coordinator.pending(SCOPE))
        return original(*args, **kwargs)

    client.confirm_receipt = confirm
    first = automatic.reconcile(SCOPE)
    assert observations == [first.event]
    assert pending(automatic) is None
