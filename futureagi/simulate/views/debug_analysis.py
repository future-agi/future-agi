from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models.test_execution import TestExecution
from simulate.serializers.test_execution import (
    TestExecutionDebugAnalysisErrorSerializer,
    TestExecutionDebugAnalysisNotFoundSerializer,
    TestExecutionDebugAnalysisResponseSerializer,
)
from simulate.views.scoping import run_test_workspace_filter
from tracer.services.simulation_investigation import (
    SimulationInvestigationConflict,
    debug_analysis_state,
    ensure_simulation_investigation,
)


class TestExecutionDebugAnalysisView(APIView):
    permission_classes = [IsAuthenticated]

    def _execution(self, request, test_execution_id):
        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        return get_object_or_404(
            TestExecution.objects.select_related("run_test"),
            run_test_workspace_filter(request, "run_test"),
            id=test_execution_id,
            run_test__organization=organization,
            run_test__deleted=False,
            deleted=False,
        )

    @swagger_auto_schema(
        operation_id="simulate_test_execution_debug_analysis_retrieve",
        operation_summary="Get Test Execution debug analysis",
        responses={
            200: TestExecutionDebugAnalysisResponseSerializer,
            404: TestExecutionDebugAnalysisNotFoundSerializer,
        },
    )
    def get(self, request, test_execution_id):
        execution = self._execution(request, test_execution_id)
        return Response(debug_analysis_state(execution), status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="simulate_test_execution_debug_analysis_create",
        operation_summary="Request Test Execution debug analysis",
        responses={
            202: TestExecutionDebugAnalysisResponseSerializer,
            404: TestExecutionDebugAnalysisNotFoundSerializer,
            409: TestExecutionDebugAnalysisErrorSerializer,
        },
    )
    def post(self, request, test_execution_id):
        execution = self._execution(request, test_execution_id)
        try:
            ensure_simulation_investigation(execution)
        except SimulationInvestigationConflict as exc:
            return Response(
                {"code": "execution_not_completed", "detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            debug_analysis_state(execution),
            status=status.HTTP_202_ACCEPTED,
        )
