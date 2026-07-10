"""
Temporal schedules configuration.

This module defines scheduled workflows that run on a recurring basis,
replacing Celery Beat schedules.

Pattern:
- Async functions prefixed with `a_` for direct async usage
- Sync wrappers using `@async_to_sync` from asgiref
- Helper functions: create, update, delete, pause, unpause, trigger, exists
"""

# Config (no temporalio)
from tfc.temporal.schedules.config import ScheduleConfig
from tfc.temporal.schedules.billing import BILLING_SCHEDULES
from tfc.temporal.schedules.integrations import INTEGRATION_SCHEDULES
from tfc.temporal.schedules.model_hub import MODEL_HUB_SCHEDULES
from tfc.temporal.schedules.deployment_telemetry import (
    DEPLOYMENT_TELEMETRY_SCHEDULES,
)
from tfc.temporal.schedules.retention import RETENTION_SCHEDULES
from tfc.temporal.schedules.simulate import SIMULATE_SCHEDULES
from tfc.temporal.schedules.tracer import TRACER_SCHEDULES

# All schedules to be registered
ALL_SCHEDULES = [
    *MODEL_HUB_SCHEDULES,
    *TRACER_SCHEDULES,
    *SIMULATE_SCHEDULES,
    *INTEGRATION_SCHEDULES,
    *BILLING_SCHEDULES,
    *RETENTION_SCHEDULES,
    *DEPLOYMENT_TELEMETRY_SCHEDULES,
]

# Manager functions
from tfc.temporal.schedules.manager import (  # Async functions (a_ prefix); Sync wrappers
    a_create_or_update_schedule,
    a_create_schedule,
    a_delete_schedule,
    a_describe_schedule,
    a_list_schedules,
    a_pause_schedule,
    a_register_schedules,
    a_schedule_exists,
    a_trigger_schedule,
    a_unpause_schedule,
    a_update_schedule,
    create_schedule,
    delete_schedule,
    delete_schedule_async,
    describe_schedule,
    list_schedules,
    list_schedules_async,
    pause_schedule,
    register_schedules,
    register_schedules_async,
    schedule_exists,
    trigger_schedule,
    unpause_schedule,
    update_schedule,
)

__all__ = [
    # Config (no temporalio)
    "ScheduleConfig",
    "MODEL_HUB_SCHEDULES",
    "TRACER_SCHEDULES",
    "SIMULATE_SCHEDULES",
    "INTEGRATION_SCHEDULES",
    "BILLING_SCHEDULES",
    "RETENTION_SCHEDULES",
    "DEPLOYMENT_TELEMETRY_SCHEDULES",
    "ALL_SCHEDULES",
    # Async functions (a_ prefix)
    "a_schedule_exists",
    "a_create_schedule",
    "a_update_schedule",
    "a_delete_schedule",
    "a_pause_schedule",
    "a_unpause_schedule",
    "a_trigger_schedule",
    "a_describe_schedule",
    "a_list_schedules",
    "a_create_or_update_schedule",
    "a_register_schedules",
    # Sync wrappers
    "schedule_exists",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "pause_schedule",
    "unpause_schedule",
    "trigger_schedule",
    "describe_schedule",
    "list_schedules",
    "register_schedules",
    # Legacy aliases
    "register_schedules_async",
    "delete_schedule_async",
    "list_schedules_async",
]
