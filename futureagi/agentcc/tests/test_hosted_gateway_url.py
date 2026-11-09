"""Hosted startup, readiness and dashboard share the public URL contract."""

import importlib
import json
from unittest.mock import patch

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate

ERROR_MESSAGE = (
    "AGENTCC_GATEWAY_PUBLIC_URL must be set to an HTTPS URL with a non-local "
    "hostname on hosted deployments."
)
MISSING_MESSAGE = (
    "AGENTCC_GATEWAY_PUBLIC_URL is not configured on this hosted deployment; "
    "gateway URL not configured and readiness (/readyz/) fails."
)


@pytest.fixture(autouse=True)
def gateway_environment(monkeypatch):
    for name in (
        "CLOUD_DEPLOYMENT",
        "AGENTCC_DEPLOYMENT_PROFILE",
        "AGENTCC_GATEWAY_PUBLIC_URL",
        "AGENTCC_GATEWAY_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(
    params=[
        ("hosted", ""),
        ("hosted", "false"),
        ("hosted", "US"),
        ("self-hosted", "US"),
    ],
    ids=[
        "profile-only",
        "profile-cloud-false",
        "both-signals",
        "cloud-over-self-hosted",
    ],
)
def hosted_signal(request, monkeypatch, gateway_environment):
    from django.test import override_settings

    from agentcc.tests.test_hosted_startup import hosted_environment, hosted_settings

    profile, cloud = request.param
    # Exercise the real ready hook, including c2.5's existing validator. These
    # helpers generate isolated test-only credentials; no deployed values are read.
    env = hosted_environment()
    env["AGENTCC_DEPLOYMENT_PROFILE"] = profile
    env["CLOUD_DEPLOYMENT"] = cloud
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with override_settings(**hosted_settings(env)):
        yield


@pytest.fixture
def ready_dependencies():
    with (
        patch("agentcc.readiness.database_ready", return_value=True) as database,
        patch("agentcc.readiness.shared_state_ready", return_value=True) as shared,
    ):
        yield database, shared


@pytest.mark.parametrize(
    "public_url", [None, "", " \t "], ids=["unset", "empty", "blank"]
)
def test_hosted_signals_missing_url_boots_and_fails_readiness(
    hosted_signal, monkeypatch, public_url, ready_dependencies
):
    from agentcc.views.gateway import _build_virtual_gateway

    if public_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", public_url)
    with patch("agentcc.gateway_configuration.logger") as logger:
        start_agentcc()
        for _ in range(2):
            assert_missing_url_readiness()
            assert_live()
        data = _build_virtual_gateway(
            health={"status": "ok", "base_url": "http://localhost:8090/v1"}
        )
        assert data["base_url"] == ""
        assert data["status"] == "unconfigured"
    logger.error.assert_called_once_with(MISSING_MESSAGE)
    database, shared = ready_dependencies
    database.assert_not_called()
    shared.assert_not_called()


@pytest.mark.parametrize("url", ["http://gateway.example.com", "https://127.1"])
def test_hosted_signals_invalid_url_refuses(hosted_signal, monkeypatch, url):
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", url)
    with pytest.raises(ImproperlyConfigured) as exc:
        start_agentcc()
    assert str(exc.value) == ERROR_MESSAGE


def test_hosted_signals_configured_url_is_healthy(
    hosted_signal, monkeypatch, ready_dependencies
):
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com")
    start_agentcc()
    assert_ready()
    assert_live()


def test_hosted_readiness_rechecks_url_without_cached_success(
    hosted_signal, monkeypatch, ready_dependencies
):
    assert_missing_url_readiness()
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com")
    assert_ready()
    monkeypatch.delenv("AGENTCC_GATEWAY_PUBLIC_URL")
    assert_missing_url_readiness()
    assert_live()


def start_agentcc():
    # Use Django's real AppConfig selection, including the app's ready hook.
    AppConfig.create("agentcc").ready()


def health_response():
    from tfc.views.health import HealthCheckView

    return HealthCheckView.as_view()(APIRequestFactory().get("/health/"))


def assert_live():
    response = health_response()
    assert response.status_code == 200
    assert response.data == {"status": True, "result": "Server is up and running"}


def readiness_response():
    from agentcc.readiness import readiness

    return readiness(RequestFactory().get("/readyz/"))


def assert_missing_url_readiness():
    response = readiness_response()
    assert response.status_code == 503
    assert json.loads(response.content) == {
        "status": "not_ready",
        "reason": "gateway_public_url",
    }
    assert response["Cache-Control"] == "no-store"


def assert_ready():
    response = readiness_response()
    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ready"}
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV", " eu ", "dev"])
@pytest.mark.parametrize("public_url", [None, "", " \t "])
def test_hosted_missing_url_boots_logs_once_and_fails_readiness(
    monkeypatch, deployment, public_url, ready_dependencies
):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    # A legacy public URL is not a guaranteed address for this hosted region.
    monkeypatch.setenv("AGENTCC_GATEWAY_URL", "https://legacy.example.com")
    if public_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", public_url)
    with patch("agentcc.gateway_configuration.logger", create=True) as logger:
        start_agentcc()
        for _ in range(2):
            assert_missing_url_readiness()
            assert_live()
    logger.error.assert_called_once_with(MISSING_MESSAGE)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8090",
        "http://127.0.0.1:8090",
        "http://0.0.0.0:8090",
        "http://[::1]:8090",
        "http://gateway.example.com",
        "https://localhost:8090",
        "https://LOCALHOST./",
        "https://gateway.localhost/",
        "https://127.0.0.1",
        "https://127.0.0.0",
        "https://127.25.30.40",
        "https://127.255.255.255",
        "https://0.0.0.0",
        "https://[::1]",
        "https://[0:0:0:0:0:0:0:1]",
        "https://[::ffff:127.0.0.1]",
        "https://[::]",
        "https://",
        "https://[::1",
        "https://127.1",
        "https://2130706433",
        "https://0x7f.1",
        "https://0177.0.0.1",
        "https://0",
        "https://0x7f000001",
        "https://0x",
        "https://0x7f.0x.0x.1",
        "https://127.1./",
        "https://0.0",
    ],
)
def test_hosted_refuses_invalid_public_url(monkeypatch, url):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", url)
    with pytest.raises(ImproperlyConfigured) as exc:
        start_agentcc()
    assert str(exc.value) == ERROR_MESSAGE


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV", " us "])
def test_hosted_accepts_public_https_url(monkeypatch, deployment):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.futureagi.com")
    start_agentcc()
    assert health_response().status_code == 200


@pytest.mark.parametrize(
    "url",
    ["https://192.0.2.1", "https://gateway1.example.com", "https://0x7f.example.com"],
)
def test_nonlocal_numeric_and_hostname_controls(monkeypatch, url):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", url)
    start_agentcc()


@pytest.mark.parametrize("deployment", ["", "false", "local", "self-hosted"])
def test_non_hosted_unset_does_not_refuse(monkeypatch, deployment, ready_dependencies):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    start_agentcc()
    assert_ready()
    assert_live()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8090",
        "http://gateway.internal",
        "https://gateway.example.com",
        "https://127.1",
    ],
)
def test_non_hosted_explicit_url_does_not_refuse(monkeypatch, url):
    monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", url)
    start_agentcc()
    assert health_response().status_code == 200


@pytest.mark.parametrize("public_url", [None, "", " "])
def test_readiness_reports_missing_url_independently_of_startup(
    monkeypatch, public_url, ready_dependencies
):
    # The readiness request itself checks configuration, not a cached log flag.
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "EU")
    if public_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", public_url)
    assert_missing_url_readiness()
    assert_live()


@pytest.mark.parametrize(
    "public_url", [None, "", " \t "], ids=["unset", "empty", "blank"]
)
def test_liveness_stays_up_with_missing_hosted_url(
    hosted_signal, monkeypatch, public_url
):
    if public_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", public_url)
    assert_live()


def test_readiness_url_check_exception_is_value_free(ready_dependencies):
    with patch(
        "agentcc.readiness.hosted_gateway_public_url_missing",
        side_effect=RuntimeError("sensitive-address"),
    ):
        assert_missing_url_readiness()
    database, shared = ready_dependencies
    database.assert_not_called()
    shared.assert_not_called()


def test_readiness_configuration_failure_precedes_url_check(ready_dependencies):
    with (
        patch(
            "agentcc.readiness.validate_hosted_configuration",
            side_effect=ImproperlyConfigured("sensitive-configuration"),
        ),
        patch("agentcc.readiness.hosted_gateway_public_url_missing") as missing,
    ):
        response = readiness_response()
    assert response.status_code == 503
    assert json.loads(response.content) == {
        "status": "not_ready",
        "reason": "configuration",
    }
    assert response["Cache-Control"] == "no-store"
    missing.assert_not_called()
    database, shared = ready_dependencies
    database.assert_not_called()
    shared.assert_not_called()


@pytest.mark.parametrize(
    "deployment,public_url,legacy_url,expected",
    [
        ("", None, None, "http://localhost:8090/v1"),
        ("false", None, "http://legacy-gateway:8090", "http://legacy-gateway:8090/v1"),
        ("", "http://localhost:9090/", None, "http://localhost:9090/v1"),
        (
            "US",
            "https://gateway.futureagi.com/",
            None,
            "https://gateway.futureagi.com/v1",
        ),
        ("EU", None, None, ""),
        ("DEV", "", "http://legacy-gateway:8090", ""),
        ("US", " ", "https://legacy.example.com", ""),
        (
            "EU",
            "https://gateway.eu.example.com",
            "https://legacy.example.com",
            "https://gateway.eu.example.com/v1",
        ),
    ],
)
def test_dashboard_displays_the_selected_url(
    monkeypatch, deployment, public_url, legacy_url, expected
):
    from agentcc.serializers.contracts import GatewaySummaryResultSerializer
    from agentcc.services import gateway_client
    from agentcc.views import gateway

    previous_public_url = gateway.AGENTCC_GATEWAY_PUBLIC_URL
    previous_legacy_url = gateway.AGENTCC_GATEWAY_URL
    monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
    if public_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_PUBLIC_URL", public_url)
    if legacy_url is not None:
        monkeypatch.setenv("AGENTCC_GATEWAY_URL", legacy_url)
    monkeypatch.setattr(
        gateway_client,
        "AGENTCC_GATEWAY_URL",
        gateway_client.resolve_gateway_public_url(),
    )
    try:
        start_agentcc()
        importlib.reload(gateway)
        data = gateway._build_virtual_gateway()
        assert data["base_url"] == expected
        assert data["status"] == ("healthy" if expected else "unconfigured")
        if deployment in {"US", "EU", "DEV"}:
            # Validate the changed hosted contract without imposing URLField's
            # public-DNS rules on legacy single-label self-hosted hostnames.
            contract = GatewaySummaryResultSerializer(data=data)
            assert contract.is_valid(), contract.errors
    finally:
        gateway.AGENTCC_GATEWAY_PUBLIC_URL = previous_public_url
        gateway.AGENTCC_GATEWAY_URL = previous_legacy_url


@pytest.mark.parametrize("action", ["list", "retrieve"])
@pytest.mark.parametrize("reachable", [True, False])
def test_dashboard_missing_url_cannot_be_hidden_by_internal_health(
    monkeypatch, action, reachable
):
    from types import SimpleNamespace

    from agentcc.serializers.contracts import (
        GatewayDetailResponseSerializer,
        GatewayListResponseSerializer,
    )
    from agentcc.views import gateway

    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    request = APIRequestFactory().get("/agentcc/gateways/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with patch.object(gateway, "get_gateway_client") as get_client:
        if reachable:
            get_client.return_value.health_check.return_value = {
                "status": "ok",
                "base_url": "http://localhost:8090/v1",
            }
        else:
            get_client.return_value.health_check.side_effect = (
                gateway.GatewayClientError("offline")
            )
        response = gateway.AgentccGatewayViewSet.as_view({"get": action})(request)
    assert response.status_code == 200
    result = response.data["result"]
    data = result[0] if action == "list" else result
    assert data["base_url"] == ""
    assert data["status"] == "unconfigured"
    serializer = (
        GatewayListResponseSerializer
        if action == "list"
        else GatewayDetailResponseSerializer
    )
    contract = serializer(data=response.data)
    assert contract.is_valid(), contract.errors
