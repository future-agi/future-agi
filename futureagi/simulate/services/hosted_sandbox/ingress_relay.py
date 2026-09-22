"""Bounded no-header HTTP callback relay for private E2B sandboxes.

Providers such as Retell cannot add E2B's traffic-access header to a webhook.
The guest can mint a short-lived URL for one sandbox port via its authenticated
attempt capability; only this platform service attaches the E2B header.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

import requests
from django.conf import settings
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from .base import SandboxProviderConfigurationError

_SALT = "simulate.hosted-sandbox.e2b-ingress.v1"
_MAX_TTL = 3600
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_FORWARDED_HEADERS = ("content-type", "accept", "user-agent")
logger = logging.getLogger(__name__)


def mint_ingress_url(*, sandbox_id: str, port: int, expires_in_seconds: int) -> str:
    base = str(getattr(settings, "HARNESS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base.startswith("https://"):
        raise SandboxProviderConfigurationError(
            "HARNESS_PUBLIC_BASE_URL must be a public HTTPS URL for E2B callbacks"
        )
    if not sandbox_id or not 1 <= port <= 65535:
        raise ValueError("invalid E2B sandbox or callback port")
    ttl = max(1, min(expires_in_seconds, _MAX_TTL))
    token = signing.dumps(
        {"sandbox_id": sandbox_id, "port": port, "expires_at": int(time.time()) + ttl},
        salt=_SALT,
    )
    return f"{base}/simulate/api/harness-ingress/{token}/"


@csrf_exempt
def relay_ingress(request: HttpRequest, token: str, path: str = "") -> HttpResponse:
    if request.method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        return HttpResponse(status=405)
    try:
        claims = signing.loads(token, salt=_SALT, max_age=_MAX_TTL)
        sandbox_id = str(claims["sandbox_id"])
        port = int(claims["port"])
        if int(claims["expires_at"]) < int(time.time()) or not 1 <= port <= 65535:
            return HttpResponse(status=410)
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return HttpResponse(status=403)

    if len(request.body) > _MAX_REQUEST_BYTES:
        return HttpResponse(status=413)

    try:
        # The destination is derived only from a signed sandbox ID and an E2B
        # SDK host, never from an inbound Host/header/query parameter.
        from simulate.services.hosted_sandbox import get_sandbox_provider

        provider = get_sandbox_provider()
        if provider.name != "e2b":
            return HttpResponse(status=410)
        sandbox = provider.get(sandbox_id)
        private = sandbox._sandbox
        access_token = private.traffic_access_token
        if not access_token:
            return HttpResponse(status=502)
        host = private.get_host(port)
        url = f"https://{host}/{quote(path, safe='/~:@!$&()*+,;=-._')}"
        query = request.META.get("QUERY_STRING", "")
        if query:
            url = f"{url}?{query}"
        headers = {
            name: request.headers[name]
            for name in _FORWARDED_HEADERS
            if name in request.headers
        }
        headers["e2b-traffic-access-token"] = access_token
        with requests.request(
            request.method,
            url,
            data=request.body,
            headers=headers,
            timeout=(5, 30),
            allow_redirects=False,
            stream=True,
        ) as upstream:
            chunks: list[bytes] = []
            size = 0
            for chunk in upstream.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    return HttpResponse(status=502)
                chunks.append(chunk)
            response = HttpResponse(b"".join(chunks), status=upstream.status_code)
            for name in ("content-type", "location"):
                if name in upstream.headers:
                    response[name] = upstream.headers[name]
            return response
    except (requests.RequestException, OSError, ValueError) as exc:
        logger.warning(
            "E2B callback relay failed for sandbox %s: %s",
            sandbox_id,
            type(exc).__name__,
        )
        return HttpResponse(status=502)
