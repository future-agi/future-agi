"""Run Prompt references retain column provenance through experiment conversion."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.models.run_prompt import RunPrompter
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.utils.optimizer_prompt_text import optimizer_column_references
from model_hub.views.datasets.create import dataset_from_experiment

pytestmark = pytest.mark.django_db


@pytest.fixture
def conversion_graph(organization, workspace, user, monkeypatch):
    monkeypatch.setattr(
        dataset_from_experiment, "log_and_deduct_cost_for_resource_request", None
    )
    monkeypatch.setattr(dataset_from_experiment, "get_recommendations", lambda _: None)

    def build(*, snapshot=True, with_input=True):
        dataset = Dataset.objects.create(
            name=f"Prompt provenance {uuid4()}",
            organization=organization,
            workspace=workspace,
        )
        input_column = Column.objects.create(
            dataset=dataset,
            name="input",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        messages = [{"role": "user", "content": "Produce a reference"}]
        if with_input:
            messages[0]["content"] += " for {{" + str(input_column.id) + ".value}}"
        prompt = RunPrompter.objects.create(
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            name="Reference prompt",
            model="",
            messages=messages,
        )
        reference = Column.objects.create(
            dataset=dataset,
            name="reference",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.RUN_PROMPT.value,
            source_id=str(prompt.id),
        )
        assert reference.id != prompt.id
        row = Row.objects.create(dataset=dataset, order=0)
        for column, value in (
            (input_column, "captured input"),
            (reference, "reference result"),
        ):
            Cell.objects.create(dataset=dataset, row=row, column=column, value=value)
        experiment = ExperimentsTable.objects.create(
            name="Prompt reference experiment",
            dataset=dataset,
            column=reference,
            user=user,
        )
        result_dataset = dataset
        result_row = row
        if snapshot:
            result_dataset, columns, rows = create_dataset_snapshot(dataset, experiment)
            experiment.column = columns[reference.id]
            experiment.save(update_fields=["column"])
            result_row = rows[row.id]
            assert experiment.column.source_id == str(reference.id)
            # Conversion must read the captured cells, not subsequently edited inputs.
            Cell.objects.filter(column=input_column).update(value="edited live input")
        table = ExperimentDatasetTable.objects.create(
            name="Completed variant",
            experiment=experiment,
            status=StatusType.COMPLETED.value,
        )
        result_column = Column.objects.create(
            dataset=result_dataset,
            name="variant output",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(
            dataset=result_dataset,
            row=result_row,
            column=result_column,
            value="variant result",
        )
        # Reference and prompt inputs are discovered by the converter, not preselected.
        table.columns.add(result_column)

        def post():
            name = f"Converted prompt reference {uuid4()}"
            request = APIRequestFactory().post(
                f"/model-hub/develops/{table.id}/create-dataset/",
                {"name": name},
                format="json",
            )
            request.organization = organization
            request.workspace = workspace
            force_authenticate(request, user=user)
            response = dataset_from_experiment.CreateDatasetFromExpView.as_view()(
                request, exp_dataset_id=str(table.id)
            )
            return response, name

        return SimpleNamespace(
            dataset=dataset,
            input=input_column,
            reference=reference,
            prompt=prompt,
            experiment=experiment,
            result_dataset=result_dataset,
            post=post,
        )

    return build


@pytest.mark.parametrize("snapshot", [True, False], ids=["snapshot", "live"])
@pytest.mark.parametrize(
    "with_input", [True, False], ids=["column-input", "literal-input"]
)
def test_run_prompt_reference_conversion_resolves_provenance(
    conversion_graph, snapshot, with_input
):
    graph = conversion_graph(snapshot=snapshot, with_input=with_input)
    response, name = graph.post()

    assert response.status_code == 200, response.data
    destination = Dataset.objects.get(name=name)
    copied_reference = Column.objects.get(dataset=destination, name="reference")
    copied_prompt = RunPrompter.objects.get(
        dataset=destination, id=copied_reference.source_id
    )
    assert copied_prompt.id != graph.prompt.id
    assert copied_reference.source == SourceChoices.RUN_PROMPT.value
    assert (copied_prompt.organization_id, copied_prompt.workspace_id) == (
        destination.organization_id,
        destination.workspace_id,
    )
    assert Cell.objects.get(column=copied_reference).value == "reference result"
    assert (
        Cell.objects.get(dataset=destination, column__name="variant output").value
        == "variant result"
    )
    if with_input:
        copied_input = Column.objects.get(dataset=destination, name="input")
        assert optimizer_column_references(copied_prompt.messages) == {copied_input.id}
        assert Cell.objects.get(column=copied_input).value == "captured input"
    else:
        assert copied_prompt.messages == graph.prompt.messages
    graph.experiment.column.refresh_from_db()
    graph.prompt.refresh_from_db()
    assert graph.experiment.column.source_id == str(
        graph.reference.id if snapshot else graph.prompt.id
    )
    assert optimizer_column_references(graph.prompt.messages) == (
        {graph.input.id} if with_input else set()
    )


@pytest.mark.parametrize(
    "unavailable", ["deleted-column", "unrelated-column", "deleted-prompt"]
)
def test_snapshot_prompt_provenance_must_remain_in_authorized_source(
    conversion_graph, unavailable
):
    graph = conversion_graph()
    if unavailable == "deleted-column":
        Column.objects.filter(id=graph.reference.id).update(deleted=True)
    elif unavailable == "unrelated-column":
        unrelated = Dataset.objects.create(
            name="Unrelated source",
            organization=graph.dataset.organization,
            workspace=graph.dataset.workspace,
        )
        Column.objects.filter(id=graph.reference.id).update(dataset=unrelated)
    else:
        RunPrompter.objects.filter(id=graph.prompt.id).update(deleted=True)
    before = (
        Dataset.objects.count(),
        Column.objects.count(),
        RunPrompter.objects.count(),
    )

    response, name = graph.post()

    assert response.status_code == 404, response.data
    assert not Dataset.objects.filter(name=name).exists()
    assert (
        Dataset.objects.count(),
        Column.objects.count(),
        RunPrompter.objects.count(),
    ) == before
