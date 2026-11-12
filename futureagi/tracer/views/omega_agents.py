"""Future AGI endpoints of a Console agent's Observe project (learning-service PLAN P1-08;
every Console-managed agent gets its own Observe project).

``POST /tracer/omega/agents/<id>/observe/`` with ``{"name": <agent name>}`` gets or creates
the agent's project ``agent/<slug>-<shortid>`` in the caller's workspace, links it to the EE
agent id, and sets up its defaults once: two monitors, and mistake analysis when the plan
has Error Feed. When a default cannot be set up, the answer is still 200 with the project,
and ``alertsDeferred`` or ``mistakes`` says why, for the agent page to show.

``DELETE`` on the same path ends the link when EE deletes the agent; the project and its
traces stay in Observe.

The work is in ``tracer.services.omega_deployments.ensure.agents``; this module routes,
checks the walls and (de)serializes. The walls: the Console product's rollout (not the
Deployments one), then the ``agent_observe`` release part (``omega_rollout.readiness``),
off by default: off, both methods answer 404 like a dark product and write nothing.
"""

import re

import structlog
from django.urls import path
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from omega_rollout import products as rollout_products
from omega_rollout import readiness
from omega_rollout.enforce import log_refusal, not_admitted, require_product
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer, StrictInputSerializer
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.services.omega_deployments.ensure import (
    AgentNotInWorkspace,
    AgentObserveTarget,
    AgentProjectNameTaken,
    ensure_agent_observe_link,
    unlink_agent_observe,
)
from tracer.services.omega_deployments.ensure.mistakes import (
    NOT_ON_PLAN,
    OFF,
    ON,
    TURNED_OFF,
)
from tracer.views.omega_deployments import (
    OmegaDeploymentObserveAlertsDeferredSerializer,
    OmegaDeploymentObserveAlertSerializer,
    _deferred,
)

logger = structlog.get_logger(__name__)

# EE's Console agent ids (``workforce_agent_<uuid>``, or a derived ``<base>-<hex>``): a short
# printable id, and nothing else.
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}$")
# EE's rule for an agent name (workforce.ts MAX_NAME_BYTES): bounded text, trimmed, no
# control characters.
AGENT_NAME_MAX = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_INVALID_NAME = get_error_message("OMEGA_AGENT_OBSERVE_INVALID_NAME")


class OmegaAgentObserveRequestSerializer(StrictInputSerializer):
    name = serializers.CharField(
        max_length=AGENT_NAME_MAX,
        trim_whitespace=False,
        error_messages={
            "invalid": _INVALID_NAME,
            "max_length": _INVALID_NAME,
            "blank": _INVALID_NAME,
            "required": _INVALID_NAME,
            "null": _INVALID_NAME,
        },
    )

    def validate_name(self, value):
        if value.strip() != value or _CONTROL.search(value):
            raise serializers.ValidationError(_INVALID_NAME)
        return value


class OmegaAgentObserveMistakesSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[ON, OFF])
    samplingRate = serializers.FloatField(allow_null=True)
    # Why the agent's runs are not analysed; null while they are.
    reason = serializers.ChoiceField(choices=[NOT_ON_PLAN, TURNED_OFF], allow_null=True)
    reasonCode = serializers.CharField(allow_null=True)
    message = serializers.CharField(allow_null=True)


class OmegaAgentObserveResultSerializer(serializers.Serializer):
    projectId = serializers.UUIDField()
    projectName = serializers.CharField(max_length=255)
    agentName = serializers.CharField(max_length=AGENT_NAME_MAX)
    alerts = OmegaDeploymentObserveAlertSerializer(many=True)
    # Null once the defaults exist (or were set up and later removed by the user).
    alertsDeferred = OmegaDeploymentObserveAlertsDeferredSerializer(allow_null=True)
    mistakes = OmegaAgentObserveMistakesSerializer()


class OmegaAgentObserveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = OmegaAgentObserveResultSerializer()


class OmegaAgentObserveUnlinkResultSerializer(serializers.Serializer):
    # False when the agent had no link in this workspace (or it was already ended).
    unlinked = serializers.BooleanField()


class OmegaAgentObserveUnlinkResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = OmegaAgentObserveUnlinkResultSerializer()


def _result(target: AgentObserveTarget) -> dict:
    mistakes = target.mistakes
    return {
        "projectId": str(target.project.id),
        "projectName": target.project.name,
        "agentName": target.link.agent_name,
        "alerts": [
            {
                "id": str(monitor.id),
                "kind": kind,
                "name": monitor.name,
                "metricType": monitor.metric_type,
                "thresholdType": monitor.threshold_type,
                "criticalThreshold": monitor.critical_threshold_value,
                "warningThreshold": monitor.warning_threshold_value,
                "alertFrequencyMinutes": monitor.alert_frequency,
                "isMuted": monitor.is_mute,
            }
            for kind, monitor in target.alerts
        ],
        "alertsDeferred": _deferred(target.alerts_deferred),
        "mistakes": {
            "status": mistakes.status,
            "samplingRate": mistakes.sampling_rate,
            "reason": mistakes.reason,
            "reasonCode": mistakes.reason_code,
            "message": mistakes.message,
        },
    }


class OmegaAgentObserveView(APIView):
    """Get or create, or end, the Observe link of one workspace's Console agent."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def check_permissions(self, request):
        """Signed in first; then the Console rollout wall; then the release part (404 while off)."""
        super().check_permissions(request)
        require_product(rollout_products.CONSOLE, request)
        if not readiness.part_ready(readiness.AGENT_OBSERVE):
            log_refusal(
                rollout_products.CONSOLE,
                rollout_products.REASON_NOT_ADMITTED,
                reason=f"not_ready:{readiness.AGENT_OBSERVE}",
                request=request,
            )
            raise not_admitted(rollout_products.CONSOLE)

    def _scope(self, request, agent_id):
        """The caller's workspace, or the 400 to answer instead."""
        if not AGENT_ID_PATTERN.match(agent_id):
            return None, self._gm.bad_request(
                get_error_message("OMEGA_AGENT_OBSERVE_INVALID_ID")
            )
        workspace = getattr(request, "workspace", None)
        if workspace is None:
            return None, self._gm.bad_request(
                get_error_message("OMEGA_AGENT_OBSERVE_WORKSPACE_REQUIRED")
            )
        return workspace, None

    @validated_request(
        OmegaAgentObserveRequestSerializer,
        responses={
            200: OmegaAgentObserveResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            409: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
        operation_id="tracer_omega_agents_observe_create",
        operation_description=(
            "Get or create the Observe project and its defaults for one workspace's "
            "Console agent."
        ),
    )
    def post(self, request, agent_id):
        workspace, refusal = self._scope(request, agent_id)
        if refusal is not None:
            return refusal
        try:
            target = ensure_agent_observe_link(
                organization=workspace.organization,
                workspace=workspace,
                user=request.user,
                ee_agent_id=agent_id,
                agent_name=request.validated_data["name"],
            )
        except AgentNotInWorkspace as refused:
            logger.info(
                "omega_agent_observe_refused",
                reason="linked_in_another_workspace",
                ee_agent_id=agent_id,
                workspace_id=str(workspace.id),
            )
            return self._gm.not_found(refused.message)
        except AgentProjectNameTaken as refused:
            return self._gm.custom_error_response(
                status.HTTP_409_CONFLICT, refused.message
            )
        except Exception:
            # Fail closed with the reason logged: nothing was committed (one transaction).
            logger.exception("omega_agent_observe_failed", ee_agent_id=agent_id)
            return self._gm.internal_server_error_response(
                get_error_message("OMEGA_AGENT_OBSERVE_FAILED")
            )
        return self._gm.success_response(_result(target))

    @validated_request(
        responses={
            200: OmegaAgentObserveUnlinkResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
        operation_id="tracer_omega_agents_observe_delete",
        operation_description=(
            "End the Observe link of one workspace's Console agent; the project and its "
            "traces stay."
        ),
    )
    def delete(self, request, agent_id):
        workspace, refusal = self._scope(request, agent_id)
        if refusal is not None:
            return refusal
        try:
            unlinked = unlink_agent_observe(workspace=workspace, ee_agent_id=agent_id)
        except Exception:
            logger.exception("omega_agent_observe_unlink_failed", ee_agent_id=agent_id)
            return self._gm.internal_server_error_response(
                get_error_message("OMEGA_AGENT_OBSERVE_FAILED")
            )
        return self._gm.success_response({"unlinked": unlinked})


urlpatterns = [
    path(
        "omega/agents/<str:agent_id>/observe/",
        OmegaAgentObserveView.as_view(),
        name="omega-agent-observe",
    ),
]
