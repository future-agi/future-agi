"""Tests for RetellService — Retell as a customer-provider engine.

Covers the engine methods (trigger / get_call status poll / hangup /
persistence / cost / normalized transcript), its registration in
ENGINE_REGISTRY, and the simulator-only methods that must raise.

Two Retell-specific invariants get their own tests because they are the
failure modes that would silently corrupt a call's data:

  1. Per-word timings are in SECONDS in Retell's payload but the conversation
     metrics calculator expects MILLISECONDS (it only sec->ms-converts
     LiveKit), so every timing must be multiplied by 1000.
  2. ``end_call`` must hit ``/v2/stop-call``, never ``call.delete`` — the
     latter destroys the call record and the transcript we are about to fetch.
"""

from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import sync_to_async

from ee.voice.services.retell_service import RetellService
from ee.voice.services.types.voice import EndCallInput, GetCallInput
from ee.voice.services.voice_service_manager import VoiceServiceManager
from simulate.semantics import CallExecutionStatus
from tracer.models.observability_provider import ProviderChoices

_KEY = "org_retell_secret_key"


def _service(mock_client):
    """RetellService with the SDK client pre-injected (no network, no key check)."""
    service = RetellService(api_key=_KEY)
    service._retell = mock_client
    return service


# ---------------------------------------------------------------------------
# Registry — Retell dispatches through VoiceServiceManager like any provider
# ---------------------------------------------------------------------------
def test_registry_maps_retell_to_retell_service():
    from ee.voice.services.vapi_service import VapiService

    assert VoiceServiceManager.ENGINE_REGISTRY[ProviderChoices.RETELL] is RetellService
    # Sanity: the default/system provider is unchanged.
    assert VoiceServiceManager.ENGINE_REGISTRY[ProviderChoices.VAPI] is VapiService


def test_provider_key_matches_enum():
    assert RetellService.PROVIDER_KEY == ProviderChoices.RETELL.value == "retell"


def test_vsm_instantiates_retell_engine():
    vsm = VoiceServiceManager(
        api_key=_KEY, system_voice_provider=ProviderChoices.RETELL
    )
    assert isinstance(vsm.engine, RetellService)


def test_engine_constructs_without_an_api_key():
    """The manager builds engines with an empty key for registry/system-side
    dispatch; construction must never raise (the SDK client is lazy)."""
    assert VoiceServiceManager(system_voice_provider=ProviderChoices.RETELL) is not None


# ---------------------------------------------------------------------------
# Outbound SIP gate — prepare_call must no longer reject a Retell customer
# ---------------------------------------------------------------------------
def test_retell_is_an_allowed_outbound_sip_provider():
    """Guards the gate in voice_small.prepare_call that used to fail fast with
    'Outbound simulation is not supported for customer provider retell'."""
    import inspect

    from ee.voice.temporal.activities import voice_small

    source = inspect.getsource(voice_small)
    gate = source.split("outbound_sip_providers = {", 1)[1].split("}", 1)[0]
    assert "RETELL" in gate


# ---------------------------------------------------------------------------
# Speaker roles — without a Retell branch every outbound turn is mislabelled
# ---------------------------------------------------------------------------
def test_speaker_roles_detect_and_map_retell():
    from simulate.utils.speaker_roles import SpeakerRoleResolver

    assert (
        SpeakerRoleResolver.detect_provider({"retell": {"call_id": "x"}})
        is ProviderChoices.RETELL
    )
    outbound = SpeakerRoleResolver._get_map(
        provider=ProviderChoices.RETELL, is_outbound=True
    )
    inbound = SpeakerRoleResolver._get_map(
        provider=ProviderChoices.RETELL, is_outbound=False
    )
    # Outbound: the customer's Retell agent is the tested agent, we are the user.
    assert outbound["agent"] == "tested_agent"
    assert outbound["user"] == "simulator"
    assert inbound["agent"] == "simulator"


# ---------------------------------------------------------------------------
# Simulator-only blueprint methods must fail closed and loud for a customer
# engine — Retell never runs the simulator or the client-matching enrichment.
# Note this list has seven entries, not Bland's eight: end_call IS supported.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call_method",
    [
        lambda s: s.initiate_inbound_call(MagicMock()),
        lambda s: s.initiate_outbound_call(MagicMock()),
        lambda s: s.get_recording_urls({}),
        lambda s: s.persist_audio_to_s3(MagicMock()),
        lambda s: s.find_client_call(MagicMock()),
        lambda s: s.get_customer_metrics(MagicMock()),
        lambda s: s.iter_call_logs("http://x", True),
    ],
)
def test_simulator_only_methods_raise_not_implemented(call_method):
    with pytest.raises(NotImplementedError):
        call_method(RetellService(api_key=_KEY))


# ---------------------------------------------------------------------------
# Trigger — create_outbound_call
# ---------------------------------------------------------------------------
def test_create_outbound_call_returns_id_and_passes_from_number():
    """Unlike Bland, Retell REQUIRES from_number (it must be a number the
    customer owns), so it is passed through rather than omitted."""
    mock_client = MagicMock()
    mock_client.call.create_phone_call.return_value.model_dump.return_value = {
        "call_id": "retell-call-1",
        "call_status": "registered",
    }

    result = _service(mock_client).create_outbound_call(
        assistant_id="agent_123",
        from_phone_number="+14157774444",
        to_phone_number="+12137774445",
        metadata={"call_id": "exec-1"},
    )

    assert result["id"] == "retell-call-1"
    kwargs = mock_client.call.create_phone_call.call_args.kwargs
    assert kwargs["from_number"] == "+14157774444"
    assert kwargs["to_number"] == "+12137774445"
    # A one-time override, not a rebinding of the agent to the number.
    assert kwargs["override_agent_id"] == "agent_123"
    assert kwargs["metadata"] == {"call_id": "exec-1"}


def test_create_outbound_call_raises_when_no_call_id():
    mock_client = MagicMock()
    mock_client.call.create_phone_call.return_value.model_dump.return_value = {}

    with pytest.raises(RuntimeError, match="did not return a call_id"):
        _service(mock_client).create_outbound_call(
            assistant_id="agent_123",
            from_phone_number="+14157774444",
            to_phone_number="+12137774445",
        )


# ---------------------------------------------------------------------------
# Hangup — end_call
# ---------------------------------------------------------------------------
def test_end_call_posts_to_stop_call_and_never_deletes():
    mock_client = MagicMock()
    service = _service(mock_client)

    assert (
        service.end_call(EndCallInput(provider_call_payload={"call_id": "retell-1"}))
        is True
    )

    assert mock_client.post.call_args.args[0] == "/v2/stop-call/retell-1"
    # delete-call destroys the record and its transcript — it is not a hangup.
    mock_client.call.delete.assert_not_called()


def test_end_call_raises_without_a_call_id():
    with pytest.raises(ValueError, match="requires a call_id"):
        _service(MagicMock()).end_call(EndCallInput(provider_call_payload={}))


# ---------------------------------------------------------------------------
# Status mapping — drives the monitor's terminal-state detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw_status,call_data_stored,expected",
    [
        # `ended` before the data is stored is the monitor's terminal signal.
        ("ended", False, CallExecutionStatus.ANALYZING),
        ("ended", True, CallExecutionStatus.COMPLETED),
        ("error", False, CallExecutionStatus.FAILED),
        ("not_connected", False, CallExecutionStatus.FAILED),
        ("ongoing", False, CallExecutionStatus.ONGOING),
        ("registered", False, CallExecutionStatus.REGISTERED),
        ("", False, CallExecutionStatus.PENDING),
    ],
)
def test_status_mapping(raw_status, call_data_stored, expected):
    from ee.voice.services.retell_service import _map_retell_status

    assert _map_retell_status(raw_status, call_data_stored=call_data_stored) == expected


# ---------------------------------------------------------------------------
# Normalization — get_call / normalize_call_data
# ---------------------------------------------------------------------------
_RETELL_PAYLOAD = {
    "call_id": "retell-call-1",
    "call_type": "phone_call",
    "agent_id": "agent_123",
    "call_status": "ended",
    "direction": "outbound",
    # Retell dials FROM the customer's number TO our simulator's number.
    "from_number": "+18885550111",
    "to_number": "+16505550100",
    "start_timestamp": 1753005600000,
    "end_timestamp": 1753005690000,
    "duration_ms": 90000,
    "disconnection_reason": "user_hangup",
    "public_log_url": "https://retell.example/log.txt",
    "recording_url": "https://retell.example/rec.mp3",
    "recording_multi_channel_url": "https://retell.example/rec-stereo.mp3",
    "call_analysis": {"call_summary": "Customer asked about opening hours."},
    "call_cost": {
        "combined_cost": 42.0,
        "product_costs": [
            {"product": "elevenlabs_tts", "cost": 12.0},
            {"product": "gpt_4o", "cost": 20.0},
            {"product": "twilio_telephony", "cost": 10.0},
        ],
    },
    "llm_token_usage": {"values": [100, 150], "average": 125, "num_requests": 2},
    "transcript_with_tool_calls": [
        {
            "role": "agent",
            "content": "Hi, how can I help?",
            "words": [
                {"word": "Hi,", "start": 1.5, "end": 1.8},
                {"word": "how", "start": 1.8, "end": 2.0},
            ],
        },
        {"role": "tool_call_invocation", "name": "lookup_hours", "arguments": "{}"},
        {
            "role": "user",
            "content": "What are your hours?",
            "words": [
                {"word": "What", "start": 3.0, "end": 3.2},
                {"word": "hours?", "start": 3.2, "end": 3.4},
            ],
        },
    ],
}


def test_normalize_call_data_maps_phone_numbers_from_the_simulator_perspective():
    """`to_number` is OUR number (system), `from_number` is THEIRS (customer)."""
    fagi = RetellService(api_key=_KEY).normalize_call_data(
        _RETELL_PAYLOAD, call_data_stored=False
    )

    assert fagi.call_id == "retell-call-1"
    assert fagi.assistant_id == "agent_123"
    assert fagi.system_phone_number == "+16505550100"
    assert fagi.customer_phone_number == "+18885550111"
    assert fagi.status == CallExecutionStatus.ANALYZING
    assert fagi.ended_reason == "user_hangup"
    assert fagi.summary == "Customer asked about opening hours."
    assert fagi.recording_url == "https://retell.example/rec.mp3"
    assert fagi.log_url == "https://retell.example/log.txt"
    # duration_ms -> seconds; cost is reported in cents by Retell.
    assert fagi.duration_seconds == 90.0
    assert fagi.cost == pytest.approx(0.42)
    assert fagi.raw_log == {"retell": _RETELL_PAYLOAD}
    assert fagi.transcript_available is True
    assert fagi.recording_available is True


def test_get_call_fetches_then_normalizes():
    mock_client = MagicMock()
    mock_client.call.retrieve.return_value.model_dump.return_value = _RETELL_PAYLOAD

    fagi = _service(mock_client).get_call(
        GetCallInput(call_id="retell-call-1", call_data_stored=True)
    )

    mock_client.call.retrieve.assert_called_once_with("retell-call-1")
    assert fagi.status == CallExecutionStatus.COMPLETED


def test_validate_api_key_is_false_on_error():
    mock_client = MagicMock()
    mock_client.call.list.side_effect = Exception("401")
    assert _service(mock_client).validate_api_key() is False

    ok_client = MagicMock()
    assert _service(ok_client).validate_api_key() is True


# ---------------------------------------------------------------------------
# Fixtures for the DB-backed persistence / recording / cost tests
# ---------------------------------------------------------------------------
@pytest.fixture
def agent_definition(db, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition

    return AgentDefinition.objects.create(
        agent_name="Retell Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+18885550111",
        inbound=False,
        description="outbound retell agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    from simulate.models.simulator_agent import SimulatorAgent

    return SimulatorAgent.objects.create(
        name="Sim",
        prompt="You are a simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, user, agent_definition):
    from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
    from simulate.models import Scenarios

    dataset = Dataset.no_workspace_objects.create(
        name="DS",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="situation")
    return Scenarios.objects.create(
        name="Scn",
        description="d",
        source="s",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def call_execution(
    db, organization, workspace, agent_definition, simulator_agent, scenario
):
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution

    run_test = RunTest.objects.create(
        name="RT",
        description="d",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    te = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )
    return CallExecution.objects.create(
        test_execution=te,
        scenario=scenario,
        phone_number="+16505550100",
        status=CallExecution.CallStatus.ANALYZING,
        call_metadata={"call_direction": "outbound"},
    )


# ---------------------------------------------------------------------------
# Normalized transcript — the seconds->milliseconds conversion is the whole
# point of this method. Getting it wrong silently corrupts every WPM, latency
# and talk-ratio metric for Retell calls.
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_normalized_transcript_converts_seconds_to_milliseconds(call_execution):
    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"retell": _RETELL_PAYLOAD}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    data = await RetellService(api_key=_KEY).get_normalized_transcript_data(
        str(call_execution.id)
    )

    # The tool_call_invocation row is dropped: it is not a speaker turn.
    assert [m.role for m in data.messages] == ["assistant", "user"]
    assert data.messages[0].time == 1500.0
    assert data.messages[0].end_time == 2000.0
    assert data.messages[0].duration == 500.0
    assert data.messages[1].time == 3000.0
    # Retell reports no input/output split, so the total lands on prompt_tokens.
    assert data.token_usage == {"llm": {"prompt_tokens": 250, "completion_tokens": 0}}


@pytest.mark.django_db(transaction=True)
async def test_normalized_transcript_row_without_timings_inherits_previous_time(
    call_execution,
):
    payload = {
        "transcript_with_tool_calls": [
            {
                "role": "agent",
                "content": "Hi",
                "words": [{"word": "Hi", "start": 2.0, "end": 2.5}],
            },
            {"role": "user", "content": "Hello"},  # no words
        ]
    }

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"retell": payload}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    data = await RetellService(api_key=_KEY).get_normalized_transcript_data(
        str(call_execution.id)
    )

    assert data.messages[1].time == 2000.0
    assert data.messages[1].end_time is None
    assert data.token_usage == {}


# ---------------------------------------------------------------------------
# Persistence — fetch_and_store_call_data (real DB)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_fetch_and_store_persists_transcript_summary_and_provider_data(
    call_execution,
):
    from simulate.models.test_execution import CallTranscript

    with patch.object(RetellService, "_get_call", return_value=_RETELL_PAYLOAD):
        count, has_agent, has_customer = await RetellService(
            api_key=_KEY
        ).fetch_and_store_call_data(
            call_execution_id=str(call_execution.id),
            provider_call_id="retell-call-1",
            status="analyzing",
        )

    # The tool_call row is stored as UNKNOWN but still counted as a message.
    assert (count, has_agent, has_customer) == (3, True, True)

    @sync_to_async
    def _read():
        call = call_execution.__class__.objects.get(id=call_execution.id)
        rows = list(
            CallTranscript.objects.filter(call_execution=call).order_by("start_time_ms")
        )
        return call, rows

    call, rows = await _read()
    assert call.provider_call_data["retell"] == _RETELL_PAYLOAD
    assert call.call_summary == "Customer asked about opening hours."
    assert call.service_provider_call_id == "retell-call-1"
    assert call.assistant_id == "agent_123"
    assert call.customer_number == "+18885550111"
    assert call.ended_reason == "user_hangup"
    # Duration must be persisted (billing gates on it): 90000ms -> 90s.
    assert call.duration_seconds == 90
    assert call.message_count == 3
    assert call.transcript_available is True
    assert [(r.speaker_role, r.content) for r in rows if r.content] == [
        (CallTranscript.SpeakerRole.ASSISTANT, "Hi, how can I help?"),
        (CallTranscript.SpeakerRole.USER, "What are your hours?"),
    ]
    # Real per-word timings, not Bland's ordinal idx*1000 fallback.
    speaker_rows = [r for r in rows if r.content]
    assert [(r.start_time_ms, r.end_time_ms) for r in speaker_rows] == [
        (1500, 2000),
        (3000, 3400),
    ]


@pytest.mark.django_db(transaction=True)
async def test_fetch_and_store_fails_open_when_retell_is_unreachable(call_execution):
    """A provider blip must still mark the call, not wedge the workflow."""
    with patch.object(RetellService, "_get_call", side_effect=Exception("boom")):
        count, has_agent, has_customer = await RetellService(
            api_key=_KEY
        ).fetch_and_store_call_data(
            call_execution_id=str(call_execution.id),
            provider_call_id="retell-call-1",
            status="failed",
            end_reason="provider_error",
        )

    assert (count, has_agent, has_customer) == (0, False, False)

    @sync_to_async
    def _read():
        return call_execution.__class__.objects.get(id=call_execution.id)

    call = await _read()
    assert call.status == "failed"
    assert call.ended_reason == "provider_error"
    assert call.transcript_available is False


# ---------------------------------------------------------------------------
# Costs — Retell reports CENTS; the blueprint contract is dollars.
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_extract_costs_converts_cents_to_dollars_and_splits_by_product(
    call_execution,
):
    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"retell": _RETELL_PAYLOAD}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    costs = await RetellService(api_key=_KEY).extract_costs(str(call_execution.id))

    assert costs.total == pytest.approx(0.42)
    assert costs.tts == pytest.approx(0.12)
    assert costs.llm == pytest.approx(0.20)
    assert costs.transport == pytest.approx(0.10)
    assert costs.stt == 0.0


@pytest.mark.django_db(transaction=True)
async def test_extract_costs_keeps_total_when_products_are_unrecognised(
    call_execution,
):
    """An unmapped product name must not corrupt the total."""
    payload = {
        "call_cost": {
            "combined_cost": 55.0,
            "product_costs": [{"product": "some_new_retell_product", "cost": 55.0}],
        }
    }

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"retell": payload}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    costs = await RetellService(api_key=_KEY).extract_costs(str(call_execution.id))

    assert costs.total == pytest.approx(0.55)
    assert (costs.stt, costs.llm, costs.tts, costs.transport) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Recordings — both artifacts rehosted, each metered independently
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_rehosts_both_artifacts(call_execution):
    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"retell": _RETELL_PAYLOAD}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    async def _fake_convert(call_id, url, url_type, **kwargs):
        return f"https://s3.example/{url_type}.mp3", 1024

    with patch(
        "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
        side_effect=_fake_convert,
    ):
        result = await RetellService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    assert result.recording_url == "https://s3.example/recording.mp3"
    assert result.stereo_recording_url == "https://s3.example/stereo_recording.mp3"


@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_isolates_a_failing_artifact(
    call_execution,
):
    """One artifact failing to rehost must not drop the other."""

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"retell": _RETELL_PAYLOAD}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    async def _flaky_convert(call_id, url, url_type, **kwargs):
        if url_type == "recording":
            raise RuntimeError("download failed")
        return "https://s3.example/stereo_recording.mp3", 2048

    with patch(
        "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
        side_effect=_flaky_convert,
    ):
        result = await RetellService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    assert result.recording_url is None
    assert result.stereo_recording_url == "https://s3.example/stereo_recording.mp3"
