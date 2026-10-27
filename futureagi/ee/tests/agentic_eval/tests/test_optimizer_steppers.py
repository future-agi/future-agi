from ee.agent_opt.optimizers.stepper import (
    RandomSearchStepper,
    MetaPromptStepper,
    BayesianStepper,
    ProTeGiStepper,
    PromptWizardStepper,
)
from ee.agent_opt.types import EvaluationResult, IterationHistory


def test_random_search_stepper_roundtrip():
    variations = ["v1", "v2"]

    def fake_generator(initial, n, model, kwargs, api_key, task_description):
        return variations

    stepper = RandomSearchStepper.from_config(
        initial_prompt="base",
        num_variations=2,
        teacher_model="model-x",
        variation_generator=fake_generator,
    )

    assert stepper.next_candidate() == "v1"
    stepper.on_result(prompt="v1", score=0.5)

    state = stepper.to_state()
    restored = RandomSearchStepper.from_state(
        state,
        config={
            "teacher_model": "model-x",
            "num_variations": 2,
            "teacher_model_kwargs": {},
        },
        variation_generator=fake_generator,
    )

    assert restored.remaining == 1
    assert restored.best_prompt == "v1"
    assert restored.next_candidate() == "v2"


def test_meta_prompt_stepper_generates_next_prompt():
    def fake_teacher_generate(meta_prompt: str, generate_kwargs: dict) -> str:
        return '{"improved_prompt": "p1", "hypothesis": "h"}'

    stepper = MetaPromptStepper.from_config(
        initial_prompt="p0",
        teacher_model="t-model",
        teacher_generate=fake_teacher_generate,
        num_rounds=2,
        eval_subset_size=1,
        dataset_size=1,
        meta_prompt_template="{current_prompt}|{other_attempts}|{annotated_results}|{task_description}",
    )

    dataset = [{"call_execution_id": "1", "text": "foo"}]
    history = IterationHistory(
        prompt="p0",
        average_score=0.2,
        individual_results={"1": EvaluationResult(score=0.2, reason="ok", metadata={})},
    )

    assert stepper.next_candidate() == "p0"
    stepper.on_result(
        prompt="p0",
        score=history.average_score,
        metadata={
            "iteration_history": history,
            "dataset_subset": dataset,
            "task_description": "task",
        },
    )

    # After consuming first round we should have moved to the improved prompt
    state = stepper.to_state()
    assert state["current_prompt"] == "p1"
    assert stepper.best_prompt == "p0"
    assert stepper.remaining == 1


def test_bayesian_stepper_counts_trials():
    stepper = BayesianStepper.from_config(n_trials=5, initial_prompt="base")
    assert stepper.remaining == 5
    stepper.on_result(prompt="p1", score=0.7, metadata={"trials_run": 2})
    assert stepper.remaining == 3
    state = stepper.to_state()
    restored = BayesianStepper.from_state(state, config={"n_trials": 5})
    assert restored.completed_trials == 2
    assert restored.best_prompt == "p1"


def test_protegi_stepper_rounds_and_beam():
    stepper = ProTeGiStepper.from_config(num_rounds=3, initial_prompts=["a", "b"])
    beam = stepper.next_beam()
    assert beam == {"a", "b"}
    stepper.on_result(prompt="a", score=0.6, metadata={"new_beam": {"c"}})
    assert stepper.beam == {"c"}
    assert stepper.remaining == 2


def test_promptwizard_stepper_progress():
    stepper = PromptWizardStepper.from_config(refine_iterations=2, initial_prompt="p0")
    assert stepper.next_candidate() == "p0"
    stepper.on_result(prompt="p1", score=0.4)
    assert stepper.current_best_instruction == "p1"
    assert stepper.remaining == 1
