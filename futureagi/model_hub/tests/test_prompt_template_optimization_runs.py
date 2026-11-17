"""A Prompt Template optimisation runs end to end, with its criteria metered.

RED at 2e720491: the create refuses Prompt Template with the retirement
sentence (views/optimize_dataset.py:227-232), the runner fails the run and
raises LegacyOptimizerRetiredError before the criteria call
(tasks/prompt_template_optimizer.py:556-562), and PromptOptimizer takes no
criteria_breakdown (ee/agenthub/prompt_optimizer_agent/agent_task_v2.py:186-197).

GREEN: the create writes its row and schedules its run. The runner makes the
criteria call with the run's one client on its organization (_run_client;
A line test's expected value changes: no longer platform_llm), so the call is
held and settled on the credit ledger (credit_ledger_port.CallMeter.metered)
with the purpose run_prompt. A refused hold sends nothing: the run
is failed with the ledger's own refusal text, no optimizer is built and the
error is not retried. PromptOptimizer scores each candidate against the
criteria it is given. RAG Prompt Template and the knowledge-base routes still
refuse exactly as before.

Doubles: the top-k and RAG task dispatch, the metrics check, the gateway port
(MockGatewayManagedRouting), its client transport (FakeGatewayClient), the
credit ledger (MockCreditLedger), the ClickHouse usage log and the LLM class
inside PromptOptimizer. No network and no provider key.
"""

import uuid
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from rest_framework import status
from temporalio.exceptions import ApplicationError

from agentic_eval.core.llm import platform_model
from ee.agenthub.prompt_optimizer_agent import agent_task_v2
from ee.agenthub.text_eval_agent.eval_text_llm import EvalTextLLM
from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.services import model_route
from model_hub.tasks import prompt_template_optimizer as template_task
from model_hub.tests.test_model_route_support import (
    OPENAI_CHAT_MODEL,
    FakeGatewayClient,
    GatewayUsage,
    chat_response,
    eligible_port,
)
from model_hub.views import optimize_dataset as optimize_views

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

RAG_UNAVAILABLE = "RAG prompt optimization is not available."
TEMPLATE = "Answer {{question}}"
OPTIMISED = "Answer {{question}} in one sentence and name the refund window."
CRITERIA = ["The answer states the refund window."]
CRITERIA_ANSWER = '{"1": "The answer states the refund window."}'
RUNNER_DATA = {
    "prompt_template": [TEMPLATE],
    "context": [None],
    "user_chat": ["What is the refund window?"],
    "model_chat": ["Thirty days."],
    "variables": [{"question": "What is the refund window?"}],
    "metadata": [{}],
}


def _ai_model(organization, workspace):
    return AIModel.all_objects.create(
        user_model_id=f"template-optimizer-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _metric(ai_model):
    # A text of its own: the criteria helper answers from its cache, keyed on
    # the text alone, before any model call.
    text = f"Does the answer state the refund window? ({uuid.uuid4()})"
    return Metric.objects.create(
        name=f"Template optimizer metric {uuid.uuid4()}",
        text_prompt=text,
        criteria_breakdown=[text],
        model=ai_model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
    )


def _run(organization, workspace, ai_model, metric):
    run = OptimizeDataset.no_workspace_objects.create(
        name=f"Prompt Template run {uuid.uuid4()}",
        optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
        model=ai_model,
        organization=organization,
        workspace=workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version="v1",
        prompt=TEMPLATE,
        status=OptimizeDataset.StatusType.RUNNING,
    )
    run.metrics.set([metric])
    return run


def _create_body(ai_model, metric, optimize_type, name):
    return {
        "name": name,
        "start_date": "2026-01-01T00:00:00",
        "end_date": "2026-01-02T00:00:00",
        "model": str(ai_model.id),
        "optimize_type": optimize_type,
        "environment": OptimizeDataset.EnvTypes.TRAINING,
        "version": "v1",
        "metrics": [str(metric.id)],
        "prompt": TEMPLATE,
        "variables": {"question": "What is the refund window?"},
    }


class RecordingOptimizer:
    """Stands in for PromptOptimizer where the loop is not under test: records
    how it was built and returns one optimised prompt, calling no model."""

    def __init__(self):
        self.built = []

    def __call__(self, *args, **kwargs):
        self.built.append((args, kwargs))
        return SimpleNamespace(
            get_optimized_prompt=lambda: [
                {"train_score": 90.0, "instruction_template": OPTIMISED}
            ]
        )


@pytest.fixture
def dispatch(monkeypatch):
    """The metrics check passes; the two task dispatches are recorded."""
    seen = {"topk": [], "rag": []}
    monkeypatch.setattr(
        optimize_views, "check_valid_metrics", lambda *args, **kwargs: (True, "")
    )
    monkeypatch.setattr(
        optimize_views.get_topk_prompts,
        "apply_async",
        lambda *args, **kwargs: seen["topk"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        optimize_views.rag_prompt_optimzer,
        "apply_async",
        lambda *args, **kwargs: seen["rag"].append((args, kwargs)),
    )
    return seen


@pytest.fixture
def managed(monkeypatch):
    """The managed route as test_model_route_llm.py and
    test_one_meter_compose_r2.py set it up: no gateway port until a test
    installs one, the meter's safety state reset, the platform features on
    the OpenAI chat model the gateway doubles price, a fixed hold estimate and
    no ClickHouse usage log."""
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


# (a) ------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_prompt_template_create_writes_the_run_and_schedules_it(
    auth_client, organization, workspace, dispatch
):
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    name = f"Scheduled Prompt Template run {uuid.uuid4()}"

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{ai_model.id}/",
        _create_body(ai_model, metric, OptimizeDataset.OptimizeType.TEMPLATE, name),
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.json()
    run = OptimizeDataset.no_workspace_objects.get(id=response.json()["data"]["id"])
    assert run.name == name
    assert run.optimize_type == OptimizeDataset.OptimizeType.TEMPLATE
    assert run.status == OptimizeDataset.StatusType.RUNNING
    assert run.prompt == TEMPLATE
    assert run.organization == organization
    assert list(run.metrics.values_list("id", flat=True)) == [metric.id]
    ((args, kwargs),) = dispatch["topk"]
    assert args == ()
    model_id, environment, version, metrics, run_id, start, end = kwargs["args"]
    assert (model_id, environment, version) == (
        str(ai_model.id),
        OptimizeDataset.EnvTypes.TRAINING,
        "v1",
    )
    assert [str(metric_id) for metric_id in metrics] == [str(metric.id)]
    assert str(run_id) == str(run.id)
    assert (start, end) == ("2026-01-01", "2026-01-02")
    assert dispatch["rag"] == []


# (b) ------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_runner_builds_prompt_optimizer_with_arguments_it_accepts(
    organization, workspace, monkeypatch
):
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    run = _run(organization, workspace, ai_model, metric)
    criteria_calls = []
    built = []

    def criteria_helper(text, **kwargs):
        criteria_calls.append((text, kwargs))
        return list(CRITERIA)

    def build(*args, **kwargs):
        # The real class: a keyword it does not take raises TypeError here.
        optimizer = agent_task_v2.PromptOptimizer(*args, **kwargs)
        built.append(optimizer)
        return optimizer

    # A line test's expected value changes: a run with neither a stored key nor
    # a managed route is refused before any call, so the run is given its
    # route here (a stand-in run client), and the criteria helper gets that
    # client as ``llm`` instead of the organization and workspace.
    run_client = SimpleNamespace(refusal=None, settled=0)
    routes = []

    def run_route(*args):
        routes.append(args)
        return run_client

    monkeypatch.setattr(template_task, "_run_client", run_route, raising=False)
    monkeypatch.setattr(template_task, "create_criteria_text_prompt", criteria_helper)
    monkeypatch.setattr(template_task, "PromptOptimizer", build)
    optimised = [{"train_score": 90.0, "instruction_template": OPTIMISED}]
    with (
        patch.object(agent_task_v2, "LLM"),
        patch.object(
            agent_task_v2.PromptOptimizer,
            "get_optimized_prompt",
            return_value=optimised,
        ),
    ):
        template_task.runner_topk_optimizer(RUNNER_DATA, [metric.text_prompt], run.id)

    (optimizer,) = built
    assert optimizer.prompt == TEMPLATE
    assert optimizer.criteria_breakdown == CRITERIA
    assert optimizer.user_eval_metrics == []
    assert optimizer.client is run_client
    assert list(optimizer.train_data["user_chat"]) == RUNNER_DATA["user_chat"]
    assert routes == [(organization, workspace)]
    ((text, kwargs),) = criteria_calls
    assert text == f"0. {metric.text_prompt}"
    assert kwargs == {"llm": run_client}
    run.refresh_from_db()
    assert run.status == OptimizeDataset.StatusType.COMPLETED
    assert run.criteria_breakdown == CRITERIA
    assert run.optimized_k_prompts == [OPTIMISED]


def test_prompt_optimizer_scores_a_chat_against_the_criteria_it_is_given():
    scored = []

    def score_chat_history(self, **kwargs):
        scored.append((self.llm, kwargs))
        return {
            "score": 0.8,
            "judgments": [
                {"criteria": CRITERIA[0], "judgment": "It names thirty days."}
            ],
            "summary_judgement": "It names thirty days.",
        }

    chat = [
        {"role": "user", "content": "What is the refund window?"},
        {"role": "assistant", "content": "Thirty days."},
    ]
    with (
        patch.object(agent_task_v2, "LLM"),
        patch.object(EvalTextLLM, "score_chat_history", score_chat_history),
    ):
        optimizer = agent_task_v2.PromptOptimizer(
            prompt=TEMPLATE,
            train_data=pd.DataFrame(RUNNER_DATA),
            criteria_breakdown=CRITERIA,
        )
        feedback = optimizer._get_evaluation_feedback(chat)

    assert feedback == (
        f"\nCRITERIA:{CRITERIA[0]}\nJUDGEMENT:It names thirty days.\n",
        80.0,
    )
    ((llm, kwargs),) = scored
    assert llm is optimizer.client
    assert kwargs == {
        "criteria_breakdown": CRITERIA,
        "chat_history": chat,
        "model_type": "test",
        "is_rag": False,
    }


# (c) ------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_criteria_call_is_held_and_settled_on_the_credit_ledger(
    organization, workspace, managed, monkeypatch
):
    gateway_response = chat_response(CRITERIA_ANSWER, model=OPENAI_CHAT_MODEL)
    gateway_response.usage = GatewayUsage(
        prompt_tokens=20, completion_tokens=10, total_tokens=30
    )
    client = FakeGatewayClient(response=gateway_response)
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("1"))
    optimizer = RecordingOptimizer()
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    run = _run(organization, workspace, ai_model, metric)

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        template_task.runner_topk_optimizer(RUNNER_DATA, [metric.text_prompt], run.id)

    assert [entry[0] for entry in ledger.calls] == ["hold", "settle"]
    (hold,) = ledger.holds.values()
    # A line test's expected value changes: the criteria call is made with the
    # run's one client, whose binding (and so every hold's purpose) is the
    # ledger's operation for this optimizer, run_prompt, not criteria_helper.
    assert hold.purpose == managed.OP_RUN_PROMPT
    assert hold.org_id == str(organization.id)
    assert hold.workspace_id == str(workspace.id)
    assert hold.state == "settled"
    assert hold.debits == 1
    assert [call["operation"] for call in port.authorize_calls] == [
        managed.OP_RUN_PROMPT
    ]
    (sent,) = client.calls
    assert sent["kwargs"]["model"] == OPENAI_CHAT_MODEL
    assert sent["kwargs"]["extra_headers"]["X-Org-Id"] == str(organization.id)
    assert sent["kwargs"]["extra_headers"]["X-Workspace-Id"] == str(workspace.id)
    ((_, built),) = optimizer.built
    assert built["criteria_breakdown"] == CRITERIA
    assert built["prompt"] == TEMPLATE
    run.refresh_from_db()
    assert run.criteria_breakdown == CRITERIA
    assert run.status == OptimizeDataset.StatusType.COMPLETED
    assert run.optimized_k_prompts == [OPTIMISED]


# (d) ------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_too_few_credits_refuse_the_run_before_any_model_call(
    organization, workspace, managed, monkeypatch
):
    client = FakeGatewayClient(
        response=chat_response(CRITERIA_ANSWER, model=OPENAI_CHAT_MODEL)
    )
    port = eligible_port(managed, organization, {OPENAI_CHAT_MODEL}, client=client)
    ledger = credit_ledger.MockCreditLedger(default_balance=Decimal("0"))
    optimizer = RecordingOptimizer()
    monkeypatch.setattr(template_task, "PromptOptimizer", optimizer)
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    run = _run(organization, workspace, ai_model, metric)
    ledger_text = credit_ledger.CreditsExhaustedError().message

    with credit_ledger.use_credit_ledger(ledger), managed.managed_routing_port(port):
        with pytest.raises(ApplicationError) as refused:
            template_task.runner_topk_optimizer(
                RUNNER_DATA, [metric.text_prompt], run.id
            )

    assert refused.value.message == ledger_text
    assert refused.value.non_retryable is True
    assert [entry[0] for entry in ledger.calls] == ["hold"]
    assert ledger.holds == {}
    assert port.authorize_calls == []
    assert client.calls == []
    assert optimizer.built == []
    run.refresh_from_db()
    assert run.status == OptimizeDataset.StatusType.FAILED
    assert run.error_message == ledger_text
    assert run.criteria_breakdown == []
    assert run.optimized_k_prompts is None


# (e) ------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("optimize_type", ["RagPromptTemplate", "RAG_PROMPT_TEMPLATE"])
def test_rag_prompt_template_create_still_refuses_before_any_row(
    auth_client, organization, workspace, dispatch, optimize_type
):
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    name = f"Refused RAG run {uuid.uuid4()}"

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{ai_model.id}/",
        _create_body(ai_model, metric, optimize_type, name),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert response.json()["message"] == RAG_UNAVAILABLE
    assert not OptimizeDataset.all_objects.filter(model=ai_model).exists()
    assert dispatch == {"topk": [], "rag": []}


@pytest.mark.django_db
def test_knowledge_base_create_and_task_still_refuse(
    auth_client, organization, workspace, dispatch
):
    name = f"Refused knowledge-base run {uuid.uuid4()}"

    response = auth_client.post(
        "/model-hub/optimize-dataset/knowledge-base/",
        {
            "name": name,
            "knowledge_base_metrics": ["Answers cite the retrieved passage."],
            "knowledge_base_filters": ["topic"],
            "prompt": "Answer from the passage.",
            "variables": {"topic": "billing"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert response.json()["message"] == RAG_UNAVAILABLE
    assert not OptimizeDataset.all_objects.filter(name=name).exists()
    assert dispatch == {"topk": [], "rag": []}
    queued = OptimizeDataset.no_workspace_objects.create(
        name=f"Queued knowledge-base run {uuid.uuid4()}",
        optimize_type=OptimizeDataset.OptimizeType.RAG_TEMPLATE,
        organization=organization,
        workspace=workspace,
        environment=OptimizeDataset.EnvTypes.CORPUS,
        version="",
        prompt="Answer from the passage.",
        criteria_breakdown=["Answers cite the retrieved passage."],
        status=OptimizeDataset.StatusType.RUNNING,
    )

    optimize_views.rag_prompt_optimzer._original_func(queued.id)

    queued.refresh_from_db()
    assert queued.status == OptimizeDataset.StatusType.FAILED
    assert queued.error_message == RAG_UNAVAILABLE
    assert queued.optimized_k_prompts is None
