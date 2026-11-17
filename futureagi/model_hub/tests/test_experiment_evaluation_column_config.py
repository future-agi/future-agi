"""The column config of an experiment evaluation column.

``GET /model-hub/column-config/<column id>/`` returns, for an experiment
evaluation column, the evaluation metric and the experiment table named by the
column's source id:

* ``<table id>-<output column id>-sourceid-<metric id>``: written by
  ``EvaluationRunner._get_column_config`` and
  ``CompositeEvaluationRunner.build_column_config``;
* ``<table id>-sourceid-<metric id>``: tables from earlier releases.

Only a table and a metric of the column's own dataset resolve.
"""

import json

import pytest

from model_hub.models.choices import SourceChoices, StatusType
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentPromptConfig
from model_hub.tests.test_experiment_dataset_completion_matcher import (
    FORMATS,
    _column,
    _create_run,
    _evaluation,
    _login,
    _seed,
    _tenant_b,
)
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

CONFIG = "/model-hub/column-config/{}/"
EARLIER_CONFIG = {"name": "Earlier run", "model": "gpt-4o-mini", "messages": []}


def _client(user, organization, workspace):
    client = _login(user, organization, workspace)
    # A server error is returned as a response, not raised into the test.
    client.raise_request_exception = False
    return client


def _get(client, column):
    clear_workspace_context()
    return client.get(CONFIG.format(column.id))


def _expected(column):
    """The config the view should return, read from the stored rows."""
    clear_workspace_context()
    column = Column.all_objects.get(id=column.id)
    table_part, metric_id = column.source_id.split("-sourceid-")
    metric = UserEvalMetric.all_objects.select_related("template").get(id=metric_id)
    table = ExperimentDatasetTable.all_objects.get(id=table_part[:36])
    expected = {
        "name": column.name,
        "template": str(metric.template_id),
        "template_config": metric.template.config,
        "description": metric.template.description,
        "config": metric.config,
        "status": metric.status,
        "experiment_dataset": table.name,
        "experiment_dataset_config": table.legacy_prompt_config,
    }
    return json.loads(json.dumps(expected))


def _seeded(organization, workspace, record):
    seeded = _seed(organization, workspace)
    ExperimentDatasetTable.all_objects.filter(id=seeded.own.table.id).update(
        legacy_prompt_config=EARLIER_CONFIG
    )
    if record:
        ExperimentPromptConfig.no_workspace_objects.create(
            experiment_dataset=seeded.own.table, name="Run", model="gpt-4o-mini"
        )
    return seeded


@pytest.mark.parametrize("record", [False, True], ids=["legacy-table", "v2-table"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_evaluation_column_config_renders(user, organization, workspace, fmt, record):
    seeded = _seeded(organization, workspace, record)
    column = _evaluation(seeded, seeded.own, fmt)
    client = _client(user, organization, workspace)

    response = _get(client, column)

    assert response.status_code == 200, response.content[:300]
    result = response.json()["result"]
    assert result == _expected(column)
    assert result["experiment_dataset"] == seeded.own.table.name
    assert result["experiment_dataset_config"] == EARLIER_CONFIG
    assert result["template"] == str(seeded.template.id)


def _foreign(seeded, owner):
    """A tenant and dataset other than the experiment's own."""
    if owner == "other-tenant":
        tenant = _tenant_b()
        dataset = Dataset.no_workspace_objects.create(
            name="Foreign",
            organization=tenant.organization,
            workspace=tenant.workspace,
        )
        return tenant, dataset
    dataset = Dataset.no_workspace_objects.create(
        name="Unrelated",
        organization=seeded.organization,
        workspace=seeded.workspace,
    )
    return None, dataset


@pytest.mark.parametrize("fmt", ["evaluation", "earlier"])
@pytest.mark.parametrize("owner", ["same-tenant", "other-tenant"])
def test_same_id_in_another_dataset_resolves_nothing(
    user, organization, workspace, owner, fmt
):
    seeded = _seeded(organization, workspace, record=True)
    own = _evaluation(seeded, seeded.own, fmt)
    tenant, dataset = _foreign(seeded, owner)
    stray = _column(dataset, SourceChoices.EXPERIMENT_EVALUATION.value, own.source_id)
    if tenant is None:
        client = _client(user, organization, workspace)
    else:
        client = _client(tenant.user, tenant.organization, tenant.workspace)

    response = _get(client, stray)

    assert response.status_code == 404, response.content[:300]
    for secret in (seeded.own.table.name, seeded.metric.name, str(seeded.template.id)):
        assert secret.encode() not in response.content


@pytest.mark.parametrize("fmt", ["evaluation", "earlier"])
@pytest.mark.parametrize("foreign", ["metric", "table"])
def test_foreign_metric_or_table_id_resolves_nothing(
    user, organization, workspace, foreign, fmt
):
    seeded = _seeded(organization, workspace, record=True)
    tenant = _tenant_b()
    other = _seed(tenant.organization, tenant.workspace)
    ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=other.own.table, name="Run B", model="gpt-4o-mini"
    )
    table = other.own.table if foreign == "table" else seeded.own.table
    metric = other.metric if foreign == "metric" else seeded.metric
    if fmt == "earlier":
        source_id = f"{table.id}-sourceid-{metric.id}"
    else:
        source_id = f"{table.id}-{seeded.own.output.id}-sourceid-{metric.id}"
    column = _column(seeded.data, SourceChoices.EXPERIMENT_EVALUATION.value, source_id)
    client = _client(user, organization, workspace)

    response = _get(client, column)

    assert response.status_code == 404, response.content[:300]
    for secret in (other.own.table.name, other.metric.name, str(other.template.id)):
        assert secret.encode() not in response.content


@pytest.mark.parametrize(
    "shape",
    ["{table}x-sourceid-{metric}", "{table}-{metric}"],
    ids=["table-id-suffix", "no-marker"],
)
def test_malformed_evaluation_id_is_not_found(user, organization, workspace, shape):
    seeded = _seeded(organization, workspace, record=True)
    source_id = shape.format(table=seeded.own.table.id, metric=seeded.metric.id)
    column = _column(seeded.data, SourceChoices.EXPERIMENT_EVALUATION.value, source_id)
    client = _client(user, organization, workspace)

    response = _get(client, column)

    assert response.status_code == 404, response.content[:300]


def test_other_tenant_cannot_read_the_column_config(organization, workspace):
    seeded = _seeded(organization, workspace, record=True)
    column = _evaluation(seeded, seeded.own, "evaluation")
    tenant = _tenant_b()
    client = _client(tenant.user, tenant.organization, tenant.workspace)

    response = _get(client, column)

    assert response.status_code == 404, response.content[:300]
    assert seeded.metric.name.encode() not in response.content


# Two tenants, real create API: the evaluation column the product writes.


@pytest.mark.django_db(transaction=True)
def test_created_run_evaluation_column_config_renders(user, organization, workspace):
    tenant_b = _tenant_b()
    run_a = _create_run(user, organization, workspace)
    run_b = _create_run(tenant_b.user, tenant_b.organization, tenant_b.workspace)
    for run in (run_a, run_b):
        run.client.raise_request_exception = False
    clear_workspace_context()
    stray = Column.no_workspace_objects.create(
        name="Copied evaluation",
        data_type="text",
        dataset_id=run_b.snapshot_id,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=run_a.evaluation.source_id,
        status=StatusType.COMPLETED.value,
    )
    metric_id = run_a.evaluation.source_id.split("-sourceid-")[1]
    metric = UserEvalMetric.no_workspace_objects.get(id=metric_id)
    assert metric.dataset_id == run_a.snapshot_id

    response = _get(run_a.client, run_a.evaluation)

    assert response.status_code == 200, response.content[:300]
    result = response.json()["result"]
    assert result == _expected(run_a.evaluation)
    assert result["name"] == run_a.evaluation.name
    assert result["experiment_dataset"] == run_a.table.name
    assert result["config"] == metric.config
    assert _get(run_b.client, run_a.evaluation).status_code == 404
    refused = _get(run_b.client, stray)
    assert refused.status_code == 404, refused.content[:300]
    assert run_a.table.name.encode() not in refused.content
