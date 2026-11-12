"""Django's half of the EE agent key's scope (CN22c).

An Omega EE agent key may call the gateway's MCP endpoint and its model
endpoints, and nothing else (AM-C25's 22:50 addendum, rule 1). No Django
route accepts a gateway key, so without this a request carrying one gets
whatever the route says about a bad credential. This middleware answers it,
on every route, 403 ``key_scope_denied`` in the app's error envelope, and
audits the refusal with the key's workspace and EE as the actor (one row per
key and route in a minute, however many requests).

Only a live agent key is refused here: any other gateway key, and a revoked
or expired agent key, is the route's own business. A request is looked at
only when it carries a gateway key (the ``sk-agentcc-`` prefix) as a Bearer
token or as X-Api-Key, so other requests cost one string check.
"""

import hashlib

import structlog
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.services import ee_agent_key_policy as policy
from agentcc.services.ee_provisioning_availability import provisioning_enabled
from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)

GATEWAY_KEY_PREFIX = "sk-agentcc-"
AUDIT_WINDOW_SECONDS = 60
SCOPE_DENIED = (
    "This is an Omega EE agent key: it may call only the gateway's MCP endpoint "
    "and its model endpoints (POST /v1/chat/completions, /v1/embeddings and "
    "/v1/responses), not this API."
)


def presented_gateway_key(request) -> str:
    """The gateway key the request carries as a Bearer token or X-Api-Key,
    or ""."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token.startswith(GATEWAY_KEY_PREFIX):
            return token
    api_key = request.headers.get("X-Api-Key", "").strip()
    return api_key if api_key.startswith(GATEWAY_KEY_PREFIX) else ""


def live_agent_key(raw_key: str):
    """The binding of the live agent key ``raw_key`` is, or None."""
    now = timezone.now()
    key = (
        AgentccAPIKey.no_workspace_objects.select_related(
            "ee_agent_key__organization", "ee_agent_key__api_key"
        )
        .filter(
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            status=AgentccAPIKey.ACTIVE,
            deleted=False,
            ee_agent_key__isnull=False,
        )
        .first()
    )
    if key is None or (key.expires_at is not None and key.expires_at <= now):
        return None
    return policy.binding_of(key)


class EEAgentKeyScopeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_key = presented_gateway_key(request)
        if raw_key:
            if (
                not provisioning_enabled()
                and AgentccAPIKey._base_manager.filter(
                    key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                    ee_agent_key__isnull=False,
                ).exists()
            ):
                return JsonResponse(
                    build_error_envelope(
                        "Invalid API key or secret key",
                        status_code=401,
                        code="authentication_failed",
                    ),
                    status=401,
                    headers={"WWW-Authenticate": "ApiKey"},
                )
            binding = live_agent_key(raw_key)
            if binding is not None:
                self._audit(binding, request)
                return JsonResponse(
                    build_error_envelope(
                        SCOPE_DENIED, status_code=403, code=policy.CODE_SCOPE_DENIED
                    ),
                    status=403,
                )
        return self.get_response(request)

    @staticmethod
    def _audit(binding, request):
        path = request.path[:300]
        window = f"cn22c:scope-refused:{binding.id}:{request.method}:{hashlib.sha256(path.encode()).hexdigest()[:16]}"
        try:
            first = cache.add(window, 1, AUDIT_WINDOW_SECONDS)
        except Exception:  # an unreachable cache must not hide the refusal
            first = True
        if not first:
            return
        policy.audit_refusal(
            binding,
            policy.CODE_SCOPE_DENIED,
            surface="django",
            method=request.method,
            path=path,
        )
        logger.warning(
            "ee_agent_key_scope_refused",
            agent_key_id=binding.api_key.gateway_key_id,
            workspace=str(binding.workspace_id),
            method=request.method,
            path=path,
        )
