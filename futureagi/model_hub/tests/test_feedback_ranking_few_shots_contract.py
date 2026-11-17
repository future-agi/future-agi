"""Pin RankingEvaluator's empty-feedback echo through engine, SDK and real ORM.

Keep evaluator construction, ranking/scoring, parameter preparation, formatting,
SDK cost logging and Evaluation persistence real. Only the LLM client/completion
boundary and the shared fixture's embedding/serving boundary are substituted.
The failed case raises at the serving probe after a real scoped-count control.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_eval.core.embeddings import embedding_manager as em
from evaluations.engine import EvalRequest, run_eval
from evaluations.engine.params import prepare_run_params
from model_hub.tests.test_feedback_rag_consumer_scope import (
    _django_db_helper,  # noqa: F401 - retain isolated transactional teardown
    consumer_scope,  # noqa: F401
)
from model_hub.tests.test_feedback_rag_workspace_scope import feedback_store  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

_SUMMARY = "Document1 (Final score: 1)\nDocument2 (Final score: 0.5)"


@pytest.fixture
def ranking_inference(monkeypatch):
    from agentic_eval.core.llm.llm import LLM
    from ee.evals.futureagi.eval_ranking.evaluator import RankingEvaluator
    from evaluations.engine.registry import get_eval_class

    assert get_eval_class("RankingEvaluator") is RankingEvaluator, (
        "SETUP_AQ-XWS-W4H: the registry must resolve the real RankingEvaluator"
    )
    # One subquery, two ranked documents and one summary. The real ranking
    # agent parses these strings and calculates nDCG; no result is fabricated.
    completion = Mock(
        side_effect=[
            json.dumps(
                [
                    {
                        "subquery": "Which context answers the question?",
                        "relevance": 1,
                        "parent": [],
                    }
                ]
            ),
            json.dumps(
                [
                    {
                        "document": f"Document{index}",
                        "relevancy": 1,
                        "relevancy_of_dependent_subqueries": [],
                        "information_gained": gain,
                    }
                    for index, gain in ((1, 1), (2, 0.5))
                ]
            ),
            _SUMMARY,
        ]
    )
    clients = []

    def complete(client, messages, **kwargs):
        clients.append(client)
        return completion(messages, **kwargs)

    # LLM.__init__ still initializes model identity, cost and token counters.
    # Only provider-client initialization (which can discover credentials) and
    # the text completion method are replaced, including the nested rank LLM.
    monkeypatch.setattr(LLM, "_init_client", lambda self, api_key: None)
    monkeypatch.setattr(LLM, "_get_completion_content", complete)
    return SimpleNamespace(
        completion=completion, clients=clients, evaluator_class=RankingEvaluator
    )


@pytest.fixture
def ranking_scope(consumer_scope):  # noqa: F811
    scope = consumer_scope
    inputs = {
        "input": "Which context answers the question?",
        "context": json.dumps(
            ["The first relevant context.", "A second relevant context."]
        ),
        "criteria": "Rank the contexts by relevance to the question.",
        "output": "The first context is the most relevant.",
    }
    scope.template.config = {
        "eval_type_id": "RankingEvaluator",
        "required_keys": ["input", "context", "criteria"],
        "output": "score",
    }
    scope.template.save(update_fields=["config"])
    # Supply every input column so a genuine matching control can pass through
    # both the real vector queries and the real multi-column formatter.
    scope.manager.db_client.upsert_vector(
        table_name=em.FEEDBACK_TABLE_NAME,
        eval_id=scope.eval_id,
        vector=[1.0, 0.0],
        metadata={
            **inputs,
            "item_id": str(uuid.uuid4()),
            "organization_id": scope.organization_id,
            "workspace_id": scope.workspace_id,
            "input_type": "text",
            "feedback_comment": "ranking-local-comment",
            "feedback_value": "ranking-local-label",
        },
        unique_keys=["item_id"],
        mark_existing_deleted=False,
    )
    assert (
        scope.manager.get_feedback_count(
            eval_id=scope.eval_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        )
        == 1
    ), "SETUP_AQ-XWS-W4H: real scoped feedback count must be positive"
    examples = prepare_run_params(
        inputs,
        scope.template,
        is_futureagi=True,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )["few_shots"]
    assert isinstance(examples, list) and "ranking-local-comment" in str(examples), (
        "SETUP_AQ-XWS-W4H: real same-scope retrieval and formatting must succeed"
    )
    return SimpleNamespace(scope=scope, inputs=inputs)


@pytest.fixture(params=["zero_match", "unbound", "failed"])
def empty_feedback(request, ranking_scope, monkeypatch):
    case = request.param
    embedding_calls = []
    health_calls = []

    def embed(text):
        embedding_calls.append(text)
        # The seeded vector is [1, 0]. Distance 1 is beyond the real reader's
        # cap, so zero_match traverses vector SQL rather than a no-rows shortcut.
        return [0.0, 1.0] if case == "zero_match" else [1.0, 0.0]

    def serving_probe():
        health_calls.append(True)
        if case == "failed":
            raise RuntimeError(
                "SETUP_AQ-XWS-W4H: injected retrieval dependency failure"
            )
        return True

    monkeypatch.setattr(
        em, "model_manager", SimpleNamespace(_use_serving=True, text_model=embed)
    )
    monkeypatch.setattr(em, "serving_available", serving_probe)
    scope = ranking_scope.scope
    return SimpleNamespace(
        case=case,
        scope=scope,
        inputs=ranking_scope.inputs,
        organization_id=None if case == "unbound" else scope.organization_id,
        workspace=None if case == "unbound" else scope.workspace,
        workspace_id=None if case == "unbound" else scope.workspace_id,
        embedding_calls=embedding_calls,
        health_calls=health_calls,
    )


def _assert_success(result, scenario, inference):
    assert inference.completion.call_count == 3, (
        "SETUP_AQ-XWS-W4H: ranking must reach all three inference completions"
    )
    assert isinstance(inference.clients[-1], inference.evaluator_class), (
        "SETUP_AQ-XWS-W4H: the real RankingEvaluator must request the summary"
    )
    assert result["failure"] is False and result["value"] == 1.0, (
        "SETUP_AQ-XWS-W4H: real ranking calculation must complete successfully"
    )
    assert result["reason"] == _SUMMARY, (
        "SETUP_AQ-XWS-W4H: the real evaluator must preserve the inference summary"
    )
    assert result["metrics"] == [{"id": "RankingEvaluator", "value": 1.0}], (
        "SETUP_AQ-XWS-W4H: real RankingEvaluator metrics must survive formatting"
    )
    data = result["data"]
    assert isinstance(data, dict) and "few_shots" in data, (
        "SETUP_AQ-XWS-W4H: RankingEvaluator must echo injected feedback in data"
    )
    assert all(data[key] == value for key, value in scenario.inputs.items()), (
        "SETUP_AQ-XWS-W4H: real evaluator result must echo the ranking inputs"
    )
    if scenario.case == "unbound":
        assert scenario.health_calls == scenario.embedding_calls == [], (
            "SETUP_AQ-XWS-W4H: unbound feedback must stop before retrieval dependencies"
        )
    elif scenario.case == "failed":
        assert scenario.health_calls == [True] and scenario.embedding_calls == [], (
            "SETUP_AQ-XWS-W4H: scoped retrieval must reach the failing dependency"
        )
    else:
        assert scenario.health_calls == [True], (
            "SETUP_AQ-XWS-W4H: zero-match retrieval must pass the real scoped count"
        )
        assert scenario.embedding_calls == list(scenario.inputs.values()), (
            "SETUP_AQ-XWS-W4H: zero-match retrieval must embed every ranking input"
        )


@pytest.fixture
def sdk_result(empty_feedback, ranking_inference, user, settings):
    from ee.usage.models.usage import (
        APICallLog,
        APICallType,
        OrganizationStatusChoices,
        OrganizationSubscription,
        PlanChoices,
        Pricing,
        SubscriptionTier,
        SubscriptionTierChoices,
    )
    from sdk.utils import evaluations as sdk_evaluations
    from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

    scenario = empty_feedback
    scope = scenario.scope
    # Seed billing through the ORM instead of stubbing cost-log creation/save.
    # The integration venue must supply its ordinary billing config and Redis.
    # Disable only background usage-stream emission through its product switch;
    # synchronous admission, APICallLog writes and SDK log updates remain real.
    settings.USAGE_EVENTS_ENABLED = False
    tier = SubscriptionTier.no_workspace_objects.create(
        name=SubscriptionTierChoices.CUSTOM.value,
        description=f"ranking-contract-{uuid.uuid4().hex}",
    )
    OrganizationSubscription.no_workspace_objects.update_or_create(
        organization=scope.organization,
        defaults={
            "subscription_tier": tier,
            "plan": PlanChoices.ENTERPRISE.value,
            "status": OrganizationStatusChoices.ACTIVE.value,
        },
    )
    call_type, _ = APICallType.no_workspace_objects.get_or_create(
        name=APICallTypeChoices.TURING_LARGE_EVALUATOR.value
    )
    Pricing.no_workspace_objects.update_or_create(
        api_call_type=call_type,
        organization=scope.organization,
        defaults={"price_per_call": 0},
    )
    # SDK users and persisted Evaluation rows require an organization. Its
    # unbound case therefore omits the workspace; the direct engine test below
    # additionally omits organization, exercising the other early-return guard.
    result = sdk_evaluations._run_eval(
        scope.template,
        scenario.inputs,
        "turing_large",
        user,
        scenario.workspace,
    )
    _assert_success(result, scenario, ranking_inference)
    logs = list(
        APICallLog.no_workspace_objects.filter(
            organization=scope.organization,
            source="standalone_v2",
            source_id=str(scope.template.id),
            reference_id=str(user.id),
        )
    )
    assert len(logs) == 1 and logs[0].status == APICallStatusChoices.SUCCESS.value, (
        "SETUP_AQ-XWS-W4H: SDK must save one successful real API-call log"
    )
    log_input = json.loads(logs[0].config)["input"]
    assert isinstance(log_input, dict) and "few_shots" in log_input, (
        "SETUP_AQ-XWS-W4H: the saved cost log must contain the SDK result input"
    )
    return SimpleNamespace(result=result, log_input=log_input)


def test_run_eval_data_has_empty_list(empty_feedback, ranking_inference):
    scenario = empty_feedback
    result = run_eval(
        EvalRequest(
            eval_template=scenario.scope.template,
            inputs=scenario.inputs,
            model="turing_large",
            organization_id=scenario.organization_id,
            workspace_id=scenario.workspace_id,
        )
    )
    _assert_success(
        {
            "data": result.data,
            "failure": result.failure,
            "value": result.value,
            "reason": result.reason,
            "metrics": result.metrics,
        },
        scenario,
        ranking_inference,
    )
    assert result.data["few_shots"] == [], (
        "AQ-XWS-W4H: run_eval data['few_shots'] must be []"
    )


def test_sdk_return_data_has_empty_list(sdk_result):
    assert sdk_result.result["data"]["few_shots"] == [], (
        "AQ-XWS-W4H: SDK returned data['few_shots'] must be []"
    )


def test_sdk_cost_log_input_has_empty_list(sdk_result):
    assert sdk_result.log_input["few_shots"] == [], (
        "AQ-XWS-W4H: SDK API-call/cost-log input['few_shots'] must be []"
    )


def test_persisted_evaluation_data_has_empty_list(empty_feedback, sdk_result, user):
    from model_hub.models.evaluation import Evaluation, StatusChoices
    from sdk.utils import evaluations as sdk_evaluations
    from tfc.middleware.workspace_context import clear_workspace_context

    scenario = empty_feedback
    # BaseModel.save fills a missing workspace from the ambient context; an unbound
    # SDK request has none, so do not inherit a fixture's context here.
    clear_workspace_context()
    evaluation = sdk_evaluations._create_evaluation(
        eval_template=scenario.scope.template,
        inputs=scenario.inputs,
        user=user,
        eval_config={},
        result=sdk_result.result,
        error_localizer_enabled=False,
        trace_eval=False,
        custom_eval_name=None,
        span_id=None,
        workspace=scenario.workspace,
        organization=scenario.scope.organization,
    )
    # Re-fetch, rather than inspecting the in-memory object passed to create().
    persisted = Evaluation.no_workspace_objects.get(pk=evaluation.pk)
    assert (
        persisted.status == StatusChoices.COMPLETED
        and str(persisted.eval_template_id) == str(scenario.scope.template.id)
        and persisted.organization_id == scenario.scope.organization.id
        and persisted.workspace_id
        == (scenario.workspace.id if scenario.workspace else None)
    ), "SETUP_AQ-XWS-W4H: real SDK persistence must save the completed scoped row"
    assert isinstance(persisted.data, dict) and "few_shots" in persisted.data, (
        "SETUP_AQ-XWS-W4H: the persisted row must contain the evaluator data"
    )
    assert persisted.data["few_shots"] == [], (
        "AQ-XWS-W4H: persisted Evaluation.data['few_shots'] must be []"
    )
