import logging
import time
from typing import Any, Dict, List, Optional, Callable

try:
    import gepa.optimize_anything as oa
    from gepa.optimize_anything import (
        optimize_anything,
        GEPAConfig,
        EngineConfig,
        ReflectionConfig,
    )
except ImportError:
    raise ImportError(
        "To use GEPAOptimizer, please install the 'gepa' library (>=0.1.1) with: pip install 'gepa>=0.1.1'"
    )

from ..base.base_optimizer import BaseOptimizer
from ..datamappers.basic_mapper import BasicDataMapper
from ..base.evaluator import Evaluator
from ..types import OptimizationResult, IterationHistory
from ..utils.template_variables import (
    extract_template_variables,
    validate_template_variables,
    repair_template_variables,
)
from simulate.utils.agent_prompt_optimiser import update_agent_optimiser_run_step

OnTrialCallback = Callable[[dict, int, Dict[str, Any], bool], None]

logger = logging.getLogger(__name__)


class GEPAOptimizer(BaseOptimizer):
    """
    Prompt optimizer backed by GEPA's optimize_anything API (gepa>=0.1.1).

    The evaluator passed to optimize_anything runs per dataset row:
      1. Repairs template variables if the reflection LM dropped them.
      2. Calls DirectEvaluator to fill the prompt template, run the LLM,
         and score the output.
      3. Logs the eval reason as Actionable Side Information (ASI) so GEPA's
         reflection LM sees *why* the candidate failed, not just that it did.
    """

    def __init__(self, reflection_model: str, generator_model: str = "gpt-4o-mini"):
        self.reflection_model = reflection_model
        self.generator_model = generator_model
        self.api_key = None
        logger.info(
            f"GEPAOptimizer initialized: reflection={reflection_model}, generator={generator_model}"
        )

    def optimize(
        self,
        evaluator: Evaluator,
        data_mapper: BasicDataMapper,
        dataset: List[Dict[str, Any]],
        initial_prompts: List[str],
        max_metric_calls: Optional[int] = 150,
        resume_state: Optional[Dict[str, Any]] = None,
        max_new_metric_calls: Optional[int] = None,
        on_trial_callback: Optional[OnTrialCallback] = None,
        task_description: str = "",
        **kwargs: Any,
    ) -> OptimizationResult:
        opt_start = time.time()
        logger.info("--- Starting GEPA Prompt Optimization (optimize_anything) ---")

        if not initial_prompts:
            raise ValueError("Initial prompts list cannot be empty for GEPAOptimizer.")

        if "api_key" in kwargs:
            self.api_key = kwargs["api_key"]

        initial_prompt = initial_prompts[0]
        required_vars = extract_template_variables(initial_prompt)
        history: List[IterationHistory] = []
        run_steps = kwargs.get("agent_optimiser_run_steps")
        prompt_trial_numbers: Dict[str, int] = {}

        completed_metric_calls = 0
        if resume_state:
            completed_metric_calls = resume_state.get("completed_metric_calls", 0)

        remaining = (
            max_metric_calls - completed_metric_calls
            if max_metric_calls is not None
            else None
        )
        if max_new_metric_calls is not None:
            remaining = (
                min(remaining, max_new_metric_calls)
                if remaining is not None
                else max_new_metric_calls
            )

        def _evaluate(candidate: str, example: Dict[str, Any]) -> tuple[float, dict]:
            prompt_text = candidate

            if required_vars:
                is_valid, _ = validate_template_variables(
                    initial_prompt, prompt_text, required_vars
                )
                if not is_valid:
                    prompt_text = repair_template_variables(
                        candidate_prompt=prompt_text,
                        original_prompt=initial_prompt,
                        required_variables=required_vars,
                    )

            eval_count = completed_metric_calls + len(history) + 1
            update_agent_optimiser_run_step(
                run_steps,
                3,
                description=f"Trial {eval_count} Running: Simulating & Evaluating...",
            )

            eval_inputs = [data_mapper.map(prompt_text, example)]
            results = evaluator.evaluate(eval_inputs)

            repaired_prompt = eval_inputs[0].get("agent_prompt", prompt_text)

            result = results[0] if results else None
            score = result.score if result else 0.0
            reason = result.reason if result else "No result."

            oa.log(f"Score: {score}")
            oa.log(f"Eval feedback: {reason}")
            if task_description:
                oa.log(f"Task constraints: {task_description}")

            item_id = (
                example.get("call_execution_id")
                or example.get("id")
                or example.get("scenario_id")
                or "unknown"
            )

            existing = next((h for h in history if h.prompt == repaired_prompt), None)
            if existing:
                existing.individual_results[str(item_id)] = result
                existing.average_score = sum(
                    r.score for r in existing.individual_results.values()
                ) / len(existing.individual_results)

                if on_trial_callback:
                    on_trial_callback(
                        trial_data=existing.dict(),
                        trial_number=prompt_trial_numbers[repaired_prompt],
                        stepper_state={
                            "completed_metric_calls": completed_metric_calls
                            + len(history),
                            "best_prompt": repaired_prompt,
                            "best_score": existing.average_score,
                        },
                        is_baseline=False,
                    )
            else:
                iteration_history = IterationHistory(
                    prompt=repaired_prompt,
                    average_score=score,
                    individual_results={str(item_id): result} if result else {},
                )
                history.append(iteration_history)
                prompt_trial_numbers[repaired_prompt] = eval_count

                if on_trial_callback:
                    on_trial_callback(
                        trial_data=iteration_history.dict(),
                        trial_number=eval_count,
                        stepper_state={
                            "completed_metric_calls": completed_metric_calls
                            + len(history),
                            "best_prompt": repaired_prompt,
                            "best_score": score,
                        },
                        is_baseline=False,
                    )

            return score, {
                "Input": str(example),
                "Feedback": reason,
            }

        background = task_description if task_description else None

        if len(dataset) >= 3:
            split_idx = int(len(dataset) * 0.8)
            train_data = dataset[:split_idx]
            val_data = dataset[split_idx:]
        else:
            train_data = dataset
            val_data = dataset

        gepa_result = optimize_anything(
            seed_candidate=initial_prompt,
            evaluator=_evaluate,
            dataset=train_data,
            valset=val_data,
            objective=task_description
            if task_description
            else "Improve the prompt so the LLM output scores higher on all evaluation criteria.",
            background=background,
            config=GEPAConfig(
                engine=EngineConfig(
                    max_metric_calls=remaining,
                    display_progress_bar=True,
                ),
                reflection=ReflectionConfig(
                    reflection_lm=self.reflection_model,
                ),
            ),
        )

        best_candidate = gepa_result.best_candidate
        if isinstance(best_candidate, dict):
            best_prompt = next(iter(best_candidate.values()))
        else:
            best_prompt = str(best_candidate)

        best_score = float(gepa_result.val_aggregate_scores[gepa_result.best_idx])
        completed_metric_calls += len(history)

        logger.info(
            f"GEPA finished in {time.time() - opt_start:.2f}s. Best score: {best_score:.4f}"
        )

        return OptimizationResult(
            best_prompt=best_prompt,
            history=history,
            final_score=best_score,
            stepper_state={
                "completed_metric_calls": completed_metric_calls,
                "best_prompt": best_prompt,
                "best_score": best_score,
            },
        )
