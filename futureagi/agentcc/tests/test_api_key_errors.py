"""R19i API-key handler census and B31 truthful gateway status classes."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.r19i_error_support import (
    SENTINEL,
    assert_private,
    assert_unexpected,
    block_network,
)
from agentcc.views import api_key as module


def invoke(action, failure):
    view = module.AgentccAPIKeyViewSet()
    request = SimpleNamespace(data={}, organization=SimpleNamespace(id="org"))
    if action == "revoke":
        # Revoke validates a real DB boundary before get_object. Establish its
        # connection before blocking sockets; never stub the boundary check.
        module.require_key_transaction_boundary()
    with ExitStack() as stack:
        block_network(stack)
        stack.enter_context(
            patch("accounts.api_key_policy.require_personal_key_session")
        )
        logger = stack.enter_context(patch.object(module, "logger"))
        if action == "create":
            injected = stack.enter_context(
                patch.object(
                    module, "AgentccAPIKeyCreateSerializer", side_effect=failure
                )
            )
        elif action == "sync":
            injected = stack.enter_context(
                patch.object(module.auth_bridge, "sync_keys", side_effect=failure)
            )
        else:
            seam = "get_queryset" if action == "list" else "get_object"
            injected = stack.enter_context(
                patch.object(view, seam, side_effect=failure)
            )
        response = getattr(view, action)(request)
        injected.assert_called_once()
    return response, logger


@pytest.mark.parametrize(
    "action",
    [
        "list",
        "create",
        "partial_update",
        "destroy",
        pytest.param("revoke", marks=pytest.mark.django_db(transaction=True)),
        "sync",
    ],
)
def test_unexpected_key_failure_is_private(action):
    if action in {"create", "revoke"}:
        synthetic_key = f"synthetic-test-key-{action}"
        failure = RuntimeError(f"{SENTINEL} {synthetic_key}")
        response, logger = invoke(action, failure)
        body = assert_private(response, 500)
        assert "Something went wrong. Reference: agentcc_internal_error" in body
        assert response.data["code"] == "agentcc_internal_error"
        assert response.data["result"] == (
            "Something went wrong. Reference: agentcc_internal_error"
        )
        assert synthetic_key not in body
        logger.exception.assert_not_called()
        # Exact calls exclude exc_info, exception text, key material and extra logs.
        assert logger.mock_calls == [
            call.warning(
                f"api_key_{action}_error", error_type="RuntimeError", status_code=None
            )
        ]
        assert SENTINEL not in repr(logger.mock_calls)
        for logged_call in logger.mock_calls:
            assert synthetic_key not in repr(logged_call.args)
            assert synthetic_key not in repr(logged_call.kwargs)
        return
    failure = RuntimeError(SENTINEL)
    response, logger = invoke(action, failure)
    assert_unexpected(response, logger, failure)


@pytest.mark.parametrize("action", ["create", "partial_update", "sync"])
@pytest.mark.parametrize("upstream", [502, 503, 504])
def test_gateway_outage_is_not_blame_assigned_to_the_caller(action, upstream):
    response, _ = invoke(action, GatewayClientError(SENTINEL, status_code=upstream))
    body = assert_private(response, 503)
    assert "temporarily unavailable" in body and "retry" in body
    assert response.data["code"] == "gateway_unavailable"


@pytest.mark.parametrize("action", ["create", "partial_update", "sync"])
@pytest.mark.parametrize("upstream", [400, 401, 403, 404, 409, 422, 429])
def test_gateway_caller_error_keeps_status_without_echoing_body(action, upstream):
    response, _ = invoke(
        action, GatewayClientError(SENTINEL, status_code=upstream, error_code=SENTINEL)
    )
    body = assert_private(response, upstream)
    assert any(
        remedy in body for remedy in ("settings", "administrator", "Refresh", "retry")
    )


@pytest.mark.parametrize(
    "action",
    [
        "create",
        "partial_update",
        "destroy",
        pytest.param("revoke", marks=pytest.mark.django_db(transaction=True)),
        "sync",
    ],
)
def test_normalized_gateway_configuration_failure_keeps_its_remedy(action):
    with ExitStack() as stack:
        block_network(stack)
        stack.enter_context(
            patch(
                "agentcc.services.gateway_client.httpx.Client",
                side_effect=ValueError(SENTINEL),
            )
        )
        with pytest.raises(ValueError) as caught:
            GatewayClient("https://gateway.invalid")._request("POST", "/-/keys")
    response, logger = invoke(action, caught.value)
    body = assert_private(response, 500)
    assert response.data["code"] == "gateway_configuration_invalid"
    assert "administrator" in body and "proxy" in body
    assert SENTINEL not in repr(logger.mock_calls)
    logger.exception.assert_not_called()


def test_intentional_person_membership_validation_remains_actionable():
    message = "A gateway key requires a person with live organization membership"
    response, _ = invoke("create", ValueError(message))
    assert response.status_code == 400
    assert response.data["result"] == message
