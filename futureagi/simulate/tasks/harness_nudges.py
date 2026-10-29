from __future__ import annotations

from typing import Any

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

__all__ = [
    "check_harness_nudges",
    "finish_harness_nudge_turn",
    "raise_harness_environment_nudge",
    "reconcile_harness_environment_nudges",
    "retry_harness_deferred_activations",
    "schedule_deferred_activations",
    "schedule_finish_turn",
    "schedule_nudge",
    "schedule_nudge_check_task",
    "schedule_reconcile_task",
    "schedule_sandbox_stop",
    "stop_harness_nudge_sandbox",
    "tick_harness_nudges",
]


@temporal_activity(time_limit=120, max_retries=3, queue="default")
def raise_harness_environment_nudge(
    environment_id: str,
    agent_version_id: str,
    default_moved: bool,
    outgoing_agent_version_id: str | None = None,
) -> str:
    """Raise one environment nudge; the inputs are ids and a flag, values are read here."""
    from simulate.services.harness_versioning.nudges import raise_environment_nudge

    close_old_connections()
    try:
        nudge = raise_environment_nudge(
            environment_id,
            agent_version_id,
            default_moved=default_moved,
            outgoing_agent_version_id=outgoing_agent_version_id,
        )
        return "raised" if nudge is not None else "skipped"
    finally:
        close_old_connections()


@temporal_activity(time_limit=120, max_retries=3, queue="default")
def reconcile_harness_environment_nudges(environment_id: str) -> str:
    """Raise what an environment's busy guards or a lost schedule skipped."""
    from simulate.services.harness_versioning.nudges import (
        reconcile_environment_nudges,
    )

    close_old_connections()
    try:
        return reconcile_environment_nudges(environment_id)
    finally:
        close_old_connections()


def schedule_nudge(
    environment_id: Any,
    agent_version_id: Any,
    default_moved: bool,
    outgoing_agent_version_id: Any = None,
):
    """Start a raise; one per environment, version and kind runs at a time."""
    from temporalio.common import WorkflowIDConflictPolicy

    return raise_harness_environment_nudge.apply_async(
        args=[
            str(environment_id),
            str(agent_version_id),
            bool(default_moved),
            str(outgoing_agent_version_id) if outgoing_agent_version_id else None,
        ],
        task_id=(
            f"harness-nudge-{environment_id}-{agent_version_id}-{int(default_moved)}"
        ),
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


def schedule_reconcile_task(environment_id: Any):
    """Start a reconcile; requests for one environment coalesce while one runs."""
    return _unique(
        reconcile_harness_environment_nudges,
        [str(environment_id)],
        f"harness-nudge-reconcile-{environment_id}",
    )


def _unique(task: Any, args: list[Any], task_id: str):
    from temporalio.common import WorkflowIDConflictPolicy

    return task.apply_async(
        args=args,
        task_id=task_id,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


@temporal_activity(time_limit=60, max_retries=0, queue="default")
def tick_harness_nudges() -> dict[str, int]:
    """Find open rebuilds and deferred activations; each gets its own activity."""
    from simulate.services.harness_versioning.rebuild import tick_nudges

    close_old_connections()
    try:
        return tick_nudges()
    finally:
        close_old_connections()


@temporal_activity(time_limit=300, max_retries=0, queue="default")
def check_harness_nudges(environment_id: str) -> str:
    """Dispatch, recover or end one environment's rebuild."""
    from simulate.services.harness_versioning.rebuild import check_nudges

    close_old_connections()
    try:
        check_nudges(environment_id)
        return "checked"
    finally:
        close_old_connections()


@temporal_activity(time_limit=600, max_retries=3, queue="default")
def finish_harness_nudge_turn(conversation_id: str, turn_sequence: int) -> str:
    """End a rebuild whose turn finished without success."""
    from simulate.services.harness_versioning.rebuild import finish_nudge_turn

    close_old_connections()
    try:
        finish_nudge_turn(conversation_id, turn_sequence)
        return "finished"
    finally:
        close_old_connections()


@temporal_activity(time_limit=180, max_retries=3, queue="default")
def stop_harness_nudge_sandbox(conversation_id: str, provider_ref: str) -> str:
    """Delete the sandbox a closed rebuild ran in; an expired lease alone does not
    stop a turn that is still working."""
    from simulate.services.hosted_harness_gateway import HostedHarnessGateway

    HostedHarnessGateway()._delete_chat_sandbox(provider_ref, conversation_id)
    return "stopped"


@temporal_activity(time_limit=300, max_retries=1, queue="simulation_runner")
def retry_harness_deferred_activations(agent_version_id: str) -> dict[str, str]:
    """Move the environments an activation found busy, now that they are not."""
    from simulate.services.harness_versioning.rebuild import (
        retry_deferred_activations,
    )

    close_old_connections()
    try:
        return retry_deferred_activations(agent_version_id)
    finally:
        close_old_connections()


def schedule_nudge_check_task(environment_id: Any):
    """Check one environment's rebuild; requests coalesce while one runs."""
    return _unique(
        check_harness_nudges,
        [str(environment_id)],
        f"harness-nudge-check-{environment_id}",
    )


def schedule_finish_turn(conversation_id: Any, turn_sequence: int):
    return _unique(
        finish_harness_nudge_turn,
        [str(conversation_id), int(turn_sequence)],
        f"harness-nudge-finish-{conversation_id}-{turn_sequence}",
    )


def schedule_sandbox_stop(conversation_id: Any, provider_ref: str):
    return _unique(
        stop_harness_nudge_sandbox,
        [str(conversation_id), str(provider_ref)],
        f"harness-nudge-sandbox-stop-{provider_ref}",
    )


def schedule_deferred_activations(agent_version_id: Any):
    return _unique(
        retry_harness_deferred_activations,
        [str(agent_version_id)],
        f"harness-deferred-activation-{agent_version_id}",
    )
