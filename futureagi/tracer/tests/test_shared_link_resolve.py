from datetime import timedelta
import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project
from tracer.models.shared_link import SharedLink


@pytest.mark.django_db
def test_trace_shared_link_lifecycle_resolves_valid_token(
    api_client,
    auth_client,
    organization,
    trace,
    observation_span,
    user,
):
    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace.id),
            "access_type": "public",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    public_link = response.json()["result"]
    assert public_link["resource_type"] == "trace"
    assert public_link["access_type"] == "public"
    assert public_link["access_list"] == []
    assert public_link["share_url"].endswith(f"/shared/{public_link['token']}")

    detail_response = auth_client.get(f"/tracer/shared-links/{public_link['id']}/")
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.json()["result"]["id"] == public_link["id"]

    list_response = auth_client.get(
        "/tracer/shared-links/",
        data={"resource_type": "trace", "resource_id": str(trace.id)},
    )
    assert list_response.status_code == status.HTTP_200_OK
    listed_ids = {row["id"] for row in list_response.json()["result"]}
    assert public_link["id"] in listed_ids

    resolved = api_client.get(f"/tracer/shared/{public_link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "trace"
    assert payload["resource_id"] == str(trace.id)
    assert payload["access_type"] == "public"
    assert payload["data"]["trace"]["id"] == str(trace.id)
    assert payload["data"]["trace"]["project_id"] == str(trace.project_id)
    assert payload["data"]["summary"]["total_spans"] == 1
    assert (
        payload["data"]["observation_spans"][0]["observation_span"]["id"]
        == observation_span.id
    )

    restricted_response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace.id),
            "access_type": "restricted",
            "emails": ["viewer@example.com"],
        },
        format="json",
    )
    assert restricted_response.status_code == status.HTTP_201_CREATED
    restricted_link = restricted_response.json()["result"]
    assert [entry["email"] for entry in restricted_link["access_list"]] == [
        "viewer@example.com"
    ]

    updated_expiry = timezone.now() + timedelta(days=1)
    patch_response = auth_client.patch(
        f"/tracer/shared-links/{restricted_link['id']}/",
        data={"expires_at": updated_expiry.isoformat()},
        format="json",
    )
    assert patch_response.status_code == status.HTTP_200_OK
    assert patch_response.json()["result"]["expires_at"] is not None

    put_response = auth_client.put(
        f"/tracer/shared-links/{restricted_link['id']}/",
        data={
            "access_type": "restricted",
            "expires_at": updated_expiry.isoformat(),
        },
        format="json",
    )
    assert put_response.status_code == status.HTTP_200_OK
    assert put_response.json()["result"]["access_type"] == "restricted"

    add_access = auth_client.post(
        f"/tracer/shared-links/{restricted_link['id']}/access/",
        data={"emails": ["second-viewer@example.com"]},
        format="json",
    )
    assert add_access.status_code == status.HTTP_201_CREATED
    added_access = add_access.json()["result"][0]
    assert added_access["email"] == "second-viewer@example.com"

    remove_access = auth_client.delete(
        f"/tracer/shared-links/{restricted_link['id']}/access/{added_access['id']}/"
    )
    assert remove_access.status_code == status.HTTP_200_OK

    unauthenticated = api_client.get(f"/tracer/shared/{restricted_link['token']}/")
    assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED
    assert unauthenticated.data["code"] == "not_authenticated"

    creator_resolved = auth_client.get(f"/tracer/shared/{restricted_link['token']}/")
    assert creator_resolved.status_code == status.HTTP_200_OK
    assert creator_resolved.json()["data"]["trace"]["id"] == str(trace.id)

    viewer = User.objects.create_user(
        email="viewer@example.com",
        password="testpassword123",
        name="Shared Link Viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    api_client.force_authenticate(user=viewer)
    viewer_resolved = api_client.get(f"/tracer/shared/{restricted_link['token']}/")
    assert viewer_resolved.status_code == status.HTTP_200_OK
    assert viewer_resolved.json()["data"]["trace"]["id"] == str(trace.id)

    revoke_response = auth_client.delete(f"/tracer/shared-links/{public_link['id']}/")
    assert revoke_response.status_code == status.HTTP_200_OK

    revoked = api_client.get(f"/tracer/shared/{public_link['token']}/")
    assert revoked.status_code == status.HTTP_410_GONE
    assert revoked.data["code"] == "gone"

    expired_link = SharedLink.objects.create(
        resource_type="trace",
        resource_id=str(trace.id),
        access_type="public",
        created_by=user,
        organization=trace.project.organization,
        workspace=trace.project.workspace,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    expired = api_client.get(f"/tracer/shared/{expired_link.token}/")
    assert expired.status_code == status.HTTP_410_GONE
    assert "expired" in expired.data["detail"]


@pytest.mark.django_db
def test_dashboard_shared_link_resolves_dashboard_detail_payload(
    api_client,
    auth_client,
    workspace,
    user,
):
    dashboard = Dashboard.objects.create(
        workspace=workspace,
        name="Shared dashboard",
        description="Dashboard resolved through a share token",
        created_by=user,
        updated_by=user,
    )
    widget = DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Latency widget",
        position=0,
        width=6,
        height=4,
        query_config={
            "time_range": {"preset": "7D"},
            "metrics": [{"name": "latency", "type": "system_metric"}],
        },
        chart_config={"chart_type": "line"},
        created_by=user,
    )

    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "dashboard",
            "resource_id": str(dashboard.id),
            "access_type": "public",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "dashboard"
    assert payload["resource_id"] == str(dashboard.id)
    assert payload["data"]["id"] == str(dashboard.id)
    assert payload["data"]["name"] == dashboard.name
    assert payload["data"]["description"] == dashboard.description
    assert payload["data"]["widget_count"] == 1
    assert payload["data"]["widgets"][0]["id"] == str(widget.id)
    assert payload["data"]["widgets"][0]["chart_config"]["chart_type"] == "line"


@pytest.mark.django_db
def test_project_shared_link_resolves_observe_project_payload(
    api_client,
    auth_client,
    observe_project,
):
    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "project",
            "resource_id": str(observe_project.id),
            "access_type": "public",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "project"
    assert payload["resource_id"] == str(observe_project.id)
    assert payload["data"]["id"] == str(observe_project.id)
    assert payload["data"]["name"] == observe_project.name
    assert payload["data"]["trace_type"] == "observe"
    assert payload["data"]["model_type"] == observe_project.model_type
    assert (
        payload["data"]["url_path"]
        == f"/dashboard/observe/{observe_project.id}/llm-tracing"
    )


@pytest.mark.django_db
def test_shared_link_create_rejects_unsupported_and_cross_workspace_resources(
    auth_client,
    organization,
    user,
):
    unsupported_id = str(uuid.uuid4())
    unsupported = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "dataset",
            "resource_id": unsupported_id,
            "access_type": "public",
        },
        format="json",
    )
    assert unsupported.status_code == status.HTTP_400_BAD_REQUEST
    assert not SharedLink.no_workspace_objects.filter(
        resource_type="dataset",
        resource_id=unsupported_id,
    ).exists()

    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Shared Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    other_project = Project.no_workspace_objects.create(
        name=f"Other Workspace Project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    cross_workspace = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "project",
            "resource_id": str(other_project.id),
            "access_type": "public",
        },
        format="json",
    )
    assert cross_workspace.status_code == status.HTTP_404_NOT_FOUND
    assert not SharedLink.no_workspace_objects.filter(
        resource_type="project",
        resource_id=str(other_project.id),
    ).exists()


@pytest.mark.django_db
def test_project_share_falls_back_to_user_org_when_request_org_unresolved(
    observe_project,
    user,
):
    """Sharing must resolve the org the same way the read paths do.

    Authentication sets ``request.organization`` to None whenever it cannot
    resolve one from the X-Organization-Id header, and every view that shows
    these resources falls back to the user's active membership
    (``ProjectView._request_organization``). When the share endpoint read the
    attribute bare instead, its existence check ran with ``organization=None``,
    matched nothing, and answered "Shared resource not found" for the very
    project the user had open.
    """
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "project",
            "resource_id": str(observe_project.id),
            "access_type": "public",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    link = SharedLink.no_workspace_objects.get(id=response.json()["result"]["id"])
    assert link.organization_id == observe_project.organization_id


@pytest.fixture
def shared_run_test(db, organization, workspace):
    from simulate.models import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.simulator_agent import SimulatorAgent

    agent = AgentDefinition.objects.create(
        agent_name="Shared Call Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1230001111",
        inbound=True,
        description="Agent for shared call tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator = SimulatorAgent.objects.create(
        name="Shared Call Simulator",
        prompt="You are a test simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )
    return RunTest.objects.create(
        name="Shared Call Run",
        agent_definition=agent,
        simulator_agent=simulator,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def shared_test_execution(db, shared_run_test):
    from simulate.models.test_execution import TestExecution

    return TestExecution.objects.create(
        run_test=shared_run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        completed_calls=2,
        simulator_agent=shared_run_test.simulator_agent,
        agent_definition=shared_run_test.agent_definition,
    )


@pytest.fixture
def shared_scenario(db, organization, workspace, shared_run_test):
    from model_hub.models.choices import StatusType
    from simulate.models import Scenarios

    scenario = Scenarios.objects.create(
        name="Shared Call Scenario",
        description="Scenario for shared call tests",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=shared_run_test.agent_definition,
        status=StatusType.COMPLETED.value,
    )
    shared_run_test.scenarios.add(scenario)
    return scenario


@pytest.fixture
def voice_call(db, shared_test_execution, shared_scenario):
    from simulate.models.test_execution import CallExecution, CallTranscript

    call = CallExecution.objects.create(
        test_execution=shared_test_execution,
        scenario=shared_scenario,
        phone_number="+9100000001",
        status="completed",
        overall_score=0.9,
        duration_seconds=60,
        recording_url="https://example.com/recording.wav",
        call_metadata={"harness_outcome_status": "passed"},
    )
    CallTranscript.objects.create(
        call_execution=call,
        speaker_role=CallTranscript.SpeakerRole.USER,
        content="I need to return my order",
        start_time_ms=0,
        end_time_ms=1500,
    )
    return call


@pytest.fixture
def chat_call(db, organization, workspace, shared_test_execution, shared_scenario):
    from simulate.models.chat_message import ChatMessageModel
    from simulate.models.test_execution import CallExecution

    call = CallExecution.objects.create(
        test_execution=shared_test_execution,
        scenario=shared_scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        status="completed",
        call_metadata={"harness_outcome_status": "failed"},
    )
    ChatMessageModel.objects.create(
        call_execution=call,
        role=ChatMessageModel.RoleChoices.USER,
        messages=["Where is my refund?"],
        content=[{"role": "user", "content": "Where is my refund?"}],
        session_id="chat-session-1",
        organization=organization,
        workspace=workspace,
    )
    return call


def _share_call(client, call, access_type="public", emails=None):
    data = {
        "resource_type": "call_execution",
        "resource_id": str(call.id),
        "access_type": access_type,
    }
    if emails is not None:
        data["emails"] = emails
    return client.post("/tracer/shared-links/", data=data, format="json")


@pytest.mark.django_db
def test_voice_call_shared_link_resolves_v3_call_detail_payload(
    api_client,
    auth_client,
    voice_call,
):
    response = _share_call(auth_client, voice_call)
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]
    assert link["resource_type"] == "call_execution"
    assert link["resource_id"] == str(voice_call.id)

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")
    assert resolved.status_code == status.HTTP_200_OK
    payload = resolved.json()
    assert payload["resource_type"] == "call_execution"
    assert payload["resource_id"] == str(voice_call.id)
    data = payload["data"]
    assert data["id"] == str(voice_call.id)
    assert data["simulation_call_type"] == "voice"
    assert data["outcome"] == "passed"
    assert data["recordings"]["combined"] == "https://example.com/recording.wav"
    assert [row["content"] for row in data["transcript"]] == [
        "I need to return my order"
    ]

    in_app = auth_client.get(f"/simulate/v3/call-executions/{voice_call.id}/")
    assert in_app.status_code == status.HTTP_200_OK
    assert data == in_app.json()


@pytest.mark.django_db
def test_chat_call_shared_link_resolves_chat_transcript(
    api_client,
    auth_client,
    chat_call,
):
    response = _share_call(auth_client, chat_call)
    assert response.status_code == status.HTTP_201_CREATED
    token = response.json()["result"]["token"]

    resolved = api_client.get(f"/tracer/shared/{token}/")
    assert resolved.status_code == status.HTTP_200_OK
    data = resolved.json()["data"]
    assert data["id"] == str(chat_call.id)
    assert data["simulation_call_type"] == "text"
    assert data["outcome"] == "failed"
    assert data["recordings"] == {}
    assert len(data["transcript"]) == 1
    assert data["transcript"][0]["messages"] == ["Where is my refund?"]

    in_app = auth_client.get(f"/simulate/v3/call-executions/{chat_call.id}/")
    assert data == in_app.json()


@pytest.mark.django_db
def test_restricted_call_shared_link_enforces_acl(
    api_client,
    auth_client,
    organization,
    voice_call,
):
    response = _share_call(
        auth_client,
        voice_call,
        access_type="restricted",
        emails=["call-viewer@example.com"],
    )
    assert response.status_code == status.HTTP_201_CREATED
    token = response.json()["result"]["token"]

    anonymous = api_client.get(f"/tracer/shared/{token}/")
    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED

    outsider = User.objects.create_user(
        email="call-outsider@example.com",
        password="testpassword123",
        name="Not On The List",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    api_client.force_authenticate(user=outsider)
    forbidden = api_client.get(f"/tracer/shared/{token}/")
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN

    creator = auth_client.get(f"/tracer/shared/{token}/")
    assert creator.status_code == status.HTTP_200_OK
    assert creator.json()["data"]["id"] == str(voice_call.id)

    viewer = User.objects.create_user(
        email="call-viewer@example.com",
        password="testpassword123",
        name="On The List",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    api_client.force_authenticate(user=viewer)
    allowed = api_client.get(f"/tracer/shared/{token}/")
    assert allowed.status_code == status.HTTP_200_OK
    assert allowed.json()["data"]["id"] == str(voice_call.id)


@pytest.mark.django_db
def test_call_shared_link_create_rejects_out_of_scope_calls(
    auth_client,
    organization,
    user,
    voice_call,
):
    from accounts.models.organization import Organization
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution

    def _call_in(run_org, run_workspace):
        run = RunTest.no_workspace_objects.create(
            name=f"Out of scope run {uuid.uuid4().hex[:8]}",
            agent_definition=voice_call.test_execution.agent_definition,
            organization=run_org,
            workspace=run_workspace,
        )
        execution = TestExecution.no_workspace_objects.create(
            run_test=run,
            status=TestExecution.ExecutionStatus.COMPLETED,
        )
        return CallExecution.no_workspace_objects.create(
            test_execution=execution,
            scenario=voice_call.scenario,
            status="completed",
        )

    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Call Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    other_org = Organization.objects.create(name="Other Call Org")
    other_org_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Org Workspace {uuid.uuid4().hex[:8]}",
        organization=other_org,
        is_default=True,
        created_by=user,
    )

    cross_workspace_call = _call_in(organization, other_workspace)
    cross_org_call = _call_in(other_org, other_org_workspace)
    for call in (cross_workspace_call, cross_org_call):
        response = _share_call(auth_client, call)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    bad_id = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "call_execution",
            "resource_id": "not-a-uuid",
            "access_type": "public",
        },
        format="json",
    )
    assert bad_id.status_code == status.HTTP_404_NOT_FOUND

    run_test = voice_call.test_execution.run_test
    run_test.deleted = True
    run_test.save(update_fields=["deleted"])
    deleted_run = _share_call(auth_client, voice_call)
    assert deleted_run.status_code == status.HTTP_404_NOT_FOUND

    assert not SharedLink.no_workspace_objects.filter(
        resource_type="call_execution"
    ).exists()


@pytest.mark.django_db
def test_call_shared_link_resolves_404_after_call_is_deleted(
    api_client,
    auth_client,
    voice_call,
):
    response = _share_call(auth_client, voice_call)
    assert response.status_code == status.HTTP_201_CREATED
    token = response.json()["result"]["token"]

    voice_call.delete()

    resolved = api_client.get(f"/tracer/shared/{token}/")
    assert resolved.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_call_shared_link_resolves_full_payload_for_viewer_in_other_workspace(
    auth_client,
    user,
    chat_call,
):
    from accounts.models.organization import Organization
    from conftest import WorkspaceAwareAPIClient

    response = _share_call(
        auth_client,
        chat_call,
        access_type="restricted",
        emails=["other-org-viewer@example.com"],
    )
    assert response.status_code == status.HTTP_201_CREATED
    token = response.json()["result"]["token"]
    expected = auth_client.get(f"/simulate/v3/call-executions/{chat_call.id}/")
    assert len(expected.json()["transcript"]) == 1

    other_org = Organization.objects.create(name="Viewer Org")
    viewer = User.objects.create_user(
        email="other-org-viewer@example.com",
        password="testpassword123",
        name="Other Org Viewer",
        organization=other_org,
        organization_role=OrganizationRoles.OWNER,
    )
    viewer_workspace = Workspace.no_workspace_objects.create(
        name=f"Viewer Workspace {uuid.uuid4().hex[:8]}",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=viewer,
    )
    viewer_client = WorkspaceAwareAPIClient()
    viewer_client.force_authenticate(user=viewer)
    viewer_client.set_workspace(viewer_workspace)
    try:
        resolved = viewer_client.get(f"/tracer/shared/{token}/")
    finally:
        viewer_client.stop_workspace_injection()

    assert resolved.status_code == status.HTTP_200_OK, resolved.json()
    assert resolved.json()["data"] == expected.json()


@pytest.mark.django_db
def test_call_shared_link_builds_the_payload_in_the_link_workspace(
    api_client,
    auth_client,
    workspace,
    voice_call,
):
    from unittest.mock import patch

    response = _share_call(auth_client, voice_call)
    assert response.status_code == status.HTTP_201_CREATED
    token = response.json()["result"]["token"]

    with patch(
        "simulate.views.run_results_v3.build_call_execution_detail",
        return_value={"id": str(voice_call.id)},
    ) as build:
        resolved = api_client.get(f"/tracer/shared/{token}/")

    assert resolved.status_code == status.HTTP_200_OK
    assert build.call_args.kwargs["workspace"] == workspace


@pytest.mark.django_db
def test_call_detail_error_localizer_state_uses_the_given_workspace(
    workspace,
    voice_call,
):
    from unittest.mock import patch

    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    voice_call.eval_outputs = {"cfg-1": {"status": "completed", "output": True}}
    serializer = CallExecutionDetailSerializer(
        voice_call, context={"workspace": workspace, "detail_mode": True}
    )
    with (
        patch(
            "simulate.serializers.test_execution.error_localizer_enabled",
            return_value=True,
        ),
        patch(
            "model_hub.selectors.error_localizer."
            "get_error_localizer_state_by_eval_config",
            return_value={},
        ) as get_state,
    ):
        serializer.get_eval_metrics(voice_call)

    get_state.assert_called_once_with(voice_call.id, ["cfg-1"], workspace)


@pytest.mark.django_db
def test_call_shared_link_without_a_workspace_still_resolves(
    api_client,
    auth_client,
    chat_call,
):
    response = _share_call(auth_client, chat_call)
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]
    SharedLink.no_workspace_objects.filter(id=link["id"]).update(workspace=None)

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")

    assert resolved.status_code == status.HTTP_200_OK
    assert resolved.json()["data"]["id"] == str(chat_call.id)
    assert len(resolved.json()["data"]["transcript"]) == 1


@pytest.mark.django_db
def test_removed_invite_can_be_invited_again(
    api_client,
    auth_client,
    organization,
    voice_call,
):
    email = "re-invited@example.com"
    viewer = User.objects.create_user(
        email=email,
        password="testpassword123",
        name="Invited Twice",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    link = _share_call(auth_client, voice_call, access_type="restricted").json()[
        "result"
    ]
    api_client.force_authenticate(user=viewer)

    added = auth_client.post(
        f"/tracer/shared-links/{link['id']}/access/", {"emails": [email]}, format="json"
    )
    assert added.status_code == status.HTTP_201_CREATED
    access_id = added.json()["result"][0]["id"]
    removed = auth_client.delete(
        f"/tracer/shared-links/{link['id']}/access/{access_id}/"
    )
    assert removed.status_code == status.HTTP_200_OK
    blocked = api_client.get(f"/tracer/shared/{link['token']}/")
    assert blocked.status_code == status.HTTP_403_FORBIDDEN

    again = auth_client.post(
        f"/tracer/shared-links/{link['id']}/access/", {"emails": [email]}, format="json"
    )

    assert again.status_code == status.HTTP_201_CREATED
    assert [row["email"] for row in again.json()["result"]] == [email]
    allowed = api_client.get(f"/tracer/shared/{link['token']}/")
    assert allowed.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_restricted_trace_link_matches_the_viewer_email_case_insensitively(
    api_client,
    auth_client,
    organization,
    trace,
    observation_span,
):
    response = auth_client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace.id),
            "access_type": "restricted",
            "emails": ["viewer@example.com"],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    link = response.json()["result"]

    viewer = User.objects.create_user(
        email="Viewer@example.com",
        password="testpassword123",
        name="Mixed Case Viewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    api_client.force_authenticate(user=viewer)
    resolved = api_client.get(f"/tracer/shared/{link['token']}/")

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    assert resolved.json()["data"]["trace"]["id"] == str(trace.id)

    # Inviting the same address in another case keeps the one row.
    again = auth_client.post(
        f"/tracer/shared-links/{link['id']}/access/",
        {"emails": ["VIEWER@example.com"]},
        format="json",
    )
    assert again.status_code == status.HTTP_201_CREATED
    detail = auth_client.get(f"/tracer/shared-links/{link['id']}/")
    assert [row["email"] for row in detail.json()["result"]["access_list"]] == [
        "viewer@example.com"
    ]


def _seed_collector_span(project, trace_id):
    """A root span that only ClickHouse holds, as the collector writes it."""
    from types import SimpleNamespace

    from tracer.tests._ch_seed import seed_ch_span

    root_id = f"span_{uuid.uuid4().hex[:16]}"
    started = timezone.now() - timedelta(seconds=5)
    seed_ch_span(
        SimpleNamespace(
            id=root_id,
            trace_id=trace_id,
            project_id=project.id,
            project=project,
            parent_span_id=None,
            name="collector root",
            observation_type="agent",
            status="OK",
            start_time=started,
            end_time=timezone.now(),
            latency_ms=5000,
            input={"prompt": "Hello"},
            output={"response": "World"},
            metadata={"source": "collector"},
            tags=["collector"],
            created_at=started,
            updated_at=started,
        )
    )
    return root_id


def _share_trace(client, trace_id):
    return client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": trace_id,
            "access_type": "public",
        },
        format="json",
    )


@pytest.mark.django_db
def test_collector_trace_shares_from_clickhouse_without_a_postgres_row(
    api_client,
    auth_client,
    project,
):
    from tracer.models.trace import Trace

    trace_id = str(uuid.uuid4())
    root_id = _seed_collector_span(project, trace_id)
    assert not Trace.no_workspace_objects.filter(id=trace_id).exists()

    response = _share_trace(auth_client, trace_id)
    assert response.status_code == status.HTTP_201_CREATED, response.content

    resolved = api_client.get(f"/tracer/shared/{response.json()['result']['token']}/")

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    data = resolved.json()["data"]
    assert data["trace"]["id"] == trace_id
    assert data["trace"]["project_id"] == str(project.id)
    assert data["trace"]["name"] == "collector root"
    assert data["summary"]["total_spans"] == 1
    assert data["observation_spans"][0]["observation_span"]["id"] == root_id


@pytest.mark.django_db
def test_collector_trace_in_another_workspace_cannot_be_shared(
    auth_client,
    organization,
    user,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other Collector Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    other_project = Project.no_workspace_objects.create(
        name=f"Other Collector Project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace_id = str(uuid.uuid4())
    _seed_collector_span(other_project, trace_id)

    response = _share_trace(auth_client, trace_id)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not SharedLink.no_workspace_objects.filter(
        resource_type="trace", resource_id=trace_id
    ).exists()

    unknown = _share_trace(auth_client, str(uuid.uuid4()))
    assert unknown.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_shared_collector_trace_is_gone_not_broken_while_clickhouse_is_down(
    api_client,
    auth_client,
    project,
):
    from unittest.mock import patch

    trace_id = str(uuid.uuid4())
    _seed_collector_span(project, trace_id)
    link = _share_trace(auth_client, trace_id).json()["result"]

    with patch(
        "tracer.services.clickhouse.v2.query_service."
        "V2AnalyticsQueryService.execute_ch_query",
        side_effect=ConnectionError("clickhouse unavailable"),
    ):
        resolved = api_client.get(f"/tracer/shared/{link['token']}/")

    assert resolved.status_code == status.HTTP_404_NOT_FOUND
    assert "no longer exists" in resolved.json()["detail"]
    assert SharedLink.objects.filter(id=link["id"]).exists()
    recovered = api_client.get(f"/tracer/shared/{link['token']}/")
    assert recovered.status_code == status.HTTP_200_OK
