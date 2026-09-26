import json
import logging
import random
import re
from typing import Any, Dict, List, Optional, Set, Callable

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from ..base.base_generator import BaseGenerator
from ..base.base_optimizer import BaseOptimizer
from ..datamappers.basic_mapper import BasicDataMapper
from ..base.evaluator import Evaluator
from ..generators.litellm_generator import LiteLLMGenerator
from ..types import IterationHistory, OptimizationResult
from simulate.utils.agent_prompt_optimiser import update_agent_optimiser_run_step
from .stepper import ProTeGiStepper

# Type for the per-trial callback
OnTrialCallback = Callable[[dict, int, Dict[str, Any], bool], None]

GET_GRADIENTS_PROMPT = """
You are an expert in prompt engineering. I'm trying to write a zero-shot classifier prompt.
My current prompt is:
---
{prompt}
---
This prompt performed poorly on the following examples:
---
{error_examples}
---
{task_description_section}
Provide {num_feedbacks} distinct reasons why the prompt could have failed. Each reason should be a concise critique.
Return ONLY a valid JSON object with a single key "variations" containing a list of strings (the critiques).
"""

APPLY_GRADIENT_PROMPT = """
You are an expert in prompt engineering. I'm trying to improve a zero-shot classifier prompt.
My current prompt is:
---
{prompt}
---
It performed poorly on these examples:
---
{error_examples}
---
A key reason for the failure is the following critique: "{feedback}"
{task_description_section}
Based on this critique, generate {num_new_prompts} different, improved versions of the prompt.
Return ONLY a valid JSON object with a single key "variations" containing a list of strings (the new prompts).
"""

PARAPHRASE_PROMPT = """
Generate {num_variations} semantic paraphrases of the following prompt. The meaning should be identical, but the wording should be different.
---
{prompt}
---
Return ONLY a valid JSON object with a single key "variations" containing a list of strings (the paraphrased prompts).
"""


class GradientVariations(BaseModel):
    variations: List[str] = Field(description="A list of generated text strings.")


class ProTeGi(BaseOptimizer):
    """
    A corrected and robust implementation of the ProTeGi optimizer.
    """

    def __init__(
        self,
        teacher_generator: LiteLLMGenerator,
        num_gradients: int = 4,
        errors_per_gradient: int = 4,
        prompts_per_gradient: int = 1,
        beam_size: int = 4,
    ):
        self.teacher = teacher_generator
        self.num_gradients = num_gradients
        self.errors_per_gradient = errors_per_gradient
        self.prompts_per_gradient = prompts_per_gradient
        self.beam_size = beam_size
        logging.info("--- ProTeGi Optimizer Initialized ---")
        self.api_key = None

    def optimize(
        self,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
        initial_prompts: List[str],
        resume_state: Optional[Dict[str, Any]] = None,
        max_new_rounds: Optional[int] = None,
        on_trial_callback: Optional[OnTrialCallback] = None,
        task_description: str = "",
        **kwargs: Any,
    ) -> OptimizationResult:
        num_rounds = kwargs.get("num_rounds", 3)
        eval_subset_size = kwargs.get("eval_subset_size", 32)

        if "api_key" in kwargs:
            self.api_key = kwargs["api_key"]

        run_steps = kwargs.get("agent_optimiser_run_steps")
        self._task_description = task_description

        beam = set(initial_prompts)
        if resume_state:
            stepper = ProTeGiStepper.from_state(
                resume_state, config={"num_rounds": num_rounds}
            )
            if not stepper.beam:
                stepper.beam = set(initial_prompts)
        else:
            stepper = ProTeGiStepper.from_config(
                num_rounds=num_rounds, initial_prompts=initial_prompts
            )

        history: List[IterationHistory] = []

        while True:
            if max_new_rounds is not None and max_new_rounds <= 0:
                break
            current_beam = stepper.next_beam()
            if current_beam is None:
                break

            round_num = stepper.round_index + 1
            logging.info(
                f"\n--- Starting Optimization Round {round_num}/{stepper.num_rounds} ---"
            )

            # 1. EXPANSION: Generate new candidates from the current beam
            current_prompts = list(current_beam)
            logging.info(
                f"Expanding {len(current_prompts)} prompts into new candidates..."
            )

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {round_num}/{stepper.num_rounds} Running: Expanding Candidates...",
            )

            expanded_prompts = self._expand_candidates(
                current_prompts, evaluator, data_mapper, dataset
            )

            # The candidate pool for this round is the union of the old beam and new prompts
            candidate_pool = current_beam.union(expanded_prompts)
            logging.info(
                f"Candidate pool for this round has {len(candidate_pool)} unique prompts."
            )

            # 2. SELECTION: Score all candidates in the pool
            eval_subset = random.sample(dataset, min(len(dataset), eval_subset_size))

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {round_num}/{stepper.num_rounds} Running: Scoring {len(candidate_pool)} Candidates...",
            )

            iteration_history = self._score_candidates(
                list(candidate_pool), evaluator, data_mapper, eval_subset
            )
            history.extend(iteration_history)

            # 3. BEAM UPDATE: Select the top N prompts for the next round
            sorted_history = sorted(
                iteration_history, key=lambda x: x.average_score, reverse=True
            )
            if not sorted_history:
                logging.warning("No successful evaluations in this round. Halting.")
                break

            new_beam = {item.prompt for item in sorted_history[: self.beam_size]}
            best_round_score = sorted_history[0].average_score
            best_round_prompt = sorted_history[0].prompt

            logging.info(f"Best score in round {round_num}: {best_round_score:.4f}")
            logging.info(f"New beam selected with {len(new_beam)} prompts.")

            stepper.on_result(
                prompt=best_round_prompt,
                score=best_round_score,
                metadata={"new_beam": new_beam},
            )

            # Call callback to persist trial immediately (use best result from round)
            if on_trial_callback:
                best_iteration = sorted_history[0]
                on_trial_callback(
                    trial_data=best_iteration.dict(),
                    trial_number=round_num,
                    stepper_state=stepper.to_state(),
                    is_baseline=False,
                )

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {round_num}/{stepper.num_rounds} Completed.",
            )
            if max_new_rounds is not None:
                max_new_rounds -= 1

        final_best_generator = LiteLLMGenerator(
            self.teacher.model_name, stepper.best_prompt
        )
        return OptimizationResult(
            best_prompt=stepper.best_prompt,
            history=history,
            final_score=stepper.best_score,
            stepper_state=stepper.to_state(),
        )

    def _expand_candidates(
        self,
        prompts: List[str],
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
    ) -> Set[str]:
        new_prompts = set()
        for i, prompt in enumerate(prompts):
            logging.debug(f"--> Expanding prompt {i + 1}/{len(prompts)}...")
            errors = self._get_errors(prompt, evaluator, data_mapper, dataset)
            if not errors:
                logging.debug(f"Prompt produced no errors. No expansion.")
                continue

            critiques = self._get_gradients(prompt, errors)
            logging.debug(f"Generated {len(critiques)} critiques (gradients).")

            for feedback in critiques:
                generated = self._apply_gradient(prompt, errors, feedback)
                if generated:
                    logging.debug(
                        f"Generated {len(generated)} new prompts from critique: '{feedback[:50]}...'"
                    )
                    new_prompts.update(generated)
        return new_prompts

    def _get_errors(
        self,
        prompt: str,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
        sample_size: int = 32,
    ) -> List[Dict[str, Any]]:
        """Find examples where the prompt performs poorly.

        For PROMPT OPTIMIZATION: the prompt itself is passed to the evaluator.
        """
        subset = random.sample(dataset, min(len(dataset), sample_size))

        generated_outputs = [prompt] * len(subset)

        eval_inputs = [
            data_mapper.map(gen_out, ex)
            for gen_out, ex in zip(generated_outputs, subset)
        ]
        results = evaluator.evaluate(eval_inputs)

        scored = sorted(zip(results, subset), key=lambda x: x[0].score)
        num_errors = max(1, len(scored) // 2)
        errors = [ex for _, ex in scored[:num_errors]]
        logging.debug(
            f"Selected bottom {num_errors} examples (worst score: {scored[0][0].score:.3f}, "
            f"threshold score: {scored[num_errors - 1][0].score:.3f}) from {len(subset)}."
        )
        return errors

    def _get_gradients(self, prompt: str, errors: List[Dict[str, Any]]) -> List[str]:
        error_sample = random.sample(errors, min(len(errors), self.errors_per_gradient))
        task_desc_section = (
            f"Task Description/Constraints:\n{self._task_description}\n"
            if getattr(self, "_task_description", "")
            else ""
        )
        critique_prompt = GET_GRADIENTS_PROMPT.format(
            prompt=prompt,
            error_examples=json.dumps(error_sample, indent=2, ensure_ascii=False),
            num_feedbacks=self.num_gradients,
            task_description_section=task_desc_section,
        )

        generate_kwargs = {"response_format": {"type": "json_object"}}
        if self.api_key:
            generate_kwargs["api_key"] = self.api_key

        for attempt in range(3):
            try:
                response_text = self.teacher.generate(
                    prompt_vars={"prompt": critique_prompt},
                    **generate_kwargs,
                )
                gradients = self._parse_variations_from_json(response_text)
                if gradients:
                    return gradients
            except Exception as e:
                logging.warning(
                    f"ProTeGi get_gradients attempt {attempt + 1} failed: {e}"
                )

        return []

    def _apply_gradient(
        self, prompt: str, errors: List[Dict[str, Any]], feedback: str
    ) -> List[str]:
        error_sample = random.sample(errors, min(len(errors), self.errors_per_gradient))
        task_desc_section = (
            f"Task Description/Constraints:\n{self._task_description}\n"
            if getattr(self, "_task_description", "")
            else ""
        )
        rewrite_prompt = APPLY_GRADIENT_PROMPT.format(
            prompt=prompt,
            error_examples=json.dumps(error_sample, indent=2, ensure_ascii=False),
            feedback=feedback,
            num_new_prompts=self.prompts_per_gradient,
            task_description_section=task_desc_section,
        )

        generate_kwargs = {"response_format": {"type": "json_object"}}
        if self.api_key:
            generate_kwargs["api_key"] = self.api_key

        for attempt in range(3):
            try:
                response_text = self.teacher.generate(
                    prompt_vars={"prompt": rewrite_prompt},
                    **generate_kwargs,
                )
                rewrites = self._parse_variations_from_json(response_text)
                if rewrites:
                    return rewrites
            except Exception as e:
                logging.warning(
                    f"ProTeGi apply_gradient attempt {attempt + 1} failed: {e}"
                )

        return []

    def _score_candidates(
        self,
        prompts: List[str],
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
    ) -> List[IterationHistory]:
        """Score candidate prompts against the dataset.

        For PROMPT OPTIMIZATION: the prompt itself is passed to the evaluator.
        """
        histories = []
        for i, prompt in enumerate(prompts):
            logging.info(
                f"--> Scoring prompt {i + 1}/{len(prompts)}: '{prompt[:100]}...'"
            )
            # For prompt optimization: THE PROMPT IS THE OUTPUT
            # We pass it directly to the evaluator (no LLM generation)
            generated_outputs = [prompt] * len(dataset)

            eval_inputs = [
                data_mapper.map(gen_out, ex)
                for gen_out, ex in zip(generated_outputs, dataset)
            ]
            results = evaluator.evaluate(eval_inputs)

            stored_prompt = prompt
            if eval_inputs and "agent_prompt" in eval_inputs[0]:
                repaired = eval_inputs[0]["agent_prompt"]
                if repaired != prompt:
                    stored_prompt = repaired

            avg_score = (
                sum(res.score for res in results) / len(results) if results else 0.0
            )
            logging.info(f"    Average score: {avg_score:.4f}")
            histories.append(
                IterationHistory(
                    prompt=stored_prompt,
                    average_score=avg_score,
                    individual_results={
                        dataset[idx].get("call_execution_id", "unknown"): res
                        for idx, res in enumerate(results)
                    },
                )
            )
        return histories

    @staticmethod
    def _parse_variations_from_json(text: str) -> List[str]:
        text = text.strip()

        try:
            data = json.loads(text)
            return GradientVariations.model_validate(data).variations
        except (json.JSONDecodeError, ValidationError):
            pass

        try:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                return GradientVariations.model_validate(data).variations
        except (json.JSONDecodeError, ValidationError):
            pass

        try:
            start_index = text.find("{")
            end_index = text.rfind("}")
            if start_index != -1 and end_index != -1 and end_index > start_index:
                json_str = text[start_index : end_index + 1]
                data = json.loads(json_str)
                return GradientVariations.model_validate(data).variations
        except (json.JSONDecodeError, ValidationError) as e:
            logging.error(
                f"Failed to parse teacher model JSON response after all fallbacks: {e}"
            )
            logging.debug(f"Raw problematic output that failed parsing:\n{text}")
            return []

        # If no JSON object is found at all
        logging.warning("Could not find any JSON in the teacher's response.")
        logging.debug(f"Raw response with no JSON:\n{text}")
        return []
