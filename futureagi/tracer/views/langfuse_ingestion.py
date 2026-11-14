import uuid
from collections import defaultdict

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from integrations.transformers.langfuse_transformer import LangfuseTransformer
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiDetailErrorResponseSerializer
from tracer.models.project import Project
from tracer.serializers.langfuse_ingestion import (
    LangfuseIngestionRequestSerializer,
    LangfuseIngestionResponseSerializer,
)
from tracer.utils.langfuse_upsert import parse_langfuse_timestamp, upsert_langfuse_trace
from tracer.utils.otel import get_or_create_project

logger = structlog.get_logger(__name__)

_transformer = LangfuseTransformer()

# Map ingestion event types to Langfuse observation types
_EVENT_TYPE_TO_OBS_TYPE = {
    "generation-create": "GENERATION",
    "generation-update": "GENERATION",
    "span-create": "SPAN",
    "span-update": "SPAN",
    "event-create": "EVENT",
}


def _compute_latency_seconds(start_time_str, end_time_str):
    """Compute latency in seconds from ISO 8601 start/end time strings."""
    start = parse_langfuse_timestamp(start_time_str)
    end = parse_langfuse_timestamp(end_time_str)
    if start and end:
        return (end - start).total_seconds()
    return 0


def _merge_observation_events(obs_list):
    """Merge create + update events that share the same observation ID.

    The Langfuse SDK sends ``generation-create`` with initial data (name,
    model, input, startTime) followed by ``generation-update`` with
    completion data (output, endTime, usage).  If we process them as
    separate observations the update overwrites the create's fields with
    empty values.  Merging preserves all non-None fields from both events.
    """
    merged = {}  # obs_id → merged body dict
    order = []  # preserve insertion order

    for obs in obs_list:
        obs_id = obs.get("id")
        if not obs_id:
            order.append(obs)
            continue

        if obs_id in merged:
            existing = merged[obs_id]
            for key, value in obs.items():
                if value is not None:
                    existing[key] = value
        else:
            merged[obs_id] = dict(obs)  # copy to avoid mutating original
            order.append(merged[obs_id])

    # Recompute latency on merged observations (startTime and endTime
    # may have come from different events).
    for obs in order:
        obs["latency"] = _compute_latency_seconds(
            obs.get("startTime"), obs.get("endTime")
        )
        # Normalise usage → usageDetails after merge
        if obs.get("usage") and not obs.get("usageDetails"):
            obs["usageDetails"] = obs["usage"]

    return order


def _resolve_project(org, workspace, workspace_id, user):
    """Determine which project to ingest traces into.

    Resolution order:
    1. Existing "observe" project in the workspace (most recently created).
    2. Fall back to creating a "Default" observe project via the shared
       ``get_or_create_project`` helper.
    """
    if workspace:
        project = (
            Project.no_workspace_objects.filter(
                organization=org,
                workspace=workspace,
                trace_type="observe",
                deleted=False,
            )
            .order_by("-created_at")
            .first()
        )
        if project:
            return project

    return get_or_create_project(
        project_name="Langfuse Ingest",
        organization_id=str(org.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=workspace_id,
    )


class LangfuseIngestionView(APIView):
    """Langfuse-compatible ``POST /api/public/ingestion`` endpoint.

    Accepts batch events from Langfuse SDK / compatible clients (e.g. Vapi)
    and ingests them as traces, observation spans, and scores.

    Returns ``207 Multi-Status`` with per-event success/error reporting,
    matching the Langfuse ingestion API contract.
    """

    # Use plain JSONParser — the Langfuse SDK sends camelCase keys (traceId,
    # startTime, etc.) and we must NOT convert them to snake_case.
    parser_classes = [JSONParser]
    authentication_classes = [LangfuseBasicAuthentication, APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=LangfuseIngestionRequestSerializer,
        responses={
            207: LangfuseIngestionResponseSerializer,
            403: ApiDetailErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        batch = request.validated_data.get("batch", [])
        if not batch:
            return Response({"successes": [], "errors": []}, status=207)

        user = request.user
        if not hasattr(user, "organization") or not user.organization:
            return Response(
                build_error_envelope("User has no organization.", status_code=403),
                status=403,
            )

        org = getattr(request, "organization", None) or user.organization
        workspace = getattr(request, "workspace", None)
        workspace_id = str(workspace.id) if workspace else None

        successes = []
        errors = []

        # ---- Group events by type and trace ID ----
        trace_bodies = {}  # langfuse_trace_id → body dict
        observation_bodies = defaultdict(list)  # langfuse_trace_id → [body dicts]
        score_bodies = defaultdict(list)  # langfuse_trace_id → [body dicts]

        for event in batch:
            event_id = event.get("id", str(uuid.uuid4()))
            event_type = event.get("type", "")
            body = event.get("body") or {}

            try:
                if event_type == "trace-create":
                    trace_id = body.get("id") or str(uuid.uuid4())
                    body["id"] = trace_id
                    # Merge if we already have a stub entry for this trace
                    if trace_id in trace_bodies:
                        trace_bodies[trace_id].update(body)
                    else:
                        trace_bodies[trace_id] = body
                    successes.append({"id": event_id, "status": 201})

                elif event_type in _EVENT_TYPE_TO_OBS_TYPE:
                    body["type"] = _EVENT_TYPE_TO_OBS_TYPE[event_type]
                    if not body.get("id"):
                        body["id"] = str(uuid.uuid4())

                    trace_id = body.get("traceId")
                    if not trace_id:
                        trace_id = str(uuid.uuid4())
                        body["traceId"] = trace_id

                    # Ensure a trace record will be created even without trace-create
                    if trace_id not in trace_bodies:
                        trace_bodies[trace_id] = {"id": trace_id}

                    observation_bodies[trace_id].append(body)
                    successes.append({"id": event_id, "status": 201})

                elif event_type == "score-create":
                    trace_id = body.get("traceId")
                    if trace_id:
                        score_bodies[trace_id].append(body)
                        if trace_id not in trace_bodies:
                            trace_bodies[trace_id] = {"id": trace_id}
                    successes.append({"id": event_id, "status": 201})

                elif event_type == "sdk-log":
                    successes.append({"id": event_id, "status": 201})

                else:
                    errors.append(
                        {
                            "id": event_id,
                            "status": 400,
                            "message": f"Unknown event type: {event_type}",
                        }
                    )

            except Exception as e:
                errors.append({"id": event_id, "status": 400, "message": str(e)})

        # ---- Process all traces ----
        if trace_bodies:
            try:
                _process_traces(
                    trace_bodies=trace_bodies,
                    observation_bodies=observation_bodies,
                    score_bodies=score_bodies,
                    org=org,
                    workspace=workspace,
                    workspace_id=workspace_id,
                    user=user,
                )
            except Exception as e:
                logger.exception("langfuse_ingestion_processing_failed", error=str(e))

        return Response({"successes": successes, "errors": errors}, status=207)


def _process_traces(
    trace_bodies,
    observation_bodies,
    score_bodies,
    org,
    workspace,
    workspace_id,
    user,
):
    """Persist grouped trace events into the database."""
    project = _resolve_project(org, workspace, workspace_id, user)
    if not project:
        logger.error("langfuse_ingestion_no_project", org_id=str(org.id))
        return

    project_id = str(project.id)

    logger.info(
        "langfuse_ingestion_start",
        project_id=project_id,
        project_name=project.name,
        trace_count=len(trace_bodies),
    )

    for langfuse_trace_id, trace_body in trace_bodies.items():
        try:
            # Merge create + update events for the same observation ID so
            # that update events don't overwrite fields from create events.
            merged_obs = _merge_observation_events(
                observation_bodies.get(langfuse_trace_id, [])
            )

            assembled = {
                "id": trace_body["id"],
                "name": trace_body.get("name"),
                "input": trace_body.get("input"),
                "output": trace_body.get("output"),
                "metadata": trace_body.get("metadata"),
                "tags": trace_body.get("tags"),
                "userId": trace_body.get("userId"),
                "sessionId": trace_body.get("sessionId"),
                "observations": merged_obs,
                "scores": score_bodies.get(langfuse_trace_id, []),
            }
            upsert_langfuse_trace(
                assembled_trace=assembled,
                transformer=_transformer,
                project_id=project_id,
                org=org,
                workspace=workspace,
                org_id=org.id,
            )
        except Exception:
            logger.exception(
                "langfuse_ingestion_trace_failed",
                trace_id=langfuse_trace_id,
            )
