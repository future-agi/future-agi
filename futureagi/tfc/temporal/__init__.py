"""
Temporal.io core configuration for FutureAGI backend.

This module provides centralized Temporal configuration and utilities.

Structure:
    tfc/temporal/
    ├── __init__.py           # This file - config and re-exports
    ├── common/               # Shared infrastructure
    │   ├── client.py         # Singleton client, workflow helpers
    │   ├── registry.py       # Workflow/activity registration
    │   └── worker.py         # Worker management
    │
    ├── experiments/          # Experiment workflows
    │   ├── activities.py     # Activity definitions
    │   ├── workflows.py      # Workflow definitions
    │   └── client.py         # start_experiment_workflow()
    │
    └── compat.py             # Backwards compatibility

Usage:
    # Configuration
    from tfc.temporal import TEMPORAL_HOST, TASK_QUEUES, get_queue_name

    # Client operations
    from tfc.temporal import get_client, start_workflow_async

    # Registry
    from tfc.temporal import register_for_queues, get_workflows_for_queue

    # Worker
    from tfc.temporal import run_worker, start_worker

    # Experiments (feature-specific)
    from tfc.temporal.experiments import start_experiment_workflow
"""

import os

# =============================================================================
# Configuration
# =============================================================================

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")

# Task queues - mirrors Celery queues
# Each queue runs in its own container/pod
TASK_QUEUES = {
    "default": "default",
    "trace_ingestion": "trace_ingestion",
    "tasks_xl": "tasks_xl",
    "exact_aggregation": "exact_aggregation",
    "property_catalog_dev_sidecar": "property_catalog_dev_sidecar",
    "tasks_l": "tasks_l",
    "tasks_s": "tasks_s",
    "agent_compass": "agent_compass",
}


def get_queue_name(celery_style_name: str) -> str:
    """Convert Celery-style queue name to Temporal queue name."""
    return TASK_QUEUES.get(celery_style_name, celery_style_name)


# =============================================================================
# Direct imports
# =============================================================================

# Client functions
from tfc.temporal.common.client import (  # noqa: E402
    cancel_workflow_async,
    cancel_workflow_sync,
    get_client,
    get_client_sync,
    get_workflow_status_async,
    get_workflow_status_sync,
    start_workflow_async,
    start_workflow_sync,
)

# Registry functions
from tfc.temporal.common.registry import (  # noqa: E402
    get_activities_for_queue,
    get_all_queues,
    get_registry_info,
    get_workflows_for_queue,
    register_activities,
    register_for_queues,
    register_workflows,
)

# Worker functions
from tfc.temporal.common.worker import run_worker, start_worker  # noqa: E402

# Compat functions
from tfc.temporal.compat import (  # noqa: E402
    get_temporal_client,
    get_workflows_and_activities_for_queue,
)

# Drop-in replacement functions
from tfc.temporal.drop_in import (  # noqa: E402
    TaskRunnerWorkflow,
    get_drop_in_activities,
    get_drop_in_workflows,
    start_activity,
    start_activity_async,
    start_activity_sync,
    temporal_activity,
)

# Schedule functions
from tfc.temporal.schedules import (  # noqa: E402
    ALL_SCHEDULES,
    MODEL_HUB_SCHEDULES,
    PROPERTY_CATALOG_SCHEDULES,
    TRACER_SCHEDULES,
    ScheduleConfig,
    register_schedules,
    register_schedules_async,
)

# =============================================================================
# Error Tracking Integration
# =============================================================================


def init_sentry(queue_name: str = "default"):
    """
    Initialize error tracking for Temporal workers.

    This MUST be called explicitly (e.g., from worker startup), NOT at module load time.
    Importing at module load would break Temporal's workflow sandbox validation.

    Args:
        queue_name: The Temporal task queue name (for tagging)
    """
    try:
        from tfc.logging import init_sentry as _init_sentry

        _init_sentry(component="temporal", tags={"queue": queue_name})
    except Exception:
        pass  # initialization is optional


def configure_sentry_for_temporal(queue_name: str = "default"):
    """
    Configure error tracking with Temporal-specific settings after init_sentry().

    This adds worker-level tags and context. Called automatically by run_worker().

    Args:
        queue_name: The Temporal task queue name
    """
    try:
        from tfc.temporal.common.sentry_interceptor import (
            configure_sentry_for_temporal as _configure,
        )

        _configure(queue_name=queue_name)
    except Exception:
        pass


# NOTE: Do NOT call init_sentry() at module load time.
# It must be called explicitly from worker startup to avoid breaking
# Temporal's workflow sandbox validation.


__all__ = [
    # Configuration
    "TEMPORAL_HOST",
    "TEMPORAL_NAMESPACE",
    "TASK_QUEUES",
    "get_queue_name",
    # Client
    "get_client",
    "get_client_sync",
    "start_workflow_async",
    "start_workflow_sync",
    "get_workflow_status_async",
    "get_workflow_status_sync",
    "cancel_workflow_async",
    "cancel_workflow_sync",
    # Registry
    "register_workflows",
    "register_activities",
    "register_for_queues",
    "get_workflows_for_queue",
    "get_activities_for_queue",
    "get_all_queues",
    "get_registry_info",
    # Worker
    "run_worker",
    "start_worker",
    # Error tracking
    "init_sentry",
    "configure_sentry_for_temporal",
    # Backwards compatibility
    "get_temporal_client",
    "get_workflows_and_activities_for_queue",
    # Drop-in Celery replacement
    "temporal_activity",
    "start_activity",
    "start_activity_sync",
    "start_activity_async",
    "TaskRunnerWorkflow",
    "get_drop_in_workflows",
    "get_drop_in_activities",
    # Schedules
    "ScheduleConfig",
    "MODEL_HUB_SCHEDULES",
    "PROPERTY_CATALOG_SCHEDULES",
    "TRACER_SCHEDULES",
    "ALL_SCHEDULES",
    "register_schedules",
    "register_schedules_async",
]
