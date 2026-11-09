"""Mistake analysis on a Console agent's Observe project (learning-service P1-08, OD5;
ruling 158 addendum 2: each agent's runs are analysed continuously by Error Feed).

Acceptance: the ensure gives the project a ``TraceScanConfig`` with a rate above 0 that the
Error Feed engine claims; without Error Feed on the plan nothing is written and the answer
says why; a user's later change to the rate stays.
"""

import uuid

import pytest
from django.test import override_settings
from structlog.testing import capture_logs

from tfc.licensing.types import DeploymentFlavor, DeploymentLocation
from tfc.utils.error_codes import get_error_message
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace_investigation import TraceInvestigationJob
from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.trace_scanner import is_trace_sampled
from tracer.services.trace_investigation import (
    claim_due_investigations,
    record_trace_notifications,
)
from tracer.tests.omega_agents.conftest import (
    capabilities,
    ensure,
    hosted,
    new_agent_id,
)
from tracer.tests.test_trace_investigation_control import _delivery
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

RATE = 0.1


def _trace_id(sampled: bool) -> uuid.UUID:
    """A trace id the stable per-trace sampling keeps (or drops) at the agent's rate."""
    for number in range(1, 10_000):
        candidate = uuid.UUID(int=number)
        if is_trace_sampled(str(candidate), RATE) is sampled:
            return candidate
    raise AssertionError("no trace id found")


def _config(project_id):
    return TraceScanConfig.no_workspace_objects.filter(project_id=project_id).first()


def _claims_for(project):
    """What one engine pass claims on ``project``. The engine claims across every project,
    so a job another test left due would otherwise be counted here too."""
    claims = claim_due_investigations(
        worker_id="agent-test", engine_version="omega-v1", limit=5
    )["claims"]
    return [claim for claim in claims if str(claim["project_id"]) == str(project.id)]


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_the_error_feed_engine_claims_the_agents_sampled_runs(auth_client):
    result = ensure(auth_client, new_agent_id()).data["result"]
    project = Project.no_workspace_objects.get(id=result["projectId"])
    sampled, skipped = _trace_id(True), _trace_id(False)

    record_trace_notifications(deliveries=[_delivery(project, trace_id=sampled)])
    record_trace_notifications(
        deliveries=[_delivery(project, offset=2, trace_id=skipped)]
    )
    claims = _claims_for(project)

    jobs = TraceInvestigationJob.no_workspace_objects.filter(project=project)
    assert [str(job.trace_id) for job in jobs] == [str(sampled)]
    assert len(claims) == 1
    assert str(claims[0]["trace_id"]) == str(sampled)


@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_a_project_the_ensure_never_set_up_is_never_claimed(user, workspace):
    """The control: Observe's own default (no config, or rate 0) analyses nothing."""
    project = get_or_create_project(
        project_name="agent/never-ensured-000000000000",
        organization_id=str(workspace.organization_id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    TraceScanConfig.no_workspace_objects.create(project=project)  # the model's default

    record_trace_notifications(
        deliveries=[_delivery(project, trace_id=_trace_id(True))]
    )

    assert _claims_for(project) == []
    assert not TraceInvestigationJob.no_workspace_objects.filter(
        project=project
    ).exists()


def test_without_error_feed_on_the_plan_nothing_is_written_and_the_answer_says_why(
    auth_client,
):
    agent_id = new_agent_id()
    with hosted(), capture_logs() as logs:
        response = ensure(auth_client, agent_id)

    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["mistakes"] == {
        "status": "off",
        "samplingRate": None,
        "reason": "not_on_plan",
        "reasonCode": "PLAN_FEATURE_MISSING",
        "message": get_error_message("OMEGA_AGENT_OBSERVE_MISTAKES_NOT_ON_PLAN"),
    }
    assert len(result["alerts"]) == 2  # the rest of the defaults are not held back
    assert _config(result["projectId"]) is None
    link = OmegaDeploymentObserveLink.no_workspace_objects.get(ee_agent_id=agent_id)
    assert link.scan_configured_at is None
    (event,) = [
        e for e in logs if e["event"] == "omega_agent_observe_mistakes_not_on_plan"
    ]
    assert (event["ee_agent_id"], event["reason_code"]) == (
        agent_id,
        "PLAN_FEATURE_MISSING",
    )

    # Error Feed added to the plan: the next ensure sets the analysis up.
    again = ensure(auth_client, agent_id).data["result"]
    assert again["mistakes"]["status"] == "on"
    assert _config(result["projectId"]).sampling_rate == RATE


def test_a_self_hosted_install_without_ee_says_why(auth_client):
    with capabilities(DeploymentFlavor.OSS, DeploymentLocation.SELF_HOSTED):
        result = ensure(auth_client, new_agent_id()).data["result"]

    assert result["mistakes"]["status"] == "off"
    assert result["mistakes"]["reason"] == "not_on_plan"
    assert result["mistakes"]["reasonCode"] == "EE_CODE_UNAVAILABLE"
    assert _config(result["projectId"]) is None


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"sampling_rate": 0}, ("off", 0.0, "turned_off")),
        ({"enabled": False}, ("off", RATE, "turned_off")),
        ({"sampling_rate": 0.5}, ("on", 0.5, None)),
    ],
)
def test_a_users_later_change_stays_and_is_reported(auth_client, change, expected):
    agent_id = new_agent_id()
    first = ensure(auth_client, agent_id).data["result"]
    TraceScanConfig.no_workspace_objects.filter(project_id=first["projectId"]).update(
        **change
    )

    again = ensure(auth_client, agent_id).data["result"]

    mistakes = again["mistakes"]
    assert (
        mistakes["status"],
        mistakes["samplingRate"],
        mistakes["reason"],
    ) == expected
    config = _config(first["projectId"])
    for field, value in change.items():
        assert getattr(config, field) == value
    if expected[0] == "off":
        assert mistakes["message"] == get_error_message(
            "OMEGA_AGENT_OBSERVE_MISTAKES_TURNED_OFF"
        )


def test_a_config_observe_made_at_rate_0_before_the_link_is_set_to_the_agents_rate(
    auth_client, user, workspace
):
    from tracer.tests.omega_agents.test_agent_observe_api import _project_name

    agent_id = new_agent_id()
    project = get_or_create_project(
        project_name=_project_name(agent_id),
        organization_id=str(workspace.organization_id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    # What Observe's own scanner does on first access (queries/trace_scanner.py).
    TraceScanConfig.objects.get_or_create(
        project_id=project.id, defaults={"sampling_rate": 0, "enabled": True}
    )

    result = ensure(auth_client, agent_id).data["result"]

    assert result["projectId"] == str(project.id)
    assert result["mistakes"]["status"] == "on"
    assert _config(project.id).sampling_rate == RATE
    assert TraceScanConfig.all_objects.filter(project_id=project.id).count() == 1


def test_a_rate_set_by_hand_before_the_link_is_kept(auth_client, user, workspace):
    from tracer.tests.omega_agents.test_agent_observe_api import _project_name

    agent_id = new_agent_id()
    project = get_or_create_project(
        project_name=_project_name(agent_id),
        organization_id=str(workspace.organization_id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    TraceScanConfig.no_workspace_objects.create(project=project, sampling_rate=0.75)

    result = ensure(auth_client, agent_id).data["result"]

    assert result["mistakes"]["samplingRate"] == 0.75
    assert _config(project.id).sampling_rate == 0.75
