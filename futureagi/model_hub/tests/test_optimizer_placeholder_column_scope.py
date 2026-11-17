"""Exercise real ORM lookups, prompt writes and optimizer trial persistence.

Fixtures follow test_dataset_optimization.py and test_run_prompt_api.py. Only
Temporal scheduling/heartbeats, EE/provider execution and task dispatch are
replaced; the guarded views, serializers, queries and activity remain real.
"""

import sys
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.models.run_prompt import RunPrompter
from model_hub.views import run_prompt
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal.dataset_optimization import activities

pytestmark = pytest.mark.django_db


def _messages(column_id, shape="parts"):
    text = "Input {{" + str(column_id) + "}}"
    content = [{"type": "text", "text": text}] if shape == "parts" else text
    return [{"role": "user", "content": content}]


@pytest.fixture
def scope_graph():
    clear_workspace_context()
    organization = Organization.objects.create(name="OC admitted organization")
    foreign_org = Organization.objects.create(name="OC foreign organization")
    user = User.objects.create_user(
        email="optimizer-scope@example.test",
        password=None,
        name="OC user",
        organization=organization,
    )
    foreign_user = User.objects.create_user(
        email="optimizer-foreign@example.test",
        password=None,
        name="OC foreign user",
        organization=foreign_org,
    )
    workspace = Workspace.objects.create(
        name="OC default", organization=organization, created_by=user, is_default=True
    )
    other_workspace = Workspace.objects.create(
        name="OC other", organization=organization, created_by=user
    )
    foreign_workspace = Workspace.objects.create(
        name="OC foreign", organization=foreign_org, created_by=foreign_user
    )
    datasets = {}
    columns = {}
    for kind, org, ws in (
        ("same", organization, workspace),
        ("other_dataset", organization, workspace),
        ("other_workspace", organization, other_workspace),
        ("null_workspace", organization, None),
        ("foreign_org", foreign_org, foreign_workspace),
    ):
        dataset = Dataset.no_workspace_objects.create(
            name=f"OC {kind}", organization=org, workspace=ws, column_order=[]
        )
        column = Column.no_workspace_objects.create(
            name=f"oc_{kind}_name",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        datasets[kind] = dataset
        columns[kind] = column
    dataset = datasets["same"]
    prompt = RunPrompter.no_workspace_objects.create(
        name="OC output",
        model="",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        messages=_messages(columns["same"].id),
        status=StatusType.COMPLETED.value,
    )
    output = Column.no_workspace_objects.create(
        name="OC output",
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=prompt.id,
        data_type=DataTypeChoices.TEXT.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    cell = Cell.objects.create(
        dataset=dataset, row=row, column=output, value="OC existing output"
    )
    assert columns["foreign_org"].dataset.organization_id != organization.id, (
        "SETUP_AQ-XWS-OC foreign organization must differ"
    )
    assert datasets["other_dataset"].id != dataset.id, (
        "SETUP_AQ-XWS-OC same-organization dataset must differ"
    )
    assert datasets["other_dataset"].organization_id == organization.id, (
        "SETUP_AQ-XWS-OC other dataset must share organization"
    )
    graph = SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
        dataset=dataset,
        datasets=datasets,
        columns=columns,
        prompt=prompt,
        output=output,
        cell=cell,
        missing=uuid.uuid4(),
    )
    assert not Column.no_workspace_objects.filter(id=graph.missing).exists(), (
        "SETUP_AQ-XWS-OC absent UUID must not exist"
    )
    yield graph
    clear_workspace_context()


@pytest.fixture
def worker(monkeypatch, scope_graph):
    # Keep DB work in pytest's transaction, but invoke the complete async activity.
    monkeypatch.setattr(
        activities,
        "sync_to_async",
        lambda func, **kwargs: sync_to_async(func, thread_sensitive=True),
    )
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(activities, "Heartbeater", lambda *args: nullcontext())
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)
    monkeypatch.setattr(activities, "get_api_key_for_model", lambda **kwargs: "oc-test")
    executions = []

    class OptimizerBoundary:
        def optimize_from_execution(self, **kwargs):
            execution = kwargs["execution_data"]
            executions.append(execution)
            prompt = execution["agent_definition_prompt"]["description"]
            # Exercise the real callback and persisted trial, not just local text.
            kwargs["on_trial_callback"](
                {"prompt": prompt, "average_score": 0.5}, 0, {}, True
            )
            return SimpleNamespace(history=[{}], final_score=0.5, best_prompt=prompt)

    monkeypatch.setitem(
        sys.modules,
        "ee.agenthub.fix_your_agent.fix_your_agent",
        SimpleNamespace(FixYourAgent=OptimizerBoundary),
    )
    monkeypatch.setitem(
        sys.modules,
        "ee.agent_opt.utils.template_variables",
        SimpleNamespace(
            normalize_prompt_text=lambda text: text,
            extract_template_variables=lambda text: [],
            build_template_variable_instruction=lambda variables: "",
        ),
    )

    def execute(column_id, shape="parts", binding=None):
        scope_graph.prompt.messages = _messages(column_id, shape)
        scope_graph.prompt.save(update_fields=["messages"])
        values = {
            "organization": scope_graph.organization,
            "workspace": scope_graph.workspace,
        }
        values.update(binding or {})
        run = OptimizeDataset.no_workspace_objects.create(
            name="OC optimization",
            column=scope_graph.output,
            optimizer_algorithm=OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            optimizer_config={"num_variations": 1},
            status=OptimizeDataset.StatusType.PENDING,
            optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
            environment=OptimizeDataset.EnvTypes.TRAINING,
            version="1.0",
            **values,
        )
        clear_workspace_context()
        result = async_to_sync(activities.run_optimization_activity)(
            {"run_id": str(run.id)}
        )
        trial = DatasetOptimizationTrial.objects.get(optimization_run=run)
        return result, trial

    return SimpleNamespace(execute=execute, executions=executions)


# Superseded by AQ2-UO-1: the worker
# now fails foreign and absent placeholder references closed and identically instead of
# leaving them unresolved. Successor: test_optimizer_copy_writers_and_worker_refusal.py::
# test_worker_refuses_foreign_and_missing_visibly (other_dataset/other_workspace/legacy/
# foreign_org x whole/strings/parts x resumed).


@pytest.mark.parametrize("shape", ["parts", "string"])
def test_worker_same_dataset_still_resolves(scope_graph, worker, shape):
    column = scope_graph.columns["same"]
    result, trial = worker.execute(column.id, shape)
    assert trial.prompt == "Input {{" + column.name + "}}", (
        "SETUP_AQ-XWS-OC same-dataset placeholder must resolve"
    )
    assert result["best_prompt"] == trial.prompt, (
        "SETUP_AQ-XWS-OC same-dataset result must be persisted"
    )


@pytest.mark.parametrize("binding", ["missing_org", "wrong_org", "wrong_workspace"])
def test_worker_rechecks_persisted_run_binding(scope_graph, worker, binding):
    overrides = {
        "missing_org": {"organization": None},
        "wrong_org": {"organization": scope_graph.datasets["foreign_org"].organization},
        "wrong_workspace": {"workspace": None},
    }[binding]
    refusal = None
    try:
        worker.execute(scope_graph.columns["same"].id, binding=overrides)
    except ValueError as exc:
        refusal = str(exc)
    assert (refusal, worker.executions) == ("Optimization dataset not found", []), (
        "AQ-XWS-OC: missing or mismatched run binding must fail closed before execution"
    )


@pytest.fixture
def write_prompt(monkeypatch, scope_graph):
    dispatch = Mock(return_value=SimpleNamespace(id="oc-workflow"))
    monkeypatch.setitem(
        sys.modules,
        "model_hub.tasks.run_prompt",
        SimpleNamespace(process_prompts_single=SimpleNamespace(apply_async=dispatch)),
    )

    def post(operation, column_id, shape="parts", omit_messages=False):
        config = {} if omit_messages else {"messages": _messages(column_id, shape)}
        payload = {
            "dataset_id": str(scope_graph.dataset.id),
            "name": f"OC updated {uuid.uuid4()}",
            "config": config,
        }
        view = run_prompt.AddRunPromptColumnView
        if operation == "edit":
            payload["column_id"] = str(scope_graph.output.id)
            view = run_prompt.EditRunPromptColumnView
        request = APIRequestFactory().post("/oc-run-prompt/", payload, format="json")
        request.organization = scope_graph.organization
        request.workspace = scope_graph.workspace
        force_authenticate(request, user=scope_graph.user)
        return view.as_view()(request)

    return SimpleNamespace(post=post, dispatch=dispatch)


@pytest.mark.parametrize("operation", ["add", "edit"])
@pytest.mark.parametrize("shape", ["parts", "string"])
@pytest.mark.parametrize(
    "kind", ["foreign_org", "other_dataset", "other_workspace", "null_workspace"]
)
def test_writes_foreign_and_absent_have_same_not_found_shape(
    scope_graph, write_prompt, operation, shape, kind
):
    before_messages = scope_graph.prompt.messages
    before_count = RunPrompter.no_workspace_objects.count()
    foreign = write_prompt.post(operation, scope_graph.columns[kind].id, shape)
    absent = write_prompt.post(operation, scope_graph.missing, shape)
    expected = run_prompt.GeneralMethods().not_found("Column or dataset not found")
    assert (
        (foreign.status_code, foreign.data)
        == (
            absent.status_code,
            absent.data,
        )
        == (expected.status_code, expected.data)
    ), "AQ-XWS-OC: add/edit foreign and absent placeholders must share not-found shape"
    scope_graph.prompt.refresh_from_db()
    scope_graph.cell.refresh_from_db()
    assert (
        scope_graph.prompt.messages,
        scope_graph.cell.value,
        RunPrompter.no_workspace_objects.count(),
        write_prompt.dispatch.call_count,
    ) == (before_messages, "OC existing output", before_count, 0), (
        "AQ-XWS-OC: refused placeholders must not mutate or dispatch"
    )


@pytest.mark.parametrize("operation", ["add", "edit"])
@pytest.mark.parametrize("shape", ["parts", "string"])
def test_writes_same_dataset_are_accepted(scope_graph, write_prompt, operation, shape):
    messages = _messages(scope_graph.columns["same"].id, shape)
    response = write_prompt.post(operation, scope_graph.columns["same"].id, shape)
    assert response.status_code == 200, (
        "SETUP_AQ-XWS-OC same-dataset add/edit must succeed"
    )
    stored = RunPrompter.no_workspace_objects.filter(dataset=scope_graph.dataset)
    assert any(row.messages == messages for row in stored), (
        "SETUP_AQ-XWS-OC same-dataset messages must be stored"
    )
    assert write_prompt.dispatch.call_count == 1, (
        "SETUP_AQ-XWS-OC same-dataset write must dispatch once"
    )


def test_edit_revalidates_retained_legacy_messages(scope_graph, write_prompt):
    scope_graph.prompt.messages = _messages(scope_graph.columns["foreign_org"].id)
    scope_graph.prompt.save(update_fields=["messages"])
    response = write_prompt.post("edit", None, omit_messages=True)
    expected = run_prompt.GeneralMethods().not_found("Column or dataset not found")
    assert (response.status_code, response.data) == (
        expected.status_code,
        expected.data,
    ), "AQ-XWS-OC: partial edit must revalidate retained legacy placeholders"
