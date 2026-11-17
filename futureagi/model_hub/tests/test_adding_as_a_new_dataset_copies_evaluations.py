"""Adding data as a new dataset copies its evaluations and run prompts.

From an experiment's output table, each evaluation column gets a new binding
and each run-prompt column a new prompt, both in the new dataset's workspace,
whatever workspace the source belongs to. A copied binding keeps its eval group
only when the group is in the new dataset's workspace. A plain dataset's columns
are copied as data. Usage accounting and the new dataset's recommendations are
replaced; the copy itself runs as the product runs it. Every value here is
synthetic.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.models.run_prompt import RunPrompter

DEVELOP_DATASET = "model_hub.views.develop_dataset"


def source_dataset(organization, workspace):
    """A dataset in ``workspace`` with one output column."""
    dataset = Dataset.objects.create(
        name=f"add-as-new-source-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    output = Column.objects.create(
        name="output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    return SimpleNamespace(dataset=dataset, output=output)


def evaluation_binding(data, organization, workspace, user, group_workspace=None):
    """An evaluation binding on ``data.dataset`` that has a workspace, an eval
    group (in ``group_workspace`` when given) and a pinned template version;
    its mapping reads the output."""
    template = EvalTemplate.no_workspace_objects.create(
        name=f"add-as-new-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="llm",
        criteria="Is {{output}} correct?",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "required_keys": ["output"],
        },
    )
    group = EvalGroup.no_workspace_objects.create(
        name=f"add-as-new-group-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=group_workspace or workspace,
        created_by=user,
    )
    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=dict(template.config),
        organization=organization,
        workspace=workspace,
    )
    return UserEvalMetric.objects.create(
        name="judge",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=data.dataset,
        eval_group=group,
        pinned_version=version,
        config={"mapping": {"output": str(data.output.id)}},
    )


def experiment_table(data):
    """An experiment on ``data.dataset`` and its output table."""
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="add-as-new experiment", dataset=data.dataset, column=data.output
    )
    table = ExperimentDatasetTable.no_workspace_objects.create(
        name="add-as-new output table", experiment=experiment
    )
    experiment.experiments_datasets.add(table)
    return table


def add_column(data, name, source, source_id):
    """A column on ``data.dataset`` whose values come from ``source``."""
    return Column.objects.create(
        name=name,
        dataset=data.dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=source,
        source_id=source_id,
    )


def add_as_new(auth_client, monkeypatch, dataset_id, columns):
    """Add ``columns`` of ``dataset_id`` as a new dataset and return it.

    Fails with the view's logged exception if the view logs one, so a failed
    copy names its cause.
    """
    logger = MagicMock(name="develop_dataset logger")
    monkeypatch.setattr(f"{DEVELOP_DATASET}.logger", logger)
    monkeypatch.setattr(
        f"{DEVELOP_DATASET}.log_and_deduct_cost_for_resource_request", None
    )
    monkeypatch.setattr(f"{DEVELOP_DATASET}.get_recommendations", lambda dataset: None)
    name = f"add-as-new-copy-{uuid.uuid4().hex[:8]}"

    response = auth_client.post(
        "/model-hub/develops/add-as-new/",
        {
            "dataset_id": str(dataset_id),
            "name": name,
            "columns": {
                str(column.id): new_name for column, new_name in columns.items()
            },
        },
        format="json",
    )

    if logger.exception.called:
        pytest.fail(f"the copy failed: {logger.exception.call_args}")
    assert response.status_code == status.HTTP_200_OK, response.content
    return Dataset.no_workspace_objects.get(name=name)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source",
    [SourceChoices.EVALUATION.value, SourceChoices.EXPERIMENT_EVALUATION.value],
    ids=["evaluation-column", "experiment-evaluation-column"],
)
def test_adding_an_experiment_table_copies_its_evaluation(
    auth_client, organization, workspace, user, monkeypatch, source
):
    """The copied binding is the new dataset's and in its workspace; it keeps
    the source binding's template, eval group and pinned version, and its
    mapping reads the copied output column."""
    data = source_dataset(organization, workspace)
    binding = evaluation_binding(data, organization, workspace, user)
    table = experiment_table(data)
    source_id = (
        str(binding.id)
        if source == SourceChoices.EVALUATION.value
        else f"{table.id}-{data.output.id}-sourceid-{binding.id}"
    )
    evaluation = add_column(data, "judge-output", source, source_id)
    table.columns.add(data.output, evaluation)

    new_dataset = add_as_new(
        auth_client,
        monkeypatch,
        table.id,
        {data.output: "output", evaluation: "judge-output"},
    )

    assert new_dataset.workspace_id == workspace.id
    new_output = Column.no_workspace_objects.get(dataset=new_dataset, name="output")
    new_evaluation = Column.no_workspace_objects.get(
        dataset=new_dataset, name="judge-output"
    )
    assert new_evaluation.source == SourceChoices.EVALUATION.value
    copied = UserEvalMetric.no_workspace_objects.get(id=new_evaluation.source_id)
    assert copied.id != binding.id
    assert copied.dataset_id == new_dataset.id
    assert copied.workspace_id == new_dataset.workspace_id
    assert copied.organization_id == organization.id
    assert copied.user_id == user.id
    assert copied.template_id == binding.template_id
    assert copied.eval_group_id == binding.eval_group_id
    assert copied.pinned_version_id == binding.pinned_version_id
    assert copied.config == {"mapping": {"output": str(new_output.id)}}


@pytest.mark.django_db
def test_adding_an_experiment_table_drops_an_eval_group_from_another_workspace(
    auth_client, organization, workspace, user, monkeypatch
):
    """A binding whose eval group is in another workspace of the organization
    is copied without the group; the source binding is unchanged."""
    other_workspace = Workspace.objects.create(
        name=f"add-as-new-other-{uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    data = source_dataset(organization, workspace)
    binding = evaluation_binding(
        data, organization, workspace, user, group_workspace=other_workspace
    )
    table = experiment_table(data)
    evaluation = add_column(
        data, "judge-output", SourceChoices.EVALUATION.value, str(binding.id)
    )
    table.columns.add(data.output, evaluation)
    source_before = (
        UserEvalMetric.no_workspace_objects.filter(id=binding.id).values().get()
    )

    new_dataset = add_as_new(
        auth_client,
        monkeypatch,
        table.id,
        {data.output: "output", evaluation: "judge-output"},
    )

    assert new_dataset.workspace_id == workspace.id
    new_evaluation = Column.no_workspace_objects.get(
        dataset=new_dataset, name="judge-output"
    )
    copied = UserEvalMetric.no_workspace_objects.get(id=new_evaluation.source_id)
    assert copied.id != binding.id
    assert copied.dataset_id == new_dataset.id
    assert copied.eval_group_id is None
    assert copied.pinned_version_id == binding.pinned_version_id
    assert (
        UserEvalMetric.no_workspace_objects.filter(id=binding.id).values().get()
        == source_before
    )


@pytest.mark.django_db
def test_adding_an_experiment_table_drops_an_eval_group_with_no_workspace(
    auth_client, organization, workspace, user, monkeypatch
):
    """A binding whose eval group has no workspace is copied without the
    group, even into the default workspace; the source binding is unchanged."""
    data = source_dataset(organization, workspace)
    binding = evaluation_binding(data, organization, workspace, user)
    # A save fills an empty workspace from the context, so clear it in place.
    EvalGroup.no_workspace_objects.filter(id=binding.eval_group_id).update(
        workspace=None
    )
    group = EvalGroup.no_workspace_objects.get(id=binding.eval_group_id)
    assert group.workspace_id is None
    assert group.organization_id == organization.id
    assert workspace.is_default
    table = experiment_table(data)
    evaluation = add_column(
        data, "judge-output", SourceChoices.EVALUATION.value, str(binding.id)
    )
    table.columns.add(data.output, evaluation)
    source_before = (
        UserEvalMetric.no_workspace_objects.filter(id=binding.id).values().get()
    )

    new_dataset = add_as_new(
        auth_client,
        monkeypatch,
        table.id,
        {data.output: "output", evaluation: "judge-output"},
    )

    assert new_dataset.workspace_id == workspace.id
    new_evaluation = Column.no_workspace_objects.get(
        dataset=new_dataset, name="judge-output"
    )
    copied = UserEvalMetric.no_workspace_objects.get(id=new_evaluation.source_id)
    assert copied.id != binding.id
    assert copied.dataset_id == new_dataset.id
    assert copied.eval_group_id is None
    assert copied.pinned_version_id == binding.pinned_version_id
    assert (
        UserEvalMetric.no_workspace_objects.filter(id=binding.id).values().get()
        == source_before
    )


@pytest.mark.django_db
def test_adding_an_experiment_table_copies_its_run_prompt(
    auth_client, organization, workspace, monkeypatch
):
    """The copied prompt is the new dataset's and in its workspace."""
    data = source_dataset(organization, workspace)
    table = experiment_table(data)
    prompt = RunPrompter.no_workspace_objects.create(
        name="answer",
        model="",
        dataset=data.dataset,
        organization=organization,
        workspace=workspace,
        messages=[{"role": "user", "content": "Answer in one sentence."}],
    )
    answer = add_column(data, "answer", SourceChoices.RUN_PROMPT.value, str(prompt.id))
    table.columns.add(data.output, answer)

    new_dataset = add_as_new(
        auth_client,
        monkeypatch,
        table.id,
        {data.output: "output", answer: "answer"},
    )

    assert new_dataset.workspace_id == workspace.id
    new_answer = Column.no_workspace_objects.get(dataset=new_dataset, name="answer")
    assert new_answer.source == SourceChoices.RUN_PROMPT.value
    copied = RunPrompter.no_workspace_objects.get(id=new_answer.source_id)
    assert copied.id != prompt.id
    assert copied.dataset_id == new_dataset.id
    assert copied.workspace_id == new_dataset.workspace_id
    assert copied.organization_id == organization.id


@pytest.mark.django_db
def test_adding_a_dataset_with_an_evaluation_column_as_a_new_dataset(
    auth_client, organization, workspace, user, monkeypatch
):
    """A plain dataset's evaluation column is copied as a column of the new
    dataset, in the new dataset's workspace."""
    data = source_dataset(organization, workspace)
    binding = evaluation_binding(data, organization, workspace, user)
    evaluation = add_column(
        data, "judge-output", SourceChoices.EVALUATION.value, str(binding.id)
    )

    new_dataset = add_as_new(
        auth_client,
        monkeypatch,
        data.dataset.id,
        {data.output: "output", evaluation: "judge-output"},
    )

    assert new_dataset.workspace_id == workspace.id
    assert set(
        Column.no_workspace_objects.filter(dataset=new_dataset).values_list(
            "name", flat=True
        )
    ) == {"output", "judge-output"}
