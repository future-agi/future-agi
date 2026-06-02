import json
from datetime import datetime
from typing import Optional

import structlog
from django.db.models import F, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce

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
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession
from tracer.models.trace import Trace
from tracer.services.clickhouse.span_attribute_lookups import (
    trace_ids_with_simulator_call_execution_id,
)
from tracer.utils.otel import ConversationAttributes
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)


def _build_trace_query(
    project_id: str,
    replay_type: str,
    ids: Optional[list[str]] = None,
    select_all: bool = False,
) -> QuerySet:
    """
    Build base Trace queryset based on replay parameters.

    Args:
        project_id: The project UUID
        replay_type: "session" or "trace"
        ids: List of session/trace IDs (depending on replay_type)
        select_all: If True, fetch all for the replay_type

    Returns:
        Trace queryset
    """
    base_query = Trace.objects.filter(project_id=project_id)

    if replay_type == "session":
        if select_all:
            query = base_query.filter(session_id__isnull=False)
            return query
        return base_query.filter(session_id__in=ids or [])
    elif replay_type == "trace":
        if select_all:
            return base_query
        return base_query.filter(id__in=ids or [])
    else:
        raise ValueError(f"Invalid replay type: {replay_type}")


def get_system_prompt(
    project_id: str,
    replay_type: str,
    ids: Optional[list[str]] = None,
    select_all: bool = False,
) -> Optional[str]:
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

    trace_ids = list(trace_query.values_list("id", flat=True))

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


def _get_agent_definition_from_replay_sessions(
    project: Project,
) -> Optional[AgentDefinition]:
    """
    Get agent definition from existing replay sessions for this project.

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
    return None


def _get_next_replay_scenario_version(
    project: Project,
    agent_def: Optional[AgentDefinition],
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
) -> tuple[bool, dict, Optional[AgentDefinition]]:
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

    agent_def = _get_agent_definition_from_replay_sessions(project)
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
    is_voice = _is_voice_trace_query(trace_query)

    if is_voice:
        # Extract the original Vapi/Retell config from the trace
        original_config = _extract_voice_trace_original_config(trace_query)

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

    existing = _get_agent_definition_from_replay_sessions(project)
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
    create_kwargs = dict(
        agent_name=agent_name,
        description=agent_description,
        agent_type=agent_type,
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
        languages=["en"],
    )
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
        if obs_agent and obs_agent.api_key:
            create_kwargs["api_key"] = obs_agent.api_key

    agent_def = AgentDefinition.objects.create(**create_kwargs)
    # create_version() calls create_snapshot() internally, which reads
    # the agent definition's current fields (api_key, assistant_id, etc.)
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


def link_agent_to_replay_session(
    replay_session_id: str,
    agent: AgentDefinition,
    organization,
) -> None:
    """
    Link an agent definition to a replay session.

    Args:
        replay_session_id: UUID of the replay session
        agent: The AgentDefinition instance to link
        organization: The organization for validation
    """

    replay_session = ReplaySession.objects.get(
        id=replay_session_id,
        project__organization=organization,
    )
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
    ids: Optional[list[str]] = None,
    select_all: bool = False,
) -> Optional[dict[str, dict]]:
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
        Returns None if no traces found.
    """
    trace_query = _build_trace_query(project_id, replay_type, ids, select_all)

    # Check if these are voice traces (have conversation-type spans)
    is_voice = _is_voice_trace_query(trace_query)

    if is_voice:
        # Load conversation spans once and extract both recordings and transcripts
        voice_spans = _load_voice_conversation_spans(trace_query)
        recordings_map = _extract_recording_urls_from_spans(voice_spans)
        if recordings_map:
            return {
                trace_id: {
                    "replay_type": replay_type,
                    "audio_url": audio_url,
                }
                for trace_id, audio_url in recordings_map.items()
            }
        # Fallback to text transcripts if no recordings found
        transcripts_map = _extract_transcripts_from_spans(voice_spans)
    elif replay_type == "session":
        transcripts_map = _get_transcripts_from_session_query(trace_query)
    elif replay_type == "trace":
        transcripts_map = _get_transcripts_from_trace_query(trace_query)
    else:
        raise ValueError(f"Invalid replay type: {replay_type}")

    if not transcripts_map:
        logger.warning("No transcripts found for project", project_id=project_id)
        return None

    return {
        id_key: {
            "replay_type": replay_type,
            "transcript": json.dumps(transcript),
        }
        for id_key, transcript in transcripts_map.items()
    }


def _get_transcripts_from_trace_query(trace_query: QuerySet) -> dict[str, list[dict]]:
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
    traces = list(trace_query.values("id", "input", "output"))
    if not traces:
        return {}

    simulator_trace_ids = trace_ids_with_simulator_call_execution_id(
        str(t["id"]) for t in traces
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


def _get_transcripts_from_session_query(trace_query: QuerySet) -> dict[str, list[dict]]:
    """
    Get transcripts from a session-filtered trace queryset, ordered by root span start time.

    Returns:
        Dictionary with session IDs as keys and transcript lists as values.
    """
    root_span_start_time = Subquery(
        ObservationSpan.objects.filter(
            trace_id=OuterRef("id"),
            parent_span_id__isnull=True,
        ).values("start_time")[:1]
    )

    # The has-key check used to run as a Django Exists() subquery against
    # ``span_attributes`` (PG JSONB). Now sourced from ClickHouse — see
    # migration 0074 / span_attribute_lookups.
    traces = list(
        trace_query.annotate(
            span_start_time=Coalesce(root_span_start_time, F("created_at")),
        )
        .order_by("span_start_time")
        .values("id", "session_id", "input", "output")
    )

    simulator_trace_ids = trace_ids_with_simulator_call_execution_id(
        str(t["id"]) for t in traces
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


def _is_voice_trace_query(trace_query: QuerySet) -> bool:
    """
    Check if the traces in the query are voice traces by looking for
    conversation-type observation spans.

    NOTE: Only samples the first 10 traces for performance. In mixed
    voice/text projects this may not be fully representative.
    """
    trace_ids = trace_query.values_list("id", flat=True)[:10]
    return ObservationSpan.objects.filter(
        trace_id__in=trace_ids,
        observation_type="conversation",
    ).exists()


def _get_first_voice_span_raw_log(trace_query: QuerySet) -> dict | None:
    """Fetch the raw_log from the first conversation span in the trace query."""
    trace_id = trace_query.values_list("id", flat=True).first()
    if not trace_id:
        return None

    span = (
        ObservationSpan.objects.filter(
            trace_id=trace_id,
            observation_type="conversation",
        )
        .values("span_attributes", "eval_attributes")
        .first()
    )
    if not span:
        return None

    attrs = merge_span_attrs(span)
    raw_log = attrs.get("raw_log")
    return raw_log if isinstance(raw_log, dict) else None


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
        number = (raw_log.get("customer") or {}).get("number", "")
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


def _extract_voice_trace_original_config(trace_query: QuerySet) -> dict | None:
    """Extract the original Vapi/Retell config from the first voice trace's raw_log.
    Returns a dict suitable for AgentVersion.configuration_snapshot.
    """
    raw_log = _get_first_voice_span_raw_log(trace_query)
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
    trace_query: QuerySet, _config: dict | None = None
) -> str | None:
    """Extract system prompt from the first voice trace.
    Uses pre-extracted config if available to avoid a redundant DB query.
    """
    if _config is not None:
        return _config.get("description") or None

    raw_log = _get_first_voice_span_raw_log(trace_query)
    if not raw_log:
        return None

    return _extract_system_prompt_from_raw_log(raw_log) or None


def _load_voice_conversation_spans(trace_query: QuerySet) -> list[dict]:
    """
    Load conversation-type spans for all traces in the query (single DB hit).
    Returns a list of dicts with trace_id, span_attributes, and eval_attributes.
    """
    trace_ids = list(trace_query.values_list("id", flat=True))
    return list(
        ObservationSpan.objects.filter(
            trace_id__in=trace_ids,
            observation_type="conversation",
        ).values("trace_id", "span_attributes", "eval_attributes")
    )


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
