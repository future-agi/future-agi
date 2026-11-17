"""Fix Your Agent evaluations end with the evaluation engine's result.

Both optimizer evaluators (the simulated-conversation one in fix_your_agent
and the dataset one, DirectEvaluator) run each configured evaluation through
run_eval_func and the evaluation engine. When the engine refuses an
evaluation (no key stored for the judge model, or a usage limit) the
evaluation ends with that refusal and no evaluator is built outside the
engine; a successful result is scored as before. The simulated-conversation
evaluator without an organization builds the LLM-as-a-judge evaluator
through the engine too. Only the model transports are replaced (see
evaluations/engine/tests/test_version_model_needs_its_own_key); every key
here is synthetic.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator
from ee.agenthub.fix_your_agent.fix_your_agent import SimulationEvaluator
from ee.usage.exceptions import UsageLimitExceeded
from ee.usage.schemas.events import CheckResult
from evaluations.engine.tests.test_version_model_needs_its_own_key import (
    OTHER_MODEL,
    assert_no_gateway_or_model_library_call,
    make_template,
    transports,
)
from model_hub.services import model_route

OUTPUT = "The answer is 4."
PROMPT = "Answer the question."
NOT_CONFIGURED = "API key not configured for openai"
LIMIT = "Evaluation usage limit reached for this billing period."
JUDGE_REASON = "The output is correct."

PATHS = ["fix-your-agent", "fix-your-agent-without-an-organization", "direct"]
ORGANIZATION_PATHS = ["fix-your-agent", "direct"]


def evaluate(path, template, organization, workspace):
    """One Fix Your Agent evaluation of ``template``; returns (score, reason)."""
    if path == "direct":
        evaluator = DirectEvaluator.__new__(DirectEvaluator)
        evaluator.execution_model = template.model
        evaluator.organization = organization
        evaluator.workspace = workspace
        evaluator.eval_source = "fix_your_agent"
        binding = SimpleNamespace(
            eval_template=template, mapping={"output": "output"}, config={}
        )
        return evaluator._run_eval_with_config(binding, {}, OUTPUT, PROMPT)

    evaluator = SimulationEvaluator.__new__(SimulationEvaluator)
    evaluator.eval_lookup = {}
    evaluator._eval_template_cache = {}
    evaluator.organization = (
        None if path == "fix-your-agent-without-an-organization" else organization
    )
    evaluator.workspace = workspace
    evaluator.eval_source = "fix_your_agent"
    binding = SimpleNamespace(
        eval_template=template,
        mapping={"output": "transcript"},
        config={},
        model=None,
    )
    return evaluator._run_eval_with_config(binding, OUTPUT, PROMPT, {})


def run_eval_func_of(path):
    """Where ``path`` looks run_eval_func up."""
    if path == "direct":
        return "model_hub.views.utils.evals.run_eval_func"
    return "ee.agenthub.fix_your_agent.fix_your_agent.run_eval_func"


def pass_fail_template(organization, workspace):
    """A Pass/Fail judge template, scored as Pass/Fail (as saving one through
    the product sets it)."""
    template = make_template(organization, workspace)
    template.output_type_normalized = "pass_fail"
    template.save(update_fields=["output_type_normalized"])
    return template


@pytest.fixture
def no_connection_reset():
    """The simulated-conversation evaluator resets stale database connections
    before each evaluation; inside a test transaction that would close it."""
    with patch("django.db.close_old_connections"):
        yield


@pytest.mark.django_db
@pytest.mark.parametrize("path", PATHS)
def test_a_judge_model_without_stored_model_settings_ends_the_evaluation_with_the_refusal(
    organization, workspace, no_connection_reset, path
):
    """No key is stored for the judge model: the evaluation ends with the
    engine's refusal and score 0.0, no evaluator is built and no model is
    called."""
    template = make_template(organization, workspace, model=OTHER_MODEL)

    with model_route.managed_routing_port(None), transports() as seams:
        score, reason = evaluate(path, template, organization, workspace)

    assert score == 0.0
    assert NOT_CONFIGURED in reason
    # Building an evaluator fetches the gateway client; none was built.
    assert seams.get_gateway.call_count == 0
    assert seams.turing.call_count == 0
    assert_no_gateway_or_model_library_call(seams)


def usage_limit():
    return UsageLimitExceeded(
        CheckResult(allowed=False, reason=LIMIT, error_code="FREE_TIER_LIMIT")
    )


@pytest.mark.django_db
def test_a_usage_limit_ends_the_simulated_conversation_evaluation(
    organization, workspace, no_connection_reset
):
    """run_eval_func refuses the evaluation for a usage limit: the evaluation
    ends with that refusal and score 0.0, no evaluator is built and no model
    is called."""
    template = make_template(organization, workspace)

    with (
        transports() as seams,
        patch(
            run_eval_func_of("fix-your-agent"), side_effect=usage_limit()
        ) as run_eval_func,
    ):
        score, reason = evaluate("fix-your-agent", template, organization, workspace)

    run_eval_func.assert_called_once()
    assert score == 0.0
    assert LIMIT in reason
    assert seams.get_gateway.call_count == 0
    assert seams.turing.call_count == 0
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
def test_a_usage_limit_stops_the_dataset_optimizer_evaluation(organization, workspace):
    """The dataset optimizer stops on a usage limit, as before: the refusal
    is raised, no evaluator is built and no model is called."""
    template = make_template(organization, workspace)

    with (
        transports() as seams,
        patch(run_eval_func_of("direct"), side_effect=usage_limit()),
    ):
        with pytest.raises(UsageLimitExceeded):
            evaluate("direct", template, organization, workspace)

    assert seams.get_gateway.call_count == 0
    assert seams.turing.call_count == 0
    assert_no_gateway_or_model_library_call(seams)


@pytest.mark.django_db
@pytest.mark.parametrize("path", ORGANIZATION_PATHS)
def test_a_successful_evaluation_result_is_scored_as_before(
    organization, workspace, no_connection_reset, path
):
    """run_eval_func returns a result: the evaluation scores it and keeps its
    reason, as before."""
    template = pass_fail_template(organization, workspace)

    with (
        transports() as seams,
        patch(
            run_eval_func_of(path),
            return_value={"output": "Passed", "reason": JUDGE_REASON},
        ) as run_eval_func,
    ):
        score, reason = evaluate(path, template, organization, workspace)

    run_eval_func.assert_called_once()
    assert (score, reason) == (1.0, JUDGE_REASON)
    assert seams.get_gateway.call_count == 0
    assert_no_gateway_or_model_library_call(seams)
