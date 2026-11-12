"""Target-kind contract: real routed requests and persisted worker bindings.

Prepared before production changes; NOT a runtime receipt. Only dispatch is
stubbed. GraphVersion, FK, membership, history and cell writes remain real.
An unknown Console version is represented by an unmapped UUID, not a fabricated
Console model or a caller-supplied kind flag.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import ActivityEnvironment

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agent_playground.models import GraphExecution, GraphVersion
from model_hub.models.develop_dataset import Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.tests import test_experiment_agent_target_admission as old
from model_hub.tests import test_experiment_native_graph as native
from tfc.constants.levels import Level

tenants = old.tenants
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(params=[False, True], ids=["console_off", "console_on"])
def console_flag(request, settings):
    settings.DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED = request.param
    return request.param


def unavailable(agent, actor, case, foreign=None):
    """Persist real revocation/binding changes, never bypass the FK constraints."""
    if case in {"deleted_graph", "deleted_version", "deleted_eac", "deleted_run"}:
        obj = {
            "deleted_graph": agent.graph,
            "deleted_version": agent.version,
            "deleted_eac": agent.eac,
            "deleted_run": agent.edt,
        }[case]
        type(obj)._base_manager.filter(pk=obj.pk).update(deleted=True)
    elif case in {"draft", "inactive"}:
        GraphVersion._base_manager.filter(pk=agent.version.pk).update(status=case)
    elif case == "revoked_org":
        OrganizationMembership._base_manager.filter(
            user=actor["user"], organization=actor["org"]
        ).update(is_active=False)
    elif case == "revoked_workspace":
        OrganizationMembership._base_manager.filter(
            user=actor["user"], organization=actor["org"]
        ).update(level=Level.MEMBER, role="Member")
        WorkspaceMembership._base_manager.filter(
            user=actor["user"], workspace=actor["workspace"]
        ).update(is_active=False)
    elif case == "inactive_workspace":
        Workspace._base_manager.filter(pk=actor["workspace"].pk).update(is_active=False)
    elif case == "inactive_user":
        type(actor["user"])._base_manager.filter(pk=actor["user"].pk).update(
            is_active=False
        )
    elif case == "wrong_org":
        type(agent.graph)._base_manager.filter(pk=agent.graph.pk).update(
            organization=foreign["org"], workspace=foreign["workspace"]
        )
    elif case == "wrong_workspace":
        workspace = Workspace.no_workspace_objects.create(
            organization=actor["org"],
            created_by=actor["user"],
            name="Non-default foreign scope",
            is_default=False,
        )
        type(agent.graph)._base_manager.filter(pk=agent.graph.pk).update(
            workspace=workspace
        )
    elif case == "mismatched_graph":
        graph, _, _ = native.make_graph(actor)
        type(agent.eac)._base_manager.filter(pk=agent.eac.pk).update(graph=graph)
    else:
        raise AssertionError(case)


def execution_state(agent):
    return (
        old.state(agent.experiment),
        GraphExecution._base_manager.count(),
        ExperimentRowBudget._base_manager.count(),
    )


def worker_args(agent):
    return (
        str(agent.row.id),
        str(agent.experiment.snapshot_dataset_id),
        str(agent.experiment.id),
        str(agent.version.id),
        str(agent.eac.id),
    )


def test_authorized_graph_create_and_full_rerun(tenants, console_flag):
    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    with patch(old.START, return_value="dispatched") as start:
        response = actor["client"].post(
            old.V2, native.payload(actor, graph, version), format="json"
        )
    assert response.status_code == 200, response.content
    config = old.ExperimentAgentConfig.no_workspace_objects.get(graph_version=version)
    assert config.graph_id == graph.id
    experiment = config.experiment_dataset.experiment
    assert experiment.snapshot_dataset.organization_id == actor["org"].id
    start.assert_called_once()
    with patch(old.START, return_value="rerun") as start:
        response = actor["client"].post(
            f"{old.V2}re-run/", {"experiment_ids": [str(experiment.id)]}, format="json"
        )
    assert response.status_code == 200, response.content
    start.assert_called_once()


@pytest.mark.parametrize("operation", ["create", "clone", "update"])
def test_unmapped_console_target_has_typed_refusal_and_no_writes(
    tenants, console_flag, operation
):
    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    body["prompt_config"][0]["agent_version"] = str(uuid4())
    if operation == "clone":
        body["name"] = "Cloned unmapped target"
    before = native.counts()
    with patch(old.START) as start:
        if operation == "update":
            response = actor["client"].put(
                f"{old.V2}{actor['exp']['experiment'].id}/",
                {"prompt_config": body["prompt_config"], "column_id": None},
                format="json",
            )
        else:
            response = actor["client"].post(old.V2, body, format="json")
    old.assert_refused(response)
    assert response.json()["message"] == (
        "Experiment target or execution context not found or unavailable."
    )
    assert native.counts() == before
    start.assert_not_called()


def test_unknown_and_foreign_versions_are_indistinguishable(tenants, console_flag):
    actor = tenants["a"]
    graph, version, _ = native.make_graph(tenants["b"])
    body = native.payload(actor, graph, version)
    with patch(old.START) as start:
        foreign = actor["client"].post(old.V2, body, format="json")
        body["prompt_config"][0]["agent_version"] = str(uuid4())
        missing = actor["client"].post(old.V2, body, format="json")
    old.assert_refused(foreign)
    assert foreign.json() == missing.json()
    assert str(graph.id) not in foreign.content.decode()
    start.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "not-a-uuid"])
def test_empty_or_malformed_new_targets_are_never_admitted(
    tenants, console_flag, value
):
    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    body["prompt_config"][0]["agent_version"] = value
    before = native.counts()
    with patch(old.START) as start:
        response = actor["client"].post(old.V2, body, format="json")
    assert response.status_code == 400
    assert native.counts() == before
    start.assert_not_called()


@pytest.mark.parametrize(
    "selection", ["full", "agent", "prompt_and_agent", "prompt", "eval", "column"]
)
def test_selective_client_preserves_legacy_targets(tenants, console_flag, selection):
    from tfc.temporal.experiments.client import start_experiment_v2_workflow

    mixed = old.mixed_experiment(tenants["a"])
    with patch(f"{old.CLIENT}.start_workflow_sync") as dispatch:
        start_experiment_v2_workflow(
            str(mixed.experiment.id), **old.selectors_for(mixed, selection)
        )
    dispatch.assert_called_once()


@pytest.mark.parametrize(
    "operation",
    ["resend", "remove", "change_version", "select_agent", "prompt_only", "eval_only"],
)
def test_update_and_cell_selection_contract(tenants, console_flag, operation):
    actor = tenants["a"]
    mixed = old.mixed_experiment(actor)
    if operation in {"resend", "remove", "change_version"}:
        entries = [old.resent_prompt(mixed), old.resent_agent(mixed.agent)]
        if operation == "remove":
            entries.pop()
        if operation == "change_version":
            _, version, _ = native.make_graph(actor)
            entries[1].update(
                agent_id=str(version.graph_id), agent_version=str(version.id)
            )
        with patch(old.START, return_value="updated") as dispatch:
            response = actor["client"].put(
                mixed.url, {"prompt_config": entries}, format="json"
            )
        assert response.status_code == 200, response.content
        if operation == "change_version":
            assert dispatch.call_args.kwargs["rerun_agent_config_ids"] == [
                str(mixed.agent.eac.id)
            ]
        else:
            dispatch.assert_not_called()
    elif operation == "eval_only":
        from tfc.temporal.experiments.client import start_rerun_cells_v2_workflow

        unavailable(mixed.agent, actor, "deleted_graph")
        with patch(f"{old.CLIENT}.start_workflow_sync") as dispatch:
            start_rerun_cells_v2_workflow(
                str(mixed.experiment.id),
                str(mixed.experiment.snapshot_dataset_id),
                agent_config_ids=[str(mixed.agent.eac.id)],
                eval_only=True,
            )
        dispatch.assert_called_once()
    else:
        run = (
            mixed.agent.edt
            if operation == "select_agent"
            else mixed.prompt_config.experiment_dataset
        )
        with patch(old.START_CELLS, return_value="rerun") as dispatch:
            response = actor["client"].post(
                f"{old.V2}{mixed.experiment.id}/rerun-cells/",
                {"source_ids": [str(run.id)]},
                format="json",
            )
        assert response.status_code == 200, response.content
        assert dispatch.call_args.kwargs["agent_config_ids"] == (
            [str(mixed.agent.eac.id)] if operation == "select_agent" else []
        )


@pytest.mark.parametrize(
    "case",
    [
        "deleted_graph",
        "deleted_version",
        "deleted_eac",
        "deleted_run",
        "draft",
        "revoked_org",
        "revoked_workspace",
        "inactive_workspace",
        "inactive_user",
        "wrong_org",
        "wrong_workspace",
        "mismatched_graph",
    ],
)
def test_worker_refusal_precedes_every_state_write(tenants, console_flag, case):
    from model_hub.services.experiment_target_admission import AgentTargetNotQualified
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )

    actor = tenants["a"]
    agent = old.stored_agent(actor)
    unavailable(agent, actor, case, tenants["b"])
    before = execution_state(agent)
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        with pytest.raises(AgentTargetNotQualified):
            _setup_agent_sync(
                str(agent.experiment.id),
                str(agent.experiment.snapshot_dataset_id),
                str(agent.eac.id),
            )
        result = _prepare_agent_row_sync(*worker_args(agent))
    assert result["status"] == "FAILED"
    assert old.CODE in result["error"]
    assert result["graph_execution_id"] == ""
    assert result["input_payload"] == {}
    assert execution_state(agent) == before


@pytest.mark.parametrize(
    "binding", ["experiment", "snapshot", "row", "version", "config"]
)
def test_worker_checks_input_binding_before_graph_and_budget(
    tenants, console_flag, binding
):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = old.stored_agent(tenants["a"])
    other = old.stored_agent(tenants["b"])
    args = list(worker_args(agent))
    index = {"row": 0, "snapshot": 1, "experiment": 2, "version": 3, "config": 4}[
        binding
    ]
    args[index] = worker_args(other)[index]
    before = execution_state(agent)
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        result = _prepare_agent_row_sync(*args)
    assert result["status"] == "FAILED"
    assert old.CODE in result["error"]
    assert execution_state(agent) == before


@pytest.mark.parametrize(
    "index", [0, 1, 2, 3], ids=["row", "dataset", "experiment", "version"]
)
@pytest.mark.parametrize("invalid", [None, "", "not-a-uuid"])
def test_malformed_worker_binding_is_not_an_omitted_check(
    tenants, console_flag, index, invalid
):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = old.stored_agent(tenants["a"])
    args = list(worker_args(agent))
    args[index] = invalid
    before = execution_state(agent)
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        result = _prepare_agent_row_sync(*args)
    assert result["status"] == "FAILED"
    assert old.CODE in result["error"]
    assert execution_state(agent) == before


@pytest.mark.parametrize(
    "empty_config", [False, True], ids=["current", "legacy_payload"]
)
def test_worker_authorized_legacy_setup_and_prepare_use_real_records(
    tenants, console_flag, empty_config
):
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )

    agent = old.stored_agent(tenants["a"])
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        setup = _setup_agent_sync(
            str(agent.experiment.id),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.eac.id),
        )
        assert setup["status"] == "READY"
        args = worker_args(agent)
        prepared = _prepare_agent_row_sync(*(args[:4] if empty_config else args))
    assert prepared["status"] == "READY", prepared
    execution = GraphExecution.no_workspace_objects.get(
        pk=prepared["graph_execution_id"]
    )
    assert execution.graph_version_id == agent.version.id
    assert str(agent.row.id) in setup["row_ids"]


def test_empty_legacy_config_is_not_authorization(tenants, console_flag):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = old.stored_agent(tenants["a"])
    _, foreign, _ = native.make_graph(tenants["b"])
    before = execution_state(agent)
    args = (*worker_args(agent)[:3], str(foreign.id))
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        result = _prepare_agent_row_sync(*args)
    assert result["status"] == "FAILED"
    assert old.CODE in result["error"]
    assert execution_state(agent) == before


async def test_async_worker_wrapper_keeps_typed_failure_without_retry(
    tenants, console_flag
):
    from tfc.temporal.experiments.activities import (
        prepare_agent_row_activity,
        setup_agent_activity,
    )
    from tfc.temporal.experiments.types import PrepareAgentRowInput, SetupAgentInput

    agent = await sync_to_async(old.stored_agent)(tenants["a"])
    await sync_to_async(unavailable)(agent, tenants["a"], "deleted_graph")
    before = await sync_to_async(execution_state)(agent)
    env = ActivityEnvironment()
    setup = await env.run(
        setup_agent_activity,
        SetupAgentInput(
            experiment_id=str(agent.experiment.id),
            dataset_id=str(agent.experiment.snapshot_dataset_id),
            experiment_agent_config_id=str(agent.eac.id),
        ),
    )
    row = await env.run(
        prepare_agent_row_activity,
        PrepareAgentRowInput(
            row_id=str(agent.row.id),
            dataset_id=str(agent.experiment.snapshot_dataset_id),
            experiment_id=str(agent.experiment.id),
            graph_version_id=str(agent.version.id),
            experiment_agent_config_id=str(agent.eac.id),
        ),
    )
    for result in (setup, row):
        assert result.status == "FAILED"
        assert old.CODE in result.error
    assert await sync_to_async(execution_state)(agent) == before


@override_settings(EXPERIMENT_RUN_CONTROL_ENABLED=True)
def test_resume_legacy_agent_checks_real_binding_then_signals(tenants, console_flag):
    agent = old.stored_agent(tenants["a"])
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
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    with patch(
        "model_hub.services.experiment_run_control.get_client",
        AsyncMock(return_value=client),
    ):
        response = tenants["a"]["client"].post(
            f"{old.V2}{agent.experiment.id}/resume/",
            {"run_ids": [str(agent.edt.id)]},
            format="json",
        )
    assert response.status_code == 202, response.content
    handle.signal.assert_awaited_once()
    assert handle.signal.call_args.args[:2] == ("set_run_stopped", False)


def test_setup_rejects_foreign_row_before_running_cells(tenants, console_flag):
    from model_hub.services.experiment_target_admission import AgentTargetNotQualified
    from tfc.temporal.experiments.activities import _setup_agent_sync

    agent = old.stored_agent(tenants["a"])
    foreign_row = Row.no_workspace_objects.get(
        pk=tenants["b"]["exp"]["snapshot_row"].pk
    )
    before = execution_state(agent)
    with (
        patch("tfc.temporal.experiments.activities.activity.logger"),
        pytest.raises(AgentTargetNotQualified),
    ):
        _setup_agent_sync(
            str(agent.experiment.id),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.eac.id),
            [str(foreign_row.id)],
        )
    assert execution_state(agent) == before


def test_historical_inactive_version_keeps_legacy_execution_contract(
    tenants, console_flag
):
    """v1.56.1 rejects draft, not historical published (inactive) versions."""
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = old.stored_agent(tenants["a"])
    GraphVersion.no_workspace_objects.filter(pk=agent.version.pk).update(
        status="inactive"
    )
    with patch(old.START, return_value="historical") as dispatch:
        response = tenants["a"]["client"].post(
            f"{old.V2}re-run/",
            {"experiment_ids": [str(agent.experiment.id)]},
            format="json",
        )
    assert response.status_code == 200, response.content
    dispatch.assert_called_once()
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        prepared = _prepare_agent_row_sync(*worker_args(agent))
    assert prepared["status"] == "READY", prepared


@pytest.mark.parametrize("operation", ["full", "cells", "resume"])
@override_settings(EXPERIMENT_RUN_CONTROL_ENABLED=True)
def test_revoked_target_is_refused_before_rerun_or_resume_mutation(
    tenants, console_flag, operation
):
    agent = old.stored_agent(tenants["a"])
    unavailable(agent, tenants["a"], "deleted_graph")
    before = execution_state(agent)
    routes = {
        "full": (f"{old.V2}re-run/", {"experiment_ids": [str(agent.experiment.id)]}),
        "cells": (
            f"{old.V2}{agent.experiment.id}/rerun-cells/",
            {"source_ids": [str(agent.edt.id)]},
        ),
        "resume": (
            f"{old.V2}{agent.experiment.id}/resume/",
            {"run_ids": [str(agent.edt.id)]},
        ),
    }
    url, body = routes[operation]
    with (
        patch(old.START) as start,
        patch(old.START_CELLS) as cells,
        patch(
            "model_hub.services.experiment_run_control.get_client",
            new_callable=AsyncMock,
        ) as client,
    ):
        response = tenants["a"]["client"].post(url, body, format="json")
    old.assert_refused(response)
    assert execution_state(agent) == before
    start.assert_not_called()
    cells.assert_not_called()
    client.assert_not_awaited()


def test_real_personal_credential_revocation_still_blocks_legacy_create(
    tenants, console_flag
):
    from rest_framework.test import APIClient

    from accounts.models import OrgApiKey
    from accounts.tests.personal_keys import personal_key_for

    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    key = personal_key_for(
        actor["user"],
        actor["org"],
        workspace=actor["workspace"],
        name="Synthetic admission test",
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(actor["workspace"].pk),
    )
    with patch(old.START, return_value="authenticated") as dispatch:
        positive = client.post(old.V2, body, format="json")
    assert positive.status_code == 200, positive.content
    dispatch.assert_called_once()
    OrgApiKey._base_manager.filter(pk=key.pk).update(deleted=True)
    before = native.counts()
    body["name"] = "After credential revocation"
    with patch(old.START) as dispatch:
        negative = client.post(old.V2, body, format="json")
    assert negative.status_code in (401, 403), negative.content
    assert native.counts() == before
    dispatch.assert_not_called()


async def test_async_cell_client_legacy_positive_and_cross_binding_refusal(
    tenants, console_flag
):
    from model_hub.services.experiment_target_admission import AgentTargetNotQualified
    from tfc.temporal.experiments.client import start_rerun_cells_v2_workflow_async

    agent = await sync_to_async(old.stored_agent)(tenants["a"])
    with patch(
        f"{old.CLIENT}.start_workflow_async", new_callable=AsyncMock
    ) as dispatch:
        await start_rerun_cells_v2_workflow_async(
            str(agent.experiment.id),
            str(agent.experiment.snapshot_dataset_id),
            agent_config_ids=[str(agent.eac.id)],
            row_ids=[str(agent.row.id)],
        )
        dispatch.assert_awaited_once()
        dispatch.reset_mock()
        with pytest.raises(AgentTargetNotQualified):
            await start_rerun_cells_v2_workflow_async(
                str(agent.experiment.id),
                str(tenants["b"]["exp"]["experiment"].snapshot_dataset_id),
                agent_config_ids=[str(agent.eac.id)],
            )
        dispatch.assert_not_awaited()
