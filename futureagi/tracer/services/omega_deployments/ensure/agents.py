"""Get or create a Console agent's Observe project and link (learning-service PLAN P1-08).

One concern: the write paths behind ``POST`` and ``DELETE
/tracer/omega/agents/<id>/observe/`` (ruling 158.2: every Console-managed agent gets its
own Observe project, made with the agent).

- The project is ``agent/<slug>-<shortid>`` (``agent_names``) in the caller's workspace,
  found or created by the function trace ingest uses, so the ensure and an agent's first
  trace land on one row whichever comes first.
- The link is keyed by the EE agent id. A rename keeps the link and the project, name and
  history; only ``agent_name`` changes. An agent made later under the same name has
  another id, so another project.
- An id already linked in another workspace answers ``AgentNotInWorkspace`` (404) and
  writes nothing; the live id is unique in the database, so a race cannot link it twice.
- ``AgentProjectNameTaken`` (409) when the name is a project of another kind, or an
  Observe project another live agent link holds; checked before any write.
- Setting up the defaults: the two monitors (``monitors``, deferred with the reason when
  the plan has no room) and mistake analysis (``mistakes``, off with the reason when the
  plan has no Error Feed).
- A soft-deleted project is made again with fresh defaults, under the agent's current name.
- Unlinking ends the link (no live row, the agent name cleared) and leaves the project,
  its traces and its monitors in Observe. The same agent linked again finds that project
  and keeps what was set up there.
"""

from dataclasses import dataclass

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import (
    ObserveLinkKind,
    OmegaDeploymentObserveLink,
)
from tracer.models.project import Project
from tracer.services.omega_deployments.ensure.agent_names import agent_project_name
from tracer.services.omega_deployments.ensure.errors import (
    AgentNotInWorkspace,
    AgentProjectNameTaken,
)
from tracer.services.omega_deployments.ensure.mistakes import (
    MistakeAnalysis,
    clear_mistake_analysis,
    ensure_mistake_analysis,
)
from tracer.services.omega_deployments.ensure.monitors import (
    clear_default_monitors,
    ensure_default_monitors,
    live_default_monitors,
)
from tracer.services.omega_deployments.ensure.quota import AlertsDeferred
from tracer.utils.otel import get_or_create_project

logger = structlog.get_logger(__name__)

OBSERVE = "observe"
AGENT = ObserveLinkKind.AGENT.value


@dataclass(frozen=True)
class AgentObserveTarget:
    """What the Console agent page needs to link into Observe."""

    link: OmegaDeploymentObserveLink
    project: Project
    alerts: list[tuple[str, UserAlertMonitor]]
    mistakes: MistakeAnalysis
    # Set when the plan had no room for the default alerts on this call.
    alerts_deferred: AlertsDeferred | None = None


def _agent_links():
    return OmegaDeploymentObserveLink.no_workspace_objects.filter(kind=AGENT)


def ensure_agent_observe_link(
    *,
    organization: Organization,
    workspace: Workspace,
    user: User,
    ee_agent_id: str,
    agent_name: str,
) -> AgentObserveTarget:
    """Idempotently link ``ee_agent_id`` to its ``agent/<slug>-<shortid>`` in ``workspace``."""
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return _ensure_once(
                    organization, workspace, user, ee_agent_id, agent_name
                )
        except IntegrityError:
            # Another request linked this id first (here or in another workspace).
            # The second pass reads what it committed; a second failure is real.
            if attempt == 2:
                raise
            logger.info("omega_agent_observe_link_race_retry", ee_agent_id=ee_agent_id)
    raise AssertionError("unreachable")


def _ensure_once(
    organization: Organization,
    workspace: Workspace,
    user: User,
    ee_agent_id: str,
    agent_name: str,
) -> AgentObserveTarget:
    links = _agent_links()
    if (
        links.filter(ee_agent_id=ee_agent_id)
        .exclude(workspace_id=workspace.id)
        .exists()
    ):
        raise AgentNotInWorkspace()

    link = (
        links.select_for_update()
        .filter(workspace_id=workspace.id, ee_agent_id=ee_agent_id)
        .first()
    )
    if link is not None and _still_serves(link):
        if link.agent_name != agent_name:
            logger.info(
                "omega_agent_observe_link_renamed",
                ee_agent_id=ee_agent_id,
                project_id=str(link.project_id),
            )
            link.agent_name = agent_name
            link.save(update_fields=["agent_name", "updated_at"])
        return _target(link, user)

    project, carried = None, {}
    if link is None:
        # Linked again: the agent's own project, if it still serves, with its set-up.
        ended = _last_ended_link(workspace, ee_agent_id)
        if ended is not None and _still_serves(ended):
            project = ended.project
            carried = {field: getattr(ended, field) for field in _SET_UP_FIELDS}
    if project is None:
        project_name = agent_project_name(agent_name, ee_agent_id)
        _refuse_taken_name(organization, workspace, project_name, ee_agent_id)
        project = get_or_create_project(
            project_name=project_name,
            organization_id=str(organization.id),
            project_type=OBSERVE,
            user_id=str(user.pk),
            workspace_id=str(workspace.id),
        )
        if project is None:  # it raises on failure; never link to nothing
            raise RuntimeError(f"no Observe project for {project_name}")
    if link is None:
        link = OmegaDeploymentObserveLink.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            kind=AGENT,
            ee_agent_id=ee_agent_id,
            agent_name=agent_name,
            project=project,
            created_by=user,
            **carried,
        )
    else:
        # The linked project was deleted in Observe: a new one, with fresh defaults.
        logger.info(
            "omega_agent_observe_link_moved",
            ee_agent_id=ee_agent_id,
            from_project_id=str(link.project_id),
            to_project_id=str(project.id),
        )
        link.agent_name = agent_name
        link.project = project
        clear_default_monitors(link)
        clear_mistake_analysis(link)
        link.save()
    return _target(link, user)


# What a link remembers of the defaults it set up on its project.
_SET_UP_FIELDS = (
    "errors_monitor_id",
    "daily_tokens_monitor_id",
    "alerts_created_at",
    "alerts_deferred_count",
    "alerts_last_deferred_at",
    "scan_configured_at",
)


def _last_ended_link(
    workspace: Workspace, ee_agent_id: str
) -> OmegaDeploymentObserveLink | None:
    """This agent's last ended link in ``workspace``, for when it is linked again.

    The same agent goes back to its own project, renamed or not in between, and keeps what was
    set up there: no second pair of monitors, and a rate the user changed stays.
    """
    return (
        OmegaDeploymentObserveLink._base_manager.filter(
            kind=AGENT,
            deleted=True,
            workspace_id=workspace.id,
            ee_agent_id=ee_agent_id,
        )
        .select_related("project")
        .order_by("-deleted_at")
        .first()
    )


def _still_serves(link: OmegaDeploymentObserveLink) -> bool:
    """The linked project is still a live Observe project. Its name is not compared: the
    name was fixed when the link was made, and a rename keeps it."""
    return link.project.trace_type == OBSERVE and not link.project.deleted


def _refuse_taken_name(
    organization: Organization,
    workspace: Workspace,
    project_name: str,
    ee_agent_id: str,
) -> None:
    other_kind = (
        Project.no_workspace_objects.filter(
            organization_id=organization.id,
            workspace_id=workspace.id,
            name=project_name,
        )
        .exclude(trace_type=OBSERVE)
        .exists()
    )
    held_by_another_agent = (
        _agent_links()
        .filter(
            workspace_id=workspace.id,
            project__name=project_name,
            project__trace_type=OBSERVE,
            project__deleted=False,
        )
        .exclude(ee_agent_id=ee_agent_id)
        .exists()
    )
    if other_kind or held_by_another_agent:
        raise AgentProjectNameTaken(project_name)


def _target(link: OmegaDeploymentObserveLink, user: User) -> AgentObserveTarget:
    deferred = ensure_default_monitors(link, user)
    mistakes = ensure_mistake_analysis(link)
    return AgentObserveTarget(
        link=link,
        project=link.project,
        alerts=live_default_monitors(link),
        mistakes=mistakes,
        alerts_deferred=deferred,
    )


def unlink_agent_observe(*, workspace: Workspace, ee_agent_id: str) -> bool:
    """End ``ee_agent_id``'s link in ``workspace``; ``False`` when it has none there.

    A link in another workspace is never touched and reads the same as none. The row
    stays as a record of which project the agent used, with no live id and no name.
    """
    now = timezone.now()
    with transaction.atomic():
        ended = (
            _agent_links()
            .filter(workspace_id=workspace.id, ee_agent_id=ee_agent_id)
            .update(deleted=True, deleted_at=now, agent_name="", updated_at=now)
        )
    if ended:
        logger.info(
            "omega_agent_observe_link_ended",
            ee_agent_id=ee_agent_id,
            workspace_id=str(workspace.id),
        )
    return bool(ended)
