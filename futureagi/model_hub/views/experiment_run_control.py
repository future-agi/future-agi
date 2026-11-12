import structlog
from django.http import Http404
from drf_yasg.app_settings import swagger_settings
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.experiment_run_control import (
    ExperimentRunControlResponseSerializer,
    ExperimentRunSelectionSerializer,
)
from model_hub.services.experiment_run_control import run_control
from model_hub.services.experiment_run_control_gate import ensure_run_control_enabled
from tfc.utils.api_contracts import validated_request

logger = structlog.get_logger(__name__)

_RUN_CONTROL_DESCRIPTION = (
    "DISABLED unless the operator enables EXPERIMENT_RUN_CONTROL_ENABLED. "
    "When disabled, returns 404 with code run_control_not_enabled."
)


class _RunControlSchema:
    """Keep class/instance schema discovery independent of runtime admission."""

    def __get__(self, instance, owner):
        return swagger_settings.DEFAULT_AUTO_SCHEMA_CLASS


def control_response(request, experiment_id, stopped=None):
    try:
        data = request.validated_data if stopped is not None else {}
        result = run_control(
            request, experiment_id, data.get("run_ids"), stopped=stopped
        )
        return Response(
            {"status": True, "result": result},
            status=202 if stopped is not None else 200,
        )
    except (APIException, Http404):
        raise
    except Exception:
        logger.exception("Experiment run control is unavailable")
        return Response(
            {"status": False, "message": "Run control is temporarily unavailable."},
            status=503,
        )


class ExperimentRunControlView(APIView):
    permission_classes = [IsAuthenticated]
    swagger_schema = _RunControlSchema()

    @swagger_auto_schema(
        operation_description=_RUN_CONTROL_DESCRIPTION,
        responses={200: ExperimentRunControlResponseSerializer},
    )
    def get(self, request, experiment_id):
        ensure_run_control_enabled()
        return control_response(request, experiment_id)


class ExperimentResumeV2View(APIView):
    permission_classes = [IsAuthenticated]
    swagger_schema = _RunControlSchema()

    @validated_request(
        operation_description=_RUN_CONTROL_DESCRIPTION,
        request_serializer=ExperimentRunSelectionSerializer,
        responses={202: ExperimentRunControlResponseSerializer},
        reject_unknown_fields=True,
    )
    def post(self, request, experiment_id):
        ensure_run_control_enabled()
        return control_response(request, experiment_id, stopped=False)
