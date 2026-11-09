"""RL1891 r2 source regressions for API/worker execution-scope agreement.

NOT_RUN locally. Routed authentication and all ORM/signals/admission are real;
only final Temporal transport and activity logging are stubbed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from temporalio.service import RPCError, RPCStatusCode

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.personal_keys import session_client_for
from agent_playground.models import GraphExecution
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.services import experiment_target_admission as admission
from model_hub.services.experiment_submit import state_of
from model_hub.tests import test_experiment_agent_target_admission as old
from model_hub.tests import test_experiment_native_graph as native
from model_hub.tests import test_experiment_target_kind as kind
from model_hub.tests.test_experiment_target_inputs import add_input
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

console_flag = kind.console_flag
tenants = old.tenants
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def actors(tenants):
    # Remove the existing force-auth fixture's global scope injection. These
    # requests must pass the real authenticator, not a stand-in request scope.
    for actor in tenants.values():
        actor["client"].stop_workspace_injection()
    clear_workspace_context()
    yield tenants
    clear_workspace_context()


def null_workspace(actor):
    experiment = actor["exp"]["experiment"]
    Dataset._base_manager.filter(
        pk__in=[experiment.dataset_id, experiment.snapshot_dataset_id]
    ).update(workspace=None)
    experiment.refresh_from_db()
    actor["exp"]["dataset"].refresh_from_db()


def route(agent, operation):
    if operation == "full":
        return f"{old.V2}re-run/", {"experiment_ids": [str(agent.experiment.pk)]}
    body = (
        {"cells": [{"column_id": str(agent.column.pk), "row_id": str(agent.row.pk)}]}
        if operation == "cell"
        else {"source_ids": [str(agent.edt.pk)]}
    )
    return f"{old.V2}{agent.experiment.pk}/rerun-cells/", body


def operator_client(actor):
    user = User.objects.create_user(
        email=f"experiment-operator-{uuid4().hex}@example.invalid",
        password="local-test-password",
        name="Experiment operator",
        organization=actor["org"],
        organization_role=OrganizationRoles.OWNER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=actor["org"],
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=actor["workspace"],
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
    )
    return user, session_client_for(user, actor["org"], actor["workspace"])


def dependent_outputs(agent):
    for source in (
        old.SourceChoices.EXPERIMENT_EVALUATION.value,
        old.SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
    ):
        column = Column.objects.create(
            dataset=agent.experiment.snapshot_dataset,
            name=f"Dependent {source}",
            source=source,
            source_id=f"{agent.edt.pk}-sourceid-{uuid4()}",
            data_type=old.DataTypeChoices.TEXT.value,
            status=old.StatusType.COMPLETED.value,
        )
        Cell.objects.create(
            dataset=agent.experiment.snapshot_dataset,
            column=column,
            row=agent.row,
            value=f"retained {source}",
            status=old.CellStatus.PASS.value,
        )


@pytest.mark.parametrize("version_status", ["active", "inactive"])
def test_null_workspace_create_reaches_real_durable_admission(
    actors, console_flag, version_status
):
    actor = actors["a"]
    null_workspace(actor)
    source_cell = Cell.objects.get(row=actor["exp"]["row"])
    Column.objects.filter(pk=source_cell.column_id).update(name="query")
    graph, version, _ = native.make_graph(actor, status=version_status)
    body = native.payload(actor, graph, version)
    client = session_client_for(actor["user"], actor["org"], actor["workspace"])
    handle = SimpleNamespace(
        describe=AsyncMock(
            side_effect=RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
        )
    )

    async def started(*args, **kwargs):
        return SimpleNamespace(id=kwargs["id"])

    temporal = SimpleNamespace(
        get_workflow_handle=Mock(return_value=handle),
        start_workflow=AsyncMock(side_effect=started),
    )
    with patch(
        "tfc.temporal.experiments.admission.get_client",
        AsyncMock(return_value=temporal),
    ):
        response = client.post(old.V2, body, format="json")
    assert response.status_code == 200, response.content
    eac = old.ExperimentAgentConfig.no_workspace_objects.get(graph_version=version)
    experiment = eac.experiment_dataset.experiment
    assert experiment.dataset.workspace_id is None
    assert experiment.snapshot_dataset.workspace_id is None
    assert state_of(experiment)["state"] == "accepted"
    temporal.start_workflow.assert_awaited_once()
    dispatch = temporal.start_workflow.call_args
    assert dispatch.args[1].experiment_id == str(experiment.pk)
    assert dispatch.kwargs["memo"] == {
        "experiment_submission_id": state_of(experiment)["admission_id"]
    }
    snapshot_cell = Cell.objects.get(
        row__dataset=experiment.snapshot_dataset, column__name="query"
    )
    assert snapshot_cell.value == source_cell.value


@pytest.mark.parametrize("operation", ["full", "cell", "column"])
@pytest.mark.parametrize("version_status", ["active", "inactive"])
def test_null_workspace_rerun_setup_and_prepare_keep_real_inputs(
    actors, console_flag, operation, version_status
):
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )

    actor = actors["a"]
    null_workspace(actor)
    agent = old.stored_agent(actor)
    type(agent.version).no_workspace_objects.filter(pk=agent.version.pk).update(
        status=version_status
    )
    add_input(agent)
    client = session_client_for(actor["user"], actor["org"], actor["workspace"])
    url, body = route(agent, operation)
    with patch(
        f"{old.CLIENT}.start_workflow_sync", return_value=SimpleNamespace(id="rerun")
    ) as dispatch:
        response = client.post(url, body, format="json")
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        setup = _setup_agent_sync(
            str(agent.experiment.pk),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.eac.pk),
        )
        prepared = _prepare_agent_row_sync(*kind.worker_args(agent))
    assert setup["status"] == "READY", setup
    assert str(agent.row.pk) in setup["row_ids"]
    assert prepared["status"] == "READY", prepared
    execution = GraphExecution.no_workspace_objects.get(
        pk=prepared["graph_execution_id"]
    )
    assert (
        prepared["input_payload"]
        == execution.input_payload
        == {"query": "native input"}
    )
    assert execution.graph_version_id == agent.version.pk
    assert not Dataset._base_manager.filter(
        pk__in=[agent.experiment.dataset_id, agent.experiment.snapshot_dataset_id],
        workspace__isnull=False,
    ).exists()


@pytest.mark.parametrize(
    "case",
    [
        "missing_default",
        "deleted_default",
        "inactive_default",
        "revoked_org",
        "revoked_workspace",
        "inactive_user",
        "deleted_user",
        "missing_owner",
        "lost_nonnull_workspace",
    ],
)
def test_execution_scope_refuses_without_healing_or_borrowing_ambient_scope(
    actors, console_flag, case
):
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )
    from tfc.temporal.experiments.client import start_experiment_v2_workflow

    actor = actors["a"]
    if case != "lost_nonnull_workspace":
        null_workspace(actor)
    agent = old.stored_agent(actor)
    add_input(agent)
    if case == "missing_default":
        Workspace._base_manager.filter(pk=actor["workspace"].pk).update(
            is_default=False
        )
    elif case in {"deleted_default", "inactive_default"}:
        Workspace._base_manager.filter(pk=actor["workspace"].pk).update(
            **({"deleted": True} if case == "deleted_default" else {"is_active": False})
        )
    elif case == "missing_owner":
        old.ExperimentsTable._base_manager.filter(pk=agent.experiment.pk).update(
            user=None
        )
    elif case == "deleted_user":
        User._base_manager.filter(pk=actor["user"].pk).update(deleted=True)
    elif case == "lost_nonnull_workspace":
        Workspace._base_manager.filter(pk=actor["workspace"].pk).update(
            is_default=False, deleted=True
        )
        Workspace.no_workspace_objects.create(
            organization=actor["org"],
            created_by=actor["user"],
            name="Other default",
            is_default=True,
        )
    else:
        kind.unavailable(agent, actor, case)
    before = kind.execution_state(agent)
    workspaces_before = list(Workspace._base_manager.order_by("pk").values())
    foreign = actors["b"]
    set_workspace_context(
        workspace=foreign["workspace"],
        organization=foreign["org"],
        user=foreign["user"],
    )
    try:
        with patch(f"{old.CLIENT}.start_workflow_sync") as dispatch:
            with pytest.raises(admission.AgentTargetNotQualified):
                start_experiment_v2_workflow(str(agent.experiment.pk))
            dispatch.assert_not_called()
        with patch("tfc.temporal.experiments.activities.activity.logger"):
            with pytest.raises(admission.AgentTargetNotQualified):
                _setup_agent_sync(
                    str(agent.experiment.pk),
                    str(agent.experiment.snapshot_dataset_id),
                    str(agent.eac.pk),
                )
            prepared = _prepare_agent_row_sync(*kind.worker_args(agent))
    finally:
        clear_workspace_context()
    assert prepared["status"] == "FAILED", prepared
    assert prepared["error"] == f"{old.CODE}: {admission.MESSAGE}"
    assert kind.execution_state(agent) == before
    assert list(Workspace._base_manager.order_by("pk").values()) == workspaces_before


@pytest.mark.parametrize("operation", ["full", "cell", "column"])
@pytest.mark.parametrize("legacy_dataset", [False, True])
@pytest.mark.parametrize(
    "revocation", ["revoked_org", "revoked_workspace", "inactive_user"]
)
def test_operator_cannot_mutate_rerun_when_execution_owner_is_revoked(
    actors, console_flag, operation, legacy_dataset, revocation
):
    actor = actors["a"]
    if legacy_dataset:
        null_workspace(actor)
    agent = old.stored_agent(actor)
    dependent_outputs(agent)
    operator, client = operator_client(actor)
    assert operator.pk != agent.experiment.user_id
    kind.unavailable(agent, actor, revocation)
    assert operator.can_access_workspace(actor["workspace"])
    before = kind.execution_state(agent)
    url, body = route(agent, operation)
    # Do not replace either domain starter: if API preflight misses U1's
    # revocation the actual client will refuse AFTER the API reset writes.
    with patch(f"{old.CLIENT}.start_workflow_sync") as dispatch:
        response = client.post(url, body, format="json")
    old.assert_refused(response)
    assert response.json() == admission.refusal_response().data
    assert kind.execution_state(agent) == before
    dispatch.assert_not_called()


@pytest.mark.parametrize("operation", ["full", "cell", "column"])
def test_second_authorized_operator_reaches_real_client_dispatch(
    actors, console_flag, operation
):
    actor = actors["a"]
    agent = old.stored_agent(actor)
    dependent_outputs(agent)
    _, client = operator_client(actor)
    url, body = route(agent, operation)
    with patch(
        f"{old.CLIENT}.start_workflow_sync",
        return_value=SimpleNamespace(id="operator-run"),
    ) as dispatch:
        response = client.post(url, body, format="json")
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()


@pytest.mark.parametrize("operation", ["full", "cell", "column"])
def test_revoked_current_caller_is_not_replaced_by_valid_owner(
    actors, console_flag, operation
):
    actor = actors["a"]
    agent = old.stored_agent(actor)
    operator, client = operator_client(actor)
    OrganizationMembership._base_manager.filter(
        user=operator, organization=actor["org"]
    ).update(is_active=False)
    before = kind.execution_state(agent)
    url, body = route(agent, operation)
    with patch(f"{old.CLIENT}.start_workflow_sync") as dispatch:
        response = client.post(url, body, format="json")
    assert response.status_code in (401, 403), response.content
    assert kind.execution_state(agent) == before
    dispatch.assert_not_called()
