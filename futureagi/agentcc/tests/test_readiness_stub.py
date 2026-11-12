"""Dev's readiness stand-in keeps the c2.5 public response contract."""

import json

import pytest
from django.test import RequestFactory
from django.urls import resolve, reverse


@pytest.fixture(autouse=True)
def gateway_environment(monkeypatch):
    for name in (
        "CLOUD_DEPLOYMENT",
        "AGENTCC_GATEWAY_PUBLIC_URL",
        "AGENTCC_GATEWAY_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def readiness_response():
    # Import at call time so the RED baseline records each missing-module case.
    from agentcc.readiness import readiness

    return readiness(RequestFactory().get("/readyz/"))


def assert_ready(response):
    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ready"}
    assert response["Cache-Control"] == "no-store"


def assert_missing(response):
    assert response.status_code == 503
    assert json.loads(response.content) == {
        "status": "not_ready",
        "reason": "gateway_public_url",
    }
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV", " eu ", "dev"])
@pytest.mark.parametrize("public_url", [None, "", " \t "])
def test_hosted_unset_or_blank_url_fails_readiness(monkeypatch, deployment, public_url):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    monkeypatch.setenv("AGENTCC_GATEWAY_URL", "https://legacy.example.com")
    if public_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", public_url)
    assert_missing(readiness_response())


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV", " us "])
def test_hosted_public_url_is_ready(monkeypatch, deployment):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com")
    assert_ready(readiness_response())


@pytest.mark.parametrize("deployment", [None, "", "false", "local", "self-hosted"])
def test_non_hosted_unset_url_is_ready(monkeypatch, deployment):
    if deployment is not None:
        monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    assert_ready(readiness_response())


def test_readiness_rechecks_configuration_without_startup(monkeypatch):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    assert_missing(readiness_response())
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com")
    assert_ready(readiness_response())
    monkeypatch.delenv("AGENTCC_GATEWAY_PUBLIC_URL")
    assert_missing(readiness_response())


@pytest.mark.parametrize("missing", [True, False])
def test_gateway_check_can_be_patched_at_module_boundary(monkeypatch, missing):
    from agentcc import readiness

    monkeypatch.setattr(readiness, "hosted_gateway_public_url_missing", lambda: missing)
    response = readiness.readiness(RequestFactory().get("/readyz/"))
    (assert_missing if missing else assert_ready)(response)


def test_readyz_route_uses_readiness_with_trailing_slash(monkeypatch):
    from agentcc.readiness import readiness

    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    route = resolve("/readyz/")
    assert route.func is readiness
    assert route.url_name == "hosted-readiness"
    assert reverse("hosted-readiness") == "/readyz/"
    assert_missing(route.func(RequestFactory().get("/readyz/")))
