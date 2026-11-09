"""Response adapters for non-destructive generation on classic dataset routes."""

from rest_framework.exceptions import APIException
from rest_framework.response import Response

from model_hub.services.row_generation import (
    GenerationOutputError,
    append_generated_rows,
)
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.utils.general_methods import GeneralMethods


def append_generation_response(request, dataset_id, *, regenerate=False):
    try:
        if regenerate and not request.validated_data.get("regenerate"):
            return Response(
                {
                    "status": False,
                    "message": "Use Generate to save a new configuration.",
                },
                status=400,
            )
        result = append_generated_rows(
            request,
            scoped_dataset_queryset(request),
            dataset_id,
            request.validated_data,
            regenerate=regenerate,
        )
    except APIException as error:
        return Response(
            {"status": False, "message": error.detail}, status=error.status_code
        )
    except GenerationOutputError as error:
        return Response({"status": False, "message": str(error)}, status=502)
    if regenerate:
        return GeneralMethods().success_response(
            {
                "message": result["message"],
                "data": {
                    "dataset_id": str(dataset_id),
                    "dataset_name": result["dataset_name"],
                },
            }
        )
    return GeneralMethods().success_response(result["message"])
