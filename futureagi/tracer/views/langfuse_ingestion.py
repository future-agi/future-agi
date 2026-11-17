import uuid
from collections import defaultdict

import structlog
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from integrations.transformers.langfuse_transformer import LangfuseTransformer
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import ApiDetailErrorResponseSerializer
from tracer.models.project import Project
from tracer.serializers.langfuse_ingestion import (
    LangfuseIngestionRequestSerializer,
    LangfuseIngestionResponseSerializer,
)
from tracer.utils.langfuse_upsert import (
    SpanIdCheckUnavailable,
    merge_langfuse_observation,
    upsert_langfuse_trace,
)
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

# The answer for an event whose observation or score id is already stored in
# another project: that row is not written, the rest of the batch is.
REFUSED_EVENT_STATUS = 409
REFUSED_EVENT_MESSAGE = "Not stored: the id is already used in another project."
REFUSED_ROOT_MESSAGE = (
    "Stored without its root span: the trace id is already used in another project."
)

# The answer when a trace's span ids could not be checked against the ids
# other projects hold (ClickHouse could not be read): nothing of that trace is
# stored, and the whole request is answered 503, on which the Langfuse SDKs
# send the batch again. No per-event status can ask for that: the SDKs send a
# batch again whenever its 207 answer lists any error, a 409 included.
UNCHECKED_STATUS = 503
UNCHECKED_MESSAGE = (
    "The batch was not fully stored: its span ids could not be checked. Send it again."
)


def _refused_event_messages(refused, trace_events, observation_events, score_events):
    """Map each row the upsert refused back to the events that sent it.

    Returns ``{event id: message}``. A refused synthetic root is answered on
    the trace's ``trace-create`` events; it has no event when the batch sent
    none.
    """
    messages = {}
    for langfuse_trace_id, rows in refused.items():
        if rows.root:
            for event_id in trace_events.get(langfuse_trace_id, ()):
                messages[event_id] = REFUSED_ROOT_MESSAGE
        for obs_id in rows.observation_ids:
            for event_id in observation_events.get((langfuse_trace_id, obs_id), ()):
                messages[event_id] = REFUSED_EVENT_MESSAGE
        for score_id in rows.score_ids:
            for event_id in score_events.get((langfuse_trace_id, score_id), ()):
                messages[event_id] = REFUSED_EVENT_MESSAGE
    return messages


def _merge_observation_events(obs_list):
    """Coalesce one batch, retaining absent vs explicitly supplied fields.

    The upsert merges these patches with persisted state and recalculates timing
    there; the start and end may have arrived in entirely different requests.
    """
    merged = {}
    order = []
    for obs in obs_list:
        obs_id = obs.get("id")
        if not obs_id:
            order.append(dict(obs))
            continue
        if obs_id not in merged:
            merged[obs_id] = {}
            order.append(merged[obs_id])
        combined = merge_langfuse_observation(merged[obs_id], obs)
        merged[obs_id].clear()
        merged[obs_id].update(combined)
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
    matching the Langfuse ingestion API contract. A batch whose span ids could
    not be checked is answered ``503`` as a whole, to be sent again.
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
        # The events behind each row, so a row the upsert refuses is answered
        # on the events that sent it.
        trace_events = defaultdict(list)  # langfuse_trace_id → [event ids]
        observation_events = defaultdict(list)  # (trace id, obs id) → [event ids]
        score_events = defaultdict(list)  # (trace id, score id) → [event ids]

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
                    trace_events[trace_id].append(event_id)
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
                    # Keyed by the id as text: the upsert reports ids as text,
                    # and a batch may send a number.
                    observation_events[(trace_id, str(body["id"]))].append(event_id)
                    successes.append({"id": event_id, "status": 201})

                elif event_type == "score-create":
                    trace_id = body.get("traceId")
                    if trace_id:
                        score_bodies[trace_id].append(body)
                        if trace_id not in trace_bodies:
                            trace_bodies[trace_id] = {"id": trace_id}
                        if body.get("id") not in (None, ""):
                            score_events[(trace_id, str(body["id"]))].append(event_id)
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
        refused = {}
        if trace_bodies:
            try:
                refused = _process_traces(
                    trace_bodies=trace_bodies,
                    observation_bodies=observation_bodies,
                    score_bodies=score_bodies,
                    org=org,
                    workspace=workspace,
                    workspace_id=workspace_id,
                    user=user,
                )
            except SpanIdCheckUnavailable:
                # The upsert logged the failure. The answer is a fixed
                # sentence, without the error's text.
                return Response(
                    build_error_envelope(
                        UNCHECKED_MESSAGE, status_code=UNCHECKED_STATUS
                    ),
                    status=UNCHECKED_STATUS,
                )
            except Exception as e:
                logger.exception("langfuse_ingestion_processing_failed", error=str(e))

        # An event whose id another project already holds was not stored: it
        # moves from successes to errors. The batch keeps its 207 and the
        # rest of it is stored.
        refused_events = _refused_event_messages(
            refused, trace_events, observation_events, score_events
        )
        if refused_events:
            stored = []
            for success in successes:
                message = refused_events.get(success["id"])
                if message is None:
                    stored.append(success)
                else:
                    errors.append(
                        {
                            "id": success["id"],
                            "status": REFUSED_EVENT_STATUS,
                            "message": message,
                        }
                    )
            successes = stored

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
    """Persist grouped trace events into the database.

    Returns ``{langfuse trace id: RefusedRows}`` for every trace with rows the
    upsert did not store because their ids are already in another project.

    Raises ``SpanIdCheckUnavailable`` at the first trace whose span ids could
    not be checked: nothing of that trace or of the traces after it is stored,
    and the traces before it are.
    """
    refused = {}
    project = _resolve_project(org, workspace, workspace_id, user)
    if not project:
        logger.error("langfuse_ingestion_no_project", org_id=str(org.id))
        return refused

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
                **trace_body,
                "observations": merged_obs,
                "scores": score_bodies.get(langfuse_trace_id, []),
            }
            *_, rows = upsert_langfuse_trace(
                assembled_trace=assembled,
                transformer=_transformer,
                project_id=project_id,
                org=org,
                workspace=workspace,
                org_id=org.id,
                patch_mode=True,
            )
            if rows.count:
                refused[langfuse_trace_id] = rows
        except SpanIdCheckUnavailable:
            # The whole request is answered 503 and sent again, so the traces
            # after this one are not tried: each would wait on the same read.
            raise
        except Exception:
            logger.exception(
                "langfuse_ingestion_trace_failed",
                trace_id=langfuse_trace_id,
            )
    return refused
