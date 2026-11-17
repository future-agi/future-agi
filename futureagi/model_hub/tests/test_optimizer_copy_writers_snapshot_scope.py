"""Exercise real snapshot production and both experiment-copy endpoints.

A legacy NULL-workspace source keeps a NULL-workspace snapshot: the snapshot
opts out of the ambient default-workspace save signal, and both are admitted
in the default workspace. No admission, ORM, snapshot, remapping, destination
validation, or transaction boundary is mocked.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.models.run_prompt import RunPrompter
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.utils.optimizer_prompt_text import (
    optimizer_column_references,
    optimizer_prompt_text,
)
from model_hub.views import develop_dataset
from model_hub.views.datasets.create import dataset_from_experiment
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context, workspace_context
from tfc.utils.general_methods import GeneralMethods

pytestmark = pytest.mark.django_db


def _messages(column_id, layout):
    text = "Input {{" + str(column_id) + ".value}}"
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


def _state():
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
def snapshot_graph(monkeypatch):
    clear_workspace_context()
    organization = Organization.objects.create(name="W5F organization")
    foreign_org = Organization.objects.create(name="W5F foreign organization")
    user = User.objects.create_user(
        email="w5f-copy@example.test",
        password=None,
        name="W5F",
        organization=organization,
    )
    workspace = Workspace.objects.create(
        name="W5F default", organization=organization, created_by=user, is_default=True
    )
    other_workspace = Workspace.objects.create(
        name="W5F other", organization=organization, created_by=user
    )
    # Dataset versions require an active creator membership
    # (dataset_version_service.create_dataset_version); the user can write to
    # the default workspace only, so other_workspace stays out of reach.
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=membership,
    )
    for module in (develop_dataset, dataset_from_experiment):
        monkeypatch.setattr(module, "log_and_deduct_cost_for_resource_request", None)
        monkeypatch.setattr(module, "get_recommendations", lambda dataset: None)

    def build(source_kind="legacy"):
        source = Dataset.no_workspace_objects.create(
            name=f"W5F {source_kind} {uuid4()}",
            organization=organization,
            workspace=None if source_kind == "legacy" else workspace,
            column_order=[],
        )
        source_input = Column.no_workspace_objects.create(
            name="W5F original input",
            dataset=source,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        row = Row.objects.create(dataset=source, order=0)
        Cell.objects.create(
            dataset=source, row=row, column=source_input, value="W5F source cell"
        )
        experiment = ExperimentsTable.objects.create(
            name="W5F experiment", dataset=source, column=source_input, user=user
        )
        with workspace_context(workspace, organization=organization, user=user):
            snapshot, column_mapping, row_mapping = create_dataset_snapshot(
                source, experiment
            )
        source.refresh_from_db()
        snapshot.refresh_from_db()
        experiment.refresh_from_db()
        expected_workspace = None if source_kind == "legacy" else workspace.id
        assert (
            source.workspace_id,
            snapshot.workspace_id,
            snapshot.organization_id,
            experiment.snapshot_dataset_id,
            set(column_mapping),
            set(row_mapping),
        ) == (
            expected_workspace,
            expected_workspace,
            organization.id,
            snapshot.id,
            {source_input.id},
            {row.id},
        ), "SETUP_AQ-XWS-W5F real snapshot must preserve the exact source scope"
        snapshot_input = column_mapping[source_input.id]
        snapshot_input.name = "W5F snapshot input"
        snapshot_input.save(update_fields=["name"])
        table = ExperimentDatasetTable.objects.create(
            name="W5F results", experiment=experiment
        )
        experiment.experiments_datasets.add(table)
        table.columns.set([snapshot_input])

        def add_prompt(location, messages):
            dataset = source if location == "original" else snapshot
            input_column = source_input if location == "original" else snapshot_input
            prompt = RunPrompter.no_workspace_objects.create(
                name="W5F output",
                model="",
                dataset=dataset,
                organization=dataset.organization,
                workspace=dataset.workspace,
                messages=messages,
            )
            output = Column.no_workspace_objects.create(
                name="W5F output",
                dataset=dataset,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.RUN_PROMPT.value,
                source_id=prompt.id,
            )
            table.columns.add(input_column, output)
            experiment.column = output
            experiment.save(update_fields=["column"])
            return prompt, output

        def post(operation, table_id=None):
            name = f"W5F copy {uuid4()}"
            payload = {"name": name}
            selected_id = table.id if table_id is None else table_id
            if operation == "create":
                view = dataset_from_experiment.CreateDatasetFromExpView
                kwargs = {"exp_dataset_id": str(selected_id)}
            else:
                view = develop_dataset.AddAsNewDataset
                kwargs = {}
                payload.update(
                    dataset_id=str(selected_id),
                    columns={
                        str(column.id): column.name for column in table.columns.all()
                    },
                )
            request = APIRequestFactory().post("/w5f-copy/", payload, format="json")
            request.organization = organization
            request.workspace = workspace
            force_authenticate(request, user=user)
            before = _state()
            with workspace_context(workspace, organization=organization, user=user):
                response = view.as_view()(request, **kwargs)
            return response, before, name

        return SimpleNamespace(
            organization=organization,
            foreign_org=foreign_org,
            workspace=workspace,
            other_workspace=other_workspace,
            source=source,
            source_input=source_input,
            snapshot=snapshot,
            snapshot_input=snapshot_input,
            table=table,
            add_prompt=add_prompt,
            post=post,
        )

    yield build
    clear_workspace_context()


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("source_kind", ["legacy", "default"])
def test_real_snapshot_without_optimizer_references(
    snapshot_graph, operation, source_kind
):
    graph = snapshot_graph(source_kind)
    response, _, name = graph.post(operation)
    destinations = list(Dataset.no_workspace_objects.filter(name=name))
    destination = destinations[0] if len(destinations) == 1 else None
    cells = (
        list(
            Cell.no_workspace_objects.filter(dataset=destination).values_list(
                "value", flat=True
            )
        )
        if destination
        else []
    )
    assert (
        response.status_code,
        len(destinations),
        destination.organization_id if destination else None,
        destination.workspace_id if destination else None,
        cells,
    ) == (200, 1, graph.organization.id, graph.workspace.id, ["W5F source cell"]), (
        "AQ-XWS-W5F: real snapshot copy without optimizer references must succeed"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("source_kind", ["legacy", "default"])
@pytest.mark.parametrize("location", ["original", "snapshot"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
def test_real_snapshot_prompts_remap_to_destination(
    snapshot_graph, operation, source_kind, location, layout
):
    graph = snapshot_graph(source_kind)
    input_column = (
        graph.source_input if location == "original" else graph.snapshot_input
    )
    graph.add_prompt(location, _messages(input_column.id, layout))
    response, _, name = graph.post(operation)
    prompts = list(RunPrompter.no_workspace_objects.filter(dataset__name=name))
    prompt = prompts[0] if len(prompts) == 1 else None
    inputs = (
        list(
            Column.no_workspace_objects.filter(
                dataset_id=prompt.dataset_id, name=input_column.name
            )
        )
        if prompt
        else []
    )
    copied_input = inputs[0] if len(inputs) == 1 else None
    assert (
        response.status_code,
        len(prompts),
        len(inputs),
        optimizer_prompt_text(prompt.messages) if prompt else None,
        optimizer_column_references(prompt.messages) if prompt else None,
        prompt.organization_id if prompt else None,
        prompt.workspace_id if prompt else None,
    ) == (
        200,
        1,
        1,
        "Input {{" + str(copied_input.id) + ".value}}" if copied_input else None,
        {copied_input.id} if copied_input else None,
        graph.organization.id,
        graph.workspace.id,
    ), (
        "AQ-XWS-W5F: independently admitted source prompts must remap to destination-local columns"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("kind", ["foreign_org", "other_workspace"])
def test_inaccessible_original_and_missing_table_have_same_shape(
    snapshot_graph, operation, kind
):
    graph = snapshot_graph()
    changes = (
        {"organization_id": graph.foreign_org.id}
        if kind == "foreign_org"
        else {"workspace_id": graph.other_workspace.id}
    )
    Dataset.no_workspace_objects.filter(id=graph.source.id).update(**changes)
    missing = uuid4()
    assert not ExperimentDatasetTable.no_workspace_objects.filter(
        id=missing
    ).exists(), "SETUP_AQ-XWS-W5F missing experiment table must not exist"
    observed = []
    for table_id in (graph.table.id, missing):
        response, before, _ = graph.post(operation, table_id)
        observed.append((_shape(response), _state() == before))
    assert observed[0] == observed[1] and observed[0][0][0] == 404 and observed[0][1], (
        "AQ-XWS-W5F: inaccessible originals and missing tables must refuse identically without writes"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("kind", ["foreign_org", "other_workspace", "deleted"])
def test_inaccessible_snapshot_and_unknown_reference_have_same_shape(
    snapshot_graph, operation, kind
):
    graph = snapshot_graph()
    changes = {
        "foreign_org": {"organization_id": graph.foreign_org.id},
        "other_workspace": {"workspace_id": graph.other_workspace.id},
        "deleted": {"deleted": True},
    }[kind]
    Dataset.no_workspace_objects.filter(id=graph.snapshot.id).update(**changes)
    response, before, _ = graph.post(operation)
    observed = [(_shape(response), _state() == before)]
    # Restore with an explicitly scoped all_objects query: no_workspace_objects
    # excludes soft-deleted rows. The next refusal must reach message validation.
    Dataset.all_objects.filter(id=graph.snapshot.id).update(
        organization_id=graph.organization.id,
        workspace_id=graph.workspace.id,
        deleted=False,
    )
    missing = uuid4()
    assert not Column.no_workspace_objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-W5F unknown snapshot reference must not exist"
    )
    graph.add_prompt("snapshot", _messages(missing, "parts"))
    response, before, _ = graph.post(operation)
    observed.append((_shape(response), _state() == before))
    expected = _shape(GeneralMethods().not_found("Column or dataset not found"))
    assert observed == [(expected, True), (expected, True)], (
        "AQ-XWS-W5F: inaccessible snapshots and unknown references must refuse identically and atomically"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize(
    "kind", ["same_scope_other_dataset", "foreign_org", "other_workspace"]
)
def test_foreign_and_unknown_message_references_are_destination_local(
    snapshot_graph, operation, layout, kind
):
    graph = snapshot_graph()
    foreign_dataset = Dataset.no_workspace_objects.create(
        name=f"W5F unrelated {uuid4()}",
        organization=graph.foreign_org if kind == "foreign_org" else graph.organization,
        workspace=graph.other_workspace if kind == "other_workspace" else None,
    )
    foreign_column = Column.no_workspace_objects.create(
        name="W5F unrelated input",
        dataset=foreign_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    prompt, _ = graph.add_prompt("snapshot", _messages(graph.snapshot_input.id, layout))
    missing = uuid4()
    assert not Column.no_workspace_objects.filter(id=missing).exists(), (
        "SETUP_AQ-XWS-W5F unknown message reference must not exist"
    )
    observed = []
    for reference in (foreign_column.id, missing):
        prompt.messages = _messages(reference, layout)
        prompt.save(update_fields=["messages"])
        response, before, _ = graph.post(operation)
        observed.append((_shape(response), _state() == before))
    expected = _shape(GeneralMethods().not_found("Column or dataset not found"))
    assert observed == [(expected, True), (expected, True)], (
        "AQ-XWS-W5F: foreign and unknown whole or split references must refuse with full rollback"
    )
