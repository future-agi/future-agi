"""Two ensures for one Console agent at the same moment (the agent made, and its page
opened) still give one link, one project, two monitors and one scan config; two workspaces
racing for one agent id leave exactly one link and nothing in the loser's workspace.

Real commits on real connections (``transaction=True``), one thread per caller, released
together by a barrier (as ``omega_deployments/test_ensure_concurrency.py``).
"""

import pytest

from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig
from tracer.services.omega_deployments.ensure import (
    AgentNotInWorkspace,
    ensure_agent_observe_link,
)
from tracer.tests.omega_agents.conftest import new_agent_id
from tracer.tests.omega_deployments.test_ensure_concurrency import _race

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

ROUNDS = 5


def _call(organization, workspace, user, agent_id, name="Race Bot"):
    return lambda: ensure_agent_observe_link(
        organization=organization,
        workspace=workspace,
        user=user,
        ee_agent_id=agent_id,
        agent_name=name,
    )


def test_two_calls_at_once_give_one_link_one_project_and_one_set_of_defaults(
    organization, workspace, user
):
    for _ in range(ROUNDS):
        agent_id = new_agent_id()
        call = _call(organization, workspace, user, agent_id)

        outcomes = _race([call, call])

        assert [kind for kind, _ in outcomes] == ["ok", "ok"], outcomes
        (_, first), (_, second) = outcomes
        assert first.project.id == second.project.id
        assert [m.id for _, m in first.alerts] == [m.id for _, m in second.alerts]
        assert first.mistakes == second.mistakes
        assert first.mistakes.status == "on"
        assert (
            OmegaDeploymentObserveLink.no_workspace_objects.filter(
                ee_agent_id=agent_id
            ).count()
            == 1
        )
        assert Project.no_workspace_objects.filter(name=first.project.name).count() == 1
        assert (
            UserAlertMonitor.no_workspace_objects.filter(
                project_id=first.project.id
            ).count()
            == 2
        )
        assert (
            TraceScanConfig.all_objects.filter(project_id=first.project.id).count() == 1
        )


def test_two_workspaces_racing_for_one_agent_id_leave_one_link(
    organization, workspace, user, second_workspace
):
    for _ in range(ROUNDS):
        agent_id = new_agent_id()

        outcomes = _race(
            [
                _call(organization, workspace, user, agent_id),
                _call(organization, second_workspace, user, agent_id),
            ]
        )

        kinds = sorted(kind for kind, _ in outcomes)
        assert kinds == ["error", "ok"], outcomes
        (winner,) = [value for kind, value in outcomes if kind == "ok"]
        (refusal,) = [value for kind, value in outcomes if kind == "error"]
        assert isinstance(refusal, AgentNotInWorkspace), refusal
        link = OmegaDeploymentObserveLink.no_workspace_objects.get(ee_agent_id=agent_id)
        assert link.workspace_id == winner.link.workspace_id
        loser = second_workspace if link.workspace_id == workspace.id else workspace
        assert not Project.no_workspace_objects.filter(
            workspace=loser, name=winner.project.name
        ).exists()
