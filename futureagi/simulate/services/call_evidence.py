"""The reader: one place that turns a saved call into the evidence a judge reads.

For each call and each of ten kinds it answers ``present`` (with the content, the
source it was read from and a digest of exactly that content), ``not_captured``
with a cause taken from stored data only, or ``not_applicable``.

Its rules:

- Nothing is filled in. A kind the run did not keep is said to be missing, with why.
- Instructions are never cut. If they would not fit they are not handed over.
- Any other cut is made here, and said, in the content and in ``cut``.
- An empty record is present, sent as a plain statement, never as an empty value.
- World changes always say they were cut when they were taken in.
- Who spoke each turn is decided here, once.
- A call outside the caller's organisation and workspace is refused.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from simulate.models.agent_setup import AgentVersionSetup
from simulate.models.chat_message import ChatMessageModel
from simulate.models.hosted_harness import (
    HostedHarnessArtifact,
    HostedHarnessExecution,
    HostedHarnessManifest,
)
from simulate.models.test_execution import CallExecution, CallTranscript
from simulate.selectors.call_transcripts import with_speakers
from simulate.services.agent_contracts import stored_version_contract
from simulate.services.canonical_json import canonical_digest
from simulate.services.run_traces_v3 import read_run_traces, run_traces_source
from simulate.utils.speaker_roles import SpeakerRoleResolver
from simulate.utils.stored_transcript_roles import call_actor_label
from tracer.models.observability_provider import ProviderChoices

READER_VERSION = 1

EMPTY_TOOL_RECORD = "No tool calls were recorded for this call."
EMPTY_TRACE_RECORD = "The agent recorded no traces for this call."
WORLD_STATE_SCHEMA = "futureagi.world-state.v1"


class Kind(StrEnum):
    TRANSCRIPT = "transcript"
    AGENT_INSTRUCTIONS = "agent_instructions"
    SCENARIO = "scenario"
    TOOL_CALLS = "tool_calls"
    WORLD_CHANGES = "world_changes"
    WORLD_STATE_END = "world_state_end"
    WORLD_STATE_START = "world_state_start"
    CHECK_RESULTS = "check_results"
    AUDIO = "audio"
    AGENT_TRACES = "agent_traces"


class State(StrEnum):
    PRESENT = "present"
    NOT_CAPTURED = "not_captured"
    NOT_APPLICABLE = "not_applicable"


class Cause(StrEnum):
    LEVEL_FORBIDS = "level_forbids"
    NOT_UPLOADED = "not_uploaded"
    NO_RECORD = "no_record"
    WORLD_NOT_RECORDED = "world_not_recorded"
    START_NOT_SEALED = "start_not_sealed"
    NO_WHOLE_INSTRUCTIONS = "no_whole_instructions"
    NO_LONGER_KEPT = "no_longer_kept"
    NOT_READABLE_YET = "not_readable_yet"
    TOO_LARGE = "too_large"


class RunKind(StrEnum):
    PROVIDER_VOICE_VAPI = "provider_voice_vapi"
    PROVIDER_VOICE_RETELL = "provider_voice_retell"
    PROVIDER_VOICE_OTHER = "provider_voice_other"
    CHAT = "chat"
    HOSTED_CHAT = "hosted_chat"
    HOSTED_VOICE = "hosted_voice"


# Marks a present record carries.
MAY_BE_FROM_ANOTHER_ATTEMPT_OR_TRIAL = "may_be_from_another_attempt_or_trial"
CUT_AT_INGESTION = "cut_at_ingestion"

# What each artifact level lets a hosted run upload (the ingestion's own table).
_LEVEL_ALLOWS = {
    "metadata-only": frozenset({"build", "result", "log"}),
    "traces": frozenset(
        {"build", "result", "log", "trace", "tool_trace", "transcript", "world_state"}
    ),
}
# The kit's mark on a contract whose prompt its save copied by code: from the source as
# written, or a provider's whole prompt. Without it the excerpt is a quote.
SYSTEM_PROMPT_COPIED = "system_prompt_copied"
# Hosted connectors whose run is configured with the whole prompt a person typed.
_STATED_CONNECTORS = frozenset({"phone", "livekit"})
_RETELL_TOOL_ROLES = frozenset({"tool_call_invocation", "tool_call_result"})
_VOICE_TOOL_ROLES = (
    CallTranscript.SpeakerRole.TOOL_CALLS,
    CallTranscript.SpeakerRole.TOOL_CALL_RESULT,
)
_KNOWN_PROVIDERS = (
    ProviderChoices.LIVEKIT,
    ProviderChoices.VAPI,
    ProviderChoices.BLAND,
    ProviderChoices.RETELL,
)


class CallNotInWorkspace(Exception):
    """The call belongs to another organisation or workspace."""


@dataclass(frozen=True)
class Evidence:
    kind: Kind
    state: State
    cause: Cause | None = None
    source: str | None = None
    content: Any = None
    digest: str | None = None
    cut: dict[str, int] | None = None
    marks: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class HostedTrial:
    """The hosted run a call was a trial of, as far as the reader needs it."""

    job_id: Any
    connector: str | None
    artifact_level: str
    attempts: int
    trials: int
    environment_version_id: Any = None
    execution_key: str | None = None
    scenario_key: str | None = None
    contract: Mapping[str, Any] | None = None
    configured_prompt: str | None = None

    def allows(self, artifact_kind: str) -> bool:
        allowed = _LEVEL_ALLOWS.get(self.artifact_level)
        return allowed is None or artifact_kind in allowed


@dataclass
class _Read:
    call: CallExecution
    trial: HostedTrial | None
    run_kind: RunKind
    organization_id: Any
    workspace_id: Any
    max_chars: int | None
    _cache: dict[str, Any] = field(default_factory=dict)

    @property
    def metadata(self) -> Mapping[str, Any]:
        return _mapping(self.call.call_metadata)

    @property
    def provider_data(self) -> Mapping[str, Any]:
        return _mapping(self.call.provider_call_data)

    @property
    def trial_state(self) -> Mapping[str, Any]:
        return _mapping(self.metadata.get("harness_trial_state"))

    def chat_messages(self) -> list[ChatMessageModel]:
        if "chat" not in self._cache:
            self._cache["chat"] = list(
                self.call.chat_messages.filter(deleted=False).order_by("created_at")
            )
        return self._cache["chat"]


# --- the public calls ----------------------------------------------------------


def read_call_evidence(
    call: CallExecution,
    kinds: Iterable[Kind],
    *,
    organization_id: Any,
    workspace_id: Any,
    max_chars: int | None = None,
) -> dict[Kind, Evidence]:
    """Each asked kind of evidence for one call. Refuses a call from elsewhere."""
    run_test = call.test_execution.run_test
    if not (
        _same_id(run_test.organization_id, organization_id)
        and _same_id(run_test.workspace_id, workspace_id)
    ):
        raise CallNotInWorkspace(str(call.id))
    trial = hosted_trial(call)
    read = _Read(
        call=call,
        trial=trial,
        run_kind=_run_kind(call, trial),
        organization_id=organization_id,
        workspace_id=workspace_id,
        max_chars=max_chars,
    )
    return {Kind(kind): _READERS[Kind(kind)](read) for kind in kinds}


def run_kind_of(call: CallExecution) -> RunKind:
    return _run_kind(call, hosted_trial(call))


def hosted_trial(call: CallExecution) -> HostedTrial | None:
    """The hosted job this call was a trial of, or ``None`` for a call no job made."""
    execution = (
        HostedHarnessExecution.objects.filter(call_execution_id=call.id)
        .select_related("job", "source_scenario")
        .first()
    )
    if execution is None:
        return None
    job = execution.job
    agent = _mapping(_mapping(job.payload).get("agent"))
    connector = agent.get("connector")
    return HostedTrial(
        job_id=job.id,
        connector=connector if isinstance(connector, str) else None,
        artifact_level=job.artifact_level,
        attempts=job.attempts.count(),
        trials=HostedHarnessExecution.objects.filter(
            job_id=job.id, source_scenario_id=execution.source_scenario_id
        ).count(),
        environment_version_id=job.environment_version_id,
        execution_key=execution.execution_key,
        scenario_key=execution.source_scenario.scenario_key,
        contract=_job_contract(job.stage_outputs),
        configured_prompt=_text_or_none(
            _mapping(agent.get("config")).get("target_system_prompt")
        ),
    )


def manifest_lists(
    job_id: Any, attempt_id: Any, digest: str, scenario_keys: set[str]
) -> bool:
    """Whether an upload manifest of that attempt of the job lists the tool trace with
    that digest under one of those scenario keys."""
    try:
        attempt_id = uuid.UUID(str(attempt_id))
    except ValueError:
        return False
    bodies = HostedHarnessManifest.objects.filter(
        attempt_id=attempt_id, attempt__job_id=job_id
    ).values_list("body", flat=True)
    for body in bodies:
        for entry in _mapping(body).get("entries") or []:
            if (
                isinstance(entry, dict)
                and entry.get("kind") == "tool_trace"
                and str(entry.get("artifact_id") or "").removeprefix("sha256:")
                == digest
                and entry.get("scenario_key") in scenario_keys
            ):
                return True
    return False


def environment_files(environment_version_id: Any) -> dict[str, bytes] | None:
    """The files of a saved environment version, ``None`` when its archive is gone."""
    from minio.error import S3Error

    from simulate.models.harness_versioning import HarnessEnvironmentVersion
    from simulate.services.harness_versioning import archives

    version = HarnessEnvironmentVersion.objects.filter(
        id=environment_version_id
    ).first()
    if version is None or not version.archive_object_key:
        return None
    try:
        body = archives.get_archive(version.archive_object_key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    return archives.read_archive(body)


def stored_artifact(read: _Read, digest: str, artifact_kind: str) -> bytes | None:
    """An uploaded artifact of the call's job, in the caller's tenant; ``None`` when not kept."""
    from minio.error import S3Error

    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage_client import get_storage_client

    if read.trial is None:
        return None
    rows = HostedHarnessArtifact.no_workspace_objects.filter(
        job_id=read.trial.job_id,
        sha256=digest.removeprefix("sha256:"),
        kind=artifact_kind,
        job__organization_id=read.organization_id,
    )
    rows = (
        rows.filter(job__workspace_id=read.workspace_id)
        if read.workspace_id is not None
        else rows.filter(job__workspace_id__isnull=True)
    )
    key = rows.values_list("object_key", flat=True).first()
    if not key:
        return None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


# --- one reader for each kind ----------------------------------------------------


def _transcript(read: _Read) -> Evidence:
    if read.run_kind in (RunKind.CHAT, RunKind.HOSTED_CHAT):
        turns = _chat_turns(read.chat_messages())
        source = "chat_messages"
    else:
        turns = _voice_turns(read.call)
        source = "call_transcript"
    if not turns:
        return _missing(Kind.TRANSCRIPT, Cause.NO_RECORD)
    kept, cut = _cut_turns(turns, read.max_chars)
    return _present(Kind.TRANSCRIPT, source, kept, cut=cut)


def _agent_instructions(read: _Read) -> Evidence:
    whole = _whole_instructions(read)
    if whole is None:
        return _missing(Kind.AGENT_INSTRUCTIONS, Cause.NO_WHOLE_INSTRUCTIONS)
    text, source = whole
    if read.max_chars is not None and len(text) > read.max_chars:
        return _missing(
            Kind.AGENT_INSTRUCTIONS,
            Cause.TOO_LARGE,
            note=f"{len(text)} characters; instructions are never cut.",
        )
    return _present(Kind.AGENT_INSTRUCTIONS, source, text)


def _scenario(read: _Read) -> Evidence:
    scenario = read.call.scenario
    content = {
        "name": scenario.name,
        "description": scenario.description,
        "scenario_type": scenario.scenario_type,
        "source": scenario.source,
    }
    if read.call.row_id:
        content["row_id"] = str(read.call.row_id)
    return _sized(Kind.SCENARIO, "scenario", content, read.max_chars)


def _tool_calls(read: _Read) -> Evidence:
    kind = Kind.TOOL_CALLS
    if read.run_kind == RunKind.PROVIDER_VOICE_VAPI:
        rows = read.call.transcripts.filter(deleted=False).order_by("start_time_ms")
        if not rows.exists():
            return _missing(kind, Cause.NO_RECORD)
        tools = [
            {
                "role": row.speaker_role,
                "content": row.content,
                "at_ms": row.start_time_ms,
            }
            for row in rows
            if row.speaker_role in _VOICE_TOOL_ROLES
        ]
        return _tool_record(kind, "call_transcript_tool_rows", tools, read.max_chars)
    if read.run_kind == RunKind.PROVIDER_VOICE_RETELL:
        retell = _mapping(read.provider_data.get("retell"))
        items = retell.get("transcript_with_tool_calls")
        if not isinstance(items, list):
            return _missing(kind, Cause.NO_RECORD)
        tools = [
            item
            for item in items
            if isinstance(item, dict) and item.get("role") in _RETELL_TOOL_ROLES
        ]
        return _tool_record(
            kind, "retell_transcript_with_tool_calls", tools, read.max_chars
        )
    if read.run_kind == RunKind.PROVIDER_VOICE_OTHER:
        return _missing(kind, Cause.NO_RECORD)
    if read.run_kind in (RunKind.HOSTED_VOICE, RunKind.HOSTED_CHAT):
        livekit = _mapping(read.provider_data.get("livekit"))
        trace = livekit.get("tool_calls")
        if isinstance(trace, list):
            return _tool_record(
                kind,
                "hosted_tool_trace",
                trace,
                read.max_chars,
                marks=_tool_record_marks(read),
            )
        if read.run_kind == RunKind.HOSTED_CHAT and read.chat_messages():
            return _tool_record(
                kind, "chat_messages", _chat_tools(read.chat_messages()), read.max_chars
            )
        return _missing(kind, _hosted_cause(read.trial, "tool_trace"))
    messages = read.chat_messages()
    if not messages:
        return _missing(kind, Cause.NO_RECORD)
    return _tool_record(kind, "chat_messages", _chat_tools(messages), read.max_chars)


def _world_state_start(read: _Read) -> Evidence:
    kind = Kind.WORLD_STATE_START
    if read.trial is None or read.trial.environment_version_id is None:
        return _not_applicable(kind)
    start = _mapping(read.trial_state.get("start"))
    if start.get("state") != "seeded" or start.get("against") != "sealed_seed":
        return _missing(kind, Cause.START_NOT_SEALED)
    files = environment_files(read.trial.environment_version_id)
    if files is None:
        return _missing(kind, Cause.NO_LONGER_KEPT)
    store = _json(files.get("store.json"))
    rows = store.get("rows") if isinstance(store, dict) else None
    if not isinstance(rows, dict):
        # The seed is a database file, or nothing the reader can hand over yet.
        return _missing(kind, Cause.NOT_READABLE_YET)
    tables = [one for one in start.get("tables") or [] if isinstance(one, str)]
    content = {name: rows[name] for name in tables if name in rows} if tables else rows
    return _sized(kind, "environment_seed", content, read.max_chars)


def _world_state_end(read: _Read) -> Evidence:
    kind = Kind.WORLD_STATE_END
    if read.trial is None:
        return _not_applicable(kind)
    if read.trial_state.get("recorded") is False:
        # A world that keeps no records may still upload a file; it is not read.
        return _missing(kind, Cause.WORLD_NOT_RECORDED)
    digest = read.trial_state.get("end_state_artifact")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        cause = _hosted_cause(read.trial, "world_state")
        return _missing(
            kind,
            cause,
            note=_incomplete_notice(read.trial_state)
            if cause == Cause.NOT_UPLOADED
            else None,
        )
    body = stored_artifact(read, digest, "world_state")
    if body is None:
        return _missing(kind, Cause.NO_LONGER_KEPT)
    state = _json(body)
    if not (isinstance(state, dict) and state.get("schema") == WORLD_STATE_SCHEMA):
        return _missing(kind, Cause.NOT_READABLE_YET)
    return _sized(kind, "hosted_world_state", state, read.max_chars)


def _world_changes(read: _Read) -> Evidence:
    kind = Kind.WORLD_CHANGES
    if read.trial is None:
        return _not_applicable(kind)
    state = read.trial_state
    if state.get("recorded") is False:
        return _missing(kind, Cause.WORLD_NOT_RECORDED)
    records = state.get("records")
    if not isinstance(records, dict):
        return _missing(kind, Cause.NO_RECORD)
    actions = read.metadata.get("harness_world_actions")
    content = {
        "changes": records.get("changes") or [],
        "skipped": records.get("skipped") or [],
        "truncated": records.get("truncated") or 0,
        "actions": actions if isinstance(actions, list) else [],
    }
    return _sized(
        kind, "harness_trial_state", content, read.max_chars, marks=(CUT_AT_INGESTION,)
    )


def _check_results(read: _Read) -> Evidence:
    verdicts = read.call.eval_outputs
    if not isinstance(verdicts, dict) or not verdicts:
        return _missing(Kind.CHECK_RESULTS, Cause.NO_RECORD)
    return _sized(
        Kind.CHECK_RESULTS, "eval_outputs", {"verdicts": verdicts}, read.max_chars
    )


def _audio(read: _Read) -> Evidence:
    kind = Kind.AUDIO
    if read.call.simulation_call_type == CallExecution.SimulationCallType.TEXT:
        return _not_applicable(kind)
    recordings = {
        name: url
        for name in ("recording_url", "stereo_recording_url")
        if (url := getattr(read.call, name, None))
    }
    if recordings:
        return _present(kind, "call_recordings", recordings)
    if read.trial is not None:
        return _missing(kind, _hosted_cause(read.trial, "recording_combined"))
    return _missing(kind, Cause.NO_RECORD)


def _agent_traces(read: _Read) -> Evidence:
    kind = Kind.AGENT_TRACES
    source = run_traces_source(read.call.test_execution)
    if source is None:
        return _not_applicable(kind)
    traces = read_run_traces(source, call_execution_id=str(read.call.id))
    if not traces:
        return _present(kind, "agent_traces", EMPTY_TRACE_RECORD)
    content = [_trace_row(trace) for trace in traces]
    kept, cut = _cut_list(content, read.max_chars)
    return _present(kind, "agent_traces", kept, cut=cut)


_READERS = {
    Kind.TRANSCRIPT: _transcript,
    Kind.AGENT_INSTRUCTIONS: _agent_instructions,
    Kind.SCENARIO: _scenario,
    Kind.TOOL_CALLS: _tool_calls,
    Kind.WORLD_CHANGES: _world_changes,
    Kind.WORLD_STATE_END: _world_state_end,
    Kind.WORLD_STATE_START: _world_state_start,
    Kind.CHECK_RESULTS: _check_results,
    Kind.AUDIO: _audio,
    Kind.AGENT_TRACES: _agent_traces,
}


# --- the two choices a ruling may change -------------------------------------------


def _tool_record_marks(read: _Read) -> tuple[str, ...]:
    """The trace ingestion attached is the newest for the trial's key, and the receipt
    does not name it. It is unmarked only on a run with one attempt and one trial, or
    when a manifest of the receipt's own attempt lists it for this trial: under the
    trial's own key, or under the scenario's key when the scenario had one trial."""
    trial = read.trial
    if trial is None or (trial.attempts <= 1 and trial.trials <= 1):
        return ()
    artifacts = _mapping(read.metadata.get("hosted_harness_artifacts"))
    digest = _mapping(artifacts.get("tool_trace")).get("sha256")
    attempt_id = _mapping(read.metadata.get("hosted_harness_receipt")).get("attempt_id")
    keys = {trial.execution_key} if trial.execution_key else set()
    if trial.trials <= 1 and trial.scenario_key:
        keys.add(trial.scenario_key)
    if (
        isinstance(digest, str)
        and digest
        and attempt_id
        and keys
        and manifest_lists(
            trial.job_id, str(attempt_id), digest.removeprefix("sha256:"), keys
        )
    ):
        return ()
    return (MAY_BE_FROM_ANOTHER_ATTEMPT_OR_TRIAL,)


def _whole_instructions(read: _Read) -> tuple[str, str] | None:
    """The agent's instructions and where they came from, only where stored data shows
    an origin that keeps them whole."""
    version = read.call.agent_version or read.call.test_execution.agent_version
    copied = _copied_prompt(read.trial.contract if read.trial else None)
    if copied is None and version is not None:
        copied = _copied_prompt(stored_version_contract(version.id))
    if copied is not None:
        return copied
    if read.trial is not None:
        # Only a phone or LiveKit run keeps the prompt a person typed. Any other
        # hosted agent's stored prompt may be the contract's excerpt.
        text = read.trial.configured_prompt
        if read.trial.connector in _STATED_CONNECTORS and text and text.strip():
            return text, "stated_by_the_user"
        return None
    if version is None:
        return None
    # Its prompt may be the reading's excerpt, and nothing stored tells that apart
    # from one a person set.
    if AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=version.id, agent_kind="source"
    ).exists():
        return None
    text = _mapping(version.configuration_snapshot).get("description")
    if isinstance(text, str) and text.strip():
        return text, "stated_by_the_user"
    return None


def _copied_prompt(contract: Mapping[str, Any] | None) -> tuple[str, str] | None:
    contract = _mapping(contract)
    text = contract.get("system_prompt_excerpt")
    if contract.get(SYSTEM_PROMPT_COPIED) is not True or not (
        isinstance(text, str) and text.strip()
    ):
        return None
    if contract.get("system_prompt_from"):
        return text, "copied_from_the_code"
    return text, "taken_from_the_provider"


def _job_contract(stage_outputs: Any) -> Mapping[str, Any] | None:
    """The contract the job's contract stage authored, as stored."""
    for output in stage_outputs if isinstance(stage_outputs, list) else []:
        if isinstance(output, dict) and output.get("kind") == "contract":
            data = output.get("data")
            return data if isinstance(data, dict) else None
    return None


# --- helpers ----------------------------------------------------------------------


def _run_kind(call: CallExecution, trial: HostedTrial | None) -> RunKind:
    is_text = call.simulation_call_type == CallExecution.SimulationCallType.TEXT
    if trial is not None:
        return RunKind.HOSTED_CHAT if is_text else RunKind.HOSTED_VOICE
    if is_text:
        return RunKind.CHAT
    provider_data = _mapping(call.provider_call_data)
    if provider_data.get(ProviderChoices.VAPI.value):
        return RunKind.PROVIDER_VOICE_VAPI
    if provider_data.get(ProviderChoices.RETELL.value):
        return RunKind.PROVIDER_VOICE_RETELL
    return RunKind.PROVIDER_VOICE_OTHER


def _voice_turns(call: CallExecution) -> list[dict[str, str]]:
    provider_data = _mapping(call.provider_call_data)
    # A call whose provider data names no provider was stored the platform's way round.
    provider = (
        SpeakerRoleResolver.detect_provider(provider_data)
        if any(provider_data.get(one.value) for one in _KNOWN_PROVIDERS)
        else ProviderChoices.LIVEKIT
    )
    is_outbound = SpeakerRoleResolver.detect_is_outbound(call)
    conversational = SpeakerRoleResolver.get_conversational_roles()
    turns = []
    rows = with_speakers(call.transcripts.filter(deleted=False)).order_by(
        "start_time_ms"
    )
    for row in rows:
        if not (row.content or "").strip():
            continue
        if row.speaker_role == CallTranscript.SpeakerRole.ACTOR:
            speaker = call_actor_label(row.speaker_name)
        elif row.speaker_role in conversational:
            speaker = SpeakerRoleResolver.get_eval_role_label(
                row.speaker_role, provider=provider, is_outbound=is_outbound
            )
        else:
            continue
        turns.append({"speaker": speaker, "text": row.content})
    return turns


def _chat_turns(messages: list[ChatMessageModel]) -> list[dict[str, str]]:
    speakers = {
        ChatMessageModel.RoleChoices.USER: "customer",
        ChatMessageModel.RoleChoices.ASSISTANT: "agent",
    }
    turns = []
    for message in messages:
        speaker = speakers.get(message.role)
        if speaker is None:
            continue
        texts = [one for one in message.messages or [] if isinstance(one, str)]
        if not texts and isinstance(message.content, list):
            texts = [
                item["content"]
                for item in message.content
                if isinstance(item, dict) and isinstance(item.get("content"), str)
            ]
        turns.extend(
            {"speaker": speaker, "text": text} for text in texts if text.strip()
        )
    return turns


def _chat_tools(messages: list[ChatMessageModel]) -> list[Any]:
    """Tool calls and results as the chat stored them, in order."""
    tools: list[Any] = []
    for message in messages:
        items = message.content if isinstance(message.content, list) else []
        from_content = [
            item
            for item in items
            if isinstance(item, dict)
            and (item.get("tool_calls") or item.get("role") == "tool")
        ]
        if from_content:
            tools.extend(from_content)
        elif message.tool_calls:
            tools.extend(message.tool_calls)
    return tools


def _tool_record(
    kind: Kind,
    source: str,
    tools: list[Any],
    max_chars: int | None,
    *,
    marks: tuple[str, ...] = (),
) -> Evidence:
    if not tools:
        return _present(kind, source, EMPTY_TOOL_RECORD, marks=marks)
    kept, cut = _cut_list(tools, max_chars)
    return _present(kind, source, kept, cut=cut, marks=marks)


def _hosted_cause(trial: HostedTrial | None, artifact_kind: str) -> Cause:
    if trial is not None and not trial.allows(artifact_kind):
        return Cause.LEVEL_FORBIDS
    return Cause.NOT_UPLOADED


def _incomplete_notice(state: Mapping[str, Any]) -> str | None:
    for notice in state.get("notices") or []:
        if isinstance(notice, dict) and notice.get("code") == "evidence_incomplete":
            message = notice.get("message")
            return message if isinstance(message, str) else None
    return None


def _trace_row(trace: Any) -> dict[str, Any]:
    row = asdict(trace)
    if row.get("start_time") is not None:
        row["start_time"] = row["start_time"].isoformat()
    return row


def _cut_turns(
    turns: list[dict[str, str]], max_chars: int | None
) -> tuple[list[dict[str, str]], dict[str, int] | None]:
    total = sum(len(turn["text"]) for turn in turns)
    if max_chars is None or total <= max_chars:
        return turns, None
    kept: list[dict[str, str]] = []
    left = max_chars
    for turn in turns:
        if left <= 0:
            break
        text = turn["text"][:left]
        kept.append({**turn, "text": text})
        left -= len(text)
    shown = max_chars - left
    kept.append(
        {
            "speaker": "reader",
            "text": f"[The rest of the transcript was cut by the reader: "
            f"{shown} of {total} characters shown.]",
        }
    )
    return kept, {
        "kept_chars": shown,
        "total_chars": total,
        "turns_total": len(turns),
    }


def _cut_list(
    items: list[Any], max_chars: int | None
) -> tuple[list[Any], dict[str, int] | None]:
    sizes = [len(json.dumps(item, default=str)) for item in items]
    total = sum(sizes)
    if max_chars is None or total <= max_chars:
        return items, None
    kept: list[Any] = []
    used = 0
    for item, size in zip(items, sizes, strict=True):
        if used + size > max_chars:
            break
        kept.append(item)
        used += size
    kept.append(
        f"[{len(items) - len(kept)} of {len(items)} entries were cut by the reader.]"
    )
    return kept, {
        "kept_chars": used,
        "total_chars": total,
        "entries_total": len(items),
    }


def _sized(
    kind: Kind,
    source: str,
    content: Any,
    max_chars: int | None,
    *,
    marks: tuple[str, ...] = (),
) -> Evidence:
    """A record the reader hands over whole or not at all."""
    size = len(json.dumps(content, default=str))
    if max_chars is not None and size > max_chars:
        return _missing(kind, Cause.TOO_LARGE, note=f"{size} characters.")
    return _present(kind, source, content, marks=marks)


def _present(
    kind: Kind,
    source: str,
    content: Any,
    *,
    cut: dict[str, int] | None = None,
    marks: tuple[str, ...] = (),
) -> Evidence:
    return Evidence(
        kind=kind,
        state=State.PRESENT,
        source=source,
        content=content,
        digest=canonical_digest(content),
        cut=cut,
        marks=marks,
    )


def _missing(kind: Kind, cause: Cause, *, note: str | None = None) -> Evidence:
    return Evidence(kind=kind, state=State.NOT_CAPTURED, cause=cause, note=note)


def _not_applicable(kind: Kind) -> Evidence:
    return Evidence(kind=kind, state=State.NOT_APPLICABLE)


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json(raw: bytes | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _same_id(stored: Any, given: Any) -> bool:
    if stored is None or given is None:
        return stored is None and given is None
    return str(stored) == str(given)
