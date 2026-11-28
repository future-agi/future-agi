"""``create_synthetic_dataset`` must keep a safe reason a generation failed.

The activity used to log the exception and stop there, so the data tab could
only say "Something went wrong". A reason is now kept in the dataset's
``dataset_config`` under ``synthetic_generation_error``, which the dataset
table response already returns. Only fixed reasons are stored: an exception's
own text can carry internal detail and never reaches the response or the log.
The reason is recorded once Temporal stops retrying, only by the latest
request, and cleared whenever generation starts again.
"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from rest_framework import status
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tasks.develop_dataset import create_synthetic_dataset
from model_hub.utils.synthetic_task_manager import SyntheticTaskManager
from tfc.temporal.drop_in.decorator import _create_real_activity

GENERIC_REASON = {
    "code": "generation_failed",
    "message": "Generation failed; try again or contact support",
}
USAGE_LIMIT_REASON = {
    "code": "usage_limit_reached",
    "message": (
        "Your plan's synthetic data generation limit has been reached. "
        "Upgrade your plan or try again in the next billing period."
    ),
}
# Text an internal failure can carry. None of it may reach the tenant or the log.
INFRA_TEXT = (
    "Code: 210. DB::NetException: Connection refused "
    "(kb-store-0.internal.example:9000) while reading kb_embeddings"
)


def _config(name, kb_id=None):
    config = {
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
    if kb_id:
        config["kb_id"] = kb_id
    return config


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


def _task_kwargs(dataset, request_uuid, validated_data=None):
    return {
        "validated_data": validated_data or _config(dataset.name),
        "dataset_id": dataset.id,
        "organization_id": dataset.organization_id,
        "creating_synthetic_dataset": True,
        "request_uuid": request_uuid,
    }


def _generate(dataset, agent_side_effect, request_uuid=None, validated_data=None):
    if request_uuid is None:
        request_uuid = SyntheticTaskManager().start_task(str(dataset.id))
    with patch(
        "model_hub.tasks.develop_dataset.SyntheticDataAgent",
        side_effect=agent_side_effect,
    ):
        # The activity wrapper's close_old_connections() would close the test
        # transaction's connection; run the activity body directly.
        create_synthetic_dataset._original_func(
            **_task_kwargs(dataset, request_uuid, validated_data)
        )
    return request_uuid


def _table_response(auth_client, dataset):
    response = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert response.status_code == status.HTTP_200_OK
    return response


@pytest.mark.django_db
def test_unexpected_failure_shows_only_the_generic_reason(
    auth_client, synthetic_dataset
):
    raw_text = "Knowledge base Handbook has no indexed documents"

    with pytest.raises(ValueError):
        _generate(synthetic_dataset, ValueError(raw_text))

    synthetic_dataset.refresh_from_db()
    assert synthetic_dataset.dataset_config["synthetic_generation_error"] == {
        **GENERIC_REASON,
        "request_id": ANY,
    }

    # The data tab reads the reason from the dataset table response.
    response = _table_response(auth_client, synthetic_dataset)
    result = response.json()["result"]
    assert result["dataset_config"]["synthetic_generation_error"] == {
        **GENERIC_REASON,
        "request_id": ANY,
    }
    assert raw_text not in response.content.decode()


@pytest.mark.django_db
def test_infra_error_text_never_reaches_the_tenant_or_the_log(
    auth_client, synthetic_dataset
):
    kb_indexer = MagicMock()
    kb_indexer.return_value.get_subset_kb_id.side_effect = Exception(INFRA_TEXT)
    task_logger = MagicMock()

    with (
        patch("model_hub.tasks.develop_dataset.KBIndexer", kb_indexer),
        patch("model_hub.tasks.develop_dataset.logger", task_logger),
        pytest.raises(Exception, match="DB::NetException"),
    ):
        _generate(
            synthetic_dataset,
            lambda *args, **kwargs: MagicMock(),
            validated_data=_config(
                "Support Tickets", kb_id="0b5f2c1e-5d7a-4c55-9a51-3f3c2d1e0a11"
            ),
        )

    synthetic_dataset.refresh_from_db()
    stored = synthetic_dataset.dataset_config["synthetic_generation_error"]
    assert stored == {**GENERIC_REASON, "request_id": ANY}
    assert "DB::NetException" not in json.dumps(synthetic_dataset.dataset_config)

    body = _table_response(auth_client, synthetic_dataset).content.decode()
    assert "DB::NetException" not in body
    assert "kb-store-0" not in body

    # The server log names the exception type, never its text.
    logged = repr(task_logger.mock_calls)
    assert "DB::NetException" not in logged
    assert "kb-store-0" not in logged
    task_logger.error.assert_any_call(
        "synthetic_generation_failed",
        dataset_id=str(synthetic_dataset.id),
        request_uuid=stored["request_id"],
        error_type="Exception",
        failure_code="generation_failed",
    )


@pytest.mark.django_db
def test_usage_limit_shows_its_fixed_reason(
    auth_client, synthetic_dataset, monkeypatch
):
    internal_reason = "tier internal-7 quota row 42 exhausted"
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *args, **kwargs: SimpleNamespace(allowed=False, reason=internal_reason),
    )

    with pytest.raises(ValueError):
        _generate(synthetic_dataset, lambda *args, **kwargs: MagicMock())

    synthetic_dataset.refresh_from_db()
    assert synthetic_dataset.dataset_config["synthetic_generation_error"] == {
        **USAGE_LIMIT_REASON,
        "request_id": ANY,
    }
    assert (
        internal_reason
        not in _table_response(auth_client, synthetic_dataset).content.decode()
    )


@pytest.mark.django_db
def test_generation_clears_the_previous_reason_when_it_starts(synthetic_dataset):
    synthetic_dataset.dataset_config = {
        "eval_recommendations": ["Deterministic Evals"],
        "synthetic_generation_error": dict(USAGE_LIMIT_REASON),
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
        "synthetic_generation_error": {**GENERIC_REASON, "request_id": ANY},
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
        "synthetic_generation_error": dict(USAGE_LIMIT_REASON),
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


@pytest.mark.django_db
def test_regenerating_clears_a_reason_the_replaced_run_wrote_while_starting(
    auth_client, synthetic_dataset, monkeypatch
):
    # The replaced run fails after the view saved the dataset but before the
    # new request became the latest, so its write still passes its own check.
    replaced_request = SyntheticTaskManager().start_task(str(synthetic_dataset.id))
    start_task = SyntheticTaskManager.start_task

    def replaced_run_writes_then_start(self, dataset_id):
        Dataset.objects.filter(id=synthetic_dataset.id).update(
            dataset_config={
                "synthetic_generation_error": {
                    **GENERIC_REASON,
                    "request_id": replaced_request,
                }
            }
        )
        return start_task(self, dataset_id)

    monkeypatch.setattr(
        SyntheticTaskManager, "start_task", replaced_run_writes_then_start
    )
    monkeypatch.setattr(
        "model_hub.views.datasets.create.synthetic.create_synthetic_dataset.delay",
        lambda *args, **kwargs: None,
    )

    response = auth_client.put(
        f"/model-hub/develops/{synthetic_dataset.id}/update-synthetic-config/",
        {**_config("Support Tickets"), "regenerate": True},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    synthetic_dataset.refresh_from_db()
    assert "synthetic_generation_error" not in synthetic_dataset.dataset_config


def _run_as_activity(
    dataset,
    request_uuid,
    attempt,
    error=None,
    retry_policy=None,
):
    """Run the task the way the worker does, as attempt ``attempt``.

    This goes through the real activity wrapper, which runs the task body on
    another thread, so the attempt has to reach that thread. Without
    ``retry_policy`` the activity has the drop-in workflow's 3 attempts.
    """
    registered = _create_real_activity(
        create_synthetic_dataset._activity_name,
        create_synthetic_dataset._sync_wrapper,
    )
    env = ActivityEnvironment()
    env.info = replace(env.info, attempt=attempt, retry_policy=retry_policy)
    with patch(
        "model_hub.tasks.develop_dataset.SyntheticDataAgent",
        side_effect=error or RuntimeError("LLM provider timed out"),
    ):
        asyncio.run(
            env.run(
                registered,
                {"args": [], "kwargs": _task_kwargs(dataset, request_uuid)},
            )
        )


@pytest.mark.django_db(transaction=True)
def test_a_failure_temporal_will_retry_records_no_reason(synthetic_dataset):
    request_uuid = SyntheticTaskManager().start_task(str(synthetic_dataset.id))

    with pytest.raises(RuntimeError):
        _run_as_activity(synthetic_dataset, request_uuid, attempt=1)

    # Attempt 1 of 3 is retried, so the data tab must keep showing generation.
    synthetic_dataset.refresh_from_db()
    assert "synthetic_generation_error" not in (synthetic_dataset.dataset_config or {})


@pytest.mark.django_db(transaction=True)
def test_the_last_attempt_records_the_reason(synthetic_dataset):
    request_uuid = SyntheticTaskManager().start_task(str(synthetic_dataset.id))

    with pytest.raises(RuntimeError):
        _run_as_activity(synthetic_dataset, request_uuid, attempt=3)

    synthetic_dataset.refresh_from_db()
    assert synthetic_dataset.dataset_config["synthetic_generation_error"] == {
        **GENERIC_REASON,
        "request_id": request_uuid,
    }


@pytest.mark.django_db(transaction=True)
def test_a_non_retryable_failure_records_the_reason_on_the_first_attempt(
    synthetic_dataset,
):
    request_uuid = SyntheticTaskManager().start_task(str(synthetic_dataset.id))

    # Temporal does not retry this error, so attempt 1 is the run's outcome.
    with pytest.raises(ApplicationError):
        _run_as_activity(
            synthetic_dataset,
            request_uuid,
            attempt=1,
            error=ApplicationError("agent unavailable", non_retryable=True),
        )

    synthetic_dataset.refresh_from_db()
    assert synthetic_dataset.dataset_config["synthetic_generation_error"] == {
        **GENERIC_REASON,
        "request_id": request_uuid,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("attempt, recorded", [(1, False), (2, True)])
def test_the_activity_retry_policy_sets_the_last_attempt(
    synthetic_dataset, attempt, recorded
):
    request_uuid = SyntheticTaskManager().start_task(str(synthetic_dataset.id))

    with pytest.raises(RuntimeError):
        _run_as_activity(
            synthetic_dataset,
            request_uuid,
            attempt=attempt,
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

    synthetic_dataset.refresh_from_db()
    stored = (synthetic_dataset.dataset_config or {}).get("synthetic_generation_error")
    if recorded:
        assert stored == {**GENERIC_REASON, "request_id": request_uuid}
    else:
        assert stored is None
