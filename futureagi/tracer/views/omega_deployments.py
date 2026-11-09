"""Future AGI endpoints of the Omega Deployments tab (SPEC 5.3).

``POST /tracer/omega/deployments/<id>/observe/`` gets or creates the deployment's
Observe project ``deploy/<name>`` in the caller's workspace, links it, and sets up
the two default monitors once. The work is in
``tracer.services.omega_deployments.ensure``; this module routes, checks the
Deployments rollout wall, and (de)serializes. When the plan has no room for the
monitors, the answer is still 200 with the project: ``alerts`` is empty and
``alertsDeferred`` says why, for the Deployments tab to show.
"""

import re

import structlog
from django.urls import path
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from omega_rollout import products as rollout_products
from omega_rollout.enforce import require_product
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer, StrictInputSerializer
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.services.omega_deployments.ensure import (
    DAILY_TOKENS,
    ERRORS_SPIKE,
    AlertsDeferred,
    DeploymentNotInWorkspace,
    ObserveTarget,
    ProjectNameTaken,
    ensure_observe_link,
)

logger = structlog.get_logger(__name__)

# EE's own rules: ``dep-`` and 16 hex digits today, so any short printable id is
# accepted and nothing else; names are EE's ``DEPLOYMENT_NAME_PATTERN``.
DEPLOYMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
DEPLOYMENT_NAME_PATTERN = r"^[a-z][a-z0-9-]{0,39}$"

_INVALID_NAME = get_error_message("OMEGA_DEPLOYMENT_OBSERVE_INVALID_NAME")


class OmegaDeploymentObserveRequestSerializer(StrictInputSerializer):
    name = serializers.RegexField(
        regex=DEPLOYMENT_NAME_PATTERN,
        max_length=40,
        error_messages={
            "invalid": _INVALID_NAME,
            "max_length": _INVALID_NAME,
            "blank": _INVALID_NAME,
            "required": _INVALID_NAME,
            "null": _INVALID_NAME,
        },
    )


class OmegaDeploymentObserveAlertSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=[ERRORS_SPIKE, DAILY_TOKENS])
    name = serializers.CharField()
    metricType = serializers.CharField()
    thresholdType = serializers.CharField()
    criticalThreshold = serializers.FloatField(allow_null=True)
    warningThreshold = serializers.FloatField(allow_null=True)
    alertFrequencyMinutes = serializers.IntegerField(min_value=1)
    isMuted = serializers.BooleanField()


class OmegaDeploymentObserveUpgradeCtaSerializer(serializers.Serializer):
    text = serializers.CharField()
    plan = serializers.CharField()


class OmegaDeploymentObserveAlertsDeferredSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    needed = serializers.IntegerField(min_value=1)
    limit = serializers.IntegerField(allow_null=True)
    currentUsage = serializers.IntegerField(min_value=0)
    upgradeCta = OmegaDeploymentObserveUpgradeCtaSerializer(allow_null=True)


class OmegaDeploymentObserveResultSerializer(serializers.Serializer):
    projectId = serializers.UUIDField()
    projectName = serializers.CharField(max_length=255)
    alerts = OmegaDeploymentObserveAlertSerializer(many=True)
    # Null once the defaults exist (or were set up and later removed by the user).
    alertsDeferred = OmegaDeploymentObserveAlertsDeferredSerializer(allow_null=True)


class OmegaDeploymentObserveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = OmegaDeploymentObserveResultSerializer()


def _deferred(deferred: AlertsDeferred | None) -> dict | None:
    if deferred is None:
        return None
    cta = deferred.upgrade_cta
    return {
        "code": deferred.code,
        "message": deferred.message,
        "needed": deferred.needed,
        "limit": deferred.limit,
        "currentUsage": deferred.current_usage,
        "upgradeCta": (
            {"text": cta.get("text"), "plan": cta.get("plan")} if cta else None
        ),
    }


def _result(target: ObserveTarget) -> dict:
    return {
        "projectId": str(target.project.id),
        "projectName": target.project.name,
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
    }


class OmegaDeploymentObserveView(APIView):
    """Get or create the Observe project and default monitors for one workspace deployment."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def check_permissions(self, request):
        """Signed in first; then the Deployments rollout wall (404 for an org not admitted)."""
        super().check_permissions(request)
        require_product(rollout_products.DEPLOYMENTS, request)

    @validated_request(
        OmegaDeploymentObserveRequestSerializer,
        responses={
            200: OmegaDeploymentObserveResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            409: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
        operation_id="tracer_omega_deployments_observe_create",
        operation_description=(
            "Get or create the Observe project and default monitors for one "
            "workspace deployment."
        ),
    )
    def post(self, request, deployment_id):
        if not DEPLOYMENT_ID_PATTERN.match(deployment_id):
            return self._gm.bad_request(
                get_error_message("OMEGA_DEPLOYMENT_OBSERVE_INVALID_ID")
            )
        workspace = getattr(request, "workspace", None)
        if workspace is None:
            return self._gm.bad_request(
                get_error_message("OMEGA_DEPLOYMENT_OBSERVE_WORKSPACE_REQUIRED")
            )
        try:
            target = ensure_observe_link(
                organization=workspace.organization,
                workspace=workspace,
                user=request.user,
                ee_deployment_id=deployment_id,
                deployment_name=request.validated_data["name"],
            )
        except DeploymentNotInWorkspace as refusal:
            logger.info(
                "omega_deployment_observe_refused",
                reason="linked_in_another_workspace",
                ee_deployment_id=deployment_id,
                workspace_id=str(workspace.id),
            )
            return self._gm.not_found(refusal.message)
        except ProjectNameTaken as refusal:
            return self._gm.custom_error_response(
                status.HTTP_409_CONFLICT, refusal.message
            )
        except Exception:
            # Fail closed with the reason logged: nothing was committed (one transaction).
            logger.exception(
                "omega_deployment_observe_failed", ee_deployment_id=deployment_id
            )
            return self._gm.internal_server_error_response(
                get_error_message("OMEGA_DEPLOYMENT_OBSERVE_FAILED")
            )
        return self._gm.success_response(_result(target))


urlpatterns = [
    path(
        "omega/deployments/<str:deployment_id>/observe/",
        OmegaDeploymentObserveView.as_view(),
        name="omega-deployment-observe",
    ),
]
