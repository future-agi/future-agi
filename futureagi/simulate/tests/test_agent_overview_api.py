import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessEnvironmentVersion,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    RunTest,
    TestExecution,
)
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.agent_setup import store_agent
from simulate.services.hosted_harness import (
    create_hosted_job,
    create_selected_harness_run,
    provision_scenarios,
    register_attempt,
)
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_harness_provider import _v1_payload
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.project import Project
from tracer.services.observability_switch import PREREQUISITES, ObservabilityRefused

AGENTS = "/simulate/agent-definitions/"
ENVIRONMENTS = "/simulate/api/harness-environments/"
START = timezone.now() - timedelta(days=30)


def _agent(organization, workspace, name, provider="vapi"):
    return AgentDefinition.objects.create(
        agent_name=name,
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description=f"{name} prompt",
        provider=provider,
        organization=organization,
        workspace=workspace,
    )


def _run_test(agent, name="Suite"):
    return RunTest.objects.create(
        name=name,
        agent_definition=agent,
        organization=agent.organization,
        workspace=agent.workspace,
    )


def _at(model, row, minutes):
    model.all_objects.filter(id=row.id).update(
        created_at=START + timedelta(minutes=minutes)
    )


def _job(run_test, *, minutes, environment=None, execution=None, kind="github"):
    job = HostedHarnessJob.no_workspace_objects.create(
        organization=run_test.organization,
        workspace=run_test.workspace,
        run_test=run_test,
        environment=environment,
        test_execution=execution,
        name=f"env-{minutes}",
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest=uuid.uuid4().hex,
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"source": {"kind": kind}, "metadata": {}},
    )
    _at(HostedHarnessJob, job, minutes)
    return job


def _environment(agent, minutes, kind="github"):
    return _job(_run_test(agent, f"suite-{minutes}"), minutes=minutes, kind=kind)


def _execution(run_test, minutes, **fields):
    execution = TestExecution.objects.create(
        run_test=run_test,
        agent_definition=run_test.agent_definition,
        status=TestExecution.ExecutionStatus.COMPLETED,
        **fields,
    )
    _at(TestExecution, execution, minutes)
    return execution


def _receipts(job, *statuses):
    attempt = HostedHarnessAttempt.objects.create(
        job=job,
        attempt_number=1,
        token_hash="t" * 64,
        fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    for index, receipt_status in enumerate(statuses):
        HostedHarnessReceipt.objects.create(
            job=job,
            attempt=attempt,
            scenario=HostedHarnessScenario.objects.create(
                job=job, scenario_key=f"scenario-{index}"
            ),
            attempt_number=1,
            digest="sha256:" + str(index).zfill(64),
            status=receipt_status,
            body={},
        )


def _rows(auth_client):
    response = auth_client.get(AGENTS, {"limit": 50})
    assert response.status_code == status.HTTP_200_OK, response.content
    return {row["agent_name"]: row for row in response.json()["results"]}


@pytest.mark.django_db
def test_list_tells_a_code_agent_from_a_hosted_one(
    auth_client, organization, workspace
):
    code = _agent(organization, workspace, "Harbor Hotels", provider="")
    version = code.create_version(description="", commit_message="v1", status="active")
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    built = _environment(code, 1, kind="archive")
    hosted = _agent(organization, workspace, "Northwind")
    hosted.create_version(description="p", commit_message="v1", status="active")
    hosted.create_version(description="p2", commit_message="v2", status="active")

    rows = _rows(auth_client)

    assert {
        key: rows["Harbor Hotels"][key]
        for key in ("agent_kind", "source", "version_count", "environment_count")
    } == {
        "agent_kind": "source",
        "source": "archive",
        "version_count": 1,
        "environment_count": 1,
    }
    assert rows["Harbor Hotels"]["environments"] == [
        {"id": str(built.id), "name": "env-1"}
    ]
    assert {
        key: rows["Northwind"][key]
        for key in ("agent_kind", "source", "version_count", "environment_count")
    } == {
        "agent_kind": None,
        "source": "vapi",
        "version_count": 2,
        "environment_count": 0,
    }
    assert rows["Northwind"]["environments"] == []
    assert "environment_name" not in rows["Northwind"]


@pytest.mark.django_db
def test_list_counts_every_environment_and_lists_the_five_oldest(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Busy Agent")
    environments = [_environment(agent, minutes) for minutes in (6, 2, 4, 1, 5, 3)]
    gone = _environment(agent, 0)
    HostedHarnessJob.all_objects.filter(id=gone.id).update(deleted=True)
    _job(environments[0].run_test, minutes=9, environment=environments[0])

    row = _rows(auth_client)["Busy Agent"]

    assert row["environment_count"] == 6
    assert [entry["name"] for entry in row["environments"]] == [
        "env-1",
        "env-2",
        "env-3",
        "env-4",
        "env-5",
    ]


@pytest.mark.django_db
def test_detail_reports_kind_source_count_and_the_switch(
    auth_client, organization, workspace, user
):
    agent = _agent(organization, workspace, "Northwind")
    _environment(agent, 1)
    project = Project.objects.create(
        name="northwind",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
        user=user,
    )
    row = ObservabilityProvider.objects.create(
        project=project,
        provider=ProviderChoices.VAPI,
        enabled=True,
        organization=organization,
        workspace=workspace,
    )
    AgentDefinition.objects.filter(id=agent.id).update(observability_provider=row)

    def detail():
        response = auth_client.get(f"{AGENTS}{agent.id}/")
        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        return (
            body["agent_kind"],
            body["source"],
            body["environment_count"],
            body["observability_enabled"],
        )

    assert detail() == (None, "vapi", 1, True)
    Project.objects.filter(id=project.id).update(deleted=True)
    assert detail() == (None, "vapi", 1, False)
    AgentDefinition.objects.filter(id=agent.id).update(observability_provider=None)
    assert detail() == (None, "vapi", 1, False)


@pytest.mark.django_db
def test_runs_list_environment_and_simulate_runs_newest_first(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    version = agent.create_version(
        description="p", commit_message="v1", status="active"
    )
    environment = _environment(agent, 1)
    first = _execution(environment.run_test, 10, agent_version=version)
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        test_execution=first
    )
    second = _execution(environment.run_test, 20)
    rerun = _job(
        environment.run_test, minutes=20, environment=environment, execution=second
    )
    _receipts(rerun, "passed", "passed", "failed", "errored", "skipped")
    old = _run_test(agent, "Old refill check")
    legacy = _execution(old, 30)
    removed = _execution(old, 40)
    TestExecution.all_objects.filter(id=removed.id).update(deleted=True)

    response = auth_client.get(f"{AGENTS}{agent.id}/runs/")

    assert response.status_code == status.HTTP_200_OK, response.content
    runs = response.json()["results"]
    assert [run["test_execution_id"] for run in runs] == [
        str(legacy.id),
        str(second.id),
        str(first.id),
    ]
    assert runs[0] | {"started_at": None, "completed_at": None} == {
        "kind": "simulate",
        "test_execution_id": str(legacy.id),
        "run_test_id": str(old.id),
        "run_test_name": "Old refill check",
        "run_number": 1,
        "status": "completed",
        "environment": None,
        "agent_version": None,
        "environment_version": None,
        "partial": None,
        "outcome_passed": None,
        "outcome_failed": None,
        "outcome_skipped": None,
        "started_at": None,
        "completed_at": None,
    }
    assert (runs[1]["kind"], runs[1]["run_number"], runs[1]["environment"]) == (
        "environment",
        2,
        {"id": str(environment.id), "name": "env-1"},
    )
    assert (
        runs[1]["outcome_passed"],
        runs[1]["outcome_failed"],
        runs[1]["outcome_skipped"],
    ) == (2, 2, 1)
    assert (runs[2]["run_number"], runs[2]["agent_version"]) == (
        1,
        {"id": str(version.id), "version_number": version.version_number},
    )
    assert (runs[2]["outcome_passed"], runs[2]["outcome_failed"]) == (0, 0)


@pytest.mark.django_db
def test_runs_of_a_deleted_test_are_left_out(auth_client, organization, workspace):
    agent = _agent(organization, workspace, "Northwind")
    kept = _execution(_run_test(agent, "Kept"), 10)
    removed_test = _run_test(agent, "Removed")
    _execution(removed_test, 20)
    RunTest.all_objects.filter(id=removed_test.id).update(deleted=True)

    runs = auth_client.get(f"{AGENTS}{agent.id}/runs/").json()["results"]

    assert [run["test_execution_id"] for run in runs] == [str(kept.id)]


@pytest.mark.django_db
def test_runs_of_a_deleted_environment_are_left_out_and_the_count_agrees(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    live = _environment(agent, 1)
    live_run = _execution(live.run_test, 10)
    HostedHarnessJob.no_workspace_objects.filter(id=live.id).update(
        test_execution=live_run
    )
    gone = _environment(agent, 2)
    own_run = _execution(gone.run_test, 20)
    HostedHarnessJob.no_workspace_objects.filter(id=gone.id).update(
        test_execution=own_run
    )
    rerun_execution = _execution(gone.run_test, 30)
    _job(gone.run_test, minutes=30, environment=gone, execution=rerun_execution)
    HostedHarnessJob.all_objects.filter(id=gone.id).update(deleted=True)

    runs = auth_client.get(f"{AGENTS}{agent.id}/runs/").json()["results"]
    detail = auth_client.get(f"{AGENTS}{agent.id}/").json()

    assert [run["test_execution_id"] for run in runs] == [str(live_run.id)]
    assert detail["environment_count"] == 1


@pytest.mark.django_db
def test_an_environment_of_a_deleted_test_is_not_counted(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    live = _environment(agent, 1)
    gone = _environment(agent, 2)
    RunTest.all_objects.filter(id=gone.run_test_id).update(deleted=True)

    detail = auth_client.get(f"{AGENTS}{agent.id}/").json()
    row = _rows(auth_client)["Northwind"]

    assert detail["environment_count"] == 1
    assert row["environment_count"] == 1
    assert [ref["id"] for ref in row["environments"]] == [str(live.id)]


@pytest.mark.django_db
def test_runs_limit_defaults_to_five_and_rejects_out_of_range(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    suite = _run_test(agent)
    for minutes in range(7):
        _execution(suite, minutes)
    url = f"{AGENTS}{agent.id}/runs/"

    assert len(auth_client.get(url).json()["results"]) == 5
    assert len(auth_client.get(url, {"limit": 2}).json()["results"]) == 2
    for limit in (0, 21, "many"):
        response = auth_client.get(url, {"limit": limit})
        assert response.status_code == status.HTTP_400_BAD_REQUEST, limit


@pytest.mark.django_db
def test_runs_of_an_agent_in_another_organization_404(auth_client, workspace):
    stranger = Organization.objects.create(name="Other Organization")
    foreign = AgentDefinition.no_workspace_objects.create(
        agent_name="Foreign",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="x",
        organization=stranger,
    )

    for url in (f"{AGENTS}{foreign.id}/runs/", f"{AGENTS}{uuid.uuid4()}/runs/"):
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND, url


@pytest.mark.django_db
def test_environments_filter_by_agent_and_carry_their_last_run(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    tested = _environment(agent, 1)
    _execution(tested.run_test, 10)
    latest = _execution(tested.run_test, 20)
    rerun = _job(tested.run_test, minutes=20, environment=tested, execution=latest)
    _receipts(rerun, "passed", "failed")
    untested = _environment(agent, 2)
    other = _environment(_agent(organization, workspace, "Other"), 3)
    headers = {"HTTP_X_WORKSPACE_ID": str(workspace.id)}

    response = auth_client.get(
        ENVIRONMENTS, {"agent_definition_id": agent.id}, **headers
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    rows = {row["id"]: row for row in response.json()["results"]}
    assert set(rows) == {str(tested.id), str(untested.id)}
    last_run = rows[str(tested.id)]["last_run"]
    assert (last_run["test_execution_id"], last_run["run_number"]) == (
        str(latest.id),
        2,
    )
    assert (last_run["outcome_passed"], last_run["outcome_failed"]) == (1, 1)
    assert rows[str(untested.id)]["last_run"] is None
    everything = auth_client.get(ENVIRONMENTS, **headers).json()["results"]
    assert {row["id"] for row in everything} == {
        str(tested.id),
        str(untested.id),
        str(other.id),
    }
    assert all("last_run" in row for row in everything)


@pytest.mark.django_db
def test_environments_reject_an_agent_id_that_is_not_a_uuid(auth_client, workspace):
    response = auth_client.get(
        ENVIRONMENTS,
        {"agent_definition_id": "not-a-uuid"},
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_edit_passes_the_switch_through(auth_client, organization, workspace):
    agent = _agent(organization, workspace, "Northwind")
    url = f"{AGENTS}{agent.id}/edit/"

    with patch("simulate.views.agent_definition.set_agent_observability") as switch:
        on = auth_client.put(url, {"observability_enabled": True}, format="json")
        off = auth_client.put(url, {"observability_enabled": False}, format="json")
        untouched = auth_client.put(url, {"agent_name": "Northwind 2"}, format="json")

    assert (on.status_code, off.status_code, untouched.status_code) == (200, 200, 200)
    assert [call.kwargs["enabled"] for call in switch.call_args_list] == [True, False]
    assert all(
        call.kwargs["agent_definition"].id == agent.id for call in switch.call_args_list
    )
    assert all(call.kwargs["workspace"] == workspace for call in switch.call_args_list)


def _signed_in(organization, workspace, *, org_role, org_level, ws_role, ws_level):
    """A client that goes through the real login and auth class, unlike the
    forced-auth fixture, so the workspace write-role check actually runs."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership

    member = User.objects.create_user(
        email=f"switch-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Switch tester",
        organization=organization,
        organization_role=org_role,
    )
    org_membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=member,
        organization=organization,
        defaults={"role": org_role, "level": org_level, "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=member,
        workspace=workspace,
        defaults={
            "role": ws_role,
            "level": ws_level,
            "organization_membership": org_membership,
            "is_active": True,
        },
    )
    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": member.email, "password": "testpassword123"},
        format="json",
    )
    assert login.status_code == status.HTTP_200_OK, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


@pytest.mark.django_db
def test_a_workspace_viewer_cannot_flip_the_switch_or_save_anything(
    organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    viewer = _signed_in(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER_VIEW_ONLY,
        org_level=Level.VIEWER,
        ws_role=OrganizationRoles.WORKSPACE_VIEWER,
        ws_level=Level.WORKSPACE_VIEWER,
    )

    with patch("simulate.views.agent_definition.set_agent_observability") as switch:
        response = viewer.put(
            f"{AGENTS}{agent.id}/edit/",
            {"observability_enabled": True, "agent_name": "Renamed"},
            format="json",
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
    assert "Write access denied to this workspace" in str(response.data)
    switch.assert_not_called()
    agent.refresh_from_db()
    assert agent.agent_name == "Northwind"


@pytest.mark.django_db
def test_a_workspace_member_can_still_flip_the_switch(organization, workspace):
    agent = _agent(organization, workspace, "Northwind")
    member = _signed_in(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
    )

    with patch("simulate.views.agent_definition.set_agent_observability") as switch:
        response = member.put(
            f"{AGENTS}{agent.id}/edit/",
            {"observability_enabled": True},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert [call.kwargs["enabled"] for call in switch.call_args_list] == [True]


@pytest.mark.django_db
def test_a_refused_switch_saves_nothing_from_the_same_edit(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    refusal = ObservabilityRefused([PREREQUISITES["credential"]])

    with patch(
        "simulate.views.agent_definition.set_agent_observability", side_effect=refusal
    ):
        response = auth_client.put(
            f"{AGENTS}{agent.id}/edit/",
            {"observability_enabled": True, "agent_name": "Renamed"},
            format="json",
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "API key is required" in response.json()["message"]
    agent.refresh_from_db()
    assert agent.agent_name == "Northwind"


@pytest.mark.django_db
def test_version_detail_leaves_the_stored_agent_to_its_own_endpoint(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Harbor Hotels", provider="")
    stored = agent.create_version(description="", commit_message="v1", status="active")
    store_agent(agent_version_id=stored.id, agent=source_agent(), sdk_version="0.2.3")
    bare = agent.create_version(description="", commit_message="v2", status="active")

    for version in (stored, bare):
        detail = auth_client.get(f"{AGENTS}{agent.id}/versions/{version.id}/")
        assert detail.status_code == status.HTTP_200_OK, detail.content
        assert "agent_details" not in detail.json()
    endpoint = auth_client.get(f"{AGENTS}{agent.id}/versions/{stored.id}/agent/")
    assert endpoint.status_code == status.HTTP_200_OK, endpoint.content
    assert endpoint.json()


@pytest.mark.django_db
def test_version_detail_masks_the_key_wherever_it_is_saved(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    own = agent.create_version(description="p", commit_message="v1", status="active")
    ProviderCredentials.objects.create(
        agent_version=own, provider_type="vapi", api_key="sk-version-key-123456"
    )
    legacy_agent = _agent(organization, workspace, "Legacy")
    legacy = legacy_agent.create_version(
        description="p", commit_message="v1", status="active"
    )
    ProviderCredentials.objects.create(
        agent_definition=legacy_agent,
        provider_type="vapi",
        api_key="sk-legacy-key-654321",
    )
    keyless_agent = _agent(organization, workspace, "Keyless")
    keyless = keyless_agent.create_version(
        description="p", commit_message="v1", status="active"
    )

    def key(owner, version):
        return auth_client.get(f"{AGENTS}{owner.id}/versions/{version.id}/").json()[
            "api_key"
        ]

    for value, secret in (
        (key(agent, own), "sk-version-key-123456"),
        (key(legacy_agent, legacy), "sk-legacy-key-654321"),
    ):
        assert value and value != secret and secret not in value
    assert key(keyless_agent, keyless) is None


@pytest.mark.django_db
def test_a_run_records_the_version_it_resolves_and_a_pin_still_wins(
    organization, workspace
):
    from simulate.services.alk_simulate_ingestion import resolved_agent_version

    agent = _agent(organization, workspace, "Northwind")
    first = agent.create_version(description="p", commit_message="v1", status="active")
    suite = _run_test(agent)
    assert resolved_agent_version(suite) == first
    second = agent.create_version(
        description="p2", commit_message="v2", status="active"
    )
    assert resolved_agent_version(suite) == second
    suite.agent_version = first
    assert resolved_agent_version(suite) == first
    assert resolved_agent_version(RunTest(name="prompt run")) is None


@pytest.mark.django_db
def test_each_environment_run_records_the_latest_version_at_launch(user, workspace):
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=1),
        idempotency_key="versions-environment",
        workspace=workspace,
    )
    attempt = register_attempt(
        environment.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    provision_scenarios(
        attempt,
        {
            "operation": "provision",
            "name": "Versions suite",
            "modality": "text",
            "personas": [
                {
                    "scenario_key": "scenario-a",
                    "name": "A",
                    "role": "customer",
                    "situation": "First situation",
                    "outcome": "First outcome",
                    "persona": {"name": "A"},
                }
            ],
        },
    )
    environment.refresh_from_db()
    payload = dict(environment.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "authoring_object_key": "harness/environments/authored.tar.gz",
    }
    environment.payload = payload
    environment.state = environment.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])
    agent = environment.run_test.agent_definition
    first = agent.latest_version or agent.create_version(
        description="p", commit_message="v1", status="active"
    )

    with override_settings(HARNESS_MAX_ARTIFACT_BYTES=10_737_418_240):
        before, _ = create_selected_harness_run(
            environment, scenario_keys=["scenario-a"], trials=1, idempotency_key="one"
        )
        second = agent.create_version(
            description="p2", commit_message="v2", status="active"
        )
        after, _ = create_selected_harness_run(
            environment, scenario_keys=["scenario-a"], trials=1, idempotency_key="two"
        )

    assert before.test_execution.agent_version_id == first.id
    assert after.test_execution.agent_version_id == second.id
    environment.run_test.refresh_from_db()
    assert environment.run_test.agent_version_id is None


def _environment_version(environment, number):
    return HarnessEnvironmentVersion.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        version_number=number,
        status="active",
        origin="authoring",
        hash="sha256:" + str(number) * 64,
        archive_object_key=f"harness/environments/{number}.tar.gz",
        archive_sha256="",
        archive_size=0,
    )


def _register(environment, *keys, minutes):
    for key in keys:
        row = HostedHarnessScenario.objects.create(job=environment, scenario_key=key)
        _at(HostedHarnessScenario, row, minutes)


def _run_of(environment, minutes, keys):
    execution = _execution(environment.run_test, minutes, total_scenarios=len(keys))
    job = _job(
        environment.run_test,
        minutes=minutes,
        environment=environment,
        execution=execution,
    )
    job.payload = {**job.payload, "metadata": {"selected_scenario_keys": list(keys)}}
    # Trials multiply the calls, not the scenarios a run picked.
    job.scenario_count = len(keys) * 3
    job.save(update_fields=["payload", "scenario_count"])
    return execution, job


@pytest.mark.django_db
def test_runs_carry_the_environment_version_and_say_when_they_were_partial(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    environment = _environment(agent, 1)
    _register(environment, "a", "b", "c", "d", minutes=2)
    full, full_job = _run_of(environment, 10, ["a", "b", "c", "d"])
    full_job.environment_version = _environment_version(environment, 7)
    full_job.save(update_fields=["environment_version"])
    some, _ = _run_of(environment, 20, ["a", "b"])
    _register(environment, "e", minutes=30)
    simulate = _execution(_run_test(agent, "Old suite"), 40)

    response = auth_client.get(f"{AGENTS}{agent.id}/runs/", {"limit": 5})

    assert response.status_code == status.HTTP_200_OK, response.content
    rows = {row["test_execution_id"]: row for row in response.json()["results"]}
    assert rows[str(full.id)]["environment_version"]["version_number"] == 7
    # Every scenario the environment had then, though it gained one later.
    assert rows[str(full.id)]["partial"] is False
    assert rows[str(some.id)]["environment_version"] is None
    assert rows[str(some.id)]["partial"] is True
    assert rows[str(simulate.id)]["environment_version"] is None
    assert rows[str(simulate.id)]["partial"] is None


@pytest.mark.django_db
def test_a_scenario_removed_after_a_run_still_counts_for_that_run(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    environment = _environment(agent, 1)
    _register(environment, "a", "b", minutes=2)
    run, _ = _run_of(environment, 10, ["a"])
    removed = HostedHarnessScenario.objects.get(job=environment, scenario_key="b")
    HostedHarnessScenario.all_objects.filter(id=removed.id).update(
        deleted=True, deleted_at=START + timedelta(minutes=20)
    )

    rows = auth_client.get(f"{AGENTS}{agent.id}/runs/").json()["results"]

    assert [
        row["partial"] for row in rows if row["test_execution_id"] == str(run.id)
    ] == [True]


@pytest.mark.django_db
def test_run_summaries_read_every_environment_s_scenarios_in_one_query(
    auth_client, organization, workspace, django_assert_max_num_queries
):
    agent = _agent(organization, workspace, "Northwind")
    for minutes in (1, 2, 3):
        environment = _environment(agent, minutes)
        _register(environment, "a", "b", minutes=minutes)
        _run_of(environment, minutes + 10, ["a"])
    url = f"{AGENTS}{agent.id}/runs/"
    with CaptureQueriesContext(connection) as one_page:
        auth_client.get(url, {"limit": 1})
    with django_assert_max_num_queries(len(one_page.captured_queries)):
        auth_client.get(url, {"limit": 3})


@pytest.mark.django_db
def test_environments_carry_the_version_each_is_pinned_to(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    pinned = _environment(agent, 1)
    version = agent.create_version(
        description="p", commit_message="v1", status="active"
    )
    pinned.agent_version = version
    pinned.save(update_fields=["agent_version"])
    unpinned = _environment(agent, 2)

    response = auth_client.get(
        ENVIRONMENTS,
        {"agent_definition_id": agent.id},
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    rows = {row["id"]: row for row in response.json()["results"]}
    assert rows[str(pinned.id)]["agent_version"] == {
        "id": str(version.id),
        "version_number": 1,
    }
    assert rows[str(unpinned.id)]["agent_version"] is None


@pytest.mark.django_db
def test_a_version_cannot_be_deleted_whether_or_not_an_environment_pins_it(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Northwind")
    agent.create_version(description="p", commit_message="v1", status="active")
    pinned = agent.create_version(description="p", commit_message="v2", status="draft")
    environment = _environment(agent, 1)
    environment.agent_version = pinned
    environment.save(update_fields=["agent_version"])

    refused = auth_client.delete(f"{AGENTS}{agent.id}/versions/{pinned.id}/delete/")
    assert refused.status_code == status.HTTP_404_NOT_FOUND, refused.content
    pinned.refresh_from_db()
    assert pinned.deleted is False


@pytest.mark.django_db
def test_an_agent_with_environments_cannot_be_deleted_one_at_a_time(
    auth_client, organization, workspace
):
    tested = _agent(organization, workspace, "Northwind")
    _environment(tested, 1)
    _environment(tested, 2)
    free = _agent(organization, workspace, "Spare")

    single = auth_client.delete(f"{AGENTS}{tested.id}/delete/")
    assert single.status_code == status.HTTP_400_BAD_REQUEST, single.content
    assert single.json()["detail"] == (
        "Northwind is tested by 2 environments. Delete those environments first."
    )
    operations = auth_client.delete(
        f"/simulate/api/agent-definition-operations/{tested.id}/"
    )
    assert operations.status_code == status.HTTP_400_BAD_REQUEST, operations.content
    tested.refresh_from_db()
    assert tested.deleted is False

    assert (
        auth_client.delete(f"{AGENTS}{free.id}/delete/").status_code
        == status.HTTP_200_OK
    )
    free.refresh_from_db()
    assert free.deleted is True


@pytest.mark.django_db
def test_version_detail_says_where_a_code_version_was_read_from(
    auth_client, organization, workspace
):
    agent = _agent(organization, workspace, "Harbor Hotels", provider="")
    code = agent.create_version(description="", commit_message="v1", status="active")
    HarnessAgentVersionRuntime.no_workspace_objects.create(
        agent_version=code,
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
        hash="sha256:" + "a" * 64,
        source_kind="github",
        trigger="creation",
        source={
            "repository": "https://github.com/acme/hotel-agent",
            "ref": "main",
            "commit_sha": "b" * 40,
            "installation_id": 175787687,
        },
    )
    bare = agent.create_version(description="", commit_message="v2", status="draft")

    detail = auth_client.get(f"{AGENTS}{agent.id}/versions/{code.id}/")
    assert detail.status_code == status.HTTP_200_OK, detail.content
    assert detail.json()["source"] == {
        "kind": "github",
        "repository": "https://github.com/acme/hotel-agent",
        "ref": "main",
        "commit_sha": "b" * 40,
    }
    assert (
        auth_client.get(f"{AGENTS}{agent.id}/versions/{bare.id}/").json()["source"]
        is None
    )


def _observe_project(organization, workspace, name):
    return Project.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _code_agent(organization, workspace, name, **stored):
    agent = _agent(organization, workspace, name, provider="")
    version = agent.create_version(description="", commit_message="v1", status="active")
    store_agent(
        agent_version_id=version.id, agent=source_agent(**stored), sdk_version="0.2.3"
    )
    return agent


def _link(client, agent, project_id, **fields):
    return client.put(
        f"{AGENTS}{agent.id}/edit/",
        {"production_project_id": project_id, **fields},
        format="json",
    )


def _detail(client, agent):
    return client.get(f"{AGENTS}{agent.id}/").json()


@pytest.mark.django_db
def test_a_code_agent_links_and_unlinks_its_production_project(
    auth_client, organization, workspace
):
    agent = _code_agent(organization, workspace, "Harbor Hotels")
    chosen = _observe_project(organization, workspace, "Harbor production")
    other = _observe_project(organization, workspace, "Harbor staging")

    assert _detail(auth_client, agent)["observe_project"] is None

    linked = _link(auth_client, agent, str(chosen.id))
    assert linked.status_code == status.HTTP_200_OK, linked.content
    assert _detail(auth_client, agent)["observe_project"] == {
        "id": str(chosen.id),
        "name": "Harbor production",
    }

    moved = _link(auth_client, agent, str(other.id))
    assert moved.status_code == status.HTTP_200_OK, moved.content
    assert _detail(auth_client, agent)["observe_project"]["name"] == "Harbor staging"

    unlinked = _link(auth_client, agent, None)
    assert unlinked.status_code == status.HTTP_200_OK, unlinked.content
    assert _detail(auth_client, agent)["observe_project"] is None


@pytest.mark.django_db
def test_linking_and_unlinking_leave_simulation_export_as_it_was(
    auth_client, organization, workspace
):
    agent = _code_agent(organization, workspace, "Harbor Hotels")
    chosen = _observe_project(organization, workspace, "Harbor production")
    url = f"{AGENTS}{agent.id}/edit/"
    switched_on = auth_client.put(url, {"observability_enabled": True}, format="json")
    assert switched_on.status_code == status.HTTP_200_OK, switched_on.content

    _link(auth_client, agent, str(chosen.id))
    assert _detail(auth_client, agent)["observability_enabled"] is True
    _link(auth_client, agent, None)
    assert _detail(auth_client, agent)["observability_enabled"] is True


@pytest.mark.django_db
def test_turning_simulation_export_off_keeps_the_production_project(
    auth_client, organization, workspace
):
    agent = _code_agent(organization, workspace, "Harbor Hotels")
    chosen = _observe_project(organization, workspace, "Harbor production")
    _link(auth_client, agent, str(chosen.id))

    # What the agent config form sends on every save.
    saved = auth_client.put(
        f"{AGENTS}{agent.id}/edit/",
        {"agent_name": "Harbor Hotels", "observability_enabled": False},
        format="json",
    )

    assert saved.status_code == status.HTTP_200_OK, saved.content
    body = _detail(auth_client, agent)
    assert body["observability_enabled"] is False
    assert body["observe_project"] == {
        "id": str(chosen.id),
        "name": "Harbor production",
    }


@pytest.mark.django_db
def test_a_code_agent_without_instrumentation_can_still_link(
    auth_client, organization, workspace
):
    agent = _code_agent(
        organization, workspace, "Harbor Hotels", instrumentation="none"
    )
    chosen = _observe_project(organization, workspace, "Harbor production")

    linked = _link(auth_client, agent, str(chosen.id))

    assert linked.status_code == status.HTTP_200_OK, linked.content
    assert _detail(auth_client, agent)["observability_enabled"] is False


@pytest.mark.django_db
def test_a_deleted_production_project_reads_as_not_linked(
    auth_client, organization, workspace
):
    agent = _code_agent(organization, workspace, "Harbor Hotels")
    chosen = _observe_project(organization, workspace, "Harbor production")
    _link(auth_client, agent, str(chosen.id))

    Project.all_objects.filter(id=chosen.id).update(deleted=True)

    assert _detail(auth_client, agent)["observe_project"] is None


@pytest.mark.django_db
def test_a_hosted_agent_shows_its_pulled_project_and_cannot_link(
    auth_client, organization, workspace
):
    hosted = _agent(organization, workspace, "Northwind")
    project = _observe_project(organization, workspace, "Northwind production")

    refused = _link(auth_client, hosted, str(project.id), agent_name="Renamed")

    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.content
    assert "Only a code agent" in refused.json()["detail"]
    hosted.refresh_from_db()
    # The rest of the edit was refused with it.
    assert hosted.agent_name == "Northwind"
    assert hosted.production_project_id is None


@pytest.mark.django_db
def test_linking_refuses_a_project_outside_the_agent_s_workspace(
    auth_client, organization, workspace, user
):
    code = _code_agent(organization, workspace, "Harbor Hotels")
    missing = _link(auth_client, code, str(uuid.uuid4()))
    assert missing.status_code == status.HTTP_400_BAD_REQUEST, missing.content
    assert "not found" in missing.json()["detail"]

    sibling = Workspace.objects.create(
        name="Sibling", organization=organization, is_active=True, created_by=user
    )
    in_sibling = _observe_project(organization, sibling, "Sibling production")
    elsewhere = _observe_project(
        Organization.objects.create(name="Elsewhere"), None, "Not ours"
    )
    for project in (in_sibling, elsewhere):
        refused = _link(auth_client, code, str(project.id))
        assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.content
    code.refresh_from_db()
    assert code.production_project_id is None


def _pulling(agent, project, *, enabled=True):
    row = ObservabilityProvider.objects.create(
        project=project,
        provider=ProviderChoices.VAPI,
        enabled=enabled,
        organization=agent.organization,
        workspace=agent.workspace,
    )
    AgentDefinition.objects.filter(id=agent.id).update(observability_provider=row)


@pytest.mark.django_db
def test_the_list_names_each_agent_s_observe_project_by_kind(
    auth_client, organization, workspace
):
    code = _code_agent(organization, workspace, "Harbor Hotels")
    production = _observe_project(organization, workspace, "Harbor production")
    _link(auth_client, code, str(production.id))
    # The switch's own project for a code agent is not where its traces land.
    _pulling(code, _observe_project(organization, workspace, "Harbor Hotels"))
    unlinked = _code_agent(organization, workspace, "Page Turner Books")
    hosted = _agent(organization, workspace, "Northwind")
    pulled = _observe_project(organization, workspace, "Northwind calls")
    _pulling(hosted, pulled)
    switched_off = _agent(organization, workspace, "Clinic")
    _pulling(
        switched_off,
        _observe_project(organization, workspace, "Clinic calls"),
        enabled=False,
    )

    rows = _rows(auth_client)

    assert rows["Harbor Hotels"]["observe_project"] == {
        "id": str(production.id),
        "name": "Harbor production",
    }
    assert rows["Page Turner Books"]["observe_project"] is None
    assert rows["Northwind"]["observe_project"] == {
        "id": str(pulled.id),
        "name": "Northwind calls",
    }
    assert rows["Clinic"]["observe_project"] is None
    assert unlinked.production_project_id is None


@pytest.mark.django_db
def test_the_list_reads_observe_projects_without_a_query_per_agent(
    auth_client, organization, workspace, django_assert_max_num_queries
):
    for index in range(3):
        agent = _agent(organization, workspace, f"Hosted {index}")
        _pulling(agent, _observe_project(organization, workspace, f"Calls {index}"))
    with CaptureQueriesContext(connection) as one_more:
        _rows(auth_client)
    agent = _agent(organization, workspace, "Hosted 3")
    _pulling(agent, _observe_project(organization, workspace, "Calls 3"))

    with django_assert_max_num_queries(len(one_more.captured_queries)):
        assert len(_rows(auth_client)) == 4


def test_bulk_delete_refuses_a_tested_agent_and_deletes_nothing(
    auth_client, organization, workspace
):
    tested = _agent(organization, workspace, "tested")
    _environment(tested, 1)
    idle = _agent(organization, workspace, "idle")

    response = auth_client.delete(
        "/simulate/agent-definitions/",
        {"agent_ids": [str(idle.id), str(tested.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "tested is tested by 1 environment" in response.json()["error"]
    assert (
        AgentDefinition.objects.filter(
            id__in=[idle.id, tested.id], deleted=False
        ).count()
        == 2
    )


def test_an_agent_version_cannot_be_deleted(auth_client, organization, workspace):
    agent = _agent(organization, workspace, "kept")
    version = agent.create_version(description="p", commit_message="v1")

    response = auth_client.delete(
        f"/simulate/agent-definitions/{agent.id}/versions/{version.id}/delete/"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert AgentVersion.objects.filter(id=version.id, deleted=False).exists()


def test_overview_reports_the_environment_pin_not_the_latest_version(
    organization, workspace
):
    from simulate.services.harness_environment import _agent as overview_agent

    agent = _agent(organization, workspace, "pinned")
    first = agent.create_version(description="p", commit_message="v1", status="active")
    agent.create_version(description="p2", commit_message="v2", status="active")
    environment = _environment(agent, 1)

    assert overview_agent(environment)["active_version"] == "v2"

    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        agent_version=first
    )
    environment.refresh_from_db()

    assert overview_agent(environment)["active_version"] == "v1"
    assert overview_agent(environment)["versions_count"] == 2


def test_environment_rows_carry_their_policy(auth_client, organization, workspace):
    from simulate.services.harness_agent_versions import POLICY_KEY

    agent = _agent(organization, workspace, "policies")
    following = _environment(agent, 1)
    pinned = _environment(agent, 2)
    unchosen = _environment(agent, 3)
    for environment, policy in ((following, "follow"), (pinned, "pinned")):
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload={**environment.payload, "metadata": {POLICY_KEY: policy}}
        )

    response = auth_client.get(
        "/simulate/api/harness-environments/", {"agent_definition_id": str(agent.id)}
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    rows = {row["id"]: row["agent_policy"] for row in response.json()["results"]}
    assert rows == {
        str(following.id): "follow",
        str(pinned.id): "pinned",
        str(unchosen.id): "pinned",
    }
