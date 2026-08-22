"""Business logic for ALK sim ingestion.

All view code delegates here. Nothing in this module knows about DRF, requests,
or serializers — inputs are plain Python objects/dicts, outputs are dataclasses
or dicts. Callable from views, Temporal activities, tests, or the shell.

Recording/artifact URLs are supplied by the client as strings; the backend
never uploads bytes (same pattern as the Vapi provider adapter).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    Scenarios,
    SimulatorAgent,
    TestExecution,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import CallTranscript
from simulate.pydantic_schemas.chat import ChatRole
from simulate.semantics import SupportedProviders
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
)
from simulate.utils.ended_reason import to_canonical_ended_reason
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from simulate.utils.websocket_notifications import notify_simulation_update
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_object_url, get_storage_client
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)

DEFAULT_BATCH_SIZE = 9
_ALK_BATCH_CLAIMED_KEY = "alk_batch_claimed"
# Bookkeeping keys the backend owns inside ``call_metadata``. A result PATCH
# merges caller-supplied ``call_metadata`` caller-wins, so the caller must not be
# able to set these — otherwise a PATCH could release a claimed row
# (``alk_batch_claimed``) or re-trigger cost deduction / CSAT dispatch.
_RESERVED_CALL_METADATA_KEYS = frozenset(
    {
        _ALK_BATCH_CLAIMED_KEY,
        "cost_deducted",
        "csat_dispatched",
        # Eval/CSAT idempotency guards + failure diagnostics the backend owns;
        # a caller flipping these could suppress or re-trigger eval dispatch.
        "eval_started",
        "eval_dispatch_failed",
        "csat_dispatch_failed",
    }
)

_STATUS_MAP = {
    "completed": CallExecution.CallStatus.COMPLETED,
    "failed": CallExecution.CallStatus.FAILED,
    "cancelled": CallExecution.CallStatus.CANCELLED,
}

_COST_FIELDS = (
    "stt_cost_cents",
    "llm_cost_cents",
    "tts_cost_cents",
    "storage_cost_cents",
    "cost_cents",
)

_TRANSCRIPT_ROLE_TO_METRIC_ROLE = {
    CallTranscript.SpeakerRole.USER: "user",
    CallTranscript.SpeakerRole.ASSISTANT: "bot",
}


@dataclass(frozen=True)
class BatchCreateResult:
    call_execution_ids: list[str]
    has_more: bool
    batched_scenarios: list[str]


@dataclass(frozen=True)
class IngestionResult:
    call_execution_id: str
    status: str
    eval_dispatched: bool


class ALKSimulateIngestionError(Exception):
    """Raised when a LiveKit ingestion request cannot be satisfied.

    Views translate this into a 400 response; internal callers can catch and
    branch. The message is safe to surface to the caller — do not include
    sensitive detail.
    """


class ALKSimulateInvalidCallTypeError(ALKSimulateIngestionError):
    """The target CallExecution is not a VOICE row."""


class ALKSimulateNothingToCreateError(ALKSimulateIngestionError):
    """All scenarios and dataset rows for this test execution are already batched."""


_ALK_RECORDING_PREFIX = "alk-sim/recordings"
_CONTENT_TYPE_BY_EXT = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
}


@dataclass(frozen=True)
class RecordingUploadResult:
    recording_url: str
    object_key: str


def store_alk_recording(
    call_execution: CallExecution,
    audio_bytes: bytes,
    *,
    filename: str | None = None,
) -> RecordingUploadResult:
    """Persist an ALK-supplied recording to the shared upload bucket.

    Uses the storage client directly (``put_object``) — bypasses
    ``tfc.utils.storage.upload_audio_to_s3`` because that helper calls
    ``ensure_bucket``/``bucket_exists``, which needs list-bucket permission
    the prod HMAC credentials do not grant. Bucket lifecycle here is owned
    by infra (Terraform / Helm); this path only writes objects into it.
    """
    if call_execution.simulation_call_type != CallExecution.SimulationCallType.VOICE:
        raise ALKSimulateInvalidCallTypeError(
            "Recording uploads are only valid for VOICE call executions"
        )
    if not audio_bytes:
        raise ALKSimulateIngestionError("recording upload was empty")

    ext = _extension_from_filename(filename)
    content_type = _CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")
    object_key = f"{_ALK_RECORDING_PREFIX}/{call_execution.id}/{uuid.uuid4().hex}.{ext}"
    client = get_storage_client()
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=object_key,
        data=BytesIO(audio_bytes),
        length=len(audio_bytes),
        content_type=content_type,
    )
    recording_url = get_object_url(UPLOAD_BUCKET_NAME, object_key)
    return RecordingUploadResult(
        recording_url=recording_url,
        object_key=object_key,
    )


def _extension_from_filename(filename: str | None) -> str:
    if not filename:
        return "wav"
    _, _, tail = filename.rpartition(".")
    tail = tail.lower().strip()
    return tail if tail and 1 <= len(tail) <= 5 else "wav"


def _provision_text_agent_definition(
    organization, agent_definition_id, agent_name, description
):
    """Resolve the RunTest's agent definition for provisioning.

    Explicit id must resolve to a non-VOICE agent (voice is entitlement-gated in
    CreateRunTestView; provisioning must not bypass that gate). Otherwise a TEXT
    agent is created — chat call type follows the agent definition's type.
    """
    if agent_definition_id:
        try:
            agent_definition = AgentDefinition.objects.get(
                id=agent_definition_id, organization=organization, deleted=False
            )
        except AgentDefinition.DoesNotExist as exc:
            raise ALKSimulateIngestionError(
                f"agent definition {agent_definition_id} not found"
            ) from exc
        if agent_definition.agent_type == AgentDefinition.AgentTypeChoices.VOICE:
            raise ALKSimulateIngestionError(
                "voice agent definitions cannot be provisioned via ALK ingestion"
            )
        return agent_definition
    return AgentDefinition.objects.create(
        agent_name=agent_name or "alk-sdk-agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,  # NOT NULL; call-direction is a no-op for chat
        description=description or "SDK-provisioned chat agent (ALK ingestion).",
        organization=organization,
    )


def provision_alk_sim_run_test(
    organization,
    *,
    name: str,
    personas: list[dict] | None = None,
    scenario_ids: list | None = None,
    agent_definition_id: str | None = None,
    agent_name: str | None = None,
    description: str = "",
) -> tuple[RunTest, list[Scenarios], AgentDefinition]:
    """Stand up a chat RunTest for an SDK-first run, two ways (exactly one):

    * ``scenario_ids`` — attach existing (natively generated) scenarios. Nothing
      is fabricated or mutated; the scenarios keep their real datasets so they
      render in the UI. A run-test-level ``simulator_agent`` is set from the
      scenarios so the batch never writes ``simulator_agent`` back onto the
      shared scenario. Preferred.
    * ``personas`` — fabricate one COMPLETED persona-dataset scenario per persona
      (see ``_build_persona_scenario_dataset``). Self-contained fallback; the
      dataset lacks the generated ``column_config`` the scenarios UI reads.

    One CallExecution is created per dataset row at batch time, so keep the row
    count (== persona count, or the reused scenarios' rows) equal to the
    conversations the run posts per execution; extras leave dangling PENDING rows.
    """
    from django.db import transaction

    from model_hub.models.choices import StatusType

    with transaction.atomic():
        if scenario_ids:
            scenarios = list(
                Scenarios.objects.filter(
                    id__in=scenario_ids, organization=organization, deleted=False
                ).select_related("agent_definition", "simulator_agent")
            )
            found = {str(s.id) for s in scenarios}
            missing = [str(sid) for sid in scenario_ids if str(sid) not in found]
            if missing:
                raise ALKSimulateIngestionError(
                    f"scenario(s) not found: {', '.join(missing)}"
                )
            agent_definition = _provision_text_agent_definition(
                organization, agent_definition_id, agent_name, description
            )
            simulator_agent = next(
                (s.simulator_agent for s in scenarios if s.simulator_agent), None
            )
            if simulator_agent is None:
                # None of the reused scenarios carries a simulator agent — give
                # the run test its own so batch's _resolve_simulator_agent returns
                # it instead of creating one and writing it onto the shared scenario.
                simulator_agent = SimulatorAgent.objects.create(
                    name=f"{name} · simulator",
                    prompt=generate_simulator_agent_prompt(agent_version=None),
                    organization=organization,
                )
            run_test = RunTest.objects.create(
                name=name,
                description=description,
                agent_definition=agent_definition,
                simulator_agent=simulator_agent,
                organization=organization,
            )
            run_test.scenarios.set(scenarios)
            return run_test, scenarios, agent_definition

        agent_definition = _provision_text_agent_definition(
            organization, agent_definition_id, agent_name, description
        )

        scenarios: list[Scenarios] = []
        for idx, persona in enumerate(personas):
            persona = dict(persona or {})
            persona_name = (persona.get("name") or f"persona-{idx + 1}").strip()
            situation = (persona.get("situation") or "").strip()
            scenario_name = f"{name} · {persona_name}"[:255]
            # A real 1-row dataset (persona/situation/outcome) makes the scenario
            # render with persona rows AND lets the simulator prompt's
            # {{persona}}/{{situation}} placeholders resolve — without it the
            # placeholders ship to the model unsubstituted.
            dataset = _build_persona_scenario_dataset(
                organization, scenario_name, persona
            )
            scenarios.append(
                Scenarios.objects.create(
                    name=scenario_name,
                    # ``clean()`` rejects blank source; fall back to the name.
                    source=situation or persona_name,
                    scenario_type=Scenarios.ScenarioTypes.DATASET,
                    source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
                    agent_definition=agent_definition,
                    organization=organization,
                    dataset=dataset,
                    status=StatusType.COMPLETED.value,
                    metadata={"origin": "alk_sdk_ingestion", "persona": persona},
                )
            )

        run_test = RunTest.objects.create(
            name=name,
            description=description,
            agent_definition=agent_definition,
            organization=organization,
        )
        run_test.scenarios.set(scenarios)

    return run_test, scenarios, agent_definition


def _build_persona_scenario_dataset(organization, scenario_name: str, persona: dict):
    """Materialize one SDK persona as a 1-row scenario dataset.

    Mirrors the native dataset-scenario grid (persona / situation / outcome
    columns) minus the async LLM generation — the SDK already carries the
    persona. Gives the scenario a real row (``row_count`` > 0, so it renders in
    the scenarios tab) and lets ``_generate_dynamic_prompt`` resolve the
    ``{{persona}}`` / ``{{situation}}`` placeholders against it.
    """
    import json

    from model_hub.models.choices import (
        DatasetSourceChoices,
        DataTypeChoices,
        SourceChoices,
        StatusType,
    )
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    persona = dict(persona or {})
    identity = persona.get("persona")
    if not isinstance(identity, dict):
        identity = {
            key: value
            for key, value in (
                ("name", persona.get("name")),
                ("role", persona.get("role")),
            )
            if value
        }

    dataset = Dataset.objects.create(
        name=f"{scenario_name} · personas"[:2000],
        source=DatasetSourceChoices.SCENARIO.value,
        organization=organization,
    )
    column_specs = (
        ("persona", DataTypeChoices.PERSONA.value),
        ("situation", DataTypeChoices.TEXT.value),
        ("outcome", DataTypeChoices.TEXT.value),
    )
    columns = {
        col_name: Column.objects.create(
            name=col_name,
            data_type=data_type,
            source=SourceChoices.OTHERS.value,
            dataset=dataset,
            status=StatusType.COMPLETED.value,
        )
        for col_name, data_type in column_specs
    }
    row = Row.objects.create(dataset=dataset, order=0)
    values = {
        "persona": json.dumps(identity, ensure_ascii=False),
        "situation": (persona.get("situation") or "").strip(),
        "outcome": (persona.get("outcome") or "").strip(),
    }
    Cell.objects.bulk_create(
        [
            Cell(dataset=dataset, column=columns[key], row=row, value=value)
            for key, value in values.items()
        ]
    )
    return dataset


def create_alk_sim_test_execution(
    run_test: RunTest,
    *,
    scenario_ids: list[str] | None = None,
    simulator_agent: SimulatorAgent | None = None,
) -> TestExecution:
    """Create a TestExecution shell for an ALK-owned run.

    Unlike ``RunTestExecutionView`` this does not dispatch Temporal or Celery
    orchestration — the SDK already ran the simulation and will PATCH results
    into the CallExecution rows created by ``create_alk_sim_call_execution_batch``.
    """
    active_scenario_ids = list(
        run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
    )
    if scenario_ids:
        requested = {str(sid) for sid in scenario_ids}
        allowed = {str(sid) for sid in active_scenario_ids}
        chosen = [sid for sid in scenario_ids if str(sid) in allowed]
        missing = requested - allowed
        if missing:
            raise ALKSimulateIngestionError(
                f"Scenarios not attached to this run test: {sorted(missing)}"
            )
    else:
        chosen = [str(sid) for sid in active_scenario_ids]

    if not chosen:
        raise ALKSimulateIngestionError(
            "run_test has no scenarios; attach at least one before starting an ALK execution"
        )

    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        started_at=timezone.now(),
        total_scenarios=len(chosen),
        scenario_ids=[str(sid) for sid in chosen],
        picked_up_by_executor=True,
        simulator_agent=simulator_agent or run_test.simulator_agent,
        agent_definition=run_test.agent_definition,
        agent_version=run_test.agent_version,
    )


def create_alk_sim_call_execution_batch(
    test_execution: TestExecution,
    *,
    count: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> BatchCreateResult:
    """Claim rows for an ALK runner batch.

    Hosted executions pre-create unclaimed PENDING rows so the UI can render
    immediately. SDK-first executions have no rows yet. This function supports
    both: it adopts unclaimed rows and creates only missing rows, preserving the
    existing paginated ``call_execution_ids`` contract without duplicates.

    An explicit ``count`` selects at most that exact number of remaining rows.
    When omitted, the legacy ``batch_size + 1`` behavior is preserved.
    """
    if count is not None and count < 1:
        raise ALKSimulateIngestionError("count must be at least 1")

    with transaction.atomic():
        locked_execution = TestExecution.objects.select_for_update().get(
            id=test_execution.id
        )
        expected_calls = _build_expected_call_executions(locked_execution)
        existing_calls = list(
            locked_execution.calls.select_for_update()
            .filter(deleted=False)
            .order_by("created_at", "id")
        )
        existing_by_key = {_call_execution_key(call): call for call in existing_calls}

        available: list[tuple[CallExecution, bool]] = []
        for expected_call in expected_calls:
            existing_call = existing_by_key.get(_call_execution_key(expected_call))
            if existing_call is None:
                available.append((expected_call, False))
                continue

            is_unclaimed = (
                existing_call.status == CallExecution.CallStatus.PENDING
                and not (existing_call.call_metadata or {}).get(_ALK_BATCH_CLAIMED_KEY)
            )
            if is_unclaimed:
                available.append((existing_call, True))

        if not available:
            raise ALKSimulateNothingToCreateError(
                "No remaining call executions to create. All scenarios and rows "
                "have been processed."
            )

        batch_limit = count if count is not None else batch_size + 1
        selected = available[:batch_limit]
        adopted: list[CallExecution] = []
        new_calls: list[CallExecution] = []
        for call, already_exists in selected:
            metadata = dict(call.call_metadata or {})
            metadata[_ALK_BATCH_CLAIMED_KEY] = True
            call.call_metadata = metadata
            if already_exists:
                adopted.append(call)
            else:
                new_calls.append(call)

        if adopted:
            CallExecution.objects.bulk_update(adopted, ["call_metadata"])
        if new_calls:
            CallExecution.objects.bulk_create(new_calls)
            locked_execution.total_calls = len(existing_calls) + len(new_calls)
            locked_execution.save(update_fields=["total_calls"])

        return BatchCreateResult(
            call_execution_ids=[str(call.id) for call, _ in selected],
            has_more=len(available) > batch_limit,
            batched_scenarios=sorted({str(call.scenario_id) for call, _ in selected}),
        )


def precreate_alk_sim_call_executions(
    test_execution: TestExecution,
) -> list[str]:
    """Create the hosted execution's visible PENDING rows before dispatch."""
    with transaction.atomic():
        locked_execution = TestExecution.objects.select_for_update().get(
            id=test_execution.id
        )
        expected_calls = _build_expected_call_executions(locked_execution)
        existing_calls = list(
            locked_execution.calls.filter(deleted=False).order_by("created_at", "id")
        )
        existing_by_key = {_call_execution_key(call): call for call in existing_calls}

        ordered_calls: list[CallExecution] = []
        missing_calls: list[CallExecution] = []
        for expected_call in expected_calls:
            existing_call = existing_by_key.get(_call_execution_key(expected_call))
            if existing_call is not None:
                ordered_calls.append(existing_call)
                continue
            metadata = dict(expected_call.call_metadata or {})
            metadata[_ALK_BATCH_CLAIMED_KEY] = False
            expected_call.call_metadata = metadata
            missing_calls.append(expected_call)
            ordered_calls.append(expected_call)

        if missing_calls:
            CallExecution.objects.bulk_create(missing_calls)

        locked_execution.total_calls = len(ordered_calls)
        locked_execution.save(update_fields=["total_calls"])
        return [str(call.id) for call in ordered_calls]


def mark_alk_sim_call_ongoing(call_execution: CallExecution) -> bool:
    """Flip a pre-created PENDING call row to ONGOING when its case starts.

    A single PENDING-gated UPDATE, so it is idempotent and a late/duplicate ping
    can never clobber a terminal result — a row already COMPLETED/FAILED/CANCELLED
    (or already ONGOING) is left untouched. Returns whether a row transitioned.
    """
    updated = CallExecution.objects.filter(
        id=call_execution.id,
        status=CallExecution.CallStatus.PENDING,
    ).update(status=CallExecution.CallStatus.ONGOING)
    return bool(updated)


def ingest_alk_sim_result(
    call_execution: CallExecution,
    organization,
    payload: dict[str, Any],
) -> IngestionResult:
    """Apply a finished LiveKit result to a CallExecution.

    Idempotent for evaluation dispatch: a second call updates fields but does
    not dispatch a second evaluation (guarded by `call_metadata['eval_started']`).
    """
    if call_execution.simulation_call_type not in (
        CallExecution.SimulationCallType.VOICE,
        CallExecution.SimulationCallType.TEXT,
    ):
        raise ALKSimulateInvalidCallTypeError(
            "ALK result can only be submitted to VOICE or TEXT call executions"
        )

    _apply_payload(call_execution, payload)

    eval_dispatched = False
    if call_execution.status == CallExecution.CallStatus.COMPLETED:
        # Chat CSAT is computed synchronously by _aggregate_chat_metrics during
        # _apply_payload; only voice needs the async CSAT task.
        if (
            call_execution.simulation_call_type
            == CallExecution.SimulationCallType.VOICE
        ):
            _dispatch_csat_once(call_execution)
        eval_dispatched = _dispatch_evaluations_once(call_execution)

    try:
        notify_simulation_update(
            organization_id=str(organization.id),
            run_test_id=str(call_execution.test_execution.run_test_id),
            test_execution_id=str(call_execution.test_execution_id),
        )
    except Exception:
        logger.exception(
            "alk_sim_notify_failed", call_execution_id=str(call_execution.id)
        )

    # Roll up parent TestExecution when children reach terminal states — mirrors
    # store_chat_messages so the frontend's simulation-runs grid actually moves
    # off "pending" once a call lands.
    try:
        from simulate.tasks.chat_sim import monitor_test_execution_for_chat

        monitor_test_execution_for_chat.apply_async(
            args=(str(call_execution.test_execution_id),)
        )
    except Exception:
        logger.exception(
            "alk_sim_monitor_dispatch_failed",
            call_execution_id=str(call_execution.id),
        )

    return IngestionResult(
        call_execution_id=str(call_execution.id),
        status="ingested",
        eval_dispatched=eval_dispatched,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_expected_call_executions(
    test_execution: TestExecution,
) -> list[CallExecution]:
    run_test = test_execution.run_test
    agent_definition = run_test.agent_definition
    selected_version = test_execution.agent_version or agent_definition.latest_version
    call_type = (
        getattr(agent_definition, "agent_type", None)
        or CallExecution.SimulationCallType.VOICE
    )
    test_executor = TestExecutor(initialize_voice_service=False)
    expected_calls: list[CallExecution] = []

    for scenario_id in test_execution.scenario_ids:
        try:
            scenario = Scenarios.objects.select_related(
                "simulator_agent", "dataset", "agent_definition"
            ).get(id=scenario_id, deleted=False)
        except Scenarios.DoesNotExist:
            logger.warning(
                "livekit_batch_scenario_missing", scenario_id=str(scenario_id)
            )
            continue

        simulator_agent = _resolve_simulator_agent(scenario, run_test, selected_version)
        base_prompt = simulator_agent.prompt
        if scenario.dataset:
            for row_id in test_executor._parse_dataset_scenario(scenario):
                row_data_info = test_executor._get_row_data_and_generate_prompt(
                    row_id=row_id,
                    base_prompt=base_prompt,
                    agent_version=selected_version,
                )
                expected_calls.append(
                    _build_call_execution(
                        test_execution=test_execution,
                        scenario=scenario,
                        agent_definition=agent_definition,
                        selected_version=selected_version,
                        simulator_agent=simulator_agent,
                        base_prompt=base_prompt,
                        row_id=row_id,
                        row_data_info=row_data_info,
                        call_type=call_type,
                    )
                )
        else:
            expected_calls.append(
                _build_call_execution(
                    test_execution=test_execution,
                    scenario=scenario,
                    agent_definition=agent_definition,
                    selected_version=selected_version,
                    simulator_agent=simulator_agent,
                    base_prompt=base_prompt,
                    row_id=None,
                    row_data_info=None,
                    call_type=call_type,
                )
            )

    return expected_calls


def _call_execution_key(call_execution: CallExecution) -> tuple[str, str | None]:
    return (
        str(call_execution.scenario_id),
        str(call_execution.row_id) if call_execution.row_id else None,
    )


def _resolve_simulator_agent(scenario, run_test, selected_version) -> SimulatorAgent:
    simulator_agent = scenario.simulator_agent or run_test.simulator_agent
    if simulator_agent is not None:
        return simulator_agent
    fallback_prompt = generate_simulator_agent_prompt(agent_version=selected_version)
    simulator_agent = SimulatorAgent.objects.create(
        name=scenario.name,
        prompt=fallback_prompt,
        voice_provider="livekit",
        voice_name="alk-simulator",
        model="gpt-4",
        llm_temperature=0.7,
        initial_message="Hi!",
        max_call_duration_in_minutes=30,
        interrupt_sensitivity=0.5,
        conversation_speed=1.0,
        finished_speaking_sensitivity=0.5,
        initial_message_delay=0,
        organization=scenario.organization,
        workspace=scenario.workspace,
    )
    scenario.simulator_agent = simulator_agent
    scenario.save(update_fields=["simulator_agent"])
    return simulator_agent


def _build_call_execution(
    *,
    test_execution: TestExecution,
    scenario: Scenarios,
    agent_definition,
    selected_version,
    simulator_agent: SimulatorAgent,
    base_prompt: str,
    row_id: str | None,
    row_data_info: dict | None,
    call_type: str = CallExecution.SimulationCallType.VOICE,
) -> CallExecution:
    row_data_info = row_data_info or {}
    system_prompt = row_data_info.get("dynamic_prompt", base_prompt)
    is_text = call_type == CallExecution.SimulationCallType.TEXT
    return CallExecution(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="",
        status=CallExecution.CallStatus.PENDING,
        simulation_call_type=call_type,
        agent_version=selected_version,
        row_id=row_id,
        call_metadata={
            "call_channel": "chat" if is_text else "livekit",
            "external_runner": "alk",
            "row_id": row_id,
            "row_data": row_data_info.get("row_data", {}),
            "dataset_id": row_data_info.get("dataset_id"),
            "base_prompt": base_prompt,
            "agent_description": agent_definition.description,
            "dynamic_prompt": row_data_info.get("dynamic_prompt"),
            "language": "en",
            "initial_message": simulator_agent.initial_message,
            "voice_name": simulator_agent.voice_name,
            "conversation_speed": simulator_agent.conversation_speed,
            "interrupt_sensitivity": simulator_agent.interrupt_sensitivity,
            "finished_speaking_sensitivity": simulator_agent.finished_speaking_sensitivity,
            "max_call_duration_in_minutes": simulator_agent.max_call_duration_in_minutes,
            "initial_message_delay": simulator_agent.initial_message_delay,
            "system_prompt": system_prompt,
        },
    )


def _apply_payload(call_execution: CallExecution, payload: dict[str, Any]) -> None:
    call_execution.status = _STATUS_MAP[payload["status"]]

    started_at = payload.get("started_at")
    ended_at = payload.get("ended_at")
    if started_at and not call_execution.started_at:
        call_execution.started_at = started_at
    if ended_at:
        call_execution.ended_at = ended_at
        call_execution.completed_at = ended_at

    duration = payload.get("duration_seconds")
    if duration is not None:
        call_execution.duration_seconds = duration
    elif call_execution.started_at and call_execution.ended_at:
        call_execution.duration_seconds = int(
            (call_execution.ended_at - call_execution.started_at).total_seconds()
        )

    for field in ("ended_reason", "error_message", "call_summary"):
        value = payload.get(field)
        if value:
            if field == "ended_reason":
                value = to_canonical_ended_reason(value)
            setattr(call_execution, field, value)

    recording_url = payload.get("recording_url")
    if recording_url:
        call_execution.recording_url = recording_url
        call_execution.recording_available = True

    stereo = payload.get("stereo_recording_url")
    if stereo:
        call_execution.stereo_recording_url = stereo

    costs = payload.get("costs") or {}
    for field in _COST_FIELDS:
        if costs.get(field) is not None:
            setattr(call_execution, field, costs[field])

    provider_data = payload.get("provider_call_data")
    if provider_data is not None:
        existing = call_execution.provider_call_data or {}
        if provider_data and set(provider_data.keys()).issubset(SupportedProviders):
            existing.update(provider_data)
        else:
            existing["livekit"] = provider_data
        call_execution.provider_call_data = existing

    if payload.get("call_metadata"):
        # Caller-wins merge, so drop the backend-owned bookkeeping keys first —
        # the caller must not release a claimed row or reset cost/CSAT flags.
        incoming = {
            k: v
            for k, v in payload["call_metadata"].items()
            if k not in _RESERVED_CALL_METADATA_KEYS
        }
        merged = call_execution.call_metadata or {}
        merged.update(incoming)
        call_execution.call_metadata = merged

    segments = payload.get("transcript") or []
    is_text = (
        call_execution.simulation_call_type == CallExecution.SimulationCallType.TEXT
    )

    if is_text:
        # Chat runs render from ChatMessage rows (voice CallTranscript is ignored
        # by the chat UI), and metrics/CSAT come from the chat aggregator — the
        # same path native chat uses. See simulate/utils/chat_simulation.py.
        if (
            segments
            and not ChatMessageModel.objects.filter(
                call_execution=call_execution
            ).exists()
        ):
            _store_alk_chat_messages(call_execution, segments)
            call_execution.transcript_available = True
            call_execution.message_count = len(segments)
        call_execution.save()
        from simulate.utils.chat_simulation import _aggregate_chat_metrics

        _aggregate_chat_metrics(call_execution)
        call_execution.save()
        _deduct_alk_sim_cost_once(call_execution)
        return

    # Atomic so a failure in _apply_conversation_metrics (whose first statement
    # is a bare ee.voice import) rolls back the transcript bulk_create — without
    # it the row is left with transcripts written but status still PENDING and no
    # recovery path. Scoped to the voice mutation only: the chat branch returned
    # above, so its synchronous CSAT LLM call is never held in a transaction, and
    # _apply_conversation_metrics is pure computation (no network) so the lock
    # window stays short.
    with transaction.atomic():
        if (
            segments
            and not CallTranscript.objects.filter(
                call_execution=call_execution
            ).exists()
        ):
            CallTranscript.objects.bulk_create(
                [
                    CallTranscript(
                        call_execution=call_execution,
                        speaker_role=seg["speaker_role"],
                        content=seg["content"],
                        start_time_ms=seg.get("start_time_ms") or 0,
                        end_time_ms=seg.get("end_time_ms") or 0,
                        confidence_score=(
                            seg["confidence_score"]
                            if seg.get("confidence_score") is not None
                            else 1.0
                        ),
                    )
                    for seg in segments
                ]
            )
            call_execution.transcript_available = True

        _apply_conversation_metrics(call_execution)
        call_execution.save()
    _deduct_alk_sim_cost_once(call_execution)


_ALK_CHAT_SESSION_PREFIX = "alk-chat"


_CHAT_AGENT_SPEAKER_ROLES = {"assistant", "tool_calls", "tool_call_result", "system"}


def _store_alk_chat_messages(
    call_execution: CallExecution, segments: list[dict]
) -> int:
    """Persist a chat transcript as ChatMessage rows in the native shape.

    Groups the transcript into exchanges — a new exchange begins at each USER
    (simulator) turn, and every following agent-side segment (assistant text,
    ``tool_calls``, ``tool_call_result``) folds into that exchange's single
    ASSISTANT row. Folding keeps the agent's real tool activity visible while
    ``turn_count`` (COUNT of ASSISTANT rows) still equals the exchange count, the
    native chat semantic. The USER and ASSISTANT rows of one exchange **share a
    created_at**: that is what the chat UI groups a turn by — staggering per row
    made every message render as interrupted.

    Mirrors ``simulate/tasks/chat_sim.py`` store_chat_messages (agent → ASSISTANT,
    simulator → USER). ``latency_ms`` is carried from the last agent segment that
    supplied one; transcripts without timing leave it null, as native does.
    """
    from simulate.utils.chat_simulation import estimate_tokens_text

    run_test = call_execution.test_execution.run_test
    base = call_execution.started_at or timezone.now()
    session_id = f"{_ALK_CHAT_SESSION_PREFIX}-{call_execution.id}"

    def _seg_latency(seg: dict) -> int | None:
        value = seg.get("latency_ms")
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

    exchanges: list[dict] = []
    current: dict | None = None
    for seg in segments:
        role = (seg.get("speaker_role") or "").lower()
        if not (seg.get("content") or "").strip():
            continue
        if role in {"user", "customer"}:
            current = {"user": seg, "agent": []}
            exchanges.append(current)
        elif role in _CHAT_AGENT_SPEAKER_ROLES:
            if current is None:
                current = {"user": None, "agent": []}
                exchanges.append(current)
            current["agent"].append(seg)

    def _row(role, role_str, segs, created_at, latency_ms=None) -> ChatMessageModel:
        texts = [seg.get("content") or "" for seg in segs]
        content_items: list[dict] = []
        for seg in segs:
            item = {"role": role_str, "content": seg.get("content") or ""}
            if seg.get("tool_calls"):
                item["tool_calls"] = seg["tool_calls"]
            speaker = (seg.get("speaker_role") or "").lower()
            if speaker in {"tool_calls", "tool_call_result"}:
                item["kind"] = speaker
            content_items.append(item)
        joined = "\n".join(t for t in texts if t)
        return ChatMessageModel(
            id=uuid.uuid4(),
            role=role,
            call_execution=call_execution,
            messages=texts,
            content=content_items,
            session_id=session_id,
            created_at=created_at,
            organization=run_test.organization,
            workspace=run_test.workspace,
            tokens=estimate_tokens_text(joined),
            latency_ms=latency_ms,
        )

    rows: list[ChatMessageModel] = []
    for index, exchange in enumerate(exchanges):
        created_at = base + timedelta(seconds=index)
        if exchange["user"] is not None:
            rows.append(_row(ChatRole.USER, "user", [exchange["user"]], created_at))
        agent_segs = exchange["agent"]
        if agent_segs:
            latency = next(
                (
                    lat
                    for lat in (_seg_latency(s) for s in reversed(agent_segs))
                    if lat is not None
                ),
                None,
            )
            rows.append(
                _row(ChatRole.ASSISTANT, "assistant", agent_segs, created_at, latency)
            )

    if rows:
        ChatMessageModel.objects.bulk_create(rows)
    return len(rows)


def _deduct_alk_sim_cost_once(call_execution: CallExecution) -> None:
    """Charge an ALK-ingested sim run the same way native does
    (TestExecutor._deduct_call_cost: text_call by turns/tokens, voice_call by
    duration, each with its TEXT_CALL/VOICE_CALL usage event). Guarded so a
    re-ingest of the same result does not double-charge; a voice call with no
    billable duration is skipped, mirroring native deduct_call_cost."""
    meta = call_execution.call_metadata or {}
    if meta.get("cost_deducted"):
        return
    if (
        call_execution.simulation_call_type == CallExecution.SimulationCallType.VOICE
        and not call_execution.duration_seconds
    ):
        return
    from simulate.services.test_executor import TestExecutor

    try:
        TestExecutor._deduct_call_cost(call_execution)
    except Exception:
        logger.exception(
            "alk_sim_cost_deduct_failed", call_execution_id=str(call_execution.id)
        )
        return
    meta["cost_deducted"] = True
    call_execution.call_metadata = meta
    call_execution.save(update_fields=["call_metadata"])


def _apply_conversation_metrics(call_execution: CallExecution) -> None:
    """Compute + persist conversation metrics from CallTranscript.

    Mirrors ee/voice/temporal/activities/voice_large.py:
    - runs ConversationMetricsCalculator on a NormalizedTranscriptData
      built from CallTranscript rows
    - writes individual CallExecution columns + conversation_metrics_data
    """
    from ee.voice.services.conversation_metrics import (
        ConversationMetricsCalculator,
    )
    from ee.voice.services.types.voice import (
        NormalizedTranscriptData,
        TranscriptMessage,
    )

    transcripts = list(
        CallTranscript.objects.filter(call_execution=call_execution).order_by(
            "start_time_ms"
        )
    )
    if not transcripts:
        return

    messages: list[TranscriptMessage] = []
    for t in transcripts:
        role = _TRANSCRIPT_ROLE_TO_METRIC_ROLE.get(t.speaker_role)
        if role is None:
            continue
        # A trailing target turn captured from LiveKit's text stream can be
        # retained for display without having audio timestamps. Its synthetic
        # zero-duration position preserves ordering, but must not fabricate an
        # agent-latency sample.
        if role == "bot" and (t.end_time_ms or 0) <= (t.start_time_ms or 0):
            continue
        start_s = (t.start_time_ms or 0) / 1000.0
        end_s = (t.end_time_ms or 0) / 1000.0 if t.end_time_ms else None
        messages.append(
            TranscriptMessage(
                role=role,
                content=t.content or "",
                time=start_s,
                end_time=end_s,
                duration=(end_s - start_s) if end_s is not None else None,
            )
        )
    if not messages:
        return

    is_outbound = (call_execution.call_metadata or {}).get(
        "call_direction"
    ) == "outbound"
    normalized = NormalizedTranscriptData(messages=messages)
    calculator = ConversationMetricsCalculator(
        voice_service_provider=ProviderChoices.LIVEKIT
    )
    metrics = calculator.calculate_metrics_from_normalized(
        normalized, is_outbound=is_outbound
    )

    call_execution.avg_agent_latency_ms = metrics.avg_agent_latency_ms
    call_execution.user_interruption_count = metrics.user_interruption_count
    call_execution.user_interruption_rate = metrics.user_interruption_rate
    call_execution.ai_interruption_count = metrics.ai_interruption_count
    call_execution.ai_interruption_rate = metrics.ai_interruption_rate
    call_execution.user_wpm = metrics.user_wpm
    call_execution.bot_wpm = metrics.bot_wpm
    call_execution.talk_ratio = metrics.talk_ratio
    call_execution.avg_stop_time_after_interruption_ms = (
        metrics.avg_stop_time_after_interruption_ms
    )

    detailed_data = dict(metrics.detailed_data or {})
    full_metric_roles = [
        role
        for transcript in transcripts
        if (role := _TRANSCRIPT_ROLE_TO_METRIC_ROLE.get(transcript.speaker_role))
        is not None
    ]
    full_user_count = full_metric_roles.count("user")
    full_bot_count = full_metric_roles.count("bot")
    detailed_data.update(
        {
            "message_count": len(full_metric_roles),
            "turn_count": full_bot_count,
            "user_message_count": full_user_count,
            "bot_message_count": full_bot_count,
        }
    )

    # Preserve csat_score across recomputes — CSAT is written by a later task
    # into conversation_metrics_data; a second (idempotent) ingest must not
    # wipe it when it rebuilds the metrics blob.
    existing_csat = (call_execution.conversation_metrics_data or {}).get("csat_score")
    if existing_csat is not None:
        detailed_data["csat_score"] = existing_csat

    # Fold in the target agent's LLM token usage (provider-reported, stored on
    # provider_call_data by ingestion) the same way voice_large.py does, so the
    # frontend's token cells and the KPI aggregate light up.
    token_usage = _extract_llm_token_usage(call_execution.provider_call_data)
    if token_usage is not None:
        if token_usage.get("input_tokens") is not None:
            detailed_data["input_tokens"] = token_usage["input_tokens"]
        if token_usage.get("output_tokens") is not None:
            detailed_data["output_tokens"] = token_usage["output_tokens"]
        if token_usage.get("total_tokens") is not None:
            detailed_data["total_tokens"] = token_usage["total_tokens"]

    if call_execution.message_count is None:
        call_execution.message_count = len(full_metric_roles)
    call_execution.conversation_metrics_data = detailed_data

    # Backfill duration from transcript span when the SDK payload carried no
    # explicit duration and no start/end timestamps — the last segment's
    # end offset is the best observed call length.
    if call_execution.duration_seconds is None:
        last_end_ms = max(
            (t.end_time_ms or 0 for t in transcripts),
            default=0,
        )
        if last_end_ms > 0:
            call_execution.duration_seconds = int(round(last_end_ms / 1000.0))


def _extract_llm_token_usage(
    provider_call_data: dict | None,
) -> dict[str, int] | None:
    """Return normalized {input_tokens, output_tokens, total_tokens} usage.

    Mirrors ee/voice get_normalized_transcript_data: reads the normalized
    ``usage.llm`` bucket the SDK writes under each provider key. Providers that
    only report a total (e.g. Retell) yield total_tokens without a split.
    Returns None when no LLM usage was reported.
    """
    if not isinstance(provider_call_data, dict):
        return None
    for provider_data in provider_call_data.values():
        if not isinstance(provider_data, dict):
            continue
        usage = provider_data.get("usage")
        if not isinstance(usage, dict):
            continue
        llm_usage = usage.get("llm")
        if not isinstance(llm_usage, dict):
            continue

        prompt = _coerce_token(
            llm_usage.get("prompt_tokens", llm_usage.get("promptTokens"))
        )
        completion = _coerce_token(
            llm_usage.get("completion_tokens", llm_usage.get("completionTokens"))
        )
        total = _coerce_token(
            llm_usage.get("total_tokens", llm_usage.get("totalTokens"))
        )

        result: dict[str, int] = {}
        if prompt is not None:
            result["input_tokens"] = prompt
        if completion is not None:
            result["output_tokens"] = completion
        if total is not None:
            result["total_tokens"] = total
        elif prompt is not None or completion is not None:
            result["total_tokens"] = (prompt or 0) + (completion or 0)

        if any(v for v in result.values()):
            return result
    return None


def _coerce_token(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dispatch_csat_once(call_execution: CallExecution) -> None:
    call_metadata = call_execution.call_metadata or {}
    if call_metadata.get("csat_dispatched"):
        return
    call_metadata["csat_dispatched"] = True
    call_execution.call_metadata = call_metadata
    call_execution.save(update_fields=["call_metadata"])
    try:
        from simulate.tasks.alk_sim import calculate_alk_voice_csat_score

        calculate_alk_voice_csat_score.apply_async(args=(str(call_execution.id),))
    except Exception as dispatch_error:
        logger.exception(
            "alk_csat_dispatch_failed",
            call_execution_id=str(call_execution.id),
        )
        call_metadata["csat_dispatched"] = False
        call_metadata["csat_dispatch_failed"] = str(dispatch_error)
        call_execution.call_metadata = call_metadata
        call_execution.save(update_fields=["call_metadata"])


def _dispatch_evaluations_once(call_execution: CallExecution) -> bool:
    call_metadata = call_execution.call_metadata or {}
    if call_metadata.get("eval_started"):
        return False
    call_metadata["eval_started"] = True
    call_execution.call_metadata = call_metadata
    call_execution.save(update_fields=["call_metadata"])
    try:
        _run_simulate_evaluations_task.apply_async(args=(str(call_execution.id),))
        return True
    except Exception as dispatch_error:
        logger.exception(
            "livekit_eval_dispatch_failed",
            call_execution_id=str(call_execution.id),
        )
        call_metadata["eval_started"] = False
        call_metadata["eval_dispatch_failed"] = str(dispatch_error)
        call_execution.call_metadata = call_metadata
        call_execution.save(update_fields=["call_metadata"])
        return False
