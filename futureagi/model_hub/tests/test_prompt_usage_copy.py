"""Execute both production runners with no DB, providers, or usage-service I/O."""

import asyncio
import importlib.util
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

MESSAGE = (
    "This request could not be completed. Try again later; "
    "if it keeps happening, contact your administrator."
)
pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network disabled for prompt usage-copy tests")

    for name in [
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
    ]:
        monkeypatch.setattr(socket, name, blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture(params=["generate", "improve"])
def runner(request, monkeypatch):
    kind = request.param
    check = Mock(return_value=SimpleNamespace(allowed=True))
    generator = SimpleNamespace(
        _generate_prompt_async=AsyncMock(),
        _improve_prompt_async=AsyncMock(),
    )
    factory = Mock(return_value=generator)
    log = Mock()

    def fake_module(name, **exports):
        module = ModuleType(name)
        module.__dict__.update(exports)
        monkeypatch.setitem(sys.modules, name, module)

    fake_module(
        "ee.agenthub.prompt_generate_agent.prompt_generate", PromptGenerator=factory
    )
    fake_module(
        "ee.usage.utils.usage_entries",
        count_text_tokens=Mock(return_value=3),
        log_and_deduct_cost_for_api_request=log,
    )
    fake_module("ee.usage.services.metering", check_usage=check)
    fake_module(
        "ee.usage.schemas.event_types",
        BillingEventType=SimpleNamespace(
            AI_PROMPT_CREATION="creation", AI_PROMPT_IMPROVEMENT="improvement"
        ),
    )
    fake_module("ee.usage.schemas.events", UsageEvent=Mock())
    fake_module("ee.usage.services.emitter", emit=Mock())
    fake_module(
        "ee.usage.services.config",
        BillingConfig=SimpleNamespace(
            get=lambda: SimpleNamespace(calculate_ai_credits=lambda cost: 0)
        ),
    )
    fake_module("ee.usage.utils.event_properties", llm_usage_properties=lambda obj: {})

    # A fresh module avoids leaving our patched collaborators in the module cache
    # for existing suites. The whole production file executes, not an AST slice.
    path = Path(__file__).parents[1] / "utils" / f"async_{kind}_prompt_runner.py"
    spec = importlib.util.spec_from_file_location(f"_usage_copy_{kind}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def call_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    module.database_sync_to_async = lambda function: (
        lambda *args, **kwargs: call_sync(function, *args, **kwargs)
    )
    organization = object()
    module.Organization = SimpleNamespace(
        objects=SimpleNamespace(get=Mock(return_value=organization)),
        DoesNotExist=LookupError,
    )
    module.close_old_connections = Mock()
    module.logger = Mock()
    ws = SimpleNamespace(
        send_generate_prompt_error_message=AsyncMock(),
        send_improve_prompt_error_message=AsyncMock(),
    )
    id_key = "generation_id" if kind == "generate" else "improve_id"
    arguments = dict(
        organization_id="org",
        user_id="user",
        uid="uid",
        workspace="workspace",
        ws_manager=ws,
        **{id_key: "request"},
    )
    if kind == "generate":
        arguments["description"] = "Describe a task"
    else:
        arguments.update(
            original_prompt="A task", improvement_suggestions="Be clear", examples=[]
        )
    return SimpleNamespace(
        module=module,
        check=check,
        log=log,
        factory=factory,
        run=lambda: asyncio.run(getattr(module, f"{kind}_prompt_async")(**arguments)),
        send=getattr(ws, f"send_{kind}_prompt_error_message"),
        execute=getattr(generator, f"_{kind}_prompt_async"),
        id_key=id_key,
    )


@pytest.mark.parametrize("status", [None, "rate_limited", "insufficient_credits"])
def test_refused_usage_or_logger_failure_uses_proposed_neutral_copy(runner, status):
    runner.log.return_value = None if status is None else SimpleNamespace(status=status)
    runner.run()
    runner.send.assert_awaited_once_with(**{runner.id_key: "request", "error": MESSAGE})
    runner.execute.assert_not_awaited()
    runner.log.assert_called_once()
    assert runner.module.close_old_connections.call_count == 2
    runner.module.logger.exception.assert_not_called()


def test_processing_still_starts_the_prompt(runner):
    runner.log.return_value = SimpleNamespace(
        status=runner.module.APICallStatusChoices.PROCESSING.value
    )
    runner.run()
    runner.execute.assert_awaited_once()
    runner.send.assert_not_awaited()
    assert runner.module.close_old_connections.call_count == 2


def test_optional_legacy_usage_logger_still_allows_execution(runner):
    runner.module.log_and_deduct_cost_for_api_request = None
    runner.run()
    runner.execute.assert_awaited_once()
    runner.send.assert_not_awaited()
    runner.log.assert_not_called()


@pytest.mark.parametrize("reason", ["A budget is paused", ""])
def test_precheck_refusal_keeps_its_existing_text_and_short_circuit(runner, reason):
    runner.check.return_value = SimpleNamespace(allowed=False, reason=reason)
    runner.run()
    runner.send.assert_awaited_once_with(
        **{runner.id_key: "request", "error": reason or "Usage limit exceeded"}
    )
    runner.factory.assert_not_called()
    runner.log.assert_not_called()
    runner.execute.assert_not_awaited()
