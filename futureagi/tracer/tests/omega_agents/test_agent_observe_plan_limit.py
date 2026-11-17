"""The plan's monitor limit and a Console agent's two default alerts (learning-service P1-08:
"monitors deferred with a reason").

As for a deployment (``omega_deployments/test_ensure_plan_limit.py``): at the limit the
ensure still answers 200 with the project and link, creates no monitor, and says why in
``alertsDeferred``, in the agent's wording; each deferral is counted and logged; a later
ensure creates the pair once the plan has room. Mistake analysis is not held back by it.
"""

import pytest
from structlog.testing import capture_logs

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.tests.omega_agents.conftest import ensure, new_agent_id
from tracer.tests.omega_deployments.conftest import CloudPlan, cloud_plan
from tracer.tests.omega_deployments.test_ensure_plan_limit import (
    _hand_made,
    _manual_project,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

DEFERRED_EVENT = "omega_agent_observe_alerts_deferred"


def _link(agent_id):
    return OmegaDeploymentObserveLink.no_workspace_objects.get(ee_agent_id=agent_id)


def test_at_the_limit_the_agent_is_linked_and_its_alerts_wait_with_the_reason(
    auth_client, user, workspace, organization
):
    agent_id = new_agent_id()
    project = _manual_project(user, workspace)
    with cloud_plan(3) as plan:
        _hand_made(auth_client, project, 3)
        with capture_logs() as logs:
            first = ensure(auth_client, agent_id)

        assert first.status_code == 200, first.data
        result = first.data["result"]
        assert result["alerts"] == []
        assert result["alertsDeferred"] == {
            "code": "ENTITLEMENT_LIMIT",
            "message": get_error_message("OMEGA_AGENT_OBSERVE_ALERTS_AT_LIMIT").format(
                needed=2, limit=3, current=3, to_free=2
            ),
            "needed": 2,
            "limit": 3,
            "currentUsage": 3,
            "upgradeCta": CloudPlan.UPGRADE_CTA,
        }
        assert "this agent's page" in result["alertsDeferred"]["message"]
        assert result["mistakes"]["status"] == "on"
        link = _link(agent_id)
        assert link.alerts_created_at is None
        assert link.alerts_deferred_count == 1
        (event,) = [entry for entry in logs if entry["event"] == DEFERRED_EVENT]
        assert {
            key: event[key]
            for key in event
            if key not in ("event", "log_level", "deferred_count")
        } == {
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "ee_agent_id": agent_id,
            "project_id": result["projectId"],
            "code": "ENTITLEMENT_LIMIT",
            "limit": 3,
            "current_usage": 3,
            "needed": 2,
        }

        plan.limit = 15
        later = ensure(auth_client, agent_id).data["result"]

    assert [alert["kind"] for alert in later["alerts"]] == [
        "errors_spike",
        "daily_tokens",
    ]
    assert later["alertsDeferred"] is None
    assert (
        UserAlertMonitor.no_workspace_objects.filter(
            project_id=result["projectId"]
        ).count()
        == 2
    )
    link.refresh_from_db()
    assert link.alerts_created_at is not None
    assert link.alerts_deferred_count == 1


def test_a_plan_smaller_than_the_pair_says_upgrade_in_the_agents_wording(auth_client):
    with cloud_plan(1):
        result = ensure(auth_client, new_agent_id()).data["result"]

    assert result["alerts"] == []
    assert result["alertsDeferred"]["message"] == get_error_message(
        "OMEGA_AGENT_OBSERVE_ALERTS_PLAN_TOO_SMALL"
    ).format(needed=2, limit=1)
