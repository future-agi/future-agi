"""GET /model-hub/experiment-runs/<run_id>/traces/: one run's traces, joined by run id (Ruling 95)."""

from uuid import UUID

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.experiment_run_traces import (
    ExperimentRunTracesQuerySerializer,
    ExperimentRunTracesResponseSerializer,
    ExperimentRunTracesResultSerializer,
)
from model_hub.services.experiment_run_read import get_run
from model_hub.services.experiment_run_traces import MAX_LIMIT, list_run_traces
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


def _project_ids(request: Request) -> list[str]:
    from tracer.services.clickhouse.v2.trace_detail_reads import (
        _MAX_ACCESSIBLE_PROJECTS,
    )
    from tracer.views.trace import _project_queryset_for_request

    # one past the span store's scope cap, so an oversized scope is refused
    # there (project_scope_too_large, a 503) instead of read unbounded here
    projects = _project_queryset_for_request(request).values_list("id", flat=True)
    return [str(pid) for pid in projects[: _MAX_ACCESSIBLE_PROJECTS + 1]]


class ExperimentRunTracesView(APIView):
    """A page of one visible run's traces: each recorded item's trace, listed only when its root span carries this
    run's id (fi.experiment.run_id)."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=ExperimentRunTracesQuerySerializer,
        responses={
            200: ExperimentRunTracesResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="experiment_run_traces_list",
        operation_description=(
            "List the traces of one experiment run visible in the caller's organization and workspace, joined by run "
            "id: a page of the run's items that recorded a trace (plan order), each read from the span store in the "
            f"caller's projects and listed only when its root span's fi.experiment.run_id is this run. limit is 1-{MAX_LIMIT} "
            "(default 50). A trace that names another run or none, or that the span store does not hold, is in "
            "not_joined with its reason, never in traces. A run in another organization or workspace is 404. "
            "Performs no writes."
        ),
    )
    @primary_required
    def get(self, request: Request, run_id: UUID) -> Response:
        from tracer.services.clickhouse.v2.trace_detail_reads import (
            TraceDetailReadUnavailable,
        )

        if getattr(request, "organization", None) is None:
            return _gm.not_found("Experiment run not found")
        run = get_run(run_id=run_id, datasets=scoped_dataset_queryset(request))
        if run is None:
            return _gm.not_found("Experiment run not found")
        query = request.validated_query_data
        try:
            page = list_run_traces(
                run,
                project_ids=_project_ids(request),
                limit=query["limit"],
                offset=query["offset"],
            )
        except TraceDetailReadUnavailable:
            return _gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Traces are temporarily unavailable. Please retry.",
                code="service_unavailable",
            )
        return _gm.success_response(
            ExperimentRunTracesResultSerializer(
                {
                    "run_id": run.id,
                    "traces": page.traces,
                    "not_joined": page.not_joined,
                    "items_with_trace": page.items_with_trace,
                    "limit": page.limit,
                    "offset": page.offset,
                    "has_more": page.has_more,
                }
            ).data
        )
