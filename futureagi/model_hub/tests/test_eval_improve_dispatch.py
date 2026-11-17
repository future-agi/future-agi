"""Improvement deadlines and multimodal resolution at the real dispatch seam."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.services import eval_improve
from model_hub.tests.test_model_route import _binding, mr
from model_hub.tests.test_model_route_support import (
    MESSAGES, OPENAI_CHAT_MODEL, FakeGatewayClient, chat_response, eligible_port,
)

pytestmark = pytest.mark.django_db(transaction=True)


def bounded_completion(mr, binding, deadline):
    llm = SimpleNamespace(
        _managed_route_active=lambda: True,
        _managed_route_completion=lambda payload, **kwargs: mr.managed_chat_completion(
            binding, payload, **kwargs
        ),
    )
    return eval_improve._bound_model(llm, deadline)._managed_route_completion


@pytest.mark.parametrize("stage", ["before_send", "authorize", "request_headers"])
def test_expired_preparation_never_sends_a_provider_request(
    mr, organization, workspace, monkeypatch, stage,
):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    client = FakeGatewayClient()
    port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
    binding = _binding(mr, port, organization, workspace)
    original = getattr(port, stage)
    released = threading.Event()
    release = port.release

    def release_route(route):
        release(route)
        released.set()

    monkeypatch.setattr(port, "release", release_route)

    def consume_budget(*args, **kwargs):
        value = original(*args, **kwargs)
        now[0] = 131.0
        return value

    monkeypatch.setattr(port, stage, consume_budget)
    complete = bounded_completion(mr, binding, 130.0)
    with pytest.raises(eval_improve.ImproveDeadlineExceeded) as error:
        complete({"model": OPENAI_CHAT_MODEL, "messages": MESSAGES})
    assert error.value.status_code == 504
    assert client.calls == []
    if port.authorize_calls:
        assert released.wait(3), "Every unsent admission must be released"
    assert len(port.release_calls) == len(port.authorize_calls)


def test_provider_timeout_uses_dispatch_budget_not_pre_admission_budget(
    mr, organization, workspace, monkeypatch,
):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    client = FakeGatewayClient()
    port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
    binding = _binding(mr, port, organization, workspace)
    authorize = port.authorize

    def admit(**kwargs):
        route = authorize(**kwargs)
        now[0] += 12
        return route

    monkeypatch.setattr(port, "authorize", admit)
    complete = bounded_completion(mr, binding, 130.0)
    complete({"model": OPENAI_CHAT_MODEL, "messages": MESSAGES, "timeout": 90})
    assert len(client.calls) == 1
    assert client.calls[0]["options"][-1] == {"timeout": 18.0, "max_retries": 0}
    assert client.calls[0]["kwargs"]["timeout"] == 18.0
    assert port.release_calls == []


def test_blocked_admission_returns_deadline_and_releases_late_admission(
    mr, organization, workspace, monkeypatch,
):
    client = FakeGatewayClient()
    port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
    binding = _binding(mr, port, organization, workspace)
    unblock = threading.Event()
    admitted = threading.Event()
    released = threading.Event()
    authorize = port.authorize
    release = port.release

    def admit(**kwargs):
        unblock.wait(2)
        route = authorize(**kwargs)
        admitted.set()
        return route

    def release_route(route):
        release(route)
        released.set()

    monkeypatch.setattr(port, "authorize", admit)
    monkeypatch.setattr(port, "release", release_route)
    # Meter construction is outside the preparation deadline under test.
    from model_hub.services import credit_ledger_port as ledger
    monkeypatch.setattr(mr, "_transport_meter", lambda binding: None)
    call = SimpleNamespace(
        dispatched=False, route=SimpleNamespace(route=binding), request_id="deadline-test",
        envelope=SimpleNamespace(
            organization_id=binding.organization_id, workspace_id=binding.workspace_id,
        ),
    )
    monkeypatch.setattr(ledger, "current_managed_call", lambda: call)
    monkeypatch.setattr(ledger, "require_managed_hold", lambda **kwargs: call)
    complete = bounded_completion(mr, binding, time.monotonic() + 0.1)
    try:
        with pytest.raises(eval_improve.ImproveDeadlineExceeded):
            complete({"model": OPENAI_CHAT_MODEL, "messages": MESSAGES})
        assert not admitted.is_set(), "Request must return while admission is still blocked"
        assert client.calls == []
    finally:
        unblock.set()
        assert released.wait(3), "A late admission must be released without sending"
    assert len(port.release_calls) == 1
    assert client.calls == []


def test_saturated_deadline_preparations_refuse_without_starting_threads_and_recover(
    mr, monkeypatch,
):
    unblock = threading.Event()
    entered = []
    finished = []
    released = []
    lock = threading.Lock()

    def prepare(binding, request, **kwargs):
        with lock:
            entered.append(binding)
        try:
            assert unblock.wait(5), "Test must unblock every preparation"
            return None, binding, {}
        finally:
            with lock:
                finished.append(binding)

    monkeypatch.setattr(mr, "_prepare_dispatch", prepare)
    monkeypatch.setattr(mr, "release_managed_request", released.append)
    try:
        for index in range(4):
            with pytest.raises(TimeoutError, match="tool completion deadline exceeded"):
                mr._prepare_dispatch_before_deadline(index, {}, time.monotonic() + 0.2)
        assert entered == list(range(4))
        assert finished == []
        start = Mock(wraps=threading.Thread.start)
        # Existing workers are blocked; a rejected call must not start a fifth.
        monkeypatch.setattr(threading.Thread, "start", lambda thread: start(thread))
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="tool completion deadline exceeded"):
            mr._prepare_dispatch_before_deadline(4, {}, started + 0.5)
        elapsed = time.monotonic() - started
        start.assert_not_called()
        assert elapsed < 0.25, "Saturation must refuse, not wait for the deadline"
        assert entered == list(range(4)), "No excess preparation may be queued"
    finally:
        unblock.set()
        until = time.monotonic() + 3
        while len(released) < len(entered) and time.monotonic() < until:
            time.sleep(0.01)
    assert sorted(finished) == list(range(4))
    assert sorted(released) == list(range(4))
    # Slots cover worker cleanup too, not merely the caller's timed-out wait.
    acquired = []
    until = time.monotonic() + 3
    try:
        while len(acquired) < 4 and time.monotonic() < until:
            if mr._DEADLINE_PREPARATION_SLOTS.acquire(blocking=False):
                acquired.append(True)
            else:
                time.sleep(0.01)
        assert len(acquired) == 4, "In-flight count must return to zero"
    finally:
        for _ in acquired:
            mr._DEADLINE_PREPARATION_SLOTS.release()
    assert mr._prepare_dispatch_before_deadline(5, {}, time.monotonic() + 3) == (
        None, 5, {},
    )


def test_expired_deadline_does_not_construct_credit_meter(
    mr, organization, workspace, monkeypatch,
):
    client = FakeGatewayClient()
    port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
    binding = _binding(mr, port, organization, workspace)
    meter = Mock(return_value=None)
    monkeypatch.setattr(mr, "_transport_meter", meter)
    with pytest.raises(TimeoutError, match="tool completion deadline exceeded"):
        mr.managed_chat_completion(
            binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES},
            deadline_monotonic=time.monotonic() - 1,
        )
    meter.assert_not_called()
    assert port.authorize_calls == []
    assert client.calls == []


@pytest.mark.parametrize("stream", [False, True])
def test_deadline_expiring_during_meter_construction_never_enters_meter(
    mr, organization, workspace, monkeypatch, stream,
):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    client = FakeGatewayClient()
    port = eligible_port(mr, organization, {OPENAI_CHAT_MODEL}, client=client)
    binding = _binding(mr, port, organization, workspace)
    from unittest.mock import MagicMock
    meter = MagicMock()

    def construct(binding):
        now[0] = 131.0
        return meter

    monkeypatch.setattr(mr, "_transport_meter", construct)
    with pytest.raises(TimeoutError, match="tool completion deadline exceeded"):
        response = mr.managed_chat_completion(
            binding, {"model": OPENAI_CHAT_MODEL, "messages": MESSAGES, "stream": stream},
            deadline_monotonic=130.0,
        )
        if stream:
            list(response)
    meter.metered.assert_not_called()
    assert port.authorize_calls == []
    assert client.calls == []


def turing_judge(mr, organization, workspace, monkeypatch, model, allowed):
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
        CustomPromptEvaluator,
    )
    from ee.turing.client import TuringClient

    port = eligible_port(mr, organization, allowed)
    with mr.managed_routing_port(port):
        binding = mr.bind_managed_route(
            organization_id=organization.id, workspace_id=workspace.id,
            model_name=model, provider="turing", operation=mr.OP_CUSTOM_PROMPT_EVAL,
        )
    judge = CustomPromptEvaluator(
        rule_prompt="Check {{output}}.", model=model, provider="turing", model_route=binding,
    )
    transport = Mock(return_value=chat_response(
        '{"result": "Pass", "explanation": "ok"}', model=model,
    ))
    monkeypatch.setattr(judge, "_managed_route_completion", transport)
    legacy = Mock(side_effect=AssertionError("Legacy unscoped transport must not run"))
    monkeypatch.setattr(TuringClient, "chat_completion", legacy)
    eval_improve._bound_model(judge, time.monotonic() + 30)
    return judge, transport, legacy, port


@pytest.mark.parametrize("modality", ["audio", "pdf"])
def test_bounded_turing_judge_resolves_media_and_restores_text_model(
    mr, organization, workspace, monkeypatch, modality,
):
    judge, transport, legacy, port = turing_judge(
        mr, organization, workspace, monkeypatch, "turing_large",
        {"turing_large", "turing_large_xl"},
    )
    detect = Mock(return_value=([{"type": "text", "text": "media fixture"}], {"output": modality}))
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator.detect_and_build_media_blocks",
        detect,
    )
    result = judge.run(output="fixture", required_keys=["output"])
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert transport.call_args.args[0]["model"] == "turing_large_xl"
    assert judge.model_route.model_name == "turing_large_xl"
    assert judge.model_route.organization_id == str(organization.id)
    assert judge.model_route.workspace_id == str(workspace.id)
    assert judge.model_route.port is port
    assert "deadline_monotonic" in transport.call_args.kwargs
    detect.return_value = ([], {})
    judge.run(output="plain text", required_keys=["output"])
    assert transport.call_args.args[0]["model"] == "turing_large"
    assert judge.model_route.model_name == "turing_large"
    assert transport.call_count == 2
    legacy.assert_not_called()


@pytest.mark.parametrize("model,allowed,error", [
    ("turing_small", {"turing_small"}, "does not support"),
    ("turing_large", {"turing_large"}, "API key not configured"),
])
def test_bounded_turing_judge_refuses_unsupported_or_unscoped_upgrade(
    mr, organization, workspace, monkeypatch, model, allowed, error,
):
    judge, transport, legacy, port = turing_judge(
        mr, organization, workspace, monkeypatch, model, allowed,
    )
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator.detect_and_build_media_blocks",
        lambda **kwargs: ([{"type": "text", "text": "media fixture"}], {"output": "audio"}),
    )
    with pytest.raises(ValueError, match=error):
        judge._evaluate(output="fixture", required_keys=["output"])
    transport.assert_not_called()
    legacy.assert_not_called()
    assert port.authorize_calls == []
