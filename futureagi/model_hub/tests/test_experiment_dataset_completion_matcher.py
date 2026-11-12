"""An experiment table is complete only when its evaluation columns are.

``check_and_update_experiment_dataset_status`` marks an experiment table
(``ExperimentDatasetTable``) complete once the result and evaluation columns it
owns in the experiment's snapshot are complete. A table's evaluation columns
carry one of two source ids:

* ``<table id>-<output column id>-sourceid-<metric id>``: one column per
  evaluated output column, written by ``EvaluationRunner._get_column_config``
  and ``CompositeEvaluationRunner.build_column_config``;
* ``<table id>-sourceid-<metric id>``: tables from earlier releases, sample
  data and evaluation tag columns.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

PASSWORD = "testpassword123"
FORMATS = ["evaluation", "composite", "earlier"]


def _table_id(first):
    """A table id starting with ``first``: ids with different first digits
    share no prefix."""
    return uuid.UUID(first + uuid.uuid4().hex[1:])


def _cell(column, row, status):
    return Cell.objects.create(
        dataset=column.dataset,
        column=column,
        row=row,
        status=status,
        value="Original",
        value_infos={"original": True},
    )


def _column(dataset, source, source_id, status=StatusType.RUNNING.value):
    return Column.objects.create(
        name=f"Column {uuid.uuid4().hex[:8]}",
        data_type="text",
        dataset=dataset,
        source=source,
        source_id=str(source_id),
        status=status,
    )


def _table(seeded, first):
    table = ExperimentDatasetTable.objects.create(
        id=_table_id(first),
        name=f"Output {first}",
        experiment=seeded.experiment,
        status=StatusType.RUNNING.value,
    )
    output = _column(
        seeded.data,
        SourceChoices.EXPERIMENT.value,
        table.id,
        status=StatusType.COMPLETED.value,
    )
    table.columns.add(output)
    for row in seeded.rows:
        _cell(output, row, CellStatus.PASS.value)
    return SimpleNamespace(table=table, output=output)


def _seed(organization, workspace, *, snapshot=True):
    clear_workspace_context()
    base = Dataset.no_workspace_objects.create(
        name="Source", organization=organization, workspace=workspace
    )
    data = base
    if snapshot:
        data = Dataset.no_workspace_objects.create(
            name="Snapshot", organization=organization, workspace=workspace
        )
    experiment = ExperimentsTable.objects.create(
        name="Completion",
        dataset=base,
        snapshot_dataset=data if snapshot else None,
    )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"completion-{uuid.uuid4().hex[:12]}",
        organization=organization,
        workspace=workspace,
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Completion metric",
        dataset=data,
        template=template,
        organization=organization,
        workspace=workspace,
        source_id=str(experiment.id),
        status=StatusType.RUNNING.value,
    )
    experiment.user_eval_template_ids.add(metric)
    seeded = SimpleNamespace(
        organization=organization,
        workspace=workspace,
        base=base,
        data=data,
        experiment=experiment,
        template=template,
        metric=metric,
        rows=[Row.objects.create(dataset=data, order=order) for order in (1, 2)],
    )
    seeded.own = _table(seeded, "1")
    return seeded


def _evaluation(seeded, owned, fmt):
    """An evaluation column of ``owned`` in the source id format ``fmt``.

    The output-qualified ids come from the runners' own column configs.
    """
    table, output, metric = owned.table, owned.output, seeded.metric
    if fmt == "earlier":
        config = {
            "name": f"{metric.name}-earlier",
            "data_type": "text",
            "source": SourceChoices.EXPERIMENT_EVALUATION.value,
            "source_id": f"{table.id}-sourceid-{metric.id}",
            "dataset": seeded.data,
        }
    elif fmt == "composite":
        from model_hub.tasks.composite_runner import CompositeEvaluationRunner

        config = CompositeEvaluationRunner.build_column_config(
            user_eval_metric=metric,
            dataset=seeded.data,
            experiment_dataset=table,
            column=output,
        )
    else:
        from model_hub.views.eval_runner import EvaluationRunner

        runner = SimpleNamespace(
            eval_template=seeded.template,
            user_eval_metric=metric,
            user_eval_metric_id=metric.id,
            experiment_dataset=table,
            column=output,
            optimize=None,
        )
        config = EvaluationRunner._get_column_config(runner, seeded.data)
    if fmt != "earlier":
        assert config["source_id"] == f"{table.id}-{output.id}-sourceid-{metric.id}"
    assert config["source"] == SourceChoices.EXPERIMENT_EVALUATION.value
    column = Column.objects.create(**config, status=StatusType.RUNNING.value)
    table.columns.add(column)
    return column


def _state(columns):
    ids = [column.id for column in columns]
    return (
        list(
            Column.all_objects.filter(id__in=ids)
            .order_by("id")
            .values("id", "dataset_id", "status", "deleted")
        ),
        list(
            Cell.all_objects.filter(column_id__in=ids)
            .order_by("id")
            .values("id", "dataset_id", "row_id", "column_id", "status", "value")
        ),
    )


def _status(table):
    return ExperimentDatasetTable.all_objects.get(id=table.id).status


def _complete(table):
    from model_hub.views.experiment_runner import (
        check_and_update_experiment_dataset_status,
    )

    return check_and_update_experiment_dataset_status(table.id)


@pytest.mark.parametrize("layout", ["snapshot", "no-snapshot"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_running_evaluation_keeps_the_table_open(organization, workspace, fmt, layout):
    seeded = _seed(organization, workspace, snapshot=layout == "snapshot")
    column = _evaluation(seeded, seeded.own, fmt)
    _cell(column, seeded.rows[0], CellStatus.PASS.value)
    _cell(column, seeded.rows[1], CellStatus.RUNNING.value)
    before = _state([column])

    assert _complete(seeded.own.table) is False

    assert _state([column]) == before
    assert _status(seeded.own.table) == StatusType.RUNNING.value


@pytest.mark.parametrize("fmt", FORMATS)
def test_finished_evaluation_completes_the_table(organization, workspace, fmt):
    seeded = _seed(organization, workspace)
    column = _evaluation(seeded, seeded.own, fmt)
    _cell(column, seeded.rows[0], CellStatus.PASS.value)
    _cell(column, seeded.rows[1], CellStatus.ERROR.value)

    assert _complete(seeded.own.table) is True

    column.refresh_from_db()
    assert column.status == StatusType.COMPLETED.value
    assert _status(seeded.own.table) == StatusType.COMPLETED.value


def test_running_evaluation_tags_keep_the_table_open(organization, workspace):
    seeded = _seed(organization, workspace)
    tags = _column(
        seeded.data,
        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        f"{seeded.own.table.id}-sourceid-{seeded.metric.id}",
    )
    _cell(tags, seeded.rows[0], CellStatus.RUNNING.value)

    assert _complete(seeded.own.table) is False

    assert _status(seeded.own.table) == StatusType.RUNNING.value


@pytest.mark.parametrize("fmt", ["evaluation", "earlier"])
@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_matching_columns_in_other_datasets_are_not_considered(
    organization, workspace, owner, fmt
):
    seeded = _seed(organization, workspace)
    own = _evaluation(seeded, seeded.own, fmt)
    for row in seeded.rows:
        _cell(own, row, CellStatus.PASS.value)
    other_org, other_workspace = organization, workspace
    if owner == "other-tenant":
        other_org = Organization.objects.create(name="Completion tenant B")
        other_workspace = None
    other = Dataset.no_workspace_objects.create(
        name="Unrelated", organization=other_org, workspace=other_workspace
    )
    other_row = Row.objects.create(dataset=other, order=1)
    table_id = seeded.own.table.id
    in_flight = _column(other, SourceChoices.EXPERIMENT_EVALUATION.value, own.source_id)
    idle = _column(
        other,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        f"{table_id}-{uuid.uuid4()}-sourceid-{seeded.metric.id}",
    )
    result = _column(other, SourceChoices.EXPERIMENT.value, table_id)
    _cell(in_flight, other_row, CellStatus.RUNNING.value)
    _cell(idle, other_row, CellStatus.PASS.value)
    _cell(result, other_row, CellStatus.PASS.value)
    before = _state([in_flight, idle, result])

    assert _complete(seeded.own.table) is True

    assert _state([in_flight, idle, result]) == before
    assert _status(seeded.own.table) == StatusType.COMPLETED.value


@pytest.mark.parametrize("fmt", ["evaluation", "earlier"])
def test_other_tables_of_the_experiment_are_unaffected(organization, workspace, fmt):
    seeded = _seed(organization, workspace)
    other = _table(seeded, "f")
    assert str(other.table.id)[0] != str(seeded.own.table.id)[0]
    own = _evaluation(seeded, seeded.own, fmt)
    for row in seeded.rows:
        _cell(own, row, CellStatus.PASS.value)
    in_flight = _evaluation(seeded, other, "evaluation")
    idle = _evaluation(seeded, other, "earlier")
    _cell(in_flight, seeded.rows[0], CellStatus.RUNNING.value)
    _cell(idle, seeded.rows[0], CellStatus.PASS.value)
    before = _state([other.output, in_flight, idle])

    assert _complete(seeded.own.table) is True

    assert _state([other.output, in_flight, idle]) == before
    assert _status(other.table) == StatusType.RUNNING.value
    assert _status(seeded.own.table) == StatusType.COMPLETED.value


# Two tenants, real create API, the workflow's own status steps.

CREATE_URL = "/model-hub/experiments/v2/"


def _login(user, organization, workspace):
    clear_workspace_context()
    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


def _tenant_b():
    clear_workspace_context()
    organization = Organization.objects.create(name="Completion tenant B")
    user = User.objects.create_user(
        email=f"completion-{uuid.uuid4().hex}@futureagi.com",
        password=PASSWORD,
        name="Completion owner B",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.no_workspace_objects.create(
        name="Completion workspace B",
        organization=organization,
        created_by=user,
        is_default=True,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return SimpleNamespace(user=user, organization=organization, workspace=workspace)


def _create_run(user, organization, workspace):
    """Create a one-row prompt experiment with one evaluation through the API."""
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name=f"completion-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
    )
    question = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=question, row=row, value="What is the weather?"
    )
    dataset.column_order = [str(question.id)]
    dataset.save(update_fields=["column_order"])
    template = EvalTemplate.no_workspace_objects.create(
        name=f"completion-eval-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "required_keys": ["output"],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
        },
        criteria="Is {{output}} correct?",
        model="turing_small",
        eval_type="code",
    )
    prompt = PromptTemplate.no_workspace_objects.create(
        name=f"completion-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        is_default=True,
        is_draft=False,
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "completion status"}],
            "configuration": {"temperature": 0, "max_tokens": 64},
        },
    )
    client = _login(user, organization, workspace)
    body = {
        "name": f"completion-{uuid.uuid4().hex}",
        "dataset_id": str(dataset.id),
        "experiment_type": "llm",
        "user_eval_metrics": [
            {
                "template_id": str(template.id),
                "name": "completion judge",
                "config": {"mapping": {"output": str(question.id)}},
                "model": "turing_small",
            }
        ],
        "prompt_config": [
            {
                "name": "completion run",
                "prompt_id": str(prompt.id),
                "prompt_version": str(version.id),
                "model": "gpt-4o-mini",
                "model_params": {},
                "configuration": {},
            }
        ],
    }
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="completion-test",
    ) as start:
        response = client.post(CREATE_URL, body, format="json")
    assert response.status_code == 200, response.content
    start.assert_called_once()
    experiment_id = response.json()["result"].split("experiment_id=")[1].strip()
    clear_workspace_context()
    record = ExperimentPromptConfig.no_workspace_objects.get(
        experiment_dataset__experiment_id=experiment_id
    )
    table = record.experiment_dataset
    snapshot_id = table.experiment.snapshot_dataset_id
    metric = table.experiment.user_eval_template_ids.get()

    from tfc.temporal.experiments.activities import _setup_prompt_v2_sync

    with patch("tfc.temporal.experiments.activities.activity.logger"):
        setup = _setup_prompt_v2_sync(
            str(table.experiment_id), str(snapshot_id), str(record.id)
        )
    clear_workspace_context()
    output = Column.no_workspace_objects.get(id=setup["column_id"])
    assert output.source == SourceChoices.EXPERIMENT.value
    assert output.source_id == str(table.id)
    evaluation = Column.no_workspace_objects.get(
        dataset_id=snapshot_id, source=SourceChoices.EXPERIMENT_EVALUATION.value
    )
    # The create API writes the output-qualified evaluation id.
    assert evaluation.source_id == f"{table.id}-{output.id}-sourceid-{metric.id}"
    return SimpleNamespace(
        client=client,
        experiment_id=experiment_id,
        table=table,
        snapshot_id=snapshot_id,
        output=output,
        evaluation=evaluation,
        row_ids=[uuid.UUID(row_id) for row_id in setup["row_ids"]],
    )


def _write_cells(run, column, status, value):
    from model_hub.views.eval_runner import bulk_update_or_create_cells

    bulk_update_or_create_cells(
        run.row_ids,
        column.id,
        run.snapshot_id,
        {"value": value, "status": status, "value_infos": json.dumps({})},
    )


def _snapshot_state(run):
    clear_workspace_context()
    columns = list(Column.all_objects.filter(dataset_id=run.snapshot_id))
    return _status(run.table), _state(columns)


def _table_view(client, run):
    response = client.get(
        f"/model-hub/develops/{run.table.id}/get-experiment-dataset-table/",
        {"current_page_index": 0, "page_size": 10},
    )
    if response.status_code != 200:
        return response.status_code, None
    result = response.json()["result"]
    config = {c["id"]: c["status"] for c in result["column_config"]}
    cells = [
        row.get(str(run.evaluation.id), {}).get("status") for row in result["table"]
    ]
    return 200, (config.get(str(run.evaluation.id)), cells)


@pytest.fixture
def no_model_call():
    """No step of this run sends a model request."""
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    with patch.object(
        RunPrompt, "litellm_response", side_effect=AssertionError("model call")
    ) as model_call:
        yield model_call


@pytest.mark.django_db(transaction=True)
def test_created_run_stays_open_until_its_evaluation_finishes(
    user, organization, workspace, no_model_call
):
    from tfc.temporal.experiments.activities import (
        _check_column_status_sync,
        _check_experiment_dataset_status_sync,
    )

    tenant_b = _tenant_b()
    run_a = _create_run(user, organization, workspace)
    run_b = _create_run(tenant_b.user, tenant_b.organization, tenant_b.workspace)
    # Tenant B's snapshot also carries a column with A's evaluation id.
    stray = Column.no_workspace_objects.create(
        name="Copied evaluation",
        data_type="text",
        dataset_id=run_b.snapshot_id,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=run_a.evaluation.source_id,
        status=StatusType.RUNNING.value,
    )
    _write_cells(run_b, stray, CellStatus.RUNNING.value, "")
    b_before = _snapshot_state(run_b)
    assert b_before[0] == StatusType.RUNNING.value
    assert _status(run_a.table) == StatusType.RUNNING.value

    # The output row finishes while the evaluation of that row is in flight.
    _write_cells(run_a, run_a.output, CellStatus.PASS.value, "Sunny")
    _write_cells(run_a, run_a.evaluation, CellStatus.RUNNING.value, "")
    clear_workspace_context()
    output_check = _check_column_status_sync(str(run_a.output.id))

    assert output_check["status"] == StatusType.COMPLETED.value
    assert _status(run_a.table) == StatusType.RUNNING.value
    assert _table_view(run_a.client, run_a) == (
        200,
        (CellStatus.RUNNING.value, [CellStatus.RUNNING.value]),
    )
    assert _table_view(run_b.client, run_a) == (404, None)
    assert run_b.client.get(f"{CREATE_URL}{run_a.experiment_id}/").status_code == 404
    assert _status(run_a.table) == StatusType.RUNNING.value

    # The evaluation finishes; the workflow then checks the table.
    _write_cells(run_a, run_a.evaluation, CellStatus.PASS.value, "Passed")
    clear_workspace_context()
    table_check = _check_experiment_dataset_status_sync(str(run_a.table.id))

    assert table_check["is_complete"] is True
    assert table_check["status"] == StatusType.COMPLETED.value
    assert _status(run_a.table) == StatusType.COMPLETED.value
    clear_workspace_context()
    run_a.evaluation.refresh_from_db()
    assert run_a.evaluation.status == StatusType.COMPLETED.value
    assert _table_view(run_a.client, run_a) == (
        200,
        (CellStatus.PASS.value, [CellStatus.PASS.value]),
    )
    assert _snapshot_state(run_b) == b_before
    no_model_call.assert_not_called()
