"""FE-specific catalog; existing experiment-runs and baseline routes are unchanged."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.results_runs import ResultsRunsQuerySerializer, ResultsRunsResponseSerializer
from model_hub.services.results_catalog import ResultsReadError, list_results_runs
from model_hub.services.results_grid import bound_response
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods


class ResultsRunsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(query_serializer=ResultsRunsQuerySerializer,
        responses={200: ResultsRunsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True, operation_id="results_runs_list",
        operation_description="Canonical Results catalog, scoped to a live dataset. limit<=500 with offset continuation; immutable settings and unavailable evidence are explicit. No writes.")
    @primary_required
    def get(self, request, dataset_id):
        try:
            data = list_results_runs(request, dataset_id, request.validated_query_data)
            return GeneralMethods().success_response(bound_response(data))
        except ResultsReadError as exc:
            return Response(build_error_envelope(str(exc), status_code=exc.status), status=exc.status)
