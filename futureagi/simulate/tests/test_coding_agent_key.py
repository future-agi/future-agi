"""A coding agent's API key reaches only its fix loop's calls, on one environment.

The key runs on the developer's machine beside the agent being fixed, so every
other route, every other environment and every run the user never sees is closed
to it.
"""

import base64
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, transaction
from django.urls import resolve, reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied

from accounts.authentication import LangfuseBasicAuthentication, is_coding_agent_key
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace
from simulate.authentication import CODING_AGENT_ROUTES, refuse_coding_agent_request
from simulate.models import (
    AgentDefinition,
    AgentFixRun,
    CallExecution,
    HarnessSuiteVersion,
    HostedHarnessJob,
    RunTest,
    Scenarios,
    TestExecution,
)

MINT_URL = "/accounts/key/generate_secret_key/"
# Named by C's held-back verify, which lands after this key.
RESERVED = {
    "simulate:harness-environment-verify",
    "simulate:harness-environment-verify-detail",
}


def _job(workspace, metadata=None, **fields):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"source": {}, "metadata": metadata or {}},
        **fields,
    )


def _run(environment, key=None, **fields):
    """A Run of ``environment`` with one execution holding one call, started by
    ``key`` when one is given (the stamp the verify branch writes)."""
    workspace = environment.workspace
    run_test = RunTest.no_workspace_objects.create(
        name="run", organization=workspace.organization, workspace=workspace
    )
    execution = TestExecution.objects.create(run_test=run_test)
    scenario = Scenarios.objects.create(
        name="Book a ride",
        description="",
        source="",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=workspace.organization,
        workspace=workspace,
    )
    call = CallExecution.objects.create(test_execution=execution, scenario=scenario)
    job = _job(
        workspace,
        metadata={"coding_agent_key_id": str(key.id)} if key else None,
        environment=environment,
        run_test=run_test,
        test_execution=execution,
        **fields,
    )
    return SimpleNamespace(job=job, execution=execution, call=call)


def _versioned(environment):
    """``environment`` with a suite version, which a coding agent key needs."""
    suite = HarnessSuiteVersion.no_workspace_objects.create(
        job=environment,
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        version_number=1,
        status="active",
        origin="manual",
        hash="sha256:" + uuid.uuid4().hex * 2,
        archive_object_key="harness-suites/x.tar.gz",
        archive_sha256="",
        archive_size=0,
    )
    environment.suite_version = suite
    environment.save(update_fields=["suite_version", "updated_at"])
    return environment


@pytest.fixture
def environment(db, workspace):
    return _versioned(_job(workspace))


@pytest.fixture
def other_environment(db, workspace):
    return _versioned(_job(workspace))


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def run(environment, coding_key):
    return _run(environment, coding_key)


@pytest.fixture
def human_run(environment):
    return _run(environment)


@pytest.fixture
def check_run(environment, coding_key, user, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=workspace.organization,
        workspace=workspace,
    )
    fix_run = AgentFixRun.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        environment=environment,
        base_agent_version=agent.create_version(
            description="", commit_message="first", status="active"
        ),
        request={},
    )
    # Stamped too, so its 404 comes from being hidden, not from the stamp.
    return _run(environment, coding_key, fix_run=fix_run)


@pytest.fixture
def coding_key(environment, user, workspace):
    return OrgApiKey.no_workspace_objects.create(
        name="coding agent",
        organization=workspace.organization,
        workspace=workspace,
        type="coding_agent",
        minted_by=user,
        simulation_environment_id=environment.id,
    )


@pytest.fixture
def user_key(user, workspace):
    return OrgApiKey.no_workspace_objects.create(
        name="person",
        organization=workspace.organization,
        workspace=workspace,
        type="user",
        user=user,
    )


@pytest.fixture(autouse=True)
def _hosted(settings):
    settings.HARNESS_PROVIDER = "hosted"


def _get(client, key, path):
    return client.get(
        path, HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key
    )


def _code(response):
    return response.json().get("code")


def _path(route, run, environment):
    view_name = route[0]
    target = CODING_AGENT_ROUTES[route]
    if target is None:
        return reverse(view_name)
    if target.run_lookup is None:
        ids = {"pk": environment.id}
        if view_name == "simulate:harness-environment-suite-members":
            ids["version_id"] = uuid.uuid4()
        return reverse(view_name, kwargs=ids)
    ids = {"pk": run.job.id, "test_execution_id": run.execution.id}
    ids["call_execution_id"] = run.call.id
    return reverse(view_name, kwargs={target.kwarg: ids[target.kwarg]})


def _request(view_name, method, path=None, **kwargs):
    match = resolve(path) if path else SimpleNamespace(view_name=view_name, **kwargs)
    return SimpleNamespace(resolver_match=match, method=method)


@pytest.mark.integration
@pytest.mark.django_db
class TestMinting:
    def test_a_coding_agent_key_names_an_environment_the_caller_sees(
        self, auth_client, environment, workspace, user
    ):
        response = auth_client.post(
            MINT_URL,
            {
                "key_name": "fixer",
                "type": "coding_agent",
                "simulation_environment_id": str(environment.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        key = OrgApiKey.no_workspace_objects.get(id=response.json()["result"]["key_id"])
        assert key.type == "coding_agent"
        assert key.simulation_environment_id == environment.id
        assert key.workspace_id == workspace.id
        # It acts as its minter but names no user, so an older build refuses it.
        assert (key.user_id, key.minted_by_id) == (None, user.id)

    @pytest.mark.parametrize(
        "body",
        [
            {"type": "coding_agent"},
            {"simulation_environment_id": str(uuid.uuid4())},
            {"type": "ingest"},
        ],
        ids=["no_environment", "environment_on_a_user_key", "other_type"],
    )
    def test_invalid_pairings_are_refused(self, auth_client, body):
        response = auth_client.post(
            MINT_URL, {"key_name": "fixer", **body}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not OrgApiKey.no_workspace_objects.filter(name="fixer").exists()

    def test_an_unknown_environment_is_refused(self, auth_client, run):
        # A Run is a job, not an environment.
        for environment_id in (uuid.uuid4(), run.job.id):
            response = auth_client.post(
                MINT_URL,
                {
                    "key_name": "fixer",
                    "type": "coding_agent",
                    "simulation_environment_id": str(environment_id),
                },
                format="json",
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not OrgApiKey.no_workspace_objects.filter(name="fixer").exists()

    @pytest.mark.parametrize("why", ["no_suite_version", "versioning_off"])
    def test_an_environment_with_nothing_to_hold_back_is_refused(
        self, auth_client, workspace, settings, why
    ):
        environment = _job(workspace)
        if why == "versioning_off":
            _versioned(environment)
            settings.HARNESS_VERSIONING_ENABLED = False

        response = auth_client.post(
            MINT_URL,
            {
                "key_name": "fixer",
                "type": "coding_agent",
                "simulation_environment_id": str(environment.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT, response.content
        assert _code(response) == "coding_agent_needs_suite_version"
        assert not OrgApiKey.no_workspace_objects.filter(name="fixer").exists()

    def test_a_plain_mint_still_makes_a_user_key(self, auth_client, user):
        response = auth_client.post(MINT_URL, {"key_name": "person"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        key = OrgApiKey.no_workspace_objects.get(id=response.json()["result"]["key_id"])
        assert (key.type, key.simulation_environment_id) == ("user", None)
        assert (key.user_id, key.minted_by_id) == (user.id, None)

    @pytest.mark.parametrize(
        "fields",
        [
            {"minted_by": "user"},
            {"minted_by": "user", "user": "user", "environment": True},
            {"environment": True},
        ],
        ids=["no_environment", "a_user", "no_minter"],
    )
    def test_the_database_refuses_a_malformed_coding_agent_key(
        self, user, workspace, environment, fields
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            OrgApiKey.no_workspace_objects.create(
                organization=workspace.organization,
                type="coding_agent",
                user=user if fields.get("user") else None,
                minted_by=user if fields.get("minted_by") else None,
                simulation_environment_id=(
                    environment.id if fields.get("environment") else None
                ),
            )


@pytest.mark.integration
@pytest.mark.django_db
class TestRevocation:
    """A coding agent key is bound to its environment's workspace, but a member
    finds, disables and deletes it from any workspace of the organization."""

    @pytest.fixture
    def elsewhere(self, coding_key, user, workspace):
        other = Workspace.no_workspace_objects.create(
            name="Elsewhere",
            organization=workspace.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        OrgApiKey.no_workspace_objects.filter(id=coding_key.id).update(workspace=other)
        return coding_key

    def test_it_is_listed_disabled_and_deleted_from_the_default_workspace(
        self, auth_client, elsewhere, user
    ):
        listed = auth_client.get("/accounts/key/get_secret_keys/")
        disabled = auth_client.post(
            "/accounts/key/disable_key/", {"key_id": str(elsewhere.id)}, format="json"
        )
        deleted = auth_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": str(elsewhere.id)},
            format="json",
        )

        rows = {row["id"]: row for row in listed.json()["result"]["table"]}
        assert str(elsewhere.id) in rows, listed.content
        assert rows[str(elsewhere.id)]["created_by"] == user.name
        assert disabled.status_code == status.HTTP_200_OK, disabled.content
        assert deleted.status_code == status.HTTP_200_OK, deleted.content
        elsewhere.refresh_from_db()
        assert elsewhere.deleted and not elsewhere.enabled


@pytest.mark.integration
@pytest.mark.django_db
class TestRoutes:
    @pytest.mark.parametrize(
        "path",
        [
            "/simulate/scenarios/",
            "/accounts/user-info/",
            "/accounts/key/get_secret_keys/",
        ],
    )
    def test_routes_off_the_list_are_refused(
        self, api_client, coding_key, user_key, path
    ):
        refused = _get(api_client, coding_key, path)

        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert _code(refused) == "coding_agent_route_refused"
        assert _get(api_client, user_key, path).status_code == status.HTTP_200_OK

    def test_a_listed_route_with_another_method_is_refused(
        self, api_client, coding_key, environment
    ):
        path = f"/simulate/api/harness-environments/{environment.id}/"
        response = api_client.delete(
            path,
            HTTP_X_API_KEY=coding_key.api_key,
            HTTP_X_SECRET_KEY=coding_key.secret_key,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _code(response) == "coding_agent_route_refused"
        assert HostedHarnessJob.no_workspace_objects.filter(id=environment.id).exists()

    def test_another_environment_is_refused(
        self, api_client, coding_key, user_key, other_environment
    ):
        other_run = _run(other_environment, coding_key)
        path = f"/simulate/api/harness-environments/{other_environment.id}/"

        refused = _get(api_client, coding_key, path)
        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert _code(refused) == "coding_agent_environment_refused"
        assert _get(api_client, user_key, path).status_code == status.HTTP_200_OK
        # A Run of another environment reads as missing, as a hidden one does.
        run_path = f"/simulate/api/harness-jobs/{other_run.job.id}/"
        assert _get(api_client, coding_key, run_path).status_code == 404

    def test_its_own_environment_and_run_are_served(
        self, api_client, coding_key, environment, run
    ):
        for path in (
            f"/simulate/api/harness-environments/{environment.id}/",
            f"/simulate/api/harness-jobs/{run.job.id}/",
            f"/simulate/test-executions/{run.execution.id}/",
        ):
            assert _get(api_client, coding_key, path).status_code == 200, path

    @pytest.mark.parametrize("why", ["no_suite_version", "versioning_off"])
    def test_every_call_is_refused_when_nothing_can_be_held_back(
        self, api_client, coding_key, user_key, environment, run, settings, why
    ):
        if why == "versioning_off":
            settings.HARNESS_VERSIONING_ENABLED = False
        else:
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                suite_version=None
            )

        for path in (
            f"/simulate/api/harness-environments/{environment.id}/",
            f"/simulate/api/harness-jobs/{run.job.id}/",
            f"/simulate/test-executions/{run.execution.id}/",
        ):
            refused = _get(api_client, coding_key, path)
            assert refused.status_code == status.HTTP_409_CONFLICT, path
            assert _code(refused) == "coding_agent_needs_suite_version", path
        person = f"/simulate/api/harness-environments/{environment.id}/"
        assert _get(api_client, user_key, person).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("which", ["check_run", "human_run"])
    def test_runs_it_did_not_start_or_never_sees_read_as_missing(
        self, request, api_client, coding_key, user_key, which
    ):
        # A person's run of the same environment can include held-back cases.
        hidden = request.getfixturevalue(which)
        for path in (
            f"/simulate/api/harness-jobs/{hidden.job.id}/",
            f"/simulate/test-executions/{hidden.execution.id}/",
            f"/simulate/v3/test-executions/{hidden.execution.id}/calls/",
            f"/simulate/v3/call-executions/{hidden.call.id}/",
        ):
            response = _get(api_client, coding_key, path)
            assert response.status_code == status.HTTP_404_NOT_FOUND, path

        job_path = f"/simulate/api/harness-jobs/{hidden.job.id}/"
        assert _get(api_client, user_key, job_path).status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.django_db
class TestRouteTable:
    @pytest.mark.parametrize(
        "route", sorted(CODING_AGENT_ROUTES), ids=lambda route: route[0]
    )
    def test_each_listed_route_is_real_and_open_on_its_environment(
        self, route, coding_key, environment, other_environment, run, check_run
    ):
        view_name, method = route
        if view_name in RESERVED:
            request = _request(view_name, method, kwargs={"pk": environment.id})
        else:
            path = _path(route, run, environment)
            request = _request(view_name, method, path)
            callback = request.resolver_match.func
            handlers = getattr(callback, "actions", None) or {
                verb: verb for verb in ("get", "post") if hasattr(callback.cls, verb)
            }
            assert method.lower() in handlers

        refuse_coding_agent_request(request, coding_key)

        target = CODING_AGENT_ROUTES[route]
        if target is None:
            return
        elsewhere = SimpleNamespace(
            id=coding_key.id, simulation_environment_id=other_environment.id
        )
        with pytest.raises((PermissionDenied, NotFound)):
            refuse_coding_agent_request(request, elsewhere)
        if target.run_lookup is not None:
            hidden = {
                "id": check_run.job.id,
                "test_execution_id": check_run.execution.id,
                "test_execution__calls__id": check_run.call.id,
            }[target.run_lookup]
            request.resolver_match.kwargs[target.kwarg] = hidden
            with pytest.raises(NotFound):
                refuse_coding_agent_request(request, coding_key)

    def test_an_unresolved_request_is_refused(self, coding_key):
        with pytest.raises(PermissionDenied):
            refuse_coding_agent_request(SimpleNamespace(method="GET"), coding_key)


@pytest.mark.integration
@pytest.mark.django_db
def test_the_other_key_pair_doors_are_closed_to_it(coding_key):
    from mcp_server import mcp_app

    encoded = base64.b64encode(
        f"{coding_key.api_key}:{coding_key.secret_key}".encode()
    ).decode("ascii")
    request = MagicMock(spec=[])
    request.method = "GET"
    request.path = "/api/public/health"
    request.headers = {}
    request.GET = {}
    request.META = {"HTTP_AUTHORIZATION": f"Basic {encoded}"}

    with pytest.raises(PermissionDenied):
        LangfuseBasicAuthentication().authenticate(request)
    assert (
        mcp_app._authenticate_and_set_context(coding_key.api_key, coding_key.secret_key)
        is None
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_is_coding_agent_key(coding_key, user_key):
    assert is_coding_agent_key(SimpleNamespace(org_api_key=coding_key))
    assert not is_coding_agent_key(SimpleNamespace(org_api_key=user_key))
    assert not is_coding_agent_key(SimpleNamespace())
