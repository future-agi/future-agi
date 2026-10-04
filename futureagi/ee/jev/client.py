"""Bounded typed requests using the existing managed-service credentials."""

import random
import threading
import time
from functools import lru_cache

from django.conf import settings

from ee.jev.mapping import JevMappingError, check_input_size
from ee.licensing.activation_client import (
    ManagedServiceError,
    call_managed_service,
    dispatch_managed_request,
)
from ee.licensing.managed_ai import _cloud_gateway_credentials
from tfc.ee_gates import is_jev_model


class JevGatewayError(RuntimeError):
    def __init__(self, code="JEV_PROVIDER_ERROR", retryable=False, retry_after=None):
        self.code, self.retryable, self.retry_after = code, retryable, retry_after
        super().__init__(code)


@lru_cache(maxsize=1)
def _semaphore(limit):
    return threading.BoundedSemaphore(limit)


def _gateway_error(code=None, status=None, retry_after=None):
    if code in (
        "upstream_auth",
        "upstream_invalid_response",
        "not_supported",
        "model_not_found",
    ) or status in (400, 401, 402, 403, 404, 413, 422, 501):
        return JevGatewayError()
    if code in ("rate_limit_exceeded", "RATE_LIMITED") or status == 429:
        return JevGatewayError("JEV_RATE_LIMITED", True, retry_after)
    if code in ("gateway_timeout", "GATEWAY_TIMEOUT") or status == 504:
        return JevGatewayError("JEV_TIMEOUT", True, retry_after)
    retryable = (
        code
        in (
            "upstream_error",
            "all_providers_unavailable",
            "service_unavailable",
            "GATEWAY_UNREACHABLE",
        )
        or status == 503
    )
    return JevGatewayError("JEV_PROVIDER_ERROR", retryable, retry_after)


class JevSystemOneClient:
    def __init__(self, *, sleep=time.sleep, clock=time.monotonic, jitter=None):
        self.sleep, self.clock = sleep, clock
        self.jitter = jitter or (lambda upper: random.uniform(0, upper))
        self.attempt = 0
        self.attempts = []

    def _request(self, payload, timeout):
        from ee.usage.deployment import DeploymentMode

        if DeploymentMode.is_cloud():
            base_url, api_key = _cloud_gateway_credentials()

            def unauthorized():
                raise ManagedServiceError(
                    "GATEWAY_UNAUTHORIZED", "Managed gateway rejected credentials"
                )

            return dispatch_managed_request(
                url=base_url.rstrip("/") + "/v1/systemone",
                api_key=api_key,
                json_body=payload,
                timeout=timeout,
                on_unauthorized=unauthorized,
            )
        return call_managed_service(
            path="/v1/systemone", json_body=payload, timeout=timeout
        )

    def evaluate(self, model, state, questions, *, timeout=35):
        if not is_jev_model(model):
            raise JevMappingError(
                "JEV_MODEL_UNKNOWN", "model", "Unsupported Jev model."
            )
        payload = {"model": model, "state": state, "questions": questions}
        check_input_size(payload)
        self.attempt, self.attempts = 0, []
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
                        raise JevGatewayError()
                    if "error" in response:
                        error = response["error"]
                        raise _gateway_error(
                            error.get("code") if isinstance(error, dict) else None
                        )
                    if self.clock() > deadline:
                        raise JevGatewayError("JEV_TIMEOUT", True)
                    # Only allowlisted accounting fields are retained; never the body.
                    self.attempts.append(
                        {
                            "attempt": attempt,
                            "outcome": "success",
                            "usage": response.get("usage", {}),
                        }
                    )
                    return response
                except ManagedServiceError as exc:
                    error = _gateway_error(
                        getattr(exc, "gateway_code", None) or exc.code,
                        getattr(exc, "status_code", None),
                        getattr(exc, "retry_after", None),
                    )
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
