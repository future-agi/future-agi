"""Generation-local gateways: original success body first, error body never evidence.

The managed route reuses licensing decisions, not the lossy completion helper.
The retained SDK route uses its raw-response API and a private zero-retry copy;
its error parser is also bypassed, since OpenAI otherwise JSON-decodes errors
before returning a raw response. No shared client, class or policy is mutated.
"""

from types import MethodType


def strict_response(response):
    from ee.agenthub.traceerroragent.background_payloads import original_json
    from tracer.services.analysis_result_completion import IncompleteAnalysis

    # Do not accept an already-parsed dict or stringify an opaque SDK object.
    body = getattr(response, "content", None)
    if not isinstance(body, bytes):
        raise IncompleteAnalysis()
    return original_json(body.decode("utf-8"))


def _reject_sdk_status(self, response):
    from tracer.services.analysis_result_completion import IncompleteAnalysis

    # ERROR_BODY_NOT_CONSUMED: no body access, decoding or message extraction.
    # The caller marks every exception sticky-failed, regardless of its contents.
    # This private client's zero-retry policy also applies to retryable statuses.
    return IncompleteAnalysis()


def sdk_completion(*, client, **kwargs):
    from tracer.services.analysis_result_completion import IncompleteAnalysis

    if kwargs.get("stream"):
        raise IncompleteAnalysis()
    import httpx

    from tracer.services.background_transport import before_send, sync_client_options

    if not callable(getattr(client, "with_options", None)):
        raise IncompleteAnalysis()
    # A shallow SDK copy otherwise retains the shared HTTP client's redirect and
    # connection policy. Own a fresh HTTP transport for this attempt only.
    before_send()
    with httpx.Client(follow_redirects=False, **sync_client_options()) as http:
        scoped = client.with_options(max_retries=0, http_client=http)
        if (
            scoped is client
            or getattr(scoped, "_client", None) is not http
            or getattr(scoped, "max_retries", None) != 0
        ):
            raise IncompleteAnalysis()
        completions = scoped.chat.completions
        if not callable(
            getattr(getattr(completions, "with_raw_response", None), "create", None)
        ):
            raise IncompleteAnalysis()
        scoped._make_status_error_from_response = MethodType(_reject_sdk_status, scoped)
        before_send()
        raw = completions.with_raw_response.create(**kwargs)
        response = getattr(raw, "http_response", None)
        try:
            # Includes 3xx: never follow or decode an error as model evidence.
            response.raise_for_status()
            return strict_response(response)
        finally:
            if response is not None:
                response.close()


def managed_completion(payload, *, deadline=None):
    import time

    import httpx

    from ee.licensing.activation_client import (
        ManagedServiceError,
        _raise_for_managed_status,
        get_service_token,
        invalidate_token,
        require_configured_license_key,
    )
    from ee.licensing.managed_ai import _cloud_gateway_credentials, is_managed_model
    from ee.usage.deployment import DeploymentMode
    from tracer.services.analysis_result_completion import IncompleteAnalysis
    from tracer.services.background_transport import before_send, sync_client_options

    if not is_managed_model(payload.get("model")) or payload.get("stream"):
        raise IncompleteAnalysis()
    cloud = DeploymentMode.is_cloud()
    if cloud:
        base_url, api_key = _cloud_gateway_credentials()
        timeout = 300.0
    else:
        token = get_service_token()
        if token is None:
            raise ManagedServiceError(
                "ACTIVATION_FAILED", "Could not obtain service token"
            )
        if token.scope == "oss" and not token.access_token:
            raise ManagedServiceError(
                "NO_ENTERPRISE_LICENSE", "Managed AI requires an Enterprise license"
            )
        base_url, api_key = token.gateway_url, token.access_token
        timeout = 30.0
        # As in call_managed_service: activation may have waited; recheck now.
        require_configured_license_key()

    def unauthorized():
        if not cloud:
            invalidate_token()
            raise ManagedServiceError(
                "TOKEN_EXPIRED", "Service token rejected — will refresh on next call"
            )
        raise ManagedServiceError(
            "GATEWAY_UNAUTHORIZED", "Managed AI gateway rejected the internal API key"
        )

    try:
        # Preserve an explicit caller deadline as well as the live guard budget.
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IncompleteAnalysis()
            timeout = min(timeout, remaining)
        # Owned HTTPX transport: no retries/redirects, admission after waits and
        # before each header/body write, without consuming the response bytes.
        timeout = before_send(timeout)
        with httpx.Client(
            timeout=timeout, follow_redirects=False, **sync_client_options()
        ) as client:
            timeout = before_send(timeout)
            response = client.post(
                base_url.rstrip("/") + "/v1/chat/completions",
                json=payload,
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.TimeoutException as exc:
        raise ManagedServiceError(
            "GATEWAY_TIMEOUT", "Managed AI gateway timed out"
        ) from exc
    except httpx.ConnectError as exc:
        raise ManagedServiceError(
            "GATEWAY_UNREACHABLE", "Cannot reach managed AI gateway"
        ) from exc
    try:
        _raise_for_managed_status(response.status_code, unauthorized)
        # Status/exception is authoritative for failure; error content is ignored.
        response.raise_for_status()
        return strict_response(response)
    finally:
        response.close()
