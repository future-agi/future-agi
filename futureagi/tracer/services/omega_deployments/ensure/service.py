"""Get or create a deployment's Observe project and link (SPEC 5.3 "Ensure").

One concern: the write path behind ``POST /tracer/omega/deployments/<id>/observe/``.

- The project is ``deploy/<name>`` in the caller's workspace, found or created by
  the same function trace ingest uses, so ensure and the relay's first trace land
  on one row whichever comes first.
- A deployment belongs to one workspace. An id already linked in another
  workspace answers ``DeploymentNotInWorkspace`` (404) and writes nothing; the
  live link id is unique in the database, so a race cannot create a second one.
- ``ProjectNameTaken`` (409) only when ``deploy/<name>`` exists in the workspace
  as a project that is not an Observe project; it is checked before any write.
- The default monitors wait when the plan has no room for them: the project and
  link are still made and ``alerts_deferred`` says why; a later call creates them.
- Calling it again returns the same project and monitors. Two calls at once
  serialize on the link row; a lost insert race is retried once and then reads
  the winner's link.
"""

from dataclasses import dataclass

import structlog
from django.db import IntegrityError, transaction

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.services.omega_deployments.ensure.defaults import project_name_for
from tracer.services.omega_deployments.ensure.errors import (
    DeploymentNotInWorkspace,
    ProjectNameTaken,
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


@dataclass(frozen=True)
class ObserveTarget:
    """What the Deployments tab needs to link into Observe."""

    link: OmegaDeploymentObserveLink
    project: Project
    alerts: list[tuple[str, UserAlertMonitor]]
    # Set when the plan had no room for the defaults on this call.
    alerts_deferred: AlertsDeferred | None = None


def ensure_observe_link(
    *,
    organization: Organization,
    workspace: Workspace,
    user: User,
    ee_deployment_id: str,
    deployment_name: str,
) -> ObserveTarget:
    """Idempotently link ``ee_deployment_id`` to ``deploy/<deployment_name>`` in ``workspace``."""
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return _ensure_once(
                    organization, workspace, user, ee_deployment_id, deployment_name
                )
        except IntegrityError:
            # Another request linked this id first (here or in another
            # workspace). The second pass reads what it committed; a second
            # failure is real.
            if attempt == 2:
                raise
            logger.info(
                "omega_deployment_observe_link_race_retry",
                ee_deployment_id=ee_deployment_id,
            )
    raise AssertionError("unreachable")


def _ensure_once(
    organization: Organization,
    workspace: Workspace,
    user: User,
    ee_deployment_id: str,
    deployment_name: str,
) -> ObserveTarget:
    links = OmegaDeploymentObserveLink.no_workspace_objects
    if (
        links.filter(ee_deployment_id=ee_deployment_id)
        .exclude(workspace_id=workspace.id)
        .exists()
    ):
        raise DeploymentNotInWorkspace()

    link = (
        links.select_for_update()
        .filter(workspace_id=workspace.id, ee_deployment_id=ee_deployment_id)
        .first()
    )
    project_name = project_name_for(deployment_name)
    if link is not None and _still_serves(link, project_name):
        return _target(link, ensure_default_monitors(link, user))

    _refuse_non_observe_name(organization, workspace, project_name)
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
        link = links.create(
            organization=organization,
            workspace=workspace,
            ee_deployment_id=ee_deployment_id,
            deployment_name=deployment_name,
            project=project,
            created_by=user,
        )
    else:
        logger.info(
            "omega_deployment_observe_link_moved",
            ee_deployment_id=ee_deployment_id,
            from_project_id=str(link.project_id),
            to_project_id=str(project.id),
        )
        link.deployment_name = deployment_name
        link.project = project
        clear_default_monitors(link)
        link.save()
    return _target(link, ensure_default_monitors(link, user))


def _still_serves(link: OmegaDeploymentObserveLink, project_name: str) -> bool:
    """The link already names this project, and the project was not deleted."""
    return (
        link.project.name == project_name
        and link.project.trace_type == OBSERVE
        and not link.project.deleted
    )


def _refuse_non_observe_name(
    organization: Organization, workspace: Workspace, project_name: str
) -> None:
    clash = (
        Project.no_workspace_objects.filter(
            organization_id=organization.id,
            workspace_id=workspace.id,
            name=project_name,
        )
        .exclude(trace_type=OBSERVE)
        .exists()
    )
    if clash:
        raise ProjectNameTaken(project_name)


def _target(
    link: OmegaDeploymentObserveLink, deferred: AlertsDeferred | None
) -> ObserveTarget:
    return ObserveTarget(
        link=link,
        project=link.project,
        alerts=live_default_monitors(link),
        alerts_deferred=deferred,
    )
