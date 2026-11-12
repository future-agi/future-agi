"""A first experiment shows the dataset's expected values in its Expected column.

The wizard sends the dataset's expected column as column_id. The create view
stores the snapshot copy of that column on the experiment, while each stored
example names its values by the source column. The results read compared the
two, so every row read "No expected value" and the expected column was shown
again as an extra input field.

These tests create the experiment through the real create endpoint, as the
wizard does, then read the run it bound. Only the workflow start is replaced.
"""
import json
import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.native_experiment_runs import (
    NativeExperimentSnapshot,
    NativeRunBinding,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests import test_results_launch_reads as reads

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

CREATE_URL = "/model-hub/experiments/v2/"
ROWS = [("Italy", "Rome"), ("Japan", "Tokyo"), ("France", "Paris")]


@pytest.fixture(autouse=True)
def _no_workflow_start():
    """The create view starts a workflow on commit; no worker runs here."""
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="wf-first-experiment",
    ):
        yield


@pytest.fixture
def world(organization, workspace, user):
    """A new dataset (input, expected_output), a published prompt and an
    evaluator with an expected input, like edit-distance similarity."""
    dataset = Dataset.no_workspace_objects.create(
        name=f"first-experiment-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    question = Column.no_workspace_objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    answer = Column.no_workspace_objects.create(
        name="expected_output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    for order, (country, capital) in enumerate(ROWS):
        row = Row.no_workspace_objects.create(dataset=dataset, order=order)
        Cell.no_workspace_objects.create(
            dataset=dataset, column=question, row=row, value=country
        )
        Cell.no_workspace_objects.create(
            dataset=dataset, column=answer, row=row, value=capital
        )
    dataset.column_order = [str(question.id), str(answer.id)]
    dataset.column_config = {
        str(column.id): {"is_visible": True, "is_frozen": None}
        for column in (question, answer)
    }
    dataset.save()
    prompt = PromptTemplate.no_workspace_objects.create(
        name=f"capital-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "Capital of {{input}}?"}],
            "configuration": {},
        },
        variable_names={},
        is_default=True,
    )
    evaluator = EvalTemplate.no_workspace_objects.create(
        name=f"edit-distance-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "required_keys": ["output", "expected"],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
        },
        criteria="Is {{output}} close to {{expected}}?",
        model="turing_small",
        eval_type="code",
    )
    EvalTemplateVersion.objects.create_version(
        eval_template=evaluator,
        prompt_messages=[{"role": "system", "content": "edit distance"}],
        config_snapshot=dict(evaluator.config),
        criteria="edit distance",
        model="turing_small",
        organization=organization,
        workspace=workspace,
    )
    return {
        "dataset": dataset,
        "question": question,
        "answer": answer,
        "prompt": prompt,
        "prompt_version": prompt_version,
        "evaluator": evaluator,
    }


def wizard_body(world, *, expected_column):
    """The create body the wizard sends: the evaluator's expected input reads
    the dataset's expected column; column_id is the Expected answers pick."""
    answer = str(world["answer"].id)
    return {
        "name": f"first-experiment-{uuid.uuid4().hex[:6]}",
        "dataset_id": str(world["dataset"].id),
        "column_id": answer if expected_column else None,
        "experiment_type": "llm",
        "prompt_config": [
            {
                "name": "capital",
                "prompt_id": str(world["prompt"].id),
                "prompt_version": str(world["prompt_version"].id),
                "model": "gpt-4o-mini",
            }
        ],
        "user_eval_metrics": [
            {
                "template_id": str(world["evaluator"].id),
                "name": "edit distance",
                "config": {"mapping": {"output": "output", "expected": answer}},
                "model": "turing_small",
            }
        ],
    }


def create(client, body):
    response = client.post(CREATE_URL, body, format="json")
    assert response.status_code == status.HTTP_200_OK, response.content
    experiment_id = response.json()["result"].split("experiment_id=")[1].strip()
    experiment = ExperimentsTable.no_workspace_objects.get(id=experiment_id)
    run_id = NativeRunBinding.objects.get(snapshot__experiment=experiment).run_id
    return experiment, str(run_id)


def grid_rows(client, world, run_id, **params):
    data = reads.result(reads.read(client, world["dataset"], runs=run_id, **params))
    return data["rows"]


def test_expected_column_shows_the_dataset_values(auth_client, world):
    experiment, run_id = create(auth_client, wizard_body(world, expected_column=True))

    rows = grid_rows(auth_client, world, run_id)

    assert len(rows) == 3
    for row in rows:
        # Input shows only the input column; the expected column is not
        # repeated as a "+1 field".
        assert list(row["inputs"]) == ["input"], row["inputs"]
        assert row["expected"]["primary"] == "expected_output", row["expected"]
        text = json.dumps(row["inputs"]) + json.dumps(row["expected"])
        assert str(world["answer"].id) not in text
        assert str(experiment.column_id) not in text
    pairs = sorted(
        (row["inputs"]["input"], row["expected"]["value"]["expected_output"])
        for row in rows
    )
    assert pairs == sorted(ROWS)
    # The full row (the row drawer) reads the same Expected.
    first = rows[0]
    full = grid_rows(
        auth_client, world, run_id, full=True, example_id=first["example_id"]
    )[0]
    assert full["inputs"] == first["inputs"]
    assert full["expected"] == first["expected"]


def test_the_evaluator_expected_input_reads_a_captured_column(auth_client, world):
    experiment, _ = create(auth_client, wizard_body(world, expected_column=True))

    # The experiment keeps the snapshot copy of the picked column.
    picked = Column.no_workspace_objects.get(id=experiment.column_id)
    assert picked.dataset_id == experiment.snapshot_dataset_id
    assert picked.source_id == str(world["answer"].id)
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    assert snapshot.column_map[str(world["answer"].id)] == str(experiment.column_id)
    # The evaluator's expected input names that same captured column, which
    # the native evaluator reads from the stored example by snapshot id,
    # source id or name.
    metric = experiment.user_eval_template_ids.get()
    assert metric.config["mapping"]["expected"] == str(experiment.column_id)
    assert metric.config["mapping"]["expected"] in snapshot.column_map.values()


def test_without_an_expected_answers_column_the_read_says_so(auth_client, world):
    # The expected column is mapped only in the evaluator; Expected answers
    # column is left empty.
    _, run_id = create(auth_client, wizard_body(world, expected_column=False))

    rows = grid_rows(auth_client, world, run_id)

    assert len(rows) == 3
    for row in rows:
        assert list(row["inputs"]) == ["input", "expected_output"], row["inputs"]
        assert row["expected"] == {"value": None, "reason": "no_reference_column"}
