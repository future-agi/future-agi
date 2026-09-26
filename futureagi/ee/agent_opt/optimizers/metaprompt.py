import json
import re
from typing import Any, Dict, List, Optional, Callable

from pydantic import BaseModel, Field, ValidationError

from ..base.base_generator import BaseGenerator
from ..base.base_optimizer import BaseOptimizer
from ..datamappers.basic_mapper import BasicDataMapper
from ..base.evaluator import Evaluator
from ..generators.litellm_generator import LiteLLMGenerator
from ..types import IterationHistory, OptimizationResult
from simulate.utils.agent_prompt_optimiser import update_agent_optimiser_run_step
from .stepper import MetaPromptStepper
import logging

# Type for the per-trial callback
OnTrialCallback = Callable[[dict, int, Dict[str, Any], bool], None]

logger = logging.getLogger(__name__)
# ==============================================================================
# Prompts and Pydantic Models for the Teacher LLM (Meta-Model)
# ==============================================================================


META_PROMPT_TEMPLATE = """
You are a world-class expert in prompt engineering. Your task is to diagnose and optimize a given prompt based on its performance on a set of test cases.

### Current Prompt
The following is the current prompt being evaluated:
---
{current_prompt}
---

### Previous Failed Attempts
You have already tried the following prompts, but they performed worse than the current one. Analyze why they failed to avoid repeating mistakes.
---
{other_attempts}
---

### Performance Data
The current prompt was run on a set of examples, and here are the results. Pay close attention to the examples with low scores.
---
{annotated_results}
---

### Task Description
{task_description}

### Your Task
Think step-by-step to generate an improved prompt:
1.  **Analyze Failures:** Deeply analyze the failing examples. What patterns do you see? Is the prompt missing specific instructions that would fix the failure?
2.  **Formulate a Hypothesis:** Based on your analysis, state a clear hypothesis for how to improve the prompt.
3.  **Generate Improved Prompt:** Produce the improved prompt based on your hypothesis.

Return ONLY a valid JSON object with two keys: "hypothesis" (your string hypothesis) and "improved_prompt" (the complete new prompt string).
"""


class MetaPromptOutput(BaseModel):
    hypothesis: str = Field(
        description="The hypothesis for why the new prompt will be better."
    )
    improved_prompt: str = Field(description="The complete, new, improved prompt.")


# ==============================================================================
# The MetaPrompt Optimizer Class
# ==============================================================================


class MetaPromptOptimizer(BaseOptimizer):
    """
    Optimizes a prompt by using a powerful "teacher" LLM to analyze its
    performance and rewrite it.
    """

    def __init__(self, teacher_generator: LiteLLMGenerator):
        """
        Initializes the MetaPrompt Optimizer.

        Args:
            teacher_generator: A powerful generator (e.g., GPT-4o, Claude 3 Opus)
                used to analyze performance and generate new prompts.
        """
        self.teacher = teacher_generator
        self.api_key = None

    def optimize(
        self,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
        initial_prompts: List[str],
        task_description: str = "I want to improve my prompt.",
        num_rounds: Optional[int] = 5,
        eval_subset_size: Optional[int] = 40,
        resume_state: Optional[Dict[str, Any]] = None,
        max_new_rounds: Optional[int] = None,
        on_trial_callback: Optional[OnTrialCallback] = None,
        **kwargs,
    ) -> OptimizationResult:
        logger.info("--- Starting Meta-Prompt Optimization ---")

        if not initial_prompts:
            raise ValueError("Initial prompts list cannot be empty.")

        if "api_key" in kwargs:
            self.api_key = kwargs["api_key"]

        run_steps = kwargs.get("agent_optimiser_run_steps")

        current_prompt = initial_prompts[0]
        history: List[IterationHistory] = []

        def _teacher_generate(meta_prompt: str, generate_kwargs: Dict[str, Any]) -> str:
            return self.teacher.generate(
                prompt_vars={"prompt": meta_prompt}, **generate_kwargs
            )

        stepper_config = {
            "teacher_model": self.teacher.model_name,
            "num_rounds": num_rounds or 0,
            "meta_prompt_template": META_PROMPT_TEMPLATE,
            "api_key": self.api_key,
        }

        if resume_state:
            stepper = MetaPromptStepper.from_state(
                resume_state,
                config=stepper_config,
                dataset_size=len(dataset),
                teacher_generate=_teacher_generate,
            )
        else:
            stepper = MetaPromptStepper.from_config(
                initial_prompt=current_prompt,
                teacher_model=self.teacher.model_name,
                teacher_generate=_teacher_generate,
                num_rounds=num_rounds or 0,
                eval_subset_size=eval_subset_size or len(dataset),
                dataset_size=len(dataset),
                meta_prompt_template=META_PROMPT_TEMPLATE,
                api_key=self.api_key,
            )

        while True:
            candidate_prompt = stepper.next_candidate()
            if candidate_prompt is None:
                break
            if max_new_rounds is not None and max_new_rounds <= 0:
                break

            round_num = stepper.round_index + 1
            total_rounds = stepper.num_rounds
            logger.info(
                f"\n--- Starting Optimization Round {round_num}/{total_rounds} ---"
            )
            logger.info(f"Current prompt:\n{candidate_prompt}")

            eval_subset = stepper.subset_for_eval(dataset)

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=(
                    f"Trial {round_num}/{total_rounds} Running: "
                    f"Evaluating Current Prompt..."
                ),
            )

            iteration_history = self._score_prompt(
                candidate_prompt, evaluator, data_mapper, eval_subset
            )

            if not iteration_history:
                logger.warning("Evaluation of current prompt failed. Skipping round.")
                stepper.on_result(prompt=candidate_prompt, score=float("-inf"))
                if max_new_rounds is not None:
                    max_new_rounds -= 1
                continue

            history.append(iteration_history)

            stepper.on_result(
                prompt=candidate_prompt,
                score=iteration_history.average_score,
                metadata={
                    "iteration_history": iteration_history,
                    "dataset_subset": eval_subset,
                    "task_description": task_description,
                },
            )

            # Call callback to persist trial immediately
            if on_trial_callback:
                on_trial_callback(
                    trial_data=iteration_history.dict(),
                    trial_number=round_num,
                    stepper_state=stepper.to_state(),
                    is_baseline=False,
                )

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {round_num}/{total_rounds} Completed.",
            )
            if max_new_rounds is not None:
                max_new_rounds -= 1

        # final_best_generator = LiteLLMGenerator(
        #     self.teacher.model_name, stepper.best_prompt
        # )
        return OptimizationResult(
            best_prompt=stepper.best_prompt,
            history=history,
            final_score=stepper.best_score,
            stepper_state=stepper.to_state(),
        )

    def _score_prompt(
        self,
        prompt: str,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
    ) -> IterationHistory | None:
        """Scores a single prompt and returns its history.

        For prompt optimization, we pass the prompt directly as the generated output
        (not an LLM response), since the SimulationEvaluator uses the prompt to run simulations.
        """
        try:
            # Pass the prompt directly - the evaluator will use it for simulation
            generated_outputs = [prompt] * len(dataset)
            eval_inputs = [
                data_mapper.map(gen_out, ex)
                for gen_out, ex in zip(generated_outputs, dataset)
            ]
            results = evaluator.evaluate(eval_inputs)

            # Read back potentially-repaired prompt from eval_inputs.
            # DirectEvaluator mutates input_data["agent_prompt"] in place when
            # it repairs missing template variables.  Store the repaired version
            # so that history/DB reflects what was actually evaluated.
            stored_prompt = prompt
            if eval_inputs and "agent_prompt" in eval_inputs[0]:
                repaired = eval_inputs[0]["agent_prompt"]
                if repaired != prompt:
                    stored_prompt = repaired

            avg_score = (
                sum(res.score for res in results) / len(results) if results else 0.0
            )
            return IterationHistory(
                prompt=stored_prompt,
                average_score=avg_score,
                individual_results={
                    dataset[idx].get("call_execution_id", "unknown"): res
                    for idx, res in enumerate(results)
                },
            )
        except Exception as e:
            logger.error(f"Failed to score prompt: {e}")
            return None

    def _format_results(
        self, iteration_history: IterationHistory, dataset: List[Dict[str, Any]]
    ) -> str:
        """Formats the evaluation results into a string for the meta-prompt."""
        formatted_lines = []
        results_dict = iteration_history.individual_results

        for i, example_input in enumerate(dataset):
            call_id = example_input.get("call_execution_id", "unknown")
            result = results_dict.get(call_id)

            # If result missing (shouldn't happen if dataset matches), skip or handle
            if not result:
                continue

            formatted_lines.append(f"Example {i + 1}:")
            formatted_lines.append(
                f"  Input: {json.dumps(example_input, ensure_ascii=False)}"
            )
            formatted_lines.append(f"  Score: {result.score:.2f}")
            formatted_lines.append(f"  Reason: {result.reason}")
            formatted_lines.append("---")
        return "\n".join(formatted_lines)

    @staticmethod
    def _parse_output_from_json(text: str) -> Optional[MetaPromptOutput]:
        """Robutsly parse the teacher's JSON response."""
        text = text.strip()

        # --- Stage 1: Try to parse the entire string as JSON ---
        try:
            data = json.loads(text)
            return MetaPromptOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

        # --- Stage 2: Look for a JSON markdown code block ---
        try:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                return MetaPromptOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

        # --- Stage 3: Greedy fallback to find the first '{' and last '}' ---
        try:
            start_index = text.find("{")
            end_index = text.rfind("}")
            if start_index != -1 and end_index != -1 and end_index > start_index:
                json_str = text[start_index : end_index + 1]
                data = json.loads(json_str)
                return MetaPromptOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Failed to parse MetaPromptOutput from teacher response: {e}")
            return None

        return None
