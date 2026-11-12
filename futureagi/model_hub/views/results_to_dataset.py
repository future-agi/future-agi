"""HTTP adapter for selected result imports into new or existing datasets."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.http import Http404
from model_hub.serializers.results_to_dataset import (
    ResultsToDatasetAllFailedSerializer,
    ResultsToDatasetErrorSerializer,
    ResultsToDatasetRequestSerializer,
    ResultsToDatasetResponseSerializer,
)
from model_hub.services.results_to_dataset import copy_results_to_dataset
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope


def _copy_contract(operation_id):
    return validated_request(
        request_serializer=ResultsToDatasetRequestSerializer,
        responses={
            200: ResultsToDatasetResponseSerializer,
            207: ResultsToDatasetResponseSerializer,
            400: ResultsToDatasetErrorSerializer,
            401: ResultsToDatasetErrorSerializer,
            403: ResultsToDatasetErrorSerializer,
            404: ResultsToDatasetErrorSerializer,
            409: ResultsToDatasetErrorSerializer,
            422: ResultsToDatasetAllFailedSerializer,
            429: ResultsToDatasetErrorSerializer,
            503: ResultsToDatasetErrorSerializer,
        },
        reject_unknown_fields=True,
        operation_id=operation_id,
        operation_description=(
            "Copy 1-1000 distinct example IDs from one run and one explicit trial. "
            "Use target new to create a dataset, otherwise a live dataset UUID. "
            "Existing targets require column_map; new targets default to JSON inputs, "
            "output, expected and selected score_columns. Only saved values are copied; "
            "no execution, mutable-cell fallback, version creation or split changes. "
            "200: every row added. 207: inspect failures; retry only failed example IDs "
            "against the returned dataset_id. 422: no rows added, no new dataset created. "
            "Every requested ID appears in rows or failures. Invalid mappings, inaccessible "
            "datasets/runs and admission/storage errors commit nothing. Maximum 100 "
            "writable destination columns and 16 MiB copied values. Repeating a successful POST "
            "appends again; do not automatically retry a completed or ambiguous request."
        ),
    )


class ResultsToDatasetView(APIView):
    permission_classes = [IsAuthenticated]

    @_copy_contract("dataset_rows_from_results")
    def post(self, request, dataset_id):
        return self._copy(request, dataset_id)

    def _copy(self, request, dataset_id):
        try:
            result = copy_results_to_dataset(request, dataset_id, request.validated_data)
        except APIException as exc:
            return Response(build_error_envelope(exc.detail, status_code=exc.status_code),
                            status=exc.status_code)
        except Http404:
            return Response(build_error_envelope("Dataset not found.", status_code=404), status=404)
        except DjangoValidationError:
            return Response(build_error_envelope(
                "The dataset or column values are invalid. No rows were added.", status_code=400,
            ), status=400)
        except DatabaseError:
            # The service's transaction has already rolled back, including a new dataset.
            return Response(build_error_envelope(
                "The rows could not be saved. No rows were added.", status_code=503,
            ), status=503)
        if not result["rows_added"]:
            return Response(build_error_envelope(
                "No rows were added. Review the failed rows before trying again.",
                status_code=422, code="no_rows_added", extra={"result": result},
            ), status=422)
        return Response({"status": True, "result": result},
                        status=207 if result["rows_failed"] else 200)


class NewResultsToDatasetView(ResultsToDatasetView):
    @_copy_contract("dataset_new_rows_from_results")
    def post(self, request):
        return self._copy(request, "new")
