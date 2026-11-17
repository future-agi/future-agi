"""A failed dataset optimization run names its real cause.

DirectEvaluator runs its coroutines through _run_async. An error raised while
they run must not carry the "no running event loop" lookup as its context: the
model call's error handler picks the most telling exception in the chain, and
would show the person that lookup instead of the failure. No model is called;
every value here is synthetic.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ee.agenthub.fix_your_agent.direct_evaluator import (
    DirectEvaluator,
    DirectInferenceError,
    _run_async,
)

pytestmark = pytest.mark.unit

FAILURE = "synthetic failure"
LOOP_LOOKUP = "no running event loop"


def chain(error):
    """``error`` and every exception chained to it (cause and context)."""
    found = []
    pending = [error]
    while pending:
        item = pending.pop()
        if item is None or any(item is seen for seen in found):
            continue
        found.append(item)
        pending.extend([item.__cause__, item.__context__])
    return found


async def fail():
    raise ValueError(FAILURE)


async def answer():
    return 7, threading.get_ident()


class FailingModelHandler:
    """Stands in for the model handler RunPrompt builds: the model call fails
    with an error that is not a provider error."""

    def __init__(self, context):
        self.context = context

    async def execute_async(self, streaming=False):
        raise ValueError(FAILURE)


def test_a_coroutine_error_does_not_carry_the_event_loop_lookup():
    """(a) No loop is running: the coroutine's error reaches the caller
    without the loop lookup's RuntimeError chained to it."""
    with pytest.raises(ValueError, match=FAILURE) as raised:
        _run_async(fail())

    context = raised.value.__context__
    assert not (isinstance(context, RuntimeError) and "event loop" in str(context))
    assert not [
        item
        for item in chain(raised.value)
        if isinstance(item, RuntimeError) and "event loop" in str(item)
    ]


def test_a_failed_inference_names_its_real_cause(monkeypatch):
    """(b) The model call fails: the message the run shows the person names
    that failure, through RunPrompt's error handler, not the loop lookup."""
    from agentic_eval.core_evals.run_prompt import litellm_response

    manager = Mock()
    manager.get_provider.return_value = "openai"
    manager.get_api_key.return_value = "synthetic-fixture-key"
    monkeypatch.setattr(
        litellm_response, "LiteLLMModelManager", Mock(return_value=manager)
    )
    monkeypatch.setattr(litellm_response, "USE_NEW_RUNPROMPT_HANDLERS", True)
    monkeypatch.setattr(
        litellm_response.ModelHandlerFactory, "create_handler", FailingModelHandler
    )
    handler = Mock(wraps=litellm_response.handle_api_error)
    monkeypatch.setattr(litellm_response, "handle_api_error", handler)
    evaluator = DirectEvaluator(
        user_eval_configs=[],
        execution_model="gpt-4o",
        organization=SimpleNamespace(id="fixture-organization"),
        workspace=SimpleNamespace(id="fixture-workspace"),
    )

    with pytest.raises(DirectInferenceError) as raised:
        evaluator.evaluate([{"agent_prompt": "Answer the question", "input": {}}])

    handler.assert_called_once()
    message = str(raised.value)
    assert FAILURE in message
    assert LOOP_LOOKUP not in message


def test_run_async_returns_the_result_with_and_without_a_running_loop():
    """(c) The result comes back as before: in the caller's thread when no
    loop is running, and from the thread path inside a running loop."""
    caller = threading.get_ident()

    assert _run_async(answer()) == (7, caller)

    async def inside_a_running_loop():
        return _run_async(answer())

    value, thread = asyncio.run(inside_a_running_loop())
    assert value == 7
    assert thread != caller
