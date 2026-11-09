"""The EE agent key's budget, usage and refusal audit (CN22c).

- An org Owner or Admin reads and sets the budget each of Omega EE's agent
  keys in a workspace may spend per period
  (``mcp/service/workspaces/<id>/agent-key-policy/``), and reads every agent
  key of the workspace with its budget and this period's spend
  (``mcp/service/workspaces/<id>/agent-keys/``). EE's own service credential
  cannot set a budget. Since CN22d it may read the workspace's agent keys
  (each with the Idempotency-Key it was minted with, and
  ``?mint_idempotency_key=`` to find one), so that it can reconcile a mint
  whose answer it lost; it reads only its own org's workspaces.
- The gateway posts its scope and budget refusals of agent keys
  (``mcp/service/agent-key-refusals/``, admin token), and each is audited
  with the key's workspace and EE as the actor.
"""

import unicodedata
from decimal import Decimal

import structlog
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from accounts.models.workspace import Workspace
from agentcc.models.ee_agent_key_policy import AgentccEEWorkspacePolicy
from agentcc.models.mcp_service_provisioning import AgentccEEServiceCredential
from agentcc.permissions import IsAdminToken, IsOrgGatewayAdmin
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.services import ee_agent_key_mint as mint_idempotency
from agentcc.services import ee_agent_key_policy as policy
from agentcc.services.ee_provisioning_availability import ProvisioningAvailabilityMixin
from agentcc.views.mcp_service_provisioning import (
    EEServiceCredentialIfPresent,
    IsEEServiceCredential,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)

MAX_REFUSAL_EVENTS = 1000

# What a refusal event's method and path may be. The gateway sends them
# cleaned the same way (agentcc-gateway/internal/keyscope/report.go clean);
# an older gateway sends them as the refused request had them, so they are
# cleaned here too, and the refusal is still audited.
REFUSAL_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}
    | {"PIPELINE", "OTHER"}
)
MAX_REFUSAL_PATH = 300


def _clean_refusal_path(path: str) -> str:
    """The path without control, format or surrogate characters (NUL, CR,
    LF, bidi overrides), cut to 300 characters; "-" when nothing is left."""
    kept = "".join(
        ch for ch in path if unicodedata.category(ch) not in ("Cc", "Cf", "Cs")
    )[:MAX_REFUSAL_PATH]
    return kept if kept.strip() else "-"


def _errors(*codes):
    return dict.fromkeys((*codes, 503), AgentccErrorResponseSerializer)


def _not_found() -> Response:
    return Response(
        build_error_envelope("Not found.", status_code=404, code="not_found"),
        status=status.HTTP_404_NOT_FOUND,
    )


def _invalid(errors) -> Response:
    field = next(iter(errors), "")
    messages = errors.get(field) if isinstance(errors, dict) else None
    text = (
        str(messages[0])
        if isinstance(messages, list) and messages
        else "Invalid request."
    )
    details = {
        str(k): [str(m) for m in (v if isinstance(v, list) else [v])]
        for k, v in errors.items()
    }
    return Response(
        build_error_envelope(
            f"{field}: {text}" if field else text, code="invalid", details=details
        ),
        status=status.HTTP_400_BAD_REQUEST,
    )


def _organization(request):
    """The org the request acts on: EE's credential's own, or the person's."""
    if isinstance(request.auth, AgentccEEServiceCredential):
        return request.auth.organization
    return request.organization


def _workspace(request, workspace_id):
    return Workspace.no_workspace_objects.filter(
        pk=workspace_id, organization=_organization(request), is_active=True
    ).first()


# --- Shapes ------------------------------------------------------------------


class AgentKeyPolicyRequestSerializer(serializers.Serializer):
    budget_usd = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=policy.MAX_BUDGET_USD,
        help_text="What each agent key of the workspace may spend per period, in USD.",
    )
    budget_period = serializers.ChoiceField(
        choices=AgentccEEWorkspacePolicy.Period.choices
    )


class AgentKeyPolicySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    budget_usd = serializers.CharField()
    budget_period = serializers.ChoiceField(
        choices=AgentccEEWorkspacePolicy.Period.choices
    )
    default = serializers.BooleanField(help_text="True when no Owner or Admin set one.")
    scopes = serializers.CharField(help_text="What every agent key may call.")
    lifetime_days = serializers.IntegerField()
    org_cap = serializers.IntegerField(help_text="Live agent keys the org may hold.")
    live_agent_keys = serializers.IntegerField()


class AgentKeyPolicyResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AgentKeyPolicySerializer()


class AgentKeyUsageSerializer(serializers.Serializer):
    agent_key_id = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField(allow_null=True)
    last_used_at = serializers.DateTimeField(allow_null=True)
    budget_usd = serializers.CharField()
    budget_period = serializers.CharField()
    period_start = serializers.DateTimeField()
    period_spend_usd = serializers.CharField()
    requests = serializers.IntegerField()
    mint_idempotency_key = serializers.CharField(
        allow_null=True,
        help_text="CN22d: the Idempotency-Key (UUIDv7) EE minted the key with; "
        "null for a key minted before CN22d.",
    )


class AgentKeysQuerySerializer(serializers.Serializer):
    mint_idempotency_key = serializers.CharField(
        required=False,
        help_text="CN22d: only the keys minted with this Idempotency-Key "
        "(UUIDv7): the key EE minted when it lost the answer.",
    )

    def validate_mint_idempotency_key(self, value):
        key = mint_idempotency.normalize(value)
        if key is None:
            raise serializers.ValidationError(mint_idempotency.QUERY_MALFORMED)
        return key


class AgentKeyUsageListSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    keys = AgentKeyUsageSerializer(many=True)


class AgentKeyUsageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = AgentKeyUsageListSerializer()


class _EachRefusalOnItsOwn(serializers.ListSerializer):
    """A batch of refusal events where an event that cannot be read becomes
    None (counted as invalid) instead of failing the batch: one hostile or
    malformed event must not keep the batch's other refusals out of the
    audit log. The list itself (not a list, over the bound) is still 400."""

    def run_child_validation(self, data):
        try:
            return super().run_child_validation(data)
        except serializers.ValidationError:
            return None


class RefusalEventSerializer(serializers.Serializer):
    agent_key_id = serializers.CharField(max_length=64)
    code = serializers.ChoiceField(choices=sorted(policy.REFUSAL_ACTIONS))
    method = serializers.CharField(
        max_length=16,
        help_text="An HTTP method, PIPELINE, or OTHER for any other method.",
    )
    path = serializers.CharField(
        max_length=MAX_REFUSAL_PATH,
        help_text="Without control characters, at most 300 characters.",
    )
    count = serializers.IntegerField(min_value=1)
    first_at = serializers.DateTimeField()
    last_at = serializers.DateTimeField()

    class Meta:
        list_serializer_class = _EachRefusalOnItsOwn

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("method"), str) and (
                data["method"] not in REFUSAL_METHODS
            ):
                data["method"] = "OTHER"
            if isinstance(data.get("path"), str):
                data["path"] = _clean_refusal_path(data["path"])
        return super().to_internal_value(data)


class RefusalsRequestSerializer(serializers.Serializer):
    events = RefusalEventSerializer(many=True, max_length=MAX_REFUSAL_EVENTS)


class RefusalsResultSerializer(serializers.Serializer):
    audited = serializers.IntegerField()
    skipped = serializers.IntegerField(
        help_text="Events for a key that is not an agent key."
    )
    invalid = serializers.IntegerField(
        help_text="Events that could not be read (an unknown code, a missing "
        "field); the batch's other events are audited."
    )


class RefusalsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = RefusalsResultSerializer()


# --- Views -------------------------------------------------------------------


def _describe(org, workspace) -> dict:
    budget = policy.budget_for(workspace.id)
    return {
        "workspace": str(workspace.id),
        "budget_usd": str(budget.usd),
        "budget_period": budget.period,
        "default": budget.default,
        "scopes": policy.SCOPES,
        "lifetime_days": policy.LIFETIME.days,
        "org_cap": policy.cap(),
        "live_agent_keys": policy.live_agent_keys(org).count(),
    }


class WorkspaceAgentKeyPolicyView(ProvisioningAvailabilityMixin, APIView):
    """GET and PUT mcp/service/workspaces/<id>/agent-key-policy/: the budget
    each EE agent key of the workspace may spend per period. A change
    reaches every live key at the gateway's next key sync."""

    permission_classes = [IsAuthenticated, IsOrgGatewayAdmin]

    @validated_request(
        responses={200: AgentKeyPolicyResponseSerializer, **_errors(401, 403, 404)}
    )
    def get(self, request, workspace_id):
        workspace = _workspace(request, workspace_id)
        if workspace is None:
            return _not_found()
        return Response(
            {"status": True, "result": _describe(request.organization, workspace)}
        )

    @validated_request(
        request_serializer=AgentKeyPolicyRequestSerializer,
        responses={
            200: AgentKeyPolicyResponseSerializer,
            **_errors(400, 401, 403, 404),
        },
        reject_unknown_fields=True,
        validation_error_response=_invalid,
    )
    def put(self, request, workspace_id):
        workspace = _workspace(request, workspace_id)
        if workspace is None:
            return _not_found()
        data = request.validated_data
        policy.set_budget(
            request.organization,
            workspace,
            data["budget_usd"],
            data["budget_period"],
            request.user,
        )
        return Response(
            {"status": True, "result": _describe(request.organization, workspace)}
        )


class WorkspaceAgentKeysView(ProvisioningAvailabilityMixin, APIView):
    """GET mcp/service/workspaces/<id>/agent-keys/: every agent key minted
    for the workspace, with its budget, this period's spend, its expiry and
    the Idempotency-Key it was minted with. ``?mint_idempotency_key=`` keeps
    the keys minted with that one (none, one, or more than one when it was
    sent again after 24 hours or under a rotated credential).

    An org Owner or Admin reads it, and so does EE's service credential
    (CN22d), to find a key whose mint answer it lost."""

    authentication_classes = [
        EEServiceCredentialIfPresent,
        *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
    ]
    permission_classes = [IsEEServiceCredential | (IsAuthenticated & IsOrgGatewayAdmin)]

    @validated_request(
        query_serializer=AgentKeysQuerySerializer,
        responses={
            200: AgentKeyUsageResponseSerializer,
            **_errors(400, 401, 403, 404),
        },
        validation_error_response=_invalid,
    )
    def get(self, request, workspace_id):
        workspace = _workspace(request, workspace_id)
        if workspace is None:
            return _not_found()
        keys = policy.workspace_agent_keys(
            _organization(request),
            workspace,
            mint_idempotency_key=request.validated_query_data.get(
                mint_idempotency.QUERY_PARAM
            ),
        )
        return Response(
            {"status": True, "result": {"workspace": str(workspace.id), "keys": keys}}
        )


class AgentKeyRefusalsView(ProvisioningAvailabilityMixin, APIView):
    """POST mcp/service/agent-key-refusals/ (gateway admin token): the
    gateway's refusals of agent keys (keyscope.Reporter), each audited with
    the key's workspace and EE as the actor. Events for a key that is not an
    agent key are skipped; an event that cannot be read is counted as
    invalid, and the batch's other events are still audited."""

    authentication_classes = []
    permission_classes = [IsAdminToken]
    renderer_classes = [JSONRenderer]

    @validated_request(
        request_serializer=RefusalsRequestSerializer,
        responses={200: RefusalsResponseSerializer, **_errors(400, 403)},
        reject_unknown_fields=True,
        validation_error_response=_invalid,
    )
    def post(self, request):
        received = request.validated_data["events"]
        events = [e for e in received if e is not None]
        invalid = len(received) - len(events)
        if invalid:
            logger.warning(
                "agent_key_refusals_invalid_events",
                invalid=invalid,
                received=len(received),
            )
        bindings = policy.bindings_by_gateway_id({e["agent_key_id"] for e in events})
        audited = 0
        for event in events:
            binding = bindings.get(event["agent_key_id"])
            if binding is None:
                continue
            policy.audit_refusal(
                binding,
                event["code"],
                surface="gateway",
                method=event["method"],
                path=event["path"],
                count=event["count"],
                first_at=event["first_at"].isoformat(),
                last_at=event["last_at"].isoformat(),
            )
            audited += 1
        return Response(
            {
                "status": True,
                "result": {
                    "audited": audited,
                    "skipped": len(events) - audited,
                    "invalid": invalid,
                },
            }
        )
