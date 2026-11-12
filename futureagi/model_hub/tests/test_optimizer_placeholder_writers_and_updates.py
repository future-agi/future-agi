"""Real admission/ORM regressions; only dispatch and licensing/catalog are stubbed.

Fixture patterns follow test_optimizer_placeholder_column_scope.py and
 test_optimizer_run_admission_binding.py. No new product symbol is imported,
so these regressions can also be collected against the pinned pre-fix tree.
"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.models.run_prompt import RunPrompter
from model_hub.serializers.dataset_optimization import (
    DatasetOptimizationCreateSerializer,
    DatasetOptimizationSerializer,
)
from model_hub.views import run_prompt
from model_hub.views.dataset_optimization import DatasetOptimizationViewSet
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
    if layout == "mixed":
        return [
            {"role": "system", "content": [{"type": "text", "text": left}]},
            {"role": "user", "content": right},
        ]
    return [
        {"role": "system", "content": left},
        {"role": "user", "content": right},
    ]


@pytest.fixture
def graph():
    clear_workspace_context()
    organization = Organization.objects.create(name="OCD admitted organization")
    foreign_org = Organization.objects.create(name="OCD foreign organization")
    user = User.objects.create_user(
        email="ocd-admission@example.test",
        password=None,
        name="OCD user",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name="OCD default", organization=organization, created_by=user, is_default=True
    )
    other_workspace = Workspace.objects.create(
        name="OCD other", organization=organization, created_by=user
    )
    foreign_workspace = Workspace.objects.create(
        name="OCD foreign", organization=foreign_org, created_by=user
    )
    datasets, columns = {}, {}
    for kind, org, ws in (
        ("same", organization, workspace),
        ("other_dataset", organization, workspace),
        ("other_workspace", organization, other_workspace),
        ("null_workspace", organization, None),
        ("foreign_org", foreign_org, foreign_workspace),
    ):
        dataset = Dataset.no_workspace_objects.create(
            name=f"OCD {kind}", organization=org, workspace=ws, column_order=[]
        )
        datasets[kind] = dataset
        columns[kind] = Column.no_workspace_objects.create(
            name=f"ocd_{kind}",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
    dataset = datasets["same"]
    prompt = RunPrompter.no_workspace_objects.create(
        name="OCD output",
        model="",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        messages=_messages(columns["same"].id),
        status=StatusType.COMPLETED.value,
    )
    output = Column.no_workspace_objects.create(
        name="OCD output",
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=prompt.id,
        data_type=DataTypeChoices.TEXT.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, row=row, column=output, value="OCD existing")
    missing = uuid4()
    assert not Column.no_workspace_objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-OCD missing column must not exist"
    )
    assert datasets["foreign_org"].organization_id != organization.id, (
        "SETUP_AQ-XWS-OCD foreign organization differs"
    )
    assert datasets["other_dataset"].organization_id == organization.id, (
        "SETUP_AQ-XWS-OCD other dataset shares organization"
    )
    assert datasets["other_dataset"].id != dataset.id, (
        "SETUP_AQ-XWS-OCD other dataset differs"
    )
    assert datasets["null_workspace"].workspace_id is None, (
        "SETUP_AQ-XWS-OCD legacy dataset has NULL workspace"
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
    # Materialize all persisted fields, including timestamps, before each request.
    return (
        list(RunPrompter.no_workspace_objects.order_by("pk").values()),
        list(Column.no_workspace_objects.order_by("pk").values()),
        list(Dataset.no_workspace_objects.order_by("pk").values()),
        list(Cell.objects.order_by("pk").values()),
    )


def _shape(response):
    return response.status_code, response.data


@pytest.fixture
def writer(monkeypatch, graph):
    dispatch = Mock(return_value=SimpleNamespace(id="ocd-workflow"))
    monkeypatch.setitem(
        sys.modules,
        "model_hub.tasks.run_prompt",
        SimpleNamespace(process_prompts_single=SimpleNamespace(apply_async=dispatch)),
    )
    monkeypatch.setattr(run_prompt, "is_model_in_catalog", lambda *args, **kwargs: True)
    monkeypatch.setattr("tfc.ee_gates.turing_oss_gate_response", lambda model: None)

    def post(operation, messages=None):
        payload = {
            "dataset_id": str(graph.dataset.id),
            "name": f"OCD changed {uuid4()}",
        }
        if operation == "legacy":
            payload.update(model="gpt-4o-mini", messages=messages)
            view = run_prompt.LitellmAPIView
        else:
            payload["config"] = {} if messages is None else {"messages": messages}
            view = run_prompt.AddRunPromptColumnView
            if operation == "edit":
                payload["column_id"] = str(graph.output.id)
                view = run_prompt.EditRunPromptColumnView
        request = APIRequestFactory().post("/ocd-run-prompt/", payload, format="json")
        request.organization = graph.organization
        request.workspace = graph.workspace
        force_authenticate(request, user=graph.user)
        return view.as_view()(request)

    return SimpleNamespace(post=post, dispatch=dispatch)


@pytest.mark.parametrize(
    "kind", ["foreign_org", "other_dataset", "other_workspace", "null_workspace"]
)
@pytest.mark.parametrize("layout", ["whole", "strings"])
def test_legacy_writer_refuses_foreign_and_missing_without_effects(
    graph, writer, kind, layout
):
    before = _snapshot()
    foreign = writer.post("legacy", _messages(graph.columns[kind].id, layout))
    missing = writer.post("legacy", _messages(graph.missing, layout))
    expected = _shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert (
        _shape(foreign),
        _shape(missing),
        _snapshot(),
        writer.dispatch.call_count,
    ) == (expected, expected, before, 0), (
        "AQ-XWS-OCD: legacy writer must refuse foreign and missing identically without mutation or dispatch"
    )


@pytest.mark.parametrize("operation", ["add", "edit"])
@pytest.mark.parametrize("layout", ["parts", "strings", "mixed"])
@pytest.mark.parametrize(
    "kind", ["foreign_org", "other_dataset", "other_workspace", "null_workspace"]
)
def test_split_text_refuses_foreign_and_missing_without_effects(
    graph, writer, operation, layout, kind
):
    before = _snapshot()
    foreign = writer.post(operation, _messages(graph.columns[kind].id, layout))
    missing = writer.post(operation, _messages(graph.missing, layout))
    expected = _shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert (
        _shape(foreign),
        _shape(missing),
        _snapshot(),
        writer.dispatch.call_count,
    ) == (expected, expected, before, 0), (
        "AQ-XWS-OCD: add/edit must refuse split foreign and missing identically without mutation or dispatch"
    )


@pytest.mark.parametrize("layout", ["parts", "strings", "mixed"])
@pytest.mark.parametrize("kind", ["foreign_org", "other_dataset"])
def test_retained_split_text_is_revalidated(graph, writer, layout, kind):
    observed = []
    for column_id in (graph.columns[kind].id, graph.missing):
        graph.prompt.messages = _messages(column_id, layout)
        graph.prompt.save(update_fields=["messages"])
        before = _snapshot()
        dispatches = writer.dispatch.call_count
        response = writer.post("edit")
        observed.append(
            (
                _shape(response),
                _snapshot() == before,
                writer.dispatch.call_count - dispatches,
            )
        )
    expected = _shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert observed == [(expected, True, 0), (expected, True, 0)], (
        "AQ-XWS-OCD: retained split foreign and missing must share refusal without mutation or dispatch"
    )


@pytest.mark.parametrize(
    ("operation", "layout"),
    [
        ("legacy", "whole"),
        ("legacy", "strings"),
        ("add", "parts"),
        ("add", "strings"),
        ("add", "mixed"),
        ("edit", "parts"),
        ("edit", "strings"),
        ("edit", "mixed"),
        ("retained", "parts"),
        ("retained", "strings"),
        ("retained", "mixed"),
    ],
)
def test_same_dataset_placeholders_remain_accepted(graph, writer, operation, layout):
    messages = _messages(graph.columns["same"].id, layout)
    if operation == "retained":
        graph.prompt.messages = messages
        graph.prompt.save(update_fields=["messages"])
        response = writer.post("edit")
    else:
        response = writer.post(operation, messages)
    assert response.status_code == 200, (
        "SETUP_AQ-XWS-OCD same-dataset writer must succeed"
    )
    stored = RunPrompter.no_workspace_objects.filter(dataset=graph.dataset)
    if operation in {"edit", "retained"}:
        stored = stored.filter(id=graph.prompt.id)
    else:
        stored = stored.exclude(id=graph.prompt.id)
    assert any(row.messages == messages for row in stored), (
        "SETUP_AQ-XWS-OCD accepted messages must be persisted"
    )
    assert writer.dispatch.call_count == 1, (
        "SETUP_AQ-XWS-OCD accepted writer must dispatch exactly once"
    )


@pytest.mark.parametrize("path", ["serializer", "view"])
@pytest.mark.parametrize(
    "legacy", [True, False], ids=["null-workspace", "same-workspace"]
)
def test_update_preserves_admitted_binding(graph, monkeypatch, path, legacy):
    column = graph.columns["null_workspace" if legacy else "same"]
    request_context = SimpleNamespace(
        organization=graph.organization, workspace=graph.workspace, user=graph.user
    )
    create = DatasetOptimizationCreateSerializer(
        data={
            "name": "OCD run",
            "column_id": str(column.id),
            "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            "optimizer_config": {"num_variations": 1},
            "user_eval_template_ids": [],
        },
        context={"request": request_context},
    )
    assert create.is_valid(), "SETUP_AQ-XWS-OCD run admission must be valid"
    run = create.save()
    # A later request reloads the object and loses creation-time instance flags.
    run = OptimizeDataset.no_workspace_objects.get(id=run.id)
    expected_binding = (column.dataset.organization_id, column.dataset.workspace_id)
    assert (run.organization_id, run.workspace_id) == expected_binding, (
        "SETUP_AQ-XWS-OCD creation persisted dataset ownership"
    )
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)
    with workspace_context(
        workspace=graph.workspace, organization=graph.organization, user=graph.user
    ):
        if path == "serializer":
            serializer = DatasetOptimizationSerializer(
                run,
                data={"name": "OCD updated run"},
                partial=True,
                context={"request": request_context},
            )
            assert serializer.is_valid(), "SETUP_AQ-XWS-OCD update must be valid"
            serializer.save()
        else:
            request = APIRequestFactory().patch(
                "/ocd-dataset-optimization/",
                {"name": "OCD updated run"},
                format="json",
            )
            request.organization = graph.organization
            request.workspace = graph.workspace
            force_authenticate(request, user=graph.user)
            response = DatasetOptimizationViewSet.as_view({"patch": "partial_update"})(
                request, pk=str(run.id)
            )
            assert response.status_code == 200, (
                "SETUP_AQ-XWS-OCD update view must succeed"
            )
    with workspace_context(workspace=None, organization=None, user=None):
        persisted = OptimizeDataset.no_workspace_objects.select_related(
            "column__dataset"
        ).get(id=run.id)
        try:
            bound_dataset = activities._verify_run_binding(persisted).id
        except ValueError:
            bound_dataset = None
    result = (
        persisted.organization_id,
        persisted.workspace_id,
        bound_dataset,
        persisted.name,
    )
    expected = (*expected_binding, column.dataset_id, "OCD updated run")
    if legacy:
        assert result == expected, (
            "AQ-XWS-OCD: update must preserve NULL dataset workspace and pass worker binding recheck"
        )
    else:
        assert result == expected, (
            "SETUP_AQ-XWS-OCD same-workspace update retains worker-compatible ownership"
        )
