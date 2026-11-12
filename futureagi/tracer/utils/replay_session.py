import json
from datetime import UTC, datetime

import structlog
from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition
from simulate.models.agent_definition import AgentTypeChoices
from simulate.models.agent_version import AgentVersion
from simulate.models.scenarios import Scenarios
from simulate.models.simulator_agent import SimulatorAgent
from simulate.utils.session_comparison import (
    merge_span_attrs,
    parse_voice_span_transcripts,
)
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession
from tracer.utils.attribute_accessor import span_raw_log, vapi_customer
from tracer.utils.otel import ConversationAttributes
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)


def _replay_content(value):
    """CH input/output are JSON strings; unencoded text is also valid content."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _build_trace_query(
    project_id: str,
    replay_type: str,
    ids: list[str] | None = None,
    select_all: bool = False,
    *,
    require_sources: bool = False,
) -> list[dict]:
    """Resolve replay sources from project-scoped CH roots, never PG mirrors.

    Keep the helper name used by the replay view. Session roots are remap-aware
    and carry the requested session ID, including for collector-only sources.
    """
    from tracer.services.clickhouse.v2 import get_reader

    if replay_type not in ("session", "trace"):
        raise ValueError(f"Invalid replay type: {replay_type}")
    if not project_id:
        raise ValueError("Project ID is required")
    project_id = str(project_id)
    requested_ids = list(dict.fromkeys(str(identifier) for identifier in ids or []))
    if not select_all and not requested_ids:
        return []

    with get_reader() as reader:
        if replay_type == "session":
            source_ids = (
                reader.distinct_session_ids_with_filters(project_id=project_id)
                if select_all
                else requested_ids
            )
            roots = reader.roots_by_session_ids(source_ids, project_id=project_id)
        elif select_all:
            root_ids = reader.root_ids_by_project(project_id)
            roots = reader.list_by_ids(
                root_ids, project_id=project_id, include_heavy=False
            )
            source_ids = requested_ids
        else:
            source_ids = requested_ids
            roots = reader.roots_by_trace_ids(source_ids, project_id=project_id)

        # Mirrored traces keep their curated conversation even when raw roots
        # are populated. Read once per trace; collector-only rows may be absent.
        curated_rows = {}
        for span in roots:
            trace_id = str(span.trace_id)
            if trace_id not in curated_rows:
                curated_rows[trace_id] = (
                    reader.get_trace_row(trace_id, project_id=project_id) or {}
                )

    def root_order(span):
        start = span.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        return start, str(span.id)

    traces = {}
    for span in sorted(roots, key=root_order):
        trace_id = str(span.trace_id)
        session_id = str(span.trace_session_id) if span.trace_session_id else None
        # Multiple roots must not duplicate a turn. Preserve separate caller
        # aliases of a session; the session reader deliberately returns both.
        key = (session_id, trace_id) if replay_type == "session" else trace_id
        content = {}
        for field in ("input", "output"):
            value = _replay_content(curated_rows[trace_id].get(field))
            if value in (None, "", [], {}):
                value = _replay_content(getattr(span, field))
            content[field] = value
        traces.setdefault(
            key,
            {
                "id": trace_id,
                "project_id": project_id,
                "session_id": session_id,
                **content,
                "created_at": span.start_time,
            },
        )
    source_key = "session_id" if replay_type == "session" else "id"
    found_ids = {trace[source_key] for trace in traces.values()}
    if require_sources and (set(source_ids) | set(requested_ids)) - found_ids:
        raise ValidationError(
            {"ids": "Replay source conversations could not be loaded."}
        )
    return list(traces.values())


def _trace_rows(trace_query, *fields) -> list[dict]:
    # Preserve the private helpers' QuerySet call shape for existing callers.
    # Request paths always use the CH rows returned above.
    return (
        trace_query
        if isinstance(trace_query, list)
        else list(trace_query.values(*fields))
    )


def _trace_ids(trace_query) -> list[str]:
    if isinstance(trace_query, list):
        return list(dict.fromkeys(trace["id"] for trace in trace_query))
    return list(trace_query.values_list("id", flat=True))


def get_system_prompt(
    project_id: str,
    replay_type: str,
    ids: list[str] | None = None,
    select_all: bool = False,
) -> str | None:
    """
    Extract the system prompt from spans based on replay parameters.

    Args:
        project_id: The project UUID
        replay_type: "session" or "trace"
        ids: List of session/trace IDs (depending on replay_type)
        select_all: If True, fetch all for the replay_type

    Returns the first system prompt found, or None if not found.
    """
    trace_query = _build_trace_query(project_id, replay_type, ids, select_all)

    trace_ids = _trace_ids(trace_query)

    if not trace_ids:
        logger.warning("No traces found for project", project_id=project_id)
        return None

    prompt = SQL_query_handler.get_system_prompt_from_traces(
        project_id=project_id,
        trace_ids=trace_ids,
    )

    logger.info(
        "System prompt search completed",
        found=prompt is not None,
        project_id=project_id,
    )

    return prompt


def _resolve_agent_definition_for_project(
    project: Project,
) -> AgentDefinition | None:
    """
    Get agent definition from existing replay sessions or observability provider for this project.

    First checks if an agent definition was linked by a prior replay session.
    Falls back to the agent definition linked via the project's observability provider.

    Args:
        project: The Project instance

    Returns:
        AgentDefinition if found, None otherwise
    """

    replay_session = (
        ReplaySession.objects.filter(
            project=project,
            agent_definition__isnull=False,
            agent_definition__deleted=False,
        )
        .select_related("agent_definition")
        .first()
    )

    if replay_session:
        return replay_session.agent_definition

    # Fallback: check if an agent definition exists via the project's observability provider
    agent_def_from_provider = (
        AgentDefinition.objects.filter(
            observability_provider__project=project,
            observability_provider__deleted=False,
            observability_provider__project__deleted=False,
            deleted=False,
        )
        .order_by("-created_at")
        .first()
    )

    return agent_def_from_provider


def _get_next_replay_scenario_version(
    project: Project,
    agent_def: AgentDefinition | None,
) -> int:
    """
    Determine the next version number for a replay scenario.

    Counts existing replay scenarios for the agent definition (or project)
    and returns count + 1. This works regardless of the scenario name format.
    """
    query = Scenarios.objects.filter(
        organization=project.organization,
        metadata__created_from="replay_session",
    )
    if agent_def:
        query = query.filter(agent_definition=agent_def)
    else:
        query = query.filter(metadata__project_id=str(project.id))

    return query.count() + 1


def get_agent_suggestions(
    project: Project,
    replay_type: str,
    ids: list[str],
    select_all: bool,
) -> tuple[bool, dict, AgentDefinition | None]:
    """
    Get agent definition suggestions for creating a replay session.

    If an agent_definition already exists for the project (via existing replay sessions),
    returns its data. Otherwise, generates suggestions based on the system prompt from traces.

    Args:
        project: The Project instance
        replay_type: "session" or "trace"
        ids: List of session/trace IDs
        select_all: If True, fetch all for the replay_type

    Returns:
        Tuple of (exists: bool, suggestions: dict, agent_def: Optional[AgentDefinition])
    """
    now = datetime.now()
    date_suffix = now.strftime("%d_%m_%y")
    agent_name = f"{project.name}_replay_agent_{date_suffix}"

    agent_def = _resolve_agent_definition_for_project(project)
    version_num = _get_next_replay_scenario_version(project, agent_def)
    scenario_name = f"{project.name}_replay_{date_suffix}_v{version_num}"

    if agent_def:
        version_name = (
            agent_def.latest_version.version_name if agent_def.latest_version else None
        )

        return (
            True,
            {
                "agent_name": agent_def.agent_name,
                "agent_description": agent_def.description,
                "agent_type": agent_def.agent_type,
                "scenario_name": scenario_name,
                "version_name": version_name,
            },
            agent_def,
        )

    # Generate defaults if no existing agent_def
    # Check if these are voice traces first
    trace_query = _build_trace_query(str(project.id), replay_type, ids, select_all)
    is_voice = _is_voice_trace_query(trace_query, project_id=str(project.id))

    if is_voice:
        # The UUID is not a tenant boundary: pin every configuration read.
        original_config = _extract_voice_trace_original_config(
            trace_query, project_id=str(project.id)
        )

        # The voice system prompt lives on the external provider (Vapi/Retell)
        # and is reachable via assistant_id on the AgentDefinition — don't
        # duplicate it into agent_description. Reuse the existing project
        # agent definition's description if one exists; otherwise leave blank.
        agent_description = ""
        agent_def_from_project = AgentDefinition.objects.filter(
            observability_provider__project=project,
        ).first()
        if agent_def_from_project:
            agent_description = agent_def_from_project.description or ""

        return (
            False,
            {
                "agent_name": agent_name,
                "agent_description": agent_description,
                "agent_type": "voice",
                "scenario_name": scenario_name,
                "version_name": None,
                "original_voice_config": original_config,
            },
            None,
        )

    # Text agent system prompt is captured on AgentVersion.configuration_snapshot
    # at version-create time, so don't copy it into agent_description here.
    return (
        False,
        {
            "agent_name": agent_name,
            "agent_description": "",
            "agent_type": "text",
            "scenario_name": scenario_name,
            "version_name": None,
        },
        None,
    )


def _update_agent_definition(
    agent_def: AgentDefinition,
    agent_name: str,
    agent_description: str,
    agent_type: str,
    voice_config: dict | None = None,
) -> None:
    """Update agent definition fields if any have changed.
    Creates a new version only when something actually changed.
    """
    desired = {
        "agent_name": agent_name,
        "description": agent_description,
        "agent_type": agent_type,
    }
    if voice_config:
        voice_fields = {
            "assistant_id": voice_config.get("assistant_id", ""),
            "provider": voice_config.get("provider", "vapi"),
            "contact_number": voice_config.get("contact_number", ""),
            "model": voice_config.get("model", ""),
        }
        # Only include voice fields that have a non-empty new value
        desired.update({k: v for k, v in voice_fields.items() if v})

    updates = {k: v for k, v in desired.items() if getattr(agent_def, k) != v}
    if not updates:
        return

    for field, value in updates.items():
        setattr(agent_def, field, value)
    agent_def.save(update_fields=list(updates.keys()))

    agent_def.create_version(
        description=agent_description,
        commit_message="Updated from replay session",
        status=AgentVersion.StatusChoices.ACTIVE,
    )


def get_or_create_agent_definition(
    project: Project,
    agent_name: str,
    agent_description: str = "",
    agent_type: str = "text",
    original_voice_config: dict | None = None,
) -> AgentDefinition:
    """Get existing agent definition for the project or create a new one.

    For voice replay, original_voice_config preserves the Vapi/Retell config
    from the original trace so the replay uses the same settings.
    """
    is_voice = original_voice_config and agent_type == AgentTypeChoices.VOICE

    existing = _resolve_agent_definition_for_project(project)
    if existing:
        _update_agent_definition(
            agent_def=existing,
            agent_name=agent_name,
            agent_description=agent_description,
            agent_type=agent_type,
            voice_config=original_voice_config if is_voice else None,
        )
        return existing

    # New agent definition
    copied_api_key = None
    create_kwargs = {
        "agent_name": agent_name,
        "description": agent_description,
        "agent_type": agent_type,
        "inbound": True,
        "organization": project.organization,
        "workspace": project.workspace,
        "languages": ["en"],
    }
    if is_voice:
        create_kwargs.update(
            assistant_id=original_voice_config.get("assistant_id", ""),
            provider=original_voice_config.get("provider", "vapi"),
            contact_number=original_voice_config.get("contact_number", ""),
            inbound=original_voice_config.get("inbound", True),
            model=original_voice_config.get("model", ""),
        )
        obs_agent = AgentDefinition.objects.filter(
            observability_provider__project=project,
        ).first()
        if obs_agent:
            copied_api_key = obs_agent.get_api_key()

    agent_def = AgentDefinition(**create_kwargs)
    agent_def.set_api_key(copied_api_key)
    agent_def.save()
    # create_version() calls create_snapshot() internally, which reads
    # the agent definition's non-credential fields (assistant_id, etc.)
    # to build configuration_snapshot — no need to pass it explicitly.
    agent_def.create_version(
        description=agent_description,
        commit_message="Initial version from replay session",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    logger.info(
        "Created new agent definition",
        agent_definition_id=str(agent_def.id),
        project_id=str(project.id),
    )
    return agent_def


def replay_session_for_agent_link(replay_session_id, request):
    """Resolve and lock a live, caller-scoped target inside the create transaction."""
    from tracer.utils.workspace_scope import project_workspace_scope_q

    workspace = getattr(request, "workspace", None) or getattr(
        getattr(request, "user", None), "workspace", None
    )
    if workspace is None:
        raise ReplaySession.DoesNotExist
    # ReplaySession has no workspace column: use exactly the project scope
    # policy used by ReplaySessionView, including default-workspace legacy rows.
    return (
        ReplaySession.no_workspace_objects.select_for_update(of=("self",))
        .filter(
            project_workspace_scope_q(request),
            deleted=False,
            project__deleted=False,
        )
        .get(id=replay_session_id)
    )


def link_agent_to_replay_session(
    replay_session_id: str,
    agent: AgentDefinition,
    *,
    request,
) -> None:
    """Link within the caller's transaction; never accept organization-only scope.

    Create callers must preflight with replay_session_for_agent_link before any
    writes. Rechecking here ensures every use of this utility is also scoped.
    """
    replay_session = replay_session_for_agent_link(replay_session_id, request)
    replay_session.agent_definition = agent
    replay_session.save(update_fields=["agent_definition"])

    logger.info(
        "Linked agent definition to replay session",
        agent_definition_id=str(agent.id),
        replay_session_id=str(replay_session_id),
    )


def get_transcripts(
    project_id: str,
    replay_type: str,
    ids: list[str] | None = None,
    select_all: bool = False,
) -> dict[str, dict]:
    """
    Get transcripts from sessions or traces as a dictionary keyed by session/trace ID.

    Args:
        project_id: The project UUID
        replay_type: "session" or "trace"
        ids: List of session/trace IDs (depending on replay_type)
        select_all: If True, fetch all for the replay_type

    Returns:
        Dictionary with session/trace IDs as keys:
        {
            "<id>": {
                "replay_type": "session" or "trace",
                "transcript": "<JSON string of transcript>"
            }
        }
        Raises ValidationError if selected conversations cannot be loaded.
    """
    trace_query = _build_trace_query(
        project_id, replay_type, ids, select_all, require_sources=True
    )

    # Do not send None to generation: it selects the generic-use-case branch.
    if not trace_query:
        raise ValidationError(
            {"ids": "Replay source conversations could not be loaded."}
        )

    is_voice = _is_voice_trace_query(trace_query, project_id=project_id)

    if is_voice:
        voice_spans = _load_voice_conversation_spans(trace_query, project_id=project_id)
        recordings_map = _extract_recording_urls_from_spans(voice_spans)
        by_trace = _extract_transcripts_from_spans(voice_spans)
        # A mixed text/voice selection, or a call without a recording, must not
        # disappear just because another selected call has audio.
        missing = set(_trace_ids(trace_query)) - recordings_map.keys() - by_trace.keys()
        if missing:
            rows = _trace_rows(trace_query, "id", "session_id", "input", "output")
            by_trace.update(
                _get_transcripts_from_trace_query(
                    [row for row in rows if str(row["id"]) in missing],
                    project_id=project_id,
                )
            )
        if recordings_map:
            # Preserve the existing per-call audio payload contract.
            return {
                trace_id: {
                    "replay_type": replay_type,
                    **(
                        {"audio_url": recordings_map[trace_id]}
                        if trace_id in recordings_map
                        else {"transcript": json.dumps(by_trace[trace_id])}
                    ),
                }
                for trace_id in _trace_ids(trace_query)
            }
        transcripts_map = by_trace
        if replay_type == "session":
            transcripts_map = {}
            for row in _trace_rows(trace_query, "id", "session_id"):
                transcripts_map.setdefault(str(row["session_id"]), []).extend(
                    by_trace[str(row["id"])]
                )
    elif replay_type == "session":
        transcripts_map = _get_transcripts_from_session_query(
            trace_query, project_id=project_id
        )
    elif replay_type == "trace":
        transcripts_map = _get_transcripts_from_trace_query(
            trace_query, project_id=project_id
        )
    else:
        raise ValueError(f"Invalid replay type: {replay_type}")

    if not transcripts_map:
        raise ValidationError(
            {"ids": "Replay source conversations could not be loaded."}
        )

    return {
        id_key: {
            "replay_type": replay_type,
            "transcript": json.dumps(transcript),
        }
        for id_key, transcript in transcripts_map.items()
    }


def _query_project_id(trace_query: QuerySet | list[dict], project_id=None) -> str:
    # Request paths pass the validated project. Legacy private callers may
    # derive scope from their trusted queryset, never from an unscoped read.
    if project_id is None:
        if isinstance(trace_query, list):
            project_id = trace_query[0].get("project_id") if trace_query else None
        else:
            project_id = trace_query.values_list("project_id", flat=True).first()
    if not project_id:
        raise ValueError("Project ID is required")
    return str(project_id)


def trace_ids_with_simulator_call_execution_id(trace_ids, *, project_id):
    """Project-scoped replacement for the legacy cross-project attribute lookup."""
    from tracer.services.clickhouse.v2 import get_reader

    if not project_id:
        raise ValueError("Project ID is required")
    ids = [str(tid) for tid in trace_ids]
    if not ids:
        return set()
    with get_reader() as reader:
        spans = reader.list_by_trace_ids(ids, project_id=str(project_id))
    key = "fi.simulator.call_execution_id"
    return {
        str(span.trace_id)
        for span in spans
        if merge_span_attrs(_chspan_to_legacy_dict(span)).get(key) is not None
    }


def _validate_replay_content(traces: list[dict]) -> None:
    for trace in traces:
        if all(trace[field] in (None, "", [], {}) for field in ("input", "output")):
            raise ValidationError(
                {"ids": "Replay source conversation content is unavailable."}
            )


def _get_transcripts_from_trace_query(
    trace_query: QuerySet | list[dict], *, project_id: str | None = None
) -> dict[str, list[dict]]:
    """
    Get transcripts from a trace queryset.
    Each trace is treated as a separate conversation with one turn.

    Returns:
        Dictionary with trace IDs as keys and transcript lists as values.
    """
    # The has-key check used to run as a Django Exists() subquery against
    # ``span_attributes`` (PG JSONB). The 77 GB GIN that backed it was
    # dropped (see migration 0074); the lookup now goes to ClickHouse which
    # has the same data shredded into ``span_attr_str`` maps.
    traces = _trace_rows(trace_query, "id", "input", "output")
    if not traces:
        return {}
    _validate_replay_content(traces)

    project_id = _query_project_id(trace_query, project_id)
    simulator_trace_ids = trace_ids_with_simulator_call_execution_id(
        (str(t["id"]) for t in traces), project_id=project_id
    )

    return {
        str(t["id"]): [
            {
                "input": (
                    parse_trace_input_for_graph_chat_scenario(t["input"])
                    if str(t["id"]) in simulator_trace_ids
                    else t["input"]
                ),
                "output": t["output"],
            }
        ]
        for t in traces
    }


def _chspan_to_legacy_dict(span) -> dict:
    """Reconstruct the legacy ``{trace_id, span_attributes, eval_attributes}``
    dict shape from a ``CHSpan`` so existing ``merge_span_attrs`` callers
    keep working.

    ``span_attributes`` is the merge of the typed maps (attrs_string /
    attrs_number / attrs_bool) plus the overflow JSON ``attributes_extra``
    minus the four PG JSONField columns the adapter round-trips into the
    overflow (model_parameters / input_images / eval_input / eval_attributes
    — see ``tracer/services/clickhouse/v2/adapter.py:330``). Those four are
    pulled back out into their original positions.
    """
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    extra = CHSpanReader.attributes_extra_as_dict(span)
    # Defensive: attributes_extra_as_dict can yield a non-dict if the
    # underlying CH column is a raw String (schema 013) rather than typed
    # JSON. Treat anything that's not a dict as no overflow data.
    if not isinstance(extra, dict):
        extra = {}

    attrs: dict = {}
    attrs.update(span.attrs_string or {})
    attrs.update(span.attrs_number or {})
    attrs.update(span.attrs_bool or {})

    _ROUND_TRIP_COLS = (
        "model_parameters",
        "input_images",
        "eval_input",
        "eval_attributes",
    )
    for k, v in extra.items():
        if k not in _ROUND_TRIP_COLS:
            attrs[k] = v

    return {
        "trace_id": span.trace_id,
        "span_attributes": attrs,
        "eval_attributes": extra.get("eval_attributes") or {},
    }


def _get_transcripts_from_session_query(
    trace_query: QuerySet | list[dict], *, project_id: str | None = None
) -> dict[str, list[dict]]:
    """
    Get transcripts from a session-filtered trace queryset, ordered by root span start time.

    Returns:
        Dictionary with session IDs as keys and transcript lists as values.
    """
    traces = _trace_rows(
        trace_query, "id", "session_id", "input", "output", "created_at"
    )
    if not traces:
        return {}
    _validate_replay_content(traces)

    # CH replay rows already carry root start_time as created_at. Retain the
    # old private QuerySet call shape and its root/created_at ordering fallback.
    root_starts = {}
    if not isinstance(trace_query, list):
        from tracer.services.clickhouse.v2 import get_reader

        project_id = _query_project_id(trace_query, project_id)
        with get_reader() as reader:
            root_starts = reader.per_trace_root_span_start_times(
                [str(t["id"]) for t in traces], project_ids=[str(project_id)]
            )

    # CH can return naive UTC while legacy rows carry timezone-aware dates.
    def _as_aware_utc(d):
        if d is None:
            return None
        return d if d.tzinfo is not None else d.replace(tzinfo=UTC)

    def _sort_key(t):
        rs = root_starts.get(str(t["id"]))
        ts = rs if rs is not None else t["created_at"]
        return _as_aware_utc(ts)

    traces.sort(key=_sort_key)

    # The has-key check used to run as a Django Exists() subquery against
    # ``span_attributes`` (PG JSONB). Now sourced from ClickHouse — see
    # migration 0074 / span_attribute_lookups.
    project_id = _query_project_id(trace_query, project_id)
    simulator_trace_ids = trace_ids_with_simulator_call_execution_id(
        (str(t["id"]) for t in traces), project_id=project_id
    )

    sessions_map: dict[str, list[dict]] = {}

    for trace in traces:
        session_id = str(trace["session_id"])
        trace_input = trace["input"]
        if str(trace["id"]) in simulator_trace_ids:
            trace_input = parse_trace_input_for_graph_chat_scenario(trace_input)

        if session_id not in sessions_map:
            sessions_map[session_id] = []
        sessions_map[session_id].append(
            {"input": trace_input, "output": trace["output"]}
        )

    return sessions_map


def _is_voice_trace_query(
    trace_query: QuerySet | list[dict], *, project_id: str | None = None
) -> bool:
    """
    Check if the traces in the query are voice traces by looking for
    conversation-type observation spans.

    Inspect all selected traces so mixed selections cannot lose voice calls.
    """
    from tracer.services.clickhouse.v2 import get_reader

    trace_ids = _trace_ids(trace_query)
    if not trace_ids:
        return False

    # Only inspect scalar observation_type here; load heavy voice attributes
    # once a conversation is found, in _load_voice_conversation_spans.
    project_id = _query_project_id(trace_query, project_id)
    with get_reader() as reader:
        spans = reader.list_by_trace_ids(
            [str(t) for t in trace_ids], project_id=project_id, include_heavy=False
        )
    return any(s.observation_type == "conversation" for s in spans)


def _get_first_voice_span_raw_log(
    trace_query: QuerySet | list[dict], *, project_id: str | None = None
) -> dict | None:
    """Fetch the raw_log from the first conversation span in the trace query."""
    from tracer.services.clickhouse.v2 import get_reader

    if isinstance(trace_query, list):
        trace_ids = _trace_ids(trace_query)
    else:
        trace_id = trace_query.values_list("id", flat=True).first()
        trace_ids = [str(trace_id)] if trace_id else []
    if not trace_ids:
        return None

    project_id = _query_project_id(trace_query, project_id)
    with get_reader() as reader:
        if isinstance(trace_query, list):
            spans = reader.list_by_trace_ids(trace_ids, project_id=project_id)
        else:
            spans = reader.list_by_trace(trace_ids[0], project_id=project_id)

    conversation_span = next(
        (s for s in spans if s.observation_type == "conversation"), None
    )
    if conversation_span is None:
        return None

    attrs = merge_span_attrs(_chspan_to_legacy_dict(conversation_span))
    return span_raw_log(attrs, span_id=str(conversation_span.id)) or None


def _find_message_by_role(messages: list, role: str) -> str:
    """Find the first message content matching a role in a Vapi messages array."""
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != role:
            continue
        content = msg.get("message") or msg.get("content") or ""
        if content:
            return content
    return ""


def _extract_system_prompt_from_raw_log(raw_log: dict) -> str:
    """Extract system prompt from a Vapi/Retell raw_log dict.
    Checks top-level messages first, then assistant.model.messages.
    """
    # Vapi stores system prompt in top-level messages array
    system_prompt = _find_message_by_role(raw_log.get("messages") or [], "system")
    if system_prompt:
        return system_prompt

    # Fallback: assistant.model.messages
    assistant = raw_log.get("assistant") or {}
    model_messages = (assistant.get("model") or {}).get("messages") or []
    return _find_message_by_role(model_messages, "system")


def _extract_phone_number_from_raw_log(raw_log: dict) -> str:
    """Extract phone number from Vapi raw_log.
    Tries phoneNumber.twilioPhoneNumber -> phoneNumber.number -> customer.number.
    """
    phone = raw_log.get("phoneNumber") or {}
    number = phone.get("twilioPhoneNumber") or phone.get("number") or ""
    if not number:
        number = vapi_customer(raw_log).get("number", "")
    return number


def _extract_model_name_from_raw_log(raw_log: dict) -> str:
    """Extract model name from assistant config or costs breakdown."""
    model_name = ((raw_log.get("assistant") or {}).get("model") or {}).get("model", "")
    if model_name:
        return model_name
    for cost in raw_log.get("costs") or []:
        if isinstance(cost, dict) and cost.get("type") == "model":
            model_name = (cost.get("model") or {}).get("model", "")
            if model_name:
                return model_name
    return ""


def _extract_voice_trace_original_config(
    trace_query: QuerySet | list[dict], *, project_id: str | None = None
) -> dict | None:
    """Extract the original Vapi/Retell config from the first voice trace's raw_log.
    Returns a dict suitable for AgentVersion.configuration_snapshot.
    """
    raw_log = _get_first_voice_span_raw_log(trace_query, project_id=project_id)
    if not raw_log:
        return None

    assistant = raw_log.get("assistant") or {}
    system_prompt = _extract_system_prompt_from_raw_log(raw_log)
    first_message = (
        _find_message_by_role(raw_log.get("messages") or [], "bot")
        or _find_message_by_role(raw_log.get("messages") or [], "assistant")
        or (assistant.get("firstMessage") or "")
    )

    provider = raw_log.get("phoneCallProvider", "")
    if provider not in ("vapi", "retell", "livekit"):
        if provider:
            logger.warning(
                "Unknown voice provider, defaulting to vapi", provider=provider
            )
        provider = "vapi"

    return {
        "provider": provider,
        "assistant_id": raw_log.get("assistantId") or assistant.get("id", ""),
        "inbound": raw_log.get("type") == "inboundPhoneCall",
        "description": system_prompt,
        "agent_type": "voice",
        "original_assistant_config": assistant,
        "first_message": first_message,
        "contact_number": _extract_phone_number_from_raw_log(raw_log),
        "model": _extract_model_name_from_raw_log(raw_log),
    }


def _extract_voice_trace_system_prompt(
    trace_query: QuerySet | list[dict],
    _config: dict | None = None,
    *,
    project_id: str | None = None,
) -> str | None:
    """Extract system prompt from the first voice trace.
    Uses pre-extracted config if available to avoid a redundant DB query.
    """
    if _config is not None:
        return _config.get("description") or None

    raw_log = _get_first_voice_span_raw_log(trace_query, project_id=project_id)
    if not raw_log:
        return None

    return _extract_system_prompt_from_raw_log(raw_log) or None


def _load_voice_conversation_spans(
    trace_query: QuerySet | list[dict], *, project_id: str | None = None
) -> list[dict]:
    """
    Load conversation-type spans for all traces in the query.
    Returns a list of dicts shaped like ``{trace_id, span_attributes, eval_attributes}``
    so downstream ``merge_span_attrs`` / attribute lookups keep working.

    Backing store is now ClickHouse (CHSpanReader). One bulk query across
    all trace_ids; observation_type filter applied in Python.
    """
    from tracer.services.clickhouse.v2 import get_reader

    trace_ids = _trace_ids(trace_query)
    if not trace_ids:
        return []

    project_id = _query_project_id(trace_query, project_id)
    with get_reader() as reader:
        spans = reader.list_by_trace_ids(
            [str(t) for t in trace_ids], project_id=project_id
        )

    return [
        _chspan_to_legacy_dict(s) for s in spans if s.observation_type == "conversation"
    ]


def _extract_recording_urls_from_spans(spans: list[dict]) -> dict[str, str]:
    """
    Extract the best recording URL for each voice trace from pre-loaded spans.
    Prefers mono_combined (single-channel mixed audio), falls back to stereo.
    """

    recording_key_stereo = f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}"
    recording_key_mono = f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}"

    recordings_map: dict[str, str] = {}
    for span in spans:
        attrs = merge_span_attrs(span)
        url = attrs.get(recording_key_mono) or attrs.get(recording_key_stereo)
        if url:
            recordings_map[str(span["trace_id"])] = url

    return recordings_map


def _extract_transcripts_from_spans(spans: list[dict]) -> dict[str, list[dict]]:
    """
    Extract transcripts from pre-loaded voice conversation spans.
    Returns a dictionary with trace IDs as keys and transcript lists as values.
    """
    transcripts_map: dict[str, list[dict]] = {}

    for span in spans:
        trace_id = str(span["trace_id"])
        attrs = merge_span_attrs(span)

        parsed = parse_voice_span_transcripts(attrs)
        if not parsed:
            continue

        # Convert [{role, messages}, ...] into [{input, output}, ...] pairs
        turns = []
        i = 0
        while i < len(parsed):
            entry = parsed[i]
            if entry["role"] == "user":
                user_content = entry["messages"][0] if entry["messages"] else ""
                assistant_content = ""
                if i + 1 < len(parsed) and parsed[i + 1]["role"] == "assistant":
                    assistant_content = (
                        parsed[i + 1]["messages"][0]
                        if parsed[i + 1]["messages"]
                        else ""
                    )
                    i += 1
                turns.append({"input": user_content, "output": assistant_content})
            i += 1

        if turns:
            transcripts_map[trace_id] = turns

    return transcripts_map


def create_scenario(
    project: Project,
    agent_def: AgentDefinition,
    scenario_name: str,
    agent_description: str,
) -> Scenarios:
    """
    Create a scenario for replay session.

    Args:
        project: The project instance
        agent_def: The agent definition instance
        scenario_name: Name for the scenario
        agent_description: Description for the scenario

    Returns:
        Scenarios instance
    """

    metadata = {
        "project_id": str(project.id),
        "created_from": "replay_session",
    }

    # Create simulator agent with prompt — without this, the simulation
    # prompt will be empty because prepare_call reads from scenario.simulator_agent.prompt
    version = agent_def.latest_version
    try:
        agent_prompt = generate_simulator_agent_prompt(
            agent_definition=agent_def,
            agent_version=version,
        )
    except Exception:
        logger.warning(
            "Failed to generate simulator agent prompt, using fallback",
            agent_definition_id=str(agent_def.id),
            has_version=version is not None,
            exc_info=True,
        )
        agent_prompt = f"You are testing the agent: {agent_def.agent_name}"

    simulator_agent = SimulatorAgent.objects.create(
        name=scenario_name,
        prompt=agent_prompt,
        organization=project.organization,
        workspace=project.workspace,
    )

    scenario = Scenarios.objects.create(
        name=scenario_name,
        description=agent_description
        or f"Generated from replay session for {agent_def.agent_name}",
        source="Session Replay",
        scenario_type=Scenarios.ScenarioTypes.GRAPH,
        organization=project.organization,
        workspace=project.workspace,
        status=StatusType.PROCESSING.value,
        agent_definition=agent_def,
        simulator_agent=simulator_agent,
        metadata=metadata,
    )

    return scenario


def parse_trace_input_for_graph_chat_scenario(trace_input: list[dict]) -> dict | None:
    """
    Parse the trace input for a graph chat scenario.

    Args:
        trace_input: The trace input

    Returns:
        The most recent item (scanning from the end) whose role is "user".
        Returns None if no such item exists.

    """
    if not trace_input:
        return None

    if isinstance(trace_input, list):
        for item in reversed(trace_input):
            if isinstance(item, dict) and item.get("role") == "user":
                return item

    return trace_input
