"""The reader: one place that turns a saved call into the evidence a judge reads.

For each call and each of ten kinds it answers ``present`` (with the content, the
source it was read from and a digest of exactly that content), ``not_captured``
with a cause taken from stored data (or ``unreadable`` when a store could not be
read just then), or ``not_applicable``.

Its rules:

- Nothing is filled in. A kind the run did not keep is said to be missing, with why.
- Instructions are never cut. If they would not fit they are not handed over.
- Any other cut is made here, and said, in the content and in ``cut``.
- An empty record is present, sent as a plain statement, never as an empty value.
- World changes always say they were cut when they were taken in.
- Who spoke each turn is decided here, once.
- Secrets are masked, and masked content carries the ``secrets_masked`` mark; a
  recording link loses only a password written in it. The digest is of the masked
  content. Nothing is masked past the point where it would be cut.
- A call outside the caller's organisation and workspace, or deleted, is refused.
  Below the run test, a deleted agent version and a deleted job's files are not read;
  the scenario is read as the call ran it.
- One kind that cannot be read does not stop the others.
"""

from __future__ import annotations

import functools
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from accounts.models.workspace import Workspace
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
from simulate.services.hosted_harness_ingestion import _ALLOWED_ARTIFACTS
from simulate.services.run_traces_v3 import read_run_traces, run_traces_source
from simulate.utils.speaker_roles import SpeakerRoleResolver
from simulate.utils.stored_transcript_roles import call_actor_label
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)

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
    UNREADABLE = "unreadable"


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

# The kit's mark on a contract whose prompt its save copied by code: from the source as
# written, or a provider's whole prompt. Without it the excerpt is a quote.
SYSTEM_PROMPT_COPIED = "system_prompt_copied"
# Content a secret was masked in, so it differs from what is stored.
SECRETS_MASKED = "secrets_masked"
# Hosted connectors whose run is configured with the whole prompt a person typed.
_STATED_CONNECTORS = frozenset({"phone", "livekit"})
# The one file of a seed the reader hands over, and the most of it it reads.
_SEED_STORE = "store.json"
_SEED_STORE_MAX_BYTES = 32 * 1024 * 1024
# The most of an uploaded world file the reader fetches.
_ARTIFACT_MAX_BYTES = 32 * 1024 * 1024
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


class CallRefused(Exception):
    """The reader will not read this call."""


class CallNotInWorkspace(CallRefused):
    """The call belongs to another organisation or workspace."""


class CallDeleted(CallRefused):
    """The call, its run or its run test was deleted."""


class ArtifactTooLarge(Exception):
    """An uploaded file is over the size the reader fetches."""

    def __init__(self, size: int) -> None:
        super().__init__(size)
        self.size = size


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
    reader_version: int = READER_VERSION


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
        # An unknown level allows nothing: ingestion refuses every upload at one.
        return artifact_kind in _ALLOWED_ARTIFACTS.get(self.artifact_level, ())


@dataclass
class _Read:
    call: CallExecution
    trial: HostedTrial | None
    run_kind: RunKind
    organization_id: Any
    workspace_id: Any
    max_chars: int | None
    shared: dict[Any, Any] = field(default_factory=dict)
    states_only: bool = False
    _cache: dict[str, Any] = field(default_factory=dict)

    def once(self, key: tuple[Any, ...], load: Any) -> Any:
        """``load()``, read once per key for every call that shares this read."""
        return _once(self.shared, key, load)

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
            # By the call alone: its tenant was checked, and the current workspace
            # may be another of the organisation's.
            self._cache["chat"] = list(
                ChatMessageModel.no_workspace_objects.filter(
                    call_execution=self.call
                ).order_by("created_at")
            )
        messages: list[ChatMessageModel] = self._cache["chat"]
        return messages


# --- a configured eval's keys --------------------------------------------------

# The kind each source of the eval table (``harness_evals``) is read as. There is one
# table: a key is turned into a source there, and the source into a kind here.
KIND_BY_SOURCE = {
    "transcript": Kind.TRANSCRIPT,
    "agent_prompt": Kind.AGENT_INSTRUCTIONS,
    "scenario_columns.situation.value": Kind.SCENARIO,
    "voice_recording": Kind.AUDIO,
}
# Keys only an ask fills, through this reader. They are not in the eval table,
# because the graders of a run's own evals have no source for them, so today's
# offer at run creation does not change.
ASK_ONLY_KEYS = {
    "tool_calls": Kind.TOOL_CALLS,
    "world_changes": Kind.WORLD_CHANGES,
    "world_state_end": Kind.WORLD_STATE_END,
}


def kind_for_key(key: str, modality: str) -> Kind | None:
    """The kind a configured eval's key reads on a run of this modality, or None
    where no kind fills it (such an eval is not offered for an ask)."""
    from simulate.services.harness_evals import source_for_key

    if key in ASK_ONLY_KEYS:
        return ASK_ONLY_KEYS[key]
    source = source_for_key(key, modality)
    return None if source is None else KIND_BY_SOURCE.get(source)


# --- the public calls ----------------------------------------------------------


def read_call_evidence(
    call: CallExecution,
    kinds: Iterable[Kind],
    *,
    organization_id: Any,
    workspace_id: Any,
    max_chars: int | None = None,
    shared: dict[Any, Any] | None = None,
    states_only: bool = False,
) -> dict[Kind, Evidence]:
    """Each asked kind of evidence for one call. Refuses a call from elsewhere, or
    one that was deleted (or whose run or run test was).

    ``shared`` keeps what calls of one run read alike (a saved contract, a seed, the
    job's counts, the run's trace source) for the next call, when one caller reads
    many calls. ``states_only`` answers each kind's state without fetching what is
    kept elsewhere: an end-of-trial world file is present when its row is, and agent
    traces when the run has a trace source. Such an answer carries no content, and a
    stored world file that cannot be parsed is found out only when it is read.
    A store that fails is remembered in ``shared`` too, so it is asked once."""
    run = call.test_execution
    shared = shared if shared is not None else {}
    run_test = run.run_test
    if not _once(
        shared,
        ("in_workspace", run_test.id, str(organization_id), str(workspace_id)),
        lambda: in_workspace(run_test, organization_id, workspace_id),
    ):
        raise CallNotInWorkspace(str(call.id))
    if call.deleted or run.deleted or run_test.deleted:
        raise CallDeleted(str(call.id))
    trial = hosted_trial(call, shared=shared)
    read = _Read(
        call=call,
        trial=trial,
        run_kind=_run_kind(call, trial),
        organization_id=organization_id,
        workspace_id=workspace_id,
        max_chars=max_chars,
        shared=shared,
        states_only=states_only,
    )
    return {kind: _read_one(read, kind) for kind in map(Kind, kinds)}


def _read_one(read: _Read, kind: Kind) -> Evidence:
    """One kind; a store that fails just then makes this kind ``unreadable``, not the
    whole read. Anything else raises: a database error (the transaction is no longer
    usable), a cancellation, or a fault in the reader itself."""
    try:
        return _READERS[kind](read)
    except _store_errors():
        logger.exception(
            "call_evidence_kind_unreadable",
            call_execution_id=str(read.call.id),
            kind=kind.value,
        )
        return _missing(kind, Cause.UNREADABLE, note="It could not be read just now.")


@functools.cache
def _store_errors() -> tuple[type[Exception], ...]:
    """What the object store, the trace store and their connections raise when they
    fail: the errors ``_read_one`` turns into ``unreadable``."""
    from minio.error import MinioException
    from urllib3.exceptions import HTTPError

    from simulate.services.hosted_harness import HostedHarnessError

    errors: tuple[type[Exception], ...] = (
        MinioException,
        HTTPError,
        HostedHarnessError,
        OSError,
        EOFError,
    )
    try:
        from clickhouse_driver.errors import Error as ClickHouseError
    except ImportError:  # the trace store's client is optional (tracer/.../client.py)
        return errors
    return (*errors, ClickHouseError)


def in_workspace(run_test: Any, organization_id: Any, workspace_id: Any) -> bool:
    """Whether a run is one the run page shows in that organisation and workspace:
    its own workspace, or, from the organisation's default workspace, a run kept
    with no workspace (``simulate/views/scoping.py``)."""
    if not _same_id(run_test.organization_id, organization_id):
        return False
    if _same_id(run_test.workspace_id, workspace_id):
        return True
    if run_test.workspace_id is not None or workspace_id is None:
        return False
    return Workspace.objects.filter(
        id=workspace_id, organization_id=organization_id, is_default=True
    ).exists()


def run_kind_of(call: CallExecution) -> RunKind:
    return _run_kind(call, hosted_trial(call))


def hosted_trial(
    call: CallExecution, *, shared: dict[Any, Any] | None = None
) -> HostedTrial | None:
    """The hosted job this call was a trial of, or ``None`` for a call no job made.
    The job's counts are read once per ``shared``."""
    memo = shared if shared is not None else {}
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
        attempts=_once(memo, ("attempts", job.id), job.attempts.count),
        trials=_once(
            memo,
            ("trials", job.id, execution.source_scenario_id),
            HostedHarnessExecution.objects.filter(
                job_id=job.id, source_scenario_id=execution.source_scenario_id
            ).count,
        ),
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


def _environment_files(environment_version_id: Any) -> dict[str, bytes] | None:
    """The seed's store file of a saved environment version, streamed alone from its
    archive; ``None`` when the archive is gone. A store over the size cap, or an
    archive that is cut short or malformed, gives no file. A store that can't be
    reached raises."""
    import tarfile
    import zlib

    from minio.error import S3Error

    from simulate.models.harness_versioning import HarnessEnvironmentVersion
    from simulate.services.harness_versioning import archives
    from simulate.services.hosted_harness import HostedHarnessError

    version = HarnessEnvironmentVersion.no_workspace_objects.filter(
        id=environment_version_id
    ).first()
    if version is None or not version.archive_object_key:
        return None
    try:
        return archives.stream_members(
            version.archive_object_key, {_SEED_STORE}, _SEED_STORE_MAX_BYTES
        )
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    except (tarfile.TarError, EOFError, zlib.error, HostedHarnessError):
        return {}


def stored_artifact(read: _Read, digest: str, artifact_kind: str) -> bytes | None:
    """An uploaded artifact of the call's job, in the caller's tenant; ``None`` when not
    kept, or when its job was deleted. Raises ``ArtifactTooLarge`` over the cap, by the
    size its row records or by what is read."""
    from minio.error import S3Error

    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage_client import get_storage_client

    row = _artifact_row(read, digest, artifact_kind)
    if row is None:
        return None
    key, size = row
    if size is not None and size > _ARTIFACT_MAX_BYTES:
        raise ArtifactTooLarge(size)
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    try:
        body: bytes = response.read(_ARTIFACT_MAX_BYTES + 1)
    finally:
        response.close()
        response.release_conn()
    if len(body) > _ARTIFACT_MAX_BYTES:
        raise ArtifactTooLarge(len(body))
    return body


def _artifact_row(
    read: _Read, digest: str, artifact_kind: str
) -> tuple[str, int | None] | None:
    """The stored key and recorded size of an upload of the call's job, in the
    caller's tenant, while its job is not deleted."""
    if read.trial is None:
        return None
    # The call passed the tenant check; its job is kept where its run is.
    run_test = read.call.test_execution.run_test
    rows = HostedHarnessArtifact.no_workspace_objects.filter(
        job_id=read.trial.job_id,
        sha256=digest.removeprefix("sha256:"),
        kind=artifact_kind,
        job__deleted=False,
        job__organization_id=run_test.organization_id,
    )
    rows = (
        rows.filter(job__workspace_id=run_test.workspace_id)
        if run_test.workspace_id is not None
        else rows.filter(job__workspace_id__isnull=True)
    )
    row = rows.values_list("object_key", "size").first()
    if row is None or not row[0]:
        return None
    return row[0], row[1]


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
    kept, cut, marks = _cut_turns(turns, read.max_chars)
    return _present(Kind.TRANSCRIPT, source, kept, cut=cut, marks=marks)


def _agent_instructions(read: _Read) -> Evidence:
    whole = _whole_instructions(read)
    if whole is None:
        return _missing(Kind.AGENT_INSTRUCTIONS, Cause.NO_WHOLE_INSTRUCTIONS)
    text, source = whole
    # Checked before masking too, so text that will not be sent is not masked.
    if (refused := _instructions_too_large(text, read.max_chars)) is not None:
        return refused
    content, marks = _masked(text)
    if (refused := _instructions_too_large(content, read.max_chars)) is not None:
        return refused
    return _present(Kind.AGENT_INSTRUCTIONS, source, content, marks=marks)


def _instructions_too_large(text: str, max_chars: int | None) -> Evidence | None:
    if max_chars is None or len(text) <= max_chars:
        return None
    return _missing(
        Kind.AGENT_INSTRUCTIONS,
        Cause.TOO_LARGE,
        note=f"{len(text)} characters; instructions are never cut.",
    )


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
    version_id = read.trial.environment_version_id
    files = read.once(("seed", version_id), lambda: _environment_files(version_id))
    if files is None:
        return _missing(kind, Cause.NO_LONGER_KEPT)
    store = _json(files.get(_SEED_STORE))
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
    if read.states_only:
        row = _artifact_row(read, digest, "world_state")
        if row is None:
            return _missing(kind, Cause.NO_LONGER_KEPT)
        if row[1] is not None and row[1] > _ARTIFACT_MAX_BYTES:
            return _missing(kind, Cause.TOO_LARGE, note=f"{row[1]} bytes stored.")
        return _present_unread(kind, "hosted_world_state")
    try:
        body = stored_artifact(read, digest, "world_state")
    except ArtifactTooLarge as exc:
        return _missing(kind, Cause.TOO_LARGE, note=f"{exc.size} bytes stored.")
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
    stored = {
        name: url
        for name in ("recording_url", "stereo_recording_url")
        if (url := getattr(read.call, name, None))
    }
    if stored:
        # A signed link must still play: only a password written in it is masked.
        recordings = {name: _mask_url_passwords(url) for name, url in stored.items()}
        marks = (SECRETS_MASKED,) if recordings != stored else ()
        return _present(kind, "call_recordings", recordings, marks=marks)
    if read.trial is not None:
        return _missing(kind, _hosted_cause(read.trial, "recording_combined"))
    return _missing(kind, Cause.NO_RECORD)


def _agent_traces(read: _Read) -> Evidence:
    kind = Kind.AGENT_TRACES
    run = read.call.test_execution
    source = read.once(("traces_source", run.id), lambda: run_traces_source(run))
    if source is None:
        return _not_applicable(kind)
    if read.states_only:
        # Present whether or not the agent recorded any: an empty record is a record.
        return _present_unread(kind, "agent_traces")
    traces = read_run_traces(source, call_execution_id=str(read.call.id))
    if not traces:
        return _present(kind, "agent_traces", EMPTY_TRACE_RECORD)
    kept, cut, marks = _cut_list(
        [_trace_row(trace) for trace in traces], read.max_chars
    )
    return _present(kind, "agent_traces", kept, cut=cut, marks=marks)


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
    if version is not None and version.deleted:
        version = None
    copied = _copied_prompt(read.trial.contract if read.trial else None)
    if copied is None and version is not None:
        copied = _copied_prompt(
            read.once(
                ("contract", version.id), lambda: stored_version_contract(version.id)
            )
        )
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
    if read.once(
        ("source_agent", version.id),
        lambda: AgentVersionSetup.no_workspace_objects.filter(
            agent_version_id=version.id, agent_kind="source"
        ).exists(),
    ):
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
        SpeakerRoleResolver.detect_provider(dict(provider_data))
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
    speakers: dict[str, str] = {
        ChatMessageModel.RoleChoices.USER: "customer",
        ChatMessageModel.RoleChoices.ASSISTANT: "agent",
    }
    turns: list[dict[str, str]] = []
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
    kept, cut, masked = _cut_list(tools, max_chars)
    return _present(kind, source, kept, cut=cut, marks=marks + masked)


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
) -> tuple[list[dict[str, str]], dict[str, int] | None, tuple[str, ...]]:
    """The turns masked, then cut at ``max_chars``. Turns past the cut are not masked,
    and are counted at their stored length."""
    if max_chars is None:
        whole, whole_marks = _masked(turns)
        return whole, None, whole_marks
    kept: list[dict[str, str]] = []
    marks: tuple[str, ...] = ()
    left = max_chars
    total = 0
    for turn in turns:
        if left <= 0:
            total += len(turn["text"])
            continue
        masked_turn, turn_marks = _masked(turn)
        text = masked_turn["text"]
        total += len(text)
        kept.append({**masked_turn, "text": text[:left]})
        left -= len(kept[-1]["text"])
        marks = marks or turn_marks
    if total <= max_chars:
        return kept, None, marks
    shown = max_chars - left
    kept.append(
        {
            "speaker": "reader",
            "text": f"[The rest of the transcript was cut by the reader: "
            f"{shown} of {total} characters shown.]",
        }
    )
    return (
        kept,
        {"kept_chars": shown, "total_chars": total, "turns_total": len(turns)},
        marks,
    )


def _cut_list(
    items: list[Any], max_chars: int | None
) -> tuple[list[Any], dict[str, int] | None, tuple[str, ...]]:
    """The entries masked, then cut at the first that does not fit in ``max_chars``.
    An entry whose stored size does not fit is cut before it is masked, even where
    masking would have made it fit. Entries past the cut are not masked, and are
    counted at their stored size."""
    if max_chars is None:
        whole, whole_marks = _masked(items)
        return whole, None, whole_marks
    kept: list[Any] = []
    marks: tuple[str, ...] = ()
    used = 0
    total = 0
    full = True
    for item in items:
        if not full:
            total += len(json.dumps(item, default=str))
            continue
        stored = len(json.dumps(item, default=str))
        if used + stored > max_chars:
            total += stored
            full = False
            continue
        masked_item, item_marks = _masked(item)
        size = len(json.dumps(masked_item, default=str))
        total += size
        if used + size > max_chars:
            full = False
            continue
        kept.append(masked_item)
        used += size
        marks = marks or item_marks
    if full:
        return kept, None, marks
    kept.append(
        f"[{len(items) - len(kept)} of {len(items)} entries were cut by the reader.]"
    )
    return (
        kept,
        {"kept_chars": used, "total_chars": total, "entries_total": len(items)},
        marks,
    )


def _sized(
    kind: Kind,
    source: str,
    content: Any,
    max_chars: int | None,
    *,
    marks: tuple[str, ...] = (),
) -> Evidence:
    """A record the reader hands over whole or not at all. Its size is checked before
    masking too, so a record that will not be sent is not masked."""
    if max_chars is not None:
        size = len(json.dumps(content, default=str))
        if size > max_chars:
            return _missing(kind, Cause.TOO_LARGE, note=f"{size} characters.")
    content, masked = _masked(content)
    size = len(json.dumps(content, default=str))
    if max_chars is not None and size > max_chars:
        return _missing(kind, Cause.TOO_LARGE, note=f"{size} characters.")
    return _present(kind, source, content, marks=marks + masked)


# --- secrets ---------------------------------------------------------------------------

_MASK = "[REDACTED]"
# Text kept under a key holding one of these is masked wherever it sits below that
# key. A number is masked only under its own key's name (``_NUMBER_SECRET_WORDS``),
# and true/false never: a count is not a secret. Every header is masked.
_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "credential",
    "authorization",
    "password",
    "passphrase",
    "cookie",
    "private_key",
)
# The words of a key's name (split at symbols and at a capital after a small letter)
# that make a number under it a secret, and those that make it a count or a limit.
_NUMBER_SECRET_WORDS = frozenset(
    {"password", "passphrase", "passcode", "passwd", "secret", "otp", "pin", "token"}
)
_NUMBER_COUNT_WORDS = frozenset({"count", "max", "min", "limit", "length", "size"})
_KEY_WORD_BREAK = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[^A-Za-z0-9]+")
# In text, a secret's name, then ``:`` or ``=``, with padding and at most one line
# break on each side. The name may be quoted, and its quotes escaped as in JSON text
# inside JSON text. Every part is bounded, so each start is read in constant time.
_SECRET_NAME = re.compile(
    r"""(?ix)
    (?P<open>\\{0,15}["'])?
    (?P<word>authorization|api[_-]?key|secret|token|password|passphrase)
    (?P<close>\\{0,15}["'])?
    [ \t]{0,64}(?:\r?\n[ \t]{0,64})?
    (?P<sep>[:=])
    [ \t]{0,64}(?:\r?\n[ \t]{0,64})?
    """
)
# What may stand before a name at the start of its line: indent, a list or heading
# mark, and the rest of the key's own name (``GITHUB_``, ``X-Api-``).
_LINE_HEAD = re.compile(r"(?:\n|\A)[ \t]{0,16}(?:[-*#>]{1,4}[ \t]{1,4})?[\w.-]{0,48}\Z")
_LINE_HEAD_REACH = 80
_ESCAPED_QUOTE = re.compile(r'\\{1,15}"')
_ESCAPED_VALUE_END = re.compile(r'["\\\n]')
# A bare value runs to the end of its line, or to a comma, semicolon, brace or quote.
_BARE_VALUE_END = re.compile(r"""[\n,;}"'\\]""")
# The look at a bare value after a name inside a sentence: a few short words.
_LOOK_WORD = re.compile(r"""[ \t]{0,64}([^\s,;}"'\\]{1,64})""")
_LOOK_WORDS = 8
_LOOK_REACH = 1024
_KEY_SHAPED_LENGTH = 16
_WORD_PUNCTUATION = ".,!?:;()[]"
# Words that make the words after a secret's name a sentence, not a value.
_SENTENCE_WORDS = frozenset(
    """
    a an the is are was be been it its to of for from and or but not no never do
    does don't must should can will would only if when then than by on in at with
    as about after before this that these those there here you your we our they
    them their he she his her i me my say ask tell please e.g i.e etc what which
    who how why so also any all each every just
    """.split()
)
_AUTH_SCHEMES = frozenset({"basic", "bearer", "token", "digest", "apikey", "negotiate"})
# scheme://user:password@. The lookbehind means a start inside a run of scheme
# characters is never tried, which keeps the scan linear on a long id or hex run.
_URL_USERINFO = re.compile(
    r"(?<![A-Za-z0-9+.\-])(?P<head>[A-Za-z][A-Za-z0-9+.\-]*://[^:/?#@\s]*):[^/?#\s]*@"
)
_URL_QUERY_PASSWORD = re.compile(r"(?P<head>[?&]password=)[^&#\s]*")
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
# (pattern, replacement): secrets by their shape, wherever they sit in text. Each is
# bounded, and none can start inside a run of its own characters, so a long run of
# near-misses is read once.
_SECRET_SHAPES = (
    (re.compile(r"(?i)(\bbearer\s{1,16})[A-Za-z0-9._~+/=-]{8,}"), rf"\1{_MASK}"),
    (re.compile(r"(?i)([?&](?:access_)?token=)[^&\s]+"), rf"\1{_MASK}"),
    (re.compile(r"\b(?:sk|pk|rk)-(?=[A-Za-z_-]{0,64}\d)[A-Za-z0-9_-]{16,}"), _MASK),
    (re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), _MASK),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
        ),
        _MASK,
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), _MASK),
    (re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"), _MASK),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), _MASK),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), _MASK),
)


def _masked(content: Any) -> tuple[Any, tuple[str, ...]]:
    """``content`` with its secrets masked, and the mark when any was."""
    masked = _mask(content)
    return masked, ((SECRETS_MASKED,) if masked != content else ())


def _mask(value: Any, under_secret: bool = False) -> Any:
    if isinstance(value, str):
        return _MASK if under_secret and value else _mask_text(value)
    if isinstance(value, Mapping):
        return {
            key: _mask_under(key, item, under_secret) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask(item, under_secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask(item, under_secret) for item in value)
    return value


def _mask_under(key: Any, value: Any, under_secret: bool) -> Any:
    name = str(key).lower()
    if name == "headers" and isinstance(value, Mapping):
        return dict.fromkeys(value, _MASK)
    if _numbers_a_secret(key, value):
        return _MASK
    return _mask(
        value, under_secret or any(marker in name for marker in _SECRET_KEY_MARKERS)
    )


def _numbers_a_secret(key: Any, value: Any) -> bool:
    """Whether ``value`` is a number its own key names as a secret: a PIN, a one-time
    code, a numeric password. Only the key's own name counts, so an id below a
    ``password_resets`` key is kept."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    words = {word.lower() for word in _KEY_WORD_BREAK.split(str(key)) if word}
    if words & _NUMBER_COUNT_WORDS:
        return False
    return bool(words & _NUMBER_SECRET_WORDS) or {"api", "key"} <= words


def _mask_text(text: str) -> str:
    text = _mask_url_passwords(text)
    text = _mask_private_keys(text)
    for pattern, replacement in _SECRET_SHAPES:
        text = pattern.sub(replacement, text)
    return _mask_named_values(text)


def _mask_url_passwords(text: str) -> str:
    text = _URL_USERINFO.sub(r"\g<head>:***@", text)
    return _URL_QUERY_PASSWORD.sub(r"\g<head>***", text)


def _mask_private_keys(text: str) -> str:
    """Each private-key block masked from its header to its END, or to the end of the
    text when it has none. One forward search each, so the scan stays linear."""
    parts: list[str] = []
    at = 0
    while begin := _PEM_BEGIN.search(text, at):
        parts += [text[at : begin.start()], _MASK]
        end = _PEM_END.search(text, begin.end())
        at = end.end() if end else len(text)
    return "".join([*parts, text[at:]])


def _mask_named_values(text: str) -> str:
    """Each value after a secret's name masked, in one forward pass.

    A quoted or escaped value is masked to its closing quote. A bare value is masked
    to the end of its line (or a comma, semicolon, brace or quote), unless the name
    sits inside a sentence and the words after it read as one (``_reads_as_a_rule``).
    Then only the name is passed over, and the scan goes on from it, so a secret
    later on the same line is still found."""
    parts: list[str] = []
    at = 0
    scan = 0
    while name := _SECRET_NAME.search(text, scan):
        found = _named_value(text, name)
        if found is None:
            scan = name.end()
            continue
        start, end = found
        parts += [text[at:start], _MASK]
        at = scan = max(end, name.end())
    return "".join([*parts, text[at:]])


def _named_value(text: str, name: re.Match[str]) -> tuple[int, int] | None:
    """Where the value after ``name`` starts and ends, when it is to be masked."""
    start = name.end()
    if text.startswith(_MASK, start):
        return None
    if escaped := _ESCAPED_QUOTE.match(text, start):
        end = _ESCAPED_VALUE_END.search(text, escaped.end())
        stop = end.start() if end else len(text)
        return (escaped.end(), stop) if stop > escaped.end() else None
    if start < len(text) and text[start] in "\"'":
        close = text.find(text[start], start + 1)
        if close != -1 and "\n" not in text[start + 1 : close]:
            return (start, close + 1) if close > start + 1 else None
        start += 1
    # The look is bounded; the value's own end is searched only when it is masked,
    # so a long line of names inside sentences is read once.
    look_end = min(len(text), start + _LOOK_REACH)
    near = _BARE_VALUE_END.search(text, start, look_end)
    if start >= len(text) or (near is not None and near.start() == start):
        return None
    if not _is_a_field(text, name) and _reads_as_a_rule(
        text, start, near.start() if near else look_end, name.group("word").lower()
    ):
        return None
    end = near or _BARE_VALUE_END.search(text, start)
    return start, end.start() if end else len(text)


def _is_a_field(text: str, name: re.Match[str]) -> bool:
    """A name that is quoted, set with ``=``, or starts its line names a value."""
    if name.group("open") or name.group("close") or name.group("sep") == "=":
        return True
    reach = max(0, name.start() - _LINE_HEAD_REACH)
    head = _LINE_HEAD.search(text, reach, name.start())
    return head is not None and (head.group(0).startswith("\n") or reach == 0)


def _reads_as_a_rule(text: str, start: int, stop: int, word: str) -> bool:
    """Whether the bare value after a name inside a sentence is a rule, not a secret:
    several words, the first (and a scheme's credential) not shaped like a key, with
    a plain sentence word among the first few. A look cut short is a secret."""
    words: list[str] = []
    at = start
    while len(words) < _LOOK_WORDS and (found := _LOOK_WORD.match(text, at, stop)):
        words.append(found.group(1))
        at = found.end()
    if len(words) < 2:
        return False
    lead = 2 if word == "authorization" and _plain(words[0]) in _AUTH_SCHEMES else 1
    if any(_key_shaped(each) for each in words[:lead]):
        return False
    return any(_plain(each) in _SENTENCE_WORDS for each in words)


def _plain(word: str) -> str:
    return word.strip(_WORD_PUNCTUATION).lower()


def _key_shaped(word: str) -> bool:
    plain = word.strip(_WORD_PUNCTUATION)
    return len(plain) >= _KEY_SHAPED_LENGTH or any(c.isdigit() for c in plain)


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


def _present_unread(kind: Kind, source: str) -> Evidence:
    """Present, for a states-only read: nothing was fetched, so nothing is given."""
    return Evidence(kind=kind, state=State.PRESENT, source=source)


def _once(memo: dict[Any, Any], key: tuple[Any, ...], load: Any) -> Any:
    """``load()`` once per key. A store error is kept and raised again, so a store
    that is down is asked once for every call that shares ``memo``."""
    if key not in memo:
        try:
            memo[key] = (load(), None)
        except _store_errors() as failed:
            memo[key] = (None, failed)
    value, kept_error = memo[key]
    if kept_error is not None:
        raise kept_error
    return value


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
