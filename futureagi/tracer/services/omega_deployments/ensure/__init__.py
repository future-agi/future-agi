"""Ensure a deployment's or Console agent's Observe project, link and defaults (SPEC 5.3;
learning-service PLAN P1-08)."""

from tracer.services.omega_deployments.ensure.agent_names import agent_project_name
from tracer.services.omega_deployments.ensure.agents import (
    AgentObserveTarget,
    ensure_agent_observe_link,
    unlink_agent_observe,
)
from tracer.services.omega_deployments.ensure.defaults import (
    DAILY_TOKENS,
    ERRORS_SPIKE,
    project_name_for,
)
from tracer.services.omega_deployments.ensure.errors import (
    AgentNotInWorkspace,
    AgentProjectNameTaken,
    DeploymentNotInWorkspace,
    ObserveLinkError,
    ProjectNameTaken,
)
from tracer.services.omega_deployments.ensure.mistakes import (
    AGENT_MISTAKES_SAMPLING_RATE,
    MistakeAnalysis,
)
from tracer.services.omega_deployments.ensure.quota import AlertsDeferred
from tracer.services.omega_deployments.ensure.service import (
    ObserveTarget,
    ensure_observe_link,
)

__all__ = [
    "AGENT_MISTAKES_SAMPLING_RATE",
    "DAILY_TOKENS",
    "ERRORS_SPIKE",
    "AgentNotInWorkspace",
    "AgentObserveTarget",
    "AgentProjectNameTaken",
    "AlertsDeferred",
    "DeploymentNotInWorkspace",
    "MistakeAnalysis",
    "ObserveLinkError",
    "ObserveTarget",
    "ProjectNameTaken",
    "agent_project_name",
    "ensure_agent_observe_link",
    "ensure_observe_link",
    "project_name_for",
    "unlink_agent_observe",
]
