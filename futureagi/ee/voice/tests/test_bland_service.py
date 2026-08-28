"""Tests for BlandService — Bland.ai as a customer-provider engine.

Covers the engine methods (trigger / get_call status poll / persistence / cost /
normalized transcript), its registration in ENGINE_REGISTRY, the
simulator-only methods that must raise, and the two dispatch invariants that
run through real activities:

  1. Monitor routing — an OUTBOUND Bland test must poll the Bland engine, never
     VAPI (a VAPI poll of a Bland call-id throws every iteration and would spin
     the monitor to its 4-hour cap); an INBOUND Bland test flows through the
     VAPI simulator and must poll VAPI.
  2. Conversation-metrics dispatch — the metrics activity must read Bland's
     transcript via the Bland engine when the customer holds the call data, so
     conversation metrics are no longer silently empty for Bland outbound (the
     regression the standalone-client design shipped).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import sync_to_async

from ee.voice.services.bland_service import BlandService
from ee.voice.services.types.voice import GetCallInput
from ee.voice.services.voice_service_manager import VoiceServiceManager
from simulate.semantics import CallExecutionStatus
from tracer.models.observability_provider import ProviderChoices

_KEY = "org_bland_secret_key"
_URL = "https://api.bland.ai/v1/calls"


# ---------------------------------------------------------------------------
# Registry — Bland dispatches through VoiceServiceManager like any provider
# ---------------------------------------------------------------------------
def test_registry_maps_bland_to_bland_service():
    from ee.voice.services.vapi_service import VapiService

    assert VoiceServiceManager.ENGINE_REGISTRY[ProviderChoices.BLAND] is BlandService
    # Sanity: the default/system provider is unchanged.
    assert VoiceServiceManager.ENGINE_REGISTRY[ProviderChoices.VAPI] is VapiService


# ---------------------------------------------------------------------------
# Simulator-only blueprint methods must fail closed and loud for a customer
# engine — Bland never runs the simulator or the client-matching enrichment.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call_method",
    [
        lambda s: s.initiate_inbound_call(MagicMock()),
        lambda s: s.initiate_outbound_call(MagicMock()),
        lambda s: s.end_call(MagicMock()),
        lambda s: s.get_recording_urls({}),
        lambda s: s.persist_audio_to_s3(MagicMock()),
        lambda s: s.find_client_call(MagicMock()),
        lambda s: s.get_customer_metrics(MagicMock()),
        lambda s: s.iter_call_logs("http://x", True),
    ],
)
def test_simulator_only_methods_raise_not_implemented(call_method):
    with pytest.raises(NotImplementedError):
        call_method(BlandService(api_key=_KEY))


# ---------------------------------------------------------------------------
# Trigger — create_outbound_call
# ---------------------------------------------------------------------------
@patch("ee.voice.services.bland_service.requests.post")
def test_create_outbound_call_returns_id_and_uses_raw_auth(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"status": "success", "call_id": "bland-call-1"},
    )

    result = BlandService(api_key=_KEY).create_outbound_call(
        assistant_id="pw-1",
        from_phone_number="+18885550111",
        to_phone_number="+16505550100",
        metadata={"call_id": "exec-1"},
    )

    # The engine returns a payload with an "id" key so the initiate activity
    # reads provider_call_id uniformly across providers.
    assert result["id"] == "bland-call-1"
    kwargs = mock_post.call_args.kwargs
    assert kwargs["headers"] == {"authorization": _KEY}  # RAW key, doubles as non-leak
    body = kwargs["json"]
    assert body["pathway_id"] == "pw-1"  # assistant_id maps to the Bland pathway
    assert body["phone_number"] == "+16505550100"
    assert body["record"] is True
    assert body["metadata"] == {"call_id": "exec-1"}
    # `from` is NEVER sent — even when a from number is supplied — because it is
    # the simulator's number, not one purchased for outbound in the customer's
    # Bland account; sending it makes Bland accept the trigger then silently
    # fail to place the call. This locks that deliberate behavior.
    assert "from" not in body


@patch("ee.voice.services.bland_service.requests.post")
def test_create_outbound_call_raises_when_not_success(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"status": "error", "message": "invalid pathway"},
    )
    with pytest.raises(RuntimeError, match="not accepted"):
        BlandService(api_key=_KEY).create_outbound_call(
            assistant_id="bad", to_phone_number="+16505550100"
        )


@patch("ee.voice.services.bland_service.requests.post")
def test_create_outbound_call_surfaces_bland_error_body(mock_post):
    # A 4xx must surface Bland's message (e.g. `from` not purchased), not hide
    # it behind a bare status code.
    mock_post.return_value = MagicMock(
        status_code=400,
        text='{"errors":["from number is not owned by your account"]}',
    )
    with pytest.raises(RuntimeError, match="not owned by your account"):
        BlandService(api_key=_KEY).create_outbound_call(
            assistant_id="pw-1", to_phone_number="+16505550100"
        )


# ---------------------------------------------------------------------------
# GET path — _get_call. Every status poll and result fetch goes through here,
# so its wire contract (endpoint, RAW auth header, raise_for_status) is pinned
# directly: a wrong endpoint, a stray "Bearer " prefix, or a dropped
# raise_for_status would otherwise ship green because higher-level tests mock
# _get_call itself.
# ---------------------------------------------------------------------------
@patch("ee.voice.services.bland_service.requests.get")
def test_get_call_hits_scoped_endpoint_with_raw_auth(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200, json=lambda: {"status": "completed"}
    )
    raw = BlandService(api_key=_KEY)._get_call("bland-call-1")

    assert raw == {"status": "completed"}
    kwargs = mock_get.call_args.kwargs
    args = mock_get.call_args.args
    assert (args and args[0] == f"{_URL}/bland-call-1") or kwargs.get(
        "url"
    ) == f"{_URL}/bland-call-1"
    assert kwargs["headers"] == {"authorization": _KEY}
    mock_get.return_value.raise_for_status.assert_called_once()


@patch("ee.voice.services.bland_service.requests.get")
def test_get_call_propagates_http_error(mock_get):
    import requests

    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = requests.HTTPError("500")
    mock_get.return_value = resp
    with pytest.raises(requests.HTTPError):
        BlandService(api_key=_KEY)._get_call("bland-call-1")


# ---------------------------------------------------------------------------
# Status poll — get_call_async → FAGICallData (the monitor reads this now)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw_status,expected",
    [
        ("completed", CallExecutionStatus.ANALYZING),  # terminal → go analyze
        ("failed", CallExecutionStatus.FAILED),
        ("no-answer", CallExecutionStatus.FAILED),
        ("in-progress", CallExecutionStatus.ONGOING),
        ("queued", CallExecutionStatus.REGISTERED),
    ],
)
async def test_get_call_async_maps_bland_status(raw_status, expected):
    with patch.object(
        BlandService,
        "_get_call",
        return_value={"status": raw_status, "call_length": 0.5},
    ):
        result = await BlandService(api_key=_KEY).get_call_async(
            GetCallInput(call_id="bland-call-1", call_data_stored=False)
        )
    assert result.status == expected


async def test_get_call_async_reads_duration_from_call_length_minutes():
    with patch.object(
        BlandService,
        "_get_call",
        return_value={"status": "completed", "call_length": 1.5},
    ):
        result = await BlandService(api_key=_KEY).get_call_async(
            GetCallInput(call_id="bland-call-1", call_data_stored=False)
        )
    # 1.5 minutes → 90 seconds.
    assert result.duration_seconds == 90


# ---------------------------------------------------------------------------
# Monitor routing invariant (through the real activity)
# ---------------------------------------------------------------------------
class _NoopHeartbeater:
    def __init__(self, *args, **kwargs):
        self.details = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_monitor_outbound_bland_polls_bland_engine_not_vapi():
    from simulate.temporal.types.activities import MonitorCallInput

    from ee.voice.services.vapi_service import VapiService
    from ee.voice.temporal.activities import voice_large

    monitor_input = MonitorCallInput(
        call_id="exec-1",
        provider_call_id="bland-call-1",
        call_type="outbound",
        provider="vapi",  # system simulator is VAPI...
        client_provider="bland",  # ...but the agent under test is Bland
        provider_config={"api_key": _KEY},
        poll_interval_seconds=1,
        max_duration_seconds=60,
    )

    seen = {}

    def _fake_get_call(self, provider_call_id):
        seen["id"] = provider_call_id
        return {"status": "completed", "completed": True, "call_length": 0.7}

    def _explode(*args, **kwargs):
        raise AssertionError("VAPI must never be polled for an outbound Bland customer")

    with patch.object(BlandService, "_get_call", _fake_get_call), patch.object(
        VapiService, "get_call_async", _explode
    ), patch("tfc.temporal.common.heartbeat.Heartbeater", _NoopHeartbeater):
        result = await voice_large.monitor_call_until_complete(monitor_input)

    assert seen["id"] == "bland-call-1"  # the Bland engine polled Bland's id
    assert result.success is True
    assert result.status == CallExecutionStatus.ANALYZING.value
    assert result.duration_seconds == 42  # 0.7 min → 42s


async def test_monitor_inbound_bland_uses_vapi_engine(monkeypatch):
    """Inbound Bland flows through the VAPI simulator: its provider_call_id
    lives in our VAPI account, so the monitor must poll VAPI — NOT Bland."""
    monkeypatch.setenv("VAPI_API_KEY", "test-vapi-key")

    from simulate.temporal.types.activities import MonitorCallInput

    from ee.voice.services.vapi_service import VapiService
    from ee.voice.temporal.activities import voice_large

    monitor_input = MonitorCallInput(
        call_id="exec-2",
        provider_call_id="vapi-sim-call-1",
        call_type="inbound",  # <-- inbound
        provider="vapi",
        client_provider="bland",  # customer is Bland, but it's an inbound test
        provider_config={},
        poll_interval_seconds=1,
        max_duration_seconds=60,
    )

    async def _fake_vapi_get(self, input):
        assert input.call_id == "vapi-sim-call-1"
        return SimpleNamespace(
            status=CallExecutionStatus.ANALYZING,
            duration_seconds=30,
            ended_reason=None,
        )

    def _explode(self, provider_call_id):
        raise AssertionError("Bland must never be polled for an inbound call")

    with patch.object(VapiService, "get_call_async", _fake_vapi_get), patch.object(
        BlandService, "_get_call", _explode
    ), patch("tfc.temporal.common.heartbeat.Heartbeater", _NoopHeartbeater):
        result = await voice_large.monitor_call_until_complete(monitor_input)

    assert result.success is True
    assert result.status == CallExecutionStatus.ANALYZING.value
    assert result.duration_seconds == 30


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def agent_definition(db, organization, workspace):
    from simulate.models import AgentDefinition

    return AgentDefinition.objects.create(
        agent_name="Bland Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+18885550111",
        inbound=False,
        description="outbound bland agent",
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
        dataset=dataset, name="situation", data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="situation")
    return Scenarios.objects.create(
        name="Scn", description="d", source="s",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization, workspace=workspace,
        dataset=dataset, agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def call_execution(db, organization, workspace, agent_definition, simulator_agent, scenario):
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution

    run_test = RunTest.objects.create(
        name="RT", description="d", agent_definition=agent_definition,
        simulator_agent=simulator_agent, organization=organization, workspace=workspace,
    )
    te = TestExecution.objects.create(
        run_test=run_test, status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1, total_calls=1,
        simulator_agent=simulator_agent, agent_definition=agent_definition,
    )
    return CallExecution.objects.create(
        test_execution=te, scenario=scenario, phone_number="+16505550100",
        status=CallExecution.CallStatus.ANALYZING,
        call_metadata={"call_direction": "outbound"},
    )


_BLAND_PAYLOAD = {
    "call_id": "bland-call-1",
    "status": "completed",
    "completed": True,
    "call_length": 1.5,
    "price": 0.42,
    "summary": "Customer asked about opening hours.",
    "recording_url": "https://bland.example/rec.mp3",
    "to": "+16505550100",
    "from": "+18885550111",
    "started_at": "2026-07-20T10:00:00Z",
    "end_at": "2026-07-20T10:01:30Z",
    "transcripts": [
        {"user": "assistant", "text": "Hi, how can I help?"},
        {"user": "user", "text": "What are your hours?"},
    ],
}


# ---------------------------------------------------------------------------
# Persistence — fetch_and_store_call_data / extract_costs (real DB)
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_fetch_and_store_persists_transcript_summary_and_provider_data(
    call_execution,
):
    from simulate.models.test_execution import CallTranscript

    client = BlandService(api_key=_KEY)
    with patch.object(BlandService, "_get_call", return_value=_BLAND_PAYLOAD):
        count, has_agent, has_customer = await client.fetch_and_store_call_data(
            call_execution_id=str(call_execution.id),
            provider_call_id="bland-call-1",
            status="analyzing",
        )

    assert (count, has_agent, has_customer) == (2, True, True)

    @sync_to_async
    def _read():
        call = call_execution.__class__.objects.get(id=call_execution.id)
        rows = list(
            CallTranscript.objects.filter(call_execution=call).order_by("start_time_ms")
        )
        return call, rows

    call, rows = await _read()
    assert call.provider_call_data["bland"] == _BLAND_PAYLOAD
    assert call.call_summary == "Customer asked about opening hours."
    assert call.service_provider_call_id == "bland-call-1"
    # Duration must be persisted (billing gates on it): 1.5 min → 90s.
    assert call.duration_seconds == 90
    assert call.message_count == 2
    assert call.transcript_available is True
    assert [(r.speaker_role, r.content) for r in rows] == [
        (CallTranscript.SpeakerRole.ASSISTANT, "Hi, how can I help?"),
        (CallTranscript.SpeakerRole.USER, "What are your hours?"),
    ]
    # start_time_ms uses idx*1000 for retry headroom (no equal 0s that scramble).
    assert [r.start_time_ms for r in rows] == [0, 1000]


@pytest.mark.django_db(transaction=True)
async def test_fetch_and_store_maps_unknown_role_to_unknown(call_execution):
    """An unrecognised Bland speaker label must land as SpeakerRole.UNKNOWN, a
    valid choice, not a raw string that bypasses the enum."""
    from simulate.models.test_execution import CallTranscript

    payload = {
        "call_id": "bland-call-9",
        "status": "completed",
        "call_length": 0.2,
        "transcripts": [{"user": "narrator", "text": "..."}],
    }
    with patch.object(BlandService, "_get_call", return_value=payload):
        await BlandService(api_key=_KEY).fetch_and_store_call_data(
            call_execution_id=str(call_execution.id),
            provider_call_id="bland-call-9",
            status="analyzing",
        )

    @sync_to_async
    def _roles():
        return [
            r.speaker_role
            for r in CallTranscript.objects.filter(call_execution_id=call_execution.id)
        ]

    assert await _roles() == [CallTranscript.SpeakerRole.UNKNOWN]


@pytest.mark.django_db(transaction=True)
async def test_extract_costs_reads_bland_price(call_execution):
    from ee.voice.services.types.voice import CostBreakdown

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"bland": {"price": 0.42}}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    costs = await BlandService(api_key=_KEY).extract_costs(str(call_execution.id))
    assert isinstance(costs, CostBreakdown)
    assert costs.total == 0.42


@pytest.mark.django_db(transaction=True)
async def test_fetch_and_store_fails_open_when_bland_unreachable(call_execution):
    """A Bland fetch error must not raise: the call still finalizes with the
    passed-through status/end_reason and an empty transcript, so the monitor
    doesn't spin. Swallowing here mirrors the VAPI fetch path."""
    with patch.object(
        BlandService, "_get_call", side_effect=RuntimeError("bland down")
    ):
        count, has_agent, has_customer = await BlandService(
            api_key=_KEY
        ).fetch_and_store_call_data(
            call_execution_id=str(call_execution.id),
            provider_call_id="bland-call-1",
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
    assert call.message_count == 0


# ---------------------------------------------------------------------------
# Normalized transcript — feeds ConversationMetricsCalculator
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_get_normalized_transcript_data_builds_messages(call_execution):
    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {
                "transcripts": [
                    {"user": "assistant", "text": "Hi"},
                    {"user": "user", "text": "Hello"},
                    {"user": "assistant", "text": ""},  # empty → dropped
                ]
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    data = await BlandService(api_key=_KEY).get_normalized_transcript_data(
        str(call_execution.id)
    )

    assert [(m.role, m.content) for m in data.messages] == [
        ("assistant", "Hi"),
        ("user", "Hello"),
    ]
    # Bland has no per-message duration or token usage — these stay unset so the
    # calculator skips WPM / talk-ratio rather than fabricating them.
    assert all(m.duration is None for m in data.messages)
    assert data.token_usage == {}


# ---------------------------------------------------------------------------
# Conversation-metrics dispatch (the regression both reviewers flagged),
# exercised through the real calculate_conversation_metrics activity.
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_metrics_populated_for_bland_outbound(call_execution):
    """With client_provider="bland" the metrics activity dispatches to the Bland
    engine, reads its transcript, and populates conversation_metrics — instead
    of reading an absent provider_call_data["vapi"] and silently bailing."""
    from simulate.temporal.types.activities import CalculateConversationMetricsInput

    from ee.voice.temporal.activities import voice_large

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {
                "transcripts": [
                    {"user": "assistant", "text": "Hi, how can I help?"},
                    {"user": "user", "text": "What are your hours?"},
                ]
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    await voice_large.calculate_conversation_metrics(
        CalculateConversationMetricsInput(
            call_id=str(call_execution.id),
            is_outbound=True,
            provider="vapi",  # system simulator
            client_provider="bland",  # customer holds the data → Bland engine
        )
    )

    @sync_to_async
    def _read():
        return call_execution.__class__.objects.get(id=call_execution.id)

    call = await _read()
    assert call.customer_latency_metrics is not None
    detailed = call.customer_latency_metrics["systemMetrics"]["detailed_data"]
    assert detailed["message_count"] == 2
    # Bland exposes no per-message timing → WPM is intentionally unset.
    assert call.customer_latency_metrics["systemMetrics"]["user_wpm"] is None


@pytest.mark.django_db(transaction=True)
async def test_metrics_skipped_when_client_provider_absent(call_execution, monkeypatch):
    """Control / old-bug repro: without client_provider the activity builds the
    VAPI system engine, reads an absent provider_call_data["vapi"], finds no
    messages and bails — leaving conversation metrics empty. This is exactly the
    behavior the fix above corrects; it fails if the fallback ever reads Bland
    data through the wrong engine."""
    monkeypatch.setenv("VAPI_API_KEY", "test-vapi-key")

    from simulate.temporal.types.activities import CalculateConversationMetricsInput

    from ee.voice.temporal.activities import voice_large

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {"transcripts": [{"user": "user", "text": "Hello"}]}
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    await voice_large.calculate_conversation_metrics(
        CalculateConversationMetricsInput(
            call_id=str(call_execution.id),
            is_outbound=True,
            provider="vapi",
            client_provider=None,  # <-- not threaded → the old (buggy) path
        )
    )

    @sync_to_async
    def _read():
        return call_execution.__class__.objects.get(id=call_execution.id)

    call = await _read()
    assert not call.customer_latency_metrics  # None / unset — metrics skipped


# ---------------------------------------------------------------------------
# Metrics degrade cleanly for a provider with no per-message durations
# (Bland gives one timestamp per row) — never a fabricated latency, never a
# negative duration.
# ---------------------------------------------------------------------------
def test_metrics_no_fake_latency_or_negative_duration_without_durations():
    from ee.voice.services.conversation_metrics import ConversationMetricsCalculator
    from ee.voice.services.types.voice import (
        NormalizedTranscriptData,
        TranscriptMessage,
    )

    # One timestamp per turn (ms from call start), increasing, no duration —
    # exactly what BlandService.get_normalized_transcript_data produces.
    messages = [
        TranscriptMessage(role="assistant", content="Hi, this is Alex.", time=0.0),
        TranscriptMessage(role="user", content="Hello, I have a minute.", time=8000.0),
        TranscriptMessage(role="assistant", content="Great, one question.", time=15000.0),
        TranscriptMessage(role="user", content="No thanks, I'm set.", time=36000.0),
    ]
    calc = ConversationMetricsCalculator(voice_service_provider=ProviderChoices.BLAND)
    metrics = calc.calculate_metrics_from_normalized(
        NormalizedTranscriptData(messages=messages, token_usage={}),
        is_outbound=True,
    )

    # The gap to the next message is the other party's whole turn, not response
    # latency — with one timestamp per turn it is unknowable, so it must be
    # None, not a (large, wrong) number.
    assert metrics.avg_agent_latency_ms is None
    # Duration is the positive span of start times, not a negative from a 0 end.
    assert metrics.detailed_data["total_duration_minutes"] == pytest.approx(0.6)
    # Duration-dependent per-turn metrics stay unset rather than fabricated.
    assert metrics.user_wpm is None
    assert metrics.bot_wpm is None
    assert metrics.talk_ratio is None
    # Counts still work.
    assert metrics.detailed_data["message_count"] == 4


@pytest.mark.django_db(transaction=True)
async def test_normalized_transcript_skips_non_speaker_rows(call_execution):
    """Rows with a role Bland doesn't map to a speaker (narrator / webhook /
    agent-action) are stored as UNKNOWN; the metrics transcript must drop them
    too, so they aren't miscounted as customer turns (inflating user counts and
    fabricating latency pairs)."""

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {
                "transcripts": [
                    {"user": "assistant", "text": "Hi"},
                    {"user": "agent-action", "text": "[routed to webhook]"},
                    {"user": "user", "text": "Hello"},
                ]
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    data = await BlandService(api_key=_KEY).get_normalized_transcript_data(
        str(call_execution.id)
    )
    assert [(m.role, m.content) for m in data.messages] == [
        ("assistant", "Hi"),
        ("user", "Hello"),
    ]


@pytest.mark.django_db(transaction=True)
async def test_normalized_transcript_orders_partial_timestamps(call_execution):
    """A row missing created_at inherits the previous message's time so the
    calculator's stable sort keeps it in place — instead of an ordinal index
    sorting it ahead of real millisecond offsets (turn-order scramble)."""

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {
                "transcripts": [
                    {"user": "assistant", "text": "a", "created_at": "2026-07-21T11:00:00.000Z"},
                    {"user": "user", "text": "b", "created_at": "2026-07-21T11:00:10.000Z"},
                    {"user": "assistant", "text": "c"},  # no timestamp
                    {"user": "user", "text": "d", "created_at": "2026-07-21T11:00:20.000Z"},
                ]
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    data = await BlandService(api_key=_KEY).get_normalized_transcript_data(
        str(call_execution.id)
    )
    # Sorting by time (what the calculator does) must not scramble the order.
    by_time = sorted(data.messages, key=lambda m: m.time)
    assert [m.content for m in by_time] == ["a", "b", "c", "d"]
    # "c" inherits "b"'s time rather than a tiny ordinal that jumps to the front.
    times = {m.content: m.time for m in data.messages}
    assert times["c"] == times["b"]


# ---------------------------------------------------------------------------
# Recordings — rehost + billing parity + idempotency
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_rehosts_scopes_and_meters(
    call_execution,
):
    """The rehost tags the S3 object with the bland provider, falls back to
    project_id=None when the agent has no observability provider, and emits one
    VOICE_RECORDING_STORAGE event for the uploaded bytes (billing parity)."""
    from ee.usage.schemas.event_types import BillingEventType

    _S3_URL = "https://s3.example/call-recordings/bland/rec.mp3"

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {"recording_url": "https://bland.example/rec.mp3"}
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    with patch(
        "simulate.temporal.utils.async_storage."
        "convert_audio_url_to_s3_async_with_size",
        new_callable=AsyncMock,
    ) as mock_convert, patch("ee.usage.services.emitter.emit") as mock_emit:
        mock_convert.return_value = (_S3_URL, 4096)
        result = await BlandService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    assert result.recording_url == _S3_URL
    assert mock_convert.call_args.kwargs["provider"] == "bland"
    assert mock_convert.call_args.kwargs["project_id"] is None
    assert mock_emit.call_count == 1
    event = mock_emit.call_args.args[0]
    assert event.event_type == BillingEventType.VOICE_RECORDING_STORAGE
    assert event.amount == 4096


@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_skips_billing_on_failed_download(
    call_execution,
):
    """When the download fails the converter returns the original provider URL
    with zero bytes, so nothing is metered and the raw URL is kept."""
    _RAW = "https://bland.example/rec.mp3"

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {"bland": {"recording_url": _RAW}}
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    with patch(
        "simulate.temporal.utils.async_storage."
        "convert_audio_url_to_s3_async_with_size",
        new_callable=AsyncMock,
    ) as mock_convert, patch("ee.usage.services.emitter.emit") as mock_emit:
        mock_convert.return_value = (_RAW, 0)
        result = await BlandService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    assert result.recording_url == _RAW
    mock_emit.assert_not_called()


@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_meters_idempotently_on_retry(
    call_execution,
):
    """initiate/fetch are Temporal activities that retry, so the same recording
    can be rehosted twice. The storage event must carry a DETERMINISTIC id
    (uuid5 over the call id) so the second emit dedups instead of double-billing.
    This test fails if the id is switched to a random uuid4."""

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {"recording_url": "https://bland.example/rec.mp3"}
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    with patch(
        "simulate.temporal.utils.async_storage."
        "convert_audio_url_to_s3_async_with_size",
        new_callable=AsyncMock,
    ) as mock_convert, patch("ee.usage.services.emitter.emit") as mock_emit:
        mock_convert.return_value = ("https://s3.example/rec.mp3", 4096)
        client = BlandService(api_key=_KEY)
        await client.extract_and_persist_recordings(str(call_execution.id))
        await client.extract_and_persist_recordings(str(call_execution.id))

    assert mock_emit.call_count == 2
    first_id = mock_emit.call_args_list[0].args[0].event_id
    second_id = mock_emit.call_args_list[1].args[0].event_id
    assert first_id == second_id


@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_repolls_until_ready(call_execution):
    """A short call fetched before Bland finished the async recording is
    re-polled; once the URL appears it is rehosted, metered once, and the stored
    raw payload is refreshed so the Attributes tab no longer shows an empty URL."""
    _READY = "https://bland.example/rec-late.mp3"
    _S3_URL = "https://s3.example/call-recordings/bland/rec.mp3"

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {
                "call_id": "bland-call-1",
                "record": True,
                "completed": True,
                "status": "completed",
                "call_length": 0.5,
                "recording_url": "",
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    # Empty on the first two polls, ready on the third.
    get_call = MagicMock(
        side_effect=[{"recording_url": ""}, {"recording_url": None}, {"recording_url": _READY}]
    )
    with patch.object(BlandService, "_get_call", get_call), patch(
        "ee.voice.services.bland_service.asyncio.sleep", new_callable=AsyncMock
    ), patch(
        "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
        new_callable=AsyncMock,
    ) as mock_convert, patch("ee.usage.services.emitter.emit") as mock_emit:
        mock_convert.return_value = (_S3_URL, 4096)
        result = await BlandService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    assert get_call.call_count == 3
    assert mock_convert.call_args.args[1] == _READY  # rehosted the re-fetched URL
    assert result.recording_url == _S3_URL
    assert result.provider_call_data["bland"]["recording_url"] == _READY
    assert mock_emit.call_count == 1


@pytest.mark.django_db(transaction=True)
async def test_extract_and_persist_recordings_gives_up_when_never_ready(call_execution):
    """If the recording never lands, the re-poll gives up after N bounded
    attempts without raising (fail-open) and rehosts nothing."""
    from ee.voice.services.bland_service import _RECORDING_POLL_MAX_ATTEMPTS

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {
                "call_id": "bland-call-1",
                "record": True,
                "completed": True,
                "status": "completed",
                "call_length": 0.5,
                "recording_url": "",
            }
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    get_call = MagicMock(return_value={"recording_url": ""})
    with patch.object(BlandService, "_get_call", get_call), patch(
        "ee.voice.services.bland_service.asyncio.sleep", new_callable=AsyncMock
    ), patch(
        "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
        new_callable=AsyncMock,
    ) as mock_convert:
        result = await BlandService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    assert get_call.call_count == _RECORDING_POLL_MAX_ATTEMPTS
    assert result.recording_url is None
    assert result.provider_call_data is None
    mock_convert.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "bland_extra",
    [
        {"record": False, "completed": True, "status": "completed", "call_length": 0.5},
        {"record": True, "completed": False, "status": "busy", "call_length": 0},
        {"record": True, "completed": True, "status": "completed", "call_length": 0},
    ],
)
async def test_extract_and_persist_recordings_no_repoll_when_not_expected(
    call_execution, bland_extra
):
    """No recording will ever exist for not-recorded / not-completed / zero-length
    calls, so the re-poll must not fire (no added latency on failed calls)."""

    @sync_to_async
    def _seed():
        call_execution.provider_call_data = {
            "bland": {"call_id": "bland-call-1", "recording_url": "", **bland_extra}
        }
        call_execution.save(update_fields=["provider_call_data"])

    await _seed()

    get_call = MagicMock(return_value={"recording_url": "x"})
    with patch.object(BlandService, "_get_call", get_call):
        result = await BlandService(api_key=_KEY).extract_and_persist_recordings(
            str(call_execution.id)
        )

    get_call.assert_not_called()
    assert result.recording_url is None
    assert result.provider_call_data is None


# ---------------------------------------------------------------------------
# prepare_call guards (unchanged behavior — kept green through the refactor)
# ---------------------------------------------------------------------------
@pytest.fixture
def bland_web_call(db, call_execution, agent_definition, organization, workspace):
    """A Bland outbound CallExecution whose version snapshot carries an
    assistant_id but NO contact_number, so prepare_call resolves
    connection_type='web_bland' — Bland has no web connector."""
    from simulate.models import AgentVersion

    version = AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={"provider": "bland", "assistant_id": "pathway-1"},
    )
    call_execution.agent_version = version
    call_execution.save(update_fields=["agent_version"])
    return call_execution


@pytest.mark.django_db(transaction=True)
async def test_prepare_call_rejects_bland_without_web_connector(
    bland_web_call, workspace
):
    from ee.voice.temporal.activities.voice_small import prepare_call
    from simulate.temporal.types.activities import PrepareCallInput

    with patch("temporalio.activity.info"), patch(
        "simulate.services.agent_definition.resolve_api_key_for_version",
        return_value="org_bland_key",
    ):
        result = await prepare_call(
            PrepareCallInput(
                call_id=str(bland_web_call.id),
                workspace_id=str(workspace.id),
            )
        )

    assert result.error is not None
    assert "web connector" in result.error
    assert result.is_outbound is True


@pytest.fixture
def unsupported_sip_call(db, call_execution, agent_definition, organization, workspace):
    """An OUTBOUND CallExecution whose customer provider is Retell (not VAPI or
    Bland) with a contact_number, so prepare_call takes the SIP path for a
    provider whose outbound data plane we don't drive."""
    from simulate.models import AgentVersion

    version = AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot={
            "provider": "retell",
            "assistant_id": "asst-1",
            "contact_number": "+16505550100",
        },
    )
    call_execution.agent_version = version
    call_execution.save(update_fields=["agent_version"])
    return call_execution


@pytest.mark.django_db(transaction=True)
async def test_prepare_call_rejects_unsupported_sip_provider(
    unsupported_sip_call, workspace
):
    from ee.voice.temporal.activities.voice_small import prepare_call
    from simulate.temporal.types.activities import PrepareCallInput

    with patch("temporalio.activity.info"), patch(
        "simulate.services.agent_definition.resolve_api_key_for_version",
        return_value="org_retell_key",
    ):
        result = await prepare_call(
            PrepareCallInput(
                call_id=str(unsupported_sip_call.id),
                workspace_id=str(workspace.id),
            )
        )

    assert result.error is not None
    assert "retell" in result.error
    assert result.is_outbound is True
