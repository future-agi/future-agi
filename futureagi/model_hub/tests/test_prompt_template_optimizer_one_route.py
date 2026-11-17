"""A Prompt Template run makes every model call on one route of its own.

RED at 6f2e6991:
- With neither a stored key nor a managed route, the runner passes no client
  and PromptOptimizer falls back to the deployment's keys; the criteria call
  falls back the same way through platform_llm.
- The criteria calls are made through platform_llm (criteria_helper), not
  the run's client, so a BYOK run's criteria call is held on Future AGI
  credits.
- Every hold's purpose is criteria_helper.
- An error after settled calls is re-raised for a Temporal retry, which
  would hold again for calls already paid for.

GREEN (tasks/prompt_template_optimizer.py _run_client):
- With neither a stored key nor a managed route, the run is refused before
  any model call with the text the key lookup has always given.
- The criteria calls and the loop share the run's one client. A BYOK run
  places no hold at all.
- Every hold's purpose is run_prompt: the meter takes it from the client's
  binding, so it is fixed per client.
- An error after a settled call fails the run without a retry; one before
  any settled call still retries.

A line test's expected value changes (r4, on 81a91c25):
- A call sent on the organization's own key counts as well, so a BYOK run
  with an error after its calls also fails without a retry.
- The reason the person reads is a fixed sentence in the style of
  CHAT_STOPPED (simulate/services/chat_sim.py), and the error's own text goes
  to the server log, never into the stored reason.

A line test's expected value changes (r5, on 15514f0d):
- A stored key no longer applies to this helper model. An LLM given a key
  sends with the deployment's credentials, unmetered, so the run is always
  on the managed route (a hold and settle per call) or refused before any
  call. The two BYOK tests now expect holds on every call and no direct
  provider call, a CustomAIModel named like the helper model included.
- The no-route refusal shows NO_CREDITS_ROUTE (a key would not help). (r6:
  only where credits can be turned on; a self-hosted deployment, by
  tfc.ee_gating.is_oss, shows CREDITS_ONLY with no Billing pointer.)
- An error before the first call fails the run with a fixed reason and no
  retry; only a transient database error is left to retry
  (test_prompt_template_optimizer_never_platform_paid.py).

Doubles as in test_prompt_template_optimizer_loop_metered.py. No test reaches
a provider, RED or GREEN.
"""

from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import pytest
from temporalio.exceptions import ApplicationError

from agentic_eval.core.llm import platform_model
from agentic_eval.core.llm.llm import LLM
from ee.agenthub.prompt_optimizer_agent import agent_task_v2
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.services import model_route
from model_hub.tasks import agent as agent_tasks
from model_hub.tasks import prompt_template_optimizer as template_task
from model_hub.tests.test_model_route_support import (
    BYOK_FIXTURE_VALUE,
    OPENAI_CHAT_MODEL,
    FakeGatewayClient,
    eligible_port,
    provider_tripwires,
)
from model_hub.tests.test_prompt_template_optimization_runs import (
    CRITERIA,
    CRITERIA_ANSWER,
    OPTIMISED,
    _ai_model,
    _metric,
    _run,
)
from model_hub.tests.test_prompt_template_optimizer_loop_metered import (
    RUNNER_DATA,
    CallingOptimizer,
    InlineExecutor,
    _ops,
    _priced,
    _stored_criteria_run,
)
from tfc import ee_gating

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

# A line test's expected value changes: with no managed route the run shows
# the credits sentence, since a stored key is not used for this model.
NO_CREDITS_ROUTE = (
    "The optimization runs on Future AGI credits, which are not available to "
    "this organization for its model, so it did not run. Check Future AGI "
    "credits on the Billing page, then run it again."
)
# On a self-hosted deployment, where Future AGI credits cannot be turned on,
# the no-route reason has no Billing pointer.
CREDITS_ONLY = (
    "The optimization currently runs on Future AGI credits only, which this "
    "deployment does not offer, so it did not run."
)
COULD_NOT_START = "The optimization could not start because of an error. Run it again."
# A line test's expected value changes: the stored reason is a fixed sentence;
# the error's own text ("score store unavailable") goes to the server log only.
STOPPED_AFTER_MODEL_CALLS = (
    "The optimization stopped because of an error after it had started "
    "calling the model, so it was not retried. Run it again to start over."
)
RAW_ERROR = "score store unavailable"


@pytest.fixture
def managed(monkeypatch):
    """As in test_prompt_template_optimizer_loop_metered.py: no gateway port
    until a test installs one, the meter's safety state reset, the platform
    features on the OpenAI chat model the gateway doubles price, a fixed hold
    estimate and no ClickHouse usage log."""
    credit_ledger.reset_meter_safety()
    models = {
        feature: (
            replace(
                config,
                provider="openai",
                model_name=OPENAI_CHAT_MODEL,
                vertex_location=None,
            ),
            temperature,
        )
        for feature, (config, temperature) in platform_model.PLATFORM_MODELS.items()
    }
    monkeypatch.setattr(platform_model, "PLATFORM_MODELS", MappingProxyType(models))
    monkeypatch.setattr(
        credit_ledger, "estimate_call_cost", lambda *args, **kwargs: Decimal("0.10")
    )
    with (
        patch("agentic_eval.core.llm.llm.log_to_clickhouse"),
        model_route.managed_routing_port(None),
    ):
        yield model_route
    credit_ledger.reset_meter_safety()


@pytest.fixture
def unrouted(monkeypatch):
    """A criteria helper LLM built without a route would reach a provider on
    the deployment's keys: it is recorded and answers here instead."""
    built = []

    def criteria_llm(**kwargs):
        if kwargs.get("model_route") is None:
            built.append(kwargs)
            return SimpleNamespace(
                _get_completion_content=lambda *args, **inner: CRITERIA_ANSWER
            )
        return LLM(**kwargs)

    monkeypatch.setattr(agent_tasks, "LLM", criteria_llm)
    return built


# 1. no key-less fallback -----------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_run_with_no_key_and_no_managed_route_is_refused_before_any_call(
    organization, workspace, managed, unrouted, monkeypatch
):
    # A line test's expected value changes: the reason now depends on the
    # deployment (tfc.ee_gating.is_oss). This test pins a deployment that can
    # turn Future AGI credits on (Cloud or a licensed install), whose reason
    # points to the Billing page.
    monkeypatch.setattr(ee_gating, "is_oss", lambda: False)
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    # A gateway port that admits no model for this organization: no binding.
    port = eligible_port(managed, organization, set(), client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer()
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as refused:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert refused.value.message == NO_CREDITS_ROUTE
    assert refused.value.non_retryable is True
    assert gateway.calls == []
    assert port.authorize_calls == []
    assert ledger.calls == []
    assert unrouted == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == NO_CREDITS_ROUTE
    assert run.criteria_breakdown == []
    assert run.optimized_k_prompts is None


@pytest.mark.django_db(transaction=True)
def test_a_self_hosted_run_is_refused_as_credits_only_before_any_call(
    organization, workspace, managed, unrouted, monkeypatch
):
    # A self-hosted deployment with no EE license and not Cloud
    # (tfc.ee_gating.is_oss) and no gateway port registered: Future AGI
    # credits cannot be turned on here, so the reason points nowhere.
    monkeypatch.setattr(ee_gating, "is_oss", lambda: True)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer()
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    with credit_ledger.use_credit_ledger(ledger):
        with pytest.raises(ApplicationError) as refused:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert refused.value.message == CREDITS_ONLY
    assert "Billing" not in refused.value.message
    assert refused.value.non_retryable is True
    assert ledger.calls == []
    assert unrouted == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == CREDITS_ONLY
    assert run.criteria_breakdown == []
    assert run.optimized_k_prompts is None


# 2. one route per run ---------------------------------------------------------


def _store_keys_named_like_the_helper_model(organization, workspace, user):
    """A stored provider key and a CustomAIModel whose user_model_id is the
    criteria helper's model string (the managed fixture's OPENAI_CHAT_MODEL):
    the stored-credential lookup would match either one."""
    ApiKey.no_workspace_objects.create(
        provider="openai",
        organization=organization,
        workspace=workspace,
        key=BYOK_FIXTURE_VALUE,
        user=user,
    )
    CustomAIModel.no_workspace_objects.create(
        user_model_id=OPENAI_CHAT_MODEL,
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=None,
        key_config={"api_key": BYOK_FIXTURE_VALUE},
    )


@pytest.mark.django_db(transaction=True)
def test_a_stored_key_and_a_custom_model_named_like_the_helper_model_stay_metered(
    organization, workspace, user, managed, monkeypatch
):
    # A line test's expected value changes: this was
    # test_a_byok_run_places_no_hold_criteria_included, which expected a
    # stored key to run every call with no hold. An LLM given a key sends with
    # the deployment's credentials, unmetered, so the run stays on its managed
    # route: a hold and settle per call, and never a direct provider call.
    tripped = provider_tripwires(monkeypatch)
    _store_keys_named_like_the_helper_model(organization, workspace, user)
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    # At the outbound boundary: nothing reached litellm or built a provider
    # SDK client, so no call went out without a hold, on any key.
    assert tripped == []
    (built,) = optimizer.built
    assert built["client"].api_key is None
    assert managed.is_managed_route_binding(built["client"].model_route)
    # One criteria call and the loop's two, each held and settled on credits.
    assert _ops(ledger) == ["hold", "settle"] * 3
    assert {hold.purpose for hold in ledger.holds.values()} == {managed.OP_RUN_PROMPT}
    assert len(gateway.calls) == 3
    assert {sent["kwargs"]["extra_headers"]["X-Org-Id"] for sent in gateway.calls} == {
        str(organization.id)
    }
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.criteria_breakdown == CRITERIA
    assert run.optimized_k_prompts == [OPTIMISED]


# 3. the purpose of every hold -------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_every_hold_of_a_managed_run_has_the_optimizers_purpose(
    organization, workspace, managed, monkeypatch
):
    gateway = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=2)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    # The criteria call and the loop's two calls, each held and settled.
    assert _ops(ledger) == ["hold", "settle"] * 3
    assert {hold.purpose for hold in ledger.holds.values()} == {managed.OP_RUN_PROMPT}
    assert [call["operation"] for call in port.authorize_calls] == [
        managed.OP_RUN_PROMPT
    ] * 3
    assert len(gateway.calls) == 3
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.criteria_breakdown == CRITERIA


# 4. retries ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_loop_error_after_settled_calls_fails_the_run_without_a_retry(
    organization, workspace, managed
):
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("2"))
    run = _stored_criteria_run(organization, workspace)

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM") as own_client,
        patch.object(agent_task_v2, "ThreadPoolExecutor", InlineExecutor),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_calculate_score",
            side_effect=[0.0, 0.0, RuntimeError(RAW_ERROR)],
        ),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_get_evaluation_feedback",
            return_value=("", 0.0),
        ),
    ):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    own_client.assert_not_called()
    # A line test's expected value changes: the fixed sentence, not the error.
    assert failed.value.message == STOPPED_AFTER_MODEL_CALLS
    assert RAW_ERROR not in failed.value.message
    assert failed.value.non_retryable is True
    # The eight calls of the first iteration, all settled; nothing after.
    assert _ops(ledger) == ["hold", "settle"] * 8
    assert len(gateway.calls) == 8
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == STOPPED_AFTER_MODEL_CALLS
    assert RAW_ERROR not in run.error_message
    assert run.optimized_k_prompts is None


@pytest.mark.django_db(transaction=True)
def test_a_loop_error_after_calls_with_a_stored_key_fails_the_run_without_a_retry(
    organization, workspace, user, managed, monkeypatch
):
    # A line test's expected value changes: this was
    # test_a_byok_loop_error_after_sent_calls_fails_the_run_without_a_retry,
    # whose calls ran on the stored key with no hold. A stored key is not used
    # for this model, so the calls are held and settled on credits, and the
    # error after them still ends the run with no retry.
    tripped = provider_tripwires(monkeypatch)
    _store_keys_named_like_the_helper_model(organization, workspace, user)
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("2"))
    run = _stored_criteria_run(organization, workspace)

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM") as own_client,
        patch.object(agent_task_v2, "ThreadPoolExecutor", InlineExecutor),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_calculate_score",
            side_effect=[0.0, 0.0, RuntimeError(RAW_ERROR)],
        ),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_get_evaluation_feedback",
            return_value=("", 0.0),
        ),
    ):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    own_client.assert_not_called()
    assert tripped == []
    assert failed.value.message == STOPPED_AFTER_MODEL_CALLS
    assert RAW_ERROR not in failed.value.message
    assert failed.value.non_retryable is True
    # The eight calls of the first iteration, each held and settled on
    # credits, then nothing.
    assert _ops(ledger) == ["hold", "settle"] * 8
    assert len(gateway.calls) == 8
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == STOPPED_AFTER_MODEL_CALLS
    assert RAW_ERROR not in run.error_message
    assert run.optimized_k_prompts is None


@pytest.mark.django_db(transaction=True)
def test_a_loop_error_before_any_call_fails_the_run_with_a_fixed_reason(
    organization, workspace, managed
):
    # A line test's expected value changes: this was
    # test_a_loop_error_before_any_settled_call_still_retries, which expected
    # the error re-raised for a retry with the run left RUNNING. A setup
    # error before the first call is deterministic, so the run is failed with
    # a fixed reason and no retry; only a transient database error is left to
    # retry (test_prompt_template_optimizer_never_platform_paid.py).
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("2"))
    run = _stored_criteria_run(organization, workspace)

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM"),
        patch.object(agent_task_v2, "ThreadPoolExecutor", InlineExecutor),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_calculate_score",
            side_effect=RuntimeError(RAW_ERROR),
        ),
    ):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert failed.value.message == COULD_NOT_START
    assert RAW_ERROR not in failed.value.message
    assert failed.value.non_retryable is True
    assert ledger.calls == []
    assert gateway.calls == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == COULD_NOT_START
