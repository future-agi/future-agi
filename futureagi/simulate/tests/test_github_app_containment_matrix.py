"""Every GitHub App containment point, in each of the four deployment cells.

Nothing yet ties a GitHub App installation to the organization that names it, so
where the platform App is configured and the deployment serves two or more
organizations, no repository is reached through an installation. Each refusal point
is driven through the real ``platform_github_app_refused()`` in four cells, with
``CLOUD_DEPLOYMENT`` empty (the setting's unset value) and the organization query
answering one or two synthetic ids:

- A: App configured, two organizations: refused, nothing reaches GitHub or the
  sandbox server.
- B: App configured, one organization: as before the containment.
- C: App not configured, two organizations: as before; the containment never asks.
- D: App not configured, one organization: as before; the containment never asks.

The points: job creation's source validation, the installation token mint, Fix my
agent's pull request push, and the sandbox provider's source mapping.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import Mock

import pytest

from simulate.serializers.harness_job import HarnessJobCreateSerializer
from simulate.services import agent_fix_github, harness_provider
from simulate.services.harness_provider import SandboxHarnessProvider
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import GitHubAppTokenProvider

try:  # New module: on the pre-containment tree these tests fail one by one.
    from simulate.services import github_app_tenancy
    from simulate.services.github_app_tenancy import platform_github_app_refused
except ImportError:
    github_app_tenancy = None
    platform_github_app_refused = None

PRIVATE_GITHUB_UNAVAILABLE_MESSAGE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Use a public repository or upload the code."
)

GATEWAY = "simulate.services.hosted_harness_gateway"
APP_ID = "12345"
APP_KEY = "synthetic-app-private-key"
INSTALLATION = "4242"
REPOSITORY = "victim-org/private-agent"
TOKEN = "synthetic-installation-token"
ACCESS_TOKENS = f"https://api.github.com/app/installations/{INSTALLATION}/access_tokens"
REVOKE = "https://api.github.com/installation/token"
REFUSED_CODE = "github_private_unavailable"
REFUSED_BODY = {
    "error": REFUSED_CODE,
    "message": PRIVATE_GITHUB_UNAVAILABLE_MESSAGE,
    "retryable": False,
}
PULL_REQUEST_REFUSAL = (
    "Pull requests through the Future AGI GitHub App are not available on"
    " Future AGI cloud. Download the patch and apply it yourself."
)
ONE_ORGANIZATION = ("synthetic-org-a",)
TWO_ORGANIZATIONS = ("synthetic-org-a", "synthetic-org-b")


class Cell(NamedTuple):
    key: str
    app: bool
    orgs: tuple
    refused: bool


CELLS = (
    Cell("A-app-multi-refused", True, TWO_ORGANIZATIONS, True),
    Cell("B-app-single-as-before", True, ONE_ORGANIZATION, False),
    Cell("C-noapp-multi-as-before", False, TWO_ORGANIZATIONS, False),
    Cell("D-noapp-single-as-before", False, ONE_ORGANIZATION, False),
)


def cells(point):
    """Parametrize a test over the four cells, its ids reading ``<point>-<cell>``."""
    return pytest.mark.parametrize(
        "cell", [pytest.param(cell, id=f"{point}-{cell.key}") for cell in CELLS]
    )


def _enter(settings, monkeypatch, cell):
    """Configure the cell's App settings and organizations; return the record of
    each time the organization query is asked."""
    settings.CLOUD_DEPLOYMENT = ""
    settings.GITHUB_APP_ID = APP_ID if cell.app else ""
    settings.GITHUB_APP_PRIVATE_KEY = APP_KEY if cell.app else ""
    asked = []

    def live_org_ids():
        asked.append(True)
        return list(cell.orgs)

    monkeypatch.setattr(github_app_tenancy, "_live_org_ids", live_org_ids)
    return asked


def _assert_asked(cell, asked, sites=1):
    """With the App, each refusal site asks once (and the first refusal stops the
    path); without it the containment never asks."""
    if not cell.app:
        assert asked == []
    elif cell.refused:
        assert asked == [True]
    else:
        assert asked == [True] * sites


def _assert_refused(error):
    assert error.code == REFUSED_CODE
    assert error.message == PRIVATE_GITHUB_UNAVAILABLE_MESSAGE
    assert error.status_code == 422
    assert error.retryable is False


@cells("tenancy")
def test_the_cell_reaches_the_real_tenancy_check(settings, monkeypatch, cell):
    asked = _enter(settings, monkeypatch, cell)

    assert platform_github_app_refused() is cell.refused
    _assert_asked(cell, asked)


# --- Job creation --------------------------------------------------------------------


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


@cells("serializer-installation")
def test_job_creation_with_an_installation(settings, monkeypatch, cell):
    asked = _enter(settings, monkeypatch, cell)
    serializer = HarnessJobCreateSerializer(
        data=_github_body(visibility="private", installation_id=INSTALLATION)
    )

    if cell.refused:
        assert not serializer.is_valid()
        assert serializer.errors["source"]["installation_id"] == [
            PRIVATE_GITHUB_UNAVAILABLE_MESSAGE
        ]
    else:
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["source"]["installation_id"] == INSTALLATION
    _assert_asked(cell, asked)


@cells("serializer-private-no-installation")
def test_job_creation_of_a_private_source_without_an_installation(
    settings, monkeypatch, cell
):
    asked = _enter(settings, monkeypatch, cell)
    serializer = HarnessJobCreateSerializer(data=_github_body(visibility="private"))

    assert not serializer.is_valid()
    expected = (
        PRIVATE_GITHUB_UNAVAILABLE_MESSAGE
        if cell.refused
        else "required for private github sources"
    )
    assert serializer.errors["source"]["installation_id"] == [expected]
    _assert_asked(cell, asked)


# --- Installation token mint ---------------------------------------------------------


class _GitHubApp:
    """Stands in for the App JWT signer and GitHub's token endpoints, recording each
    call and answering as GitHub does."""

    def __init__(self):
        self.signed = []
        self.posts = []
        self.deletes = []

    def encode(self, claims, _key, algorithm):
        self.signed.append((claims["iss"], algorithm))
        return "synthetic-app-jwt"

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs["headers"]["Authorization"]))
        return SimpleNamespace(status_code=201, json=lambda: {"token": TOKEN})

    def delete(self, url, **kwargs):
        self.deletes.append((url, kwargs["headers"]["Authorization"]))
        return SimpleNamespace(status_code=204)


@pytest.fixture
def github_app(monkeypatch):
    app = _GitHubApp()
    monkeypatch.setattr(f"{GATEWAY}.jwt.encode", app.encode)
    monkeypatch.setattr(f"{GATEWAY}.requests.post", app.post)
    monkeypatch.setattr(f"{GATEWAY}.requests.delete", app.delete)
    return app


def _assert_github_untouched(github_app):
    assert github_app.signed == []
    assert github_app.posts == []
    assert github_app.deletes == []


def _assert_minted(github_app):
    assert github_app.signed == [(APP_ID, "RS256")]
    assert github_app.posts == [(ACCESS_TOKENS, "Bearer synthetic-app-jwt")]


def _provider():
    """A provider holding its own App credentials, as one built before the platform
    settings were read."""
    return GitHubAppTokenProvider(app_id=APP_ID, private_key=APP_KEY)


@cells("mint-token")
def test_installation_token(settings, monkeypatch, github_app, cell):
    asked = _enter(settings, monkeypatch, cell)

    if cell.refused:
        with pytest.raises(HostedHarnessError) as refused:
            _provider().installation_token(INSTALLATION)
        _assert_refused(refused.value)
        _assert_github_untouched(github_app)
    else:
        assert _provider().installation_token(INSTALLATION) == TOKEN
        _assert_minted(github_app)
        assert github_app.deletes == []
    _assert_asked(cell, asked)


@cells("mint-credential")
def test_installation_credential(settings, monkeypatch, github_app, cell):
    asked = _enter(settings, monkeypatch, cell)
    yielded = []

    if cell.refused:
        with pytest.raises(HostedHarnessError) as refused:
            with _provider().credential(INSTALLATION) as token:
                yielded.append(token)
        _assert_refused(refused.value)
        assert yielded == []
        _assert_github_untouched(github_app)
    else:
        with _provider().credential(INSTALLATION) as token:
            yielded.append(token)
            assert github_app.deletes == []
        assert yielded == [TOKEN]
        _assert_minted(github_app)
        assert github_app.deletes == [(REVOKE, f"Bearer {TOKEN}")]
    _assert_asked(cell, asked)


@cells("mint-from-settings")
def test_installation_token_from_the_platform_settings(
    settings, monkeypatch, github_app, cell
):
    asked = _enter(settings, monkeypatch, cell)

    if cell.refused:
        with pytest.raises(HostedHarnessError) as refused:
            GitHubAppTokenProvider.from_settings().installation_token(INSTALLATION)
        _assert_refused(refused.value)
        _assert_github_untouched(github_app)
    elif cell.app:
        token = GitHubAppTokenProvider.from_settings().installation_token(INSTALLATION)
        assert token == TOKEN
        _assert_minted(github_app)
    else:
        # Without the platform App there is nothing to build the provider from, as
        # before the containment.
        with pytest.raises(HostedHarnessError) as unconfigured:
            GitHubAppTokenProvider.from_settings()
        assert unconfigured.value.code == "github_app_not_configured"
        assert unconfigured.value.status_code == 503
        assert unconfigured.value.retryable is True
        _assert_github_untouched(github_app)
    # On this line only ``installation_token`` asks; ``from_settings`` does not.
    _assert_asked(cell, asked)


# --- Fix my agent's pull request push ------------------------------------------------


class _Minted(Exception):
    """GitHub's access-token endpoint was asked for an installation token."""


@pytest.fixture
def stored_installation(monkeypatch):
    """A GitHub agent whose stored version runtime names an installation. The real
    token provider runs against a recording JWT signer and HTTP seam; every other
    step before the token is faked."""
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

    monkeypatch.setattr(f"{GATEWAY}.jwt.encode", encode)
    monkeypatch.setattr(f"{GATEWAY}.requests.post", post)
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


@cells("agent-fix-push")
def test_pull_request_push_through_a_stored_installation(
    settings, monkeypatch, stored_installation, cell
):
    asked = _enter(settings, monkeypatch, cell)

    if cell.refused:
        with pytest.raises(HostedHarnessError) as refused:
            agent_fix_github.open_pull_request(stored_installation.candidate, user=None)
        # The code the fix page already answers with its patch download.
        assert refused.value.code == "fix_github_not_connected"
        assert refused.value.status_code == 422
        assert refused.value.message == PULL_REQUEST_REFUSAL
        # Refused before the override is recorded or GitHub is asked for a token.
        assert stored_installation.overrides == []
        assert stored_installation.signed == []
        assert stored_installation.posts == []
    elif cell.app:
        with pytest.raises(_Minted):
            agent_fix_github.open_pull_request(stored_installation.candidate, user=None)
        assert len(stored_installation.overrides) == 1
        assert stored_installation.signed == [(APP_ID, "RS256")]
        assert stored_installation.posts == [ACCESS_TOKENS]
    else:
        # As before the containment: the override is recorded, then there is no
        # platform App to mint with.
        with pytest.raises(HostedHarnessError) as unconfigured:
            agent_fix_github.open_pull_request(stored_installation.candidate, user=None)
        assert unconfigured.value.code == "github_app_not_configured"
        assert unconfigured.value.status_code == 503
        assert len(stored_installation.overrides) == 1
        assert stored_installation.signed == []
        assert stored_installation.posts == []
    # Two sites ask with the App: the push guard, then ``installation_token``.
    _assert_asked(cell, asked, sites=2)


# --- Sandbox provider source mapping -------------------------------------------------


def _github_job(**source):
    return {
        "source": {
            "kind": "github",
            "repository": REPOSITORY,
            "ref": "main",
            **source,
        },
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "metadata": {},
    }


def _forwarded(**github):
    """The flat body the sandbox server receives for ``_github_job``."""
    return {
        "scenario_count": 1,
        "connector": "auto",
        "connector_config": {},
        "secret_refs": {},
        "metadata": {},
        "github_repository": REPOSITORY,
        "github_ref": "main",
        **github,
    }


FORWARDED_INSTALLATION = _forwarded(
    github_visibility="private", github_installation_id=INSTALLATION
)


def _request(validated_data):
    """A request as the view hands it to the provider once validation has run."""
    return SimpleNamespace(
        validated_data=validated_data,
        organization=SimpleNamespace(pk="synthetic-org"),
        workspace=None,
        headers={},
        user=None,
    )


@pytest.fixture
def sandbox_server(monkeypatch):
    """Record every call the provider makes to the sandbox server."""
    client = Mock()
    client.submit.return_value = {"job": {"job_id": "job-1"}}
    client.preflight.return_value = {"ready_to_submit": True}
    factory = Mock(return_value=client)
    monkeypatch.setattr(SandboxHarnessProvider, "_client", factory)
    recorded = []
    monkeypatch.setattr(
        SandboxHarnessProvider,
        "_record_job",
        staticmethod(lambda result, **scope: recorded.append(result)),
    )
    monkeypatch.setattr(
        harness_provider,
        "_sandbox_preflight_body",
        lambda payload, report: {"ready_to_submit": report["ready_to_submit"]},
    )
    return SimpleNamespace(client=client, factory=factory, recorded=recorded)


@cells("provider-flatten")
def test_sandbox_source_mapping(settings, monkeypatch, cell):
    asked = _enter(settings, monkeypatch, cell)
    job = _github_job(visibility="private", installation_id=INSTALLATION)

    if cell.refused:
        with pytest.raises(HostedHarnessError) as refused:
            SandboxHarnessProvider._flatten_source(job)
        _assert_refused(refused.value)
    else:
        assert SandboxHarnessProvider._flatten_source(job) == FORWARDED_INSTALLATION
    _assert_asked(cell, asked)


@cells("provider-create")
def test_sandbox_job_creation(settings, monkeypatch, sandbox_server, cell):
    asked = _enter(settings, monkeypatch, cell)
    job = _github_job(visibility="private", installation_id=INSTALLATION)

    response = SandboxHarnessProvider().create(_request(job))

    if cell.refused:
        assert response.status_code == 422
        assert response.data == REFUSED_BODY
        sandbox_server.factory.assert_not_called()
        sandbox_server.client.submit.assert_not_called()
        assert sandbox_server.recorded == []
    else:
        assert response.status_code == 202
        sandbox_server.client.submit.assert_called_once_with(FORWARDED_INSTALLATION)
        assert sandbox_server.recorded == [{"job": {"job_id": "job-1"}}]
    _assert_asked(cell, asked)


@cells("provider-preflight")
def test_sandbox_preflight(settings, monkeypatch, sandbox_server, cell):
    asked = _enter(settings, monkeypatch, cell)
    job = _github_job(visibility="private", installation_id=INSTALLATION)

    response = SandboxHarnessProvider().preflight(_request(job))

    if cell.refused:
        assert response.status_code == 422
        assert response.data == REFUSED_BODY
        sandbox_server.factory.assert_not_called()
        sandbox_server.client.preflight.assert_not_called()
    else:
        assert response.status_code == 200
        assert response.data == {"ready_to_submit": True}
        sandbox_server.client.preflight.assert_called_once_with(FORWARDED_INSTALLATION)
    _assert_asked(cell, asked)
