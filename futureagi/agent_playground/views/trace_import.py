import structlog
from django.core.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.trace_import import (
    TraceToGraphRequestSerializer,
    TraceToGraphResponseSerializer,
)
from agent_playground.services.trace_to_graph import convert_trace_to_graph
from model_hub.utils.workspace_scope import request_workspace_filter
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tracer.models.trace import Trace

logger = structlog.get_logger(__name__)


class TraceToGraphView(APIView):
    """
    POST /agent-playground/graphs/from-trace/

    Create a new agent playground graph from a trace's LLM spans.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TraceToGraphRequestSerializer,
        responses={
            201: TraceToGraphResponseSerializer,
            **AGENT_PLAYGROUND_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        trace_id = request.validated_data["trace_id"]

        try:
            trace = (
                Trace.no_workspace_objects.select_related("project")
                .filter(
                    request_workspace_filter(request, field_name="project__workspace"),
                    id=trace_id,
                    project__organization=request.organization,
                )
                .get()
            )
        except Trace.DoesNotExist:
            return self._gm.not_found("Trace not found")

        try:
            graph, version = convert_trace_to_graph(
                trace=trace,
                user=request.user,
                organization=request.organization,
                workspace=request.workspace,
            )
            return self._gm.create_response(
                {
                    "graph_id": str(graph.id),
                    "version_id": str(version.id),
                }
            )
        except ValidationError as e:
            return self._gm.bad_request(str(e))
        except Exception:
            logger.exception("Error creating graph from trace", trace_id=trace_id)
            return self._gm.internal_server_error_response(
                "Failed to create graph from trace"
            )
