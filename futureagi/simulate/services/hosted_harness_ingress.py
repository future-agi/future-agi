"""Run-scoped HTTP relay for managed sandbox callbacks.

Daytona can mint a no-header signed preview URL. E2B instead protects its public
host with a traffic-access-token header, so customer/provider traffic goes through
this platform URL. The relay signs only the attempt and port; it resolves the
sandbox and provider header server-side on every request.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from time import time
from urllib.parse import quote

import requests
from django.conf import settings
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.http import HttpResponse
from django.http.request import RawPostDataException
from django.utils import timezone

from simulate.models import HostedHarnessAttempt
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_sandbox import get_sandbox_provider

logger = logging.getLogger("simulate.hosted_harness_ingress")

_TOKEN_SALT = "futureagi.hosted-harness-ingress.v1"
_TOKEN_MAX_AGE_SECONDS = 86400
_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_RELAY_TIMEOUT = (10.0, 30.0)
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_RESPONSE_HEADERS_TO_SKIP = _HOP_BY_HOP_HEADERS | {
    "content-length",
    "content-encoding",
    "set-cookie",
}
_CLOSED_STATES = frozenset(
    {
        HostedHarnessAttempt.State.CLEANING_UP,
        HostedHarnessAttempt.State.SUPERSEDED,
        HostedHarnessAttempt.State.COMPLETED,
        HostedHarnessAttempt.State.FAILED,
        HostedHarnessAttempt.State.CANCELED,
    }
)


@dataclass(frozen=True)
class IngressGrant:
    attempt_id: uuid.UUID
    port: int
    expires_at: float


def _raise_closed(code: str = "ingress_closed") -> None:
    raise HostedHarnessError(
        code,
        "the hosted callback URL is no longer active",
        status_code=410,
    )


def _public_base_url(request) -> str:
    value = str(getattr(settings, "HARNESS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not value:
        raise HostedHarnessError(
            "public_base_url_missing",
            "HARNESS_PUBLIC_BASE_URL is required for hosted callback URLs",
            status_code=500,
        )
    if not value.startswith("https://"):
        raise HostedHarnessError(
            "public_base_url_invalid",
            "HARNESS_PUBLIC_BASE_URL must be an HTTPS URL",
            status_code=500,
        )
    del request
    return value


def create_ingress_proxy_url(
    request,
    attempt: HostedHarnessAttempt,
    port: int,
    *,
    expires_in_seconds: int,
) -> str:
    """Create a URL capability for one attempt and one guest-selected HTTP port."""
    expires_at = time() + expires_in_seconds
    token = signing.dumps(
        {
            "attempt_id": str(attempt.id),
            "port": port,
            "expires_at": expires_at,
        },
        salt=_TOKEN_SALT,
        compress=True,
    )
    return (
        f"{_public_base_url(request)}/simulate/api/harness/ingress/"
        f"{quote(token, safe='')}/"
    )


def _grant_from_token(token: str) -> IngressGrant:
    try:
        payload = signing.loads(
            token,
            salt=_TOKEN_SALT,
            max_age=_TOKEN_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired) as exc:
        raise HostedHarnessError(
            "ingress_not_found",
            "the hosted callback URL is invalid",
            status_code=404,
        ) from exc
    if not isinstance(payload, dict):
        raise HostedHarnessError(
            "ingress_not_found", "the hosted callback URL is invalid", status_code=404
        )
    try:
        attempt_id = uuid.UUID(str(payload["attempt_id"]))
        port = int(payload["port"])
        expires_at = float(payload["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HostedHarnessError(
            "ingress_not_found",
            "the hosted callback URL is invalid",
            status_code=404,
        ) from exc
    if isinstance(payload.get("port"), bool) or not 1 <= port <= 65535:
        raise HostedHarnessError(
            "ingress_not_found", "the hosted callback URL is invalid", status_code=404
        )
    if expires_at <= time():
        _raise_closed("ingress_expired")
    return IngressGrant(attempt_id=attempt_id, port=port, expires_at=expires_at)


def _active_attempt(grant: IngressGrant) -> HostedHarnessAttempt:
    try:
        attempt = HostedHarnessAttempt.no_workspace_objects.get(id=grant.attempt_id)
    except HostedHarnessAttempt.DoesNotExist as exc:
        raise HostedHarnessError(
            "ingress_not_found", "the hosted callback URL is invalid", status_code=404
        ) from exc
    if (
        attempt.state in _CLOSED_STATES
        or attempt.cleanup_verified_at is not None
        or timezone.now() >= attempt.expires_at
    ):
        _raise_closed()
    if not attempt.provider_ref:
        raise HostedHarnessError(
            "sandbox_not_ready",
            "the hosted sandbox has not been created",
            status_code=409,
        )
    return attempt


def _target_url(base_url: str, target_path: str, query_string: str) -> str:
    if "\x00" in target_path or target_path.startswith("//"):
        raise HostedHarnessError(
            "ingress_path_invalid",
            "the hosted callback path is invalid",
            status_code=400,
        )
    encoded_path = quote(target_path.lstrip("/"), safe="/%:@-._~!$&'()*+,;=")
    url = f"{base_url.rstrip('/')}/{encoded_path}"
    return f"{url}?{query_string}" if query_string else url


def _request_headers(request, provider_headers) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS
        and name.lower() not in {"host", "content-length", "accept-encoding"}
    }
    headers["Accept-Encoding"] = "identity"
    headers.update(provider_headers)
    return headers


def _request_body(request) -> bytes:
    try:
        return request.body
    except RawPostDataException:
        return json.dumps(
            request.data,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


def proxy_ingress_request(request, token: str, target_path: str = "") -> HttpResponse:
    grant = _grant_from_token(token)
    attempt = _active_attempt(grant)
    body = _request_body(request)
    if len(body) > _MAX_REQUEST_BYTES:
        return HttpResponse("request body is too large", status=413)

    try:
        provider = get_sandbox_provider()
        sandbox = provider.get(str(attempt.provider_ref), request_timeout=30)
        preview = provider.create_preview_url(
            sandbox,
            grant.port,
            expires_in_seconds=max(60, int(grant.expires_at - time())),
        )
        if not preview.url.startswith("https://"):
            raise ValueError("sandbox callback target is not HTTPS")
        upstream = requests.request(
            request.method,
            _target_url(
                preview.url,
                target_path,
                request.META.get("QUERY_STRING", ""),
            ),
            headers=_request_headers(request, preview.headers),
            data=body,
            timeout=_RELAY_TIMEOUT,
            allow_redirects=False,
        )
    except HostedHarnessError:
        raise
    except requests.RequestException as exc:
        logger.warning(
            "hosted ingress upstream failed attempt=%s provider_ref=%s: %s",
            attempt.id,
            attempt.provider_ref,
            exc,
        )
        raise HostedHarnessError(
            "ingress_unavailable",
            "the hosted callback service could not be reached",
            status_code=502,
            retryable=True,
        ) from exc
    except Exception as exc:
        logger.warning(
            "hosted ingress relay failed attempt=%s provider_ref=%s: %s: %s",
            attempt.id,
            attempt.provider_ref,
            type(exc).__name__,
            exc,
        )
        raise HostedHarnessError(
            "ingress_unavailable",
            "the hosted callback service could not be reached",
            status_code=502,
            retryable=True,
        ) from exc

    if len(upstream.content) > _MAX_RESPONSE_BYTES:
        return HttpResponse("upstream response is too large", status=502)
    response = HttpResponse(upstream.content, status=upstream.status_code)
    for name, value in upstream.headers.items():
        if name.lower() not in _RESPONSE_HEADERS_TO_SKIP:
            response[name] = value
    return response
