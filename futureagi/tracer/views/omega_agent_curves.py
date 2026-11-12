"""A Console agent's learning curves, read from its Observe project (learning-service PLAN
stage 6 and section 2: the curves read the same Observe data, with
no separate store).

``GET /tracer/omega/agents/<id>/curves/?project_id=<uuid>[&start_date=&end_date=]`` answers
for the runs whose root span began in the window, in UTC days (default: the last 30 days,
today included). The answer has:
- per day and version points;
- per version summaries;
- window totals;
- the Overview tiles (cost per 1,000 runs, p50 latency, outcome success rate), each with its
  delta from the previous version.
Every figure carries its n, and a missing or zero figure carries why
(``tracer.services.omega_agents.curves.aggregate``).

``project_id`` names the Observe project the agent's runs are traced to. That is the agent's
own ``agent/<slug>-<shortid>`` project (P1-08's ensure answers its ``projectId``), or the
workspace's explicit operator project, where runs are told apart by ``omega.agent.id``. The
project must be an Observe project of the caller's own workspace and organization; any other
id gets the same 404 and nothing is read. The agent id only narrows the read to spans whose
``omega.agent.id`` equals it.

The walls, in order: signed in; the Console product's rollout; then the ``learning_curves``
release part (``omega_rollout.readiness``), which is off by default. While it is off the route
answers 404 like a dark product and reads nothing.
"""

import re
from datetime import UTC, date, datetime, time, timedelta

import structlog
from django.urls import path
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from omega_rollout import products as rollout_products
from omega_rollout import readiness
from omega_rollout.enforce import log_refusal, not_admitted, require_product
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer, StrictInputSerializer
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.project import Project
from tracer.serializers.omega_agent_curves import OmegaAgentCurvesResponseSerializer
from tracer.services.omega_agents.curves.aggregate import curves
from tracer.services.omega_agents.curves.runs import (
    MAX_RUNS,
    TooManyRuns,
    read_agent_runs,
)

logger = structlog.get_logger(__name__)

# EE's Console agent ids (``workforce_agent_<uuid>``, or a derived ``<base>-<hex>``): a short
# printable id and nothing else. The same rule as the agent's Observe link (P1-08).
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}$")
DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 92


def _today() -> date:
    return timezone.now().astimezone(UTC).date()


class OmegaAgentCurvesQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(
        help_text="The Observe project the agent's runs are traced to."
    )
    start_date = serializers.DateField(
        required=False, help_text="First UTC day of the window (inclusive)."
    )
    end_date = serializers.DateField(
        required=False,
        help_text="Last UTC day of the window (inclusive); today at most.",
    )

    def validate(self, attrs):
        end = attrs.get("end_date") or _today()
        start = attrs.get("start_date") or end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
        days = (end - start).days + 1
        if end > _today() or days < 1 or days > MAX_WINDOW_DAYS:
            raise serializers.ValidationError(
                get_error_message("OMEGA_AGENT_CURVES_INVALID_WINDOW").format(
                    MAX_WINDOW_DAYS
                )
            )
        return {**attrs, "start_date": start, "end_date": end}


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """[the start day's midnight, the midnight after the end day), in UTC."""
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC),
    )


class OmegaAgentCurvesView(APIView):
    """One Console agent's learning curves from its Observe project."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def check_permissions(self, request):
        """Signed in first; then the Console rollout wall; then the release part (404 while off)."""
        super().check_permissions(request)
        require_product(rollout_products.CONSOLE, request)
        if not readiness.part_ready(readiness.LEARNING_CURVES):
            log_refusal(
                rollout_products.CONSOLE,
                rollout_products.REASON_NOT_ADMITTED,
                reason=f"not_ready:{readiness.LEARNING_CURVES}",
                request=request,
            )
            raise not_admitted(rollout_products.CONSOLE)

    def _project(self, workspace, project_id):
        """The caller's own Observe project with this id, or None. The scope is explicit, not
        the thread's workspace context: that manager widens a default workspace to the org's
        rows with no workspace. It still leaves out simulation and deleted projects."""
        return (
            Project.no_workspace_objects.filter(
                id=project_id,
                organization_id=workspace.organization_id,
                workspace_id=workspace.id,
                trace_type="observe",
            )
            .only("id")
            .first()
        )

    @validated_request(
        query_serializer=OmegaAgentCurvesQuerySerializer,
        responses={
            200: OmegaAgentCurvesResponseSerializer,
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
        operation_id="tracer_omega_agents_curves_read",
        operation_description=(
            "A Console agent's cost, latency and outcome curves from its Observe project, "
            "per day and version, with the Overview tiles."
        ),
    )
    def get(self, request, agent_id):
        if not AGENT_ID_PATTERN.match(agent_id):
            return self._gm.bad_request(
                get_error_message("OMEGA_AGENT_CURVES_INVALID_ID")
            )
        workspace = getattr(request, "workspace", None)
        if workspace is None:
            return self._gm.bad_request(
                get_error_message("OMEGA_AGENT_CURVES_WORKSPACE_REQUIRED")
            )
        query = request.validated_query_data
        project = self._project(workspace, query["project_id"])
        if project is None:
            return self._gm.not_found(
                get_error_message("OMEGA_AGENT_CURVES_PROJECT_NOT_FOUND")
            )
        start, end = _bounds(query["start_date"], query["end_date"])
        try:
            runs = read_agent_runs(
                project_id=str(project.id), agent_id=agent_id, start=start, end=end
            )
        except TooManyRuns:
            return self._gm.bad_request(
                get_error_message("OMEGA_AGENT_CURVES_TOO_MANY_RUNS").format(MAX_RUNS)
            )
        except Exception:
            logger.exception(
                "omega_agent_curves_failed",
                ee_agent_id=agent_id,
                project_id=str(project.id),
            )
            return self._gm.internal_server_error_response(
                get_error_message("OMEGA_AGENT_CURVES_FAILED")
            )
        return self._gm.success_response(
            {
                "agentId": agent_id,
                "projectId": str(project.id),
                "window": {
                    "startDate": query["start_date"].isoformat(),
                    "endDate": query["end_date"].isoformat(),
                    "timezone": "UTC",
                },
                **curves(runs),
            }
        )


urlpatterns = [
    path(
        "omega/agents/<str:agent_id>/curves/",
        OmegaAgentCurvesView.as_view(),
        name="omega-agent-curves",
    ),
]
