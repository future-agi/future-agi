"""A voice call's provider credentials are read when the call is dialled.

Activity inputs and results carry the call id. The activity that dials reads
the customer's provider credentials from the call's agent version by call id,
and uses a value its input carries as it is. Stored call records keep no
provider key. Every credential in this module is a made-up string.
"""

import dataclasses
import importlib
import inspect
import json
import re
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.converter import DataConverter

from ee.voice.temporal.activities.customer_credentials import (
    SECRET_METADATA_KEYS,
    CustomerCredentials,
)
from ee.voice.tests.test_bland_service import agent_definition as agent_definition
from ee.voice.tests.test_bland_service import call_execution as call_execution
from ee.voice.tests.test_bland_service import scenario as scenario
from ee.voice.tests.test_bland_service import simulator_agent as simulator_agent
from simulate.temporal.types.activities import (
    FetchAndPersistCallResultInput,
    FetchClientCallInput,
    InitiateCallInput,
    MonitorCallInput,
    PrepareCallOutput,
    RunBridgeInput,
)
from tracer.models.observability_provider import ProviderChoices

SENTINEL_KEY = "made-up-key-7f3a"
SENTINEL_SECRET = "made-up-secret-91c2"
SENTINEL_URL = "wss://made-up-livekit.example"
OLD_KEY = "made-up-old-key-4d0e"
OLD_SECRET = "made-up-old-secret-5b6f"
PROVIDER_EXTRA = "made-up-provider-field-2a8c"

READER = "ee.voice.temporal.activities.customer_credentials.read_customer_credentials"
MANAGER = "ee.voice.services.voice_service_manager.VoiceServiceManager"

VAPI_SIP_SNAPSHOT = {
    "provider": "vapi",
    "assistant_id": "made-up-assistant",
    "contact_number": "+16505550100",
}
VAPI_WEB_SNAPSHOT = {"provider": "vapi", "assistant_id": "made-up-assistant"}
LIVEKIT_BRIDGE_SNAPSHOT = {
    "provider": "livekit_bridge",
    "livekit_agent_name": "made-up-agent",
}

# The credential-named fields the activity types keep. Each one is accept-only:
# a set value is used, an empty one is read by call id.
LEGACY_ACCEPT_ONLY = {
    "simulate.temporal.types.activities": frozenset(
        {
            ("PrepareCallOutput", "client_api_key"),
            ("InitiateCallInput", "user_api_key"),
            ("FetchClientCallInput", "client_api_key"),
            ("RunBridgeInput", "customer_api_key"),
            ("RunBridgeInput", "customer_livekit_api_key"),
            ("RunBridgeInput", "customer_livekit_api_secret"),
        }
    ),
    "ee.voice.temporal.types.voice_activities": frozenset(
        {
            ("InitiateCallInput", "user_api_key"),
            ("FetchClientCallInput", "client_api_key"),
            ("RunBridgeInput", "customer_api_key"),
            ("RunBridgeInput", "customer_livekit_api_key"),
            ("RunBridgeInput", "customer_livekit_api_secret"),
        }
    ),
}
_SECRET_NAME = re.compile(r"api_?key|secret|token|password|credential|auth", re.I)
_PAYLOADS = DataConverter.default.payload_converter


class _Heartbeat:
    def __init__(self, *args, **kwargs):
        self.details = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@contextmanager
def _activity_context(module):
    with (
        patch(f"ee.voice.temporal.activities.{module}.close_old_connections"),
        patch("tfc.temporal.common.heartbeat.Heartbeater", _Heartbeat),
        patch("temporalio.activity.heartbeat"),
    ):
        yield


def assert_no_secret(obj, *sentinels):
    """Check what would land in workflow history for ``obj``."""
    for payload in _PAYLOADS.to_payloads([obj]):
        for sentinel in sentinels:
            assert sentinel.encode() not in payload.data
    for name in ("metadata", "provider_config"):
        held = getattr(obj, name, None) or {}
        assert not SECRET_METADATA_KEYS & set(held)


def _give_credentials(
    call, agent_definition, organization, workspace, provider_type, snapshot
):
    from simulate.models import AgentVersion
    from simulate.models.agent_definition import ProviderCredentials

    version = AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        configuration_snapshot=snapshot,
    )
    ProviderCredentials.objects.create(
        agent_version=version,
        provider_type=provider_type,
        api_key=SENTINEL_KEY,
        api_secret=SENTINEL_SECRET,
        server_url=SENTINEL_URL,
    )
    call.agent_version = version
    call.save(update_fields=["agent_version"])
    return call


@pytest.fixture
def vapi_call(db, call_execution, agent_definition, organization, workspace):
    """An outbound call whose agent version holds VAPI credentials."""
    return _give_credentials(
        call_execution,
        agent_definition,
        organization,
        workspace,
        "vapi",
        VAPI_SIP_SNAPSHOT,
    )


@pytest.fixture
def livekit_call(db, call_execution, agent_definition, organization, workspace):
    """A call whose agent version holds LiveKit bridge credentials."""
    return _give_credentials(
        call_execution,
        agent_definition,
        organization,
        workspace,
        "livekit",
        LIVEKIT_BRIDGE_SNAPSHOT,
    )


@pytest.fixture
def fake_managers():
    """Stands in for VoiceServiceManager and records what each one is given."""
    built = []
    providers = []
    dispatched = []

    class _Manager:
        def __init__(self, api_key="", system_voice_provider=None):
            built.append(api_key)
            providers.append(system_voice_provider)
            self.engine = SimpleNamespace(create_outbound_call=_create_outbound_call)

        def initiate_outbound_call(self, call_input):
            return SimpleNamespace(
                success=True,
                phone_number="+15555550100",
                assistant_id="made-up-simulator",
                error=None,
            )

        def initiate_inbound_call(self, call_input):
            dispatched.append(call_input.metadata)
            return SimpleNamespace(
                success=True,
                provider_call_id="made-up-room",
                provider_data={"room_name": "made-up-room"},
                error=None,
            )

    def _create_outbound_call(**kwargs):
        return {"id": "made-up-call-1", "status": "queued", "echo": PROVIDER_EXTRA}

    with patch(MANAGER, _Manager):
        yield SimpleNamespace(built=built, providers=providers, dispatched=dispatched)


def _persisting_manager():
    """A voice manager whose engine steps succeed with nothing to store."""
    manager = MagicMock()
    manager.fetch_and_store_call_data = AsyncMock(return_value=(0, False, False))
    manager.extract_and_persist_recordings = AsyncMock(
        return_value=SimpleNamespace(
            recording_url=None,
            stereo_recording_url=None,
            customer_recording_url=None,
            assistant_recording_url=None,
            provider_call_data=None,
        )
    )
    manager.extract_costs = AsyncMock(
        return_value=SimpleNamespace(
            total=None, stt=None, llm=None, tts=None, transport=None, storage=None
        )
    )
    return manager


def _outbound_input(call_id, user_api_key=None):
    return InitiateCallInput(
        call_id=call_id,
        call_data={"call_type": "outbound"},
        provider="vapi",
        phone_number="+15555550100",
        provider_phone_id="made-up-phone-id",
        user_api_key=user_api_key,
        user_assistant_id="made-up-assistant",
        user_phone_number="+15555550111",
        client_provider="vapi",
    )


def _fetch_input(call_id, status="completed", provider_call_id="made-up-call-1"):
    return FetchAndPersistCallResultInput(
        call_id=call_id,
        status=status,
        provider_call_id=provider_call_id,
        provider="vapi",
        call_type="outbound",
        client_provider="vapi",
    )


# ---------------------------------------------------------------------------
# Activity types: credential-named fields are accept-only
# ---------------------------------------------------------------------------
def _secret_named_fields(module):
    found = {}
    for cls in vars(module).values():
        if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
            continue
        if cls.__module__ != module.__name__:
            continue
        for item in dataclasses.fields(cls):
            if not _SECRET_NAME.search(item.name):
                continue
            if item.name.endswith(("_id", "_ids")):
                continue
            if item.name.endswith("_tokens") and item.type in (int, "int"):
                continue
            found[(cls.__name__, item.name)] = item
    return found


@pytest.mark.parametrize("module_name", sorted(LEGACY_ACCEPT_ONLY))
def test_activity_types_carry_only_accept_only_credential_fields(module_name):
    found = _secret_named_fields(importlib.import_module(module_name))

    assert set(found) == LEGACY_ACCEPT_ONLY[module_name]
    for name, item in found.items():
        assert item.default in ("", None), name


@pytest.mark.parametrize(
    "type_, payload, expected",
    [
        (
            RunBridgeInput,
            {
                "call_id": "made-up-call",
                "room_name": "made-up-room",
                "connection_type": "web_livekit_bridge",
                "customer_api_key": OLD_KEY,
                "customer_assistant_id": "made-up-agent",
                "max_duration_seconds": 2100,
                "customer_livekit_url": SENTINEL_URL,
                "customer_livekit_api_key": OLD_KEY,
                "customer_livekit_api_secret": OLD_SECRET,
            },
            {
                "customer_api_key": OLD_KEY,
                "customer_assistant_id": "made-up-agent",
                "customer_livekit_api_key": OLD_KEY,
                "customer_livekit_api_secret": OLD_SECRET,
            },
        ),
        (
            RunBridgeInput,
            {
                "call_id": "made-up-call",
                "room_name": "made-up-room",
                "connection_type": "web_vapi",
            },
            {"customer_api_key": "", "customer_assistant_id": ""},
        ),
        (
            InitiateCallInput,
            {"call_id": "made-up-call", "call_data": {}, "user_api_key": OLD_KEY},
            {"user_api_key": OLD_KEY},
        ),
        (
            FetchClientCallInput,
            {"call_id": "made-up-call", "client_api_key": OLD_KEY},
            {"client_api_key": OLD_KEY},
        ),
        (
            PrepareCallOutput,
            {"is_outbound": True, "client_api_key": OLD_KEY},
            {"client_api_key": OLD_KEY},
        ),
    ],
    ids=[
        "bridge-with-values",
        "bridge-with-ids-only",
        "initiate",
        "client-fetch",
        "prepare-result",
    ],
)
def test_recorded_activity_payloads_decode_into_the_activity_types(
    type_, payload, expected
):
    (decoded,) = _PAYLOADS.from_payloads(_PAYLOADS.to_payloads([payload]), [type_])

    for name, value in expected.items():
        assert getattr(decoded, name) == value


def test_customer_credentials_never_print_their_values():
    text = repr(
        CustomerCredentials(
            api_key=SENTINEL_KEY,
            livekit_url=SENTINEL_URL,
            livekit_api_key=SENTINEL_KEY,
            livekit_api_secret=SENTINEL_SECRET,
        )
    )

    assert SENTINEL_KEY not in text
    assert SENTINEL_SECRET not in text


@pytest.mark.django_db(transaction=True)
async def test_a_call_without_an_agent_version_has_no_credentials(call_execution):
    from ee.voice.temporal.activities.customer_credentials import (
        read_customer_credentials,
    )

    assert await read_customer_credentials(str(call_execution.id)) == (
        CustomerCredentials()
    )


# ---------------------------------------------------------------------------
# run_bridge
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("carried_url", ["", SENTINEL_URL])
async def test_livekit_bridge_reads_its_credentials_by_call_id(
    livekit_call, carried_url
):
    from ee.voice.temporal.activities.bridge import _connector_config

    with patch("ee.voice.temporal.activities.bridge.close_old_connections"):
        config = await _connector_config(
            RunBridgeInput(
                call_id=str(livekit_call.id),
                room_name="made-up-room",
                connection_type="web_livekit_bridge",
                customer_livekit_url=carried_url,
            )
        )

    assert config.api_key == SENTINEL_KEY
    assert config.livekit_api_secret == SENTINEL_SECRET
    assert config.livekit_url == SENTINEL_URL
    assert config.room_name == "customer_made-up-room"


@pytest.mark.django_db(transaction=True)
async def test_web_vapi_bridge_reads_only_the_api_key_by_call_id(vapi_call):
    from ee.voice.temporal.activities.bridge import _connector_config

    with patch("ee.voice.temporal.activities.bridge.close_old_connections"):
        config = await _connector_config(
            RunBridgeInput(
                call_id=str(vapi_call.id),
                room_name="made-up-room",
                connection_type="web_vapi",
                customer_assistant_id="made-up-assistant",
            )
        )

    assert config.api_key == SENTINEL_KEY
    assert config.assistant_id == "made-up-assistant"
    assert config.livekit_url == ""
    assert config.livekit_api_secret == ""


@pytest.mark.parametrize(
    "bridge_input, api_key, secret",
    [
        (
            RunBridgeInput(
                call_id="made-up-call",
                room_name="made-up-room",
                connection_type="web_vapi",
                customer_api_key=OLD_KEY,
                customer_assistant_id="made-up-assistant",
            ),
            OLD_KEY,
            "",
        ),
        (
            RunBridgeInput(
                call_id="made-up-call",
                room_name="made-up-room",
                connection_type="web_livekit_bridge",
                customer_api_key=OLD_KEY,
                customer_livekit_url=SENTINEL_URL,
                customer_livekit_api_key=OLD_KEY,
                customer_livekit_api_secret=OLD_SECRET,
            ),
            OLD_KEY,
            OLD_SECRET,
        ),
    ],
    ids=["web_vapi", "web_livekit_bridge"],
)
async def test_bridge_uses_the_credentials_its_input_carries(
    bridge_input, api_key, secret
):
    from ee.voice.temporal.activities.bridge import _connector_config

    with patch(READER, new_callable=AsyncMock) as reader:
        config = await _connector_config(bridge_input)

    reader.assert_not_called()
    assert config.api_key == api_key
    assert config.livekit_api_secret == secret


# ---------------------------------------------------------------------------
# initiate_call
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_outbound_dial_reads_the_customer_key_by_call_id(
    vapi_call, fake_managers
):
    from ee.voice.temporal.activities import voice_large

    with _activity_context("voice_large"):
        result = await voice_large.initiate_call(_outbound_input(str(vapi_call.id)))

    assert result.success is True
    assert fake_managers.built == ["", SENTINEL_KEY]


async def test_outbound_dial_uses_the_key_its_input_carries(fake_managers):
    from ee.voice.temporal.activities import voice_large

    with (
        _activity_context("voice_large"),
        patch(READER, new_callable=AsyncMock) as reader,
    ):
        result = await voice_large.initiate_call(
            _outbound_input("made-up-call", user_api_key=OLD_KEY)
        )

    reader.assert_not_called()
    assert result.success is True
    assert fake_managers.built == ["", OLD_KEY]


@pytest.mark.django_db(transaction=True)
async def test_livekit_system_sip_outbound_is_placed_and_fetched_with_one_key(
    vapi_call, fake_managers
):
    # Under the LiveKit system provider, the customer's own account places the
    # SIP call, and its result is fetched from that account with the same key.
    from ee.voice.temporal.activities import voice_large

    dial = dataclasses.replace(_outbound_input(str(vapi_call.id)), provider="livekit")
    with _activity_context("voice_large"):
        placed = await voice_large.initiate_call(dial)

    assert placed.success is True
    # Our side is set up on the LiveKit system provider without the customer's
    # key; the call is placed on the customer's own provider with it.
    assert fake_managers.built == ["", SENTINEL_KEY]
    assert fake_managers.providers == [ProviderChoices.LIVEKIT, ProviderChoices.VAPI]

    fetch = dataclasses.replace(_fetch_input(str(vapi_call.id)), provider="livekit")
    with (
        _activity_context("voice_large"),
        patch(MANAGER, return_value=_persisting_manager()) as manager_cls,
    ):
        fetched = await voice_large.fetch_and_persist_call_result(fetch)

    assert fetched.success is True
    # The result is fetched from the customer's provider with that same key,
    # not from the system engine.
    assert manager_cls.call_args.kwargs["api_key"] == SENTINEL_KEY
    assert manager_cls.call_args.kwargs["system_voice_provider"] == ProviderChoices.VAPI


async def test_outbound_dial_result_keeps_only_the_provider_call_id_and_status(
    fake_managers,
):
    from ee.voice.temporal.activities import voice_large

    with _activity_context("voice_large"):
        result = await voice_large.initiate_call(
            _outbound_input("made-up-call", user_api_key=OLD_KEY)
        )

    assert result.provider_data["trigger_response"] == {
        "id": "made-up-call-1",
        "status": "queued",
    }
    assert_no_secret(result, PROVIDER_EXTRA, OLD_KEY)


@pytest.mark.django_db(transaction=True)
async def test_livekit_bridge_dispatch_carries_credentials_read_by_call_id(
    livekit_call, fake_managers
):
    from ee.voice.temporal.activities import voice_large

    with _activity_context("voice_large"):
        result = await voice_large.initiate_call(
            InitiateCallInput(
                call_id=str(livekit_call.id),
                call_data={"call_type": "outbound"},
                metadata={
                    "customer_livekit_url": SENTINEL_URL,
                    "workflow_id": "made-up-workflow",
                },
                provider="livekit",
                user_assistant_id="made-up-agent",
                client_provider="livekit_bridge",
                connection_type="web_livekit_bridge",
            )
        )

    assert result.success is True
    (metadata,) = fake_managers.dispatched
    assert metadata["customer_api_key"] == SENTINEL_KEY
    assert metadata["customer_livekit_api_key"] == SENTINEL_KEY
    assert metadata["customer_livekit_api_secret"] == SENTINEL_SECRET
    assert metadata["customer_livekit_url"] == SENTINEL_URL
    assert metadata["customer_assistant_id"] == "made-up-agent"
    assert metadata["workflow_id"] == "made-up-workflow"


@pytest.mark.django_db(transaction=True)
async def test_web_vapi_dispatch_carries_only_the_api_key_read_by_call_id(
    vapi_call, fake_managers
):
    from ee.voice.temporal.activities import voice_large

    with _activity_context("voice_large"):
        await voice_large.initiate_call(
            InitiateCallInput(
                call_id=str(vapi_call.id),
                call_data={"call_type": "inbound"},
                provider="livekit",
                user_assistant_id="made-up-assistant",
                client_provider="vapi",
                connection_type="web_vapi",
            )
        )

    (metadata,) = fake_managers.dispatched
    assert metadata["customer_api_key"] == SENTINEL_KEY
    assert "customer_livekit_api_key" not in metadata
    assert "customer_livekit_api_secret" not in metadata


async def test_dispatch_carries_the_credentials_its_input_carries(fake_managers):
    from ee.voice.temporal.activities import voice_large

    with (
        _activity_context("voice_large"),
        patch(READER, new_callable=AsyncMock) as reader,
    ):
        await voice_large.initiate_call(
            InitiateCallInput(
                call_id="made-up-call",
                call_data={"call_type": "inbound"},
                metadata={
                    "customer_livekit_url": SENTINEL_URL,
                    "customer_livekit_api_key": OLD_KEY,
                    "customer_livekit_api_secret": OLD_SECRET,
                },
                provider="livekit",
                user_api_key=OLD_KEY,
                user_assistant_id="made-up-agent",
                client_provider="livekit_bridge",
                connection_type="web_livekit_bridge",
            )
        )

    reader.assert_not_called()
    (metadata,) = fake_managers.dispatched
    assert metadata["customer_api_key"] == OLD_KEY
    assert metadata["customer_livekit_api_key"] == OLD_KEY
    assert metadata["customer_livekit_api_secret"] == OLD_SECRET
    assert metadata["customer_assistant_id"] == "made-up-agent"


# ---------------------------------------------------------------------------
# monitor_call_until_complete
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_outbound_monitor_polls_with_the_key_read_by_call_id(vapi_call):
    from ee.voice.services.bland_service import BlandService
    from ee.voice.temporal.activities import voice_large

    seen = {}

    def _fake_get_call(self, provider_call_id):
        seen["api_key"] = self.api_key
        return {"status": "completed", "completed": True, "call_length": 0.5}

    with (
        _activity_context("voice_large"),
        patch.object(BlandService, "_get_call", _fake_get_call),
    ):
        result = await voice_large.monitor_call_until_complete(
            MonitorCallInput(
                call_id=str(vapi_call.id),
                provider_call_id="made-up-bland-call",
                call_type="outbound",
                provider="vapi",
                client_provider="bland",
                provider_config={},
                poll_interval_seconds=1,
                max_duration_seconds=60,
            )
        )

    assert result.success is True
    assert seen["api_key"] == SENTINEL_KEY


# ---------------------------------------------------------------------------
# fetch_and_persist_call_result
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_customer_account_fetch_reads_the_key_by_call_id_for_every_end(
    vapi_call, status
):
    from ee.voice.temporal.activities import voice_large

    with (
        _activity_context("voice_large"),
        patch(MANAGER, return_value=_persisting_manager()) as manager_cls,
    ):
        result = await voice_large.fetch_and_persist_call_result(
            _fetch_input(str(vapi_call.id), status=status)
        )

    assert result.success is True
    assert manager_cls.call_args.kwargs["api_key"] == SENTINEL_KEY


@pytest.mark.django_db(transaction=True)
async def test_customer_account_fetch_without_a_provider_call_reads_nothing(
    vapi_call,
):
    from ee.voice.temporal.activities import voice_large

    with (
        _activity_context("voice_large"),
        patch(READER, new_callable=AsyncMock) as reader,
        patch(MANAGER, return_value=_persisting_manager()) as manager_cls,
    ):
        result = await voice_large.fetch_and_persist_call_result(
            _fetch_input(str(vapi_call.id), status="failed", provider_call_id="")
        )

    reader.assert_not_called()
    assert result.success is True
    assert manager_cls.call_args.kwargs["api_key"] == ""


@pytest.mark.django_db(transaction=True)
async def test_customer_account_fetch_uses_the_key_its_input_carries(
    call_execution,
):
    from ee.voice.temporal.activities import voice_large

    fetch_input = _fetch_input(str(call_execution.id))
    fetch_input.provider_config = {"api_key": OLD_KEY}
    with (
        _activity_context("voice_large"),
        patch(READER, new_callable=AsyncMock) as reader,
        patch(MANAGER, return_value=_persisting_manager()) as manager_cls,
    ):
        result = await voice_large.fetch_and_persist_call_result(fetch_input)

    reader.assert_not_called()
    assert result.success is True
    assert manager_cls.call_args.kwargs["api_key"] == OLD_KEY


# ---------------------------------------------------------------------------
# fetch_client_call_data
# ---------------------------------------------------------------------------
def _lookup_manager(lookups):
    """A voice manager whose client-call lookup records its key, finds none."""
    manager = MagicMock()

    def _find_client_call(find_input):
        lookups.append(find_input.customer_api_key)
        return None

    manager.find_client_call = _find_client_call
    return manager


def _client_input(call_id, client_api_key="", client_provider="vapi"):
    return FetchClientCallInput(
        call_id=call_id,
        client_api_key=client_api_key,
        client_assistant_id="made-up-assistant",
        client_provider=client_provider,
        call_type="outbound",
    )


@pytest.mark.django_db(transaction=True)
async def test_client_call_lookup_reads_the_key_by_call_id(vapi_call):
    from ee.voice.temporal.activities import voice_xl

    lookups = []
    with (
        _activity_context("voice_xl"),
        patch(MANAGER, return_value=_lookup_manager(lookups)) as manager_cls,
    ):
        result = await voice_xl.fetch_client_call_data(_client_input(str(vapi_call.id)))

    assert result.error == "Could not find matching client call"
    assert manager_cls.call_args.kwargs["api_key"] == SENTINEL_KEY
    assert lookups == [SENTINEL_KEY]


@pytest.mark.django_db(transaction=True)
async def test_client_call_lookup_uses_the_key_its_input_carries(call_execution):
    from ee.voice.temporal.activities import voice_xl

    lookups = []
    with (
        _activity_context("voice_xl"),
        patch(READER, new_callable=AsyncMock) as reader,
        patch(MANAGER, return_value=_lookup_manager(lookups)) as manager_cls,
    ):
        await voice_xl.fetch_client_call_data(
            _client_input(str(call_execution.id), client_api_key=OLD_KEY)
        )

    reader.assert_not_called()
    assert manager_cls.call_args.kwargs["api_key"] == OLD_KEY
    assert lookups == [OLD_KEY]


async def test_livekit_bridge_client_lookup_reads_nothing():
    from ee.voice.temporal.activities import voice_xl

    with (
        _activity_context("voice_xl"),
        patch(READER, new_callable=AsyncMock) as reader,
    ):
        result = await voice_xl.fetch_client_call_data(
            _client_input("made-up-call", client_provider="livekit_bridge")
        )

    reader.assert_not_called()
    assert result.success is True


# ---------------------------------------------------------------------------
# Stored call records keep no provider key
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
async def test_new_call_records_store_no_provider_key(vapi_call, scenario):
    from asgiref.sync import sync_to_async

    from simulate.models.run_test import CreateCallExecution
    from simulate.temporal.activities.test_execution import (
        create_call_execution_records,
    )
    from simulate.temporal.types.activities import CreateCallRecordsInput

    test_execution = vapi_call.test_execution
    test_execution.agent_version = vapi_call.agent_version
    await test_execution.asave(update_fields=["agent_version"])

    with (
        patch("simulate.temporal.activities.test_execution.close_old_connections"),
        patch("tfc.temporal.common.heartbeat.Heartbeater", _Heartbeat),
        patch("temporalio.activity.info"),
    ):
        result = await create_call_execution_records(
            CreateCallRecordsInput(
                test_execution_id=str(test_execution.id),
                scenarios=[{"id": str(scenario.id), "row_ids": []}],
            )
        )

    assert result.error is None
    stored = await sync_to_async(list)(
        CreateCallExecution.objects.filter(
            call_execution__test_execution=test_execution
        ).values_list("metadata", flat=True)
    )
    assert stored
    for metadata in stored:
        assert "user_api_key" not in metadata
        assert SENTINEL_KEY not in json.dumps(metadata)


@pytest.mark.django_db
def test_rerun_call_record_stores_no_provider_key(call_execution, monkeypatch):
    from simulate.models.run_test import CreateCallExecution
    from simulate.views.run_test import _create_rerun_call_execution

    monkeypatch.setenv("SYSTEM_VOICE_PROVIDER", "livekit")
    call_execution.call_metadata = {
        "call_direction": "outbound",
        "user_api_key": SENTINEL_KEY,
        "user_assistant_id": "made-up-assistant",
    }
    call_execution.save(update_fields=["call_metadata"])

    _create_rerun_call_execution(call_execution)

    stored = CreateCallExecution.objects.get(call_execution=call_execution).metadata
    assert "user_api_key" not in stored
    assert SENTINEL_KEY not in json.dumps(stored)
    assert stored["user_assistant_id"] == "made-up-assistant"


def test_executor_queues_calls_without_a_provider_key():
    from simulate.services.test_executor import TestExecutor

    assert "user_api_key" not in inspect.getsource(TestExecutor._execute_call)
