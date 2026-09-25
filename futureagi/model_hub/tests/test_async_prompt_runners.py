import sys
import types
from types import SimpleNamespace

import pytest

from tfc.constants.api_calls import APICallStatusChoices


def _sync_to_async(func):
    async def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class _UsageEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _install_usage_modules(monkeypatch, emitted):
    modules = {
        "ee": types.ModuleType("ee"),
        "ee.usage": types.ModuleType("ee.usage"),
        "ee.usage.schemas": types.ModuleType("ee.usage.schemas"),
        "ee.usage.services": types.ModuleType("ee.usage.services"),
        "ee.usage.utils": types.ModuleType("ee.usage.utils"),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    event_types = types.ModuleType("ee.usage.schemas.event_types")
    event_types.BillingEventType = SimpleNamespace(
        AI_PROMPT_CREATION="ai_prompt_creation",
        AI_PROMPT_IMPROVEMENT="ai_prompt_improvement",
    )
    monkeypatch.setitem(sys.modules, "ee.usage.schemas.event_types", event_types)

    events = types.ModuleType("ee.usage.schemas.events")
    events.UsageEvent = _UsageEvent
    monkeypatch.setitem(sys.modules, "ee.usage.schemas.events", events)

    config = types.ModuleType("ee.usage.services.config")
    config.BillingConfig = SimpleNamespace(
        get=lambda: SimpleNamespace(calculate_ai_credits=lambda cost: cost * 100)
    )
    monkeypatch.setitem(sys.modules, "ee.usage.services.config", config)

    emitter = types.ModuleType("ee.usage.services.emitter")
    emitter.emit = emitted.append
    monkeypatch.setitem(sys.modules, "ee.usage.services.emitter", emitter)

    properties = types.ModuleType("ee.usage.utils.event_properties")
    properties.llm_usage_properties = lambda generator: {"model": "fake-model"}
    monkeypatch.setitem(sys.modules, "ee.usage.utils.event_properties", properties)


@pytest.mark.asyncio
async def test_improve_prompt_insufficient_credits_returns_before_generator(
    monkeypatch,
):
    from model_hub.utils import async_improve_prompt_runner as runner

    class FakePromptGenerator:
        called = False

        async def _improve_prompt_async(self, **kwargs):
            FakePromptGenerator.called = True

    class FakeWsManager:
        def __init__(self):
            self.errors = []

        async def send_improve_prompt_error_message(self, **kwargs):
            self.errors.append(kwargs)

    monkeypatch.setattr(runner, "database_sync_to_async", _sync_to_async)
    monkeypatch.setattr(runner, "close_old_connections", lambda: None)
    monkeypatch.setattr(runner.Organization.objects, "get", lambda id: object())
    monkeypatch.setattr(runner, "PromptGenerator", FakePromptGenerator)
    monkeypatch.setattr(runner, "count_text_tokens", lambda text: len(text))
    monkeypatch.setattr(runner, "log_and_deduct_cost_for_api_request", lambda *args, **kwargs: None)

    ws_manager = FakeWsManager()

    await runner.improve_prompt_async(
        original_prompt="Original prompt",
        improvement_suggestions="Make it clearer",
        examples=[],
        improve_id="improve-1",
        organization_id="org-1",
        user_id="user-1",
        uid="uid-1",
        workspace=object(),
        ws_manager=ws_manager,
    )

    assert ws_manager.errors == [
        {"improve_id": "improve-1", "error": "Insufficient credits"}
    ]
    assert FakePromptGenerator.called is False


@pytest.mark.asyncio
async def test_generate_prompt_emits_usage_event_after_success(monkeypatch):
    from model_hub.utils import async_generate_prompt_runner as runner

    emitted = []
    _install_usage_modules(monkeypatch, emitted)

    class FakePromptGenerator:
        def __init__(self):
            self.llm = SimpleNamespace(cost={"total_cost": 1.25})

        async def _generate_prompt_async(self, **kwargs):
            return None

    monkeypatch.setattr(runner, "database_sync_to_async", _sync_to_async)
    monkeypatch.setattr(runner, "close_old_connections", lambda: None)
    monkeypatch.setattr(runner.Organization.objects, "get", lambda id: object())
    monkeypatch.setattr(runner, "PromptGenerator", FakePromptGenerator)
    monkeypatch.setattr(runner, "count_text_tokens", lambda text: len(text))
    monkeypatch.setattr(
        runner,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: SimpleNamespace(
            status=APICallStatusChoices.PROCESSING.value
        ),
    )

    await runner.generate_prompt_async(
        description="Generate a concise support prompt",
        generation_id="generation-1",
        organization_id="org-1",
        user_id="user-1",
        uid="uid-1",
        workspace=object(),
        ws_manager=SimpleNamespace(),
    )

    assert len(emitted) == 1
    assert emitted[0].org_id == "org-1"
    assert emitted[0].event_type == "ai_prompt_creation"
    assert emitted[0].amount == 125
    assert emitted[0].properties == {
        "source": "run_prompt_gen",
        "source_id": "generation-1",
        "raw_cost_usd": "1.25",
        "model": "fake-model",
    }
