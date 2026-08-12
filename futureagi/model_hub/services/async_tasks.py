"""
Async task aggregation for the notification center data layer.

Long-running, user-initiated jobs (evals, run prompts, experiments) live in
separate models with different status vocabularies. This module exposes a
single query surface that lists recent tasks with a normalized status
(``queued`` / ``running`` / ``completed`` / ``failed``) so the UI can build a
notification center without knowing each model's internals.

Follow-up work (out of scope here): the in-app notification center UI
(bell icon, read/unread state, click-through) that consumes this endpoint.
"""

from model_hub.models.evaluation import Evaluation
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import RunPrompter

# Canonical notification states.
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

# Evaluation model statuses -> canonical states.
_EVALUATION_STATUS_MAP = {
    "pending": QUEUED,
    "processing": RUNNING,
    "completed": COMPLETED,
    "failed": FAILED,
}

# StatusType vocabulary shared by RunPrompter and ExperimentsTable -> canonical states.
_STATUS_TYPE_MAP = {
    "NotStarted": QUEUED,
    "Queued": QUEUED,
    "Running": RUNNING,
    "Processing": RUNNING,
    "Uploading": RUNNING,
    "ExperimentEvaluation": RUNNING,
    "OptimizationEvaluation": RUNNING,
    "Completed": COMPLETED,
    "PartialCompleted": COMPLETED,
    "Failed": FAILED,
    "Error": FAILED,
    "Cancelled": FAILED,
}

TASK_LIMIT_DEFAULT = 50
TASK_LIMIT_MAX = 100


def _normalize_status(status_map, raw_status):
    """Map a model-specific status to the canonical notification state."""
    return status_map.get(raw_status, str(raw_status).lower())


def _evaluation_tasks(organization):
    rows = Evaluation.objects.filter(organization=organization).values(
        "id",
        "eval_template__name",
        "status",
        "error_message",
        "created_at",
        "updated_at",
    )
    return [
        {
            "id": str(row["id"]),
            "type": "evaluation",
            "title": row["eval_template__name"] or "Evaluation",
            "status": _normalize_status(_EVALUATION_STATUS_MAP, row["status"]),
            "error_message": row["error_message"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
        for row in rows
    ]


def _run_prompt_tasks(organization):
    rows = RunPrompter.objects.filter(organization=organization).values(
        "id",
        "name",
        "status",
        "created_at",
        "updated_at",
    )
    return [
        {
            "id": str(row["id"]),
            "type": "run_prompt",
            "title": row["name"] or "Run Prompt",
            "status": _normalize_status(_STATUS_TYPE_MAP, row["status"]),
            "error_message": None,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
        for row in rows
    ]


def _experiment_tasks(organization):
    rows = ExperimentsTable.objects.filter(dataset__organization=organization).values(
        "id",
        "name",
        "status",
        "created_at",
        "updated_at",
    )
    return [
        {
            "id": str(row["id"]),
            "type": "experiment",
            "title": row["name"] or "Experiment",
            "status": _normalize_status(_STATUS_TYPE_MAP, row["status"]),
            "error_message": None,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
        for row in rows
    ]


def list_recent_async_tasks(organization, limit=TASK_LIMIT_DEFAULT):
    """Return recent async tasks for an organization, newest first.

    Aggregates evaluations, run prompts, and experiments into a single list
    with a normalized ``status``. ``limit`` is clamped to ``[1, 100]``.
    Scoping follows the app convention of organization-level access.
    """
    limit = max(1, min(limit, TASK_LIMIT_MAX))
    tasks = (
        _evaluation_tasks(organization)
        + _run_prompt_tasks(organization)
        + _experiment_tasks(organization)
    )
    # ISO-8601 strings from the same database compare lexicographically.
    tasks.sort(key=lambda task: task["created_at"], reverse=True)
    return tasks[:limit]
