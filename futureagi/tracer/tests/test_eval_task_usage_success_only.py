"""Eval task Usage counts and lists only successful runs.

Owner rule (2026-09-25): every eval run shows in the eval logs, but only a
successful run is usage. On dev, task 1d955369 had 28 completed and 47 errored
runs (a required span attribute was missing); its Usage tab counted all 75 and
listed the 47 errors, while the template Usage listed none of them. The Task
Logs keep reporting every run.
"""

import pytest

# Break the import cycle (see test_eval_logger_schema.py for the
# canonical comment).
import model_hub.tasks  # noqa: F401
from tracer.models.observation_span import EvalEntryStatus
from tracer.tests.eval_task_factories import (
    make_config as _config,
)
from tracer.tests.eval_task_factories import (
    make_fresh_span as _fresh_span,
)
from tracer.tests.eval_task_factories import (
    make_row as _row,
)
from tracer.tests.eval_task_factories import (
    make_task as _task,
)
from tracer.tests.eval_task_factories import (
    make_template as _template,
)

USAGE_URL = "/tracer/eval-task/get_usage/"
LOGS_URL = "/tracer/eval-task/get_eval_task_logs/"


@pytest.fixture
def task_runs(project, organization, workspace, observation_span):
    template = _template(organization=organization, workspace=workspace)
    cfg = _config(project=project, template=template, name="Toxicity")
    task = _task(project=project)
    success = _row(
        span=observation_span,
        cfg=cfg,
        task=task,
        status=EvalEntryStatus.COMPLETED,
        output_bool=True,
    )
    for fields in (
        # The evaluator raised, or input validation rejected the run.
        {
            "status": EvalEntryStatus.ERRORED,
            "error": True,
            "error_message": (
                "Required attribute 'input.system_prompt' for key "
                "'agent_prompt' not found on trace"
            ),
        },
        # Legacy cron rows keep the default COMPLETED status with error=True.
        {
            "status": EvalEntryStatus.COMPLETED,
            "error": True,
            "error_message": "Error during evaluation: timeout",
            "output_str": "ERROR",
        },
        {
            "status": EvalEntryStatus.SKIPPED,
            "skipped_reason": "missing_required_attribute",
        },
        {"status": EvalEntryStatus.PENDING},
    ):
        _row(span=_fresh_span(observation_span), cfg=cfg, task=task, **fields)
    return task, success


def _result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_task_usage_counts_and_lists_only_successful_runs(auth_client, task_runs):
    task, success = task_runs

    result = _result(
        auth_client.get(USAGE_URL, {"eval_task_id": str(task.id), "period": "30d"})
    )

    stats = result["stats"]
    assert stats["total_runs"] == 1
    assert stats["runs_period"] == 1
    assert stats["success_count"] == 1
    assert stats["error_count"] == 0
    assert sum(bucket["calls"] for bucket in result["chart"]) == 1
    assert result["logs"]["count"] == 1
    assert [row["id"] for row in result["logs"]["results"]] == [str(success.id)]
    assert [row["status"] for row in result["logs"]["results"]] == ["success"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_task_logs_still_report_every_run(auth_client, task_runs):
    task, _success = task_runs

    result = _result(auth_client.get(LOGS_URL, {"eval_task_id": str(task.id)}))

    assert result["total_count"] == 5
    assert result["success_count"] == 2
    assert result["errors_count"] == 1
    assert result["skipped_count"] == 1
