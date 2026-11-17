"""A failed evaluation's stored reason on the results read.

A native evaluator whose code executor was down left the Data tab cell at
"Not scored · the evaluation failed" with no way to learn why. The error
score now carries the text its result cell stores ("explanation"); the grid
cell and the full row (the row drawer) return it as stored. A run recorded
before this change has no explanation and still reads as before.

Real route, ORM and auth scope; nothing is mocked.
"""

import pytest

from model_hub.tests import test_results_launch_reads as reads

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

EXECUTOR_DOWN = (
    "Code executor unavailable: the code-executor service could not be reached"
)


def test_a_failed_evaluation_keeps_its_stored_reason_on_the_grid_and_the_row(
    auth_client, organization, workspace, user
):
    dataset, version, examples = reads.seed(organization, workspace, user, 3)
    run, binding = reads.pinned_run(version, examples, user)
    metric = str(binding.metric.id)
    pair = {"metric_id": metric, "evaluator_version_id": str(binding.version.id)}
    reads.record(
        run,
        examples[0],
        score_refs=[
            {
                **pair,
                "status": "error",
                "reason": "evaluator_execution_failed",
                "explanation": EXECUTOR_DOWN,
            }
        ],
    )
    # Recorded before this change: the error score has no explanation.
    reads.record(
        run,
        examples[1],
        score_refs=[
            {**pair, "status": "error", "reason": "evaluator_execution_failed"}
        ],
    )
    reads.score_record(run, examples[2], binding, 1)
    data = reads.result(
        reads.read(auth_client, dataset, runs=str(run.id), baseline="none")
    )
    by_id = {
        r["example_id"]: r["results"][str(run.id)]["scores"][metric]
        for r in data["rows"]
    }

    failed = by_id[str(examples[0].example_id)]
    assert failed["value"] is None and failed["status"] == "not_assessed"
    assert failed["error_trials"] == 1
    assert failed["explanation"] == EXECUTOR_DOWN

    legacy = by_id[str(examples[1].example_id)]
    assert legacy["error_trials"] == 1
    assert legacy.get("explanation") is None

    scored = by_id[str(examples[2].example_id)]
    assert scored["value"] == 1 and scored["error_trials"] == 0
    assert "explanation" not in scored

    # The row drawer reads the full row: the cell and the try both carry it.
    full = reads.result(
        reads.read(
            auth_client,
            dataset,
            runs=str(run.id),
            baseline="none",
            full=True,
            example_id=str(examples[0].example_id),
        )
    )
    cell = full["rows"][0]["results"][str(run.id)]
    assert cell["scores"][metric]["explanation"] == EXECUTOR_DOWN
    trial = cell["trials_detail"][0]["scores"][metric]
    assert trial["status"] == "error" and trial["explanation"] == EXECUTOR_DOWN
