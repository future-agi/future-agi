"""Older experiments on a dataset with no workspace can be re-run again.

Production saved an experiment's snapshot with the workspace of the request
that created it, which is the organization's default workspace, while the
source dataset kept no workspace (seeded demo data and datasets older than
workspaces). The rows below are stored in that shape with the ORM.

Each place a re-run compares the snapshot with its source is reached here:
agent admission (API, client gate, workers, resume), the prompt row status
the workflow reads, agent failure settlement, the failed-row evaluation
policy and per-row limits. Requests pass the real authenticator; only the
final Temporal transport, the provider call and activity logging are
stubbed. Prepared from source, not run by the author.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from django.test import override_settings
from temporalio.client import WorkflowExecutionStatus

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.personal_keys import session_client_for
from agent_playground.models import GraphExecution
from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.services import experiment_target_admission as admission
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.tests import test_experiment_agent_target_admission as old
from model_hub.tests import test_experiment_target_kind as kind
from model_hub.tests.test_experiment_target_inputs import add_input
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

console_flag = kind.console_flag
tenants = old.tenants
pytestmark = pytest.mark.django_db(transaction=True)

DISPATCH = f"{old.CLIENT}.start_workflow_sync"
ACTIVITIES = "tfc.temporal.experiments.activities"
LOGGER = f"{ACTIVITIES}.activity.logger"
PROVIDER_ROW = "model_hub.views.experiment_runner._process_row_impl"
LIMITS = {"tokens": 5}
FAILED_UPSTREAM = "Upstream prompt failed"


@pytest.fixture
def actors(tenants):
    # Requests below pass the real authenticator, not an injected scope.
    for actor in tenants.values():
        actor["client"].stop_workspace_injection()
    clear_workspace_context()
    yield tenants
    clear_workspace_context()


def sibling_workspace(actor):
    """A second, non-default workspace in the actor's organization."""
    return Workspace.no_workspace_objects.create(
        organization=actor["org"],
        created_by=actor["user"],
        name=f"Sibling {uuid4().hex[:8]}",
        is_default=False,
        is_active=True,
    )


def store_workspaces(actor, *, source, snapshot):
    """Store the workspaces of the experiment's source dataset and snapshot
    directly, the way the rows already sit in the database."""
    experiment = actor["exp"]["experiment"]
    Dataset._base_manager.filter(pk=experiment.dataset_id).update(workspace=source)
    Dataset._base_manager.filter(pk=experiment.snapshot_dataset_id).update(
        workspace=snapshot
    )
    experiment.refresh_from_db()
    actor["exp"]["dataset"].refresh_from_db()
    return experiment


def as_production_saved_it(actor):
    """The source has no workspace; the snapshot has the default workspace
    the creating request ran in."""
    assert actor["workspace"].is_default
    return store_workspaces(actor, source=None, snapshot=actor["workspace"])


def stored_workspaces(experiment):
    """(source workspace id, snapshot workspace id), read from the database."""
    rows = dict(
        Dataset._base_manager.filter(
            pk__in=[experiment.dataset_id, experiment.snapshot_dataset_id]
        ).values_list("pk", "workspace_id")
    )
    return rows[experiment.dataset_id], rows[experiment.snapshot_dataset_id]


def outside_scope(actors, shape):
    """Source and snapshot workspaces that are not one scope."""
    actor = actors["a"]
    if shape == "snapshot_in_non_default_workspace":
        return None, sibling_workspace(actor)
    if shape == "snapshot_in_other_organizations_default":
        return None, actors["b"]["workspace"]
    if shape == "two_real_workspaces":
        return actor["workspace"], sibling_workspace(actor)
    if shape == "two_real_workspaces_reversed":
        return sibling_workspace(actor), actor["workspace"]
    raise AssertionError(shape)


def in_scope(actors, shape):
    """Source and snapshot workspaces that are one scope."""
    actor = actors["a"]
    if shape == "production_saved":
        return None, actor["workspace"]
    if shape == "both_without_workspace":
        return None, None
    if shape == "same_workspace":
        return actor["workspace"], actor["workspace"]
    raise AssertionError(shape)


IN_SCOPE = ["production_saved", "both_without_workspace", "same_workspace"]
OUTSIDE_SCOPE = [
    "snapshot_in_non_default_workspace",
    "snapshot_in_other_organizations_default",
    "two_real_workspaces",
    "two_real_workspaces_reversed",
]


def set_run_policy(experiment, **policy):
    ExperimentsTable._base_manager.filter(pk=experiment.pk).update(run_policy=policy)
    return ExperimentsTable.no_workspace_objects.select_related(
        "dataset", "snapshot_dataset"
    ).get(pk=experiment.pk)


def route(agent, operation):
    if operation == "full":
        return f"{old.V2}re-run/", {"experiment_ids": [str(agent.experiment.pk)]}
    body = (
        {"cells": [{"column_id": str(agent.column.pk), "row_id": str(agent.row.pk)}]}
        if operation == "cell"
        else {"source_ids": [str(agent.edt.pk)]}
    )
    return f"{old.V2}{agent.experiment.pk}/rerun-cells/", body


def signed_in(actor, workspace=None):
    return session_client_for(
        actor["user"], actor["org"], workspace or actor["workspace"]
    )


def member_signed_in_to(actor, workspace):
    """A member (not an owner) of ``workspace`` only, signed in to it."""
    user = User.objects.create_user(
        email=f"rerun-scope-member-{uuid4().hex}@example.invalid",
        password="local-test-password",
        name="Workspace member",
        organization=actor["org"],
        organization_role=OrganizationRoles.MEMBER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=actor["org"],
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return session_client_for(user, actor["org"], workspace)


def assert_workers_refuse(agent):
    """The Temporal client gate, worker setup and row preparation all refuse,
    and none of them writes anything."""
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )
    from tfc.temporal.experiments.client import start_experiment_v2_workflow

    before = kind.execution_state(agent)
    with patch(DISPATCH) as dispatch:
        with pytest.raises(admission.AgentTargetNotQualified):
            start_experiment_v2_workflow(str(agent.experiment.pk))
        dispatch.assert_not_called()
    with patch(LOGGER):
        with pytest.raises(admission.AgentTargetNotQualified):
            _setup_agent_sync(
                str(agent.experiment.pk),
                str(agent.experiment.snapshot_dataset_id),
                str(agent.eac.pk),
            )
        prepared = _prepare_agent_row_sync(*kind.worker_args(agent))
    assert prepared["status"] == "FAILED", prepared
    assert prepared["error"] == f"{old.CODE}: {admission.MESSAGE}"
    assert kind.execution_state(agent) == before


def prompt_row_status(actor, *, output_status):
    """Run the classic prompt row activity with the provider call stubbed, and
    return the status it reports to the workflow for the stored output."""
    from tfc.temporal.experiments.activities import _process_row_sync

    exp = actor["exp"]
    Cell._base_manager.filter(pk=exp["output_cell"].pk).update(status=output_status)
    experiment = exp["experiment"]
    with (
        patch(PROVIDER_ROW, return_value={"status": "SUCCESS"}) as provider,
        patch(f"{ACTIVITIES}.close_old_connections"),
        patch(LOGGER),
    ):
        result = _process_row_sync(
            str(exp["snapshot_row"].pk),
            str(exp["output_column"].pk),
            str(experiment.snapshot_dataset_id),
            str(experiment.pk),
            [],
            "stub-model",
            {},
            None,
            None,
        )
    provider.assert_called_once()
    return result["status"]


def settle_failed_agent_row(agent):
    """Agent failure settlement for one row, as the workflow calls it after a
    graph-level failure on the classic path."""
    from tfc.temporal.experiments.activities import _create_error_agent_cells_sync

    node_id = str(uuid4())
    Column._base_manager.filter(pk=agent.column.pk).update(
        metadata={"node_id": node_id}
    )
    Cell._base_manager.filter(
        dataset_id=agent.experiment.snapshot_dataset_id,
        column=agent.column,
        row=agent.row,
    ).update(status=CellStatus.RUNNING.value)
    with patch(f"{ACTIVITIES}.close_old_connections"), patch(LOGGER):
        return _create_error_agent_cells_sync(
            str(agent.row.pk),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.experiment.pk),
            {node_id: str(agent.column.pk)},
            "Agent preparation or execution failed",
            experiment_agent_config_id=str(agent.eac.pk),
        )


def failed_row_policy_cells(actor):
    """Failed-row evaluation settlement under the zero error policy, as the
    workflow calls it after a prompt row fails."""
    from tfc.temporal.experiments.activities import _create_error_eval_cells_sync

    exp = actor["exp"]
    experiment = exp["experiment"]
    variant = ExperimentDatasetTable.no_workspace_objects.get(experiment=experiment)
    Column._base_manager.filter(pk=exp["output_column"].pk).update(
        source_id=str(variant.pk)
    )
    set_run_policy(experiment, error_policy="zero")
    with patch(f"{ACTIVITIES}.close_old_connections"), patch(LOGGER):
        return _create_error_eval_cells_sync(
            [str(exp["snapshot_row"].pk)],
            str(exp["output_column"].pk),
            str(experiment.snapshot_dataset_id),
            str(experiment.pk),
            [],
            FAILED_UPSTREAM,
        )


# --------------------------------------------------------------------------
# The defect, run end to end through the prompt row activity
# --------------------------------------------------------------------------


def test_passing_prompt_rows_are_reported_passed_on_an_experiment_production_saved(
    actors,
):
    """A prompt experiment on a dataset with no workspace, whose snapshot
    carries the default workspace as production saved it. The re-run is
    admitted, the provider writes a passing output for every row, and the row
    activity must report every row as passed to the workflow."""
    from tfc.temporal.experiments.activities import _process_row_sync

    actor = actors["a"]
    experiment = as_production_saved_it(actor)
    exp = actor["exp"]
    snapshot = experiment.snapshot_dataset
    column = exp["output_column"]
    rows = [exp["snapshot_row"]] + [
        Row.objects.create(dataset=snapshot, order=order) for order in (1, 2)
    ]
    for row in rows:
        Cell.objects.update_or_create(
            dataset=snapshot,
            column=column,
            row=row,
            defaults={"value": "", "status": CellStatus.RUNNING.value},
        )

    with patch(DISPATCH, return_value=SimpleNamespace(id="rerun")) as dispatch:
        response = signed_in(actor).post(
            f"{old.V2}re-run/", {"experiment_ids": [str(experiment.pk)]}, format="json"
        )
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()

    def provider_writes_a_passing_output(
        row_id, column_id, dataset_id, *args, **kwargs
    ):
        Cell._base_manager.filter(
            row_id=row_id, column_id=column_id, dataset_id=dataset_id, deleted=False
        ).update(value="provider answer", status=CellStatus.PASS.value)
        return {}

    with (
        patch(PROVIDER_ROW, side_effect=provider_writes_a_passing_output),
        patch(f"{ACTIVITIES}.close_old_connections"),
        patch(LOGGER),
    ):
        reported = {
            str(row.pk): _process_row_sync(
                str(row.pk),
                str(column.pk),
                str(snapshot.pk),
                str(experiment.pk),
                [],
                "stub-model",
                {},
                None,
                None,
            )["status"]
            for row in rows
        }

    # Every row passed ...
    assert set(
        Cell._base_manager.filter(
            dataset=snapshot, column=column, row__in=rows, deleted=False
        ).values_list("status", flat=True)
    ) == {CellStatus.PASS.value}
    # ... so every row is reported passed, and no row is relabelled.
    assert reported == {str(row.pk): "COMPLETED" for row in rows}
    assert stored_workspaces(experiment) == (None, actor["workspace"].pk)


# --------------------------------------------------------------------------
# Agent admission: the default workspace re-runs what production saved
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["full", "cell", "column"])
def test_default_member_reruns_an_agent_experiment_production_saved(
    actors, console_flag, operation
):
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )

    actor = actors["a"]
    as_production_saved_it(actor)
    agent = old.stored_agent(actor)
    add_input(agent)
    url, body = route(agent, operation)
    with patch(DISPATCH, return_value=SimpleNamespace(id="rerun")) as dispatch:
        response = signed_in(actor).post(url, body, format="json")
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()
    with patch(LOGGER):
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
    # Admission matched the pair; it did not relabel either row.
    assert stored_workspaces(agent.experiment) == (None, actor["workspace"].pk)


@override_settings(EXPERIMENT_RUN_CONTROL_ENABLED=True)
def test_default_member_resumes_an_agent_run_production_saved(tenants, console_flag):
    actor = tenants["a"]
    as_production_saved_it(actor)
    agent = old.stored_agent(actor)
    binding = ExperimentRunControl.objects.create(
        experiment_dataset=agent.edt,
        workflow_id="agent-run",
        workflow_run_id=uuid4(),
        config_id=agent.eac.id,
        kind="agent",
    )
    control = {
        "experiment_id": str(agent.experiment.id),
        "config_id": str(agent.eac.id),
        "workflow_run_id": str(binding.workflow_run_id),
        "status": "STOPPED",
    }
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        ),
        query=AsyncMock(return_value=control),
        signal=AsyncMock(),
    )
    temporal = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    with patch(
        "model_hub.services.experiment_run_control.get_client",
        AsyncMock(return_value=temporal),
    ):
        response = actor["client"].post(
            f"{old.V2}{agent.experiment.id}/resume/",
            {"run_ids": [str(agent.edt.id)]},
            format="json",
        )
    assert response.status_code == 202, response.content
    handle.signal.assert_awaited_once()
    assert handle.signal.call_args.args[:2] == ("set_run_stopped", False)


def test_prompt_only_rerun_production_saved_is_admitted_without_the_agent_check(
    actors,
):
    actor = actors["a"]
    experiment = as_production_saved_it(actor)
    with patch(DISPATCH, return_value=SimpleNamespace(id="rerun")) as dispatch:
        response = signed_in(actor).post(
            f"{old.V2}re-run/", {"experiment_ids": [str(experiment.pk)]}, format="json"
        )
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()


@pytest.mark.parametrize("shape", ["both_without_workspace", "same_workspace"])
@pytest.mark.parametrize("operation", ["full", "cell", "column"])
def test_new_snapshots_are_admitted_as_before(actors, shape, operation):
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )

    actor = actors["a"]
    source, snapshot = in_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    agent = old.stored_agent(actor)
    add_input(agent)
    url, body = route(agent, operation)
    with patch(DISPATCH, return_value=SimpleNamespace(id="rerun")) as dispatch:
        response = signed_in(actor).post(url, body, format="json")
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()
    with patch(LOGGER):
        setup = _setup_agent_sync(
            str(agent.experiment.pk),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.eac.pk),
        )
        prepared = _prepare_agent_row_sync(*kind.worker_args(agent))
    assert setup["status"] == "READY", setup
    assert prepared["status"] == "READY", prepared
    expected = None if source is None else source.pk
    assert stored_workspaces(agent.experiment) == (expected, expected)


@pytest.mark.parametrize("shape", OUTSIDE_SCOPE[:3])
@pytest.mark.parametrize("operation", ["full", "cell", "column"])
def test_default_member_is_refused_when_the_snapshot_is_outside_the_source_scope(
    actors, shape, operation
):
    actor = actors["a"]
    source, snapshot = outside_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    agent = old.stored_agent(actor)
    before = kind.execution_state(agent)
    url, body = route(agent, operation)
    with patch(DISPATCH) as dispatch:
        response = signed_in(actor).post(url, body, format="json")
    old.assert_refused(response)
    assert response.json() == admission.refusal_response().data
    assert kind.execution_state(agent) == before
    dispatch.assert_not_called()


@pytest.mark.parametrize("shape", OUTSIDE_SCOPE)
def test_workers_refuse_a_snapshot_outside_the_source_scope(actors, shape):
    actor = actors["a"]
    source, snapshot = outside_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    agent = old.stored_agent(actor)
    assert_workers_refuse(agent)
    assert stored_workspaces(agent.experiment) == (
        None if source is None else source.pk,
        snapshot.pk,
    )


def test_source_without_workspace_in_another_organization_is_refused(actors):
    a, b = actors["a"], actors["b"]
    as_production_saved_it(a)
    # Organization A's snapshot, now paired with organization B's dataset
    # that has no workspace.
    foreign = b["exp"]["dataset"]
    Dataset._base_manager.filter(pk=foreign.pk).update(workspace=None)
    ExperimentsTable._base_manager.filter(pk=a["exp"]["experiment"].pk).update(
        dataset=foreign
    )
    a["exp"]["experiment"].refresh_from_db()
    agent = old.stored_agent(a)
    before = kind.execution_state(agent)
    url, body = route(agent, "full")
    # Organization B's default workspace sees the experiment through its
    # source, and is refused.
    with patch(DISPATCH) as dispatch:
        response = signed_in(b).post(url, body, format="json")
    old.assert_refused(response)
    dispatch.assert_not_called()
    # Organization A no longer sees it at all.
    with patch(DISPATCH) as dispatch:
        response = signed_in(a).post(url, body, format="json")
    assert response.status_code == 404, response.content
    dispatch.assert_not_called()
    assert kind.execution_state(agent) == before
    assert_workers_refuse(agent)


def test_non_default_workspace_member_cannot_rerun_what_production_saved(actors):
    actor = actors["a"]
    as_production_saved_it(actor)
    agent = old.stored_agent(actor)
    before = kind.execution_state(agent)
    client = member_signed_in_to(actor, sibling_workspace(actor))
    url, body = route(agent, "full")
    with patch(DISPATCH) as dispatch:
        response = client.post(url, body, format="json")
    assert response.status_code == 404, response.content
    dispatch.assert_not_called()
    assert kind.execution_state(agent) == before


# --------------------------------------------------------------------------
# Prompt row status: a passed row is reported COMPLETED, not silently FAILED
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", IN_SCOPE)
def test_prompt_row_that_passed_is_reported_completed(actors, shape):
    actor = actors["a"]
    source, snapshot = in_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    assert prompt_row_status(actor, output_status=CellStatus.PASS.value) == "COMPLETED"


@pytest.mark.parametrize("shape", OUTSIDE_SCOPE)
def test_prompt_row_outside_the_source_scope_is_still_reported_failed(actors, shape):
    actor = actors["a"]
    source, snapshot = outside_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    assert prompt_row_status(actor, output_status=CellStatus.PASS.value) == "FAILED"


def test_prompt_row_that_failed_is_still_reported_failed(actors):
    actor = actors["a"]
    as_production_saved_it(actor)
    assert prompt_row_status(actor, output_status=CellStatus.ERROR.value) == "FAILED"


# --------------------------------------------------------------------------
# Agent failure settlement: a failed agent row is written as ERROR
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", IN_SCOPE)
def test_failed_agent_row_is_settled(actors, shape):
    actor = actors["a"]
    source, snapshot = in_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    agent = old.stored_agent(actor)
    result = settle_failed_agent_row(agent)
    assert result == {"cells_created": 1, "status": "SUCCESS"}, result
    cell = Cell._base_manager.get(
        dataset_id=agent.experiment.snapshot_dataset_id,
        column=agent.column,
        row=agent.row,
    )
    assert cell.status == CellStatus.ERROR.value
    assert cell.value == "Agent preparation or execution failed"


@pytest.mark.parametrize("shape", OUTSIDE_SCOPE)
def test_agent_settlement_outside_the_source_scope_is_still_refused(actors, shape):
    actor = actors["a"]
    source, snapshot = outside_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    agent = old.stored_agent(actor)
    result = settle_failed_agent_row(agent)
    assert result["status"] == "FAILED", result
    assert result["error"] == "agent settlement snapshot or row is unavailable"
    cell = Cell._base_manager.get(
        dataset_id=agent.experiment.snapshot_dataset_id,
        column=agent.column,
        row=agent.row,
    )
    assert cell.status == CellStatus.RUNNING.value


# --------------------------------------------------------------------------
# Failed-row evaluation policy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", IN_SCOPE)
def test_failed_row_policy_is_applied(actors, shape):
    actor = actors["a"]
    source, snapshot = in_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    result = failed_row_policy_cells(actor)
    assert result == {"cells_created": 0, "status": "COMPLETED"}, result


@pytest.mark.parametrize("shape", OUTSIDE_SCOPE)
def test_failed_row_policy_outside_the_source_scope_is_still_refused(actors, shape):
    actor = actors["a"]
    source, snapshot = outside_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    result = failed_row_policy_cells(actor)
    assert result["status"] == "FAILED", result
    assert (
        result["error"] == "Error policy target does not match the experiment snapshot."
    )


# --------------------------------------------------------------------------
# Per-row limits
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", IN_SCOPE)
def test_prompt_row_budget_is_reserved(actors, shape):
    from model_hub.services.experiment_row_budget import budget_for_prompt

    actor = actors["a"]
    source, snapshot = in_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    exp = actor["exp"]
    experiment = set_run_policy(exp["experiment"], limits=LIMITS)
    budget = budget_for_prompt(
        experiment, str(exp["snapshot_row"].pk), str(exp["output_column"].pk)
    )
    assert budget is not None
    assert str(budget.experiment_id) == str(experiment.pk)
    assert str(budget.row_id) == str(exp["snapshot_row"].pk)
    assert budget.limits == LIMITS


@pytest.mark.parametrize("shape", OUTSIDE_SCOPE)
def test_prompt_row_budget_outside_the_source_scope_is_still_refused(actors, shape):
    from model_hub.services.experiment_row_budget import budget_for_prompt

    actor = actors["a"]
    source, snapshot = outside_scope(actors, shape)
    store_workspaces(actor, source=source, snapshot=snapshot)
    exp = actor["exp"]
    experiment = set_run_policy(exp["experiment"], limits=LIMITS)
    before = ExperimentRowBudget.objects.count()
    with pytest.raises(ValueError, match="Budget dataset does not match"):
        budget_for_prompt(
            experiment, str(exp["snapshot_row"].pk), str(exp["output_column"].pk)
        )
    assert ExperimentRowBudget.objects.count() == before


def test_agent_row_budget_is_bound_on_a_rerun_production_saved(actors):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    actor = actors["a"]
    as_production_saved_it(actor)
    agent = old.stored_agent(actor)
    add_input(agent)
    set_run_policy(agent.experiment, limits=LIMITS)
    with patch(LOGGER):
        prepared = _prepare_agent_row_sync(*kind.worker_args(agent))
    assert prepared["status"] == "READY", prepared
    budget = ExperimentRowBudget.objects.get(experiment=agent.experiment, row=agent.row)
    assert str(budget.graph_execution_id) == str(prepared["graph_execution_id"])
    assert budget.limits == LIMITS


# --------------------------------------------------------------------------
# Snapshot identity capture is never reached by an older experiment
# --------------------------------------------------------------------------


def test_older_experiment_never_reaches_snapshot_identity_capture(actors):
    from model_hub.services.dataset_snapshot import create_dataset_snapshot

    actor = actors["a"]
    experiment = as_production_saved_it(actor)
    with patch(
        "model_hub.services.native_run_binding.persist_snapshot_identity"
    ) as capture:
        with pytest.raises(RunWriteError, match="already has a snapshot"):
            create_dataset_snapshot(experiment.dataset, experiment)
    capture.assert_not_called()
    assert stored_workspaces(experiment) == (None, actor["workspace"].pk)
