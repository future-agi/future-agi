"""Scoped current settings and serialized policy writes; no change-history store."""

from django.db import transaction

from accounts.models.workspace import Workspace
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level
from tracer.models.feed_escalation import FeedEscalationPolicy
from tracer.services.feed_escalation_policy import (
    ALGORITHM_VERSION,
    POLICY_FIELDS,
    Policy,
)

RANGES = {
    "min_findings": {"min": 2, "max": 1_000_000, "step": 1},
    "entry_multiplier": {"min": 1.1, "max": 20, "step": 0.1},
    "entry_delta": {"min": 1, "max": 1_000_000, "step": 1},
    "entry_confirmation_minutes": {"min": 5, "max": 60, "step": 5},
    "recovery_min": {"min": 0, "max": 999_999, "step": 1},
    "recovery_multiplier": {"min": 1, "max": 19.9, "step": 0.1},
    "recovery_delta": {"min": 0, "max": 999_999, "step": 1},
    "recovery_confirmation_minutes": {"min": 5, "max": 60, "step": 5},
}


class PolicyConflict(Exception):
    """The submitted revision no longer identifies the current settings."""


def can_edit_policy(user, workspace) -> bool:
    level = get_effective_workspace_level(
        user, workspace.id, organization=workspace.organization
    )
    return level is not None and level >= Level.WORKSPACE_ADMIN


def provision_policy(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        FeedEscalationPolicy.no_workspace_objects.get_or_create(workspace=instance)


def lock_policy(workspace_id) -> FeedEscalationPolicy:
    """Called inside a transaction, before any grouping/issue locks.

    The workspace lock serializes only missing-row creation, including a race
    between two first saves. Existing policies only need their own row lock.
    """
    row = (
        FeedEscalationPolicy.no_workspace_objects.select_for_update()
        .filter(workspace_id=workspace_id)
        .first()
    )
    if row is None:
        workspace = Workspace.no_workspace_objects.select_for_update().get(
            pk=workspace_id
        )
        FeedEscalationPolicy.no_workspace_objects.get_or_create(workspace=workspace)
        row = FeedEscalationPolicy.no_workspace_objects.select_for_update().get(
            workspace=workspace
        )
    return row


def get_policy(workspace_id) -> Policy:
    return get_policy_snapshot(workspace_id)[0]


def get_policy_snapshot(workspace_id):
    row = FeedEscalationPolicy.no_workspace_objects.filter(
        workspace_id=workspace_id
    ).first()
    return (row.policy(), row.updated_at) if row else (Policy(), None)


def settings_response(workspace, *, can_edit: bool) -> dict:
    policy = get_policy(workspace.id)
    return {
        "values": policy.values(),
        "defaults": Policy().values(),
        "ranges": RANGES,
        "revision": policy.revision,
        "algorithm_version": ALGORITHM_VERSION,
        "defaults_version": 1,
        "workspace_id": str(workspace.id),
        "workspace_name": workspace.display_name or workspace.name,
        "can_edit": can_edit,
        "window_minutes": 60,
        "baseline_hours": 24,
        "cadence_minutes": 5,
        "processing_allowance_minutes": 10,
    }


def update_policy(*, workspace, actor, expected_revision: int, changes: dict) -> Policy:
    if not can_edit_policy(actor, workspace):
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("Workspace administrator access required")
    with transaction.atomic():
        row = lock_policy(workspace.id)
        current = row.policy()
        if current.revision != expected_revision:
            raise PolicyConflict("Settings changed. Reload before saving.")
        policy = Policy.from_values(changes, current=current)
        if policy.values() == current.values():
            return current
        for name in POLICY_FIELDS:
            setattr(row, name, getattr(policy, name))
        row.revision += 1
        row.updated_by = actor
        row.save(
            update_fields=[
                *sorted(POLICY_FIELDS),
                "revision",
                "updated_by",
                "updated_at",
            ]
        )
        # Revision mismatch is the durable request; a lost wake cannot lose work.
        transaction.on_commit(lambda: _wake_workspace(str(workspace.id)))
        return row.policy()


def _wake_workspace(workspace_id: str):
    import structlog

    try:
        from tracer.services.feed_escalation import sweep_feed_escalations

        sweep_feed_escalations.delay(workspace_id=workspace_id)
    except Exception:
        structlog.get_logger(__name__).exception(
            "feed_escalation_wake_failed", workspace_id=workspace_id
        )
