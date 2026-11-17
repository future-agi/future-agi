import hashlib
import hmac
import os
import time
from functools import wraps

import structlog
from rest_framework.permissions import AllowAny
from rest_framework.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from rest_framework.views import APIView

from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    WebhookIngestResponseSerializer,
    WebhookLogsRequestSerializer,
)
from agentcc.services.log_ingestion import LogIngestionError, ingest_request_logs
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

AGENTCC_WEBHOOK_SECRET = os.environ.get("AGENTCC_WEBHOOK_SECRET", "")

# The gateway signs each delivery: X-Webhook-Timestamp (unix seconds) and
# X-Webhook-Signature, the hex HMAC-SHA256 of "<timestamp>.<body bytes>" under
# the webhook secret. A signed delivery is accepted only inside this window.
# For one release a delivery with neither header is still accepted on the
# static X-Webhook-Secret header alone (a gateway older than the signing),
# and counted; AGENTCC_WEBHOOK_STATIC_SECRET_ALLOWED=false refuses it, which
# is the next release's default.
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SIGNATURE_HEADER = "X-Webhook-Signature"
SIGNATURE_WINDOW_SECONDS = 300
STATIC_SECRET_ALLOWED = os.environ.get(
    "AGENTCC_WEBHOOK_STATIC_SECRET_ALLOWED", "true"
).strip().lower() not in ("0", "false", "no", "off")

# The only refusal the gateway's spool treats as final: the batch itself is
# malformed and a redelivery would be refused again.
INVALID_LOG_BATCH = "invalid_log_batch"

# Error bodies are fixed text: never the secret, a signature or the body.
_NOT_CONFIGURED = "The log webhook is not configured."
_UNAUTHORIZED = "The log webhook refused the delivery's credentials."
_PARTIAL = "Part of the log batch was not stored; deliver it again."
_FAILED = "The log batch could not be stored; deliver it again."


def _sign(secret, timestamp, body):
    return hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def _refuse(status_code, message, code):
    return GeneralMethods().custom_error_response(status_code, message, code=code)


def _refusal(request):
    """The response that refuses a delivery before its body is parsed, or None.

    A delivery that carries either signing header is judged on its signature
    alone: a missing, malformed, stale or wrong signature is refused, never
    retried on the static header (no downgrade).
    """
    secret = AGENTCC_WEBHOOK_SECRET
    if not secret:
        return _refuse(HTTP_503_SERVICE_UNAVAILABLE, _NOT_CONFIGURED, "not_configured")
    timestamp = request.headers.get(TIMESTAMP_HEADER)
    signature = request.headers.get(SIGNATURE_HEADER)
    if timestamp is not None or signature is not None:
        try:
            fresh = abs(time.time() - int(timestamp)) <= SIGNATURE_WINDOW_SECONDS
        except (TypeError, ValueError):
            fresh = False
        expected = _sign(secret, timestamp, request.body) if fresh else ""
        if fresh and hmac.compare_digest((signature or "").encode(), expected.encode()):
            return None
        return _refuse(HTTP_401_UNAUTHORIZED, _UNAUTHORIZED, "invalid_signature")
    provided = request.headers.get("X-Webhook-Secret", "").encode()
    if STATIC_SECRET_ALLOWED and hmac.compare_digest(provided, secret.encode()):
        # Counted, with nothing from the delivery: the static header goes away
        # next release.
        logger.info("agentcc_webhook_static_secret_accepted")
        return None
    return _refuse(HTTP_401_UNAUTHORIZED, _UNAUTHORIZED, "invalid_webhook_secret")


def _authenticated(view_func):
    """Check the delivery's credentials before validated_request parses it, so
    a delivery that is not the gateway's is refused before anything in it is
    read (the signature needs the exact body bytes, read here first)."""

    @wraps(view_func)
    def wrapper(self, request, *args, **kwargs):
        refusal = _refusal(request)
        if refusal is not None:
            return refusal
        return view_func(self, request, *args, **kwargs)

    return wrapper


def _malformed_batch(errors):
    # The serializer's field errors: field names and DRF's fixed messages, never
    # a value from the delivery.
    return _refuse(400, errors, INVALID_LOG_BATCH)


class GatewayWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @_authenticated
    @validated_request(
        request_serializer=WebhookLogsRequestSerializer,
        responses={
            200: WebhookIngestResponseSerializer,
            400: AgentccErrorResponseSerializer,
            503: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=_malformed_batch,
    )
    def post(self, request):
        logs = request.validated_data.get("logs", [])
        if not logs:
            return self._gm.success_response({"ingested": 0})

        try:
            count = ingest_request_logs(logs)
            return self._gm.success_response({"ingested": count})
        except LogIngestionError as e:
            # Part of the batch was not stored. The gateway delivers it again,
            # and ingestion is replay-safe, so the retry stores only what is
            # missing.
            logger.warning("webhook_ingestion_partial", error=str(e))
            return _refuse(HTTP_503_SERVICE_UNAVAILABLE, _PARTIAL, "log_batch_partial")
        except Exception:
            # A server error (a database blip, say) is not the batch's fault:
            # 500, so the gateway keeps the batch and delivers it again.
            logger.exception("webhook_ingestion_error")
            return _refuse(HTTP_500_INTERNAL_SERVER_ERROR, _FAILED, "log_batch_failed")
