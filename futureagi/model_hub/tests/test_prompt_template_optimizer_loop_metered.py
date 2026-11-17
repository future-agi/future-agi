"""A Prompt Template run's optimisation loop is metered on its organization.

RED at cf9180f2: PromptOptimizer builds its own LLM with no model route and
no organization (ee/agenthub/prompt_optimizer_agent/agent_task_v2.py:217-224),
so the loop and its scoring run unrecorded on the deployment's keys; the
scheduled scoring's criteria call carries no organization
(tasks/prompt_template_optimizer.py:278); and a criteria answer that does not
parse is re-raised for a Temporal retry, which would place a second hold.

GREEN: the runner builds the run's client on its organization for the
criteria helper's own model and passes it to PromptOptimizer. The managed
binding holds and settles each call on the credit ledger. (A line test's
expected value changes, r5: a stored provider key is no longer used for this
model, since an LLM given a key sends with the deployment's credentials.)
The first route or ledger refusal fails the run without a retry, and
no further model call is made. The scheduled scoring's criteria call runs on
the same route. A criteria answer that does not parse fails the run without
a retry. A re-run whose criteria are stored still meters the loop.

Doubles: the gateway port (MockGatewayManagedRouting), its client transport
(FakeGatewayClient), the credit ledger (MockCreditLedger), the ClickHouse
usage log, PromptOptimizer's scoring and score helpers where the loop's own
calls are under test, and an executor that runs the loop's rows on the
test's thread. Any client PromptOptimizer would build itself is a Mock, so no
test reaches a provider, RED or GREEN.
"""

from concurrent.futures import Future
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from temporalio.exceptions import ApplicationError

from agentic_eval.core.llm import platform_model
from agentic_eval.core.llm.llm import LLM
from ee.agenthub.prompt_optimizer_agent import agent_task_v2
from model_hub.models.api_key import ApiKey
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.services import model_route
from model_hub.tasks import agent as agent_tasks
from model_hub.tasks import prompt_template_optimizer as template_task
from model_hub.tests.test_model_route_support import (
    BYOK_FIXTURE_VALUE,
    MESSAGES,
    OPENAI_CHAT_MODEL,
    FakeGatewayClient,
    GatewayUsage,
    chat_response,
    eligible_port,
)
from model_hub.tests.test_prompt_template_optimization_runs import (
    CRITERIA,
    CRITERIA_ANSWER,
    OPTIMISED,
    TEMPLATE,
    _ai_model,
    _metric,
    _run,
)

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

CRITERIA_UNAVAILABLE = "The criteria for this run could not be built from its metrics."
# Ten traced rows: the loop trains on two of them, one row per mini-batch.
RUNNER_DATA = {
    "prompt_template": [TEMPLATE] * 10,
    "context": [None] * 10,
    "user_chat": ["What is the refund window?"] * 10,
    "model_chat": ["Thirty days."] * 10,
    "variables": [{"question": "What is the refund window?"}] * 10,
    "metadata": [{}] * 10,
}


def _priced(content):
    response = chat_response(content, model=OPENAI_CHAT_MODEL)
    response.usage = GatewayUsage(
        prompt_tokens=20, completion_tokens=10, total_tokens=30
    )
    return response


def _ops(ledger):
    return [entry[0] for entry in ledger.calls]


class InlineExecutor:
    """Runs each submitted row at once on the test's thread, so the loop's
    calls reach the test database's connection; the loop's code is unchanged."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future


class CallingOptimizer:
    """Stands in for PromptOptimizer: records how it was built and makes
    ``calls`` model calls through the client it was given, as its loop does."""

    def __init__(self, calls=0):
        self.built = []
        self.calls = calls

    def __call__(self, *args, **kwargs):
        self.built.append(kwargs)
        client = kwargs["client"]

        def optimise():
            for _ in range(self.calls):
                client._get_completion_content(MESSAGES)
            return [{"train_score": 90.0, "instruction_template": OPTIMISED}]

        return SimpleNamespace(get_optimized_prompt=optimise)


@pytest.fixture
def managed(monkeypatch):
    """The managed route as test_prompt_template_optimization_runs.py sets it
    up: no gateway port until a test installs one, the meter's safety state
    reset, the platform features on the OpenAI chat model the gateway doubles
    price, a fixed hold estimate and no ClickHouse usage log."""
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


def _stored_criteria_run(organization, workspace):
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))
    run.criteria_breakdown = list(CRITERIA)
    run.save()
    return run


# PromptOptimizer ------------------------------------------------------------


def test_prompt_optimizer_uses_a_given_client_and_builds_its_own_only_without_one():
    given = Mock(name="run client")
    with patch.object(agent_task_v2, "LLM") as own_client:
        with_client = agent_task_v2.PromptOptimizer(
            prompt=TEMPLATE,
            train_data=pd.DataFrame(RUNNER_DATA),
            criteria_breakdown=CRITERIA,
            client=given,
        )
        own_client.assert_not_called()
        without_client = agent_task_v2.PromptOptimizer(
            prompt=TEMPLATE,
            train_data=pd.DataFrame(RUNNER_DATA),
            criteria_breakdown=CRITERIA,
        )

    assert with_client.client is given
    config = agent_task_v2.ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
    own_client.assert_called_once_with(
        model_name=config.model_name,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        provider=config.provider,
        config=None,
        optimizer=True,
    )
    assert without_client.client is own_client.return_value


# (a) the loop ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_loop_holds_and_settles_each_call_on_the_organization_route(
    organization, workspace, managed
):
    client = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("2"))

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM") as own_client,
        patch.object(agent_task_v2, "ThreadPoolExecutor", InlineExecutor),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_calculate_score",
            side_effect=[0.0, 0.0, 1.0],
        ),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_get_evaluation_feedback",
            return_value=("", 0.0),
        ),
    ):
        run_client = template_task._run_client(organization, workspace)
        optimizer = agent_task_v2.PromptOptimizer(
            prompt=TEMPLATE,
            train_data=pd.DataFrame(RUNNER_DATA),
            criteria_breakdown=CRITERIA,
            client=run_client,
        )
        result = optimizer.get_optimized_prompt(k=1)

    own_client.assert_not_called()
    assert optimizer.client is run_client
    assert result == [
        {"iteration_num": 1, "train_score": 1.0, "instruction_template": OPTIMISED}
    ]
    # Two mini-batches, each: the row's chat, two gradient steps, one follow-up.
    assert _ops(ledger) == ["hold", "settle"] * 8
    assert len(ledger.holds) == 8
    for hold in ledger.holds.values():
        # A line test's expected value changes: the run's client is bound for
        # the ledger's operation for this optimizer, run_prompt.
        assert hold.purpose == managed.OP_RUN_PROMPT
        assert hold.org_id == str(organization.id)
        assert hold.state == "settled"
        assert hold.debits == 1
    assert len(client.calls) == 8
    admitted = [call["request_id"] for call in port.authorize_calls]
    assert len(set(admitted)) == 8
    assert {sent["kwargs"]["extra_headers"]["X-Org-Id"] for sent in client.calls} == {
        str(organization.id)
    }


@pytest.mark.django_db(transaction=True)
def test_a_stored_key_does_not_take_the_loop_off_its_managed_route(
    organization, workspace, user, managed, monkeypatch
):
    # A line test's expected value changes: this was
    # test_a_byok_organization_runs_the_loop_on_its_own_key_with_no_hold. An
    # LLM given a key sends with the deployment's credentials, unmetered, so a
    # stored key is not used for this helper model: the loop's client is the
    # managed binding, whose calls are held and settled one by one.
    ApiKey.no_workspace_objects.create(
        provider="openai",
        organization=organization,
        workspace=workspace,
        key=BYOK_FIXTURE_VALUE,
        user=user,
    )
    gateway = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=gateway)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    run = _stored_criteria_run(organization, workspace)

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(LLM, "_init_client") as own_key_client,
    ):
        template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    (built,) = optimizer.built
    run_client = built["client"]
    own_key_client.assert_not_called()
    assert run_client.api_key is None
    assert managed.is_managed_route_binding(run_client.model_route)
    assert run_client.model_name == OPENAI_CHAT_MODEL
    assert _ops(ledger) == ["hold", "settle"]
    assert [call["operation"] for call in port.authorize_calls] == [
        managed.OP_RUN_PROMPT
    ]
    assert len(gateway.calls) == 1
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.optimized_k_prompts == [OPTIMISED]


@pytest.mark.django_db(transaction=True)
def test_a_refusal_mid_loop_fails_the_run_without_a_retry_or_another_call(
    organization, workspace, managed
):
    client = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    # Four calls' holds fit; the fifth, the second mini-batch's row, is refused.
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("0.40"))
    run = _stored_criteria_run(organization, workspace)
    ledger_text = credit_ledger.CreditsExhaustedError().message

    with (
        credit_ledger.use_credit_ledger(ledger),
        managed.managed_routing_port(port),
        patch.object(agent_task_v2, "LLM") as own_client,
        patch.object(agent_task_v2, "ThreadPoolExecutor", InlineExecutor),
        patch.object(
            agent_task_v2.PromptOptimizer, "_calculate_score", return_value=0.0
        ),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "_get_evaluation_feedback",
            return_value=("", 0.0),
        ),
    ):
        with pytest.raises(ApplicationError) as refused:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    own_client.assert_not_called()
    assert refused.value.message == ledger_text
    assert refused.value.non_retryable is True
    assert _ops(ledger) == ["hold", "settle"] * 4 + ["hold"]
    assert len(client.calls) == 4
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == ledger_text
    assert run.optimized_k_prompts is None


# (b) a re-run with stored criteria -------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_rerun_with_stored_criteria_still_meters_the_loop(
    organization, workspace, managed, monkeypatch
):
    client = FakeGatewayClient(response=_priced(OPTIMISED))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer(calls=1)
    criteria_calls = []
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    monkeypatch.setattr(
        template_task,
        "create_criteria_text_prompt",
        lambda *args, **kwargs: criteria_calls.append((args, kwargs)),
    )
    run = _stored_criteria_run(organization, workspace)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert criteria_calls == []
    (built,) = optimizer.built
    assert built["criteria_breakdown"] == CRITERIA
    assert _ops(ledger) == ["hold", "settle"]
    (hold,) = ledger.holds.values()
    # A line test's expected value changes: run_prompt, the run's one purpose.
    assert hold.purpose == managed.OP_RUN_PROMPT
    assert hold.org_id == str(organization.id)
    assert hold.state == "settled"
    assert len(client.calls) == 1
    run.refresh_from_db()
    assert run.status == run.StatusType.COMPLETED
    assert run.optimized_k_prompts == [OPTIMISED]


# (c) the scheduled scoring's criteria call ----------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_scheduled_scoring_criteria_call_is_metered(
    organization, workspace, managed, monkeypatch
):
    client = FakeGatewayClient(response=_priced(CRITERIA_ANSWER))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    unmetered = []
    scored = []

    def criteria_llm(**kwargs):
        # A criteria helper LLM without a route would reach a provider on the
        # deployment's keys; record it and answer here instead.
        if kwargs.get("model_route") is None:
            unmetered.append(kwargs)
            return SimpleNamespace(
                _get_completion_content=lambda *args, **inner: CRITERIA_ANSWER
            )
        return LLM(**kwargs)

    class Scorer:
        def score_chat_history(self, **kwargs):
            scored.append(kwargs["criteria_breakdown"])
            return {"score": 0.7}

    monkeypatch.setattr(agent_tasks, "LLM", criteria_llm)
    monkeypatch.setattr(template_task, "EvalTextLLM", Scorer)
    monkeypatch.setattr(
        template_task, "get_updated_chat", lambda chat, prompt, *rest: chat
    )
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    run = _run(organization, workspace, ai_model, metric)
    inference = {
        "optim_obj_id": run.id,
        "conversation": [
            {
                "message": {
                    "author": {"role": "user"},
                    "content": {
                        "parts": ["What is the refund window?"],
                        "rag_info": {},
                    },
                }
            }
        ],
    }
    metric_data = {
        "id": str(metric.id),
        "text_prompt": metric.text_prompt,
        "criteria_breakdown": [],
        "model": {"model_type": ai_model.model_type},
    }

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        result = template_task.process_metric_and_prompts(
            inference, metric_data, OPTIMISED, 1
        )

    assert result == {f"optimized_{run.id}_{metric.id}_0_score": 0.7}
    assert scored == [CRITERIA]
    assert unmetered == []
    assert _ops(ledger) == ["hold", "settle"]
    (hold,) = ledger.holds.values()
    # A line test's expected value changes: the scheduled scoring's criteria
    # call is made with the run's client (_run_client), so run_prompt.
    assert hold.purpose == managed.OP_RUN_PROMPT
    assert hold.org_id == str(organization.id)
    (sent,) = client.calls
    assert sent["kwargs"]["extra_headers"]["X-Org-Id"] == str(organization.id)


# (d) a criteria answer that does not parse ----------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_criteria_answer_that_does_not_parse_fails_the_run_without_a_retry(
    organization, workspace, managed, monkeypatch
):
    client = FakeGatewayClient(response=_priced("These are not criteria."))
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = CallingOptimizer()
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    run = _run(organization, workspace, ai_model, _metric(ai_model))

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as failed:
            template_task.runner_topk_optimizer(RUNNER_DATA, [], run.id)

    assert failed.value.message == CRITERIA_UNAVAILABLE
    assert failed.value.non_retryable is True
    assert _ops(ledger) == ["hold", "settle"]
    assert len(client.calls) == 1
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == run.StatusType.FAILED
    assert run.error_message == CRITERIA_UNAVAILABLE
    assert run.criteria_breakdown == []
    assert run.optimized_k_prompts is None
