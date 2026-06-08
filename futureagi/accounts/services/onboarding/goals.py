from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import OnboardingGoal
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.onboarding.activation_events import (
    build_idempotency_key,
    first_quality_loop_completed,
    record_event,
    sanitize_activation_metadata,
)
from accounts.services.onboarding.constants import (
    ONBOARDING_GOALS,
    PRODUCT_PATHS,
    canonical_goal,
    canonical_path,
)
from accounts.services.onboarding.flow_config import (
    configured_default_goal_id,
    configured_goal_primary_paths,
)
from tfc.constants.roles import RolePermissions

GOAL_PRIMARY_PATHS = configured_goal_primary_paths()
DEFAULT_GOAL_ID = configured_default_goal_id()


@dataclass(frozen=True)
class OnboardingGoalSaveResult:
    goal: OnboardingGoal
    created: bool
    changed: bool
    event_name: str | None
    previous_goal: OnboardingGoal | None


class OnboardingGoalConflict(Exception):
    def __init__(self, reason, current_goal=None):
        self.reason = reason
        self.current_goal = current_goal
        super().__init__(reason)


def normalize_goal(goal):
    canonical = canonical_goal(goal)
    if canonical not in ONBOARDING_GOALS:
        raise ValidationError("Unsupported onboarding goal.")
    return canonical


def goal_to_primary_path(goal):
    return GOAL_PRIMARY_PATHS[normalize_goal(goal)]


def normalize_primary_path(path):
    canonical = canonical_path(path)
    if canonical not in PRODUCT_PATHS:
        raise ValidationError("Unsupported onboarding primary path.")
    return canonical


def _validate_goal_path(goal, primary_path=None):
    canonical_goal_value = normalize_goal(goal)
    expected_path = GOAL_PRIMARY_PATHS[canonical_goal_value]
    if primary_path in {None, ""}:
        return canonical_goal_value, expected_path
    canonical_path_value = normalize_primary_path(primary_path)
    if canonical_path_value != expected_path:
        raise ValidationError("Primary path does not match onboarding goal.")
    return canonical_goal_value, canonical_path_value


def get_active_goal(*, organization, workspace):
    if not organization or not workspace:
        return None
    return (
        OnboardingGoal.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
            is_active=True,
        )
        .order_by("-selected_at", "-created_at")
        .first()
    )


def _legacy_goal_for_user(user, *, use_default=True):
    config = getattr(user, "config", None) or {}
    onboarding_config = config.get("onboarding", {}) or {}
    candidates = []
    goals = getattr(user, "goals", None)
    if isinstance(goals, list):
        candidates.extend(goals)
    elif goals:
        candidates.append(goals)
    config_goals = onboarding_config.get("goals", [])
    if isinstance(config_goals, list):
        candidates.extend(config_goals)
    elif config_goals:
        candidates.append(config_goals)

    canonical_goals = []
    for goal in candidates:
        canonical = canonical_goal(goal)
        if canonical in ONBOARDING_GOALS and canonical not in canonical_goals:
            canonical_goals.append(canonical)
    if canonical_goals:
        goal = canonical_goals[0]
        return {
            "goal": goal,
            "primary_path": GOAL_PRIMARY_PATHS[goal],
            "goal_id": None,
            "source": "legacy_user_goals",
        }
    if use_default and DEFAULT_GOAL_ID:
        return {
            "goal": DEFAULT_GOAL_ID,
            "primary_path": GOAL_PRIMARY_PATHS[DEFAULT_GOAL_ID],
            "goal_id": None,
            "source": "default_first_run_goal",
        }
    return {
        "goal": None,
        "primary_path": None,
        "goal_id": None,
        "source": "none",
    }


def _requested_goal_context(
    *,
    requested_goal=None,
    requested_primary_path=None,
    source=None,
):
    if source != "setup_org" or not requested_goal or not requested_primary_path:
        return None
    try:
        goal, primary_path = _validate_goal_path(requested_goal, requested_primary_path)
    except ValidationError:
        return None
    return {
        "goal": goal,
        "primary_path": primary_path,
        "goal_id": None,
        "source": "setup_quick_start",
    }


def resolve_goal_for_context(
    *,
    user,
    organization,
    workspace,
    requested_goal=None,
    requested_primary_path=None,
    source=None,
):
    requested_context = _requested_goal_context(
        requested_goal=requested_goal,
        requested_primary_path=requested_primary_path,
        source=source,
    )
    if requested_context:
        return requested_context

    active_goal = get_active_goal(organization=organization, workspace=workspace)
    if active_goal:
        return {
            "goal": active_goal.goal,
            "primary_path": active_goal.primary_path,
            "goal_id": str(active_goal.id),
            "source": "active_workspace_goal",
        }
    return _legacy_goal_for_user(user, use_default=bool(organization and workspace))


def _validate_workspace_scope(*, organization, workspace):
    if organization is None:
        raise ValidationError("Organization is required.")
    if workspace is None:
        raise ValidationError("Workspace is required.")
    if workspace.organization_id != organization.id:
        raise ValidationError("Workspace does not belong to organization.")


def _user_can_access_workspace(*, user, organization, workspace):
    org_membership = (
        OrganizationMembership.no_workspace_objects.filter(
            user=user,
            organization=organization,
            is_active=True,
        )
        .order_by("-created_at")
        .first()
    )
    if not org_membership:
        return False
    if org_membership.role in RolePermissions.GLOBAL_ACCESS_ROLES:
        return True
    return WorkspaceMembership.no_workspace_objects.filter(
        user=user,
        workspace=workspace,
        is_active=True,
    ).exists()


def _clean_metadata(metadata):
    return sanitize_activation_metadata(metadata or {})


def _metadata_for_event(
    *,
    previous_goal,
    new_goal,
    new_primary_path,
    source,
    reason,
    metadata,
    expected_stage,
):
    event_metadata = {
        "previous_goal": previous_goal.goal if previous_goal else None,
        "previous_primary_path": previous_goal.primary_path if previous_goal else None,
        "new_goal": new_goal,
        "new_primary_path": new_primary_path,
        "source": source,
        "reason": reason,
    }
    if expected_stage:
        event_metadata["expected_stage"] = expected_stage
    if metadata.get("campaign_key"):
        event_metadata["campaign_key"] = metadata["campaign_key"]
    if metadata.get("persona"):
        event_metadata["persona"] = metadata["persona"]
    return sanitize_activation_metadata(event_metadata)


def _record_goal_event(
    *,
    user,
    organization,
    workspace,
    event_name,
    primary_path,
    source,
    event_metadata,
):
    idempotency_parts = [
        "goal_selected" if event_name == "onboarding_goal_selected" else "goal_changed",
        workspace.id,
        getattr(user, "id", None),
        event_metadata.get("previous_goal"),
        event_metadata["new_goal"],
    ]
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name=event_name,
        source=source,
        product_path=primary_path,
        activation_stage="choose_goal",
        metadata=event_metadata,
        idempotency_key=build_idempotency_key(idempotency_parts),
    )


def _check_stale_submission(
    *,
    organization,
    workspace,
    current_goal,
    known_goal_id,
    expected_stage,
):
    if known_goal_id and str(getattr(current_goal, "id", "")) != str(known_goal_id):
        raise OnboardingGoalConflict("known_goal_mismatch", current_goal=current_goal)
    if current_goal and expected_stage in {"feature_disabled", "workspace_missing"}:
        raise OnboardingGoalConflict("stage_changed", current_goal=current_goal)
    if expected_stage not in {None, "activated", "daily_review"} and (
        first_quality_loop_completed(
            organization=organization,
            workspace=workspace,
            product_path=None,
        )
    ):
        raise OnboardingGoalConflict("stage_changed", current_goal=current_goal)


def save_onboarding_goal(
    *,
    user,
    organization,
    workspace,
    goal,
    primary_path=None,
    source="goal_picker",
    reason="first_selection",
    metadata=None,
    expected_stage=None,
    known_goal_id=None,
):
    _validate_workspace_scope(organization=organization, workspace=workspace)
    if not _user_can_access_workspace(
        user=user,
        organization=organization,
        workspace=workspace,
    ):
        raise ValidationError("User cannot access workspace.")

    canonical_goal_value, canonical_path_value = _validate_goal_path(
        goal,
        primary_path=primary_path,
    )
    source = (source or "goal_picker")[:64]
    reason = (reason or "first_selection")[:64]
    safe_metadata = _clean_metadata(metadata)
    current_goal = get_active_goal(organization=organization, workspace=workspace)
    _check_stale_submission(
        organization=organization,
        workspace=workspace,
        current_goal=current_goal,
        known_goal_id=known_goal_id,
        expected_stage=expected_stage,
    )

    if (
        current_goal
        and current_goal.goal == canonical_goal_value
        and current_goal.primary_path == canonical_path_value
    ):
        return OnboardingGoalSaveResult(
            goal=current_goal,
            created=False,
            changed=False,
            event_name=None,
            previous_goal=current_goal,
        )

    event_name = (
        "onboarding_goal_changed" if current_goal else "onboarding_goal_selected"
    )
    event_metadata = _metadata_for_event(
        previous_goal=current_goal,
        new_goal=canonical_goal_value,
        new_primary_path=canonical_path_value,
        source=source,
        reason=reason,
        metadata=safe_metadata,
        expected_stage=expected_stage,
    )

    try:
        with transaction.atomic():
            if current_goal:
                OnboardingGoal.no_workspace_objects.filter(id=current_goal.id).update(
                    is_active=False,
                    updated_at=timezone.now(),
                )
            new_goal = OnboardingGoal.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                user=user,
                goal=canonical_goal_value,
                primary_path=canonical_path_value,
                source=source,
                reason=reason,
                metadata=safe_metadata,
            )
    except IntegrityError as exc:
        raise OnboardingGoalConflict(
            "active_goal_changed",
            current_goal=get_active_goal(
                organization=organization, workspace=workspace
            ),
        ) from exc

    _record_goal_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name=event_name,
        primary_path=canonical_path_value,
        source=source,
        event_metadata=event_metadata,
    )

    return OnboardingGoalSaveResult(
        goal=new_goal,
        created=True,
        changed=current_goal is not None,
        event_name=event_name,
        previous_goal=current_goal,
    )
