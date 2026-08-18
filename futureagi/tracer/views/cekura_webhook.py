import hmac

import structlog
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.cekura_integration import CekuraIntegration
from tracer.serializers.cekura_webhook import (
    CekuraIngestResponseSerializer,
    CekuraRunWebhookRequestSerializer,
)
from tracer.services.cekura_ingestion import ingest_cekura_run

logger = structlog.get_logger(__name__)

# Same text for every rejection reason, see the check in post().
INVALID_SECRET_MESSAGE = "Invalid webhook secret"


class CekuraRunWebhookView(APIView):
    """Receives run-completed callbacks from Cekura for a single project.

    The caller is Cekura, so there is no session or API key to authenticate
    against: the project is addressed in the URL and proven by the
    per-project shared secret in ``X-Webhook-Secret``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=CekuraRunWebhookRequestSerializer,
        responses={
            200: CekuraIngestResponseSerializer,
            400: ApiErrorResponseSerializer,
        },
    )
    def post(self, request, project_id):
        integration = CekuraIntegration.no_workspace_objects.filter(
            project_id=project_id
        ).first()

        # "Not configured", "disabled" and "wrong secret" answer identically:
        # telling them apart would let an unauthenticated caller map which
        # project ids exist and which of them are wired to Cekura.
        if integration is None:
            return self._reject(project_id, "not_configured")
        if not integration.enabled or not integration.signing_secret:
            return self._reject(project_id, "disabled")
        if not _secret_matches(
            request.headers.get("X-Webhook-Secret", ""), integration.signing_secret
        ):
            return self._reject(project_id, "secret_mismatch")

        # Ingestion failures are deliberately not caught: a 5xx is what makes
        # Cekura redeliver the run, and answering 200 or 400 to a database
        # error would drop those scores for good.
        result = ingest_cekura_run(integration, request.validated_data)
        return self._gm.success_response(
            {"ingested": result.ingested, "skipped": result.skipped}
        )

    def _reject(self, project_id, reason):
        logger.warning(
            "cekura_webhook_rejected", project_id=str(project_id), reason=reason
        )
        return self._gm.bad_request(INVALID_SECRET_MESSAGE)


def _secret_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison of the shared secret.

    Compares utf-8 bytes rather than str: ``hmac.compare_digest`` raises
    ``TypeError`` on non-ASCII str input, which would turn a malformed header
    into a 500 instead of a rejection.
    """
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
