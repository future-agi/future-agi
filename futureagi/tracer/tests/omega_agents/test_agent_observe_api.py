"""POST /tracer/omega/agents/<id>/observe/: one project per Console agent, its link, its
defaults, and the route's walls (learning-service P1-08).

Acceptance: a unique live link per EE agent id; idempotent; two default monitors; mistake
analysis on at the OD5 rate; the Console wall, never the Deployments one; the
``agent_observe`` part off by default answers 404 and writes nothing.
"""

import hashlib

import pytest

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig
from tracer.tests.omega_agents.conftest import (
    agent_url,
    all_rows,
    ensure,
    new_agent_id,
    unlink,
)
from tracer.tests.omega_deployments.conftest import ensure_url, new_deployment_id
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NAME = "Support Bot"


def _project_name(agent_id, slug="support-bot"):
    return f"agent/{slug}-{hashlib.sha256(agent_id.encode()).hexdigest()[:12]}"


def _links(workspace):
    """The agent links of this test's own workspace."""
    return OmegaDeploymentObserveLink.no_workspace_objects.filter(
        kind="agent", workspace=workspace
    )


def _monitors(project_id):
    return UserAlertMonitor.no_workspace_objects.filter(project_id=project_id)


def test_first_ensure_creates_project_link_monitors_and_mistake_analysis(
    auth_client, user, workspace, organization
):
    agent_id = new_agent_id()

    response = ensure(auth_client, agent_id)

    assert response.status_code == 200, response.data
    assert response.data["status"] is True
    result = response.data["result"]
    project = Project.no_workspace_objects.get(id=result["projectId"])
    assert result["projectName"] == project.name == _project_name(agent_id)
    assert (project.trace_type, project.workspace_id, project.organization_id) == (
        "observe",
        workspace.id,
        organization.id,
    )
    assert result["agentName"] == NAME

    link = _links(workspace).get(ee_agent_id=agent_id)
    assert link.ee_deployment_id is None
    assert (link.workspace_id, link.organization_id, link.project_id) == (
        workspace.id,
        organization.id,
        project.id,
    )
    assert (link.agent_name, link.created_by_id) == (NAME, user.id)
    assert link.alerts_created_at is not None
    assert link.scan_configured_at is not None

    assert [alert["kind"] for alert in result["alerts"]] == [
        "errors_spike",
        "daily_tokens",
    ]
    errors, tokens = (
        UserAlertMonitor.no_workspace_objects.get(id=alert["id"])
        for alert in result["alerts"]
    )
    assert (errors.name, tokens.name) == ("Errors spike", "Daily tokens over 1,000,000")
    assert (link.errors_monitor_id, link.daily_tokens_monitor_id) == (
        errors.id,
        tokens.id,
    )
    assert (errors.metric_type, errors.threshold_type) == (
        "count_of_errors",
        "percentage_change",
    )
    assert (tokens.metric_type, tokens.critical_threshold_value) == (
        "daily_tokens_spent",
        1_000_000.0,
    )
    for monitor in (errors, tokens):
        # The project is this agent's alone, and Console spans carry no deployment
        # environment or run source: a filter on either would count nothing.
        assert monitor.filters == {}
        assert monitor.project_id == project.id
        assert monitor.notification_emails == [user.email]
    assert result["alertsDeferred"] is None

    config = TraceScanConfig.no_workspace_objects.get(project=project)
    assert (config.sampling_rate, config.enabled, config.scan_version) == (
        0.1,
        True,
        "omega-v1",
    )
    assert result["mistakes"] == {
        "status": "on",
        "samplingRate": 0.1,
        "reason": None,
        "reasonCode": None,
        "message": None,
    }


def test_a_second_ensure_returns_the_same_answer_and_writes_nothing_new(
    auth_client, workspace
):
    agent_id = new_agent_id()

    first = ensure(auth_client, agent_id)
    after_first = all_rows()
    second = ensure(auth_client, agent_id)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.data["result"] == first.data["result"]
    assert all_rows() == after_first
    assert _links(workspace).count() == 1
    assert Project.no_workspace_objects.filter(workspace=workspace).count() == 1
    assert _monitors(first.data["result"]["projectId"]).count() == 2
    assert (
        TraceScanConfig.no_workspace_objects.filter(
            project__workspace=workspace
        ).count()
        == 1
    )


def test_a_project_ingest_created_first_is_the_one_linked(
    auth_client, user, workspace, organization
):
    agent_id = new_agent_id()
    ingested = get_or_create_project(
        project_name=_project_name(agent_id),
        organization_id=str(organization.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )

    response = ensure(auth_client, agent_id)

    assert response.status_code == 200, response.data
    assert response.data["result"]["projectId"] == str(ingested.id)
    assert Project.no_workspace_objects.filter(workspace=workspace).count() == 1


def test_two_agents_with_one_name_get_two_projects(auth_client, workspace):
    first = ensure(auth_client, new_agent_id()).data["result"]
    second = ensure(auth_client, new_agent_id()).data["result"]

    assert first["projectId"] != second["projectId"]
    assert first["projectName"] != second["projectName"]
    assert {a["id"] for a in first["alerts"]}.isdisjoint(
        {a["id"] for a in second["alerts"]}
    )
    assert _links(workspace).count() == 2


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Café Bot ☕", "cafe-bot"),
        ("  ".join(["x"] * 3), "x-x-x"),
        ("___", "agent"),
        ("日本語", "agent"),
        ("A" * 60, "a" * 40),
        ("ab" + "-" * 50 + "cd", "ab-cd"),
    ],
)
def test_the_project_name_is_a_bounded_ascii_slug_and_the_ids_tag(
    auth_client, name, slug
):
    agent_id = new_agent_id()

    response = ensure(auth_client, agent_id, name=name)

    assert response.status_code == 200, response.data
    assert response.data["result"]["projectName"] == _project_name(agent_id, slug)
    assert response.data["result"]["agentName"] == name


def test_no_deployments_admission_is_needed(auth_client, organization):
    from omega_rollout.models import ProductAccess

    assert not ProductAccess.objects.filter(
        organization=organization, product="deployments"
    ).exists()
    deployment = auth_client.post(
        ensure_url(new_deployment_id()), {"name": "billing-bot"}, format="json"
    )
    assert (
        deployment.status_code == 404
    )  # the Deployments wall stands for its own route

    response = ensure(auth_client, new_agent_id())

    assert response.status_code == 200, response.data


def test_an_org_not_admitted_to_the_console_gets_404_and_nothing_is_written(
    auth_client, organization
):
    from omega_rollout.models import ProductAccess

    ProductAccess.objects.filter(organization=organization).delete()
    before = all_rows()

    response = ensure(auth_client, new_agent_id())

    assert response.status_code == 404
    assert response.data["code"] == "product_not_admitted"
    assert all_rows() == before


def test_a_paused_console_is_503_and_nothing_is_written(auth_client):
    from omega_rollout.models import ProductRollout

    ProductRollout.objects.filter(product="console").update(paused=True)
    before = all_rows()

    response = ensure(auth_client, new_agent_id())

    assert response.status_code == 503
    assert response.data["code"] == "product_paused"
    assert all_rows() == before


@pytest.mark.parametrize("released", ["", "deploy_cli,hub_cli,agent_observe_x"])
def test_the_part_off_is_404_on_both_methods_and_writes_nothing(
    auth_client, workspace, settings, released
):
    agent_id = new_agent_id()
    owner = ensure(auth_client, agent_id).data["result"]
    before = all_rows()
    settings.OMEGA_RELEASED_PARTS = released

    created = ensure(auth_client, new_agent_id())
    ended = unlink(auth_client, agent_id)

    for response in (created, ended):
        assert response.status_code == 404
        assert response.data["code"] == "product_not_admitted"
    assert all_rows() == before
    (link,) = _links(workspace)
    assert (link.ee_agent_id, str(link.project_id)) == (agent_id, owner["projectId"])


def test_the_part_is_off_by_default(auth_client, settings, monkeypatch):
    del settings.OMEGA_RELEASED_PARTS
    monkeypatch.delenv("OMEGA_RELEASED_PARTS", raising=False)
    before = all_rows()

    response = ensure(auth_client, new_agent_id())

    assert response.status_code == 404
    assert all_rows() == before


def test_anonymous_is_refused_before_any_work(api_client):
    before = all_rows()

    for response in (ensure(api_client, new_agent_id()), unlink(api_client, "a")):
        assert response.status_code in (401, 403)
    assert all_rows() == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": None},
        {"name": " Support Bot"},
        {"name": "Support Bot "},
        {"name": "Support\nBot"},
        {"name": "Support\x7fBot"},
        {"name": "b" * 201},
    ],
)
def test_a_name_ee_would_refuse_is_400(auth_client, body):
    before = all_rows()

    response = auth_client.post(agent_url(new_agent_id()), body, format="json")

    assert response.status_code == 400
    assert get_error_message("OMEGA_AGENT_OBSERVE_INVALID_NAME") in str(response.data)
    assert all_rows() == before


def test_a_name_with_a_nul_is_400(auth_client):
    """DRF refuses a NUL in any text field with its own message, before ours runs."""
    before = all_rows()

    response = auth_client.post(
        agent_url(new_agent_id()), {"name": "Support\x00Bot"}, format="json"
    )

    assert response.status_code == 400
    assert all_rows() == before


def test_the_longest_name_ee_allows_is_linked(auth_client):
    response = ensure(auth_client, new_agent_id(), name="b" * 200)

    assert response.status_code == 200, response.data
    assert response.data["result"]["agentName"] == "b" * 200


def test_an_unknown_body_field_is_400(auth_client):
    before = all_rows()

    response = auth_client.post(
        agent_url(new_agent_id()),
        {"name": NAME, "project_id": "anything"},
        format="json",
    )

    assert response.status_code == 400
    assert all_rows() == before


@pytest.mark.parametrize(
    "agent_id", ["-agent", "_agent", "agent%20one", "agent%3Cx%3E", "a" * 256]
)
def test_a_malformed_agent_id_is_400_on_both_methods(auth_client, agent_id):
    before = all_rows()

    for response in (ensure(auth_client, agent_id), unlink(auth_client, agent_id)):
        assert response.status_code == 400
        assert response.data["message"] == get_error_message(
            "OMEGA_AGENT_OBSERVE_INVALID_ID"
        )
    assert all_rows() == before


def test_the_longest_id_is_linked(auth_client, workspace):
    agent_id = "a" * 255

    response = ensure(auth_client, agent_id)

    assert response.status_code == 200, response.data
    (link,) = _links(workspace)
    assert (link.ee_agent_id, str(link.project_id)) == (
        agent_id,
        response.data["result"]["projectId"],
    )


def test_a_non_observe_project_with_the_name_is_409_and_nothing_is_written(
    auth_client, user, workspace, organization
):
    agent_id = new_agent_id()
    get_or_create_project(
        project_name=_project_name(agent_id),
        organization_id=str(organization.id),
        project_type="experiment",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    before = all_rows()

    response = ensure(auth_client, agent_id)

    assert response.status_code == 409
    assert response.data["message"] == get_error_message(
        "OMEGA_AGENT_OBSERVE_NAME_TAKEN"
    ).format(_project_name(agent_id))
    assert all_rows() == before


def test_a_name_another_live_agent_link_holds_is_409_never_adopted(
    auth_client, user, workspace, organization
):
    agent_id = new_agent_id()
    held = get_or_create_project(
        project_name=_project_name(agent_id),
        organization_id=str(organization.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    OmegaDeploymentObserveLink.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        kind="agent",
        ee_agent_id=new_agent_id(),
        agent_name="Other",
        project=held,
    )
    before = all_rows()

    response = ensure(auth_client, agent_id)

    assert response.status_code == 409
    assert all_rows() == before
    assert not OmegaDeploymentObserveLink.no_workspace_objects.filter(
        ee_agent_id=agent_id
    ).exists()
