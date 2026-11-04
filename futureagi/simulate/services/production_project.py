"""The Observe project a code agent's production traces land in.

The user picks it once their app sends traces. It is separate from the
observability switch, which for a code agent only gates the export of
simulation spans to the agent's hidden project, so linking and unlinking leave
that export exactly as it was.
"""

from __future__ import annotations

import uuid

import structlog
from django.utils import timezone

from simulate.models import AgentDefinition
from simulate.services.agent_setup import agent_summary
from tracer.models.project import Project

logger = structlog.get_logger(__name__)


class ProductionProjectError(Exception):
    """A link refused: the agent isn't a code agent, or the project isn't one it may use."""


def link_production_project(
    *, agent_definition: AgentDefinition, project_id: uuid.UUID | None
) -> Project | None:
    """Point a code agent at an Observe project of its workspace, or clear the link."""
    if agent_summary(agent_definition.id).kind != "source":
        raise ProductionProjectError(
            "Only a code agent links its production Observe project. A hosted "
            "agent's project is created when observability is turned on."
        )
    project = None
    if project_id is not None:
        projects = Project.no_workspace_objects.filter(
            id=project_id,
            organization_id=agent_definition.organization_id,
            trace_type="observe",
            deleted=False,
        )
        if agent_definition.workspace_id is not None:
            projects = projects.filter(workspace_id=agent_definition.workspace_id)
        project = projects.first()
        if project is None:
            raise ProductionProjectError(
                "That Observe project was not found in this workspace."
            )
    AgentDefinition.no_workspace_objects.filter(pk=agent_definition.pk).update(
        production_project=project, updated_at=timezone.now()
    )
    agent_definition.production_project = project
    logger.info(
        "production_project_linked" if project else "production_project_unlinked",
        agent_definition_id=str(agent_definition.id),
        project_id=str(project.id) if project else None,
    )
    return project
