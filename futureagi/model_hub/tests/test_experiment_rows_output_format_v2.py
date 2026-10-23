"""Regression test: the experiment rows API reports a V2 experiment's output format.

V2 experiments store their prompt configs in ExperimentPromptConfig and save the
deprecated ``ExperimentsTable.prompt_config`` JSON as ``[]``
(views/experiments.py:4083). The rows view used to derive ``output_format`` from
that JSON only, so every V2 experiment reported ``"text"`` and the Data tab never
offered Show Diff (ExperimentBarDataRightSection.jsx: ``outputFormat !== "string"``).

The two API tests need the Postgres test database (pytest-django ``db``); run
them in CI. The helper test at the bottom needs no database.
"""

from types import SimpleNamespace

import pytest

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)

# organization / workspace / auth_client come from futureagi/conftest.py.


def _v2_experiment(organization, workspace, *, output_format):
    dataset = Dataset.objects.create(
        name="Support tickets",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    source_column = Column.objects.create(
        name="expected_reply",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    snapshot = Dataset.objects.create(
        name="Support tickets (snapshot)",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    snapshot_column = Column.objects.create(
        name="expected_reply",
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        source_id=str(source_column.id),
    )
    experiment = ExperimentsTable.objects.create(
        name="support-replies-oct",
        dataset=dataset,
        column=snapshot_column,
        snapshot_dataset=snapshot,
        status=StatusType.COMPLETED.value,
        prompt_config=[],  # what V2 create saves (views/experiments.py:4083)
        experiment_type="llm",
    )
    edt = ExperimentDatasetTable.objects.create(
        name="support-reply-gpt-4o",
        status=StatusType.COMPLETED.value,
        experiment=experiment,
    )
    ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        name="support-reply-gpt-4o",
        model="gpt-4o",
        output_format=output_format,
        order=0,
    )
    return experiment


@pytest.mark.django_db
@pytest.mark.parametrize("column_config_only", ["true", "false"])
@pytest.mark.parametrize("output_format", ["string", "object"])
def test_rows_report_v2_output_format_from_prompt_config_model(
    auth_client, organization, workspace, column_config_only, output_format
):
    experiment = _v2_experiment(organization, workspace, output_format=output_format)

    response = auth_client.get(
        f"/model-hub/experiments/v2/{experiment.id}/rows/",
        {
            "column_config_only": column_config_only,
            "current_page_index": 0,
            "page_size": 10,
        },
    )

    assert response.status_code == 200, response.content
    assert response.json()["result"]["output_format"] == output_format


@pytest.mark.django_db
def test_rows_keep_legacy_output_format_for_v1_experiments(
    auth_client, organization, workspace
):
    dataset = Dataset.objects.create(
        name="Legacy dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    column = Column.objects.create(
        name="expected_reply",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    experiment = ExperimentsTable.objects.create(
        name="legacy",
        dataset=dataset,
        column=column,
        status=StatusType.COMPLETED.value,
        prompt_config=[
            {
                "name": "legacy prompt",
                "messages": [{"role": "user", "content": "{{expected_reply}}"}],
                "model": ["gpt-4o"],
                "configuration": {},
                "output_format": "string",
            }
        ],
    )

    response = auth_client.get(
        f"/model-hub/experiments/v2/{experiment.id}/rows/",
        {"column_config_only": "true", "current_page_index": 0, "page_size": 10},
    )

    assert response.status_code == 200, response.content
    assert response.json()["result"]["output_format"] == "string"


def test_rows_output_format_reads_prompt_config_model_for_v2(monkeypatch):
    """No database: the ORM read is replaced, the view helper is real."""
    from model_hub.views import experiments as views

    filters = []

    class _EpcQuery:
        def __init__(self, **kwargs):
            filters.append(kwargs)

        def first(self):
            return SimpleNamespace(output_format="string")

    deadline_calls = []
    deadline = SimpleNamespace(before_query=lambda: deadline_calls.append(1))
    monkeypatch.setattr(
        views.ExperimentPromptConfig.objects, "filter", lambda **kw: _EpcQuery(**kw)
    )
    v2_experiment = SimpleNamespace(prompt_config=[])

    assert views._rows_output_format(v2_experiment, True, deadline) == "string"
    assert filters == [
        {
            "experiment_dataset__experiment": v2_experiment,
            "experiment_dataset__deleted": False,
        }
    ]
    assert deadline_calls == [1]

    v1_experiment = SimpleNamespace(prompt_config=[{"output_format": "string"}])
    assert views._rows_output_format(v1_experiment, False, deadline) == "string"
    assert (
        views._rows_output_format(SimpleNamespace(prompt_config=[]), False, deadline)
        == "text"
    )
    assert deadline_calls == [1]
