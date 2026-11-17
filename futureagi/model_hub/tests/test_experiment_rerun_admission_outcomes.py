"""Main504 Option A regressions authored before the r3 product edit.

NOT_RUN locally. Real routed sessions, ORM, preflight and synchronous domain
starter; only final transport is controlled except the explicit malformed
DOMAIN-return test. Simulated admitted effects are not workflow execution proof.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from accounts.tests.personal_keys import session_client_for
from model_hub.services import experiment_target_admission as admission
from model_hub.tests import test_experiment_agent_target_admission as old
from model_hub.tests import test_experiment_target_kind as kind
from model_hub.tests import test_experiment_target_scope as scope
from tfc.utils.general_methods import GeneralMethods

actors = scope.actors
tenants = old.tenants
console_flag = kind.console_flag
pytestmark = pytest.mark.django_db(transaction=True)

UNCONFIRMED = (
    "Re-run admission was not confirmed for every experiment. "
    "Check execution status before retrying."
)
GENERIC = "Experiment target or execution context not found or unavailable."
SUCCESS = "Re-Run has started for the experiments."


@pytest.fixture
def batch(actors):
    actor = actors["a"]
    second = old.tenancy._make_experiment(
        actor["org"], actor["workspace"], actor["user"], "rerun-second"
    )
    agents = [old.stored_agent(actor), old.stored_agent(actor, second)]
    for agent in agents:
        scope.dependent_outputs(agent)
    return SimpleNamespace(
        actor=actor,
        agents={str(agent.experiment.pk): agent for agent in agents},
        foreign=actors["b"]["exp"]["experiment"],
        client=session_client_for(actor["user"], actor["org"], actor["workspace"]),
    )


def rerun(batch, body=None):
    return batch.client.post(
        f"{old.V2}re-run/",
        body if body is not None else {"experiment_ids": list(batch.agents)},
        format="json",
    )


def assert_error(response, text=UNCONFIRMED, status=500, code="server_error"):
    assert response.status_code == status, response.content
    expected = GeneralMethods().custom_error_response(status, text, code=code).data
    assert response.json() == expected
    for key in ("message", "detail", "error", "result"):
        assert response.json()[key] == text
    assert SUCCESS not in response.content.decode()


def admitted_effects(agent):
    # Model work already issued before another selected start fails. The API
    # must not roll back/reset these exact values, cancel, or retry the start.
    old.ExperimentsTable._base_manager.filter(pk=agent.experiment.pk).update(
        status=old.StatusType.RUNNING.value
    )
    old.Cell._base_manager.filter(column_id=agent.column.pk).update(
        value="already-admitted-output", status=old.CellStatus.PASS.value
    )
    return kind.execution_state(agent)


@pytest.mark.parametrize(
    "outcomes",
    [
        ("ok", "ok"),
        ("failure", "failure"),
        ("ok", "failure"),
        ("failure", "ok"),
        ("ok", "empty"),
        ("empty", "ok"),
    ],
)
def test_full_rerun_reports_only_confirmed_batch_admission(
    batch, console_flag, outcomes
):
    attempts, admitted = [], {}
    foreign_before = old.state(batch.foreign)

    def transport(**kwargs):
        experiment_id = kwargs["workflow_input"].experiment_id
        assert experiment_id in batch.agents
        assert experiment_id not in attempts
        outcome = outcomes[len(attempts)]
        attempts.append(experiment_id)
        assert kwargs["workflow_id"] == f"experiment-{experiment_id}"
        assert kwargs["workflow_input"].max_concurrent_rows == 3
        assert kwargs["workflow_input"].rerun_agent_config_ids == []
        if outcome == "failure":
            raise RuntimeError("synthetic-exception-payload-do-not-return")
        if outcome == "empty":
            return SimpleNamespace(id="")
        admitted[experiment_id] = admitted_effects(batch.agents[experiment_id])
        return SimpleNamespace(id=kwargs["workflow_id"])

    with (
        patch(f"{old.CLIENT}.start_workflow_sync", side_effect=transport) as start,
        patch("tfc.temporal.experiments.cancel_experiment_workflow") as cancel,
    ):
        response = rerun(
            batch, {"experiment_ids": list(batch.agents), "max_concurrent_rows": 3}
        )
    if all(outcome == "ok" for outcome in outcomes):
        assert response.status_code == 200, response.content
        assert response.json() == GeneralMethods().success_response(SUCCESS).data
    else:
        assert_error(response)
    assert start.call_count == len(batch.agents)
    assert set(attempts) == set(batch.agents)
    assert len(admitted) == outcomes.count("ok")
    for experiment_id, exact_state in admitted.items():
        assert kind.execution_state(batch.agents[experiment_id]) == exact_state
    assert old.state(batch.foreign) == foreign_before
    cancel.assert_not_called()
    assert "synthetic-exception-payload" not in response.content.decode()


@pytest.mark.parametrize("boundary", ["transport", "domain"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " \t\n",
        False,
        7,
        [],
        {},
        object(),
        SimpleNamespace(id="nested"),
        Mock(),
    ],
    ids=[
        "none",
        "empty",
        "blank",
        "bool",
        "int",
        "list",
        "dict",
        "object",
        "handle",
        "mock",
    ],
)
def test_malformed_workflow_id_is_not_confirmation(
    batch, console_flag, boundary, value
):
    # A transport handle's id is what the real domain starter returns. A domain
    # starter returning a handle/object itself is also not its string contract.
    target = (
        f"{old.CLIENT}.start_workflow_sync" if boundary == "transport" else old.START
    )
    result = SimpleNamespace(id=value) if boundary == "transport" else value
    with patch(target, return_value=result) as start:
        response = rerun(batch)
    assert_error(response)
    assert start.call_count == len(batch.agents)


@pytest.mark.parametrize("handle", [None, {}, object()])
def test_missing_transport_handle_id_is_not_success(batch, console_flag, handle):
    with patch(f"{old.CLIENT}.start_workflow_sync", return_value=handle) as start:
        response = rerun(batch)
    assert_error(response)
    assert start.call_count == len(batch.agents)


@pytest.mark.parametrize(
    "case,detail",
    [
        ("deleted_graph", GENERIC),
        (
            "mismatched_graph",
            "Selected agent version does not belong to the selected agent.",
        ),
        (
            "draft",
            "Selected agent version is in draft state and cannot be used in experiments.",
        ),
        ("invalid_status", "Selected agent version is not executable."),
        ("snapshot_mismatch", GENERIC),
    ],
)
def test_preflight_policy_refusal_keeps_exact_403_and_no_writes(
    batch, console_flag, case, detail
):
    agent = next(iter(batch.agents.values()))
    if case == "invalid_status":
        type(agent.version)._base_manager.filter(pk=agent.version.pk).update(
            status="invalid"
        )
    elif case == "snapshot_mismatch":
        old.ExperimentsTable._base_manager.filter(pk=agent.experiment.pk).update(
            snapshot_dataset_id=batch.foreign.snapshot_dataset_id
        )
    else:
        kind.unavailable(agent, batch.actor, case)
    before = {key: kind.execution_state(value) for key, value in batch.agents.items()}
    with patch(f"{old.CLIENT}.start_workflow_sync") as start:
        response = rerun(batch)
    assert_error(response, detail, 403, old.CODE)
    assert {
        key: kind.execution_state(value) for key, value in batch.agents.items()
    } == before
    assert (
        admission.worker_error(admission.AgentTargetNotQualified(detail))
        == f"{old.CODE}: {detail}"
    )
    start.assert_not_called()


def test_late_policy_refusal_does_not_undo_already_admitted_start(batch, console_flag):
    admitted = {}

    def transport(**kwargs):
        experiment_id = kwargs["workflow_input"].experiment_id
        # Preflight already passed. Persist a real revocation for the next
        # selected target so its real domain recheck (not a fake) refuses it.
        for other_id, other in batch.agents.items():
            if other_id != experiment_id:
                old.revoke_graph(other)
        admitted[experiment_id] = admitted_effects(batch.agents[experiment_id])
        return SimpleNamespace(id=kwargs["workflow_id"])

    with (
        patch(f"{old.CLIENT}.start_workflow_sync", side_effect=transport) as start,
        patch("tfc.temporal.experiments.cancel_experiment_workflow") as cancel,
    ):
        response = rerun(batch)
    assert_error(response, GENERIC, 403, old.CODE)
    start.assert_called_once()
    cancel.assert_not_called()
    assert len(admitted) == 1
    for experiment_id, exact_state in admitted.items():
        assert kind.execution_state(batch.agents[experiment_id]) == exact_state


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"experiment_ids": []},
        {"experiment_ids": None},
        {"experiment_ids": "not-a-list"},
        {"experiment_ids": ["not-a-uuid"]},
        {"experiment_ids": [None]},
    ],
)
def test_invalid_or_empty_selection_keeps_validation_without_starts(
    batch, console_flag, body
):
    before = {key: kind.execution_state(value) for key, value in batch.agents.items()}
    with patch(f"{old.CLIENT}.start_workflow_sync") as start:
        response = rerun(batch, body)
    assert response.status_code == 400, response.content
    assert SUCCESS not in response.content.decode()
    assert UNCONFIRMED not in response.content.decode()
    assert {
        key: kind.execution_state(value) for key, value in batch.agents.items()
    } == before
    start.assert_not_called()


@pytest.mark.parametrize(
    "case", ["unknown", "foreign", "bad_concurrency", "unknown_field"]
)
def test_invalid_selected_batch_never_partially_starts(batch, console_flag, case):
    body = {"experiment_ids": list(batch.agents)}
    expected_status = 404
    if case in {"unknown", "foreign"}:
        body["experiment_ids"].append(
            str(uuid4() if case == "unknown" else batch.foreign.pk)
        )
    else:
        body["max_concurrent_rows" if case == "bad_concurrency" else "unexpected"] = 0
        expected_status = 400
    before = {key: kind.execution_state(value) for key, value in batch.agents.items()}
    with patch(f"{old.CLIENT}.start_workflow_sync") as start:
        response = rerun(batch, body)
    assert response.status_code == expected_status, response.content
    if expected_status == 404:
        assert (
            response.json() == GeneralMethods().not_found("Experiment not found").data
        )
    assert {
        key: kind.execution_state(value) for key, value in batch.agents.items()
    } == before
    start.assert_not_called()


def test_duplicate_selection_starts_each_identity_once(batch, console_flag):
    def transport(**kwargs):
        return SimpleNamespace(id=kwargs["workflow_id"])

    with patch(f"{old.CLIENT}.start_workflow_sync", side_effect=transport) as start:
        response = rerun(batch, {"experiment_ids": list(batch.agents) * 2})
    assert response.status_code == 200, response.content
    assert response.json() == GeneralMethods().success_response(SUCCESS).data
    assert start.call_count == len(batch.agents)
    assert {call.kwargs["workflow_id"] for call in start.call_args_list} == {
        f"experiment-{experiment_id}" for experiment_id in batch.agents
    }
