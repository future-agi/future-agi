import hmac
import os

import structlog
from django.db import (
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    WebhookIngestResponseSerializer,
    WebhookLogsRequestSerializer,
)
from agentcc.services.log_ingestion import ingest_request_logs
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

AGENTCC_WEBHOOK_SECRET = os.environ.get("AGENTCC_WEBHOOK_SECRET", "")


class GatewayWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=WebhookLogsRequestSerializer,
        responses={
            200: WebhookIngestResponseSerializer,
            400: AgentccErrorResponseSerializer,
            503: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        expected_secret = AGENTCC_WEBHOOK_SECRET
        if not expected_secret:
            return self._gm.bad_request("Webhook secret not configured")
        provided = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(provided, expected_secret):
            return self._gm.bad_request("Invalid webhook secret")

        logs = request.validated_data.get("logs", [])
        if not logs:
            return self._gm.success_response({"ingested": 0})

        try:
            count = ingest_request_logs(logs)
            return self._gm.success_response({"ingested": count})
        except (
            OperationalError,
            InterfaceError,
            ProgrammingError,
            InternalError,
            NotSupportedError,
        ) as e:
            # The database is unreachable, went away, or refuses every row
            # (permissions, a read-only transaction, a schema mismatch): not
            # the batch's fault. 503 makes the gateway send it again; ingest
            # is replay-safe, keyed on request id.
            logger.exception("webhook_ingestion_unavailable", error=str(e))
            return self._gm.custom_error_response(
                503, "Log ingest is unavailable; retry the batch"
            )
        except Exception as e:
            # Anything else would fail the same way on every resend, and the
            # gateway holds a 503'd batch ahead of every record behind it. A
            # 400 makes it drop this batch now. Rows the database refuses one
            # by one are skipped inside ingest and never get here.
            logger.exception("webhook_ingestion_error", error=str(e))
            return self._gm.bad_request("Log batch could not be ingested")
