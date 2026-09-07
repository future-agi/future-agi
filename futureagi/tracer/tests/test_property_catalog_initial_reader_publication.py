"""Deterministic INITIAL publication interleavings; no services or real clients."""

from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import activation_control as control
from tracer.tests.test_property_catalog_follow_selection import row as qualified_row
from tracer.tests.test_property_catalog_managed_reader_admission import Executor
from tracer.tests.test_property_catalog_reader_activation import (
    AT,
    SCOPE,
    Client,
    service,
    target,
)

DATABASE = "property_catalog_dev_oss"
HISTORY = [{"catalog_history_exists": 1}]


def empty_client():
    client = Client(database=DATABASE, deployment="dev")
    client.targets = []
    return client


def initial_event(item=None, *, previous=None, database=DATABASE):
    item = item or target()
    sequence = 1 if previous is None else previous.control_sequence + 1
    predecessor = "0" * 64 if previous is None else previous.control_sha256
    return control.ActivationControlEvent.create(
        control_sequence=sequence,
        request_id=control.initial_follow_request_id(
            database,
            item,
            control_sequence=sequence,
            previous_control_sha256=predecessor,
        ),
        action=control.ActivationControlAction.FOLLOW,
        target=item,
        previous_control_sha256=predecessor,
        controlled_at=AT,
    )


def manual_event(previous, action, *, item=None):
    return control.ActivationControlEvent.create(
        control_sequence=previous.control_sequence + 1,
        request_id=str(UUID(int=100)),
        action=control.ActivationControlAction(action),
        target=item or previous.target,
        previous_control_sha256=previous.control_sha256,
        controlled_at=AT,
    )


class ReaderExecutor:
    def __init__(self, client):
        self.client, self.calls = client, []

    def execute(self, sql, params, *, timeout_ms, settings):
        self.calls.append((sql, params, timeout_ms, settings))
        assert params["catalog_workspace_id"] == SCOPE.workspace_id
        assert settings["result_overflow_mode"] == "throw"
        return SimpleNamespace(
            data=list(self.client.query(sql, params, timeout_ms=timeout_ms))
        )


def select(executor, **kwargs):
    return control.ClickHouseActivationControlSelector(
        executor, database=DATABASE, deployment="dev", managed=True
    ).select_target(scope=asdict(SCOPE), timeout_ms=kwargs.get("timeout_ms", 1000))


def unavailable(executor, *, reason=None):
    with pytest.raises(control.ActivationControlUnavailable) as caught:
        select(executor)
    assert not isinstance(caught.value, control.ActivationControlBootstrapPending)
    if reason is not None:
        assert caught.value.reason == reason


def test_prepared_follow_is_pending_until_exact_active_and_then_follows(tmp_path):
    client = empty_client()
    prepared = service(client, tmp_path).prepare_initial(target())
    assert prepared.status == "prepared" and prepared.selected_target is None
    assert prepared.event == initial_event()
    reader = ReaderExecutor(client)
    with pytest.raises(control.ActivationControlBootstrapPending):
        select(reader)
    assert len(reader.calls) == 4
    assert (
        service(client, tmp_path).reconcile(SCOPE).status == "waiting_for_qualification"
    )
    client.targets = [target()]
    assert select(reader) == target()
    assert service(client, tmp_path).reconcile(SCOPE).status == "already_selected"
    client.targets.append(target(2))
    assert select(reader) == target(2)
    assert len(client.events) == len(client.attempts) == 1


@pytest.mark.parametrize("failure", [None, "before", "after"])
def test_restart_retries_identical_initial_follow_bytes(tmp_path, failure):
    client = empty_client()
    client.failure = failure
    if failure:
        with pytest.raises(TimeoutError):
            service(client, tmp_path).prepare_initial(target())
    else:
        service(client, tmp_path).prepare_initial(target())
    original = client.attempts[0]
    result = service(
        client, tmp_path, now=lambda: AT + timedelta(days=9)
    ).prepare_initial(target())
    assert result.event.controlled_at == AT and result.selected_target is None
    assert all(attempt == original for attempt in client.attempts)
    assert len(client.events) == 1
    assert service(client, tmp_path).reconcile(SCOPE).selected_target is None


@pytest.mark.parametrize("failure", [None, "before", "after"])
def test_failed_never_active_initial_can_be_replaced_without_qualifying_it(
    tmp_path, failure
):
    client = empty_client()
    client.failure = failure
    if failure:
        with pytest.raises(TimeoutError):
            service(client, tmp_path).prepare_initial(target())
    else:
        service(client, tmp_path).prepare_initial(target())
    # The lifecycle caller has admitted a newer fenced INITIAL; no ACTIVE exists.
    replacement = service(client, tmp_path).prepare_initial(target(2))
    assert replacement.event == initial_event(target(2), previous=initial_event())
    with pytest.raises(control.ActivationControlBootstrapPending):
        select(ReaderExecutor(client))
    client.targets = [target(2)]  # Failed revision 1 never acquires qualification.
    assert select(ReaderExecutor(client)) == target(2)
    assert service(client, tmp_path).reconcile(SCOPE).selected_target == target(2)
    with pytest.raises(
        control.ActivationControlRejected, match="control_initial_target_conflict"
    ):
        service(client, tmp_path).prepare_initial(target())
    assert len(client.events) == 2


@pytest.mark.parametrize(
    "history", [HISTORY, [{"bad": 1}], [{"catalog_history_exists": True}]]
)
@pytest.mark.parametrize("already_prepared", [False, True])
def test_history_or_invalid_proof_blocks_initial_creation_and_replacement(
    tmp_path, history, already_prepared
):
    client = empty_client()
    if already_prepared:
        service(client, tmp_path).prepare_initial(target())
    client.history_rows = history
    with pytest.raises(control.ActivationControlRejected):
        service(client, tmp_path).prepare_initial(target(2))
    assert len(client.events) == int(already_prepared)


def test_active_initial_cannot_be_automatically_replaced_as_onboarding(tmp_path):
    client = empty_client()
    service(client, tmp_path).prepare_initial(target())
    client.targets = [target()]
    with pytest.raises(
        control.ActivationControlRejected, match="control_initial_history_not_empty"
    ):
        service(client, tmp_path).prepare_initial(target(2))
    assert select(ReaderExecutor(client)) == target()


@pytest.mark.parametrize("answers", [(False,), (True, False)])
def test_initial_authorization_required_before_read_and_before_append(
    tmp_path, answers
):
    client = empty_client()
    authorizations = iter(answers)
    with pytest.raises(
        control.ActivationControlRejected, match="control_initial_scope_ineligible"
    ):
        service(
            client, tmp_path, authorize_scope=lambda _: next(authorizations)
        ).prepare_initial(target())
    assert not client.attempts


def test_preparation_keeps_existing_history_and_installation_identity(tmp_path):
    with pytest.raises(control.ActivationControlRejected, match="history_not_empty"):
        service(Client(), tmp_path).prepare_initial(target())
    for item in (target(epoch=8), target(projection=1)):
        with pytest.raises(
            control.ActivationControlRejected, match="identity_conflict"
        ):
            service(empty_client(), tmp_path).prepare_initial(item)


def test_manual_disable_before_active_is_sticky_and_can_be_explicitly_reenabled(
    tmp_path,
):
    client = empty_client()
    automatic = service(client, tmp_path)
    prepared = automatic.prepare_initial(target())
    plane = control.PropertyCatalogActivationControlPlane(automatic.store)
    disabled = plane.disable(
        request=control.ActivationControlRequest(
            str(UUID(int=100)), target(), prepared.event.head
        ),
        now=AT,
    )
    assert disabled.selected_target is None
    assert automatic.prepare_initial(target(2)).status == "disable"
    client.targets = [target()]
    assert automatic.reconcile(SCOPE).status == "disable"
    unavailable(ReaderExecutor(client), reason="control_disabled")
    plane.activate(
        request=control.ActivationControlRequest(
            str(UUID(int=101)), target(), disabled.event.head
        ),
        now=AT,
    )
    assert select(ReaderExecutor(client)) == target()
    assert len(client.events) == 3


def test_rollback_cannot_qualify_failed_initial_and_remains_sticky_after_active(
    tmp_path,
):
    client = empty_client()
    automatic = service(client, tmp_path)
    prepared = automatic.prepare_initial(target())
    plane = control.PropertyCatalogActivationControlPlane(automatic.store)
    with pytest.raises(
        control.ActivationControlRejected, match="qualified_activation_missing"
    ):
        plane.rollback(
            request=control.ActivationControlRequest(
                str(UUID(int=100)), target(), prepared.event.head
            ),
            now=AT,
        )
    client.targets = [target(), target(2)]
    plane.rollback(
        request=control.ActivationControlRequest(
            str(UUID(int=100)), target(), prepared.event.head
        ),
        now=AT,
    )
    assert automatic.prepare_initial(target(3)).status == "rollback"
    assert automatic.reconcile(SCOPE).selected_target == target()
    assert select(ReaderExecutor(client)) == target()


def test_disabled_failed_initial_requires_explicit_reenable_of_qualified_replacement(
    tmp_path,
):
    client = empty_client()
    automatic = service(client, tmp_path)
    prepared = automatic.prepare_initial(target())
    plane = control.PropertyCatalogActivationControlPlane(automatic.store)
    disabled = plane.disable(
        request=control.ActivationControlRequest(
            str(UUID(int=100)), target(), prepared.event.head
        ),
        now=AT,
    )
    assert automatic.prepare_initial(target(2)).status == "disable"
    client.targets = [target(2)]
    assert automatic.reconcile(SCOPE).status == "disable"
    unavailable(ReaderExecutor(client), reason="control_disabled")
    plane.activate(
        request=control.ActivationControlRequest(
            str(UUID(int=101)), target(2), disabled.event.head
        ),
        now=AT,
    )
    assert select(ReaderExecutor(client)) == target(2)
    assert len(client.events) == 3


def test_reconcile_recovers_prepublished_follow_without_claiming_qualification(
    tmp_path,
):
    client = empty_client()
    client.failure = "after"
    with pytest.raises(TimeoutError):
        service(client, tmp_path).prepare_initial(target())
    result = service(client, tmp_path).reconcile(SCOPE)
    assert (
        result.status == "waiting_for_qualification" and result.selected_target is None
    )
    assert len(client.events) == 1


def test_completed_control_witness_rejects_disappearance_on_prepare_restart(tmp_path):
    client = empty_client()
    service(client, tmp_path).prepare_initial(target())
    client.hidden = True
    with pytest.raises(
        control.ActivationControlRejected, match="control_durable_head_not_visible"
    ):
        service(client, tmp_path).prepare_initial(target(2))
    assert len(client.attempts) == 1


@pytest.mark.parametrize("history", [[], HISTORY])
def test_initial_control_miss_rereads_follow_after_history_probe(history):
    event = initial_event()
    executor = Executor([[], history, [event.as_row()], [qualified_row(target())]])
    assert select(executor) == target()
    assert len(executor.calls) == 4


def test_existing_catalog_missing_control_is_still_an_error():
    executor = Executor([[], HISTORY, []])
    unavailable(executor, reason="control_missing")
    assert len(executor.calls) == 3


def test_active_publication_between_follow_and_history_queries_is_reread():
    event = initial_event().as_row()
    executor = Executor([[event], [], HISTORY, [event], [qualified_row(target())]])
    assert select(executor) == target()
    assert len(executor.calls) == 5


@pytest.mark.parametrize(
    "history",
    [
        HISTORY,
        [{"bad": 1}],
        [{"catalog_history_exists": True}],
        None,
        RuntimeError("offline"),
    ],
)
def test_missing_qualification_with_nonempty_or_malformed_history_never_pending(
    history,
):
    event = initial_event().as_row()
    executor = Executor([[event], [], history, [event], []])
    unavailable(executor)


@pytest.mark.parametrize("action", ["disable", "rollback"])
def test_manual_head_on_pending_reread_wins(action):
    prepared = initial_event()
    manual = manual_event(prepared, action)
    executor = Executor(
        [[prepared.as_row()], [], [], [prepared.as_row(), manual.as_row()]]
    )
    if action == "disable":
        unavailable(executor, reason="control_disabled")
    else:
        # Selector only pins; the normal reader still verifies exact qualification.
        assert select(executor) == target()
    assert len(executor.calls) == 4


def test_superseding_initial_on_pending_reread_remains_typed_pending():
    first = initial_event()
    replacement = initial_event(target(2), previous=first)
    with pytest.raises(control.ActivationControlBootstrapPending):
        select(
            Executor([[first.as_row()], [], [], [first.as_row(), replacement.as_row()]])
        )


@pytest.mark.parametrize("corruption", ["digest", "gap", "fork", "foreign_scope"])
def test_corrupt_control_on_pending_reread_never_pending(corruption):
    first = initial_event()
    row = first.as_row()
    if corruption == "digest":
        row["control_sha256"] = "f" * 64
    elif corruption == "gap":
        row = manual_event(first, "disable").as_row()
    elif corruption == "foreign_scope":
        row = initial_event(replace(target(), workspace_id=str(UUID(int=999)))).as_row()
    else:
        row = initial_event(target(2)).as_row()
    rows = [first.as_row(), row] if corruption == "fork" else [row]
    unavailable(Executor([[first.as_row()], [], [], rows]), reason="control_invalid")


def test_regular_or_wrong_database_follow_cannot_claim_prepublication_pending():
    wrong = initial_event(database="property_catalog_dev_other")
    unavailable(Executor([[wrong.as_row()], []]), reason="control_invalid")


def test_deadline_is_shared_across_initial_pending_probes(monkeypatch):
    ticks = iter([0.0, 0.01, 0.03, 0.06, 1.01])
    monkeypatch.setattr(control, "monotonic", lambda: next(ticks))
    executor = Executor([[initial_event().as_row()], [], []])
    unavailable(executor, reason="control_deadline")
    assert [call[2]["timeout_ms"] for call in executor.calls] == [990, 970, 940]


def test_history_probe_is_scoped_and_does_not_filter_out_bad_or_other_epoch_rows():
    sql = control.activation_history_sql(DATABASE, deployment="dev")
    assert "organization_id = %(catalog_organization_id)s" in sql
    assert "workspace_id = %(catalog_workspace_id)s" in sql
    assert (
        "catalog_epoch" not in sql and "status" not in sql and "qualified_at" not in sql
    )
    assert sql.endswith("LIMIT 1")
