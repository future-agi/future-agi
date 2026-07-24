"""End-to-end coverage for ai_credits UsageEvent emission across eval paths.

Asserts the new-billing-system dual-write fires for span-, trace-, and
session-level evals with the right shape:
    - event_type resolved from custom_eval_config.model
    - amount = BillingConfig.calculate_ai_credits(actual_cost)
    - properties.{source, source_id, raw_cost_usd, target_type}
    - properties.{prompt_tokens, completion_tokens, total_tokens}

These three paths used to charge legacy billing only; this verifies the
new emit hits all of them.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ee.usage.services.config")

# Break the tracer.utils.eval ↔ model_hub.tasks import cycle for test-time
# imports — see test_eval_task_runtime.py for the rationale.
import model_hub.tasks  # noqa: F401

from evaluations.engine.runner import EvalResult


PROMPT_TOKENS = 100
COMPLETION_TOKENS = 50
TOTAL_TOKENS = PROMPT_TOKENS + COMPLETION_TOKENS
RAW_COST_USD = 0.12345


def _build_result():
    return EvalResult(
        value=True,
        data={"input": "x"},
        reason="ok",
        failure=None,
        runtime=0.01,
        model_used="turing_large",
        metrics=None,
        metadata={"stub": True},
        output_type="Pass/Fail",
        start_time=0.0,
        end_time=0.01,
        duration=0.01,
        cost={"total_cost": RAW_COST_USD},
        token_usage={
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": COMPLETION_TOKENS,
            "total_tokens": TOTAL_TOKENS,
        },
    )


@pytest.fixture
def patched_run_eval(monkeypatch):
    """Patch ``run_eval`` with a non-zero-cost EvalResult.

    Stronger variant of conftest's ``stub_run_eval`` so we can assert
    credits calculation produces a non-zero amount.

    Also zeroes ``BillingConfig.get_eval_per_run_fee`` so ``actual_cost`` in
    ``_emit_eval_billing`` collapses to ``RAW_COST_USD`` and the assertions
    below can compare against it directly.
    """

    def _stub(_request):
        return _build_result()

    monkeypatch.setattr("evaluations.engine.run_eval", _stub, raising=False)
    monkeypatch.setattr("evaluations.engine.runner.run_eval", _stub, raising=False)

    from ee.usage.services.config import BillingConfig

    monkeypatch.setattr(BillingConfig, "get_eval_per_run_fee", lambda self: 0)


@pytest.fixture
def captured_emit(monkeypatch):
    """Capture every UsageEvent passed to ``ee.usage.services.emitter.emit``.

    Mirrors the import path used inside the eval helpers (lazy import inside
    the dual-write try-block), so patching the source module is enough.
    """
    captured: list = []

    def _record(event):
        captured.append(event)

    monkeypatch.setattr("ee.usage.services.emitter.emit", _record)
    return captured


def _credit_event(captured):
    """Pick the ai_credits emit, ignoring tracing/observe events fired elsewhere."""
    from ee.usage.models.usage import APICallTypeChoices

    credit_event_types = {
        APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
        APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
        APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
        APICallTypeChoices.PROTECT_EVALUATOR.value,
        APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
    }
    matches = [e for e in captured if e.event_type in credit_event_types]
    assert matches, f"No ai_credits event emitted. Captured: {[e.event_type for e in captured]}"
    assert len(matches) == 1, f"Expected one ai_credits event, got {len(matches)}"
    return matches[0]


def _assert_token_properties(props):
    assert props["prompt_tokens"] == PROMPT_TOKENS
    assert props["completion_tokens"] == COMPLETION_TOKENS
    assert props["total_tokens"] == TOTAL_TOKENS
    assert props["raw_cost_usd"] == str(RAW_COST_USD)


@pytest.mark.django_db
def test_span_eval_emits_ai_credits_with_tokens(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    custom_eval_config,
    patched_run_eval,
    stub_cost_log,
    captured_emit,
):
    from ee.usage.models.usage import APICallTypeChoices
    from ee.usage.services.config import BillingConfig
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    _execute_evaluation(
        observation_span_id=observation_span.id,
        custom_eval_config_id=custom_eval_config.id,
        eval_task_id=None,
        type=OBSERVE,
        run_params={"input": "hello", "output": "world"},
    )

    event = _credit_event(captured_emit)
    expected_credits = BillingConfig.get().calculate_ai_credits(RAW_COST_USD)

    assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
    assert event.org_id == str(organization.id)
    assert event.amount == expected_credits
    props = event.properties
    assert props["source"] == "tracer"
    assert props["source_id"] == str(custom_eval_config.eval_template.id)
    assert props["target_type"] == "span"
    _assert_token_properties(props)


@pytest.mark.django_db
def test_trace_eval_emits_ai_credits_with_tokens(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    custom_eval_config,
    patched_run_eval,
    stub_cost_log,
    captured_emit,
):
    from ee.usage.models.usage import APICallTypeChoices
    from ee.usage.services.config import BillingConfig
    from tracer.utils.eval import _execute_evaluation_for_trace

    _execute_evaluation_for_trace(
        trace=trace,
        anchor_span=observation_span,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params={"input": "hello", "output": "world"},
    )

    event = _credit_event(captured_emit)
    expected_credits = BillingConfig.get().calculate_ai_credits(RAW_COST_USD)

    assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
    assert event.org_id == str(organization.id)
    assert event.amount == expected_credits
    props = event.properties
    assert props["source"] == "tracer"
    assert props["source_id"] == str(custom_eval_config.eval_template.id)
    assert props["target_type"] == "trace"
    _assert_token_properties(props)


@pytest.mark.django_db
def test_session_eval_emits_ai_credits_with_tokens(
    organization,
    workspace,
    observe_project,
    trace_session,
    custom_eval_config,
    patched_run_eval,
    stub_cost_log,
    captured_emit,
):
    from ee.usage.models.usage import APICallTypeChoices
    from ee.usage.services.config import BillingConfig
    from tracer.utils.eval import _execute_evaluation_for_session

    _execute_evaluation_for_session(
        trace_session=trace_session,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params={"input": "hello", "output": "world"},
    )

    event = _credit_event(captured_emit)
    expected_credits = BillingConfig.get().calculate_ai_credits(RAW_COST_USD)

    assert event.event_type == APICallTypeChoices.TURING_LARGE_EVALUATOR.value
    assert event.org_id == str(organization.id)
    assert event.amount == expected_credits
    props = event.properties
    assert props["source"] == "tracer"
    assert props["source_id"] == str(custom_eval_config.eval_template.id)
    assert props["target_type"] == "session"
    _assert_token_properties(props)


@pytest.mark.django_db
def test_eval_failure_does_not_emit_ai_credits(
    organization,
    workspace,
    project,
    trace,
    observation_span,
    custom_eval_config,
    stub_cost_log,
    captured_emit,
    monkeypatch,
):
    """If the eval engine raises, we mark the api_call_log as ERROR and the
    dual-write emit must be skipped (no charge for a failed eval)."""
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    def _boom(_request):
        raise RuntimeError("engine_blew_up")

    monkeypatch.setattr("evaluations.engine.run_eval", _boom, raising=False)
    monkeypatch.setattr("evaluations.engine.runner.run_eval", _boom, raising=False)

    _execute_evaluation(
        observation_span_id=observation_span.id,
        custom_eval_config_id=custom_eval_config.id,
        eval_task_id=None,
        type=OBSERVE,
        run_params={"input": "hello", "output": "world"},
    )

    from ee.usage.models.usage import APICallTypeChoices

    credit_event_types = {
        APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
        APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
        APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
        APICallTypeChoices.PROTECT_EVALUATOR.value,
        APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
    }
    credit_events = [e for e in captured_emit if e.event_type in credit_event_types]
    assert credit_events == []
