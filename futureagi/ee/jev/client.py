"""Bounded typed requests to TypeSafe's System One API with the org's own key.

By default they go through the AgentCC gateway's /v1/systemone: the backend
authenticates with its internal key and hands the gateway the org's TypeSafe
key in x-agentcc-provider-key for that one call. JEV_TRANSPORT=direct calls
TypeSafe from the backend instead, for local dev without a gateway.
"""

import os
import random
import re
import threading
import time
from functools import lru_cache

import structlog
from django.conf import settings

from ee.jev.mapping import JevMappingError, check_input_size
from tfc.ee_gates import is_jev_model

logger = structlog.get_logger(__name__)

TYPESAFE_BASE_URL = "https://api.typesafe.ai"
PROVIDER_KEY_HEADER = "x-agentcc-provider-key"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
# Gateway error codes are snake_case identifiers; anything else is not read.
_GATEWAY_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
# The gateway's id for a request (a ULID), which its logs are found by.
REQUEST_ID_HEADER = "x-agentcc-request-id"
_REQUEST_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
# The gateway refused to carry the org's key, so the call never reached
# TypeSafe. These are deployment faults; no retry can fix them.
_GATEWAY_REFUSALS = frozenset(
    {
        "byok_required",
        "decision_provider_not_configured",
        "provider_key_header_not_allowed",
    }
)


class JevGatewayError(RuntimeError):
    def __init__(self, code="JEV_PROVIDER_ERROR", retryable=False, retry_after=None):
        self.code, self.retryable, self.retry_after = code, retryable, retry_after
        super().__init__(code)


@lru_cache(maxsize=1)
def _semaphore(limit):
    return threading.BoundedSemaphore(limit)


def _provider_error(status, retry_after=None):
    """Map a TypeSafe HTTP status to a typed, body-free error."""
    if status in (401, 403):
        # The org's key was rejected; retrying cannot help.
        return JevGatewayError("JEV_KEY_INVALID")
    if status == 429:
        return JevGatewayError("JEV_RATE_LIMITED", True, retry_after)
    if status in (408, 504):
        return JevGatewayError("JEV_TIMEOUT", True, retry_after)
    return JevGatewayError("JEV_PROVIDER_ERROR", status in (500, 502, 503), retry_after)


def _gateway_error(status, code=None, retry_after=None, upstream_status=None):
    """Map a gateway error to a typed, body-free error: its code first, then
    its status. The gateway itself never retries."""
    if code == "provider_key_rejected":
        # TypeSafe rejected the org's key; retrying cannot help.
        return JevGatewayError("JEV_KEY_INVALID")
    if code in _GATEWAY_REFUSALS:
        return JevGatewayError()
    if status == 429:
        return JevGatewayError("JEV_RATE_LIMITED", True, retry_after)
    if status == 504:
        return JevGatewayError("JEV_TIMEOUT", True, retry_after)
    if code == "upstream_invalid_response":
        # TypeSafe answered 200, so the org's key was billed; a retry would bill
        # it again. The same answer is final on the direct transport.
        return JevGatewayError()
    if status == 502 and upstream_status is not None:
        # TypeSafe answered with a status the gateway does not map (500, 501,
        # 415, a redirect...): retry only what the direct transport retries.
        # A 502 without one is the gateway getting no answer, and is retried.
        return JevGatewayError(
            "JEV_PROVIDER_ERROR", upstream_status in (500, 502, 503), retry_after
        )
    # 400, 402, 404, 413 and 422 are final, and so is any status the gateway
    # does not map: a 401 is the gateway refusing the internal key, not the
    # org's, and a retry after a gateway 500 could bill the org's key twice.
    return JevGatewayError("JEV_PROVIDER_ERROR", status in (502, 503), retry_after)


def _gateway_code(response):
    """The code from the gateway's error envelope. The message is never read:
    it may carry TypeSafe's text about the request."""
    if len(response.content) > _MAX_RESPONSE_BYTES:
        return None
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _GATEWAY_CODE.fullmatch(code) else None


def _upstream_status(response):
    """The status TypeSafe answered with, when the gateway got an answer."""
    value = response.headers.get("x-agentcc-upstream-status", "")
    return int(value) if value.isdigit() and len(value) == 3 else None


def _request_id(response):
    """The gateway's id for the call, when it is one; nothing else of the
    header is kept."""
    value = response.headers.get(REQUEST_ID_HEADER, "")
    return value if _REQUEST_ID.fullmatch(value) else None


def _internal_api_key():
    return str(
        getattr(settings, "AGENTCC_INTERNAL_API_KEY", "")
        or os.environ.get("AGENTCC_INTERNAL_API_KEY")
        or ""
    ).strip()


class JevSystemOneClient:
    def __init__(
        self, *, api_key=None, sleep=time.sleep, clock=time.monotonic, jitter=None
    ):
        self.api_key = api_key
        self.sleep, self.clock = sleep, clock
        self.jitter = jitter or (lambda upper: random.uniform(0, upper))
        self.attempt = 0
        self.attempts = []
        # The gateway's id for the last call, success or error (None on the
        # direct transport).
        self.gateway_request_id = None

    def _gateway_url(self):
        from agentcc.services.gateway_client import resolve_gateway_internal_url

        base_url = resolve_gateway_internal_url()
        # Only whether the internal key is set: an error raised here keeps
        # this frame, and so its locals, for Sentry.
        internal_key_set = bool(_internal_api_key())
        if not (base_url and internal_key_set):
            # Never fall back to calling TypeSafe directly: that would take the
            # org's key off the gateway without anyone choosing it.
            logger.error(
                "jev_gateway_not_configured",
                gateway_url_set=bool(base_url),
                internal_key_set=internal_key_set,
            )
            raise JevGatewayError()
        return base_url.rstrip("/") + "/v1/systemone"

    def _headers(self, gateway):
        if not gateway:
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {_internal_api_key()}",
            # The org's own key, for this one call. The gateway never uses an
            # operator key for an internal-key decision call.
            PROVIDER_KEY_HEADER: self.api_key,
            "Content-Type": "application/json",
        }

    def invalid_response(self, reason, code="JEV_PROVIDER_ERROR"):
        """A 200 whose body can't be used (``reason``: too_large, not_json,
        not_object or error_body; a caller whose own check rejects the answer
        reports it the same way), logged with the gateway's id for the call,
        which finds what came back in the gateway's logs. Returns the error to
        raise. Nothing of the body is logged."""
        logger.warning(
            "jev_invalid_response",
            reason=reason,
            code=code,
            attempt=self.attempt,
            gateway_request_id=self.gateway_request_id,
        )
        return JevGatewayError(code)

    def _request(self, payload, timeout):
        import httpx

        self.gateway_request_id = None
        if not self.api_key:
            raise JevGatewayError("JEV_KEY_MISSING")
        # Only an explicit "direct" leaves the gateway.
        gateway = getattr(settings, "JEV_TRANSPORT", "gateway") != "direct"
        if gateway:
            url = self._gateway_url()
            body = {**payload, "model": "typesafe/" + payload["model"]}
        else:
            base_url = getattr(settings, "TYPESAFE_BASE_URL", "") or TYPESAFE_BASE_URL
            url, body = base_url.rstrip("/") + "/v1/systemone", payload
        try:
            # The headers are built in the call and never bound to a name: the
            # errors raised below keep this frame, and Sentry reports a
            # frame's locals, where its scrubber knows only some key names.
            response = httpx.post(
                url,
                json=body,
                timeout=timeout,
                follow_redirects=False,
                headers=self._headers(gateway),
            )
        except httpx.TimeoutException:
            raise JevGatewayError("JEV_TIMEOUT", True) from None
        except httpx.HTTPError:
            raise JevGatewayError("JEV_PROVIDER_ERROR", True) from None
        if gateway:
            self.gateway_request_id = _request_id(response)
        if response.status_code != 200:
            retry_after = response.headers.get("Retry-After")
            if gateway:
                code = _gateway_code(response)
                upstream_status = _upstream_status(response)
                # Only the gateway's status, code and request id: never its
                # message, the body or another header. A refusal by the gateway
                # itself (a rotated internal key, a gateway without
                # /v1/systemone) shows here.
                logger.warning(
                    "jev_gateway_error",
                    status=response.status_code,
                    code=code,
                    upstream_status=upstream_status,
                    gateway_request_id=self.gateway_request_id,
                )
                raise _gateway_error(
                    response.status_code, code, retry_after, upstream_status
                )
            raise _provider_error(response.status_code, retry_after)
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise self.invalid_response("too_large")
        try:
            return response.json()
        except ValueError:
            raise self.invalid_response("not_json") from None

    def evaluate(self, model, state, questions, *, timeout=35):
        if not is_jev_model(model):
            raise JevMappingError(
                "JEV_MODEL_UNKNOWN", "model", "Unsupported Jev model."
            )
        payload = {"model": model, "state": state, "questions": questions}
        check_input_size(payload)
        self.attempt, self.attempts = 0, []
        self.gateway_request_id = None
        deadline = self.clock() + min(getattr(settings, "JEV_TOTAL_DEADLINE", 90), 90)
        semaphore = _semaphore(max(1, int(getattr(settings, "JEV_MAX_CONCURRENT", 8))))
        if not semaphore.acquire(timeout=max(0, deadline - self.clock())):
            raise JevGatewayError("JEV_TIMEOUT", True)
        try:
            for attempt in range(
                1, min(int(getattr(settings, "JEV_MAX_RETRIES", 2)), 2) + 2
            ):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise JevGatewayError("JEV_TIMEOUT", True)
                self.attempt = attempt
                try:
                    response = self._request(payload, min(timeout, remaining))
                    if not isinstance(response, dict):
                        raise self.invalid_response("not_object")
                    if "error" in response:
                        raise self.invalid_response("error_body")
                    # A returned 200 is final even when its read ran a little
                    # past the deadline: the org's key was charged for it, and
                    # the deadline is checked before every send.
                    # Only allowlisted accounting fields are retained; never the body.
                    self.attempts.append(
                        {
                            "attempt": attempt,
                            "outcome": "success",
                            "usage": response.get("usage", {}),
                            "gateway_request_id": self.gateway_request_id,
                        }
                    )
                    return response
                except JevGatewayError as exc:
                    error = exc
                except (ValueError, TypeError):
                    error = JevGatewayError()
                self.attempts.append(
                    {
                        "attempt": attempt,
                        "outcome": "error",
                        "usage": {},
                        "code": error.code,
                        "gateway_request_id": self.gateway_request_id,
                    }
                )
                if (
                    not error.retryable
                    or attempt
                    >= min(int(getattr(settings, "JEV_MAX_RETRIES", 2)), 2) + 1
                ):
                    raise error from None
                delay = self.jitter(2 ** (attempt - 1))
                try:
                    retry_after = float(error.retry_after)
                    if 0 <= retry_after <= 10:
                        delay = retry_after
                except (TypeError, ValueError):
                    pass
                if self.clock() + delay >= deadline:
                    raise JevGatewayError("JEV_TIMEOUT", True) from None
                self.sleep(delay)
            raise JevGatewayError()
        finally:
            semaphore.release()
