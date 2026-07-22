import structlog
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from sdk.serializers.contracts import (
    SDKCICDEvaluationRunAcceptedResponseSerializer,
    SDKCICDEvaluationRunsResponseSerializer,
    SDKErrorResponseSerializer,
)
from sdk.serializers.eval_ci_cd import (
    CICDEvaluationRunsQuerySerializer,
    CICDJobSerializer,
)
from sdk.utils.api_errors import sdk_validation_error_response
from sdk.utils.cicd_evaluations import (
    are_evaluation_runs_processing,
    create_evaluation_run,
    get_evaluation_runs_summaries,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class CICDEvaluationsView(APIView):
    _gm = GeneralMethods()
    authentication_classes = [
        APIKeyAuthentication,
    ]
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)

    @validated_request(
        request_serializer=CICDJobSerializer,
        responses={
            200: SDKCICDEvaluationRunAcceptedResponseSerializer,
            400: SDKErrorResponseSerializer,
            500: SDKErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=sdk_validation_error_response,
        serializer_context=lambda request: {"request": request},
    )
    def post(self, request, *args, **kwargs):
        try:
            evaluation_run = create_evaluation_run(request.validated_data, request.user)

            return self._gm.success_response(
                {
                    "message": "Evaluation run accepted and is being processed.",
                    "project_name": evaluation_run.project.name,
                    "version": evaluation_run.version,
                    "evaluation_run_id": str(evaluation_run.id),
                }
            )
        except Exception as e:
            logger.exception(f"Error in creating evaluation run: {e}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_EVALUATION_RUN")
            )

    @validated_request(
        query_serializer=CICDEvaluationRunsQuerySerializer,
        responses={
            200: SDKCICDEvaluationRunsResponseSerializer,
            400: SDKErrorResponseSerializer,
            500: SDKErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=sdk_validation_error_response,
        serializer_context=lambda request: {"request": request},
    )
    def get(self, request, *args, **kwargs):
        try:
            validated_data = request.validated_query_data
            evaluation_runs = validated_data["evaluation_runs"]

            evaluation_runs_processing = are_evaluation_runs_processing(evaluation_runs)

            if evaluation_runs_processing:
                return self._gm.success_response(
                    {
                        "message": "Evaluations are being processed. Please try again in a few minutes.",
                        "status": "processing",
                    }
                )

            summaries = get_evaluation_runs_summaries(evaluation_runs)

            results = []
            for evaluation_run in evaluation_runs:
                results.append(
                    {
                        "id": str(evaluation_run.id),
                        "project": evaluation_run.project.name,
                        "version": evaluation_run.version,
                        "results_summary": summaries.get(evaluation_run.version, {}),
                    }
                )

            return self._gm.success_response(
                {
                    "message": "Evaluation runs retrieved successfully.",
                    "status": "completed",
                    "evaluation_runs": results,
                }
            )

        except Exception as e:
            logger.exception(f"Error in get method: {e}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_EVALUATION_RUN_SUMMARY")
            )
