"""The hidden simulation project of an agent definition.

Every agent definition owns at most one project that holds the traces of its
simulated calls. The project is created here, named from the agent's id, and is
returned only by ``Project.with_simulation``; the project managers used by the
rest of the application leave it out. The one reader that opens it for a run the
caller can read is ``simulation_scope_for_run``.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

import structlog

from tracer.models.project import Project

if TYPE_CHECKING:
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.test_execution import TestExecution

logger = structlog.get_logger(__name__)

SIMULATION_PROJECT_PREFIX = "fi-simulation-"
RESERVED_PROJECT_NAME_MESSAGE = "that project name is reserved"

_RESERVED_NAME = re.compile(
    re.escape(SIMULATION_PROJECT_PREFIX)
    + r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


class SimulationProjectError(Exception):
    """The hidden project of an agent definition cannot be created or used."""


class ReservedProjectNameError(ValueError):
    """A project was asked for under the name reserved for a hidden project."""


def simulation_project_name(agent_definition_id: str | uuid.UUID) -> str:
    return SIMULATION_PROJECT_PREFIX + str(uuid.UUID(str(agent_definition_id)))


def agent_id_in_simulation_project_name(name: str) -> str | None:
    """Return the agent id a reserved project name carries, else ``None``."""
    if not isinstance(name, str):
        return None
    match = _RESERVED_NAME.fullmatch(name)
    return match.group(1) if match else None


def _default_workspace_id(organization_id) -> uuid.UUID:
    from accounts.models.workspace import Workspace

    workspace_id = (
        Workspace.no_workspace_objects.filter(
            organization_id=organization_id, is_default=True, is_active=True
        )
        .values_list("id", flat=True)
        .first()
    )
    if workspace_id is None:
        raise SimulationProjectError(
            "The organization of this agent definition has no active default "
            "workspace to hold its simulation project."
        )
    return workspace_id


def ensure_simulation_project(agent_definition: AgentDefinition) -> Project:
    """Return the hidden project of the agent definition, creating it if needed.

    Safe to call concurrently: one project exists per agent definition. The
    agent definition is read again from the database, so a stale instance
    cannot get a project for a deleted agent. The project goes in the agent's
    workspace, else in the one active default workspace of its organization,
    never in the workspace of the current request. A project of the same name
    raises ValidationError or IntegrityError, which propagates; a foreign-key
    failure for an agent hard deleted in the meantime surfaces when the
    caller's outermost transaction commits.
    """
    from model_hub.models.ai_model import AIModel
    from simulate.models.agent_definition import AgentDefinition
    from tracer.utils.helper import get_default_project_version_config

    agent_id = getattr(agent_definition, "pk", None)
    if agent_id is None:
        raise SimulationProjectError("An agent definition is required.")

    agent = (
        AgentDefinition._base_manager.filter(pk=agent_id, deleted=False)
        .values("organization_id", "workspace_id")
        .first()
    )
    if agent is None:
        raise SimulationProjectError(
            "The agent definition does not exist or was deleted."
        )

    workspace_id = agent["workspace_id"] or _default_workspace_id(
        agent["organization_id"]
    )

    project, created = Project.with_simulation.get_or_create(
        simulation_agent_id=agent_id,
        defaults={
            "name": simulation_project_name(agent_id),
            "organization_id": agent["organization_id"],
            "workspace_id": workspace_id,
            "trace_type": "observe",
            "source": "simulator",
            "model_type": AIModel.ModelTypes.GENERATIVE_LLM,
            "config": get_default_project_version_config(),
            "user": None,
        },
    )

    if project.deleted:
        raise SimulationProjectError(
            "The simulation project of this agent definition was deleted."
        )
    if created:
        logger.info(
            "simulation_project_created",
            agent_definition_id=str(agent_id),
            project_id=str(project.id),
            by="platform",
        )
    return project


def simulation_project_of(agent_definition_id) -> Project | None:
    """Return the hidden project of the agent definition, deleted ones included.

    Reads through the default database router, so a replica can lag behind a
    project that ``ensure_simulation_project`` just created.
    """
    if agent_definition_id is None:
        return None
    return Project.with_simulation.filter(
        simulation_agent_id=agent_definition_id
    ).first()


def agent_of_run(test_execution: TestExecution) -> AgentDefinition | None:
    """Return the agent definition a test execution ran against.

    The execution's own agent wins over the agent of its run test. A deleted
    agent definition is returned as is.
    """
    if test_execution.agent_definition_id is not None:
        return test_execution.agent_definition
    return test_execution.run_test.agent_definition


def simulation_scope_for_run(
    request, test_execution_id=None, call_execution_id=None
) -> Project | None:
    """Return the hidden project a by-id read may open for a run, else ``None``.

    The run must be readable by the caller the way the run pages read it: same
    organization, same workspace rule, neither the run test nor the execution
    deleted. Its agent definition (see ``agent_of_run``) must be live and in
    that organization. Nothing is raised for a missing, foreign or unreadable
    id, and nothing is queried when neither id or both ids are given.
    """
    if (test_execution_id is None) == (call_execution_id is None):
        return None
    organization = getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )
    if organization is None:
        return None

    # Function-local: importing the scoping module loads every simulate view.
    from simulate.models.test_execution import CallExecution, TestExecution
    from simulate.views.scoping import run_test_workspace_filter

    if test_execution_id is not None:
        execution = (
            TestExecution.no_workspace_objects.select_related(
                "run_test", "agent_definition", "run_test__agent_definition"
            )
            .filter(
                run_test_workspace_filter(request, "run_test"),
                id=test_execution_id,
                run_test__organization=organization,
                run_test__deleted=False,
            )
            .first()
        )
    else:
        call = (
            CallExecution.no_workspace_objects.select_related(
                "test_execution__run_test",
                "test_execution__agent_definition",
                "test_execution__run_test__agent_definition",
            )
            .filter(
                run_test_workspace_filter(request, "test_execution__run_test"),
                id=call_execution_id,
                test_execution__run_test__organization=organization,
                test_execution__run_test__deleted=False,
                test_execution__deleted=False,
            )
            .first()
        )
        execution = call.test_execution if call is not None else None
    if execution is None:
        return None

    agent = agent_of_run(execution)
    if agent is None or agent.deleted or agent.organization_id != organization.id:
        return None
    return Project.with_simulation.filter(
        simulation_agent_id=agent.pk,
        deleted=False,
        organization_id=organization.id,
    ).first()


def run_scope_project_ids(project: Project, pinned_project_id=None) -> list[str]:
    """Return the project ids a by-id read may query with a run's scope.

    A ``project_id`` sent next to the run must name the scope's project; if it
    names another project the list is empty, which reads like a missing trace.
    """
    if pinned_project_id in (None, ""):
        return [str(project.id)]
    if str(pinned_project_id) == str(project.id):
        return [str(project.id)]
    return []
