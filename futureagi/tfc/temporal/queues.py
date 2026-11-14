"""Shared validated routing for exact-read workflows and schedules."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from tfc.logging.temporal import get_logger

EXACT_AGGREGATION_QUEUES = frozenset({"tasks_xl", "exact_aggregation"})


def configured_exact_aggregation_task_queue() -> str | None:
    task_queue = str(
        getattr(settings, "EXACT_AGGREGATION_TASK_QUEUE", "tasks_xl")
    ).strip()
    if task_queue in EXACT_AGGREGATION_QUEUES:
        return task_queue
    get_logger(__name__).error(
        "exact_aggregation_task_queue_invalid",
        configured_queue=task_queue,
        allowed_queues=sorted(EXACT_AGGREGATION_QUEUES),
    )
    return None


def require_exact_aggregation_task_queue() -> str:
    queue = configured_exact_aggregation_task_queue()
    if queue is None:
        raise ImproperlyConfigured(
            "EXACT_AGGREGATION_TASK_QUEUE must be tasks_xl or exact_aggregation."
        )
    return queue
