"""Which workspace's feedback steers one evaluation run.

Feedback is read only through EmbeddingManager, whose one scope clause
(``_feedback_scope_sql``) admits the run organization's rows of the workspace
named here plus no-workspace rows saved before the release cutoff. This module
only names that workspace; it never reads feedback itself.
"""

import structlog

logger = structlog.get_logger(__name__)

# A person started the run: the workspace is the request's workspace.
STARTED_BY_PERSON = "person"
# A scheduler or background worker runs it: the workspace stored on its task
# or metric, else the organization's default workspace.
STARTED_BY_SCHEDULE = "schedule"


def default_workspace_id(organization_id):
    """The organization's active default workspace id, or None.

    Fails closed: when the lookup cannot run, no workspace is returned.
    """
    if not organization_id:
        return None
    try:
        from accounts.models.workspace import Workspace

        workspace_id = (
            Workspace.no_workspace_objects.filter(
                organization_id=organization_id, is_default=True, is_active=True
            )
            .values_list("id", flat=True)
            .first()
        )
    except Exception:
        logger.warning("feedback_default_workspace_unavailable")
        return None
    return str(workspace_id) if workspace_id else None


def feedback_workspace_for_run(
    organization_id, target_workspace_id, *, started_by, workspace_id
):
    """Workspace id whose feedback steers a run, or None for no feedback.

    ``target_workspace_id`` is the workspace of the dataset or metric the run
    evaluates, None when it has none. With ``started_by=STARTED_BY_PERSON``,
    ``workspace_id`` is the workspace the person started the run in; empty
    means no feedback. With ``started_by=STARTED_BY_SCHEDULE``,
    ``workspace_id`` is the workspace stored on the run's task or metric; when
    it is empty the organization's default workspace is used. Request input
    is never passed for a scheduled run.

    A target with a workspace binds only that workspace. A target with no
    workspace is visible only from the organization's default workspace, so
    only the default workspace can bind it; any other workspace gets None.
    """
    if not organization_id:
        return None
    default_id = None
    if started_by == STARTED_BY_PERSON:
        candidate = workspace_id
    elif started_by == STARTED_BY_SCHEDULE:
        candidate = workspace_id
        if not candidate:
            default_id = default_workspace_id(organization_id)
            candidate = default_id
    else:
        raise ValueError(f"unknown run starter: {started_by!r}")
    if not candidate:
        return None
    candidate = str(candidate)
    if target_workspace_id:
        return candidate if candidate == str(target_workspace_id) else None
    if default_id is None:
        default_id = default_workspace_id(organization_id)
    return candidate if default_id and candidate == default_id else None
