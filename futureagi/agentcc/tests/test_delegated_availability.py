"""Console calls explain their availability before contacting the gateway.

A delegated call is offered when this deployment's switch is on (unset means
on) and the gateway's GET /-/config "delegated" report says it serves it. The
autouse ``gateway_serves_delegated_calls`` fixture (agentcc/tests/conftest.py)
leaves this module's report cache empty, so each test sets the report itself.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.services import delegated_availability
from agentcc.services.delegated_availability import (
    BATCH_SINGLE_REPLICA_MESSAGE,
    batch_needs_single_replica,
    delegated_available,
    delegated_enabled,
    gateway_delegated_report,
    unavailable_response,
)
from agentcc.services.gateway_client import GatewayClientError

SWITCHES = ("AGENTCC_DELEGATED_PLAYGROUND_ENABLED", "AGENTCC_DELEGATED_BATCH_ENABLED")
SERVES_BOTH = {"playground": True, "batch": True, "cluster": False}
# A gateway cluster with a shared nonce store: Try it yes, batch no.
CLUSTER = {"playground": True, "batch": False, "cluster": True}
# An older gateway: GET /-/config has no "delegated" block.
OLDER_GATEWAY_CONFIG = {"routing": {"strategy": "round_robin"}}
SINGLE_REPLICA = "Batch jobs need a single gateway replica, or a cluster whose replicas share a Redis store, in this version."


def _config(report):
    return {"routing": {}, "delegated": dict(report)}


def _client(config):
    client = MagicMock(name="gateway_client")
    client.get_config.return_value = config
    return client


@pytest.fixture
def switches_unset(settings, monkeypatch):
    for name in SWITCHES:
        monkeypatch.delenv(name, raising=False)
        if hasattr(settings, name):
            delattr(settings, name)


# --- the switches -----------------------------------------------------------


def test_switches_are_on_when_unset(switches_unset):
    assert delegated_enabled() is True
    assert delegated_enabled(batch=True) is True


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "source,value,enabled",
    [
        ("env", "false", False),
        ("env", "true", True),
        ("settings", False, False),
        ("settings", "false", False),
        ("settings", True, True),
        ("settings", "true", True),
    ],
)
def test_only_an_explicit_false_turns_a_switch_off(
    switches_unset, settings, monkeypatch, batch, source, value, enabled
):
    name = SWITCHES[1] if batch else SWITCHES[0]
    if source == "env":
        monkeypatch.setenv(name, value)
    else:
        setattr(settings, name, value)
    assert delegated_enabled(batch=batch) is enabled
    # The other switch stays on.
    assert delegated_enabled(batch=not batch) is True


# --- the gateway's report ---------------------------------------------------


def test_report_is_read_from_gateway_config_and_cached():
    client = _client(_config(CLUSTER))
    assert gateway_delegated_report(client) == CLUSTER
    assert gateway_delegated_report(client) == CLUSTER
    # The second answer came from the cache.
    client.get_config.assert_called_once_with()


def test_report_cache_hit_builds_no_client():
    gateway_delegated_report(_client(_config(SERVES_BOTH)))
    with patch("agentcc.services.gateway_client.get_gateway_client") as factory:
        assert gateway_delegated_report() == SERVES_BOTH
    factory.assert_not_called()


def test_report_miss_without_client_builds_one():
    client = _client(_config(SERVES_BOTH))
    with patch(
        "agentcc.services.gateway_client.get_gateway_client", return_value=client
    ) as factory:
        assert gateway_delegated_report() == SERVES_BOTH
    factory.assert_called_once_with()


def test_older_gateway_reports_nothing_and_that_is_cached():
    client = _client(OLDER_GATEWAY_CONFIG)
    assert gateway_delegated_report(client) is None
    assert gateway_delegated_report(client) is None
    client.get_config.assert_called_once_with()


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"delegated": None}, id="null-block"),
        pytest.param({"delegated": []}, id="list-block"),
        pytest.param(
            {"delegated": {"playground": True, "batch": True}}, id="no-cluster"
        ),
        pytest.param(
            {"delegated": {"playground": "true", "batch": True, "cluster": False}},
            id="string-bool",
        ),
        pytest.param(
            {"delegated": {"playground": 1, "batch": True, "cluster": False}},
            id="int-bool",
        ),
        pytest.param([], id="list-config"),
        pytest.param("ok", id="string-config"),
        pytest.param(None, id="null-config"),
    ],
)
def test_malformed_report_is_no_report(config):
    assert gateway_delegated_report(_client(config)) is None


def test_report_keeps_only_the_three_fields():
    report = {**SERVES_BOTH, "nonce_store": "shared"}
    assert gateway_delegated_report(_client({"delegated": report})) == SERVES_BOTH


@pytest.mark.parametrize(
    "error",
    [
        GatewayClientError("Cannot connect to gateway"),
        GatewayClientError("Gateway returned 503.", status_code=503),
        ValueError("Invalid gateway client configuration"),
        RuntimeError("unexpected"),
    ],
    ids=["unreachable", "http-error", "value-error", "any-error"],
)
def test_report_read_failure_never_raises(error):
    client = MagicMock(name="gateway_client")
    client.get_config.side_effect = error
    with patch.object(delegated_availability, "logger") as logger:
        assert gateway_delegated_report(client) is None
    # Only the error type is logged, never upstream text.
    logger.warning.assert_called_once_with(
        "gateway_delegated_report_failed", error_type=type(error).__name__
    )
    # The failed read is cached like an absent report.
    assert gateway_delegated_report(client) is None
    client.get_config.assert_called_once_with()


def test_client_construction_failure_never_raises():
    with patch(
        "agentcc.services.gateway_client.get_gateway_client",
        side_effect=ValueError("Invalid gateway client configuration"),
    ):
        assert gateway_delegated_report() is None


def test_cache_failure_never_raises_and_still_reads_the_gateway():
    client = _client(_config(SERVES_BOTH))
    broken = MagicMock(name="cache")
    broken.get.side_effect = ConnectionError("cache down")
    broken.set.side_effect = ConnectionError("cache down")
    with patch.object(delegated_availability, "cache", broken):
        assert gateway_delegated_report(client) == SERVES_BOTH
    client.get_config.assert_called_once_with()


# --- availability from switch and report -------------------------------------


@pytest.mark.parametrize(
    "report,playground,batch",
    [
        (SERVES_BOTH, True, True),
        (CLUSTER, True, False),
        ({"playground": False, "batch": False, "cluster": True}, False, False),
        (None, False, False),
    ],
    ids=["single-gateway", "cluster", "cluster-no-shared-nonce", "older-gateway"],
)
def test_available_needs_switch_and_report(switches_unset, report, playground, batch):
    assert delegated_available(report=report) is playground
    assert delegated_available(batch=True, report=report) is batch


@pytest.mark.parametrize("batch", [False, True])
def test_switch_off_is_unavailable_whatever_the_gateway_reports(settings, batch):
    setattr(settings, SWITCHES[1] if batch else SWITCHES[0], False)
    assert delegated_available(batch=batch, report=SERVES_BOTH) is False


def test_batch_needs_single_replica_only_for_an_on_switch_and_a_cluster(
    switches_unset, settings
):
    assert batch_needs_single_replica(CLUSTER) is True
    assert batch_needs_single_replica(SERVES_BOTH) is False
    assert batch_needs_single_replica(None) is False
    settings.AGENTCC_DELEGATED_BATCH_ENABLED = False
    assert batch_needs_single_replica(CLUSTER) is False


def test_refusal_names_the_single_replica_limit_for_a_cluster(switches_unset):
    response = unavailable_response(batch=True, report=CLUSTER)
    assert response.status_code == 409
    assert response.data["code"] == "feature_unavailable_733"
    assert response.data["known_issue"] == "KI-733-BATCH"
    assert response.data["message"] == SINGLE_REPLICA
    assert BATCH_SINGLE_REPLICA_MESSAGE == SINGLE_REPLICA


@pytest.mark.parametrize(
    "batch,report",
    [(True, None), (True, SERVES_BOTH), (False, CLUSTER), (False, None)],
    ids=["batch-older-gateway", "batch-single", "playground-cluster", "playground"],
)
def test_other_refusals_keep_todays_text(switches_unset, batch, report):
    response = unavailable_response(batch=batch, report=report)
    assert response.status_code == 409
    assert response.data == unavailable_response(batch=batch).data
    assert response.data["message"].startswith("Unavailable in this release:")
    assert response.data["known_issue"] == ("KI-733-BATCH" if batch else "KI-733-PG")


def test_switched_off_batch_on_a_cluster_says_it_is_off(settings):
    settings.AGENTCC_DELEGATED_BATCH_ENABLED = False
    response = unavailable_response(batch=True, report=CLUSTER)
    assert response.data["message"] != SINGLE_REPLICA
    assert "turned off on this deployment" in response.data["message"]


# --- the gateway read (list / retrieve) --------------------------------------


def _gateway_read(monkeypatch, action, client):
    from agentcc.views import gateway

    # A public URL the response contract's URLField accepts.
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setattr(
        gateway, "AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com"
    )
    request = APIRequestFactory().get("/agentcc/gateways/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with patch.object(gateway, "get_gateway_client", return_value=client):
        response = gateway.AgentccGatewayViewSet.as_view({"get": action})(request)
    assert response.status_code == 200
    result = response.data["result"]
    return result[0] if action == "list" else result


@pytest.mark.parametrize("action", ["list", "retrieve"])
@pytest.mark.parametrize("enabled", [False, True])
def test_gateway_read_says_whether_console_tests_run(monkeypatch, action, enabled):
    """The console reads this before offering Try it or Guardrail test."""
    from agentcc.serializers.contracts import (
        GatewayDetailResponseSerializer,
        GatewayListResponseSerializer,
    )
    from agentcc.views import gateway

    # A public URL the response contract's URLField accepts.
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setattr(
        gateway, "AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com"
    )
    request = APIRequestFactory().get("/agentcc/gateways/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with (
        override_settings(AGENTCC_DELEGATED_PLAYGROUND_ENABLED=enabled),
        patch.object(gateway, "get_gateway_client") as get_client,
    ):
        # A gateway health field of the same name cannot stand in for it.
        get_client.return_value.health_check.return_value = {
            "status": "ok",
            "delegated_playground_available": not enabled,
        }
        get_client.return_value.get_config.return_value = _config(SERVES_BOTH)
        response = gateway.AgentccGatewayViewSet.as_view({"get": action})(request)
    assert response.status_code == 200
    result = response.data["result"]
    data = result[0] if action == "list" else result
    assert data["delegated_playground_available"] is enabled
    serializer = (
        GatewayListResponseSerializer
        if action == "list"
        else GatewayDetailResponseSerializer
    )
    contract = serializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert (
        contract.validated_data["result"][0]
        if action == "list"
        else contract.validated_data["result"]
    )["delegated_playground_available"] is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_gateway_read_says_whether_batch_submission_runs(monkeypatch, enabled):
    """The console reads this before offering Submit Batch Job."""
    from agentcc.serializers.contracts import GatewayDetailResponseSerializer
    from agentcc.views import gateway

    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setattr(
        gateway, "AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com"
    )
    request = APIRequestFactory().get("/agentcc/gateways/default/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with (
        override_settings(
            AGENTCC_DELEGATED_BATCH_ENABLED=enabled,
            AGENTCC_DELEGATED_PLAYGROUND_ENABLED=not enabled,
        ),
        patch.object(gateway, "get_gateway_client") as get_client,
    ):
        # A gateway health field of the same name cannot stand in for it.
        get_client.return_value.health_check.return_value = {
            "status": "ok",
            "delegated_batch_available": not enabled,
        }
        get_client.return_value.get_config.return_value = _config(SERVES_BOTH)
        response = gateway.AgentccGatewayViewSet.as_view({"get": "retrieve"})(request)
    assert response.status_code == 200
    data = response.data["result"]
    assert data["delegated_batch_available"] is enabled
    assert data["delegated_playground_available"] is (not enabled)
    contract = GatewayDetailResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert contract.validated_data["result"]["delegated_batch_available"] is enabled


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_gateway_read_offers_both_by_default_on_a_serving_gateway(
    switches_unset, monkeypatch, action
):
    client = _client(_config(SERVES_BOTH))
    client.health_check.return_value = {"status": "ok"}
    data = _gateway_read(monkeypatch, action, client)
    assert data["delegated_playground_available"] is True
    assert data["delegated_batch_available"] is True


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_gateway_read_offers_nothing_on_an_older_gateway(
    switches_unset, monkeypatch, action
):
    client = _client(OLDER_GATEWAY_CONFIG)
    client.health_check.return_value = {"status": "ok"}
    data = _gateway_read(monkeypatch, action, client)
    assert data["delegated_playground_available"] is False
    assert data["delegated_batch_available"] is False


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_gateway_read_on_a_cluster_offers_try_it_but_not_batch(
    switches_unset, monkeypatch, action
):
    client = _client(_config(CLUSTER))
    client.health_check.return_value = {"status": "ok"}
    data = _gateway_read(monkeypatch, action, client)
    assert data["delegated_playground_available"] is True
    assert data["delegated_batch_available"] is False


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_gateway_read_failing_report_offers_nothing_and_still_answers(
    switches_unset, monkeypatch, action
):
    client = _client(None)
    client.health_check.return_value = {"status": "ok"}
    client.get_config.side_effect = GatewayClientError("Cannot connect to gateway")
    data = _gateway_read(monkeypatch, action, client)
    assert data["status"] == "healthy"
    assert data["delegated_playground_available"] is False
    assert data["delegated_batch_available"] is False


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_unreachable_gateway_is_not_asked_what_it_serves(
    switches_unset, monkeypatch, action
):
    client = _client(_config(SERVES_BOTH))
    client.health_check.side_effect = GatewayClientError("Cannot connect to gateway")
    data = _gateway_read(monkeypatch, action, client)
    assert data["status"] == "unreachable"
    assert data["delegated_playground_available"] is False
    assert data["delegated_batch_available"] is False
    client.get_config.assert_not_called()


# --- the refusal sites -------------------------------------------------------


@pytest.mark.parametrize("batch", [False, True])
def test_refusal_helper_reads_the_cached_report_only_when_switched_on(
    switches_unset, settings, batch
):
    from agentcc.views.gateway import _delegated_refusal

    client = _client(_config(SERVES_BOTH))
    with patch(
        "agentcc.services.gateway_client.get_gateway_client", return_value=client
    ) as factory:
        setattr(settings, SWITCHES[1] if batch else SWITCHES[0], False)
        off = _delegated_refusal(batch=batch)
        factory.assert_not_called()
        setattr(settings, SWITCHES[1] if batch else SWITCHES[0], True)
        assert _delegated_refusal(batch=batch) is None
        assert _delegated_refusal(batch=batch) is None
    assert off.status_code == 409
    assert off.data == unavailable_response(batch=batch).data
    # One config read for both on-calls: the second came from the cache.
    factory.assert_called_once_with()
    client.get_config.assert_called_once_with()


def test_refusal_helper_on_a_cluster_refuses_batch_but_not_try_it(switches_unset):
    from agentcc.views.gateway import _delegated_refusal

    with patch(
        "agentcc.services.gateway_client.get_gateway_client",
        return_value=_client(_config(CLUSTER)),
    ):
        assert _delegated_refusal() is None
        refused = _delegated_refusal(batch=True)
    assert refused.status_code == 409
    assert refused.data["code"] == "feature_unavailable_733"
    assert refused.data["known_issue"] == "KI-733-BATCH"
    assert refused.data["message"] == SINGLE_REPLICA


DELEGATED_ROUTES = [
    ("test-playground", {"prompt": "hello", "model": "test/model"}, "KI-733-PG"),
    (
        "test-playground-stream",
        {"prompt": "hello", "model": "test/model"},
        "KI-733-PG",
    ),
    (
        "submit-batch",
        {
            "requests": [
                {
                    "custom_id": "one",
                    "body": {
                        "model": "test/model",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                }
            ]
        },
        "KI-733-BATCH",
    ),
]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("route,payload,issue", DELEGATED_ROUTES)
def test_disabled_console_call_has_no_gateway_work(
    auth_client, settings, route, payload, issue
):
    # Both switches are on by default; an operator turns them off explicitly.
    settings.AGENTCC_DELEGATED_PLAYGROUND_ENABLED = False
    settings.AGENTCC_DELEGATED_BATCH_ENABLED = False
    with (
        patch("agentcc.views.gateway.get_gateway_client") as gateway,
        patch("agentcc.services.gateway_client.get_gateway_client") as report_client,
    ):
        response = auth_client.post(
            f"/agentcc/gateways/default/{route}/", payload, format="json"
        )
    assert response.status_code == 409
    assert response.json()["code"] == "feature_unavailable_733"
    assert response.json()["known_issue"] == issue
    assert "Unavailable in this release" in response.json()["message"]
    gateway.assert_not_called()
    report_client.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("route,payload,issue", DELEGATED_ROUTES)
def test_older_gateway_refuses_console_call_before_any_other_gateway_work(
    auth_client, switches_unset, route, payload, issue
):
    with (
        patch("agentcc.views.gateway.get_gateway_client") as gateway,
        patch(
            "agentcc.services.gateway_client.get_gateway_client",
            return_value=_client(OLDER_GATEWAY_CONFIG),
        ),
    ):
        response = auth_client.post(
            f"/agentcc/gateways/default/{route}/", payload, format="json"
        )
    assert response.status_code == 409
    assert response.json()["code"] == "feature_unavailable_733"
    assert response.json()["known_issue"] == issue
    assert "Unavailable in this release" in response.json()["message"]
    gateway.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
def test_cluster_gateway_refuses_batch_submission_with_the_single_replica_reason(
    auth_client, switches_unset
):
    route, payload, _ = DELEGATED_ROUTES[2]
    with (
        patch("agentcc.views.gateway.get_gateway_client") as gateway,
        patch(
            "agentcc.services.gateway_client.get_gateway_client",
            return_value=_client(_config(CLUSTER)),
        ),
    ):
        response = auth_client.post(
            f"/agentcc/gateways/default/{route}/", payload, format="json"
        )
    assert response.status_code == 409
    assert response.json()["code"] == "feature_unavailable_733"
    assert response.json()["known_issue"] == "KI-733-BATCH"
    assert response.json()["message"] == SINGLE_REPLICA
    gateway.assert_not_called()
