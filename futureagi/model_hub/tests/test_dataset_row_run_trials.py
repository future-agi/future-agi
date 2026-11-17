"""The row-runs response keeps evidence attached to its recorded trial."""

import uuid

import pytest

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.tests.test_dataset_row_runs import (
    dataset_with_rows,
    metric,
    read,
    result,
    version_of,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def row_with_trials(organization, workspace, user):
    dataset, rows = dataset_with_rows(organization, workspace, user, 1)
    version, items = version_of(dataset, rows)
    item = items[rows[0].id]
    binding = metric(dataset, user)
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        evaluator_metrics=[binding.metric],
        expected_items=[(item.example_id, 1), (item.example_id, 2)],
    )
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="completed")
    attempts = []
    for number, output, value, explanation, trace in (
        (1, "Paris", 1, "Matches the reference.", "a" * 32),
        (2, "Lyon", 0, "Does not match the reference.", "b" * 32),
    ):
        attempts.append(
            ExperimentRunItem.no_workspace_objects.create(
                run=run,
                example_id=item.example_id,
                trial_number=number,
                status="completed",
                output={"text": output},
                trace_id=trace,
                score_refs=[
                    {
                        "metric_id": str(binding.metric.id),
                        "evaluator_version_id": str(binding.version.id),
                        "status": "assessed",
                        "value": value,
                        "explanation": explanation,
                    }
                ],
            )
        )
    return dataset, rows[0], run, binding, attempts


def test_serialized_trials_keep_their_own_evidence(auth_client, row_with_trials):
    dataset, row, run, binding, attempts = row_with_trials
    data = result(read(auth_client, dataset, row))
    assert len(data["runs"]) == 1
    entry = data["runs"][0]
    key = str(binding.metric.id)
    assert entry["run_id"] == str(run.id)
    assert entry["trials"] == 2
    assert entry["evals"][key]["value"] == 0.5
    assert entry["evals"][key]["n"] == 2
    assert entry["evals"][key]["flaky"] is True
    assert entry["trials_detail"] == [
        {
            "trial_number": attempt.trial_number,
            "status": "completed",
            "trace_id": str(uuid.UUID(attempt.trace_id)),
            "output": attempt.output,
            "error": None,
            "scores": {
                key: {
                    "value": attempt.score_refs[0]["value"],
                    "status": "assessed",
                    "passed": attempt.trial_number == 1,
                    "evaluator_version_id": str(binding.version.id),
                    "explanation": attempt.score_refs[0]["explanation"],
                }
            },
        }
        for attempt in attempts
    ]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        ({"kind": "runtime", "message": "The second attempt failed."}, "error"),
        ({"kind": "infra", "message": "The worker was unavailable."}, "infra"),
    ],
)
def test_trial_object_errors_are_preserved(auth_client, row_with_trials, error, status):
    dataset, row, _, binding, attempts = row_with_trials
    ExperimentRunItem.no_workspace_objects.filter(pk=attempts[1].pk).update(
        status="failed", error=error
    )
    entry = result(read(auth_client, dataset, row))["runs"][0]
    first, second = entry["trials_detail"]
    key = str(binding.metric.id)
    assert first["output"] == {"text": "Paris"}
    assert first["error"] is None
    assert first["scores"][key]["value"] == 1
    assert second["trial_number"] == 2
    assert second["status"] == status
    assert second["error"] == error
    assert second["output"] == {"text": "Lyon"}
    assert second["trace_id"] == str(uuid.UUID(attempts[1].trace_id))
    assert second["scores"][key] == {
        "value": None,
        "status": "not_assessed",
        "passed": None,
        "evaluator_version_id": str(binding.version.id),
        "explanation": "Does not match the reference.",
    }
    assert entry["status"] == "completed"
    assert entry["error"] is None
    assert entry["output"] == first["output"]


def test_unrecorded_trial_has_no_borrowed_evidence(auth_client, row_with_trials):
    dataset, row, _, binding, attempts = row_with_trials
    ExperimentRunItem.no_workspace_objects.filter(pk=attempts[1].pk).delete()
    entry = result(read(auth_client, dataset, row))["runs"][0]
    first, second = entry["trials_detail"]
    assert first["output"] == {"text": "Paris"}
    assert first["scores"][str(binding.metric.id)]["value"] == 1
    assert second == {
        "trial_number": 2,
        "status": "not_reached",
        "trace_id": None,
        "output": None,
        "error": None,
        "scores": {},
    }
    assert entry["trials"] == 2
    assert entry["output"] == first["output"]
