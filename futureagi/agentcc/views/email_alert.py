import structlog
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.serializers.email_alert import (
    AgentccEmailAlertSerializer,
    AgentccEmailAlertTestSerializer,
    AgentccEmailAlertWriteSerializer,
)
from agentcc.services.email_sender import send_test_email
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccEmailAlertViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """CRUD + test for email alert configurations. Org-scoped."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccEmailAlertSerializer
    queryset = AgentccEmailAlert.no_workspace_objects.all()
    _gm = GeneralMethods()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccEmailAlertSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("email_alert_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(AgentccEmailAlertSerializer(instance).data)
        except Exception as e:
            logger.exception("email_alert_retrieve_error", error=str(e))
            return self._gm.not_found("Email alert not found")

    def create(self, request, *args, **kwargs):
        # Entitlement check: can this org create more alerts?
        # Raised as APIException so DRF's handler returns 402 cleanly — do
        # NOT wrap in the catch-all try/except below or we'd swallow it.
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")

        from tfc.ee_gating import EEResource, check_ee_can_create

        current_count = AgentccEmailAlert.objects.filter(
            organization=org, deleted=False
        ).count()
        check_ee_can_create(
            EEResource.GATEWAY_EMAIL_ALERTS,
            org_id=str(org.id),
            current_count=current_count,
        )

        try:
            serializer = AgentccEmailAlertWriteSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            alert = serializer.save(organization=org)
            return self._gm.success_response(AgentccEmailAlertSerializer(alert).data)
        except Exception as e:
            logger.exception("email_alert_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccEmailAlertWriteSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            alert = serializer.save()
            return self._gm.success_response(AgentccEmailAlertSerializer(alert).data)
        except Exception as e:
            logger.exception("email_alert_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("email_alert_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def test(self, request, *args, **kwargs):
        """Send a test email using this alert's configuration."""
        try:
            instance = self.get_object()
            serializer = AgentccEmailAlertTestSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            recipient_override = serializer.validated_data.get("recipient_override")
            # Sent now, outside the cooldown and without spending it.
            result = send_test_email(instance, recipient_override=recipient_override)

            if result.get("success"):
                return self._gm.success_response(
                    {"message": "Test email sent successfully."}
                )
            return self._gm.bad_request(
                result.get("error", "Failed to send test email.")
            )

        except Exception as e:
            logger.exception("email_alert_test_error", error=str(e))
            return self._gm.bad_request(str(e))
