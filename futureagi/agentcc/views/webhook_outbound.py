import hashlib
import hmac
import json

import requests as http_requests
import structlog
from django.db import transaction
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
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

DELIVER_EVENT_ACTIVITY = "deliver_agentcc_webhook_event_activity"
# The request waits at most this long for Temporal to accept the start.
START_TIMEOUT_SECONDS = 2


def _start_event_delivery(event_id):
    """Start one event's delivery on Temporal; on failure the schedule delivers it."""
    try:
        from tfc.temporal.drop_in import runner

        runner.start_activity(
            DELIVER_EVENT_ACTIVITY,
            args=(event_id,),
            queue="default",
            dispatch_timeout_seconds=START_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "webhook_event_retry_start_failed", event_id=event_id, error=str(exc)
        )


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
            webhook = serializer.save()
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
        """Retry a webhook event now: reset it to pending and start its delivery.

        The delivery runs on Temporal, off the request thread; when the start
        fails, the agentcc-webhook-delivery schedule delivers it within 30 s.
        """
        try:
            instance = self.get_object()
            if instance.status == AgentccWebhookEvent.DELIVERED:
                return self._gm.bad_request("Event already delivered")

            instance.status = AgentccWebhookEvent.PENDING
            instance.attempts = 0
            instance.next_retry_at = None
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
            event_id = str(instance.id)
            transaction.on_commit(lambda: _start_event_delivery(event_id))

            return self._gm.success_response(
                AgentccWebhookEventSerializer(instance).data
            )
        except Exception as e:
            logger.exception("webhook_event_retry_error", error=str(e))
            return self._gm.bad_request(str(e))
