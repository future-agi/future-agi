import structlog

from accounts.services.onboarding.activation_events import (
    build_idempotency_key,
    record_event,
)

logger = structlog.get_logger(__name__)


def record_observe_project_created(*, project, user=None, source="observe_project"):
    if not project or project.trace_type != "observe":
        return None
    if not project.organization_id or not project.workspace_id:
        return None
    if project.source == "demo" or (project.metadata or {}).get("is_sample") is True:
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
