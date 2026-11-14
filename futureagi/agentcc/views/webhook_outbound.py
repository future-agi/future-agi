import hashlib
import hmac
import json

import requests as http_requests
import structlog
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.serializers.webhook import (
    AgentccWebhookEventSerializer,
    AgentccWebhookSerializer,
)
from agentcc.services.url_safety import (
    WEBHOOK_PRIVATE_URL_ERROR,
    build_ssrf_safe_session,
    ensure_public_http_url,
)
from agentcc.services.webhook_hygiene import cancel_events_for_webhook
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccWebhookViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """CRUD for outbound webhook endpoints. Org-scoped."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccWebhookSerializer
    queryset = AgentccWebhook.no_workspace_objects.all()
    _gm = GeneralMethods()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccWebhookSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("webhook_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(AgentccWebhookSerializer(instance).data)
        except Exception as e:
            logger.exception("webhook_retrieve_error", error=str(e))
            return self._gm.not_found("Webhook not found")

    def create(self, request, *args, **kwargs):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")

        from tfc.ee_gating import EEResource, check_ee_can_create

        current_count = AgentccWebhook.no_workspace_objects.filter(
            organization_id=str(org.id), deleted=False
        ).count()
        check_ee_can_create(
            EEResource.GATEWAY_WEBHOOKS,
            org_id=str(org.id),
            current_count=current_count,
        )

        try:
            serializer = AgentccWebhookSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            webhook = serializer.save(organization=org)
            return self._gm.success_response(AgentccWebhookSerializer(webhook).data)
        except Exception as e:
            logger.exception("webhook_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccWebhookSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            was_active = instance.is_active
            # Saves only the fields the request set: the health counters
            # change concurrently in delivery runs and must not be written
            # back from the copy read at the start of this request.
            webhook = serializer.save()
            if webhook.is_active and not was_active:
                # Turned back on: the failure streak, its reason and any pause
                # are history. Its pending events go out on the next tick.
                AgentccWebhook.all_objects.filter(pk=webhook.pk).update(
                    consecutive_failures=0,
                    failing_since=None,
                    disabled_reason="",
                    disabled_at=None,
                    next_delivery_at=None,
                )
                webhook.refresh_from_db()
            return self._gm.success_response(AgentccWebhookSerializer(webhook).data)
        except Exception as e:
            logger.exception("webhook_update_error", error=str(e))
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
            # A soft delete cascades nothing: without this its pending events
            # would wait in the delivery queue for ever. Only the pending ones,
            # which the pending cap bounds, in short batches of their own; the
            # maintenance run cancels its failed and dead-lettered history.
            cancel_events_for_webhook(
                instance.id, statuses=(AgentccWebhookEvent.PENDING,)
            )
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("webhook_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Send a test event to the webhook endpoint."""
        try:
            instance = self.get_object()
            test_payload = {
                "event": "test",
                "webhook_id": str(instance.id),
                "message": "This is a test event from Agentcc.",
            }
            body = json.dumps(test_payload)

            headers = {"Content-Type": "application/json"}
            headers.update(instance.headers or {})

            if instance.secret:
                sig = hmac.new(
                    instance.secret.encode(),
                    body.encode(),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Agentcc-Signature"] = f"sha256={sig}"

            ensure_public_http_url(instance.url, WEBHOOK_PRIVATE_URL_ERROR)
            http = build_ssrf_safe_session(WEBHOOK_PRIVATE_URL_ERROR)
            try:
                resp = http.post(
                    instance.url,
                    data=body,
                    headers=headers,
                    timeout=5,
                )
                delivered = 200 <= resp.status_code < 300
                AgentccWebhookEvent.objects.create(
                    organization=instance.organization,
                    webhook=instance,
                    event_type="test",
                    payload=test_payload,
                    status=(
                        AgentccWebhookEvent.DELIVERED
                        if delivered
                        else AgentccWebhookEvent.FAILED
                    ),
                    attempts=1,
                    last_attempt_at=timezone.now(),
                    last_response_code=resp.status_code,
                )
                return self._gm.success_response(
                    {
                        "status_code": resp.status_code,
                        "success": delivered,
                    }
                )
            except (http_requests.RequestException, ConnectionError, ValueError) as e:
                AgentccWebhookEvent.objects.create(
                    organization=instance.organization,
                    webhook=instance,
                    event_type="test",
                    payload=test_payload,
                    status=AgentccWebhookEvent.FAILED,
                    attempts=1,
                    last_attempt_at=timezone.now(),
                    last_error=str(e),
                )
                return self._gm.success_response(
                    {
                        "status_code": None,
                        "success": False,
                        "error": str(e),
                    }
                )
            finally:
                http.close()
        except (ConnectionError, ValueError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("webhook_test_error", error=str(e))
            return self._gm.bad_request(str(e))


class AgentccWebhookEventViewSet(
    BaseModelViewSetMixinWithUserOrg, ReadOnlyModelViewSet
):
    """Read-only view of webhook event delivery records."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccWebhookEventSerializer
    queryset = AgentccWebhookEvent.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        qs = super().get_queryset()
        webhook_id = self.request.query_params.get("webhook_id")
        if webhook_id:
            qs = qs.filter(webhook_id=webhook_id)
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        event_type = self.request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()[:100]
            serializer = AgentccWebhookEventSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("webhook_event_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccWebhookEventSerializer(instance).data
            )
        except Exception as e:
            logger.exception("webhook_event_retrieve_error", error=str(e))
            return self._gm.not_found("Webhook event not found")

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Manually retry a failed webhook event delivery."""
        try:
            instance = self.get_object()
            if instance.status == AgentccWebhookEvent.DELIVERED:
                return self._gm.bad_request("Event already delivered")
            if instance.status in AgentccWebhookEvent.NOT_RETRYABLE:
                return self._gm.bad_request(
                    f"A {instance.status} event cannot be retried"
                )
            webhook = AgentccWebhook.all_objects.filter(pk=instance.webhook_id).first()
            if webhook is None or webhook.deleted:
                return self._gm.bad_request("Webhook was deleted")
            if not webhook.is_active:
                return self._gm.bad_request("Enable the webhook before retrying")

            instance.status = AgentccWebhookEvent.PENDING
            instance.attempts = 0
            # Due now, and the pending age limit counts from this re-queue: an
            # event retried after the limit is delivered, not expired.
            instance.next_retry_at = timezone.now()
            instance.last_error = ""
            instance.save(
                update_fields=[
                    "status",
                    "attempts",
                    "next_retry_at",
                    "last_error",
                    "updated_at",
                ]
            )

            return self._gm.success_response(
                AgentccWebhookEventSerializer(instance).data
            )
        except Exception as e:
            logger.exception("webhook_event_retry_error", error=str(e))
            return self._gm.bad_request(str(e))
