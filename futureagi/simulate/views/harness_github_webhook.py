import hashlib
import hmac
import json
import logging
import re

from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from simulate.serializers.hosted_harness import HarnessGitHubPushSerializer
from simulate.services.harness_github import safe_ref, safe_repository, short_ref

logger = logging.getLogger(__name__)

# The body is a hint: a larger push is left to the poller, never read.
_MAX_BODY = 2 * 1024 * 1024
_DELIVERY = re.compile(r"^[0-9a-fA-F-]{36}$")


class _WebhookThrottle(AnonRateThrottle):
    # No default rates are configured; this one lives here and uses the cache only.
    scope = "harness_github_webhook"
    rate = "600/min"

    def get_ident(self, request):
        # The connecting address only: no client-set header picks the bucket.
        return request.META.get("REMOTE_ADDR")


def _rejected(reason: str, delivery: str = "unsigned") -> Response:
    logger.info("harness_webhook_rejected reason=%s delivery=%s", reason, delivery)
    return Response(status=status.HTTP_204_NO_CONTENT)


def _delivery(request) -> str:
    value = request.headers.get("X-GitHub-Delivery", "")
    return value if _DELIVERY.fullmatch(value) else "invalid"


class HarnessGitHubWebhookView(APIView):
    """GitHub App push webhook: a signed push re-reads the head of the watched
    repositories it names. Nothing is read from the database here."""

    authentication_classes = []
    permission_classes = [AllowAny]
    # The signature covers the raw bytes: nothing parses them before it is checked.
    parser_classes = []
    # The signature is the gate; only refused requests are throttled.
    throttle_classes = []

    def _refused(self, request, reason: str) -> Response:
        throttle = _WebhookThrottle()
        if not throttle.allow_request(request, self):
            self.throttled(request, throttle.wait())
        return _rejected(reason)

    @swagger_auto_schema(
        operation_description=(
            "GitHub App push webhook, signed with X-Hub-Signature-256. Answers 204, "
            "or 429 when throttled."
        ),
        request_body=HarnessGitHubPushSerializer,
        # Checked by the view once the signature over the raw body holds.
        runtime_request_validation=True,
        responses={204: "Accepted or ignored", 429: "Throttled"},
    )
    def post(self, request):
        from simulate.services.harness_versioning.versions import versioning_enabled
        from simulate.tasks.harness_agent_changes import schedule_github_push

        secret = getattr(settings, "GITHUB_APP_WEBHOOK_SECRET", "")
        if not secret:
            return self._refused(request, "not_configured")
        try:
            length = int(request.META.get("CONTENT_LENGTH") or "")
        except ValueError:
            return self._refused(request, "no_length")
        if length <= 0 or length > _MAX_BODY:
            return self._refused(request, "too_large")
        # The raw bytes, before anything parses them.
        body = request.body
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        if (
            not signature
            or not signature.isascii()
            or not hmac.compare_digest(signature, expected)
        ):
            return self._refused(request, "bad_signature")
        delivery = _delivery(request)
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return Response(status=status.HTTP_204_NO_CONTENT)
        if event != "push":
            return _rejected("ignored_event", delivery)
        try:
            push = HarnessGitHubPushSerializer(data=json.loads(body))
        except ValueError:
            return _rejected("bad_json", delivery)
        if not push.is_valid():
            return _rejected("bad_json", delivery)
        repository = push.validated_data["repository"]
        name = repository["full_name"]
        ref = push.validated_data["ref"]
        if (
            push.validated_data["deleted"]
            or not safe_repository(name)
            or not ref.startswith("refs/")
            or not safe_ref(short_ref(ref) or "")
        ):
            return _rejected("ignored_ref", delivery)
        if not versioning_enabled():
            return _rejected("versioning_disabled", delivery)
        try:
            schedule_github_push(
                name,
                ref,
                str(repository.get("default_branch") or ""),
                repository.get("id"),
            )
        except Exception:  # noqa: BLE001 - the poller covers a lost push
            logger.warning("harness_webhook_enqueue_failed delivery=%s", delivery)
        return Response(status=status.HTTP_204_NO_CONTENT)
