"""Real copy views, worker ORM and update signals; only external boundaries stubbed.

Fixture and Temporal bridge patterns follow the adjacent optimizer placeholder
suites. No post-fix product symbol is imported, so this file collects at RED.
"""

import sys
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from rest_framework.test import APIRequestFactory, force_authenticate
from structlog.testing import capture_logs
from temporalio.exceptions import ApplicationError

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.models.run_prompt import PromptTemplate, PromptVersion, RunPrompter
from model_hub.serializers.dataset_optimization import DatasetOptimizationSerializer
from model_hub.utils.optimizer_prompt_text import optimizer_prompt_text
from model_hub.views import develop_dataset, run_prompt
from model_hub.views.dataset_optimization import DatasetOptimizationViewSet
from model_hub.views.datasets.create import dataset_from_experiment
from tfc.middleware.workspace_context import clear_workspace_context, workspace_context
from tfc.temporal.dataset_optimization import activities

pytestmark = pytest.mark.django_db


def _messages(column_id, layout="whole"):
    text = "Input {{" + str(column_id) + "}}"
    if layout == "whole":
        return [{"role": "user", "content": text}]
    left, right = text[:25], text[25:]
    if layout == "parts":
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": left},
                    {"type": "text", "text": right},
                ],
            }
        ]
    return [{"role": "system", "content": left}, {"role": "user", "content": right}]


@pytest.fixture
def graph():
    clear_workspace_context()
    organization = Organization.objects.create(name="OCE organization")
    other_org = Organization.objects.create(name="OCE foreign organization")
    user = User.objects.create_user(
        email="oce-copy@example.test",
        password=None,
        name="OCE",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name="OCE default", organization=organization, created_by=user, is_default=True
    )
    other_workspace = Workspace.objects.create(
        name="OCE other", organization=organization, created_by=user
    )
    datasets, columns = {}, {}
    for kind, org, ws in (
        ("same", organization, workspace),
        ("other_dataset", organization, workspace),
        ("other_workspace", organization, other_workspace),
        ("legacy", organization, None),
        ("foreign_org", other_org, None),
    ):
        dataset = Dataset.no_workspace_objects.create(
            name=f"OCE {kind}", organization=org, workspace=ws, column_order=[]
        )
        datasets[kind] = dataset
        columns[kind] = Column.no_workspace_objects.create(
            name=f"oce_{kind}",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
    dataset = datasets["same"]
    prompt = RunPrompter.no_workspace_objects.create(
        name="OCE output",
        model="",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        messages=_messages(columns["same"].id),
        status=StatusType.COMPLETED.value,
    )
    output = Column.no_workspace_objects.create(
        name="OCE output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=prompt.id,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, row=row, column=output, value="OCE fallback")
    missing = uuid4()
    assert not Column.no_workspace_objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-OCE missing reference must not exist"
    )
    assert datasets["legacy"].workspace_id is None, (
        "SETUP_AQ-XWS-OCE legacy source must have NULL workspace"
    )
    yield SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
        dataset=dataset,
        datasets=datasets,
        columns=columns,
        prompt=prompt,
        output=output,
        missing=missing,
    )
    clear_workspace_context()


def _snapshot():
    return tuple(
        list(model.no_workspace_objects.order_by("pk").values())
        for model in (
            Dataset,
            Column,
            RunPrompter,
            Row,
            Cell,
            ExperimentsTable,
            ExperimentDatasetTable,
        )
    )


def _shape(response):
    return response.status_code, response.data


@pytest.fixture
def copy_writer(graph, monkeypatch):
    source = graph.datasets["legacy"]
    prompt = RunPrompter.no_workspace_objects.create(
        name="OCE legacy output",
        model="",
        dataset=source,
        organization=graph.organization,
        workspace=None,
        messages=_messages(graph.columns["legacy"].id),
    )
    output = Column.no_workspace_objects.create(
        name="OCE legacy output",
        dataset=source,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=prompt.id,
    )
    experiment = ExperimentsTable.objects.create(
        name="OCE experiment", dataset=source, column=output, user=graph.user
    )
    table = ExperimentDatasetTable.objects.create(
        name="OCE results", experiment=experiment
    )
    experiment.experiments_datasets.add(table)
    assert prompt.workspace_id is None, (
        "SETUP_AQ-XWS-OCE source prompt must have NULL workspace"
    )
    for module in (develop_dataset, dataset_from_experiment):
        monkeypatch.setattr(module, "log_and_deduct_cost_for_resource_request", None)
        monkeypatch.setattr(module, "get_recommendations", lambda dataset: None)

    def post(operation, messages):
        prompt.messages = messages
        prompt.save(update_fields=["messages"])
        payload = {"name": f"OCE copy {uuid4()}"}
        if operation == "create":
            table.columns.clear()
            view = dataset_from_experiment.CreateDatasetFromExpView
            kwargs = {"exp_dataset_id": str(table.id)}
        else:
            table.columns.set([graph.columns["legacy"], output])
            payload.update(
                dataset_id=str(table.id),
                columns={
                    str(graph.columns["legacy"].id): "OCE copied input",
                    str(output.id): "OCE copied output",
                },
            )
            view = develop_dataset.AddAsNewDataset
            kwargs = {}
        before = _snapshot()
        request = APIRequestFactory().post("/oce-copy/", payload, format="json")
        request.organization = graph.organization
        request.workspace = graph.workspace
        force_authenticate(request, user=graph.user)
        response = view.as_view()(request, **kwargs)
        return response, before

    return SimpleNamespace(post=post, prompt=prompt, output=output)


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("kind", ["other_dataset", "other_workspace", "foreign_org"])
def test_copy_refuses_foreign_and_missing_atomically(
    graph, copy_writer, operation, layout, kind
):
    observed = []
    for reference in (graph.columns[kind].id, graph.missing):
        response, before = copy_writer.post(operation, _messages(reference, layout))
        observed.append((_shape(response), _snapshot() == before))
    expected = _shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert observed == [(expected, True), (expected, True)], (
        "AQ-XWS-OCE: copy writers must refuse foreign and missing identically without committing records"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
def test_copy_remaps_valid_source_to_destination(graph, copy_writer, operation, layout):
    response, _ = copy_writer.post(
        operation, _messages(graph.columns["legacy"].id, layout)
    )
    copied = list(
        RunPrompter.no_workspace_objects.exclude(
            dataset_id__in=[dataset.id for dataset in graph.datasets.values()]
        )
    )
    prompt = copied[0] if len(copied) == 1 else None
    local = (
        list(
            Column.no_workspace_objects.filter(
                dataset_id=prompt.dataset_id, source=SourceChoices.OTHERS.value
            )
        )
        if prompt
        else []
    )
    observed = (
        response.status_code,
        len(copied),
        len(local),
        optimizer_prompt_text(prompt.messages) if prompt else None,
        prompt.organization_id if prompt else None,
        prompt.workspace_id if prompt else None,
    )
    expected_text = "Input {{" + str(local[0].id) + "}}" if len(local) == 1 else None
    assert observed == (
        200,
        1,
        1,
        expected_text,
        graph.organization.id,
        graph.workspace.id,
    ), (
        "AQ-XWS-OCE: copied prompt must contain only remapped destination-local references and ownership"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
def test_copy_source_misses_share_refusal(graph, copy_writer, operation):
    foreign = RunPrompter.no_workspace_objects.create(
        name="OCE unrelated source",
        model="",
        dataset=graph.datasets["other_dataset"],
        organization=graph.organization,
        workspace=None,
        messages=[],
    )
    missing = uuid4()
    assert not RunPrompter.no_workspace_objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-OCE missing copied source must not exist"
    )
    observed = []
    for reference in (foreign.id, missing):
        copy_writer.output.source_id = reference
        copy_writer.output.save(update_fields=["source_id"])
        response, before = copy_writer.post(
            operation, _messages(graph.columns["legacy"].id)
        )
        observed.append((_shape(response), _snapshot() == before))
    expected = _shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert observed == [(expected, True), (expected, True)], (
        "AQ-XWS-OCE: copy source misses must refuse foreign and missing identically without committing records"
    )


@pytest.fixture
def worker(graph, monkeypatch):
    monkeypatch.setattr(
        activities,
        "sync_to_async",
        lambda func, **kwargs: sync_to_async(func, thread_sensitive=True),
    )
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(activities, "Heartbeater", lambda *args: nullcontext())
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        activities, "get_api_key_for_model", lambda **kwargs: "oce-test"
    )
    executions = []

    class OptimizerBoundary:
        def optimize_from_execution(self, **kwargs):
            execution = kwargs["execution_data"]
            executions.append(execution)
            prompt = execution["agent_definition_prompt"]["description"]
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

    def execute(resumed=False, saved_prompt="OCE existing", saved_state=None):
        run = OptimizeDataset.no_workspace_objects.create(
            name="OCE run",
            column=graph.output,
            organization=graph.organization,
            workspace=graph.workspace,
            optimizer_algorithm=OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            optimizer_config={"num_variations": 1},
            status=OptimizeDataset.StatusType.PENDING,
            optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
            environment=OptimizeDataset.EnvTypes.TRAINING,
            version="1.0",
        )
        if resumed:
            DatasetOptimizationTrial.objects.create(
                optimization_run=run,
                trial_number=1,
                average_score=0.5,
                prompt=saved_prompt,
                metadata={"optimizer_state": saved_state or {"done": True}},
            )
        before = list(
            DatasetOptimizationTrial.objects.filter(optimization_run=run).values()
        )
        calls = len(executions)
        error = None
        result = None
        with capture_logs() as logs:
            try:
                result = async_to_sync(activities.run_optimization_activity)(
                    {"run_id": str(run.id)}
                )
            except ApplicationError as exc:
                error = exc.message
                async_to_sync(activities.finalize_run_activity)(
                    {
                        "run_id": str(run.id),
                        "status": "completed" if resumed else "failed",
                        "error": "Activity task failed",
                    }
                )
        run.refresh_from_db()
        after = list(
            DatasetOptimizationTrial.objects.filter(optimization_run=run).values()
        )
        refusal_logs = [
            entry
            for entry in logs
            if entry.get("event") == "Optimization reference not found"
        ]
        return SimpleNamespace(
            run=run,
            result=result,
            error=error,
            unchanged=before == after,
            calls=len(executions) - calls,
            logged=bool(refusal_logs),
        )

    return SimpleNamespace(execute=execute, executions=executions)


def _refusal(result):
    return (
        result.run.status,
        result.run.error_message,
        result.error,
        result.unchanged,
        result.calls,
        result.logged,
    )


@pytest.mark.parametrize(
    "kind", ["other_dataset", "other_workspace", "legacy", "foreign_org"]
)
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("resumed", [False, True])
def test_worker_refuses_foreign_and_missing_visibly(
    graph, worker, kind, layout, resumed
):
    observed = []
    for reference in (graph.columns[kind].id, graph.missing):
        graph.prompt.messages = _messages(reference, layout)
        graph.prompt.save(update_fields=["messages"])
        observed.append(_refusal(worker.execute(resumed)))
    expected = (
        OptimizeDataset.StatusType.FAILED,
        "Optimization reference not found",
        "Optimization reference not found",
        True,
        0,
        True,
    )
    assert observed == [expected, expected], (
        "AQ-XWS-OCE: worker must fail foreign and missing references identically with a fixed reason and log before execution"
    )


@pytest.mark.parametrize("location", ["trial", "state"])
def test_worker_rechecks_in_flight_references(graph, worker, location):
    observed = []
    for reference in (graph.columns["other_dataset"].id, graph.missing):
        text = "Input {{" + str(reference) + "}}"
        kwargs = (
            {"saved_prompt": text}
            if location == "trial"
            else {"saved_state": {"prompt": text}}
        )
        observed.append(_refusal(worker.execute(resumed=True, **kwargs)))
    expected = (
        OptimizeDataset.StatusType.FAILED,
        "Optimization reference not found",
        "Optimization reference not found",
        True,
        0,
        True,
    )
    assert observed == [expected, expected], (
        "AQ-XWS-OCE: in-flight trial and resume-state references must fail closed identically before returning saved results"
    )


@pytest.mark.parametrize("source", ["run_prompt", "template"])
def test_worker_source_misses_never_fall_back(graph, worker, source):
    dataset = graph.datasets["other_workspace"]
    if source == "run_prompt":
        foreign = RunPrompter.no_workspace_objects.create(
            name="OCE foreign prompt",
            model="",
            dataset=dataset,
            organization=dataset.organization,
            workspace=dataset.workspace,
            messages=[],
        )
        graph.output.source = SourceChoices.RUN_PROMPT.value
    else:
        foreign = PromptTemplate.no_workspace_objects.create(
            name="OCE foreign template",
            organization=dataset.organization,
            workspace=dataset.workspace,
        )
        graph.output.source = SourceChoices.OTHERS.value
    missing = uuid4()
    assert not type(foreign).no_workspace_objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-OCE missing source must not exist"
    )
    observed = []
    for reference in (foreign.id, missing):
        graph.output.source_id = reference
        graph.output.save(update_fields=["source", "source_id"])
        observed.append(_refusal(worker.execute()))
    expected = (
        OptimizeDataset.StatusType.FAILED,
        "Optimization reference not found",
        "Optimization reference not found",
        True,
        0,
        True,
    )
    assert observed == [expected, expected], (
        "AQ-XWS-OCE: scoped source misses must fail identically rather than optimize the fallback cell"
    )


@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
def test_worker_same_dataset_still_executes(graph, worker, layout):
    graph.prompt.messages = _messages(graph.columns["same"].id, layout)
    graph.prompt.save(update_fields=["messages"])
    result = worker.execute()
    assert result.error is None and result.calls == 1, (
        "SETUP_AQ-XWS-OCE same-dataset worker must execute exactly once"
    )
    trial = DatasetOptimizationTrial.objects.get(optimization_run=result.run)
    assert result.result["best_prompt"] == trial.prompt == "Input {{oce_same}}", (
        "SETUP_AQ-XWS-OCE same-dataset worker must persist resolved text"
    )


def test_worker_scoped_empty_prompt_keeps_cell_fallback(graph, worker):
    graph.prompt.messages = []
    graph.prompt.save(update_fields=["messages"])
    result = worker.execute()
    assert result.error is None and result.calls == 1, (
        "SETUP_AQ-XWS-OCE a scoped empty prompt may keep the existing local-cell fallback"
    )
    trial = DatasetOptimizationTrial.objects.get(optimization_run=result.run)
    assert result.result["best_prompt"] == trial.prompt == "OCE fallback", (
        "SETUP_AQ-XWS-OCE scoped fallback must persist the same-dataset cell text"
    )


def test_worker_same_dataset_resume_still_returns_saved_result(graph, worker):
    text = "Input {{" + str(graph.columns["same"].id) + "}}"
    result = worker.execute(
        resumed=True, saved_prompt=text, saved_state={"prompt": text}
    )
    assert result.error is None and result.calls == 0 and result.unchanged, (
        "SETUP_AQ-XWS-OCE same-dataset completed resume must not execute or mutate trials"
    )
    assert result.result["best_prompt"] == text, (
        "SETUP_AQ-XWS-OCE same-dataset completed resume must return its saved result"
    )


def test_worker_same_workspace_template_still_executes(graph, worker):
    template = PromptTemplate.no_workspace_objects.create(
        name="OCE same template",
        organization=graph.organization,
        workspace=graph.workspace,
    )
    PromptVersion.objects.create(
        original_template=template,
        template_version="1",
        is_default=True,
        prompt_config_snapshot=[
            {"messages": [{"role": "user", "content": "OCE template"}]}
        ],
    )
    graph.output.source = SourceChoices.OTHERS.value
    graph.output.source_id = template.id
    graph.output.save(update_fields=["source", "source_id"])
    result = worker.execute()
    assert result.error is None and result.calls == 1, (
        "SETUP_AQ-XWS-OCE same-workspace template must execute exactly once"
    )
    trial = DatasetOptimizationTrial.objects.get(optimization_run=result.run)
    assert result.result["best_prompt"] == trial.prompt == "OCE template", (
        "SETUP_AQ-XWS-OCE same-workspace template must persist its own text"
    )


@pytest.mark.parametrize("path", ["serializer", "view"])
def test_update_preserves_null_organization_and_workspace(graph, monkeypatch, path):
    run = OptimizeDataset.no_workspace_objects.create(
        name="OCE legacy run",
        column=graph.columns["legacy"],
        organization=None,
        workspace=None,
        optimizer_algorithm=OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
        optimizer_config={"num_variations": 1},
    )
    run = OptimizeDataset.no_workspace_objects.get(id=run.id)
    assert (run.organization_id, run.workspace_id) == (None, None), (
        "SETUP_AQ-XWS-OCE legacy run must start with NULL ownership"
    )
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)
    context = SimpleNamespace(
        organization=graph.organization, workspace=graph.workspace, user=graph.user
    )
    with workspace_context(
        workspace=graph.workspace, organization=graph.organization, user=graph.user
    ):
        if path == "serializer":
            serializer = DatasetOptimizationSerializer(
                run,
                data={"name": "OCE renamed"},
                partial=True,
                context={"request": context},
            )
            assert serializer.is_valid(), (
                "SETUP_AQ-XWS-OCE name-only update must validate"
            )
            serializer.save()
        else:
            request = APIRequestFactory().patch(
                "/oce-update/", {"name": "OCE renamed"}, format="json"
            )
            request.organization = graph.organization
            request.workspace = graph.workspace
            force_authenticate(request, user=graph.user)
            response = DatasetOptimizationViewSet.as_view({"patch": "partial_update"})(
                request, pk=str(run.id)
            )
            assert response.status_code == 200, (
                "SETUP_AQ-XWS-OCE name-only update must succeed"
            )
    run = OptimizeDataset.no_workspace_objects.select_related("column__dataset").get(
        id=run.id
    )
    refusal = None
    try:
        activities._verify_run_binding(run)
    except ValueError as exc:
        refusal = str(exc)
    assert (run.organization_id, run.workspace_id, run.name, refusal) == (
        None,
        None,
        "OCE renamed",
        "Optimization dataset not found",
    ), (
        "AQ-XWS-OCE: ordinary updates must preserve NULL ownership and continued worker refusal"
    )
