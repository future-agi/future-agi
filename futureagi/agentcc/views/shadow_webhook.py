import hmac
import os

import structlog
from django.db.models import F
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from agentcc.models.shadow_experiment import AgentccShadowExperiment
from agentcc.models.shadow_result import AgentccShadowResult
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    ShadowResultsWebhookRequestSerializer,
    WebhookIngestResponseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

AGENTCC_WEBHOOK_SECRET = os.environ.get("AGENTCC_WEBHOOK_SECRET", "")


class ShadowResultWebhookView(APIView):
    """
    Receives shadow result batches from the Agentcc Go gateway flusher.
    Auth via X-Webhook-Secret header.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=ShadowResultsWebhookRequestSerializer,
        responses={
            200: WebhookIngestResponseSerializer,
            400: AgentccErrorResponseSerializer,
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

        results = request.validated_data.get("results", [])
        if not results:
            return self._gm.success_response({"ingested": 0})

        try:
            count = self._ingest_shadow_results(results)
            return self._gm.success_response({"ingested": count})
        except Exception as e:
            logger.exception("shadow_webhook_ingestion_error", error=str(e))
            return self._gm.bad_request(str(e))

    def _ingest_shadow_results(self, results):
        experiment_ids = {
            r.get("experiment_id") for r in results if r.get("experiment_id")
        }
        experiments = {}
        if experiment_ids:
            for exp in AgentccShadowExperiment.no_workspace_objects.filter(
                id__in=experiment_ids, deleted=False
            ):
                experiments[str(exp.id)] = exp

        objects = []
        skipped = 0
        experiment_counts = {}
        for r in results:
            exp_id = r.get("experiment_id")
            experiment = experiments.get(exp_id) if exp_id else None
            if experiment is None:
                skipped += 1
                logger.warning(
                    "shadow_result_skipped_no_experiment",
                    experiment_id=exp_id,
                    request_id=r.get("request_id", ""),
                )
                continue

            objects.append(
                AgentccShadowResult(
                    organization=experiment.organization,
                    experiment=experiment,
                    request_id=r.get("request_id", ""),
                    source_model=r.get("source_model", ""),
                    shadow_model=r.get("shadow_model", ""),
                    source_response=r.get("source_response", ""),
                    shadow_response=r.get("shadow_response", ""),
                    source_latency_ms=r.get("source_latency_ms", 0),
                    shadow_latency_ms=r.get("shadow_latency_ms", 0),
                    source_tokens=r.get("source_tokens", 0),
                    shadow_tokens=r.get("shadow_tokens", 0),
                    source_status_code=r.get("source_status_code", 200),
                    shadow_status_code=r.get("shadow_status_code", 200),
                    shadow_error=r.get("shadow_error", ""),
                    prompt_hash=r.get("prompt_hash", ""),
                )
            )
            if experiment:
                experiment_counts[str(experiment.id)] = (
                    experiment_counts.get(str(experiment.id), 0) + 1
                )

        AgentccShadowResult.no_workspace_objects.bulk_create(objects, batch_size=500)

        for exp_id, count in experiment_counts.items():
            AgentccShadowExperiment.no_workspace_objects.filter(id=exp_id).update(
                total_comparisons=F("total_comparisons") + count
            )

        logger.info(
            "shadow_results_ingested",
            count=len(objects),
            skipped=skipped,
            experiments=len(experiment_counts),
        )
        return len(objects)
