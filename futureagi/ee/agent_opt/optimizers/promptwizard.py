import json
import random
import re
from typing import Any, Dict, List, Optional, Set, Callable

import structlog

logger = structlog.get_logger(__name__)

from pydantic import BaseModel, Field, ValidationError

from ..base.base_generator import BaseGenerator
from ..base.base_optimizer import BaseOptimizer
from ..datamappers.basic_mapper import BasicDataMapper
from ..base.evaluator import Evaluator
from ..generators.litellm_generator import LiteLLMGenerator
from ..types import IterationHistory, OptimizationResult
from simulate.utils.agent_prompt_optimiser import update_agent_optimiser_run_step
from .stepper import PromptWizardStepper

# Type for the per-trial callback
OnTrialCallback = Callable[[dict, int, Dict[str, Any], bool], None]

MUTATE_PROMPT = """
You are an expert in prompt engineering. You will be given a task description and different styles known as meta prompts. Your task is to generate {num_variations} diverse variations of the following instruction by adaptively mixing meta prompt while keeping similar semantic meaning.

[Task Description]: {task_description}
[Meta Prompts]: {meta_prompts}
[Prompt Instruction]: {prompt_instruction}

Return ONLY a valid JSON object with a single key "variations" containing a list of the new prompt strings.
"""

CRITIQUE_PROMPT = """
You are an expert prompt engineering analyst. My current prompt is:
---
{instruction}
---
This prompt performed poorly on the following examples:
---
{examples}
---
Provide a detailed critique explaining the potential reasons for failure.

Return ONLY a valid JSON object with a single key "variations" containing a list with ONE string: your critique.
"""

REFINE_PROMPT = """
You are an expert prompt engineer. My current prompt is:
---
{instruction}
---
It failed on these examples:
---
{examples}
---
Here is a critique of the prompt's weaknesses: "{critique}"

Based on this critique, write {steps_per_sample} different, improved versions of the prompt.

Return ONLY a valid JSON object with a single key "variations" containing a list of the new prompt strings.
"""


class Variations(BaseModel):
    variations: List[str]


class PromptWizardOptimizer(BaseOptimizer):
    """
    An adapter for the PromptWizard optimization algorithm, using a multi-stage
    process of mutation, critique, and refinement for prompt instructions.
    """

    def __init__(
        self,
        teacher_generator: LiteLLMGenerator,
        mutate_rounds: int = 3,
        refine_iterations: int = 2,
        beam_size: int = 1,
    ):
        self.teacher = teacher_generator
        self.mutate_rounds = mutate_rounds
        self.refine_iterations = refine_iterations
        self.beam_size = beam_size
        self.thinking_styles = THINKING_STYLES
        logger.info("--- PromptWizard Optimizer Initialized ---")
        logger.debug(
            f"Initialized with: mutate_rounds={mutate_rounds}, "
            f"refine_iterations={refine_iterations}, beam_size={beam_size}"
        )
        self.api_key = None

    def optimize(
        self,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
        initial_prompts: List[str],
        task_description: str = "No task description given.",
        resume_state: Optional[Dict[str, Any]] = None,
        max_new_iterations: Optional[int] = None,
        on_trial_callback: Optional[OnTrialCallback] = None,
        **kwargs: Any,
    ) -> OptimizationResult:
        eval_subset_size = kwargs.get("eval_subset_size", 25)
        logger.info("--- Starting PromptWizard Optimization ---")
        logger.debug(f"Task: {task_description}")
        logger.debug(f"Initial prompts count: {len(initial_prompts)}")
        logger.debug(f"Dataset size: {len(dataset)}")
        logger.debug(f"Evaluation subset size: {eval_subset_size}")

        if "api_key" in kwargs:
            self.api_key = kwargs["api_key"]

        run_steps = kwargs.get("agent_optimiser_run_steps")

        if not initial_prompts:
            raise ValueError("Initial prompts list cannot be empty.")

        if resume_state:
            stepper = PromptWizardStepper.from_state(
                resume_state,
                config={"refine_iterations": self.refine_iterations},
            )
            if not stepper.current_best_instruction:
                stepper.current_best_instruction = initial_prompts[0]
        else:
            stepper = PromptWizardStepper.from_config(
                refine_iterations=self.refine_iterations,
                initial_prompt=initial_prompts[0],
            )
        history: List[IterationHistory] = []
        logger.info(
            f"Initial best instruction: '{stepper.current_best_instruction[:100]}...'"
        )

        while True:
            if max_new_iterations is not None and max_new_iterations <= 0:
                break
            current_instruction = stepper.next_candidate()
            if current_instruction is None:
                break

            i = stepper.iteration_index
            logger.info(
                f"\n--- Instruction Refinement Iteration {i + 1}/{self.refine_iterations} ---"
            )

            # 1. Mutate
            logger.info("Step 1: Mutating instruction...")

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {i + 1}/{self.refine_iterations} Running: Mutating Instruction...",
            )

            mutated_prompts = self._mutate_instruction(
                current_instruction, task_description
            )
            candidate_pool = {current_instruction, *mutated_prompts}
            logger.info(f"Generated {len(mutated_prompts)} unique new prompts.")
            logger.debug(f"Candidate pool size: {len(candidate_pool)}")

            # 2. Score
            logger.info("Step 2: Scoring candidate prompts...")
            eval_subset = random.sample(dataset, min(len(dataset), eval_subset_size))
            logger.debug(f"Scoring against a subset of {len(eval_subset)} examples.")

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {i + 1}/{self.refine_iterations} Running: Scoring Candidates...",
            )

            iteration_history = self._score_candidates(
                list(candidate_pool), evaluator, data_mapper, eval_subset
            )
            history.extend(iteration_history)

            sorted_by_score = sorted(
                iteration_history, key=lambda x: x.average_score, reverse=True
            )
            top_prompts_this_round = [
                item.prompt for item in sorted_by_score[: self.beam_size]
            ]
            logger.info(f"Top {self.beam_size} prompts selected for refinement.")
            for idx, p in enumerate(top_prompts_this_round):
                score = sorted_by_score[idx].average_score
                logger.debug(f"  - Prompt (Score: {score:.4f}): '{p[:100]}...'")

            # 3. Critique and Refine
            logger.info("Step 3: Critiquing and refining top prompts...")

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {i + 1}/{self.refine_iterations} Running: Critiquing & Refining...",
            )

            refined_prompts = set()
            for prompt_to_refine in top_prompts_this_round:
                errors = self._get_errors(
                    prompt_to_refine, evaluator, data_mapper, dataset
                )
                if errors:
                    logger.debug(
                        f"Found {len(errors)} errors for prompt: '{prompt_to_refine[:100]}...'"
                    )
                    refined = self._critique_and_refine(prompt_to_refine, errors)
                    if refined:
                        logger.debug(f"Successfully refined prompt.")
                        refined_prompts.add(refined)
                else:
                    logger.debug(
                        f"No errors found for prompt, skipping refinement: '{prompt_to_refine[:100]}...'"
                    )

            # Determine the best instruction for the next iteration
            if refined_prompts:
                logger.info(
                    f"Scoring {len(refined_prompts)} refined prompts to find new best."
                )
                final_candidates_this_round = {
                    stepper.current_best_instruction,
                    *refined_prompts,
                }
                final_history = self._score_candidates(
                    list(final_candidates_this_round),
                    evaluator,
                    data_mapper,
                    eval_subset,
                )
                history.extend(final_history)
                best_instruction = sorted(
                    final_history, key=lambda x: x.average_score, reverse=True
                )[0].prompt
            else:
                logger.info("No prompts were refined, carrying over previous best.")
                best_instruction = top_prompts_this_round[0]

            logger.info(
                f"Best instruction after iteration {i + 1}: '{best_instruction[:100]}...'"
            )

            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {i + 1}/{self.refine_iterations} Completed.",
            )
            best_score_iteration = max(
                (h.average_score for h in history if h.prompt == best_instruction),
                default=0.0,
            )
            stepper.on_result(prompt=best_instruction, score=best_score_iteration)

            # Call callback to persist trial immediately
            if on_trial_callback:
                # Create iteration history for the best instruction
                best_history = next(
                    (h for h in history if h.prompt == best_instruction), None
                )
                if best_history:
                    on_trial_callback(
                        trial_data=best_history.dict(),
                        trial_number=i + 1,
                        stepper_state=stepper.to_state(),
                        is_baseline=False,
                    )

            if max_new_iterations is not None:
                max_new_iterations -= 1

        logger.info("--- PromptWizard Optimization Finished ---")
        final_history = sorted(history, key=lambda x: x.average_score, reverse=True)
        if final_history:
            best_prompt = final_history[0].prompt
            best_score = final_history[0].average_score
        else:
            best_prompt = stepper.best_prompt
            best_score = stepper.best_score
        logger.info(f"Final best prompt (Score: {best_score:.4f}): '{best_prompt}'")

        # final_best_generator = LiteLLMGenerator(self.teacher.model_name, best_prompt)
        return OptimizationResult(
            best_prompt=best_prompt,
            history=history,
            final_score=best_score,
            stepper_state=stepper.to_state(),
        )

    def _mutate_instruction(
        self,
        base_instruction: str,
        task_description: str,
    ) -> Set[str]:
        logger.debug(
            f"Entering mutation phase for instruction: '{base_instruction[:100]}...'"
        )
        all_variations = set()
        temp_generator = LiteLLMGenerator(self.teacher.model_name, "{prompt}")
        for i in range(self.mutate_rounds):
            logger.debug(f"Mutation round {i + 1}/{self.mutate_rounds}")
            self.thinking_styles = random.sample(
                THINKING_STYLES, self.refine_iterations
            )
            prompt = MUTATE_PROMPT.format(
                num_variations=len(self.thinking_styles),
                task_description=task_description,
                meta_prompts="\n".join(self.thinking_styles),
                prompt_instruction=base_instruction,
            )

            generate_kwargs = {"response_format": {"type": "json_object"}}
            if self.api_key:
                generate_kwargs["api_key"] = self.api_key

            response_text = temp_generator.generate(
                {"prompt": prompt}, **generate_kwargs
            )
            variations = self._parse_variations_from_json(response_text)
            logger.debug(f"Generated {len(variations)} variations in this round.")
            all_variations.update(variations)
        logger.debug(f"Total unique variations from mutation: {len(all_variations)}")
        return all_variations

    def _critique_and_refine(
        self,
        prompt: str,
        errors: List[Dict[str, Any]],
    ) -> str | None:
        logger.debug(f"Entering critique and refine for: '{prompt[:100]}...'")
        error_str = json.dumps(errors, indent=2, ensure_ascii=False)

        logger.debug("Generating critique...")
        critique_prompt = CRITIQUE_PROMPT.format(instruction=prompt, examples=error_str)

        generate_kwargs = {"response_format": {"type": "json_object"}}
        if self.api_key:
            generate_kwargs["api_key"] = self.api_key

        critique_response = self.teacher.generate(
            {"prompt": critique_prompt}, **generate_kwargs
        )
        critiques = self._parse_variations_from_json(critique_response)
        if not critiques:
            logger.warning("Critique generation failed, skipping refinement.")
            return None
        critique_text = "\n".join([critique for critique in critiques])
        logger.debug(f"Generated critique: '{critique_text[:100]}...'")

        logger.debug("Refining prompt based on critique...")
        refine_prompt = REFINE_PROMPT.format(
            instruction=prompt,
            examples=error_str,
            critique=critique_text,
            steps_per_sample=1,
        )

        generate_kwargs = {"response_format": {"type": "json_object"}}
        if self.api_key:
            generate_kwargs["api_key"] = self.api_key

        refined_text = self.teacher.generate(
            {"prompt": refine_prompt}, **generate_kwargs
        )

        refined_prompts = self._parse_variations_from_json(refined_text)
        if refined_prompts:
            logger.debug(f"Refined prompt: '{refined_prompts[0][:100]}...'")
            return refined_prompts[0]
        else:
            logger.warning("Refinement step produced no new prompts.")
            return None

    # Helper methods (shared with ProTeGi, kept here for encapsulation)
    def _get_errors(
        self,
        prompt: str,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
        sample_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get examples where the prompt performed poorly.

        For prompt optimization, we pass the prompt directly as the generated output
        (not an LLM response), since the SimulationEvaluator uses the prompt to run simulations.
        """
        logger.debug(f"Getting errors for prompt: '{prompt[:100]}...'")
        subset = random.sample(dataset, min(len(dataset), sample_size))
        generated_outputs = [prompt] * len(subset)
        eval_inputs = [
            data_mapper.map(gen_out, ex)
            for gen_out, ex in zip(generated_outputs, subset)
        ]
        results = evaluator.evaluate(eval_inputs)
        scored = sorted(enumerate(results), key=lambda x: x[1].score)
        num_errors = max(1, len(scored) // 2)
        errors = [
            {"inputs": subset[i], "output": generated_outputs[i], "score": res.score}
            for i, res in scored[:num_errors]
        ]
        logger.debug(
            f"Selected bottom {num_errors} examples (worst: {scored[0][1].score:.3f}, "
            f"threshold: {scored[num_errors - 1][1].score:.3f})."
        )
        return errors

    def _score_candidates(
        self,
        prompts: List[str],
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
    ) -> List[IterationHistory]:
        """Score candidate prompts.

        For prompt optimization, we pass the prompt directly as the generated output
        (not an LLM response), since the SimulationEvaluator uses the prompt to run simulations.
        """
        logger.debug(f"Scoring {len(prompts)} candidate prompts.")
        histories = []
        for i, prompt in enumerate(prompts):
            # Pass the prompt directly - the evaluator will use it for simulation
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
            logger.debug(
                f"  - Scored prompt {i + 1}/{len(prompts)} (Avg Score: {avg_score:.4f}): '{prompt[:100]}...'"
            )
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

        # --- Stage 1: Try to parse the entire string as JSON ---
        try:
            data = json.loads(text)
            return Variations.model_validate(data).variations
        except (json.JSONDecodeError, ValidationError):
            # This is expected if there's extra text, so we continue.
            pass

        # --- Stage 2: Look for a JSON markdown code block ---
        try:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                return Variations.model_validate(data).variations
        except (json.JSONDecodeError, ValidationError):
            pass

        # --- Stage 3: Greedy fallback to find the first '{' and last '}' ---
        try:
            start_index = text.find("{")
            end_index = text.rfind("}")
            if start_index != -1 and end_index != -1 and end_index > start_index:
                json_str = text[start_index : end_index + 1]
                data = json.loads(json_str)
                return Variations.model_validate(data).variations
        except (json.JSONDecodeError, ValidationError) as e:
            # If all parsing attempts fail, log the error and return empty.
            logger.error(
                f"Failed to parse teacher model JSON response after all fallbacks: {e}"
            )
            logger.debug(f"Raw problematic output that failed parsing:\n{text}")
            return []

        # If no JSON object is found at all
        logger.warning("Could not find any JSON in the teacher's response.")
        logger.debug(f"Raw response with no JSON:\n{text}")
        return []


# Static list of thinking styles from PromptWizard's config
THINKING_STYLES = [
    "How could I devise an experiment to help solve that problem?",
    "Make a list of ideas for solving this problem, and apply them one by one.",
    "How can I simplify the problem so that it is easier to solve?",
    "What are the key assumptions underlying this problem?",
    "Critical Thinking: Analyze the problem from different perspectives, questioning assumptions.",
    "Try creative thinking, generate innovative and out-of-the-box ideas.",
    "Use systems thinking: Consider the problem as part of a larger interconnected system.",
    "Let's think step by step.",
]
