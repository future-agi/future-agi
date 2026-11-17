"""Candidate generation and read-only edited-draft scoring for Agreement."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.eval_improve import EvalImproveRequest, EvalImproveResponse
from model_hub.services.eval_improve import improve_judge
from model_hub.throttles import AIEvalWriterThrottle, PlatformFeatureLimited
from tfc.utils.api_contracts import validated_request


class EvalImproveView(PlatformFeatureLimited, APIView):
    permission_classes = [IsAuthenticated]
    platform_throttle = AIEvalWriterThrottle

    @validated_request(
        request_serializer=EvalImproveRequest,
        responses={
            200: EvalImproveResponse,
            **MODEL_HUB_ERROR_RESPONSES,
            413: MODEL_HUB_ERROR_RESPONSES[400],
            429: MODEL_HUB_ERROR_RESPONSES[500],
            502: MODEL_HUB_ERROR_RESPONSES[500],
            503: MODEL_HUB_ERROR_RESPONSES[500],
            504: MODEL_HUB_ERROR_RESPONSES[500],
        },
        reject_unknown_fields=True,
        strict_response_validation=True,
    )
    def post(self, request, template_id):
        result = improve_judge(
            getattr(request, "organization", None),
            getattr(request, "workspace", None),
            template_id,
            request.validated_data,
        )
        return Response({"status": True, "result": result})
