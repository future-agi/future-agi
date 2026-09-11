"""Teacher-model failures in the metaprompt stepper keep the current prompt."""

from ee.agent_opt.optimizers.stepper import MetaPromptStepper
from ee.agent_opt.types import IterationHistory

BASE_PROMPT = "You are a helpful agent."
TEMPLATE = "{current_prompt}{other_attempts}{annotated_results}{task_description}"


def _stepper(teacher_generate, num_rounds: int = 3) -> MetaPromptStepper:
    return MetaPromptStepper.from_config(
        initial_prompt=BASE_PROMPT,
        teacher_model="gpt-4.1",
        teacher_generate=teacher_generate,
        num_rounds=num_rounds,
        eval_subset_size=1,
        dataset_size=1,
        meta_prompt_template=TEMPLATE,
        api_key="sk-user-key",
    )


def _metadata() -> dict:
    return {
        "iteration_history": IterationHistory(
            prompt=BASE_PROMPT, average_score=0.5, individual_results={}
        ),
        "dataset_subset": [],
        "task_description": "improve",
    }


def test_teacher_failure_keeps_current_prompt():
    def failing_teacher(meta_prompt, generate_kwargs):
        raise RuntimeError("auth error")

    stepper = _stepper(failing_teacher)
    stepper.on_result(prompt=BASE_PROMPT, score=0.5, metadata=_metadata())

    assert stepper.current_prompt == BASE_PROMPT


def test_teacher_success_updates_prompt():
    def teacher(meta_prompt, generate_kwargs):
        return '{"improved_prompt": "better prompt"}'

    stepper = _stepper(teacher)
    stepper.on_result(prompt=BASE_PROMPT, score=0.5, metadata=_metadata())

    assert stepper.current_prompt == "better prompt"
