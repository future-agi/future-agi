"""Public error boundary for agentcc views; never serialize exception text.

Keep deliberate, narrow serializer/validation responses at their call sites.
At broad boundaries classify by exception TYPE, not exception text. A caller
may supply an allowlist of literal validation messages, never request values.
Imports of Django/application classes stay lazy so the status/copy/logging
contract can also be tested without Django, a database, or network access.
"""

import json
import ssl
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicError:
    status: int
    code: str
    message: str


class ProviderAuthenticationRejected(Exception):
    """A value-free, audited provider-specific HTTP400 credential refusal."""

    status_code = 400


def upstream_error(status, *, service="gateway"):
    """Map an upstream status, never its body or machine-code string.

    Gateway 5xx/no-status use 503, matching API-key deletion's existing
    AGENTCC_KEY_DELETE_GATEWAY_UNREACHABLE response (B31).
    """
    if service not in {"gateway", "provider"}:
        raise ValueError("Unknown upstream service")
    label = "Gateway" if service == "gateway" else "Provider"
    if type(status) is not int or not 400 <= status < 500:
        return PublicError(
            503,
            f"{service}_unavailable",
            f"{label} is temporarily unavailable. Please retry shortly.",
        )
    if service == "provider" and status in (401, 403):
        return PublicError(
            400,
            "provider_authentication_failed",
            "Provider rejected the credential. Check its permissions or rotate it, then retry.",
        )
    messages = {
        401: "Gateway authentication failed. Ask your administrator to check the gateway credentials.",
        403: "Gateway access was refused. Ask your administrator to check the gateway permissions.",
        404: "Resource not found on the gateway. Refresh and check the selected resource before retrying.",
        409: "Gateway resource changed or already exists. Refresh and review it before retrying.",
        429: f"{label} rate limit reached. Wait briefly, then retry.",
    }
    if service == "provider" and status == 404:
        return PublicError(
            400,
            "provider_endpoint_not_found",
            "Provider model endpoint was not found. Check the base URL and API format.",
        )
    message = messages.get(status) if service == "gateway" or status == 429 else None
    return PublicError(
        status if service == "gateway" or status == 429 else 400,
        f"{service}_request_rejected",
        message
        or f"{label} rejected the request. Check the submitted settings and try again.",
    )


def _log_type(exc, *, logger, event):
    """Transport exceptions may contain saved credentials, even in a traceback."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    logger.warning(
        event,
        error_type=type(exc).__name__,
        status_code=status if type(status) is int and 100 <= status <= 599 else None,
    )


def unexpected_error(exc, *, logger, event, log_detail=True):
    error = PublicError(
        500,
        "agentcc_internal_error",
        "Something went wrong. Reference: agentcc_internal_error",
    )
    if log_detail:
        logger.exception(
            event,
            code=error.code,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        # fetch_models' stronger security contract (6e7e54de56): the Google
        # request URL holds ?key=, so NEVER log its exception/chain/traceback.
        _log_type(exc, logger=logger, event=event)
    return error


def _domain_error(exc):
    """Curated messages for agentcc's typed failures, not arbitrary ValueError.

    Deliberate field-level handlers should keep their richer validation copy.
    These are the fallbacks when a typed refusal reaches a broad boundary.
    """
    from agentcc.services.auth_bridge import GatewayKeyIdCollision
    from agentcc.services.cap_policy import CapPolicyRejected
    from agentcc.services.config_push import (
        MCPCredentialPlacementRefused,
        OrgConfigRejected,
        SecretInConfig,
        UnsupportedProviderCredentialFields,
    )
    from agentcc.services.org_config_writer import (
        ConfigVersionUnavailable,
        UnknownConfigSections,
    )
    from agentcc.services.webhook_headers import UnsupportedHeaders

    mappings = (
        (
            GatewayKeyIdCollision,
            503,
            "gateway_key_id_collision",
            "Gateway issued a duplicate key identifier; the new key was discarded. Ask an administrator to upgrade the gateway, then retry.",
        ),
        (
            UnsupportedProviderCredentialFields,
            400,
            "unsupported_provider_credential_fields",
            "Provider credentials contain unsupported fields. Remove fields not supported by the selected provider.",
        ),
        (
            MCPCredentialPlacementRefused,
            400,
            "mcp_credential_placement_unsupported",
            "The gateway cannot send the MCP credential in the requested location. Use a supported credential placement before retrying.",
        ),
        (
            SecretInConfig,
            400,
            "secret_in_config",
            "Secrets cannot be saved in this configuration. Remove secret values and use credential references instead.",
        ),
        (
            ConfigVersionUnavailable,
            409,
            "config_version_unavailable",
            "This configuration version was deleted. Refresh and select an available version.",
        ),
        (
            UnknownConfigSections,
            400,
            "unknown_config_sections",
            "Configuration contains unknown fields. Remove unsupported configuration sections and try again.",
        ),
        (
            CapPolicyRejected,
            400,
            "cap_policy_rejected",
            "The gateway cannot enforce this cap policy. Check the budget and routing limits before retrying.",
        ),
        (
            OrgConfigRejected,
            400,
            "org_config_rejected",
            "Gateway configuration is invalid. Check the submitted configuration fields and try again.",
        ),
        (
            UnsupportedHeaders,
            400,
            "webhook_headers_invalid",
            "The webhook's saved headers are not a JSON object: enter them again.",
        ),
    )
    for cls, status, code, message in mappings:
        if isinstance(exc, cls):
            return PublicError(status, code, message)
    return None


def _validation_error(exc):
    # Keep the ownership validator's actual remedy without copying arbitrary
    # ValidationError parameters (Django can interpolate values into messages).
    from agentcc.models.api_key_ownership import IMMUTABLE_MESSAGE

    detail = getattr(exc, "detail", None)
    if detail is None:
        detail = getattr(exc, "messages", [])

    def leaves(value):
        if isinstance(value, dict):
            return [item for child in value.values() for item in leaves(child)]
        if isinstance(value, (list, tuple)):
            return [item for child in value for item in leaves(child)]
        return [value]

    items = leaves(detail)
    if items and all(str(item) == IMMUTABLE_MESSAGE for item in items):
        return PublicError(400, "key_ownership_immutable", IMMUTABLE_MESSAGE)
    remedies = {
        "required": "Complete all required fields before retrying.",
        "blank": "Required fields cannot be blank. Enter a value before retrying.",
        "null": "Required fields cannot be null. Enter a value before retrying.",
        "invalid_choice": "Choose one of the supported values for the submitted fields.",
        "unique": "A record already uses this value. Choose a different value or update that record.",
        "max_length": "A submitted value is too long. Shorten it and try again.",
        "min_length": "A submitted value is too short. Enter a longer value and try again.",
        "min_value": "A submitted number is below the allowed minimum. Increase it and try again.",
        "max_value": "A submitted number exceeds the allowed maximum. Reduce it and try again.",
    }
    codes = [getattr(item, "code", None) for item in items]
    codes.extend(
        getattr(item, "code", None) for item in leaves(getattr(exc, "error_dict", {}))
    )
    codes.extend(getattr(item, "code", None) for item in getattr(exc, "error_list", []))
    messages = list(
        dict.fromkeys(
            remedies[code]
            for code in codes
            if isinstance(code, str) and code in remedies
        )
    )
    message = (
        " ".join(messages)
        or "One or more fields are invalid. Check the submitted field types, allowed values and required fields."
    )
    return PublicError(400, "invalid", message)


def _known_error(exc, *, safe_messages):
    import httpx
    import requests.exceptions as requests_errors
    from django.core.exceptions import (
        ObjectDoesNotExist,
        PermissionDenied,
        ValidationError,
    )
    from django.db import DataError, IntegrityError, InterfaceError, OperationalError
    from django.http import Http404
    from pydantic import ValidationError as SchemaValidationError
    from rest_framework import exceptions as drf_errors

    from agentcc.services.gateway_client import GatewayClientError

    if isinstance(exc, GatewayClientError):
        return upstream_error(exc.status_code, service="gateway")
    if type(exc) in (ValueError, ConnectionError) and str(exc) in safe_messages:
        return PublicError(400, "invalid", str(exc))
    if type(exc) is ValueError and str(exc) == (
        "A gateway key requires a person with live organization membership"
    ):
        return PublicError(400, "gateway_key_person_required", str(exc))
    if isinstance(
        exc, (ValidationError, drf_errors.ValidationError, SchemaValidationError)
    ):
        return _validation_error(exc)
    if isinstance(exc, drf_errors.ParseError):
        return PublicError(
            400,
            "parse_error",
            "Request body could not be parsed. Check its syntax and Content-Type, then retry.",
        )
    if isinstance(exc, drf_errors.UnsupportedMediaType):
        return PublicError(
            415,
            "unsupported_media_type",
            "The request content type is not supported. Set a supported Content-Type and try again.",
        )
    if isinstance(exc, (Http404, ObjectDoesNotExist, drf_errors.NotFound)):
        return PublicError(
            404,
            "not_found",
            "Resource not found. Refresh and check the selected resource.",
        )
    if isinstance(exc, (PermissionDenied, drf_errors.PermissionDenied)):
        return PublicError(
            403,
            "permission_denied",
            "You do not have permission for this action. Ask an organization administrator for access.",
        )
    if isinstance(exc, (drf_errors.AuthenticationFailed, drf_errors.NotAuthenticated)):
        return PublicError(
            401,
            "not_authenticated",
            "Authentication failed. Sign in again or check your API key.",
        )
    if isinstance(exc, drf_errors.Throttled):
        return PublicError(
            429, "rate_limited", "Too many requests. Wait briefly, then retry."
        )
    if isinstance(exc, IntegrityError):
        return PublicError(
            409,
            "resource_conflict",
            "The change conflicts with an existing or related record. Refresh and check for duplicates or dependent resources before retrying.",
        )
    if isinstance(exc, DataError):
        return PublicError(
            400,
            "invalid_data",
            "A submitted value has an invalid format or length. Check the submitted fields and try again.",
        )
    if isinstance(exc, (OperationalError, InterfaceError)):
        return PublicError(
            503,
            "storage_unavailable",
            "Data storage is temporarily unavailable. Please retry shortly.",
        )
    # JSONDecodeError and InvalidURL also inherit ValueError. Order matters:
    # neither their message nor their document/request belongs in a response.
    if isinstance(
        exc,
        (json.JSONDecodeError, requests_errors.JSONDecodeError, httpx.DecodingError),
    ):
        return PublicError(
            502,
            "upstream_invalid_response",
            "The service returned an invalid response. Check the endpoint and API format, then retry.",
        )
    if isinstance(
        exc,
        (
            requests_errors.InvalidURL,
            requests_errors.MissingSchema,
            requests_errors.InvalidSchema,
        ),
    ):
        return PublicError(
            400,
            "invalid_provider_url",
            "Provider URL is invalid. Enter a complete http(s) base URL and check the API format.",
        )
    if isinstance(exc, requests_errors.InvalidHeader):
        return PublicError(
            400,
            "invalid_provider_header",
            "Provider credential or headers are malformed. Check the credential and remove line breaks before retrying.",
        )
    if isinstance(exc, (requests_errors.SSLError, ssl.SSLError)):
        return PublicError(
            502,
            "upstream_tls_error",
            "The secure connection could not be verified. Ask an administrator to check the service certificate and TLS configuration.",
        )
    if isinstance(exc, (requests_errors.Timeout, httpx.TimeoutException, TimeoutError)):
        return PublicError(
            504,
            "upstream_timeout",
            "The service did not respond in time. Please retry shortly.",
        )
    if isinstance(
        exc,
        (
            requests_errors.ConnectionError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            ConnectionError,
        ),
    ):
        return PublicError(
            503,
            "upstream_connection_failed",
            "The service connection failed. Check availability and retry shortly.",
        )
    if isinstance(exc, ProviderAuthenticationRejected):
        # Use the existing provider-auth copy/adapter, not a raw provider body.
        return upstream_error(401, service="provider")
    if isinstance(exc, requests_errors.HTTPError):
        response = exc.response
        return upstream_error(
            getattr(response, "status_code", None), service="provider"
        )
    # GatewayClient._request deliberately normalizes HTTPX constructor
    # ValueErrors to this fixed literal. No prefix/subclass matching: arbitrary
    # ValueErrors (including ones carrying URLs or credentials) stay private.
    if (
        type(exc) is ValueError and str(exc) == "Invalid gateway client configuration"
    ) or isinstance(
        exc,
        (
            httpx.InvalidURL,
            httpx.UnsupportedProtocol,
            httpx.LocalProtocolError,
            httpx.ProxyError,
        ),
    ):
        return PublicError(
            500,
            "gateway_configuration_invalid",
            "Gateway connection configuration is invalid. Ask an administrator to check the gateway URL, proxy and protocol settings.",
        )
    if isinstance(exc, (requests_errors.TooManyRedirects, httpx.TooManyRedirects)):
        return PublicError(
            502,
            "upstream_redirects",
            "The service redirected too many times. Check the configured endpoint URL.",
        )
    if isinstance(exc, requests_errors.RequestException):
        return PublicError(
            502,
            "upstream_request_failed",
            "The service request failed. Check the endpoint and credentials, then retry.",
        )
    if type(exc).__module__.startswith("agentcc."):
        return _domain_error(exc)
    return None


def exception_response(gm, exc, *, logger, event, safe_messages=(), log_detail=True):
    """Return GeneralMethods' standard envelope with safe copy and a stable code.

    ``safe_messages`` must contain only audited constant messages from a narrow
    validator. ``log_detail=False`` is required at secret-bearing transport
    boundaries; otherwise unexpected failures include their full exception.
    Known failures are logged by type/status only (no bodies, URLs or secrets).
    """
    error = _known_error(exc, safe_messages=safe_messages)
    if error is None:
        error = unexpected_error(exc, logger=logger, event=event, log_detail=log_detail)
    else:
        _log_type(exc, logger=logger, event=event)
    return gm.custom_error_response(error.status, error.message, code=error.code)
