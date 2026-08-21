"""Integration tests for the RL harness internal persistence API
(``simulate/views/rl_internal.py``), mounted under ``simulate/api/rl-harness/``.

Only ``InternalServiceAuthentication`` (the fleet secret bearer token) is
accepted here — never a tenant API key — so the auth matrix below is as
important as the behavior it gates.
"""

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    CallExecution,
    RLContract,
    RLEnvironment,
    RLEnvironmentMessage,
    RLScenario,
    RLWorld,
    RLWorldCopy,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services.alk_simulate_ingestion import _REPORTED_EVAL_TEMPLATE
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

RL_BASE = "/simulate/api/rl-harness"
INTERNAL_SECRET = "rl-internal-secret"


@pytest.fixture(autouse=True)
def _internal_secret(settings):
    settings.INTERNAL_API_SECRET = INTERNAL_SECRET


@pytest.fixture
def internal_client(api_client):
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {INTERNAL_SECRET}")
    return api_client


def _make_chain(organization, workspace, title="Chain Env"):
    """A full environment -> contract -> world -> scenario chain."""
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title=title
    )
    contract = RLContract.objects.create(
        organization=organization,
        environment=environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    world = RLWorld.objects.create(
        organization=organization, environment=environment, contract=contract, version=1
    )
    scenario = RLScenario.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        name=f"{title} Scenario",
    )
    return environment, contract, world, scenario


def _make_call_execution(organization, workspace, name="ALK Scenario"):
    scenario = Scenarios.objects.create(
        organization=organization, workspace=workspace, name=name, source="src"
    )
    run_test = RunTest.objects.create(
        organization=organization, workspace=workspace, name=f"{name} RT"
    )
    test_execution = TestExecution.objects.create(run_test=run_test)
    return CallExecution.objects.create(test_execution=test_execution, scenario=scenario), run_test


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


@pytest.fixture
def full_setup(db, organization, workspace):
    environment, contract, world, scenario = _make_chain(
        organization, workspace, title="Auth Matrix"
    )
    call_execution, _ = _make_call_execution(organization, workspace, name="Auth Matrix ALK")
    # READY (not the default PROVISIONING) so the call-log route's status
    # gate doesn't 409 the auth-matrix's "correct secret succeeds" case; tied
    # to call_execution so the world-copy-lookup route also finds it.
    world_copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        call_execution=call_execution,
        purpose=RLWorldCopy.Purpose.GATE,
        status=RLWorldCopy.Status.READY,
    )

    # organization=None global template: an active workspace context would
    # silently reassign it back to the current org, defeating the
    # organization__isnull lookup the verdicts endpoint depends on.
    clear_workspace_context()
    EvalTemplate.objects.create(name=_REPORTED_EVAL_TEMPLATE, organization=None, config={})

    return SimpleNamespace(
        environment=environment,
        contract=contract,
        world=world,
        scenario=scenario,
        world_copy=world_copy,
        call_execution=call_execution,
    )


def _route_table(setup):
    env = setup.environment
    return {
        "environment_create": (
            "post",
            f"{RL_BASE}/environments/",
            {
                "organization_id": str(env.organization_id),
                "workspace_id": str(env.workspace_id),
                "title": "Auth Matrix New Env",
            },
            201,
        ),
        "environment_get": ("get", f"{RL_BASE}/environments/{env.id}/", None, 200),
        "environment_patch": (
            "patch",
            f"{RL_BASE}/environments/{env.id}/",
            {"title": "Patched Title"},
            200,
        ),
        "message_create": (
            "post",
            f"{RL_BASE}/environments/{env.id}/messages/",
            {"turn_id": str(uuid.uuid4()), "role": "user"},
            201,
        ),
        "message_list": ("get", f"{RL_BASE}/environments/{env.id}/messages/", None, 200),
        "contract_create": (
            "post",
            f"{RL_BASE}/environments/{env.id}/contracts/",
            {"data": {}},
            201,
        ),
        "world_create": (
            "post",
            f"{RL_BASE}/environments/{env.id}/worlds/",
            {"contract_id": str(setup.contract.id)},
            201,
        ),
        "world_get": (
            "get",
            f"{RL_BASE}/environments/{env.id}/worlds/{setup.world.version}/",
            None,
            200,
        ),
        "world_patch": (
            "patch",
            f"{RL_BASE}/worlds/{setup.world.id}/",
            {"status": "saved"},
            200,
        ),
        "world_get_by_id": ("get", f"{RL_BASE}/worlds/{setup.world.id}/", None, 200),
        "scenario_create": (
            "post",
            f"{RL_BASE}/environments/{env.id}/scenarios/",
            {"name": "Auth Matrix Fresh Scenario", "world_id": str(setup.world.id)},
            201,
        ),
        "scenario_patch": (
            "patch",
            f"{RL_BASE}/scenarios/{setup.scenario.id}/",
            {"gate_status": "passed"},
            200,
        ),
        "scenario_get": ("get", f"{RL_BASE}/scenarios/{setup.scenario.id}/", None, 200),
        "world_copy_create": (
            "post",
            f"{RL_BASE}/world-copies/",
            {
                "environment_id": str(env.id),
                "world_id": str(setup.world.id),
                "scenario_id": str(setup.scenario.id),
                "purpose": "gate",
            },
            201,
        ),
        "world_copy_by_token": (
            "get",
            f"{RL_BASE}/world-copies/by-token/{setup.world_copy.token}/",
            None,
            200,
        ),
        "world_copy_patch": (
            "patch",
            f"{RL_BASE}/world-copies/{setup.world_copy.id}/",
            {"status": "ready"},
            200,
        ),
        "world_copy_call_log": (
            "post",
            f"{RL_BASE}/world-copies/{setup.world_copy.id}/call-log/",
            {"entries": [{"tool": "x"}]},
            200,
        ),
        "world_copy_lookup": (
            "get",
            f"{RL_BASE}/world-copies/?call_execution_id={setup.call_execution.id}",
            None,
            200,
        ),
        "rl_verdicts": (
            "post",
            f"{RL_BASE}/call-executions/{setup.call_execution.id}/rl-verdicts/",
            {"evaluations": [{"name": "Sub-goal", "passed": True}]},
            200,
        ),
    }


_ROUTE_NAMES = [
    "environment_create",
    "environment_get",
    "environment_patch",
    "message_create",
    "message_list",
    "contract_create",
    "world_create",
    "world_get",
    "world_patch",
    "world_get_by_id",
    "scenario_create",
    "scenario_patch",
    "scenario_get",
    "world_copy_create",
    "world_copy_by_token",
    "world_copy_patch",
    "world_copy_call_log",
    "world_copy_lookup",
    "rl_verdicts",
]


def _call_route(api_client, route_name, setup):
    method, url, body, expected_ok = _route_table(setup)[route_name]
    response = getattr(api_client, method)(url, body, format="json")
    return response, expected_ok


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
@pytest.mark.parametrize("route_name", _ROUTE_NAMES)
class TestAuthMatrix:
    def test_no_auth_rejected(self, api_client, route_name, full_setup):
        response, _ = _call_route(api_client, route_name, full_setup)
        assert response.status_code in (401, 403)

    def test_wrong_secret_rejected(self, api_client, route_name, full_setup):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer wrong-secret")
        response, _ = _call_route(api_client, route_name, full_setup)
        assert response.status_code in (401, 403)

    def test_org_api_key_rejected(
        self, api_client, route_name, full_setup, organization, user
    ):
        org_api_key = OrgApiKey.objects.create(
            organization=organization,
            user=user,
            type="user",
            api_key=f"rl-key-{uuid.uuid4().hex[:8]}",
            secret_key=f"rl-secret-{uuid.uuid4().hex[:8]}",
        )
        api_client.credentials(
            HTTP_X_API_KEY=org_api_key.api_key,
            HTTP_X_SECRET_KEY=org_api_key.secret_key,
        )
        response, _ = _call_route(api_client, route_name, full_setup)
        assert response.status_code not in (200, 201)

    def test_correct_secret_succeeds(self, api_client, route_name, full_setup):
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {INTERNAL_SECRET}")
        response, expected_ok = _call_route(api_client, route_name, full_setup)
        assert response.status_code == expected_ok, response.content


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_create_environment_explicit_workspace(internal_client, organization, user):
    second_workspace = Workspace.objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    response = internal_client.post(
        f"{RL_BASE}/environments/",
        {
            "organization_id": str(organization.id),
            "workspace_id": str(second_workspace.id),
            "title": "Explicit Workspace Env",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["workspace_id"] == str(second_workspace.id)

    set_workspace_context(organization=organization, workspace=second_workspace)
    try:
        assert RLEnvironment.objects.filter(id=body["id"]).exists()
    finally:
        clear_workspace_context()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_create_environment_cross_org_workspace_404(internal_client, organization, workspace):
    foreign_org = Organization.objects.create(name="Foreign Org — Cross Workspace")
    foreign_user = User.objects.create_user(
        email=f"foreign-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Foreign User",
        organization=foreign_org,
    )
    foreign_workspace = Workspace.objects.create(
        name="Foreign Workspace",
        organization=foreign_org,
        is_default=True,
        is_active=True,
        created_by=foreign_user,
    )
    response = internal_client.post(
        f"{RL_BASE}/environments/",
        {
            "organization_id": str(organization.id),
            "workspace_id": str(foreign_workspace.id),
            "title": "Should Not Exist",
        },
        format="json",
    )
    assert response.status_code == 404
    assert not RLEnvironment.no_workspace_objects.filter(title="Should Not Exist").exists()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_create_environment_foreign_agent_definition_404(internal_client, organization, workspace):
    foreign_org = Organization.objects.create(name="Foreign Org — Agent Definition")
    foreign_agent = AgentDefinition.objects.create(
        agent_name="Foreign Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=foreign_org,
    )
    response = internal_client.post(
        f"{RL_BASE}/environments/",
        {
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "title": "Should Not Exist Either",
            "agent_definition_id": str(foreign_agent.id),
        },
        format="json",
    )
    assert response.status_code == 404
    assert not RLEnvironment.no_workspace_objects.filter(
        title="Should Not Exist Either"
    ).exists()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_patch_environment_fields_and_empty_body_400(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Original Title"
    )
    response = internal_client.patch(
        f"{RL_BASE}/environments/{environment.id}/",
        {"title": "New Title", "status": "working"},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["title"] == "New Title"
    assert body["status"] == "working"

    empty = internal_client.patch(
        f"{RL_BASE}/environments/{environment.id}/", {}, format="json"
    )
    assert empty.status_code == 400


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_message_append_idempotent(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Message Env"
    )
    turn_id = str(uuid.uuid4())

    first = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/messages/",
        {"turn_id": turn_id, "role": "user", "text": "hi"},
        format="json",
    )
    assert first.status_code == 201, first.content
    first_body = first.json()

    retry = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/messages/",
        {"turn_id": turn_id, "role": "user", "text": "ignored on retry"},
        format="json",
    )
    assert retry.status_code == 200, retry.content
    assert retry.json()["id"] == first_body["id"]
    assert retry.json()["seq"] == first_body["seq"]
    assert RLEnvironmentMessage.objects.filter(environment=environment).count() == 1

    next_turn = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/messages/",
        {"turn_id": str(uuid.uuid4()), "role": "assistant"},
        format="json",
    )
    assert next_turn.status_code == 201, next_turn.content
    assert next_turn.json()["seq"] == first_body["seq"] + 1


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_message_history_pagination(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Pagination Env"
    )
    for _ in range(5):
        internal_client.post(
            f"{RL_BASE}/environments/{environment.id}/messages/",
            {"turn_id": str(uuid.uuid4()), "role": "user"},
            format="json",
        )

    response = internal_client.get(
        f"{RL_BASE}/environments/{environment.id}/messages/?after_seq=1&limit=2"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["count"] == 2
    seqs = [message["seq"] for message in body["messages"]]
    assert seqs == sorted(seqs)
    assert min(seqs) > 1


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_contract_versioning_supersedes(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Contract Env"
    )
    first = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/contracts/",
        {"data": {"v": 1}},
        format="json",
    )
    assert first.status_code == 201, first.content
    assert first.json() == {"id": first.json()["id"], "version": 1, "status": "active"}

    second = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/contracts/",
        {"data": {"v": 2}},
        format="json",
    )
    assert second.status_code == 201, second.content
    assert second.json()["version"] == 2
    assert second.json()["status"] == "active"

    contracts = list(
        RLContract.objects.filter(environment=environment).order_by("version")
    )
    assert [c.status for c in contracts] == [
        RLContract.Status.SUPERSEDED,
        RLContract.Status.ACTIVE,
    ]
    assert (
        RLContract.objects.filter(
            environment=environment, status=RLContract.Status.ACTIVE
        ).count()
        == 1
    )


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_world_save_marks_scenarios_stale(internal_client, organization, workspace):
    environment, contract, world_v1, _ = _make_chain(
        organization, workspace, title="Stale World Env"
    )
    passed_scenario = RLScenario.objects.create(
        organization=organization,
        environment=environment,
        world=world_v1,
        name="Passed Scenario",
        gate_status=RLScenario.GateStatus.PASSED,
    )
    unproven_scenario = RLScenario.objects.create(
        organization=organization,
        environment=environment,
        world=world_v1,
        name="Unproven Scenario",
    )

    response = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {"contract_id": str(contract.id)},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["version"] == 2
    assert body["scenarios_marked_stale"] == 1

    passed_scenario.refresh_from_db()
    unproven_scenario.refresh_from_db()
    assert passed_scenario.gate_status == RLScenario.GateStatus.STALE
    assert unproven_scenario.gate_status == RLScenario.GateStatus.UNPROVEN


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_world_version_increments_and_get_roundtrip(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Version Env"
    )
    contract = RLContract.objects.create(
        organization=organization,
        environment=environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    snapshot = {"rows": {"users": [{"id": 1}]}, "counters": {"users": 1}}

    first = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {"contract_id": str(contract.id), "snapshot": snapshot},
        format="json",
    )
    assert first.status_code == 201, first.content
    assert first.json()["version"] == 1

    second = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {"contract_id": str(contract.id)},
        format="json",
    )
    assert second.status_code == 201, second.content
    assert second.json()["version"] == 2

    got = internal_client.get(f"{RL_BASE}/environments/{environment.id}/worlds/1/")
    assert got.status_code == 200, got.content
    body = got.json()
    assert body["snapshot"] == snapshot
    assert body["contract_version"] == 1


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_world_get_by_id(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="World By Id Env"
    )
    contract = RLContract.objects.create(
        organization=organization,
        environment=environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    snapshot = {"rows": {"users": [{"id": 1}]}, "counters": {"users": 1}}
    handlers = {"on_start": "def on_start(): ..."}
    schema_scripts = ["CREATE TABLE users (id int)"]
    world_checks = {"holds": "def check(world):\n    return None"}

    with_checks = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {
            "contract_id": str(contract.id),
            "snapshot": snapshot,
            "handlers": handlers,
            "schema_scripts": schema_scripts,
            "world_checks": world_checks,
        },
        format="json",
    )
    assert with_checks.status_code == 201, with_checks.content
    world_id = with_checks.json()["id"]

    response = internal_client.get(f"{RL_BASE}/worlds/{world_id}/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body.keys()) == {
        "id",
        "environment_id",
        "contract_id",
        "contract_version",
        "version",
        "status",
        "store_kind",
        "schema_scripts",
        "snapshot",
        "state",
        "handlers",
        "tool_specs",
        "world_checks",
        "refusal_signature",
        "master_db_name",
        "master_materialized_at",
        "created_at",
        "updated_at",
    }
    assert body["id"] == world_id
    assert body["snapshot"] == snapshot
    assert body["handlers"] == handlers
    assert body["schema_scripts"] == schema_scripts
    assert body["contract_version"] == contract.version
    assert body["world_checks"] == world_checks

    without_checks = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {"contract_id": str(contract.id)},
        format="json",
    )
    assert without_checks.status_code == 201, without_checks.content
    world_id_no_checks = without_checks.json()["id"]

    response_no_checks = internal_client.get(f"{RL_BASE}/worlds/{world_id_no_checks}/")
    assert response_no_checks.status_code == 200, response_no_checks.content
    assert response_no_checks.json()["world_checks"] == {}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_world_create_rejects_sqlite(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Sqlite Env"
    )
    contract = RLContract.objects.create(
        organization=organization,
        environment=environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    response = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {"contract_id": str(contract.id), "store_kind": "sqlite"},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "sqlite" in str(response.json()).lower()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_world_contract_must_belong_to_environment_404(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Owner Env"
    )
    other_environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Other Env"
    )
    foreign_contract = RLContract.objects.create(
        organization=organization,
        environment=other_environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    response = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/worlds/",
        {"contract_id": str(foreign_contract.id)},
        format="json",
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_scenario_upsert_resets_gate(internal_client, organization, workspace):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Scenario Env"
    )
    contract = RLContract.objects.create(
        organization=organization,
        environment=environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    world = RLWorld.objects.create(
        organization=organization, environment=environment, contract=contract, version=1
    )

    created = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/scenarios/",
        {"name": "Refund Flow", "world_id": str(world.id), "checks": {"cart.count": 1}},
        format="json",
    )
    assert created.status_code == 201, created.content
    scenario_id = created.json()["id"]

    RLScenario.objects.filter(id=scenario_id).update(
        gate_status=RLScenario.GateStatus.PASSED,
        gate_results={"ok": True},
        proved_at=timezone.now(),
    )

    upserted = internal_client.post(
        f"{RL_BASE}/environments/{environment.id}/scenarios/",
        {
            "name": "Refund Flow",
            "world_id": str(world.id),
            "instruction": "Updated instruction",
        },
        format="json",
    )
    assert upserted.status_code == 200, upserted.content
    body = upserted.json()
    assert body["id"] == scenario_id
    assert body["gate_status"] == "unproven"
    assert body["gate_results"] == {}
    assert body["proved_at"] is None
    assert body["instruction"] == "Updated instruction"
    # Fields the upsert didn't mention are preserved from the create call.
    assert body["checks"] == {"cart.count": 1}
    assert (
        RLScenario.objects.filter(environment=environment, name="Refund Flow").count() == 1
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_scenario_patch_gate_verdict(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Patch Gate Env"
    )
    proved_at = timezone.now().replace(microsecond=0)

    response = internal_client.patch(
        f"{RL_BASE}/scenarios/{scenario.id}/",
        {
            "gate_status": "passed",
            "gate_results": {"turns": 3},
            "proved_at": proved_at.isoformat(),
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    scenario.refresh_from_db()
    assert scenario.gate_status == RLScenario.GateStatus.PASSED
    assert scenario.gate_results == {"turns": 3}
    assert scenario.proved_at == proved_at


# ---------------------------------------------------------------------------
# World copies
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_copy_create_idempotent_by_call_execution(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Copy Idempotent Env"
    )
    call_execution, _ = _make_call_execution(organization, workspace, name="Copy Idempotent ALK")

    body = {
        "environment_id": str(environment.id),
        "world_id": str(world.id),
        "scenario_id": str(scenario.id),
        "purpose": "gate",
        "call_execution_id": str(call_execution.id),
    }
    first = internal_client.post(f"{RL_BASE}/world-copies/", body, format="json")
    assert first.status_code == 201, first.content

    second = internal_client.post(f"{RL_BASE}/world-copies/", body, format="json")
    assert second.status_code == 200, second.content
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["token"] == first.json()["token"]
    assert RLWorldCopy.objects.filter(call_execution=call_execution).count() == 1


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_copy_create_integrity_error_recovers_existing_row(
    internal_client, organization, workspace
):
    """A genuine create-vs-create race can't be reproduced synchronously, so
    the idempotency pre-check is patched to miss (simulating "another prepare
    won the race between our read and our write") while a row for this
    call_execution already exists in the DB. The create must then hit
    uniq_rl_world_copy_call_exec, recover via the except-branch, and return
    the winner's row rather than surfacing the constraint violation."""
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Integrity Race Env"
    )
    call_execution, _ = _make_call_execution(
        organization, workspace, name="Integrity Race ALK"
    )
    existing_copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        call_execution=call_execution,
        purpose=RLWorldCopy.Purpose.GATE,
    )

    with mock.patch.object(
        RLWorldCopy.no_workspace_objects,
        "filter",
        return_value=RLWorldCopy.no_workspace_objects.none(),
    ):
        response = internal_client.post(
            f"{RL_BASE}/world-copies/",
            {
                "environment_id": str(environment.id),
                "world_id": str(world.id),
                "scenario_id": str(scenario.id),
                "purpose": "gate",
                "call_execution_id": str(call_execution.id),
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    assert response.json()["id"] == str(existing_copy.id)
    assert response.json()["token"] == str(existing_copy.token)
    assert RLWorldCopy.objects.filter(call_execution=call_execution).count() == 1


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_copy_lookup_by_call_execution(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Copy Lookup Env"
    )
    call_execution, _ = _make_call_execution(organization, workspace, name="Copy Lookup ALK")
    copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        call_execution=call_execution,
        purpose=RLWorldCopy.Purpose.GATE,
    )

    found = internal_client.get(
        f"{RL_BASE}/world-copies/?call_execution_id={call_execution.id}"
    )
    assert found.status_code == 200, found.content
    assert found.json()["id"] == str(copy.id)

    missing_param = internal_client.get(f"{RL_BASE}/world-copies/")
    assert missing_param.status_code == 400

    invalid_uuid = internal_client.get(
        f"{RL_BASE}/world-copies/?call_execution_id=not-a-uuid"
    )
    assert invalid_uuid.status_code == 400

    unknown = internal_client.get(
        f"{RL_BASE}/world-copies/?call_execution_id={uuid.uuid4()}"
    )
    assert unknown.status_code == 404


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_copy_coherence_404s(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Copy Coherence Env"
    )
    _, _, _, foreign_scenario = _make_chain(
        organization, workspace, title="Copy Coherence Foreign Env"
    )

    wrong_scenario = internal_client.post(
        f"{RL_BASE}/world-copies/",
        {
            "environment_id": str(environment.id),
            "world_id": str(world.id),
            "scenario_id": str(foreign_scenario.id),
            "purpose": "gate",
        },
        format="json",
    )
    assert wrong_scenario.status_code == 404

    foreign_org = Organization.objects.create(name="Foreign Org — Run Test")
    foreign_run_test = RunTest.objects.create(organization=foreign_org, name="Foreign RT")
    wrong_run_test = internal_client.post(
        f"{RL_BASE}/world-copies/",
        {
            "environment_id": str(environment.id),
            "world_id": str(world.id),
            "scenario_id": str(scenario.id),
            "purpose": "gate",
            "run_test_id": str(foreign_run_test.id),
        },
        format="json",
    )
    assert wrong_run_test.status_code == 404


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_copy_call_log_appends_under_retry(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Call Log Env"
    )
    copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        purpose=RLWorldCopy.Purpose.GATE,
        status=RLWorldCopy.Status.READY,
    )

    first = internal_client.post(
        f"{RL_BASE}/world-copies/{copy.id}/call-log/",
        {"entries": [{"tool": "a"}]},
        format="json",
    )
    assert first.status_code == 200, first.content
    assert first.json()["count"] == 1

    second = internal_client.post(
        f"{RL_BASE}/world-copies/{copy.id}/call-log/",
        {"entries": [{"tool": "b"}]},
        format="json",
    )
    assert second.status_code == 200, second.content
    assert second.json()["count"] == 2

    copy.refresh_from_db()
    assert [entry["tool"] for entry in copy.call_log] == ["a", "b"]

    empty = internal_client.post(
        f"{RL_BASE}/world-copies/{copy.id}/call-log/", {"entries": []}, format="json"
    )
    assert empty.status_code == 400


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_call_log_fenced_after_grading(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Fenced Call Log Env"
    )
    copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        purpose=RLWorldCopy.Purpose.GATE,
        status=RLWorldCopy.Status.GRADED,
    )

    response = internal_client.post(
        f"{RL_BASE}/world-copies/{copy.id}/call-log/",
        {"entries": [{"tool": "late"}]},
        format="json",
    )
    assert response.status_code == 409, response.content
    assert response.json()["error"] == "copy is graded"

    copy.refresh_from_db()
    assert copy.call_log == []


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_call_log_carries_state(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Call Log State Env"
    )
    copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        purpose=RLWorldCopy.Purpose.GATE,
        status=RLWorldCopy.Status.IN_CALL,
    )

    response = internal_client.post(
        f"{RL_BASE}/world-copies/{copy.id}/call-log/",
        {"entries": [{"tool": "a"}], "state": {"cart.count": 2}},
        format="json",
    )
    assert response.status_code == 200, response.content

    by_token = internal_client.get(f"{RL_BASE}/world-copies/by-token/{copy.token}/")
    assert by_token.status_code == 200, by_token.content
    body = by_token.json()
    assert body["state"] == {"cart.count": 2}
    assert [entry["tool"] for entry in body["call_log"]] == ["a"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_terminal_status_patch_409(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="Terminal Status Env"
    )
    copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        purpose=RLWorldCopy.Purpose.GATE,
        status=RLWorldCopy.Status.GRADED,
    )

    response = internal_client.patch(
        f"{RL_BASE}/world-copies/{copy.id}/",
        {"status": "dropped"},
        format="json",
    )
    assert response.status_code == 409, response.content
    body = response.json()
    assert body["error"] == "copy is graded"
    assert body["status"] == "graded"
    copy.refresh_from_db()
    assert copy.status == RLWorldCopy.Status.GRADED

    # Only the "status" key triggers the fence; other bookkeeping fields on a
    # terminal row still write.
    bookkeeping = internal_client.patch(
        f"{RL_BASE}/world-copies/{copy.id}/",
        {"error": "late grading note"},
        format="json",
    )
    assert bookkeeping.status_code == 200, bookkeeping.content
    copy.refresh_from_db()
    assert copy.error == "late grading note"
    assert copy.status == RLWorldCopy.Status.GRADED


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_copy_by_token_and_unknown_token_404(internal_client, organization, workspace):
    environment, _, world, scenario = _make_chain(
        organization, workspace, title="By Token Env"
    )
    copy = RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        purpose=RLWorldCopy.Purpose.GATE,
    )

    response = internal_client.get(f"{RL_BASE}/world-copies/by-token/{copy.token}/")
    assert response.status_code == 200, response.content
    assert response.json()["id"] == str(copy.id)

    unknown = internal_client.get(f"{RL_BASE}/world-copies/by-token/{uuid.uuid4()}/")
    assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_rl_verdicts_endpoint(internal_client, organization, workspace):
    clear_workspace_context()
    EvalTemplate.objects.create(name=_REPORTED_EVAL_TEMPLATE, organization=None, config={})

    call_execution, run_test = _make_call_execution(
        organization, workspace, name="Verdicts ALK"
    )
    body = {
        "evaluations": [
            {"name": "Sub-goal A", "passed": True, "reason": "matched"},
            {"name": "Sub-goal B", "score": 0.8},
        ]
    }

    response = internal_client.post(
        f"{RL_BASE}/call-executions/{call_execution.id}/rl-verdicts/", body, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["stored"] == 2

    call_execution.refresh_from_db()
    assert len(call_execution.eval_outputs or {}) == 2
    assert SimulateEvalConfig.objects.filter(run_test=run_test).count() == 2

    again = internal_client.post(
        f"{RL_BASE}/call-executions/{call_execution.id}/rl-verdicts/", body, format="json"
    )
    assert again.status_code == 200, again.content
    assert SimulateEvalConfig.objects.filter(run_test=run_test).count() == 2


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_rl_verdicts_missing_template_500(internal_client, organization, workspace):
    call_execution, _ = _make_call_execution(
        organization, workspace, name="No Template ALK"
    )
    response = internal_client.post(
        f"{RL_BASE}/call-executions/{call_execution.id}/rl-verdicts/",
        {"evaluations": [{"name": "X", "passed": True}]},
        format="json",
    )
    assert response.status_code == 500
    assert _REPORTED_EVAL_TEMPLATE in response.json()["error"]
