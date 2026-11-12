"""R3 grid/full-row/IDs/matrix all use the same authorized population."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dataset_results import DatasetResultsQuerySerializer, DatasetResultsResponseSerializer
from model_hub.services.results_catalog import ResultsReadError
from model_hub.services.results_selection import select_results
from model_hub.services.results_grid import grid_results
from model_hub.services.results_matrix import matrix_results
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods


class DatasetResultsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(query_serializer=DatasetResultsQuerySerializer,
        responses={200: DatasetResultsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True, operation_id="dataset_results_read",
        operation_description="Immutable Results selection. Grid page_size<=1000; matrix<=500. Full-row and IDs share filters and ordering. Scope is complete only for the declared population; incompatible evidence is 409 and bounded-read overflow 503. No writes.")
    @primary_required
    def get(self, request, dataset_id):
        try:
            query = request.validated_query_data
            selection = select_results(request, dataset_id, query)
            project = matrix_results if query["shape"] == "matrix" else grid_results
            return GeneralMethods().success_response(project(selection, query))
        except ResultsReadError as exc:
            return Response(build_error_envelope(str(exc), status_code=exc.status), status=exc.status)
