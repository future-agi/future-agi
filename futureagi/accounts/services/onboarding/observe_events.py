import structlog

from accounts.services.onboarding.activation_events import (
    build_idempotency_key,
    first_quality_loop_completed,
    has_event,
    record_event,
)

logger = structlog.get_logger(__name__)
NON_REAL_OBSERVE_PROJECT_SOURCES = {"demo", "sample"}


def _user_can_access_project_workspace(project, user):
    if user is None or getattr(user, "is_authenticated", True) is False:
        return False
    if not getattr(project, "workspace_id", None):
        return False
    try:
        return user.can_access_workspace(project.workspace)
    except Exception:
        logger.warning(
            "observe_project_created_user_scope_check_failed",
            project_id=str(getattr(project, "id", "")),
            workspace_id=str(getattr(project, "workspace_id", "")),
            user_id=str(getattr(user, "id", "")),
            exc_info=True,
        )
        return False


def record_observe_project_created(*, project, user=None, source="observe_project"):
    if not project or project.trace_type != "observe":
        return None
    if not project.organization_id or not project.workspace_id:
        return None
    if not _is_real_observe_project(project):
        return None
    if not _user_can_access_project_workspace(project, user):
        return None

    try:
        return record_event(
            user=user,
            organization=project.organization,
            workspace=project.workspace,
            event_name="observe_project_created",
            source=(source or "observe_project")[:64],
            product_path="observe",
            activation_stage="connect_observability",
            metadata={
                "project_id": str(project.id),
                "project_source": project.source or "",
                "project_type": "observe",
            },
            idempotency_key=build_idempotency_key(
                [
                    "observe_project_created",
                    project.workspace_id,
                    project.id,
                ]
            ),
            is_sample=False,
        )
    except Exception:
        logger.exception(
            "observe_project_created_activation_record_failed",
            project_id=str(getattr(project, "id", "")),
            workspace_id=str(getattr(project, "workspace_id", "")),
        )
        return None


def _is_real_observe_project(project):
    if not project or project.trace_type != "observe":
        return False
    if project.source in NON_REAL_OBSERVE_PROJECT_SOURCES:
        return False
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    return metadata.get("is_sample") is not True


def _has_real_trace_review(*, organization, workspace):
    return has_event(
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        is_sample=False,
    )


def record_observe_first_loop_completed(
    *,
    organization,
    workspace,
    artifact_type,
    artifact_id,
    user=None,
    source="observe_loop_artifact_created",
    project=None,
):
    if not organization or not workspace:
        return None
    if project is not None and not _is_real_observe_project(project):
        return None
    if first_quality_loop_completed(
        organization=organization,
        workspace=workspace,
        product_path="observe",
    ):
        return None
    if not _has_real_trace_review(organization=organization, workspace=workspace):
        return None

    project_id = str(project.id) if project is not None else None
    try:
        return record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="first_quality_loop_completed",
            source=(source or "observe_loop_artifact_created")[:64],
            product_path="observe",
            activation_stage="activated",
            metadata={
                "artifact_type": artifact_type,
                "artifact_id": str(artifact_id) if artifact_id else None,
                "project_id": project_id,
                "completion_source": source or "observe_loop_artifact_created",
            },
            idempotency_key=build_idempotency_key(
                [
                    "first_quality_loop_completed",
                    workspace.id,
                    "observe",
                ]
            ),
            is_sample=False,
        )
    except Exception:
        logger.exception(
            "observe_first_loop_completion_record_failed",
            artifact_id=str(artifact_id or ""),
            artifact_type=artifact_type,
            project_id=project_id or "",
            workspace_id=str(getattr(workspace, "id", "")),
        )
        return None
