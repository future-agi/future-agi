"""Actual Django/requests/agentcc exception classes against the shared helper."""

from contextlib import ExitStack
from unittest.mock import Mock, patch

import pytest
import requests
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, OperationalError, ProgrammingError
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.exceptions import ValidationError as DRFValidationError

from agentcc.services.auth_bridge import GatewayKeyIdCollision
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.r19i_error_support import SENTINEL, assert_private, block_network
from agentcc.views._errors import exception_response
from tfc.utils.general_methods import GeneralMethods


@pytest.mark.parametrize(
    "error,status,remedy",
    [
        pytest.param(
            ValidationError(SENTINEL), 400, "submitted", id="django-validation"
        ),
        pytest.param(
            DRFValidationError({"name": SENTINEL}),
            400,
            "submitted",
            id="drf-validation",
        ),
        pytest.param(ParseError(SENTINEL), 400, "syntax", id="parse-error"),
        pytest.param(
            UnsupportedMediaType(SENTINEL),
            415,
            "Content-Type",
            id="unsupported-media-type",
        ),
        pytest.param(IntegrityError(SENTINEL), 409, "Refresh", id="integrity"),
        pytest.param(DataError(SENTINEL), 400, "format", id="data-error"),
        pytest.param(
            OperationalError(SENTINEL), 503, "retry", id="database-unavailable"
        ),
        pytest.param(
            GatewayKeyIdCollision(SENTINEL), 503, "upgrade", id="key-id-collision"
        ),
        pytest.param(
            OrgConfigRejected(SENTINEL), 400, "configuration", id="org-config-rejected"
        ),
        pytest.param(
            requests.exceptions.InvalidURL(SENTINEL), 400, "URL", id="invalid-url"
        ),
        pytest.param(
            requests.exceptions.MissingSchema(SENTINEL), 400, "URL", id="missing-schema"
        ),
        pytest.param(
            requests.exceptions.JSONDecodeError(SENTINEL, "{}", 0),
            502,
            "response",
            id="json-decode",
        ),
        pytest.param(
            requests.exceptions.SSLError(SENTINEL), 502, "certificate", id="tls"
        ),
    ],
)
def test_known_failure_has_sanitized_actionable_copy(error, status, remedy):
    response = exception_response(
        GeneralMethods(), error, logger=Mock(), event="test_error"
    )
    assert remedy in assert_private(response, status)
    assert response.data["code"] != "agentcc_internal_error"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_real_gateway_client_error_uses_server_class(status):
    response = exception_response(
        GeneralMethods(),
        GatewayClientError(SENTINEL, status_code=status, error_code=SENTINEL),
        logger=Mock(),
        event="gateway_error",
    )
    assert "retry" in assert_private(response, 503)
    assert response.data["code"] == "gateway_unavailable"
    assert response.data["type"] == "service_unavailable"


@pytest.mark.parametrize(
    "error",
    [
        ValidationError({"name": ValidationError(SENTINEL, code="required")}),
        DRFValidationError({"name": SENTINEL}, code="required"),
    ],
    ids=["django", "drf"],
)
def test_validation_code_keeps_a_specific_remedy_without_the_value(error):
    response = exception_response(
        GeneralMethods(), error, logger=Mock(), event="test_error"
    )
    assert "Complete all required fields" in assert_private(response, 400)


def test_key_ownership_validation_keeps_its_original_remedy():
    from agentcc.models.api_key_ownership import IMMUTABLE_MESSAGE

    error = ValidationError({"user": IMMUTABLE_MESSAGE})
    response = exception_response(
        GeneralMethods(), error, logger=Mock(), event="test_error"
    )
    assert response.status_code == 400
    assert response.data["result"] == IMMUTABLE_MESSAGE


@pytest.mark.parametrize(
    "failure",
    [ProgrammingError(SENTINEL), PermissionError(SENTINEL)],
    ids=["database-programming", "filesystem-permission"],
)
def test_unexpected_platform_failures_are_not_caller_errors(failure):
    logger = Mock()
    response = exception_response(
        GeneralMethods(), failure, logger=logger, event="test_error"
    )
    assert "Reference" in assert_private(response, 500)
    assert logger.exception.call_args.kwargs["exc_info"][1] is failure


def test_contract_validation_does_not_echo_pydantic_input_values():
    from pydantic import BaseModel
    from pydantic import ValidationError as SchemaValidationError

    class Contract(BaseModel):
        count: int

    with pytest.raises(SchemaValidationError) as caught:
        Contract(count=SENTINEL)
    response = exception_response(
        GeneralMethods(), caught.value, logger=Mock(), event="test_error"
    )
    assert "field types" in assert_private(response, 400)


@pytest.mark.parametrize(
    "message",
    [SENTINEL, "Invalid gateway client configuration " + SENTINEL],
    ids=["arbitrary", "configuration-prefix-with-private-detail"],
)
def test_arbitrary_value_error_is_not_a_validation_error(message):
    logger = Mock()
    failure = ValueError(message)
    response = exception_response(
        GeneralMethods(), failure, logger=logger, event="test_error"
    )
    assert "Reference" in assert_private(response, 500)
    assert logger.exception.call_args.kwargs["exc_info"][1] is failure


@pytest.mark.parametrize("real_proxy_validation", [False, True])
def test_gateway_client_constructor_failure_has_configuration_remedy(
    real_proxy_validation,
):
    """Exercise the actual _request normalization, not a made-up helper input."""
    logger = Mock()
    with ExitStack() as stack:
        block_network(stack)
        if real_proxy_validation:
            # HTTPX rejects ftp proxies before any I/O; never inherit host proxy
            # configuration (which can include real operator credentials).
            proxy = "ftp://synthetic-user:synthetic-password@proxy.invalid:3128"
            stack.enter_context(
                patch.dict(
                    "os.environ",
                    {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy},
                    clear=True,
                )
            )
        else:
            stack.enter_context(
                patch(
                    "agentcc.services.gateway_client.httpx.Client",
                    side_effect=ValueError(SENTINEL),
                )
            )
        with pytest.raises(ValueError) as caught:
            GatewayClient("https://gateway.invalid")._request("POST", "/-/keys")
        assert str(caught.value) == "Invalid gateway client configuration"
        assert caught.value.__suppress_context__
        response = exception_response(
            GeneralMethods(), caught.value, logger=logger, event="api_key_create_error"
        )
    body = assert_private(response, 500)
    assert response.data["code"] == "gateway_configuration_invalid"
    for remedy in ("administrator", "URL", "proxy", "protocol"):
        assert remedy in body
    logger.exception.assert_not_called()
    logger.warning.assert_called_once_with(
        "api_key_create_error", error_type="ValueError", status_code=None
    )
    for private in (SENTINEL, "synthetic-user", "synthetic-password", "proxy.invalid"):
        assert private not in body
        assert private not in repr(logger.mock_calls)
