"""R19i provider handlers, including requests' ValueError subclasses."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from agentcc.services.url_safety import PROVIDER_PRIVATE_URL_ERROR, PROVIDER_URL_ERROR
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.tests.r19i_error_support import (
    SENTINEL,
    assert_private,
    assert_unexpected,
    block_network,
)
from agentcc.views import provider_credential as module
from django.db import IntegrityError


@pytest.mark.parametrize(
    "action", ["list", "create", "partial_update", "destroy", "rotate"]
)
def test_unexpected_provider_failure_is_private(action):
    view = module.AgentccProviderCredentialViewSet()
    request = SimpleNamespace(data={}, organization=SimpleNamespace(id="org"))
    seam = (
        "get_queryset"
        if action == "list"
        else "get_serializer"
        if action == "create"
        else "get_object"
    )
    failure = RuntimeError(SENTINEL)
    with ExitStack() as stack:
        block_network(stack)
        if action == "rotate":
            request.data = {"credentials": {"api_key": "synthetic-key"}}
        if action in {"rotate", "partial_update"}:
            request.organization.pk = "org"
            atomic = stack.enter_context(patch.object(module.transaction, "atomic"))
            org_lock = stack.enter_context(patch.object(module, "lock_org"))
        stack.enter_context(patch.object(view, seam, side_effect=failure))
        logger = stack.enter_context(patch.object(module, "logger"))
        response = getattr(view, action)(request)
    assert_unexpected(response, logger, failure)
    if action in {"rotate", "partial_update"}:
        atomic.assert_called_once_with()
        org_lock.assert_called_once_with("org")
        assert "synthetic-key" not in repr(response.data)


def test_rotate_without_credentials_is_a_private_bad_request():
    view = module.AgentccProviderCredentialViewSet()
    request = SimpleNamespace(data={}, organization=SimpleNamespace(id="org", pk="org"))
    with ExitStack() as stack:
        block_network(stack)
        atomic = stack.enter_context(patch.object(module.transaction, "atomic"))
        org_lock = stack.enter_context(patch.object(module, "lock_org"))
        get_object = stack.enter_context(patch.object(view, "get_object"))
        decrypt = stack.enter_context(patch.object(module.CredentialManager, "decrypt"))
        encrypt = stack.enter_context(patch.object(module.CredentialManager, "encrypt"))
        push = stack.enter_context(patch.object(view, "_push_config_to_gateway"))
        logger = stack.enter_context(patch.object(module, "logger"))
        response = view.rotate(request)
    assert_private(response, 400)
    assert response.data["result"] == (
        "credentials must be a JSON object with at least an 'api_key' field"
    )
    atomic.assert_not_called()
    org_lock.assert_not_called()
    get_object.assert_not_called()
    decrypt.assert_not_called()
    encrypt.assert_not_called()
    push.assert_not_called()
    logger.exception.assert_not_called()


def test_duplicate_provider_has_an_actionable_conflict():
    view = module.AgentccProviderCredentialViewSet()
    with (
        patch.object(view, "get_serializer", side_effect=IntegrityError(SENTINEL)),
        patch.object(module, "logger"),
    ):
        response = view.create(SimpleNamespace(data={}))
    assert "Refresh" in assert_private(response, 409)


@pytest.mark.parametrize("action", ["create", "partial_update"])
def test_narrow_url_validation_keeps_the_actionable_message(action):
    view = module.AgentccProviderCredentialViewSet()
    request = SimpleNamespace(data={}, organization=SimpleNamespace(id="org", pk="org"))
    with (
        patch.object(module.transaction, "atomic"),
        patch.object(module, "lock_org"),
        patch.object(view, "get_object"),
        patch.object(view, "get_serializer") as serializer,
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(
            module,
            "ensure_provider_base_url_allowed",
            side_effect=ValueError(PROVIDER_PRIVATE_URL_ERROR),
        ),
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = None
        serializer.return_value.is_valid.return_value = True
        serializer.return_value.validated_data = {"provider_name": "test"}
        response = getattr(view, action)(request)
    assert response.status_code == 400
    assert response.data["result"] == PROVIDER_PRIVATE_URL_ERROR


FETCH_FAILURES = [
    pytest.param(lambda: ValueError(SENTINEL), 500, "Reference", id="value-error"),
    pytest.param(
        lambda: ConnectionError(SENTINEL), 503, "connection", id="builtin-connection"
    ),
    pytest.param(
        lambda: requests.exceptions.InvalidURL(SENTINEL), 400, "URL", id="invalid-url"
    ),
    pytest.param(
        lambda: requests.exceptions.MissingSchema(SENTINEL),
        400,
        "URL",
        id="missing-schema",
    ),
    pytest.param(
        lambda: requests.exceptions.JSONDecodeError(SENTINEL, "{}", 0),
        502,
        "response",
        id="json-decode",
    ),
    pytest.param(
        lambda: requests.exceptions.ConnectionError(SENTINEL),
        503,
        "connection",
        id="requests-connection",
    ),
    pytest.param(
        lambda: requests.exceptions.Timeout(SENTINEL), 504, "retry", id="timeout"
    ),
    pytest.param(
        lambda: requests.exceptions.InvalidHeader(SENTINEL),
        400,
        "malformed",
        id="invalid-header",
    ),
    pytest.param(lambda: RuntimeError(SENTINEL), 500, "Reference", id="unexpected"),
]


@pytest.mark.parametrize("make_error,status,action", FETCH_FAILURES)
def test_fetch_models_does_not_return_or_log_transport_values(
    make_error, status, action
):
    view = module.AgentccProviderCredentialViewSet()
    request = SimpleNamespace(data={"api_key": "synthetic-key", "api_format": "google"})
    with ExitStack() as stack:
        block_network(stack)
        stack.enter_context(
            patch.object(view, "_fetch_models_from_provider", side_effect=make_error())
        )
        logger = stack.enter_context(patch.object(module, "logger"))
        response = view.fetch_models(request)
    assert action in assert_private(response, status)
    assert SENTINEL not in repr(logger.mock_calls)
    logger.exception.assert_not_called()
    if action != "malformed":
        logger.warning.assert_called_once()
        assert "error_type" in logger.warning.call_args.kwargs
        assert "status_code" in logger.warning.call_args.kwargs


@pytest.mark.parametrize("status", [401, 403])
def test_provider_auth_refusal_keeps_the_discovery_verdict(status):
    """The dialog's onSuccess path blocks a rejected replacement key's Save.

    Only a definitive auth refusal is a discovery result. Transport failures
    must keep reaching onError (no verdict), as the matrix above verifies.
    """
    view = module.AgentccProviderCredentialViewSet()
    upstream = requests.Response()
    upstream.status_code = status
    failure = requests.exceptions.HTTPError(SENTINEL, response=upstream)
    with ExitStack() as stack:
        block_network(stack)
        stack.enter_context(
            patch.object(view, "_fetch_models_from_provider", side_effect=failure)
        )
        logger = stack.enter_context(patch.object(module, "logger"))
        response = view.fetch_models(
            SimpleNamespace(data={"api_key": "synthetic-replacement-key"})
        )
    assert "rotate" in assert_private(response, 200)
    assert response.data["result"]["models"] == []
    assert response.data["result"]["code"] == "provider_authentication_failed"
    assert "permissions" in response.data["result"]["error"]
    logger.exception.assert_not_called()
    logger.warning.assert_called_once_with(
        "fetch_models_error", error_type="HTTPError", status_code=status
    )
    assert SENTINEL not in repr(logger.mock_calls)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_provider_upstream_failure_is_not_a_key_verdict(status):
    view = module.AgentccProviderCredentialViewSet()
    upstream = requests.Response()
    upstream.status_code = status
    failure = requests.exceptions.HTTPError(SENTINEL, response=upstream)
    with ExitStack() as stack:
        block_network(stack)
        stack.enter_context(
            patch.object(view, "_fetch_models_from_provider", side_effect=failure)
        )
        logger = stack.enter_context(patch.object(module, "logger"))
        response = view.fetch_models(SimpleNamespace(data={"api_key": "synthetic-key"}))
    assert "retry" in assert_private(response, 429 if status == 429 else 503)
    assert response.data["code"] != "provider_authentication_failed"
    assert SENTINEL not in repr(logger.mock_calls)
    logger.exception.assert_not_called()


@pytest.mark.parametrize(
    "message",
    [
        PROVIDER_URL_ERROR,
        PROVIDER_PRIVATE_URL_ERROR,
        "Connection to private address blocked",
    ],
    ids=["base-url", "private-url", "ssrf-adapter"],
)
def test_fetch_models_retains_fixed_url_safety_remedies(message):
    view = module.AgentccProviderCredentialViewSet()
    failure = (
        ConnectionError(message)
        if message.startswith("Connection")
        else ValueError(message)
    )
    with (
        patch.object(view, "_fetch_models_from_provider", side_effect=failure),
        patch.object(module, "logger"),
    ):
        response = view.fetch_models(SimpleNamespace(data={"api_key": "synthetic-key"}))
    assert response.status_code == 400
    assert response.data["result"] == message
