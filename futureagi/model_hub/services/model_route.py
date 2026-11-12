"""One-key model routing for LLM calls: managed (Future AGI credits)
versus BYOK (the organization's own provider key).

This module is the seam of ONEKEY-ASSUMED-CONTRACT-r1, with the
refusal shape of ONEKEY-REAL-REFUSAL-CONTRACT-r1. Everything that depends on
the gateway's real port goes through ``GatewayManagedRouting``, so when the
gateway publishes its port the swap is one call,
``set_managed_routing_port(<gateway port>)``, plus this module's port
signature if the gateway's differs.

Rules kept here:

- BYOK first. A stored provider credential (custom model, workspace key,
  organization key) is found by ``LiteLLMModelManager`` exactly as before and
  always wins; the port is not consulted for it.
- Managed only when the port admits the model for the organization. A
  managed route is a ``ManagedRouteBinding``: organization, workspace, model
  and operation, bound and tenancy-checked, with nothing admitted. It is not
  a provider key and is never handed to litellm.
- One completion is one admission: ``managed_chat_completion`` validates the
  request, gets the gateway managed client and runs the access check, then
  admits (a fresh request id), sends one request through the gateway client
  with the identity headers, and maps any failure to a typed refusal. The
  SDK's own retries of that request share its id. An admission whose request
  was never sent is released.
- A managed refusal (not eligible, a gateway refusal, a failure) is final;
  nothing falls back to a provider credential or the deployment's own keys.
  Refusals carry the gateway's ``error.code`` and our own message, never the
  gateway's or provider's text, and are raised without an exception chain.
- Until a port is registered, ``UnavailableGatewayManagedRouting`` admits no
  model, so every route is BYOK exactly as before.
- Nothing here prints a credential or gateway material.
"""

from __future__ import annotations

import contextlib
import inspect
import re
import threading
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple, Protocol, runtime_checkable

import structlog
from requests.exceptions import Timeout as RequestsTimeout

from tfc.utils.error_codes import (
    MANAGED_REFUSAL_CONTRACT, classify_managed_refusal, get_error_message,
    managed_gateway_display, managed_refusal_metadata, managed_refusal_parts, positive_retry_after,
)

logger = structlog.get_logger(__name__)

MODE_MANAGED = "managed"
MODE_BYOK = "byok"
MODE_UNAVAILABLE = "unavailable"

PAYER_FUTURE_AGI_CREDITS = "future_agi_credits"
PAYER_OWN_PROVIDER_KEY = "own_provider_key"

# The call sites a route is resolved for (sent to the port as ``operation``).
OP_RUN_PROMPT = "run_prompt"
OP_DYNAMIC_COLUMN = "dynamic_column"
OP_EXPERIMENT_PROMPT = "experiment_prompt"
OP_CUSTOM_PROMPT_EVAL = "custom_prompt_eval"
OP_AI_EVAL_WRITER = "ai_eval_writer"
OP_SMART_FILTER = "smart_filter"
OP_CRITERIA_HELPER = "criteria_helper"
OPERATIONS = frozenset(
    {
        OP_RUN_PROMPT,
        OP_DYNAMIC_COLUMN,
        OP_EXPERIMENT_PROMPT,
        OP_CUSTOM_PROMPT_EVAL,
        OP_AI_EVAL_WRITER,
        OP_SMART_FILTER,
        OP_CRITERIA_HELPER,
    }
)

# Model modes a managed route serves: text chat through the gateway's
# OpenAI-compatible chat completions. Image, speech and transcription models
# keep their provider-key routes.
MANAGED_MODEL_MODES = frozenset({"chat"})

# Identity headers on every managed request; set here from the admitted
# route and never taken from the port's own headers.
ORG_HEADER = "X-Org-Id"
WORKSPACE_HEADER = "X-Workspace-Id"
REQUEST_HEADER = "x-agentcc-request-id"
_IDENTITY_HEADER_NAMES = frozenset(
    name.lower() for name in (ORG_HEADER, WORKSPACE_HEADER, REQUEST_HEADER, "X-Request-Id")
)

# The refusal LiteLLMModelManager.get_api_key has always given; a model with
# neither a stored credential nor a managed route keeps it word for word.
NOT_CONFIGURED_MESSAGE = (
    "API key not configured for {provider}. Please add your API key in settings."
)


# ------------------------------------------------- real refusal contract ---


class RefusalSpec(NamedTuple):
    """One gateway refusal (ONEKEY-REAL-REFUSAL-CONTRACT-r1), keyed on
    ``error.code``. ``retry``: never | always | after (Retry-After) |
    in_flight (only for an in-flight hold the gateway says to retry)."""

    http_status: int
    error_type: str
    message: str
    retry: str
    remedy: str | None


REFUSAL_CONTRACT = {
    pair: RefusalSpec(pair[0], spec["type"], get_error_message(spec["message_key"]),
                      spec["retry"], spec["remedy"])
    for pair, spec in MANAGED_REFUSAL_CONTRACT.items()
}
CREDIT_REFUSAL_CODES = frozenset(code for status, code in REFUSAL_CONTRACT if status == 402)
IN_FLIGHT_LIMIT_SOURCE = "agentcc_in_flight"
IN_FLIGHT_RETRY_AFTER_SECONDS = 2
IN_FLIGHT_MESSAGE = (
    "Other requests are holding your Future AGI credits right now. "
    "Try again in a few seconds."
)
_MAX_RETRY_AFTER_SECONDS = 3600
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_SAFE_TIMESTAMP = re.compile(r"[0-9T:.+\-Z ]{1,40}")


# --------------------------------------------------------------- refusals ---


class ModelRouteError(Exception):
    """A typed refusal of a managed route. ``message`` is safe to show; it
    never carries provider or gateway text. ``code`` is the gateway's
    ``error.code`` for a gateway refusal, else its own code."""

    code = "MODEL_ROUTE_ERROR"
    default_message = "The model route could not be used."
    http_status = 503
    error_type = "server_error"

    def __init__(self, code=None, message=None, *, retryable=False, retry_after=None):
        self.code = code or type(self).code
        self.message = message or type(self).default_message
        self.retryable = bool(retryable)
        self.retry_after = retry_after
        super().__init__(self.message)

    def response_body(self) -> dict:
        body = {
            "type": self.error_type, "code": self.code, "message": self.message,
            "retryable": self.retryable, "retry_after": self.retry_after,
            "status": self.http_status, "category": getattr(self, "category", "generic"),
            "display_wait_seconds": getattr(self, "display_wait_seconds", None),
            "param": getattr(self, "param", None),
        }
        display = managed_gateway_display(self)
        outcome = self.code in {"MANAGED_OUTCOME_PENDING_RECONCILE", "CREDIT_SETTLEMENT_FAILED",
                               "MANAGED_CALL_ALREADY_COMPLETED"}
        if display is not None and not outcome:
            for key in ("status", "code", "type", "category", "message", "metadata", "param",
                        "remedy", "display_wait_seconds"):
                body[key] = display[key]
            body["retryable"] = bool(self.retryable and display["retryable"])
            body["retry_after"] = display["retry_after"] if body["retryable"] else None
            # Only terminal in-flight copy suppresses checked retry advice.
            if (display["display_classification"] == "in_flight"
                    and (not body["retryable"] or body["retry_after"] is None)):
                body["metadata"] = {
                    key: value for key, value in body["metadata"].items()
                    if not (key == "remedy_hint" and value == "retry")
                }
                if body["remedy"] == "retry":
                    body["remedy"] = None
            if getattr(self, "charged", False) is True:
                body["message"] = self.message
        if getattr(self, "original_refusal", None) is not None:
            body["original_refusal"] = dict(self.original_refusal)
        return body

    def detached(self) -> ModelRouteError:
        """A copy with no traceback, cause or context, to raise where the
        original's chain (which may hold upstream text) must not follow."""
        clone = type(self).__new__(type(self), self.message)
        clone.__dict__.update(self.__dict__)
        clone.args = (self.message,)
        return clone


class ManagedRouteDenied(ModelRouteError):
    """The managed route cannot be used (the port refused it, or the gateway
    cannot be reached)."""

    code = "MANAGED_ROUTE_DENIED"
    default_message = "Future AGI credits cannot run this model right now."
    http_status = 403
    error_type = "permission_error"


class ManagedRouteUnsupported(ManagedRouteDenied):
    """A managed route asked for something other than a text chat
    completion; refused before anything is admitted or sent."""

    code = "MANAGED_ROUTE_UNSUPPORTED_OUTPUT"
    default_message = (
        "Future AGI credits run text chat completions only. Add your own "
        "provider key to use audio, image, transcription or custom-endpoint "
        "models."
    )
    http_status = 400
    error_type = "invalid_request_error"


class ManagedRouteRefused(ManagedRouteDenied):
    """The gateway refused a managed call (ONEKEY-REAL-REFUSAL-CONTRACT-r1):
    ``code`` is its ``error.code`` and ``http_status`` its status;
    ``retryable``/``retry_after`` follow the contract; ``details`` holds the
    checked refusal metadata. The message is ours, never the gateway's."""

    code = "MANAGED_ROUTE_REFUSED"

    def __init__(
        self,
        code=None,
        message=None,
        *,
        http_status=None,
        error_type=None,
        retryable=False,
        retry_after=None,
        remedy=None,
        details=None,
    ):
        super().__init__(code, message, retryable=retryable, retry_after=retry_after)
        if http_status is not None:
            self.http_status = http_status
        if error_type is not None:
            self.error_type = error_type
        self.remedy = remedy
        self.details = dict(details or {})

    def response_body(self) -> dict:
        body = super().response_body()
        body.setdefault("remedy", self.remedy)
        body["billing_url"] = managed_billing_url()
        if "metadata" not in body:
            body["metadata"] = {key: _decimal_text(value) if isinstance(value, Decimal) else value
                                for key, value in self.details.items()}
        for key in ("limit_source", "scope", "resets_at"):
            if key in body["metadata"]:
                body[key] = body["metadata"][key]
        return body


class InsufficientCreditError(ManagedRouteRefused):
    """A 402 credit refusal (managed_credits_exhausted,
    managed_credits_insufficient, agent_budget_exhausted, or an unknown 402
    code); ``required``/``available`` are Decimal or None."""

    code = "MANAGED_CREDITS_REFUSED"
    default_message = "Not enough Future AGI credits to run this model."
    http_status = 402
    error_type = "payment_required"

    def __init__(
        self, code=None, message=None, *, required=None, available=None, **kwargs
    ):
        kwargs.setdefault("http_status", 402)
        kwargs.setdefault("error_type", "payment_required")
        super().__init__(code, message, **kwargs)
        self.required = _decimal_or_none(required)
        self.available = _decimal_or_none(available)

    def response_body(self) -> dict:
        body = super().response_body()
        body["required_usd"] = _decimal_text(self.required)
        body["available_usd"] = _decimal_text(self.available)
        return body


class ManagedRouteCallFailed(ModelRouteError):
    """A managed call failed at the gateway; it is not retried on a provider
    key or the deployment's keys."""

    code = "MANAGED_ROUTE_CALL_FAILED"
    default_message = "The model call on Future AGI credits failed."
    http_status = 502
    error_type = "server_error"

    def __init__(self, *, retryable=False, status_code=None, code=None, message=None):
        super().__init__(code, message, retryable=retryable)
        self.status_code = status_code


class TerminalManagedRouteCallFailed(ManagedRouteCallFailed, ValueError):
    """A terminal meter outcome; ValueError also blocks row/activity retries."""


class TerminalManagedRouteRefused(ManagedRouteRefused, ValueError):
    """Released or charged meter refusal with the route display contract."""


class TerminalInsufficientCreditError(InsufficientCreditError, ValueError):
    """Terminal credit refusal; preserves display without enabling retry."""


class ManagedRouteTimeout(ManagedRouteCallFailed, TimeoutError):
    """A managed call timed out (a TimeoutError, so callers that handle
    timeouts still do)."""

    code = "MANAGED_ROUTE_TIMEOUT"
    default_message = "The model call on Future AGI credits timed out."
    http_status = 504

    def __init__(self, *, retryable=True, status_code=None, code=None, message=None):
        super().__init__(
            retryable=retryable, status_code=status_code, code=code, message=message
        )


class ManagedRouteBadRequest(ModelRouteError):
    """The request cannot be sent on a managed route (no messages)."""

    code = "MANAGED_ROUTE_BAD_REQUEST"
    default_message = "This request cannot be sent on Future AGI credits."
    http_status = 400
    error_type = "invalid_request_error"


class ModelRouteScopeError(ModelRouteError):
    """The workspace is not in the organization the route is resolved for."""

    code = "MODEL_ROUTE_SCOPE_MISMATCH"
    default_message = "The workspace does not belong to this organization."
    http_status = 403
    error_type = "permission_error"


class ModelRouteUnavailable(ValueError):
    """Neither a stored provider credential nor a managed route: the refusal
    ``get_api_key`` has always raised, same type (ValueError) and text."""

    code = "MODEL_ROUTE_UNAVAILABLE"

    def __init__(self, provider):
        self.provider = provider
        super().__init__(NOT_CONFIGURED_MESSAGE.format(provider=provider))


def _decimal_or_none(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _decimal_text(value):
    return None if value is None else str(value)


def managed_billing_url():
    """Where to add credits: server configuration only
    (``MANAGED_CREDITS_BILLING_URL``), never a value from a gateway body."""
    try:
        from django.conf import settings

        value = getattr(settings, "MANAGED_CREDITS_BILLING_URL", None)
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


# ------------------------------------------------------------------ route ---


@dataclass(frozen=True, repr=False)
class ManagedRouteBinding:
    """A managed route bound to an organization, workspace, model and
    operation, checked (tenancy, eligibility) but not admitted: each call
    made with it is admitted on its own (``managed_chat_completion``).

    What ``LiteLLMModelManager.get_api_key(allow_managed=True)`` returns for
    a key-less, managed-eligible model, and what ``LLM(model_route=...)``
    binds. It is not a provider key: it is never a str or dict, and turning
    it into text (a header, a URL) raises TypeError."""

    organization_id: str
    workspace_id: str | None
    model_name: str
    provider: str
    operation: str
    port: Any = field(compare=False)

    # The contract's route fields for a managed route.
    mode = MODE_MANAGED
    payer = PAYER_FUTURE_AGI_CREDITS
    credential = None
    gateway_route = None
    request_id = None

    @property
    def is_managed(self) -> bool:
        return True

    def __repr__(self) -> str:
        return (
            f"<ManagedRouteBinding model={self.model_name!r} "
            f"operation={self.operation!r}>"
        )

    def __str__(self) -> str:
        raise TypeError("a managed route binding is not a provider key")

    def __bool__(self) -> bool:
        return True

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def is_managed_route_binding(value) -> bool:
    return isinstance(value, ManagedRouteBinding)


@dataclass(frozen=True)
class ModelRoute:
    """The contract's route: mode 'managed'|'byok', provider, credential
    (BYOK only, opaque, never printed), gateway_route (an admitted managed
    request only, opaque, never printed) and payer; plus the identity it was
    resolved for."""

    mode: str
    provider: str
    credential: Any = field(default=None, repr=False, compare=False)
    gateway_route: Any = field(default=None, repr=False, compare=False)
    payer: str = PAYER_OWN_PROVIDER_KEY
    model_name: str = ""
    operation: str = ""
    organization_id: str | None = None
    workspace_id: str | None = None
    request_id: str | None = None
    port: Any = field(default=None, repr=False, compare=False)

    @property
    def is_managed(self) -> bool:
        return self.mode == MODE_MANAGED


def new_request_id() -> str:
    return uuid.uuid4().hex


# ------------------------------------------------------------------- port ---


@runtime_checkable
class GatewayManagedRouting(Protocol):
    """The gateway-owned port (R3-01/R3-10). ``gateway_route`` is opaque here:
    the port mints it in ``authorize`` and reads it back in
    ``request_headers`` and ``release``."""

    def eligible_models(
        self, *, organization_id: str, workspace_id: str | None, model_names
    ) -> frozenset:
        """The subset of ``model_names`` the organization may run on Future
        AGI credits."""

    def authorize(
        self,
        *,
        organization_id: str,
        workspace_id: str | None,
        model_name: str,
        operation: str,
        request_id: str,
    ) -> Any:
        """Admit one managed request (one completion) and return its opaque
        gateway route; raise a ``ModelRouteError`` to refuse."""

    def release(self, gateway_route: Any) -> None:
        """Undo an admission whose request was never sent."""

    def request_headers(self, gateway_route: Any) -> Mapping[str, str]:
        """Headers the gateway expects for this route (identity headers are
        added by the adapter and cannot be overridden)."""

    def client(self, *, is_async: bool) -> Any:
        """OpenAI-compatible client including chat.completions.with_raw_response;
        raw response headers are required for the reconcile identity, or None.
        """

    def before_send(self) -> None:
        """The access check before each send on a retained client (raise to
        refuse)."""


class UnavailableGatewayManagedRouting:
    """The port until the gateway registers its own: no model is eligible,
    so every route stays BYOK exactly as before."""

    def eligible_models(self, *, organization_id, workspace_id, model_names):
        return frozenset()

    def authorize(
        self, *, organization_id, workspace_id, model_name, operation, request_id
    ):
        raise ManagedRouteDenied("MANAGED_ROUTE_UNAVAILABLE")

    def release(self, gateway_route):
        return None

    def request_headers(self, gateway_route):
        return {}

    def client(self, *, is_async):
        return None

    def before_send(self):
        return None


class GatewayClientRouting(UnavailableGatewayManagedRouting):
    """Base for the gateway's real port: the client is the existing gateway
    managed client, re-checked for access before every send. Eligibility,
    admission and release stay the gateway's to define (a subclass that
    admits must release too)."""

    def client(self, *, is_async):
        try:
            from ee.usage.services.gateway_llm_client import (
                get_async_gateway_client,
                get_gateway_client,
            )
        except ImportError:
            return None
        return get_async_gateway_client() if is_async else get_gateway_client()

    def before_send(self):
        try:
            from ee.usage.services.gateway_llm_client import require_gateway_access
        except ImportError:
            raise ManagedRouteDenied("MANAGED_GATEWAY_UNAVAILABLE") from None
        require_gateway_access()


_DEFAULT_PORT = UnavailableGatewayManagedRouting()
_port_lock = threading.Lock()
_port: Any = None


def get_managed_routing_port():
    port = _port
    return _DEFAULT_PORT if port is None else port


def set_managed_routing_port(port):
    """Register the gateway's port (None restores the default); returns the
    previous registration."""
    global _port
    if port is not None and not isinstance(port, GatewayManagedRouting):
        raise TypeError("port must implement GatewayManagedRouting")
    with _port_lock:
        previous, _port = _port, port
    return previous


@contextlib.contextmanager
def managed_routing_port(port) -> Iterator[Any]:
    previous = set_managed_routing_port(port)
    try:
        yield port
    finally:
        set_managed_routing_port(previous)


@dataclass(frozen=True)
class MockGatewayRoute:
    route_id: str
    organization_id: str
    workspace_id: str | None
    model_name: str
    operation: str
    request_id: str


class MockGatewayManagedRouting:
    """Test double of the gateway port: answers eligibility and admission as
    the gateway would and records every call (``events`` keeps their order).
    Credentials, money verdicts and tenancy checks stay real in the code
    under test.

    ``eligible``: {organization_id: model names}; ``insufficient``:
    {organization_id: (required, available)} refused at admission with
    managed_credits_exhausted; ``deny``: organization ids refused at
    admission; ``eligibility_error``: raised by eligible_models;
    ``headers_error``: raised by request_headers; ``before_send_error``:
    raised by before_send.
    """

    def __init__(
        self,
        *,
        eligible=None,
        insufficient=None,
        deny=(),
        client=None,
        async_client=None,
        route_headers=None,
        eligibility_error=None,
        headers_error=None,
        before_send_error=None,
    ):
        self.eligible = {
            str(org): frozenset(names) for org, names in (eligible or {}).items()
        }
        self.insufficient = {
            str(org): tuple(amounts) for org, amounts in (insufficient or {}).items()
        }
        self.deny = frozenset(str(org) for org in deny)
        self._client = client
        self._async_client = async_client
        self.route_headers = dict(route_headers or {})
        self.eligibility_error = eligibility_error
        self.headers_error = headers_error
        self.before_send_error = before_send_error
        self.events: list[str] = []
        self.eligibility_calls: list[dict] = []
        self.authorize_calls: list[dict] = []
        self.authorize_threads: list[int] = []
        self.release_calls: list[Any] = []
        self.before_send_calls = 0

    def eligible_models(self, *, organization_id, workspace_id, model_names):
        names = tuple(model_names)
        self.events.append("eligible_models")
        self.eligibility_calls.append(
            {
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "model_names": names,
            }
        )
        if self.eligibility_error is not None:
            raise self.eligibility_error
        allowed = self.eligible.get(str(organization_id), frozenset())
        return frozenset(name for name in names if name in allowed)

    def authorize(
        self, *, organization_id, workspace_id, model_name, operation, request_id
    ):
        org = str(organization_id)
        self.events.append("authorize")
        self.authorize_threads.append(threading.get_ident())
        self.authorize_calls.append(
            {
                "organization_id": org,
                "workspace_id": workspace_id,
                "model_name": model_name,
                "operation": operation,
                "request_id": request_id,
            }
        )
        if model_name not in self.eligible.get(org, frozenset()):
            raise ManagedRouteDenied()
        if org in self.insufficient:
            required, available = self.insufficient[org]
            raise InsufficientCreditError(
                "managed_credits_exhausted",
                REFUSAL_CONTRACT[(402, "managed_credits_exhausted")].message,
                required=required,
                available=available,
                remedy="add_credits",
            )
        if org in self.deny:
            raise ManagedRouteDenied()
        return MockGatewayRoute(
            route_id=f"mock-route-{len(self.authorize_calls)}",
            organization_id=org,
            workspace_id=workspace_id,
            model_name=model_name,
            operation=operation,
            request_id=request_id,
        )

    def release(self, gateway_route):
        self.events.append("release")
        self.release_calls.append(gateway_route)

    def request_headers(self, gateway_route):
        self.events.append("request_headers")
        if self.headers_error is not None:
            raise self.headers_error
        return {"X-Gateway-Route": gateway_route.route_id, **self.route_headers}

    def client(self, *, is_async):
        return self._async_client if is_async else self._client

    def before_send(self):
        self.events.append("before_send")
        self.before_send_calls += 1
        if self.before_send_error is not None:
            raise self.before_send_error


# ------------------------------------------------------------------ scope ---


def _id_of(value) -> str | None:
    if value is None or value == "":
        return None
    if hasattr(value, "_meta"):  # a model instance
        return str(value.pk) if value.pk is not None else None
    return str(value)


def _scope_ids(organization, workspace) -> tuple[str | None, str | None]:
    """Organization and workspace ids; a workspace instance of another
    organization is refused."""
    org_id = _id_of(organization)
    ws_id = _id_of(workspace)
    if hasattr(workspace, "_meta") and org_id is not None:
        ws_org = getattr(workspace, "organization_id", None)
        if ws_org is not None and str(ws_org) != org_id:
            raise ModelRouteScopeError()
    return org_id, ws_id


def _require_workspace_in_organization(org_id: str, ws_id: str | None) -> None:
    if not ws_id:
        return
    from django.core.exceptions import ValidationError

    from accounts.models.workspace import Workspace

    try:
        exists = Workspace.objects.filter(id=ws_id, organization_id=org_id).exists()
    except (ValueError, ValidationError):
        exists = False
    if not exists:
        raise ModelRouteScopeError()


def _require_operation(operation) -> None:
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown model route operation: {operation!r}")


def _exception_chain(exc) -> list:
    seen, pending, chain = set(), [exc], []
    while pending and len(chain) < 32:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        pending.extend([current.__cause__, current.__context__])
    return chain


def _is_license_denial(exc) -> bool:
    """The self-hosted keyless denial: final, passed on unchanged."""
    try:
        from ee.licensing.activation_client import ManagedServiceError
    except ImportError:
        return False
    return (
        isinstance(exc, ManagedServiceError)
        and getattr(exc, "code", None) == "NO_ENTERPRISE_LICENSE"
    )


# -------------------------------------------------------------- resolution ---


def _port_eligible(port, org_id, ws_id, model_name, operation) -> bool:
    """One eligibility read; a failed read is a typed refusal (never an
    implicit yes or no)."""
    error = None
    try:
        eligible = port.eligible_models(
            organization_id=org_id, workspace_id=ws_id, model_names=(model_name,)
        )
    except ModelRouteError as exc:
        error = exc.detached()
    except Exception as exc:
        logger.warning(
            "managed_route_eligibility_failed",
            operation=operation,
            error_type=type(exc).__name__,
        )
        error = ManagedRouteDenied("MANAGED_ROUTE_UNAVAILABLE", retryable=True)
    if error is not None:
        raise error
    return model_name in frozenset(eligible or ())


def is_managed_eligible(
    *, organization_id, workspace_id, model_name, model_mode="chat"
) -> bool:
    """Whether the port admits the model for the organization on Future AGI
    credits. An eligibility read only: nothing is admitted or held."""
    org_id = _id_of(organization_id)
    ws_id = _id_of(workspace_id)
    if not org_id or not model_name or model_mode not in MANAGED_MODEL_MODES:
        return False
    port = get_managed_routing_port()
    if port is _DEFAULT_PORT:
        return False
    _require_workspace_in_organization(org_id, ws_id)
    return _port_eligible(port, org_id, ws_id, model_name, None)


def bind_managed_route(
    *,
    organization_id,
    workspace_id,
    model_name,
    provider,
    operation,
    model_mode="chat",
) -> ManagedRouteBinding:
    """A managed binding for a key-less model the port admits for this
    organization (workspace checked against it first). Nothing is admitted:
    each call made with the binding is. ``ModelRouteUnavailable`` (the
    legacy refusal) when the model is not managed-eligible here; a
    ``ModelRouteError`` when the eligibility read fails or the scope is
    wrong."""
    _require_operation(operation)
    org_id = _id_of(organization_id)
    ws_id = _id_of(workspace_id)
    if not org_id or not model_name or model_mode not in MANAGED_MODEL_MODES:
        raise ModelRouteUnavailable(provider)
    port = get_managed_routing_port()
    if port is _DEFAULT_PORT:
        # No gateway port registered: the legacy refusal, no extra queries.
        raise ModelRouteUnavailable(provider)
    _require_workspace_in_organization(org_id, ws_id)
    if not _port_eligible(port, org_id, ws_id, model_name, operation):
        raise ModelRouteUnavailable(provider)
    return ManagedRouteBinding(
        organization_id=org_id,
        workspace_id=ws_id,
        model_name=model_name,
        provider=provider or "",
        operation=operation,
        port=port,
    )


def resolve_model_route(
    *, organization, workspace, model_name, operation, provider=None
):
    """The contract's resolver: a BYOK ``ModelRoute`` when a stored
    credential exists (found exactly as ``get_api_key`` finds it), else a
    ``ManagedRouteBinding`` (mode 'managed', payer future_agi_credits; its
    gateway_route is minted per call by admission), else
    ``ModelRouteUnavailable`` (the legacy refusal)."""
    _require_operation(operation)
    org_id, ws_id = _scope_ids(organization, workspace)

    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    manager = LiteLLMModelManager(model_name)
    lookup = manager.stored_credential_lookup(org_id, ws_id, provider)
    if lookup.found:
        return ModelRoute(
            mode=MODE_BYOK,
            provider=lookup.provider,
            credential=lookup.value,
            gateway_route=None,
            payer=PAYER_OWN_PROVIDER_KEY,
            model_name=model_name,
            operation=operation,
            organization_id=org_id,
            workspace_id=ws_id,
        )
    return bind_managed_route(
        organization_id=org_id,
        workspace_id=lookup.workspace_id,
        model_name=model_name,
        provider=lookup.provider,
        operation=operation,
        model_mode=manager.model_mode(),
    )


def _require_binding(binding) -> ManagedRouteBinding:
    if not isinstance(binding, ManagedRouteBinding):
        raise TypeError("a ManagedRouteBinding is required")
    if not binding.organization_id:
        raise ModelRouteScopeError()
    return binding


def admit_managed_request(binding, *, request_id=None) -> ModelRoute:
    """Admit one managed request for the binding: a fresh request id (unless
    given) and the port's opaque gateway route."""
    binding = _require_binding(binding)
    request_id = str(request_id) if request_id else new_request_id()
    error = None
    gateway_route = None
    try:
        gateway_route = binding.port.authorize(
            organization_id=binding.organization_id,
            workspace_id=binding.workspace_id,
            model_name=binding.model_name,
            operation=binding.operation,
            request_id=request_id,
        )
    except ModelRouteError as exc:
        error = exc.detached()
    except Exception as exc:
        logger.warning(
            "managed_route_admission_failed",
            operation=binding.operation,
            error_type=type(exc).__name__,
        )
        error = ManagedRouteDenied("MANAGED_ROUTE_UNAVAILABLE", retryable=True)
    if error is not None:
        raise error
    if gateway_route is None:
        raise ManagedRouteDenied()
    return ModelRoute(
        mode=MODE_MANAGED,
        provider=binding.provider,
        credential=None,
        gateway_route=gateway_route,
        payer=PAYER_FUTURE_AGI_CREDITS,
        model_name=binding.model_name,
        operation=binding.operation,
        organization_id=binding.organization_id,
        workspace_id=binding.workspace_id,
        request_id=request_id,
        port=binding.port,
    )


def release_managed_request(route: ModelRoute) -> None:
    """Undo an admission whose request was never sent. A failed release is
    logged, never raised over the refusal that caused it."""
    try:
        route.port.release(route.gateway_route)
    except Exception as exc:
        logger.warning(
            "managed_route_release_failed",
            operation=route.operation,
            request_id=route.request_id,
            error_type=type(exc).__name__,
        )


# --------------------------------------------------------------- transport ---


def _require_admitted(route) -> ModelRoute:
    if (
        not isinstance(route, ModelRoute)
        or not route.is_managed
        or route.gateway_route is None
    ):
        raise TypeError("an admitted managed ModelRoute is required")
    if not route.organization_id or not route.request_id:
        raise ModelRouteScopeError()
    return route


def managed_request_headers(route: ModelRoute) -> dict[str, str]:
    """The port's headers for an admitted route, then the identity headers,
    which the port cannot override."""
    _require_admitted(route)
    headers = {}
    for name, value in dict(
        route.port.request_headers(route.gateway_route) or {}
    ).items():
        if str(name).strip().lower() in _IDENTITY_HEADER_NAMES:
            continue
        headers[str(name)] = str(value)
    headers[ORG_HEADER] = str(route.organization_id)
    if route.workspace_id:
        headers[WORKSPACE_HEADER] = str(route.workspace_id)
    headers[REQUEST_HEADER] = str(route.request_id)
    return headers


def _gateway_client(port, *, is_async: bool):
    """The gateway managed client; a refusal (never None and never a
    provider client) when the gateway is unavailable."""
    error = None
    client = None
    try:
        client = port.client(is_async=is_async)
    except ModelRouteError as exc:
        error = exc.detached()
    except Exception as exc:
        logger.warning("managed_gateway_client_failed", error_type=type(exc).__name__)
        error = ManagedRouteDenied("MANAGED_GATEWAY_UNAVAILABLE", retryable=True)
    if error is not None:
        raise error
    if client is None:
        raise ManagedRouteDenied("MANAGED_GATEWAY_UNAVAILABLE", retryable=True)
    return client


def _before_send(port) -> None:
    """The port's access check before a send: the license denial passes on
    unchanged; any other failure is a typed refusal."""
    error = None
    try:
        port.before_send()
    except ModelRouteError as exc:
        error = exc.detached()
    except Exception as exc:
        if _is_license_denial(exc):
            raise
        logger.warning("managed_access_check_failed", error_type=type(exc).__name__)
        error = ManagedRouteDenied("MANAGED_GATEWAY_UNAVAILABLE", retryable=True)
    if error is not None:
        raise error


# OpenAI chat-completions arguments a managed request may carry. Everything
# else in a caller's payload is dropped: credentials and addresses (api_key,
# api_base, base_url, client, vertex/aws credentials), litellm routing
# (custom_llm_provider, drop_params), and caller headers (the identity
# headers are set per admitted request).
_GATEWAY_REQUEST_FIELDS = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "frequency_penalty",
    "presence_penalty",
    "response_format",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "stream",
    "stream_options",
    "reasoning_effort",
    "stop",
    "seed",
    "n",
    "logprobs",
    "top_logprobs",
    "logit_bias",
    "timeout",
)
# Model parameters the gateway takes in the request body.
_GATEWAY_EXTRA_BODY_FIELDS = ("thinking",)


def gateway_request_kwargs(binding, request: Mapping) -> dict:
    """The chat-completions arguments for one managed request: the
    binding's model only (another model is refused before admission), the
    messages, and the allowed parameters."""
    binding = _require_binding(binding)
    model = request.get("model", binding.model_name)
    if isinstance(model, (list, tuple)) and len(model) == 1:
        model = model[0]
    if model != binding.model_name:
        raise ManagedRouteDenied("MANAGED_ROUTE_MODEL_MISMATCH")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ManagedRouteBadRequest()
    kwargs = {"model": binding.model_name, "messages": messages}
    for name in _GATEWAY_REQUEST_FIELDS:
        value = request.get(name)
        if value is None or (isinstance(value, (list, dict)) and not value):
            continue
        kwargs[name] = value
    extra_body = {
        name: request[name]
        for name in _GATEWAY_EXTRA_BODY_FIELDS
        if request.get(name) is not None
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def _prepare_dispatch(binding, request, *, is_async, deadline_monotonic=None):
    """Everything before the send, in this order: the request (model,
    messages), the gateway client, the caller's deadline and the access
    check, none of which admits; then the admission and its headers. An
    admission whose headers cannot be built is released, so nothing stays
    admitted without a send."""
    binding = _require_binding(binding)
    kwargs = gateway_request_kwargs(binding, request)
    port = binding.port
    client = _gateway_client(port, is_async=is_async)
    if deadline_monotonic is not None:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("tool completion deadline exceeded")
        with_options = getattr(client, "with_options", None)
        if callable(with_options):
            client = with_options(timeout=remaining, max_retries=0)
        else:
            kwargs["timeout"] = remaining
    _before_send(port)
    from model_hub.services import credit_ledger_port as ledger
    call = ledger.current_managed_call()
    if call is None:
        ledger.require_managed_hold(model_name=binding.model_name)
    if call.dispatched:
        raise ledger.CreditHoldMissingError("A managed hold can pay for only one provider call.")
    pinned = _require_binding(call.route.route)
    # A context from another tenant, workspace, model or transport cannot fund
    # this send. Never re-resolve a held payer route.
    if (
        pinned.organization_id != binding.organization_id
        or pinned.workspace_id != binding.workspace_id
        or pinned.model_name != binding.model_name
        or pinned.port is not binding.port
        or call.envelope.organization_id != binding.organization_id
        or call.envelope.workspace_id != binding.workspace_id
    ):
        raise ModelRouteScopeError()
    route = admit_managed_request(pinned, request_id=call.request_id)
    error = None
    try:
        kwargs["extra_headers"] = managed_request_headers(route)
    except BaseException as exc:
        release_managed_request(route)
        if not isinstance(exc, Exception):
            raise
        logger.warning(
            "managed_route_headers_failed",
            operation=route.operation,
            request_id=route.request_id,
            error_type=type(exc).__name__,
        )
        error = ManagedRouteDenied("MANAGED_ROUTE_UNAVAILABLE", retryable=True)
    if error is not None:
        raise error
    return client, route, kwargs


def _dispatch_failure(route: ModelRoute, exc: BaseException) -> ModelRouteError:
    error = managed_call_error(exc)
    status = getattr(exc, "status_code", None)
    logger.warning(
        "managed_route_call_failed",
        operation=route.operation,
        request_id=route.request_id,
        code=error.code,
        status=status if isinstance(status, int) else None,
        error_type=type(exc).__name__,
    )
    return error


def _completed(route: ModelRoute, response, *, stream: bool, call):
    if stream:
        return ManagedStream(response, route, call)
    if response is None or not getattr(response, "choices", None):
        raise ManagedRouteCallFailed()
    # Record output and usage before caller validation, including under an
    # outer meter. Settlement still belongs to the owning meter's exit.
    call.observe(response=response)
    return response


def _managed_chat_completion_held(binding, request: Mapping, *, deadline_monotonic=None):
    """One managed chat completion: one admission, one request id, one
    request through the gateway managed client (the SDK's retries of it
    carry the same id). A failure is a typed refusal with no exception
    chain; a stream yields typed refusals too (``ManagedStream``)."""
    client, route, kwargs = _prepare_dispatch(
        binding, request, is_async=False, deadline_monotonic=deadline_monotonic
    )
    from model_hub.services.credit_ledger_port import note_gateway_failure, note_gateway_response, require_managed_hold

    error = None
    response = None
    call = require_managed_hold(model_name=route.model_name)
    try:
        raw = client.chat.completions.with_raw_response.create(**kwargs)
        note_gateway_response(call, raw)
        response = raw.parse()
    except Exception as exc:
        note_gateway_failure(call, exc)
        if _is_license_denial(exc):
            raise
        error = _dispatch_failure(route, exc)
    if error is not None:
        raise error
    return _completed(route, response, stream=bool(kwargs.get("stream")), call=call)


async def _managed_chat_completion_async_held(binding, request: Mapping):
    """The async ``managed_chat_completion``. Admission, the access check,
    the client and the port headers can block (ORM, token refresh), so they
    run off the event loop."""
    from asgiref.sync import sync_to_async

    client, route, kwargs = await sync_to_async(
        _prepare_dispatch, thread_sensitive=True
    )(binding, request, is_async=True)
    from model_hub.services.credit_ledger_port import note_gateway_failure, note_gateway_response, require_managed_hold

    error = None
    response = None
    call = require_managed_hold(model_name=route.model_name)
    try:
        raw = await client.chat.completions.with_raw_response.create(**kwargs)
        note_gateway_response(call, raw)
        parsed = raw.parse()
        response = await parsed if inspect.isawaitable(parsed) else parsed
    except Exception as exc:
        note_gateway_failure(call, exc)
        if _is_license_denial(exc):
            raise
        error = _dispatch_failure(route, exc)
    if error is not None:
        raise error
    return _completed(route, response, stream=bool(kwargs.get("stream")), call=call)


def _transport_meter(binding):
    """Standalone eval/helper calls use the same meter as outer surfaces."""
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from model_hub.services import credit_ledger_port as ledger

    binding = _require_binding(binding)
    if ledger._UNMETERED_SURFACE.get() is not None:
        raise ledger.ManagedDispatchUnavailableError()
    if ledger.current_managed_call() is not None:
        return None
    if binding.operation == OP_CUSTOM_PROMPT_EVAL and ledger.legacy_billing_installed():
        # One key (OK-COMPOSE r2): a judge runs on its own meter only where
        # EvaluationRunner opted into single-meter billing, which it never does
        # while the legacy combined billing is installed (that debit and the
        # evaluator's cost event would bill this call again). Refused before
        # anything is admitted or sent.
        raise ledger.ManagedDispatchUnavailableError(ledger.MANAGED_JUDGE_CUT_MESSAGE)
    _require_workspace_in_organization(binding.organization_id, binding.workspace_id)
    organization = Organization.objects.get(pk=binding.organization_id)
    workspace = None
    if binding.workspace_id is not None:
        workspace = Workspace.objects.get(
            pk=binding.workspace_id, organization_id=binding.organization_id
        )
    return ledger.CallMeter(
        organization=organization, workspace=workspace,
        route=ledger.PayerRoute.coerce(binding),
        envelope=ledger.build_envelope(
            organization=organization, workspace=workspace,
            purpose=binding.operation, source="managed_transport",
            source_id=new_request_id(), model=binding.model_name,
        ),
    )


def _charged_failure(error: ModelRouteError, held: list) -> ModelRouteError:
    """Finish a detached standalone error from the call's actual state.

    The original normalized code/type/status/remedy remain for display, but
    settled calls disclose their charge through every raw message consumer.
    This never infers a debit or refund from the exception itself.
    """
    from model_hub.services import credit_ledger_port as ledger

    call = held[0] if held else None
    if getattr(call, "dispatched", False) is not True:
        return error
    status = getattr(call, "status", None)
    if status in ("settle_failed", "release_failed", "pending_reconcile"):
        refusal = (ledger.ManagedOutcomePendingReconcileError() if status == "pending_reconcile"
                   else ledger.CreditSettlementError())
        refusal.gateway_display = managed_gateway_display(error)
        return _terminal_meter_error(refusal)
    if status not in ("settled", "released_refused"):
        return error
    error.dispatched = status == "settled"
    error.charged = status == "settled"
    error.retryable = False
    error.backoff = False
    error.retry_after = None
    if getattr(error, "remedy", None) == ledger.REMEDY_RETRY:
        error.remedy = None
    if isinstance(getattr(error, "details", None), dict):
        error.details = {
            key: value for key, value in error.details.items()
            if not (key == "remedy_hint" and value == ledger.REMEDY_RETRY)
        }
    if error.charged:
        if error.code == ledger.CODE_BILLING_UNAVAILABLE:
            copy = ledger.CreditLedgerUnavailableAfterDispatchError()
        elif error.code in ledger.MANAGED_REFUSAL_CODES or error.code in (
            "MANAGED_CREDITS_REFUSED", "MANAGED_ROUTE_LIMITED", "MANAGED_ROUTE_DENIED",
        ):
            copy = ledger.ManagedRefusedAfterDispatchError()
        else:
            copy = ledger.ManagedCallFailedAfterDispatchError()
        error.message = copy.message
        error.args = (error.message,)
    return error


def managed_chat_completion(binding, request: Mapping, *, deadline_monotonic=None):
    from model_hub.services import credit_ledger_port as ledger
    gateway_request_kwargs(binding, request)
    meter = _transport_meter(binding)
    if meter is None:
        return _managed_chat_completion_held(binding, request, deadline_monotonic=deadline_monotonic)
    kwargs = {"messages": request.get("messages"), "max_tokens": request.get("max_completion_tokens") or request.get("max_tokens")}
    if request.get("stream"):
        def chunks():
            held = []
            error = None
            try:
                with meter.metered(**kwargs) as call:
                    held.append(call)
                    stream = _managed_chat_completion_held(binding, request, deadline_monotonic=deadline_monotonic)
                    for chunk in stream:
                        call.observe(response=chunk)
                        yield chunk
            except Exception as exc:
                error = managed_route_failure(exc)
                if error is None:
                    raise
                error = _charged_failure(error, held)
            if error is not None:
                raise error
        return chunks()
    held = []
    try:
        with meter.metered(**kwargs) as call:
            held.append(call)
            response = _managed_chat_completion_held(binding, request, deadline_monotonic=deadline_monotonic)
        return response
    except Exception as exc:
        error = managed_route_failure(exc)
        if error is None:
            raise
        error = _charged_failure(error, held)
    raise error


async def managed_chat_completion_async(binding, request: Mapping):
    from asgiref.sync import sync_to_async
    gateway_request_kwargs(binding, request)
    meter = await sync_to_async(_transport_meter, thread_sensitive=True)(binding)
    if meter is None:
        return await _managed_chat_completion_async_held(binding, request)
    kwargs = {"messages": request.get("messages"), "max_tokens": request.get("max_completion_tokens") or request.get("max_tokens")}
    if request.get("stream"):
        async def chunks():
            held = []
            error = None
            try:
                async with meter.ametered(**kwargs) as call:
                    held.append(call)
                    stream = await _managed_chat_completion_async_held(binding, request)
                    async for chunk in stream:
                        call.observe(response=chunk)
                        yield chunk
            except Exception as exc:
                error = managed_route_failure(exc)
                if error is None:
                    raise
                error = _charged_failure(error, held)
            if error is not None:
                raise error
        return chunks()
    held = []
    try:
        async with meter.ametered(**kwargs) as call:
            held.append(call)
            response = await _managed_chat_completion_async_held(binding, request)
        return response
    except Exception as exc:
        error = managed_route_failure(exc)
        if error is None:
            raise
        error = _charged_failure(error, held)
    raise error


class ManagedStream:
    """A managed streaming response: chunks pass through unchanged; a
    failure while streaming becomes the typed refusal, raised with no
    exception chain. Everything else is the stream's own."""

    def __init__(self, stream, route: ModelRoute, call):
        self._stream = stream
        self._route = route
        # Bind the claimed call, not the ambient context at iteration time.
        self._call = call
        # SDK streams retain the HTTP response. Read its headers, not chunks or
        # error metadata; the raw create response may already have recorded it.
        from model_hub.services.credit_ledger_port import note_gateway_response

        note_gateway_response(call, getattr(stream, "response", None))
        self._iterator = None
        self._async_iterator = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stream, name)

    def __iter__(self):
        return self

    def __next__(self):
        error = None
        try:
            if self._iterator is None:
                self._iterator = iter(self._stream)
            chunk = next(self._iterator)
            self._call.output_seen = True
            return chunk
        except StopIteration:
            raise
        except Exception as exc:
            from model_hub.services.credit_ledger_port import note_gateway_failure

            note_gateway_failure(self._call, exc)
            if _is_license_denial(exc):
                raise
            error = _dispatch_failure(self._route, exc)
        raise error

    def __aiter__(self):
        return self

    async def __anext__(self):
        error = None
        try:
            if self._async_iterator is None:
                self._async_iterator = self._stream.__aiter__()
            chunk = await self._async_iterator.__anext__()
            self._call.output_seen = True
            return chunk
        except StopAsyncIteration:
            raise
        except Exception as exc:
            from model_hub.services.credit_ledger_port import note_gateway_failure

            note_gateway_failure(self._call, exc)
            if _is_license_denial(exc):
                raise
            error = _dispatch_failure(self._route, exc)
        raise error


def managed_binding_in(kwargs: Mapping):
    """The managed binding a litellm-style call carries as ``api_key``, or
    None. A binding anywhere else is refused: it is never handed to litellm
    or to a provider SDK."""
    binding = kwargs.get("api_key")
    for name, value in kwargs.items():
        if name != "api_key" and isinstance(value, ManagedRouteBinding):
            raise ManagedRouteDenied("MANAGED_ROUTE_MISPLACED")
    return binding if isinstance(binding, ManagedRouteBinding) else None


# ------------------------------------------------------------------ errors ---


_TIMEOUT_CLASS_NAMES = frozenset(
    {
        "APITimeoutError",
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "Timeout",
    }
)
_UPSTREAM_MODULE_PREFIXES = ("openai", "httpx", "httpcore", "litellm", "anthropic")


def _is_timeout(exc) -> bool:
    return any(
        isinstance(error, TimeoutError) or type(error).__name__ in _TIMEOUT_CLASS_NAMES
        for error in _exception_chain(exc)
    )


def _is_upstream_error(exc) -> bool:
    """Whether ``exc`` came from the gateway, a provider or the transport
    (its text is not ours to show)."""
    if isinstance(getattr(exc, "status_code", None), int):
        return True
    if isinstance(getattr(getattr(exc, "response", None), "status_code", None), int):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, RequestsTimeout)):
        return True
    return type(exc).__module__.split(".")[0] in _UPSTREAM_MODULE_PREFIXES


def _error_body(exc) -> dict:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return {}
    inner = body.get("error")
    if isinstance(inner, dict):
        return {**{k: v for k, v in body.items() if k != "error"}, **inner}
    return body


def _retry_after(headers):
    try:
        values = [value for key, value in headers.items()
                  if isinstance(key, str) and key.lower() == "retry-after"]
    except (AttributeError, TypeError, ValueError):
        return None
    return positive_retry_after(values[0]) if len(values) == 1 else None


def _gateway_error_parts(exc):
    return managed_refusal_parts(exc)


def _refusal_metadata(body, code=None):
    return managed_refusal_metadata(code, body)


def _contract_refusal(code, spec, body, retry_after):
    display = classify_managed_refusal(spec.http_status, code, body, retry_after)
    details = display["metadata"]
    common = dict(http_status=display["status"], error_type=display["type"],
                  retryable=display["retryable"], retry_after=display["retry_after"],
                  remedy=display["remedy"], details=details)
    if spec.http_status == 402:
        error = InsufficientCreditError(code, display["message"],
            required=details.get("required_usd"), available=details.get("available_usd"), **common)
    else:
        error = ManagedRouteRefused(code, display["message"], **common)
    error.category = display["category"]
    error.param = display["param"]
    error.display_wait_seconds = display["display_wait_seconds"]
    error.display_classification = display["display_classification"]
    error.metadata = dict(details)
    error.gateway_display = dict(display)
    error.contract_valid = True
    error.is_managed_refusal = True
    return error


def managed_call_error(exc: BaseException) -> ModelRouteError:
    """Exact contract status + structured envelope code; otherwise terminal generic."""
    if isinstance(exc, ModelRouteError):
        return exc.detached()
    from model_hub.services.credit_ledger_port import ManagedCallRefusal, ManagedCreditRefusal

    if isinstance(exc, ManagedCreditRefusal):
        return _terminal_meter_error(exc)
    if isinstance(exc, ManagedCallRefusal):
        # Local deferred refusals are not SDK envelopes. Preserve their local
        # contract without indexing an absent display or making them terminal.
        error = ModelRouteError(exc.code, exc.message,
                                retryable=exc.retryable, retry_after=exc.retry_after)
        error.http_status = exc.http_status
        error.error_type = exc.error_type
        error.metadata = dict(exc.metadata)
        error.gateway_display = managed_gateway_display(exc)
        error.dispatched = exc.dispatched
        error.charged = exc.charged
        error.backoff = exc.backoff
        return error
    status, code, body, retry_after = _gateway_error_parts(exc)
    spec = REFUSAL_CONTRACT.get((status, code)) if isinstance(code, str) else None
    if spec is not None:
        return _contract_refusal(code, spec, body, retry_after)
    error = TerminalManagedRouteCallFailed(retryable=False, status_code=status,
        message=get_error_message("MANAGED_REFUSAL_UNKNOWN"))
    error.gateway_display = managed_gateway_display(exc)
    error.http_status = error.gateway_display["status"]
    error.category = "generic"
    error.display_wait_seconds = None
    error.backoff = False
    error.contract_valid = False
    error.is_managed_refusal = True
    return error


def _terminal_meter_error(refusal):
    """Present r8's already-selected terminal outcome without changing money.

    The ledger's code/class/status/retry flags remain untouched. Only this
    detached terminal route error gets the canonical gateway presentation.
    """
    display = managed_gateway_display(refusal)
    outcome = refusal.code in {"MANAGED_OUTCOME_PENDING_RECONCILE", "CREDIT_SETTLEMENT_FAILED",
                               "MANAGED_CALL_ALREADY_COMPLETED"}
    projected = display is not None and not outcome
    code = display["code"] if projected else refusal.code
    status = display["status"] if projected else refusal.http_status
    error_type = display["type"] if projected else refusal.error_type
    # Only the in-flight display must not restore the retry advice that
    # the ledger already stripped from this terminal refusal.
    metadata = {
        key: value for key, value in (display["metadata"] if projected else refusal.metadata).items()
        if not (projected and display["display_classification"] == "in_flight"
                and key == "remedy_hint" and value == "retry")
    }
    message = display["message"] if projected else refusal.message
    if getattr(refusal, "charged", False) is True:
        message = refusal.message  # The frozen meter's charge disclosure wins.
    if (status, code) in REFUSAL_CONTRACT:
        cls = TerminalInsufficientCreditError if status == 402 else TerminalManagedRouteRefused
        kwargs = dict(http_status=status, error_type=error_type, retryable=False,
                      retry_after=None, details=metadata,
                      remedy=display["remedy"] if projected else getattr(refusal, "remedy", None))
        if cls is TerminalInsufficientCreditError:
            kwargs.update(required=metadata.get("required_usd", refusal.required),
                          available=metadata.get("available_usd", refusal.available))
        error = cls(code, message, **kwargs)
    else:
        error = TerminalManagedRouteCallFailed(code=code, message=message,
                                               retryable=False, status_code=status)
        error.http_status = status
        error.error_type = error_type
    error.gateway_display = display
    error.category = display["category"] if projected else getattr(refusal, "category", "generic")
    error.param = display["param"] if projected else None
    error.display_wait_seconds = display["display_wait_seconds"] if projected else None
    error.contract_valid = bool(projected and display["contract_valid"])
    error.original_refusal = None
    if display is not None:
        error.original_refusal = dict(display, retryable=False, retry_after=None)
    error.is_managed_refusal = True
    error.metadata = metadata
    error.display_classification = display["display_classification"] if projected else None
    error.dispatched = refusal.dispatched
    error.charged = refusal.charged
    error.backoff = False
    error.retry_after = None
    if getattr(error, "remedy", None) == "retry":
        error.remedy = None
    return error


def managed_route_failure(exc: BaseException):
    """For a managed call that failed: the typed refusal it carries (found
    through any wrapping, e.g. litellm_try_except's ``raise Exception(...)
    from root``), else the typed refusal for a gateway, provider or
    transport error in its chain; None for a local error (validation and
    similar, whose text is ours). The result is detached: raising it carries
    no chain."""
    from model_hub.services.credit_ledger_port import ManagedCreditRefusal

    if isinstance(exc, ManagedCreditRefusal):
        return _terminal_meter_error(exc)
    chain = _exception_chain(exc)
    for error in chain:
        if isinstance(error, ManagedCreditRefusal):
            return _terminal_meter_error(error)
    for error in chain:
        if isinstance(error, ModelRouteError):
            return error.detached()
    for error in chain:
        if _is_upstream_error(error):
            return managed_call_error(error)
    return None


def managed_stream_error(api_key, error: BaseException) -> BaseException:
    """For a streaming handler: the typed refusal in place of ``error`` when
    the call ran on a managed binding (its message is what the user sees);
    ``error`` unchanged otherwise."""
    if not isinstance(api_key, ManagedRouteBinding):
        return error
    typed = managed_route_failure(error)
    return typed if typed is not None else error


# ------------------------------------------------------------ availability ---


def availability_fields(
    *, listed_available: bool, byok_credential: bool, managed_available: bool
) -> dict:
    """The contract's model-list fields. ``credential_mode``/``payer`` say
    how the runtime would run the model: BYOK when ``get_api_key`` would find
    a stored credential (``byok_credential``, the runtime's own test), else
    managed when the port admits it, else unavailable. ``is_available``
    keeps its existing meaning (``listed_available``) and is also true for a
    model that runs on Future AGI credits."""
    if byok_credential:
        mode, payer = MODE_BYOK, PAYER_OWN_PROVIDER_KEY
    elif managed_available:
        mode, payer = MODE_MANAGED, PAYER_FUTURE_AGI_CREDITS
    else:
        mode, payer = MODE_UNAVAILABLE, None
    return {
        "is_available": bool(listed_available) or mode == MODE_MANAGED,
        "credential_mode": mode,
        "payer": payer,
    }


def managed_model_names(*, organization, workspace, models: Iterable[Mapping]):
    """Names of the given catalog models (chat mode, not one of the
    organization's custom models) the port admits for this organization.
    For availability reads only: any failure shows no managed model."""
    try:
        org_id, ws_id = _scope_ids(organization, workspace)
    except ModelRouteError:
        logger.warning("managed_availability_scope_mismatch")
        return frozenset()
    port = get_managed_routing_port()
    if not org_id or port is _DEFAULT_PORT:
        return frozenset()
    names = {
        model.get("model_name")
        for model in models
        if model
        and model.get("mode") in MANAGED_MODEL_MODES
        and model.get("model_name")
    }
    if not names:
        return frozenset()
    try:
        from model_hub.models.custom_models import CustomAIModel

        names -= set(
            CustomAIModel.objects.filter(organization_id=org_id).values_list(
                "user_model_id", flat=True
            )
        )
        if not names:
            return frozenset()
        eligible = port.eligible_models(
            organization_id=org_id,
            workspace_id=ws_id,
            model_names=tuple(sorted(names)),
        )
    except Exception as exc:
        logger.warning(
            "managed_availability_unavailable", error_type=type(exc).__name__
        )
        return frozenset()
    return frozenset(name for name in (eligible or ()) if name in names)


# ------------------------------------------------------- auxiliary callers ---


def platform_llm(
    *,
    organization,
    workspace=None,
    operation,
    provider,
    model_name,
    llm_class=None,
    **llm_kwargs,
):
    """An LLM for a platform-chosen auxiliary model (AI eval writer, smart
    filters, criteria helpers) bound to the caller's organization and
    workspace. Managed-eligible: explicit managed mode with a binding (no
    provider key, no fallback; each call admitted on its own). Otherwise the
    existing transport, now carrying the identity."""
    _require_operation(operation)
    if llm_class is None:
        from agentic_eval.core.llm.llm import LLM as llm_class
    org_id, ws_id = _scope_ids(organization, workspace)
    if not org_id:
        return llm_class(provider=provider, model_name=model_name, **llm_kwargs)
    # The identity is bound (workspace in organization) before it is used.
    _require_workspace_in_organization(org_id, ws_id)
    try:
        binding = bind_managed_route(
            organization_id=org_id,
            workspace_id=ws_id,
            model_name=model_name,
            provider=provider,
            operation=operation,
        )
    except ModelRouteUnavailable:
        # Not managed-eligible: the existing transport, with the identity.
        # Every other refusal (eligibility read failed, foreign workspace)
        # propagates.
        return llm_class(
            provider=provider,
            model_name=model_name,
            org_id=org_id,
            workspace_id=ws_id,
            **llm_kwargs,
        )
    return llm_class(
        provider=provider, model_name=model_name, model_route=binding, **llm_kwargs
    )
