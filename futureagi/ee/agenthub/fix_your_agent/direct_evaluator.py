"""
Direct evaluator for single input/output evaluation.
Uses existing eval system via run_eval_func().

This evaluator is designed for dataset optimization where we want:
- prompt_template + input_variables → LLM call → output → eval metrics → score

No multi-turn conversation simulation is performed.
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

import structlog
import litellm

from ee.agent_opt.types import EvaluationResult
from ee.agent_opt.utils.template_variables import (
    validate_template_variables,
    repair_template_variables,
)
from model_hub.utils.scoring import score_eval_output

logger = structlog.get_logger(__name__)


def _run_async(coro):
    """Run a coroutine from sync context, handling the case where an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Event loop exists (e.g., inside Temporal async activity) but we're
    # in sync code that's blocking it. Spin up a new loop in a thread.
    result = [None]
    exc = [None]

    def _run():
        try:
            result[0] = asyncio.run(coro)
        except BaseException as e:
            exc[0] = e

    t = threading.Thread(target=_run)
    t.start()
    t.join()

    if exc[0] is not None:
        raise exc[0]
    return result[0]


class DirectEvaluator:
    """
    Evaluator for direct prompt evaluation without multi-turn simulation.
    Uses the existing eval system (run_eval_func) for evaluation.

    Flow:
    1. Take prompt template + input variables from dataset row
    2. Fill template variables with input values
    3. Call LLM to generate output
    4. Evaluate output against configured metrics using run_eval_func()
    5. Return aggregated score
    """

    def __init__(
        self,
        user_eval_configs: List[Any],  # List of SimulateEvalConfig or similar
        execution_model: str = "gpt-4o",
        initial_agent_prompt: str = "",
        organization: Optional[Any] = None,
        workspace: Optional[Any] = None,
        eval_source: str = "dataset_optimization",
        template_variables: Optional[Set[str]] = None,
        max_parallel_evals: int = 5,
    ):
        self.user_eval_configs = user_eval_configs or []
        self.execution_model = execution_model
        self.initial_agent_prompt = initial_agent_prompt
        self.organization = organization
        self.workspace = workspace
        self.eval_source = eval_source
        self.template_variables = template_variables
        self.max_parallel_evals = max_parallel_evals

        logger.info(
            "DirectEvaluator initialized",
            num_eval_configs=len(self.user_eval_configs),
            execution_model=execution_model,
            eval_config_names=[
                getattr(c, "name", None) or getattr(c, "eval_template", {})
                for c in self.user_eval_configs[:3]  # Log first 3
            ]
            if self.user_eval_configs
            else [],
        )

    def evaluate(self, inputs: List[Dict[str, Any]]) -> List[EvaluationResult]:
        """
        Evaluate each input using direct LLM call + existing eval system.
        Inputs are processed concurrently using asyncio (acompletion for LLM calls).

        Args:
            inputs: List of dicts, each containing:
                - agent_prompt: The prompt template being tested
                - input: Dict of input variables from dataset row

        Returns:
            List of EvaluationResult with score, reason, and metadata
        """
        logger.info(
            f"DirectEvaluator.evaluate called with {len(inputs)} inputs",
            num_eval_configs=len(self.user_eval_configs),
            execution_model=self.execution_model,
            max_parallel=self.max_parallel_evals,
        )

        if not inputs:
            return []

        return _run_async(self._evaluate_all_async(inputs))

    async def _evaluate_all_async(
        self, inputs: List[Dict[str, Any]]
    ) -> List[EvaluationResult]:
        semaphore = asyncio.Semaphore(self.max_parallel_evals)
        tasks = [
            self._evaluate_single_input_async(idx, input_data, semaphore)
            for idx, input_data in enumerate(inputs)
        ]
        return await asyncio.gather(*tasks)

    async def _evaluate_single_input_async(
        self, idx: int, input_data: Dict[str, Any], semaphore: asyncio.Semaphore
    ) -> EvaluationResult:
        async with semaphore:
            try:
                return await self._evaluate_single_input_impl(idx, input_data)
            except Exception as e:
                logger.error(f"Evaluation failed for input {idx}: {e}", exc_info=True)
                return EvaluationResult(score=0.0, reason=f"Evaluation failed: {e}")

    async def _evaluate_single_input_impl(
        self, idx: int, input_data: Dict[str, Any]
    ) -> EvaluationResult:
        try:
            prompt_template = input_data.get("agent_prompt", "")
            input_vars = input_data.get("input", {})

            if not prompt_template:
                logger.warning(
                    f"Empty agent_prompt for input {idx} - this will produce poor results",
                    input_keys=list(input_vars.keys()) if input_vars else [],
                    available_keys=list(input_data.keys()),
                )

            if self.template_variables:
                is_valid, error_msg = validate_template_variables(
                    self.initial_agent_prompt,
                    prompt_template,
                    self.template_variables,
                )
                if not is_valid:
                    logger.warning(
                        f"Template variables missing for input {idx}, attempting LLM repair",
                        error=error_msg,
                        prompt_preview=prompt_template[:200],
                    )
                    prompt_template = repair_template_variables(
                        candidate_prompt=prompt_template,
                        original_prompt=self.initial_agent_prompt,
                        required_variables=self.template_variables,
                    )
                    input_data["agent_prompt"] = prompt_template

            logger.info(
                f"Processing input {idx}",
                prompt_len=len(prompt_template),
                prompt_preview=prompt_template[:100] if prompt_template else "(empty)",
                input_keys=list(input_vars.keys()) if input_vars else [],
            )

            filled_prompt = self._fill_template(prompt_template, input_vars)

            if filled_prompt == prompt_template and "{{" in prompt_template:
                logger.warning(
                    "Template was NOT filled - no substitutions made!",
                    template=prompt_template[:200],
                    input_var_keys=list(input_vars.keys()),
                )

            logger.info(
                "Calling LLM with filled prompt",
                filled_prompt_len=len(filled_prompt),
                filled_prompt_preview=filled_prompt[:300]
                if filled_prompt
                else "(empty)",
            )
            llm_output = await self._call_llm_async(filled_prompt)

            loop = asyncio.get_event_loop()
            eval_scores = []
            for eval_config in self.user_eval_configs:
                try:
                    score, reason = await loop.run_in_executor(
                        None,
                        self._run_eval_with_config,
                        eval_config,
                        input_vars,
                        llm_output,
                        filled_prompt,
                    )
                    eval_scores.append((score, reason))
                except Exception as e:
                    logger.warning(f"Eval failed: {e}", exc_info=True)
                    eval_scores.append((0.0, str(e)))

            scores_only = [s for s, _ in eval_scores]
            if scores_only:
                final_score = sum(scores_only) / len(scores_only)
                reasons = [r for _, r in eval_scores if r]
                reason_text = (
                    "; ".join(reasons) if reasons else "Evaluated successfully"
                )
            else:
                if llm_output and not llm_output.startswith("Error:"):
                    final_score = 0.5
                    reason_text = (
                        "No evals configured - LLM output generated successfully"
                    )
                else:
                    final_score = 0.0
                    reason_text = (
                        f"No evals configured and LLM call failed: {llm_output}"
                    )

            logger.info(
                f"Evaluation complete for input {idx}",
                final_score=final_score,
                num_evals_run=len(eval_scores),
                output_len=len(llm_output) if llm_output else 0,
            )

            return EvaluationResult(
                score=final_score,
                reason=reason_text,
                metadata={
                    "input": input_vars,
                    "output": llm_output,
                    "filled_prompt": filled_prompt,
                    "individual_scores": eval_scores,
                },
            )

        except Exception as e:
            logger.error(f"Evaluation failed for input {idx}: {e}", exc_info=True)
            return EvaluationResult(score=0.0, reason=f"Evaluation failed: {e}")

    def _fill_template(self, template: str, variables: Dict[str, Any]) -> str:
        """Replace {variable} and {{variable}} placeholders with actual values."""
        import re

        result = template
        filled_count = 0

        for key, value in variables.items():
            str_value = str(value) if value else ""
            # Handle double braces {{variable}} (RunPrompter format)
            double_brace = f"{{{{{key}}}}}"
            if double_brace in result:
                result = result.replace(double_brace, str_value)
                filled_count += 1
            # Handle single braces {variable} (standard format)
            single_brace = f"{{{key}}}"
            if single_brace in result:
                result = result.replace(single_brace, str_value)
                filled_count += 1

        # Check for unfilled placeholders
        remaining_double = re.findall(r"\{\{(\w+)\}\}", result)
        remaining_single = re.findall(r"\{(\w+)\}", result)

        if remaining_double or remaining_single:
            logger.warning(
                "Template has unfilled placeholders after filling",
                remaining_double_brace=remaining_double,
                remaining_single_brace=remaining_single,
                available_keys=list(variables.keys()),
                template_preview=template[:200],
            )

        logger.info(
            "Template filling complete",
            original_len=len(template),
            filled_len=len(result),
            placeholders_filled=filled_count,
            result_preview=result[:200] if result else "(empty)",
        )

        return result

    async def _call_llm_async(self, prompt: str) -> str:
        logger.info(
            f"DirectEvaluator._call_llm_async called",
            model=self.execution_model,
            prompt_len=len(prompt),
        )
        try:
            response = await litellm.acompletion(
                temperature=0.1,
                seed=47,
                model=self.execution_model,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.choices[0].message.content
            logger.info(
                f"LLM call succeeded",
                output_len=len(output) if output else 0,
            )
            return output
        except Exception as e:
            logger.error(f"LLM call failed: {e}", exc_info=True)
            return f"Error: {e}"

    def _run_eval_with_config(
        self,
        eval_config,
        input_data: Dict[str, Any],
        output: str,
        prompt: str,
    ) -> Tuple[float, str]:
        """
        Run evaluation using existing eval system (run_eval_func).
        Pattern borrowed from SimulationEvaluator._run_eval_with_config().

        Args:
            eval_config: The evaluation config with template and mapping
            input_data: Dict of input variables from dataset row
            output: The LLM-generated output
            prompt: The filled prompt that was sent to LLM

        Returns:
            Tuple of (score, reason)
        """
        from model_hub.views.utils.evals import run_eval_func
        from model_hub.models.evals_metric import EvalTemplate

        eval_template = eval_config.eval_template

        # Skip only when the template is a labeled-choices eval AND no
        # choice_scores mapping is configured — score_eval_output has nothing
        # to map the labels through, so hedging at 0.5 stays correct.
        # Templates WITH choice_scores now flow through the scorer.
        output_type = (
            eval_template.config.get("output", "") if eval_template.config else ""
        )
        if output_type.lower() == "choices":
            inner_config = (
                eval_template.config.get("config", {}) if eval_template.config else {}
            )
            choices = (
                inner_config.get("choices", [])
                if isinstance(inner_config, dict)
                else []
            )
            all_numeric = not choices or all(
                isinstance(c, (int, float))
                or (isinstance(c, str) and c.replace(".", "", 1).lstrip("-").isdigit())
                for c in choices
            )
            has_choice_scores = bool(getattr(eval_template, "choice_scores", None))
            if not all_numeric and not has_choice_scores:
                return 0.5, "skipped_choices"

        # Prepare mappings - substitute placeholder values with actual data
        mapping = eval_config.mapping.copy() if eval_config.mapping else {}
        updated_mapping = {}

        for key, value in mapping.items():
            if value in ("output", "response"):
                updated_mapping[key] = output
            elif value in ("input", "query"):
                # For "input"/"query", try to get a meaningful text representation
                # First try common query field names, then fall back to JSON
                query_text = (
                    input_data.get("query")
                    or input_data.get("question")
                    or input_data.get("prompt")
                    or input_data.get("input")
                    or input_data.get("text")
                )
                if query_text:
                    updated_mapping[key] = str(query_text)
                else:
                    # Fall back to JSON for structured data (better than Python repr)
                    import json

                    try:
                        updated_mapping[key] = json.dumps(
                            input_data, indent=2, default=str
                        )
                    except Exception:
                        updated_mapping[key] = str(input_data)
            elif value in ("prompt", "agent_prompt"):
                updated_mapping[key] = prompt
            elif value in input_data:
                # Direct column reference
                updated_mapping[key] = str(input_data.get(value, ""))
            else:
                updated_mapping[key] = value

        # Try to get template from DB for full run_eval_func support
        try:
            db_template = EvalTemplate.objects.get(id=eval_template.id)
            eval_template_for_runner = db_template
        except Exception:
            eval_template_for_runner = eval_template

        # Prepare config
        config = eval_config.config.copy() if eval_config.config else {}

        # Run evaluation using existing infrastructure
        try:
            logger.info(
                "Calling run_eval_func",
                config=config,
                mappings_keys=list(updated_mapping.keys()),
                mappings_preview={k: str(v)[:100] for k, v in updated_mapping.items()},
                template_id=str(eval_template.id)
                if hasattr(eval_template, "id")
                else None,
                template_name=getattr(eval_template, "name", None),
            )

            eval_result = run_eval_func(
                config=config,
                mappings=updated_mapping,
                template=eval_template_for_runner,
                org=self.organization,
                model=eval_template.model or self.execution_model,
                workspace=self.workspace,
                source=self.eval_source,
            )

            # DEBUG: Log the full eval result
            logger.info(
                "run_eval_func returned",
                eval_result_type=type(eval_result).__name__,
                eval_result=eval_result,
                eval_result_keys=list(eval_result.keys())
                if isinstance(eval_result, dict)
                else None,
            )

            # Extract score from result
            if isinstance(eval_result, dict):
                raw_output = eval_result.get("output")
                score = score_eval_output(raw_output, eval_template, default_score=0.5)
                reason = eval_result.get("reason") or ""

                if eval_template.config and eval_template.config.get(
                    "reverse_output", False
                ):
                    score = 1.0 - score

                return score, reason
            else:
                logger.warning(
                    "eval_result is not a dict",
                    eval_result_type=type(eval_result).__name__,
                    eval_result=eval_result,
                )
                return 0.0, f"Unexpected result type: {type(eval_result)}"

        except Exception as e:
            logger.warning(f"run_eval_func failed: {e}", exc_info=True)
            # Fallback: direct eval class instantiation
            return self._run_eval_direct(eval_template, updated_mapping)

    def _run_eval_direct(
        self, eval_template, mapping: Dict[str, Any]
    ) -> Tuple[float, str]:
        """Fallback: run eval by directly instantiating the eval class."""
        from agentic_eval.core_evals import fi_evals

        template_config = eval_template.config or {}
        eval_type_id = template_config.get("eval_type_id")
        if not eval_type_id:
            return 0.0, "No eval_type_id configured"

        eval_class = getattr(fi_evals, eval_type_id, None)
        if not eval_class:
            return 0.0, f"Eval class {eval_type_id} not found"

        try:
            config = template_config.copy()
            eval_instance = eval_class(**config)
            result = eval_instance.run(**mapping)

            if hasattr(result, "eval_results") and result.eval_results:
                score = score_eval_output(result, eval_template, default_score=0.5)
                first_res = result.eval_results[0]
                if isinstance(first_res, list) and first_res:
                    first_res = first_res[0]
                reason = (
                    first_res.get("reason", "") if isinstance(first_res, dict) else ""
                )
                return score, reason
            return 0.0, "No eval results"
        except Exception as e:
            logger.error(f"Direct eval failed: {e}", exc_info=True)
            return 0.0, str(e)
