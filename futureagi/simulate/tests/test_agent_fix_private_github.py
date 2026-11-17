"""Fix my agent on an agent whose source is a private GitHub repository this deployment
no longer reads through the platform GitHub App.

A fix first reads the agent's source as the failed run ran it, so such a fix is refused
when it would start: the launch form says why before Start, and a start or a retry is
answered at once with the reason, before anything is created or scheduled. A fix the
engine ends on the same refusal says so in the same words. Public repositories, uploaded
sources and deployments that still read private repositories start a fix as before.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simulate.models import AgentFixRun, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import (
    CheckFailure,
    Ending,
    EngineReport,
    FailureOwner,
)
from simulate.services import agent_fix_engine as engine
from simulate.services import github_app_tenancy
from simulate.services.hosted_harness import hash_secret
from simulate.tests.test_agent_fix_services import KEYS, _fix_run
from simulate.tests.test_agent_fix_services import (
    any_suite_may_launch as any_suite_may_launch,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/agent-fix-runs"
REFUSED = "github_private_unavailable"
ENVIRONMENT_SENTENCE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Upload the code to keep using this environment."
)
# The words the repository read itself refuses with, written for a new environment.
CREATE_PANEL_SENTENCE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Use a public repository or upload the code."
)
UPLOADED_ARCHIVE_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def entitled():
    with patch("simulate.views.agent_fix.check_ee_feature") as gate:
        yield gate


def _platform_app(settings, monkeypatch, organizations):
    """The platform GitHub App is configured on a deployment with ``organizations``."""
    settings.CLOUD_DEPLOYMENT = ""
    settings.GITHUB_APP_ID = "12345"
    settings.GITHUB_APP_PRIVATE_KEY = "synthetic-app-private-key"
    monkeypatch.setattr(github_app_tenancy, "_live_org_ids", lambda: organizations)


@pytest.fixture
def private_github_refused(settings, monkeypatch):
    """The platform App serves two organizations: private repositories are refused."""
    _platform_app(settings, monkeypatch, ["org-a", "org-b"])


@pytest.fixture
def private_github_allowed(settings, monkeypatch):
    """One organization configured its own App, so private repositories are read."""
    _platform_app(settings, monkeypatch, ["org-a"])


def _ran_from(run, **source):
    """Record ``source`` as the source the run ran, the one a fix from it reads."""
    payload = dict(run.payload)
    payload["source"] = source
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)
    run.refresh_from_db()
    return run


def _private_repository(run):
    return _ran_from(
        run,
        kind="github",
        repository="acme/agent",
        ref="main",
        visibility="private",
        installation_id="42",
    )


def _public_repository(run):
    return _ran_from(
        run, kind="github", repository="acme/agent", ref="main", visibility="public"
    )


def _launch_body(run):
    return {
        "source_run_id": str(run.test_execution_id),
        "target_scenario_keys": [KEYS[0]],
        "surfaces": ["prompt"],
        "budget_credits": 500,
        "max_minutes": 30,
    }


def _launch_options(client, run):
    response = client.get(
        f"{BASE}/launch-options/", {"source_run_id": str(run.test_execution_id)}
    )
    assert response.status_code == 200, response.content
    return response.json()


def _start(client, run, capture):
    with capture(execute=True):
        return client.post(f"{BASE}/", _launch_body(run), format="json")


def _refusal_of(response):
    body = response.json()
    return response.status_code, body.get("error"), body.get("message")


class TestAPrivateRepositoryThisDeploymentNoLongerReads:
    def test_the_launch_form_says_why_before_start(
        self, auth_client, source_run, private_github_refused
    ):
        _private_repository(source_run)

        options = _launch_options(auth_client, source_run)

        assert options["blocked"] == {"code": REFUSED, "message": ENVIRONMENT_SENTENCE}

    def test_a_start_is_refused_at_once_with_the_reason_and_nothing_is_scheduled(
        self,
        auth_client,
        source_run,
        temporal,
        private_github_refused,
        django_capture_on_commit_callbacks,
    ):
        _private_repository(source_run)

        response = _start(auth_client, source_run, django_capture_on_commit_callbacks)

        assert _refusal_of(response) == (422, REFUSED, ENVIRONMENT_SENTENCE)
        assert response.json()["retryable"] is False
        assert not AgentFixRun.no_workspace_objects.exists()
        temporal.start.assert_not_called()

    def test_a_retry_of_a_failed_fix_is_refused_with_the_same_reason(
        self,
        auth_client,
        source_run,
        temporal,
        private_github_refused,
        django_capture_on_commit_callbacks,
    ):
        failed = _fix_run(
            _private_repository(source_run),
            state=AgentFixRun.State.FAILED,
            failure={"code": "fix_engine_failed", "message": "m"},
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(f"{BASE}/{failed.id}/retry/", {}, format="json")

        assert _refusal_of(response) == (422, REFUSED, ENVIRONMENT_SENTENCE)
        assert list(AgentFixRun.no_workspace_objects.values_list("id", flat=True)) == [
            failed.id
        ]
        temporal.start.assert_not_called()


class TestAFixTheEngineEndsOnTheRefusal:
    @pytest.mark.parametrize(
        ("side", "message"),
        [
            (FailureOwner.OURS, '422 {"error": "github_private_unavailable"}'),
            (FailureOwner.USER, CREATE_PANEL_SENTENCE),
        ],
        ids=["reported-as-the-platforms", "reported-as-the-users"],
    )
    def test_it_says_what_happened_in_the_environment_s_words(
        self, source_run, side, message
    ):
        fix_run = _fix_run(source_run, token_hash=hash_secret("t"))

        with patch.object(engine, "logger") as log:
            closed = engine.apply_report(
                fix_run,
                EngineReport(
                    stage="done",
                    ending=Ending.FAILED,
                    summary="raw",
                    failure=CheckFailure(owner=side, code=REFUSED, message=message),
                ),
            )

        assert closed.state == AgentFixRun.State.FAILED
        assert closed.failure == {"code": REFUSED, "message": ENVIRONMENT_SENTENCE}
        # The user's to fix: nothing of the platform's failed, so no alert is raised.
        log.error.assert_not_called()


class TestEveryOtherSourceStartsAFixAsBefore:
    def test_a_public_repository_starts_where_private_ones_are_refused(
        self,
        auth_client,
        source_run,
        temporal,
        private_github_refused,
        django_capture_on_commit_callbacks,
    ):
        _public_repository(source_run)

        options = _launch_options(auth_client, source_run)
        response = _start(auth_client, source_run, django_capture_on_commit_callbacks)

        assert options["blocked"] is None
        assert response.status_code == 201, response.content
        temporal.start.assert_called_once_with(response.json()["id"])

    def test_an_upload_that_replaced_a_private_repository_starts(
        self,
        auth_client,
        source_run,
        temporal,
        private_github_refused,
        django_capture_on_commit_callbacks,
    ):
        # The run read an uploaded archive: only a repository read is refused, so a
        # private visibility left beside it changes nothing.
        _ran_from(
            source_run,
            kind="archive",
            archive_artifact_id=UPLOADED_ARCHIVE_ID,
            visibility="private",
            installation_id="42",
        )

        options = _launch_options(auth_client, source_run)
        response = _start(auth_client, source_run, django_capture_on_commit_callbacks)

        assert options["blocked"] is None
        assert response.status_code == 201, response.content
        temporal.start.assert_called_once_with(response.json()["id"])

    def test_a_private_repository_starts_where_the_deployment_still_reads_it(
        self,
        auth_client,
        source_run,
        temporal,
        private_github_allowed,
        django_capture_on_commit_callbacks,
    ):
        _private_repository(source_run)

        options = _launch_options(auth_client, source_run)
        response = _start(auth_client, source_run, django_capture_on_commit_callbacks)

        assert options["blocked"] is None
        assert response.status_code == 201, response.content
        temporal.start.assert_called_once_with(response.json()["id"])
