"""``create_synthetic_dataset`` must keep the reason a generation failed.

The activity used to log the exception and stop there, so the data tab could
only say "Something went wrong". The reason is now kept in the dataset's
``dataset_config`` under ``synthetic_generation_error``, which the dataset
table response already returns. It is cleared whenever generation starts again
so a retry never shows an old reason.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tasks.develop_dataset import create_synthetic_dataset
from model_hub.utils.synthetic_task_manager import SyntheticTaskManager


def _config(name):
    return {
        "num_rows": 10,
        "columns": [
            {
                "name": "answer",
                "data_type": "text",
                "description": "Answer",
                "property": "answer",
            }
        ],
        "dataset": {
            "name": name,
            "description": "Dataset",
            "objective": "Generate rows",
            "patterns": "",
        },
    }


@pytest.fixture
def synthetic_dataset(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Support Tickets",
        organization=organization,
        workspace=workspace,
        user=user,
        synthetic_dataset_config=_config("Support Tickets"),
        column_order=[],
        column_config={},
    )
    column = Column.objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True, "is_frozen": None}}
    dataset.save(update_fields=["column_order", "column_config"])
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=column, row=row, value=None)
    return dataset


@pytest.fixture(autouse=True)
def _usage_allowed(monkeypatch):
    # Keep the plan check out of the way so the agent is what fails.
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=""),
    )


def _generate(dataset, agent_side_effect, request_uuid=None):
    if request_uuid is None:
        request_uuid = SyntheticTaskManager().start_task(str(dataset.id))
    with patch(
        "model_hub.tasks.develop_dataset.SyntheticDataAgent",
        side_effect=agent_side_effect,
    ):
        # The activity wrapper's close_old_connections() would close the test
        # transaction's connection; run the activity body directly.
        create_synthetic_dataset._original_func(
            validated_data=_config(dataset.name),
            dataset_id=dataset.id,
            organization_id=dataset.organization_id,
            creating_synthetic_dataset=True,
            request_uuid=request_uuid,
        )


@pytest.mark.django_db
def test_failed_generation_keeps_its_reason_for_the_data_tab(
    auth_client, synthetic_dataset
):
    reason = "Knowledge base Handbook has no indexed documents"

    with pytest.raises(ValueError):
        _generate(synthetic_dataset, ValueError(reason))

    synthetic_dataset.refresh_from_db()
    assert synthetic_dataset.dataset_config["synthetic_generation_error"] == reason

    # The data tab reads the reason from the dataset table response.
    response = auth_client.get(
        f"/model-hub/develops/{synthetic_dataset.id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["dataset_config"]["synthetic_generation_error"] == reason


@pytest.mark.django_db
def test_generation_clears_the_previous_reason_when_it_starts(synthetic_dataset):
    synthetic_dataset.dataset_config = {
        "eval_recommendations": ["Deterministic Evals"],
        "synthetic_generation_error": "Usage limit exceeded",
    }
    synthetic_dataset.save(update_fields=["dataset_config"])
    seen_while_running = []

    def agent_times_out(*args, **kwargs):
        seen_while_running.append(
            Dataset.objects.get(id=synthetic_dataset.id).dataset_config
        )
        raise RuntimeError("LLM provider timed out")

    with pytest.raises(RuntimeError):
        _generate(synthetic_dataset, agent_times_out)

    assert seen_while_running == [{"eval_recommendations": ["Deterministic Evals"]}]
    synthetic_dataset.refresh_from_db()
    assert synthetic_dataset.dataset_config == {
        "eval_recommendations": ["Deterministic Evals"],
        "synthetic_generation_error": "LLM provider timed out",
    }


@pytest.mark.django_db
def test_failure_of_a_replaced_run_is_not_recorded(synthetic_dataset):
    task_manager = SyntheticTaskManager()
    replaced_request = task_manager.start_task(str(synthetic_dataset.id))
    # The user regenerated, so a newer request is now the one that counts.
    task_manager.start_task(str(synthetic_dataset.id))

    with pytest.raises(ValueError):
        _generate(
            synthetic_dataset,
            ValueError("Generation was cancelled"),
            request_uuid=replaced_request,
        )

    synthetic_dataset.refresh_from_db()
    assert "synthetic_generation_error" not in synthetic_dataset.dataset_config


@pytest.mark.django_db
def test_regenerating_clears_the_previous_reason(
    auth_client, synthetic_dataset, monkeypatch
):
    synthetic_dataset.dataset_config = {
        "synthetic_generation_error": "Usage limit exceeded",
    }
    synthetic_dataset.save(update_fields=["dataset_config"])
    queued_tasks = []
    monkeypatch.setattr(
        "model_hub.views.datasets.create.synthetic.create_synthetic_dataset.delay",
        lambda *args, **kwargs: queued_tasks.append(kwargs),
    )

    response = auth_client.put(
        f"/model-hub/develops/{synthetic_dataset.id}/update-synthetic-config/",
        {**_config("Support Tickets"), "regenerate": True},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(queued_tasks) == 1
    synthetic_dataset.refresh_from_db()
    assert "synthetic_generation_error" not in synthetic_dataset.dataset_config
