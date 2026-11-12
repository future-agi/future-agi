"""The sandbox provider forwards no GitHub App installation where the platform App
serves more than one organization.

With ``HARNESS_PROVIDER=sandbox`` the out-of-process sandbox server mints the
installation token and clones the repository itself, beyond the hosted mint's
refusal. So with the platform App configured (``GITHUB_APP_ID`` and
``GITHUB_APP_PRIVATE_KEY``) on Future AGI Cloud or with two or more organizations, a
source that carries an installation id is refused before the sandbox server is asked.
A self-hosted install with one organization still forwards it, without the App
nothing changes, and a source without an installation id is forwarded as before.

The provider is called with a request as the view hands it over after validation, so
an installation id that reached ``validated_data`` some other way than a client
naming it is refused too.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import DatabaseError

from simulate.services import harness_provider
from simulate.services.harness_provider import SandboxHarnessProvider
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
# The code the hosted mint refuses with on this line.
REFUSED_CODE = "github_private_unavailable"
REFUSED_BODY = {
    "error": REFUSED_CODE,
    "message": PRIVATE_GITHUB_UNAVAILABLE_MESSAGE,
    "retryable": False,
}

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
VISIBILITIES = pytest.mark.parametrize("visibility", ["private", "public"])


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


# --- Refused where the platform App may serve several organizations ----------------


@VISIBILITIES
@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_sandbox_job_with_an_installation_never_reaches_the_sandbox(
    settings, monkeypatch, sandbox_server, deployment, orgs, visibility
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, orgs)
    job = _github_job(visibility=visibility, installation_id=INSTALLATION)

    response = SandboxHarnessProvider().create(_request(job))

    assert response.status_code == 422
    assert response.data == REFUSED_BODY
    sandbox_server.factory.assert_not_called()
    sandbox_server.client.submit.assert_not_called()
    assert sandbox_server.recorded == []


@VISIBILITIES
@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_sandbox_preflight_with_an_installation_never_reaches_the_sandbox(
    settings, monkeypatch, sandbox_server, deployment, orgs, visibility
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, orgs)
    job = _github_job(visibility=visibility, installation_id=INSTALLATION)

    response = SandboxHarnessProvider().preflight(_request(job))

    assert response.status_code == 422
    assert response.data == REFUSED_BODY
    sandbox_server.factory.assert_not_called()
    sandbox_server.client.preflight.assert_not_called()


@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_flatten_refuses_before_the_installation_is_mapped(
    settings, monkeypatch, deployment, orgs
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, orgs)

    with pytest.raises(HostedHarnessError) as refused:
        SandboxHarnessProvider._flatten_source(
            _github_job(visibility="private", installation_id=INSTALLATION)
        )

    assert refused.value.code == REFUSED_CODE
    assert refused.value.message == PRIVATE_GITHUB_UNAVAILABLE_MESSAGE
    assert refused.value.status_code == 422
    assert refused.value.retryable is False


# --- Unchanged where the refusal does not apply --------------------------------------


@pytest.mark.parametrize("deployment", SELF_HOSTED)
def test_single_org_self_hosted_sandbox_still_forwards_the_installation(
    settings, monkeypatch, sandbox_server, deployment
):
    _configure_github_app(settings, deployment)
    asked = _live_orgs(monkeypatch, ("org-a",))
    job = _github_job(visibility="private", installation_id=INSTALLATION)

    response = SandboxHarnessProvider().create(_request(job))

    assert response.status_code == 202
    sandbox_server.client.submit.assert_called_once_with(
        _forwarded(github_visibility="private", github_installation_id=INSTALLATION)
    )
    assert sandbox_server.recorded == [{"job": {"job_id": "job-1"}}]
    assert asked == [True]


@pytest.mark.parametrize("deployment", SELF_HOSTED)
def test_single_org_self_hosted_sandbox_preflight_still_forwards_the_installation(
    settings, monkeypatch, sandbox_server, deployment
):
    _configure_github_app(settings, deployment)
    _live_orgs(monkeypatch, ("org-a",))
    job = _github_job(visibility="private", installation_id=INSTALLATION)

    response = SandboxHarnessProvider().preflight(_request(job))

    assert response.status_code == 200
    assert response.data == {"ready_to_submit": True}
    sandbox_server.client.preflight.assert_called_once_with(
        _forwarded(github_visibility="private", github_installation_id=INSTALLATION)
    )


@pytest.mark.parametrize("deployment", ["", "US"])
@pytest.mark.parametrize(
    "app",
    [
        pytest.param({"GITHUB_APP_ID": "", "GITHUB_APP_PRIVATE_KEY": ""}, id="none"),
        pytest.param(
            {"GITHUB_APP_ID": "12345", "GITHUB_APP_PRIVATE_KEY": ""}, id="key-missing"
        ),
    ],
)
def test_without_the_platform_app_the_installation_is_forwarded_as_before(
    settings, monkeypatch, deployment, app
):
    settings.CLOUD_DEPLOYMENT = deployment
    for name, value in app.items():
        setattr(settings, name, value)
    asked = _live_orgs(monkeypatch, ("org-a", "org-b"))

    flat = SandboxHarnessProvider._flatten_source(
        _github_job(visibility="private", installation_id=INSTALLATION)
    )

    assert flat == _forwarded(
        github_visibility="private", github_installation_id=INSTALLATION
    )
    assert asked == []


@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_sandbox_job_without_an_installation_is_forwarded_as_before(
    settings, monkeypatch, sandbox_server, deployment, orgs
):
    _configure_github_app(settings, deployment)
    asked = _live_orgs(monkeypatch, orgs)
    job = _github_job(visibility="public", commit_sha="a" * 40)

    response = SandboxHarnessProvider().create(_request(job))

    assert response.status_code == 202
    sandbox_server.client.submit.assert_called_once_with(
        _forwarded(github_visibility="public", github_commit_sha="a" * 40)
    )
    assert asked == []


@pytest.mark.parametrize(("deployment", "orgs"), MULTI_TENANT)
def test_multi_tenant_sandbox_archive_source_is_forwarded_as_before(
    settings, monkeypatch, deployment, orgs
):
    _configure_github_app(settings, deployment)
    asked = _live_orgs(monkeypatch, orgs)

    flat = SandboxHarnessProvider._flatten_source(
        {
            "source": {
                "kind": "archive",
                "archive_artifact_id": "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7",
            },
            "agent": {"connector": "auto"},
        }
    )

    assert flat["source_id"] == "63ef3598-a84d-4ce0-a7a1-53c4e27f69f7"
    assert "github_installation_id" not in flat
    assert asked == []
