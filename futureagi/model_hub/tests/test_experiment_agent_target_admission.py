"""Migration of TX1-B controls: only unavailable targets are refused.

The old blanket-OFF negative cases now use unmapped incoming versions or a
revoked stored graph. Their no-write/no-dispatch assertions remain intact.
Authorized GraphVersion positives under both flags are in test_experiment_target_kind.
Prompt/model, evaluator-only and unchanged valid-config controls remain intact.
"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from agent_playground.models import GraphVersion
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.experiments import (
    ExperimentAgentConfig,
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.tests import test_experiment_append_variant as variants
from model_hub.tests import test_experiment_native_graph as native
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy

tenants = tenancy.tenants

SETTING = "DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED"
OFF = {SETTING: False}
ON = {SETTING: True}
CODE = "tx1b_not_qualified"
V2 = native.V2
START = native.START
START_CELLS = "tfc.temporal.experiments.start_rerun_cells_v2_workflow"
CLIENT = "tfc.temporal.experiments.client"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def assert_refused(response):
    assert response.status_code == 403, response.content
    assert response.json()["code"] == CODE, response.content


def assert_refusal(error):
    assert error.status_code == 403
    assert error.get_codes() == CODE


def stored_agent(actor, exp=None):
    """Store an agent graph target on a tenancy experiment with the ORM, as the
    create API leaves it, so no request serializer is involved."""
    exp = exp or actor["exp"]
    experiment = exp["experiment"]
    graph, version, _ = native.make_graph(actor)
    edt = ExperimentDatasetTable.no_workspace_objects.create(
        name="Agent target",
        experiment=experiment,
        status=StatusType.COMPLETED.value,
    )
    eac = ExperimentAgentConfig.no_workspace_objects.create(
        experiment_dataset=edt,
        graph=graph,
        graph_version=version,
        name="Agent target",
        order=1,
    )
    column = Column.objects.create(
        name="Agent output",
        dataset=experiment.snapshot_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(edt.id),
        status=StatusType.COMPLETED.value,
    )
    edt.columns.add(column)
    Cell.objects.create(
        dataset=experiment.snapshot_dataset,
        column=column,
        row=exp["snapshot_row"],
        value="stored agent output",
        status=CellStatus.PASS.value,
    )
    return SimpleNamespace(
        experiment=experiment,
        graph=graph,
        version=version,
        edt=edt,
        eac=eac,
        column=column,
        row=exp["snapshot_row"],
    )


def revoke_graph(agent):
    """A real stored target whose graph is no longer available."""
    type(agent.graph)._base_manager.filter(pk=agent.graph.pk).update(deleted=True)
    return agent


def mixed_experiment(actor):
    """The tenancy experiment with one stored prompt config and one stored
    agent graph target."""
    experiment, prompt_config, template, version, url = variants.prepare(actor)
    return SimpleNamespace(
        experiment=experiment,
        prompt_config=prompt_config,
        template=template,
        version=version,
        url=url,
        agent=stored_agent(actor),
    )


def resent_prompt(mixed, **changes):
    """The stored prompt config exactly as the editor resends it on PUT."""
    stored = mixed.prompt_config
    return {
        **variants.entry(mixed.template, mixed.version, model=stored.model),
        "id": str(stored.id),
        "name": stored.name,
        **changes,
    }


def resent_agent(agent, **changes):
    """The stored agent config exactly as the editor resends it on PUT."""
    return {
        "id": str(agent.eac.id),
        "agent_id": str(agent.graph.id),
        "agent_version": str(agent.version.id),
        **changes,
    }


def state(experiment):
    """Every row a refused request could write for this experiment, read with
    the plain base manager (no workspace or soft-delete filtering)."""
    snapshot_id = experiment.snapshot_dataset_id
    return (
        native.counts(),
        ExperimentsTable._base_manager.values("status", "column_id").get(
            id=experiment.id
        ),
        sorted(
            ExperimentAgentConfig._base_manager.filter(
                experiment_dataset__experiment_id=experiment.id
            ).values_list("id", "graph_id", "graph_version_id", "name", "deleted")
        ),
        sorted(
            ExperimentPromptConfig._base_manager.filter(
                experiment_dataset__experiment_id=experiment.id
            ).values_list("id", "model", "prompt_version_id", "deleted")
        ),
        sorted(
            ExperimentDatasetTable._base_manager.filter(
                experiment_id=experiment.id
            ).values_list("id", "status", "name", "deleted")
        ),
        sorted(
            Column._base_manager.filter(dataset_id=snapshot_id).values_list(
                "id", "status", "deleted"
            )
        ),
        sorted(
            Cell._base_manager.filter(dataset_id=snapshot_id).values_list(
                "id", "status", "value", "deleted"
            )
        ),
    )


def stage_full_run(experiment):
    """Durable intent for a full run, as the create path stages it."""
    from model_hub.services.experiment_submit import stage_submission

    with transaction.atomic():
        stage_submission(
            experiment, {"name": experiment.name}, "create", max_concurrent_rows=10
        )


# --------------------------------------------------------------------------
# Setting: an env-backed bool, OFF unless explicitly true
# --------------------------------------------------------------------------


def test_setting_is_read_from_env_and_off_by_default():
    raw = os.environ.get(SETTING, "false")
    assert getattr(settings, SETTING) is (raw.lower() in ("true", "1", "yes"))


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["missing", False, None, "true", 1])
def test_non_true_settings_never_admit_an_unmapped_target(tenants, value):
    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    body["prompt_config"][0]["agent_version"] = str(uuid.uuid4())
    before = native.counts()
    with override_settings(), patch(START) as start:
        if value == "missing":
            if hasattr(settings, SETTING):
                delattr(settings, SETTING)
        else:
            setattr(settings, SETTING, value)
        response = actor["client"].post(V2, body, format="json")
    assert_refused(response)
    assert native.counts() == before
    start.assert_not_called()


# --------------------------------------------------------------------------
# POST create (REST and the MCP create_experiment tool)
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("shape", ["graph_only", "mixed"])
@override_settings(**OFF)
def test_create_refuses_unmapped_target_before_any_write(tenants, shape):
    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    body["prompt_config"][0]["agent_version"] = str(uuid.uuid4())
    if shape == "mixed":
        template, prompt_version = variants.prompt(actor, "mixed prompt")
        body["prompt_config"].insert(0, variants.entry(template, prompt_version))
    before = native.counts()
    with patch(START) as start:
        response = actor["client"].post(V2, body, format="json")
    assert_refused(response)
    assert native.counts() == before
    start.assert_not_called()


@pytest.mark.django_db
@override_settings(**OFF)
def test_mcp_create_experiment_tool_is_refused(tenants):
    from mcp_server.api_executor import APIExecutionError, MCPRequestContext, executor
    from mcp_server.generated_registry import registry

    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    body = native.payload(actor, graph, version)
    body["prompt_config"][0]["agent_version"] = str(uuid.uuid4())
    context = MCPRequestContext(
        user=actor["user"], organization=actor["org"], workspace=actor["workspace"]
    )
    before = native.counts()
    with patch(START) as start, pytest.raises(APIExecutionError) as info:
        executor.execute_sync(registry.get("create_experiment"), body, context)
    assert info.value.status_code == 403
    assert info.value.data["code"] == CODE
    assert native.counts() == before
    start.assert_not_called()


# --------------------------------------------------------------------------
# PUT: refuse only NEW or CHANGED graph targets (diff-aware)
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    ["add_to_prompt_only", "add_to_mixed", "change_version", "change_graph", "new_id"],
)
@override_settings(**OFF)
def test_update_refuses_new_or_changed_graph_target_before_any_write(tenants, case):
    actor = tenants["a"]
    if case == "add_to_prompt_only":
        experiment, prompt_config, template, version, url = variants.prepare(actor)
        mixed = SimpleNamespace(
            experiment=experiment,
            prompt_config=prompt_config,
            template=template,
            version=version,
            url=url,
        )
        entries = [resent_prompt(mixed)]
    else:
        mixed = mixed_experiment(actor)
        entries = [resent_prompt(mixed), resent_agent(mixed.agent)]
    graph, new_version, _ = native.make_graph(actor)
    new_entry = {"agent_id": str(graph.id), "agent_version": str(new_version.id)}
    if case in ("add_to_prompt_only", "add_to_mixed"):
        entries.append(new_entry)
    elif case == "change_version":
        later = GraphVersion.no_workspace_objects.create(
            graph=mixed.agent.graph, version_number=2, status="inactive"
        )
        entries[1] = resent_agent(mixed.agent, agent_version=str(later.id))
    elif case == "change_graph":
        entries[1] = resent_agent(mixed.agent, **new_entry)
    else:
        entries[1] = resent_agent(mixed.agent, id=str(uuid.uuid4()))
    # These are unavailable new/changed targets, not valid legacy graphs.
    if case != "new_id":
        entries[-1]["agent_version"] = str(uuid.uuid4())
    # The column change proves the refusal precedes every PUT mutation.
    body = {
        "column_id": str(actor["exp"]["output_column"].id),
        "prompt_config": entries,
    }
    before = state(mixed.experiment)
    with patch(START) as start:
        response = actor["client"].put(mixed.url, body, format="json")
    assert_refused(response)
    assert state(mixed.experiment) == before
    start.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["prompt_edit", "column_edit", "agent_removed"])
@override_settings(**OFF)
def test_update_of_stored_mixed_experiment_stays_admitted(tenants, case):
    actor = tenants["a"]
    mixed = mixed_experiment(actor)
    entries = [resent_prompt(mixed), resent_agent(mixed.agent)]
    body = {"prompt_config": entries}
    if case == "prompt_edit":
        entries[0] = resent_prompt(mixed, model="gpt-4o")
    elif case == "column_edit":
        body["column_id"] = str(actor["exp"]["output_column"].id)
    else:
        body["prompt_config"] = entries[:1]
    with patch(START, return_value="edit-workflow") as start:
        response = actor["client"].put(mixed.url, body, format="json")
    assert response.status_code == 200, response.content
    for call in start.call_args_list:
        assert call.kwargs["rerun_agent_config_ids"] == []
    if case == "prompt_edit":
        start.assert_called_once()
        assert start.call_args.kwargs["rerun_prompt_config_ids"] == [
            str(mixed.prompt_config.id)
        ]
    stored = ExperimentAgentConfig.no_workspace_objects.get(id=mixed.agent.eac.id)
    assert stored.graph_version_id == mixed.agent.version.id
    edt = ExperimentDatasetTable._base_manager.get(id=mixed.agent.edt.id)
    assert edt.deleted is (case == "agent_removed")


@pytest.mark.django_db
@override_settings(**OFF)
def test_prompt_variant_append_on_graph_experiment_stays_admitted(tenants):
    actor = tenants["a"]
    mixed = mixed_experiment(actor)
    template, version = variants.prompt(actor, "appended prompt")
    with patch(START, return_value="append-workflow") as start:
        response = actor["client"].put(
            mixed.url,
            {"append_only": True, "prompt_config": [variants.entry(template, version)]},
            format="json",
        )
    assert response.status_code == 200, response.content
    start.assert_called_once()
    assert start.call_args.kwargs["rerun_agent_config_ids"] == []
    assert ExperimentAgentConfig.no_workspace_objects.filter(
        id=mixed.agent.eac.id, experiment_dataset__deleted=False
    ).exists()


# --------------------------------------------------------------------------
# Re-runs of stored graph targets
# --------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(**OFF)
def test_full_rerun_refuses_a_batch_holding_a_graph_target_atomically(tenants):
    actor = tenants["a"]
    agent = revoke_graph(stored_agent(actor))
    other = tenancy._make_experiment(
        actor["org"], actor["workspace"], actor["user"], "prompt-only"
    )
    before = (state(agent.experiment), state(other["experiment"]))
    with patch(START) as start:
        response = actor["client"].post(
            f"{V2}re-run/",
            {
                "experiment_ids": [
                    str(other["experiment"].id),
                    str(agent.experiment.id),
                ]
            },
            format="json",
        )
    assert_refused(response)
    assert (state(agent.experiment), state(other["experiment"])) == before
    start.assert_not_called()


@pytest.mark.django_db
@override_settings(**OFF)
def test_full_rerun_of_prompt_only_experiment_stays_admitted(tenants):
    actor = tenants["a"]
    experiment = actor["exp"]["experiment"]
    with patch(START, return_value="rerun-workflow") as start:
        response = actor["client"].post(
            f"{V2}re-run/", {"experiment_ids": [str(experiment.id)]}, format="json"
        )
    assert response.status_code == 200, response.content
    start.assert_called_once()
    assert start.call_args.kwargs["experiment_id"] == str(experiment.id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "request_kind", ["column", "column_failed_only", "cell", "mixed_columns"]
)
@override_settings(**OFF)
def test_rerun_cells_refuses_graph_target_before_any_reset(tenants, request_kind):
    actor = tenants["a"]
    mixed = mixed_experiment(actor)
    agent = revoke_graph(mixed.agent)
    body = {
        "column": {"source_ids": [str(agent.edt.id)]},
        "column_failed_only": {"source_ids": [str(agent.edt.id)], "failed_only": True},
        "cell": {
            "cells": [{"column_id": str(agent.column.id), "row_id": str(agent.row.id)}]
        },
        "mixed_columns": {
            "source_ids": [
                str(mixed.prompt_config.experiment_dataset_id),
                str(agent.edt.id),
            ]
        },
    }[request_kind]
    before = state(mixed.experiment)
    with patch(START_CELLS) as start:
        response = actor["client"].post(
            f"{V2}{mixed.experiment.id}/rerun-cells/", body, format="json"
        )
    assert_refused(response)
    assert state(mixed.experiment) == before
    start.assert_not_called()


@pytest.mark.django_db
@override_settings(**OFF)
def test_prompt_column_rerun_on_graph_experiment_stays_admitted(tenants):
    actor = tenants["a"]
    mixed = mixed_experiment(actor)
    with patch(START_CELLS, return_value="cells-workflow") as start:
        response = actor["client"].post(
            f"{V2}{mixed.experiment.id}/rerun-cells/",
            {"source_ids": [str(mixed.prompt_config.experiment_dataset_id)]},
            format="json",
        )
    assert response.status_code == 200, response.content
    start.assert_called_once()
    assert start.call_args.kwargs["agent_config_ids"] == []
    assert start.call_args.kwargs["prompt_config_ids"] == [str(mixed.prompt_config.id)]


@pytest.mark.django_db
def test_evaluator_only_run_on_graph_experiment_stays_admitted(tenants):
    """StartEvalsProcess delegates to the eval-only branch of rerun-cells."""
    actor = tenants["a"]
    graph, version, _ = native.make_graph(actor)
    with override_settings(**ON), patch(START, return_value="native-workflow"):
        created = actor["client"].post(
            V2, native.payload(actor, graph, version), format="json"
        )
    assert created.status_code == 200, created.content
    experiment = ExperimentsTable.no_workspace_objects.get(name="Native experiment")
    metric = experiment.user_eval_template_ids.get()
    edt = ExperimentDatasetTable.no_workspace_objects.get(
        experiment=experiment, agent_config__isnull=False
    )
    output = edt.columns.filter(
        source=SourceChoices.EXPERIMENT.value, deleted=False
    ).first()
    Column.no_workspace_objects.get_or_create(
        dataset=experiment.snapshot_dataset,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{edt.id}-{output.id}-sourceid-{metric.id}",
        defaults={"name": "native-output-check", "data_type": "text"},
    )
    with override_settings(**OFF), patch(START_CELLS, return_value="evals") as start:
        response = actor["client"].post(
            f"/model-hub/develops/{experiment.dataset_id}/start_evals_process/",
            {"user_eval_ids": [str(metric.id)], "experiment_id": str(experiment.id)},
            format="json",
        )
    assert response.status_code == 200, response.content
    start.assert_called_once()
    assert start.call_args.kwargs["agent_config_ids"] == []
    assert start.call_args.kwargs["eval_only"] is True


# --------------------------------------------------------------------------
# Resume of a stored graph run (stop and reads are not gated)
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("selection", ["agent", "prompt_and_agent"])
# F06's own default-OFF gate refuses every resume first (404); open it so this
# TX1-B predicate is the boundary under test.
@override_settings(**OFF, EXPERIMENT_RUN_CONTROL_ENABLED=True)
def test_resume_refuses_stored_graph_run_before_temporal(tenants, selection):
    actor = tenants["a"]
    mixed = mixed_experiment(actor)
    revoke_graph(mixed.agent)
    run_ids = [str(mixed.agent.edt.id)]
    if selection == "prompt_and_agent":
        run_ids.insert(0, str(mixed.prompt_config.experiment_dataset_id))
    with patch(
        "model_hub.services.experiment_run_control.get_client",
        side_effect=AssertionError("Temporal contacted"),
    ):
        response = actor["client"].post(
            f"{V2}{mixed.experiment.id}/resume/", {"run_ids": run_ids}, format="json"
        )
    assert_refused(response)


# --------------------------------------------------------------------------
# Durable admission (submit_transaction -> admit_submission)
# --------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(**OFF)
def test_durable_admission_refuses_a_staged_graph_run(tenants):
    from model_hub.services.experiment_submit import admit_submission, state_of

    agent = revoke_graph(stored_agent(tenants["a"]))
    stage_full_run(agent.experiment)
    with patch(START, return_value="never") as start:
        with pytest.raises(PermissionDenied) as info:
            admit_submission(str(agent.experiment.id))
    assert_refusal(info.value)
    start.assert_not_called()
    agent.experiment.refresh_from_db()
    assert state_of(agent.experiment)["state"] == "pending"


@pytest.mark.django_db
@override_settings(**OFF)
def test_submit_transaction_maps_policy_refusal_to_403(tenants):
    from model_hub.services.experiment_submit import Submission, submit_transaction

    agent = revoke_graph(stored_agent(tenants["a"]))
    stage_full_run(agent.experiment)

    @submit_transaction
    def retry():
        return Submission(str(agent.experiment.id), Response({"status": True}))

    with patch(START, return_value="never") as start:
        response = retry()
    assert response.status_code == 403
    assert response.data["code"] == CODE
    start.assert_not_called()


# --------------------------------------------------------------------------
# Temporal domain clients (direct service callers)
# --------------------------------------------------------------------------


def selectors_for(mixed, names):
    values = {
        "full": {},
        "agent": {"rerun_agent_config_ids": [str(mixed.agent.eac.id)]},
        "prompt_and_agent": {
            "rerun_prompt_config_ids": [str(mixed.prompt_config.id)],
            "rerun_agent_config_ids": [str(mixed.agent.eac.id)],
        },
        "durable_full": {"admission_id": str(uuid.uuid4())},
        "prompt": {"rerun_prompt_config_ids": [str(mixed.prompt_config.id)]},
        "eval": {"rerun_eval_template_ids": [str(uuid.uuid4())]},
        "column": {"column_changed": True},
    }
    return values[names]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selectors", ["full", "agent", "prompt_and_agent", "durable_full"]
)
@override_settings(**OFF)
def test_v2_client_refuses_runs_that_include_a_graph_target(tenants, selectors):
    from tfc.temporal.experiments import client

    mixed = mixed_experiment(tenants["a"])
    revoke_graph(mixed.agent)
    with (
        patch(f"{CLIENT}.start_workflow_sync") as start,
        patch(f"{CLIENT}._run_async_in_sync_context") as bridge,
        pytest.raises(PermissionDenied) as info,
    ):
        client.start_experiment_v2_workflow(
            experiment_id=str(mixed.experiment.id),
            **selectors_for(mixed, selectors),
        )
    assert_refusal(info.value)
    start.assert_not_called()
    bridge.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("selectors", ["prompt", "eval", "column"])
@override_settings(**OFF)
def test_v2_client_admits_runs_without_a_graph_target(tenants, selectors):
    from tfc.temporal.experiments import client

    mixed = mixed_experiment(tenants["a"])
    with patch(f"{CLIENT}.start_workflow_sync") as start:
        client.start_experiment_v2_workflow(
            experiment_id=str(mixed.experiment.id),
            **selectors_for(mixed, selectors),
        )
    start.assert_called_once()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selectors", ["full", "durable_full"])
@override_settings(**OFF)
async def test_async_v2_client_refuses_runs_that_include_a_graph_target(
    tenants, selectors
):
    from tfc.temporal.experiments import client

    mixed = await sync_to_async(mixed_experiment)(tenants["a"])
    await sync_to_async(revoke_graph)(mixed.agent)
    with (
        patch(f"{CLIENT}.start_workflow_async", new_callable=AsyncMock) as start,
        patch(
            "tfc.temporal.experiments.admission.admit_v2_workflow",
            new_callable=AsyncMock,
        ) as admit,
        pytest.raises(PermissionDenied) as info,
    ):
        await client.start_experiment_v2_workflow_async(
            experiment_id=str(mixed.experiment.id),
            **selectors_for(mixed, selectors),
        )
    assert_refusal(info.value)
    start.assert_not_awaited()
    admit.assert_not_awaited()


@pytest.mark.django_db
@override_settings(**OFF)
def test_rerun_cells_client_refuses_graph_targets():
    from tfc.temporal.experiments import client

    with (
        patch(f"{CLIENT}.start_workflow_sync") as start,
        pytest.raises(PermissionDenied) as info,
    ):
        client.start_rerun_cells_v2_workflow(
            experiment_id=str(uuid.uuid4()),
            dataset_id=str(uuid.uuid4()),
            agent_config_ids=[str(uuid.uuid4())],
        )
    assert_refusal(info.value)
    start.assert_not_called()


@pytest.mark.django_db(transaction=True)
@override_settings(**OFF)
async def test_async_rerun_cells_client_refuses_graph_targets():
    from tfc.temporal.experiments import client

    with (
        patch(f"{CLIENT}.start_workflow_async", new_callable=AsyncMock) as start,
        pytest.raises(PermissionDenied) as info,
    ):
        await client.start_rerun_cells_v2_workflow_async(
            experiment_id=str(uuid.uuid4()),
            dataset_id=str(uuid.uuid4()),
            prompt_config_ids=[str(uuid.uuid4())],
            agent_config_ids=[str(uuid.uuid4())],
        )
    assert_refusal(info.value)
    start.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"eval_only": True, "edt_ids": [str(uuid.uuid4())]},
        {"eval_only": True, "base_eval_only": True},
        {"prompt_config_ids": [str(uuid.uuid4())]},
    ],
    ids=["per_edt_eval", "base_eval", "prompt"],
)
@override_settings(**OFF)
def test_rerun_cells_client_admits_eval_and_prompt_runs(kwargs):
    from tfc.temporal.experiments import client

    with patch(f"{CLIENT}.start_workflow_sync") as start:
        client.start_rerun_cells_v2_workflow(
            experiment_id=str(uuid.uuid4()), dataset_id=str(uuid.uuid4()), **kwargs
        )
    start.assert_called_once()


# --------------------------------------------------------------------------
# Durable admission boundary called directly (admission.admit_v2_workflow)
# --------------------------------------------------------------------------


def temporal_client(memo_submission_id=None):
    """A mocked Temporal client. With no memo the experiment has no workflow
    (describe is NOT_FOUND); otherwise one exists for that submission."""
    from temporalio.service import RPCError, RPCStatusCode

    handle = SimpleNamespace(describe=AsyncMock())
    if memo_submission_id is None:
        handle.describe.side_effect = RPCError(
            "not found", RPCStatusCode.NOT_FOUND, b""
        )
    else:
        handle.describe.return_value = SimpleNamespace(
            memo=AsyncMock(
                return_value={"experiment_submission_id": memo_submission_id}
            )
        )
    return SimpleNamespace(
        get_workflow_handle=Mock(return_value=handle),
        start_workflow=AsyncMock(return_value=SimpleNamespace(id="experiment-new")),
    )


async def admit_directly(experiment, client, submission_id, **selectors):
    """Call the durable admission boundary itself, not through a client."""
    from tfc.temporal.experiments.admission import admit_v2_workflow
    from tfc.temporal.experiments.types import RunExperimentInput

    workflow_input = RunExperimentInput(experiment_id=str(experiment.id), **selectors)
    with patch(
        "tfc.temporal.experiments.admission.get_client",
        AsyncMock(return_value=client),
    ):
        return await admit_v2_workflow(workflow_input, submission_id, "tasks_l")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("existing", ["none", "other_submission"])
@pytest.mark.parametrize("selectors", ["full", "agent", "prompt_and_agent"])
@override_settings(**OFF)
async def test_direct_admission_refuses_a_new_start_of_a_graph_target(
    tenants, selectors, existing
):
    mixed = await sync_to_async(mixed_experiment)(tenants["a"])
    await sync_to_async(revoke_graph)(mixed.agent)
    before = await sync_to_async(state)(mixed.experiment)
    client = temporal_client(None if existing == "none" else "submission-other")
    with pytest.raises(PermissionDenied) as info:
        await admit_directly(
            mixed.experiment,
            client,
            "submission-new",
            **selectors_for(mixed, selectors),
        )
    assert_refusal(info.value)
    client.start_workflow.assert_not_awaited()
    assert await sync_to_async(state)(mixed.experiment) == before


@pytest.mark.django_db(transaction=True)
@override_settings(**OFF)
async def test_direct_admission_replays_a_matching_submission_without_a_start(tenants):
    mixed = await sync_to_async(mixed_experiment)(tenants["a"])
    client = temporal_client("submission-a")
    workflow_id = await admit_directly(mixed.experiment, client, "submission-a")
    assert workflow_id == f"experiment-{mixed.experiment.id}"
    client.start_workflow.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selectors", ["prompt", "eval", "column"])
@override_settings(**OFF)
async def test_direct_admission_starts_runs_without_a_graph_target(tenants, selectors):
    mixed = await sync_to_async(mixed_experiment)(tenants["a"])
    client = temporal_client()
    workflow_id = await admit_directly(
        mixed.experiment, client, "submission-a", **selectors_for(mixed, selectors)
    )
    assert workflow_id == "experiment-new"
    client.start_workflow.assert_awaited_once()
    assert client.start_workflow.call_args.kwargs["memo"] == {
        "experiment_submission_id": "submission-a"
    }


@pytest.mark.django_db(transaction=True)
@override_settings(**OFF)
async def test_direct_admission_starts_a_full_prompt_only_run(tenants):
    experiment, *_ = await sync_to_async(variants.prepare)(tenants["a"])
    client = temporal_client()
    workflow_id = await admit_directly(experiment, client, "submission-a")
    assert workflow_id == "experiment-new"
    client.start_workflow.assert_awaited_once()


@pytest.mark.django_db(transaction=True)
@override_settings(**ON)
async def test_direct_admission_starts_a_graph_run_when_qualified(tenants):
    mixed = await sync_to_async(mixed_experiment)(tenants["a"])
    client = temporal_client()
    workflow_id = await admit_directly(mixed.experiment, client, "submission-a")
    assert workflow_id == "experiment-new"
    client.start_workflow.assert_awaited_once()


# --------------------------------------------------------------------------
# Worker activities (direct registered workflow starts bypass every client)
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@override_settings(**OFF)
def test_agent_setup_activity_refuses_before_running_mutation(tenants):
    from tfc.temporal.experiments.activities import _setup_agent_sync

    agent = revoke_graph(stored_agent(tenants["a"]))
    before = state(agent.experiment)
    with (
        patch("tfc.temporal.experiments.activities.activity.logger"),
        pytest.raises(PermissionDenied) as info,
    ):
        _setup_agent_sync(
            str(agent.experiment.id),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.eac.id),
        )
    assert_refusal(info.value)
    assert state(agent.experiment) == before


@pytest.mark.django_db(transaction=True)
@override_settings(**OFF)
def test_agent_row_activity_refuses_before_graph_execution(tenants):
    from tfc.temporal.experiments.activities import _prepare_agent_row_sync

    agent = revoke_graph(stored_agent(tenants["a"]))
    with (
        patch("tfc.temporal.experiments.activities.activity.logger"),
        patch("tfc.temporal.agent_playground.client._create_graph_execution") as create,
        patch("model_hub.services.experiment_row_budget.bind_graph_budget") as bind,
    ):
        result = _prepare_agent_row_sync(
            str(agent.row.id),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.experiment.id),
            str(agent.version.id),
            str(agent.eac.id),
        )
    assert result["status"] == "FAILED"
    # Contract migration: no blanket GraphVersion release-disqualification claim.
    assert CODE in result["error"]
    assert result["error"] == (
        "tx1b_not_qualified: "
        "Experiment target or execution context not found or unavailable."
    )
    assert result["graph_execution_id"] == ""
    create.assert_not_called()
    bind.assert_not_called()
