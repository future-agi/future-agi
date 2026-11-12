"""A grading refusal inside any optimizer's trial loop ends optimization.

FixYourAgent grades the baseline itself, so every evaluator call an optimizer
makes is trial work. A typed EvaluationDenied raised by the evaluator must
leave optimize() unchanged: it is not a failed score, and no later candidate
may be graded once it has been raised.
"""

import json
from functools import partial

import pytest

pytest.importorskip("optuna")  # Optional prompt-opt extra.
pytest.importorskip("gepa")

import litellm

from ee.agent_opt.datamappers import BasicDataMapper
from ee.agent_opt.generators.litellm_generator import LiteLLMGenerator
from ee.agent_opt.optimizers import (
    BayesianSearchOptimizer,
    GEPAOptimizer,
    MetaPromptOptimizer,
    PromptWizardOptimizer,
    ProTeGi,
    RandomSearchOptimizer,
    random_search,
)
from ee.agent_opt.optimizers import gepa as gepa_optimizer
from ee.agent_opt.types import EvaluationResult
from tfc.utils.evaluation_denials import EvaluationDenied, EvaluationRateLimited

BASE_PROMPT = "You are a synthetic support agent."
TEACHER_REPLY = json.dumps(
    {
        "hypothesis": "synthetic",
        "improved_prompt": "Synthetic improved prompt.",
        "variations": ["Synthetic variation A.", "Synthetic variation B."],
        "example_template": "{question}",
    }
)
DATASET = [
    {"call_execution_id": "synthetic-1", "question": "Synthetic question one?"},
    {"call_execution_id": "synthetic-2", "question": "Synthetic question two?"},
]


class DenyAfter:
    """Grades the first `allowed` batches, then refuses every later batch."""

    def __init__(self, allowed, denial):
        self.allowed = allowed
        self.denial = denial
        self.batches = 0
        self.denied_batches = 0

    def evaluate(self, inputs):
        self.batches += 1
        if self.batches > self.allowed:
            self.denied_batches += 1
            raise self.denial
        return [EvaluationResult(score=0.5, reason="Synthetic grading") for _ in inputs]


class FakeLiteLLMResponse:
    def __init__(self, content):
        message = type("Message", (), {"content": content})()
        self.choices = [type("Choice", (), {"message": message})()]


def synthetic_teacher():
    return LiteLLMGenerator("gpt-4o-mini", "{prompt}")


def build_optimizer(optimizer_type):
    """Mirror FixYourAgent's constructors with small synthetic budgets."""
    if optimizer_type == "random_search":
        return (
            RandomSearchOptimizer(
                generator=LiteLLMGenerator("gpt-4o-mini", BASE_PROMPT),
                teacher_model="gpt-4o-mini",
                num_variations=3,
            ),
            {},
        )
    if optimizer_type == "metaprompt":
        return MetaPromptOptimizer(teacher_generator=synthetic_teacher()), {
            "num_rounds": 3
        }
    if optimizer_type == "bayesian":
        return (
            BayesianSearchOptimizer(
                teacher_model_name="gpt-4o-mini",
                min_examples=1,
                max_examples=1,
                n_trials=3,
            ),
            {},
        )
    if optimizer_type == "protegi":
        return (
            ProTeGi(
                teacher_generator=synthetic_teacher(),
                beam_size=1,
                num_gradients=1,
                errors_per_gradient=1,
                prompts_per_gradient=1,
            ),
            {"num_rounds": 3},
        )
    if optimizer_type == "promptwizard":
        return (
            PromptWizardOptimizer(
                teacher_generator=synthetic_teacher(),
                mutate_rounds=2,
                refine_iterations=2,
                beam_size=1,
            ),
            {},
        )
    if optimizer_type == "gepa":
        return (
            GEPAOptimizer(
                reflection_model="gpt-4o-mini", generator_model="gpt-4o-mini"
            ),
            {"max_metric_calls": 8},
        )
    raise AssertionError(optimizer_type)


def denial_for(code):
    if code == "rate_limited":
        return EvaluationRateLimited()
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult

    return UsageLimitExceeded(
        CheckResult(
            allowed=False,
            error_code=code,
            reason="Synthetic budget paused; resume before grading.",
        )
    )


@pytest.mark.parametrize("allowed_batches", [0, 1], ids=["first-trial", "later-trial"])
@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
@pytest.mark.parametrize(
    "optimizer_type",
    ["random_search", "metaprompt", "bayesian", "protegi", "promptwizard", "gepa"],
)
def test_trial_denial_escapes_optimizer(
    monkeypatch, optimizer_type, code, allowed_batches
):
    monkeypatch.setattr(
        LiteLLMGenerator, "generate", lambda self, prompt_vars, **kw: TEACHER_REPLY
    )
    monkeypatch.setattr(
        random_search,
        "_generate_variations",
        lambda *a, **kw: ["Synthetic variation A.", "Synthetic variation B."],
    )
    monkeypatch.setattr(
        litellm, "completion", lambda **kw: FakeLiteLLMResponse(TEACHER_REPLY)
    )

    # GEPA grades one example per evaluator call, so its seed trial is one
    # call per row; "later-trial" then refuses inside GEPA's engine loop.
    allowed = allowed_batches * (len(DATASET) if optimizer_type == "gepa" else 1)
    denial = denial_for(code)
    evaluator = DenyAfter(allowed, denial)
    trials_after_denial = []

    def on_trial(**trial):
        if evaluator.denied_batches:
            trials_after_denial.append(trial["trial_number"])

    # GEPA evaluates a minibatch's examples concurrently; run its real engine
    # serially so the call counts below are exact.
    monkeypatch.setattr(
        gepa_optimizer,
        "EngineConfig",
        partial(gepa_optimizer.EngineConfig, parallel=False),
    )

    optimizer, config = build_optimizer(optimizer_type)
    with pytest.raises(EvaluationDenied) as caught:
        optimizer.optimize(
            evaluator=evaluator,
            data_mapper=BasicDataMapper(
                {"agent_prompt": "generated_output", "question": "question"}
            ),
            dataset=[dict(row) for row in DATASET],
            initial_prompts=[BASE_PROMPT],
            on_trial_callback=on_trial,
            api_key="synthetic-not-a-real-key",
            **config,
        )

    assert caught.value is denial
    assert caught.value.code == code
    # The refusal ended the run: nothing was graded or persisted after it.
    assert evaluator.denied_batches == 1
    assert evaluator.batches == allowed + 1
    assert trials_after_denial == []
