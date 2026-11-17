"""No repository is reached through a GitHub App installation where the platform App
serves more than one organization.

Nothing yet ties an installation to the organization that names it. So with the
platform App configured (``GITHUB_APP_ID`` and ``GITHUB_APP_PRIVATE_KEY``) on Future
AGI Cloud (``CLOUD_DEPLOYMENT`` US, EU or DEV) or with two or more organizations, a
job may not name one and Fix my agent does not push through one. A self-hosted
install with one organization keeps both, and without the App nothing changes.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.db import DatabaseError
from rest_framework.response import Response

from simulate.serializers.harness_job import (
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
)
from simulate.services import agent_fix_github
from simulate.services.hosted_harness import HostedHarnessError

try:  # New module: on the pre-containment tree these tests fail one by one.
    from simulate.services import github_app_tenancy
except ImportError:
    github_app_tenancy = None

PRIVATE_GITHUB_UNAVAILABLE_MESSAGE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Use a public repository or upload the code."
)

INSTALLATION = "4242"
REPOSITORY = "victim-org/private-agent"
URL = "/simulate/api/harness-jobs/"
ACCESS_TOKENS = f"https://api.github.com/app/installations/{INSTALLATION}/access_tokens"

# (CLOUD_DEPLOYMENT, the organization query's answer) where the platform App serves,
# or may serve, more than one organization.
MULTI_TENANT = (
    pytest.param("", ("org-a", "org-b"), id="two-orgs-not-cloud"),
    pytest.param("US", ("org-a",), id="cloud-US-one-org"),
    pytest.param("eu", ("org-a",), id="cloud-lowercase-eu-one-org"),
    pytest.param(" DEV ", ("org-a",), id="cloud-padded-DEV-one-org"),
    pytest.param("", DatabaseError("synthetic outage"), id="org-query-fails"),
)
SELF_HOSTED = ("", "false", "onprem")


def _configure_github_app(settings, deployment=""):
    settings.CLOUD_DEPLOYMENT = deployment
    settings.GITHUB_APP_ID = "12345"
    settings.GITHUB_APP_PRIVATE_KEY = "synthetic-app-private-key"


def _live_orgs(monkeypatch, answer):
    """Answer the organization query with ``answer`` ids, or raise it if it is an
    exception, recording each time it is asked."""
    asked = []

    def live_org_ids():
        asked.append(True)
        if isinstance(answer, BaseException):
            raise answer
        return list(answer)

    monkeypatch.setattr(github_app_tenancy, "_live_org_ids", live_org_ids)
    return asked


def _github_body(**source):
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": REPOSITORY,
            "ref": "main",
            **source,
        },
        # ``auto`` needs no provider credential, so only the source is judged.
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "artifacts": {"level": "full"},
        "metadata": {},
    }


SOURCES = (
    pytest.param(
        {"visibility": "private", "installation_id": INSTALLATION}, id="private"
    ),
    pytest.param(
        {"visibility": "public", "installation_id": INSTALLATION}, id="public"
    ),
)
SERIALIZERS = pytest.mark.parametrize(
    "serializer_cls", [HarnessJobCreateSerializer, HarnessPreflightSerializer]
)


# --- Job creation and preflight -----------------------------------------------------


@SERIALIZERS
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_refuses_a_github_source_reached_through_an_installation(
    settings, monkeypatch, deployment, orgs, source, serializer_cls
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, orgs)
    serializer = serializer_cls(data=_github_body(**source))

    assert not serializer.is_valid()
    assert serializer.errors["source"]["installation_id"] == [
        PRIVATE_GITHUB_UNAVAILABLE_MESSAGE
    ]


@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_tells_a_private_source_what_works_instead_of_asking_for_one(
    settings, monkeypatch, deployment, orgs
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, orgs)
    serializer = HarnessJobCreateSerializer(data=_github_body(visibility="private"))

    assert not serializer.is_valid()
    assert serializer.errors["source"]["installation_id"] == [
        PRIVATE_GITHUB_UNAVAILABLE_MESSAGE
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("deployment", ["", "US"])
def test_job_creation_answers_400_before_the_provider_sees_the_job(
    settings, auth_client, workspace, monkeypatch, deployment
):
    """Two real organizations (the caller's and another) make the deployment
    multi-tenant even without CLOUD_DEPLOYMENT."""
    from accounts.models import Organization

    _configure_github_app(settings, deployment)
    Organization.objects.create(name="Another synthetic organization")
    created = []
    monkeypatch.setattr(
        "simulate.views.harness_job.get_harness_provider",
        lambda: SimpleNamespace(
            create=lambda request: created.append(request) or Response(status=202)
        ),
    )

    response = auth_client.post(
        URL,
        _github_body(visibility="private", installation_id=INSTALLATION),
        format="json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )

    assert response.status_code == 400, response.content
    assert PRIVATE_GITHUB_UNAVAILABLE_MESSAGE in response.content.decode()
    assert created == []


@SERIALIZERS
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("deployment", SELF_HOSTED)
def test_single_org_self_hosted_still_accepts_a_source_reached_through_an_installation(
    settings, monkeypatch, deployment, source, serializer_cls
):
    _configure_github_app(settings, deployment)
    asked = _live_orgs(monkeypatch, ("only-org",))
    serializer = serializer_cls(data=_github_body(**source))

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"]["installation_id"] == INSTALLATION
    assert asked == [True]


@pytest.mark.parametrize("deployment", ("US", "EU") + SELF_HOSTED)
@pytest.mark.parametrize("missing", ["GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY"])
def test_without_the_platform_app_a_source_is_judged_as_before(
    settings, monkeypatch, deployment, missing
):
    _configure_github_app(settings, deployment)
    setattr(settings, missing, "")
    asked = _live_orgs(monkeypatch, ("org-a", "org-b"))

    with_installation = HarnessJobCreateSerializer(
        data=_github_body(visibility="private", installation_id=INSTALLATION)
    )
    assert with_installation.is_valid(), with_installation.errors
    without_installation = HarnessJobCreateSerializer(
        data=_github_body(visibility="private")
    )
    assert not without_installation.is_valid()
    assert without_installation.errors["source"]["installation_id"] == [
        "required for private github sources"
    ]
    assert asked == []


@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_a_public_source_without_an_installation_is_accepted_everywhere(
    settings, monkeypatch, deployment, orgs
):
    _configure_github_app(settings, deployment)
    asked = _live_orgs(monkeypatch, orgs)
    serializer = HarnessJobCreateSerializer(data=_github_body(visibility="public"))

    assert serializer.is_valid(), serializer.errors
    # Nothing to refuse, so the organizations are not counted.
    assert asked == []


@pytest.mark.parametrize("deployment", SELF_HOSTED)
def test_single_org_self_hosted_still_asks_a_private_source_for_its_installation(
    settings, monkeypatch, deployment
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, ("only-org",))
    serializer = HarnessJobCreateSerializer(data=_github_body(visibility="private"))

    assert not serializer.is_valid()
    assert serializer.errors["source"]["installation_id"] == [
        "required for private github sources"
    ]


# --- Fix my agent's pull request ----------------------------------------------------


class _Minted(Exception):
    """GitHub's access-token endpoint was asked for an installation token."""


@pytest.fixture
def stored_installation(monkeypatch):
    """A GitHub agent whose stored version runtime names an installation, as one
    created before this containment does. The real token provider runs against a
    recording JWT signer and HTTP seam; every other step before the token is faked."""
    seen = SimpleNamespace(filters=[], overrides=[], signed=[], posts=[])
    runtime = SimpleNamespace(
        agent_payload={
            "source_access": {"visibility": "private", "installation_id": INSTALLATION}
        },
        source={"repository": REPOSITORY, "ref": "refs/heads/main"},
    )

    class _Rows:
        def filter(self, **lookup):
            seen.filters.append(lookup)
            return SimpleNamespace(first=lambda: runtime)

    def encode(claims, _key, algorithm):
        seen.signed.append((claims["iss"], algorithm))
        return "synthetic-app-jwt"

    def post(url, **_kwargs):
        seen.posts.append(url)
        raise _Minted(url)

    gateway = "simulate.services.hosted_harness_gateway"
    monkeypatch.setattr(f"{gateway}.jwt.encode", encode)
    monkeypatch.setattr(f"{gateway}.requests.post", post)
    monkeypatch.setattr(
        agent_fix_github,
        "HarnessAgentVersionRuntime",
        SimpleNamespace(
            no_workspace_objects=_Rows(),
            SourceKind=SimpleNamespace(GITHUB="github"),
        ),
    )
    monkeypatch.setattr(agent_fix_github, "require_shippable", lambda *a, **k: None)
    monkeypatch.setattr(
        agent_fix_github,
        "record_override",
        lambda candidate, **kwargs: seen.overrides.append(kwargs),
    )
    monkeypatch.setattr(agent_fix_github, "load_object", lambda key: b"")
    monkeypatch.setattr(agent_fix_github, "limited_validation", lambda fix_run: False)
    monkeypatch.setattr(
        agent_fix_github, "overridable_regressions", lambda candidate: []
    )
    fix_run = SimpleNamespace(
        id=uuid.uuid4(),
        base_agent_version_id=uuid.uuid4(),
        source_run=SimpleNamespace(payload={"source": {"commit_sha": "a" * 40}}),
    )
    seen.candidate = SimpleNamespace(
        id=uuid.uuid4(),
        fix_run=fix_run,
        pull_request_url="",
        attempt=1,
        patch_object_key="fix/patch",
        summary="Keep the date the caller asked for",
        verdict={},
        override_reason="",
    )
    return seen


@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_refuses_the_pull_request_push_through_a_stored_installation(
    settings, monkeypatch, deployment, orgs, stored_installation
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, orgs)

    with pytest.raises(HostedHarnessError) as refused:
        agent_fix_github.open_pull_request(stored_installation.candidate, user=None)

    # The code the fix page already answers with its patch download.
    assert (refused.value.code, refused.value.status_code) == (
        "fix_github_not_connected",
        422,
    )
    assert "not available on Future AGI cloud" in refused.value.message
    assert "Download the patch" in refused.value.message
    # GitHub is never asked for a token, and nothing is recorded on the candidate.
    assert stored_installation.posts == []
    assert stored_installation.signed == []
    assert stored_installation.overrides == []


@pytest.mark.parametrize("deployment", SELF_HOSTED)
def test_single_org_self_hosted_still_pushes_through_the_stored_installation(
    settings, monkeypatch, deployment, stored_installation
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, ("only-org",))

    with pytest.raises(_Minted):
        agent_fix_github.open_pull_request(stored_installation.candidate, user=None)

    assert stored_installation.signed == [("12345", "RS256")]
    assert stored_installation.posts == [ACCESS_TOKENS]
    assert len(stored_installation.overrides) == 1
