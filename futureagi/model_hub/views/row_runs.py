"""GET /model-hub/datasets/{dataset_id}/rows/{row_id}/runs/: the Row view's Runs tab."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.row_runs import (
    DatasetRowRunsQuerySerializer,
    DatasetRowRunsResponseSerializer,
)
from model_hub.services.results_catalog import ResultsReadError
from model_hub.services.results_grid import bound_response
from model_hub.services.row_runs import row_runs
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods


class DatasetRowRunsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DatasetRowRunsQuerySerializer,
        responses={200: DatasetRowRunsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
        operation_id="dataset_row_runs_read",
        operation_description=(
            "One dataset row in every recorded run on the dataset, scoped to the "
            "caller's organization and workspace. Classic experiments are counted, "
            "not listed. No writes."
        ),
    )
    @primary_required
    def get(self, request, dataset_id, row_id):
        try:
            data = row_runs(request, dataset_id, row_id, request.validated_query_data)
            return GeneralMethods().success_response(bound_response(data))
        except ResultsReadError as exc:
            return Response(
                build_error_envelope(str(exc), status_code=exc.status),
                status=exc.status,
            )
