import json
import time
from typing import Any

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core_evals.fi_evals.base_evaluator import BaseEvaluator
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult, EvalResultMetric
from agentic_eval.core_evals.fi_utils.logging import logger

from .prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

class SRERootCauseAccuracyEvaluator(BaseEvaluator):
    """
    Evaluator that scores an AI SRE / investigation agent's final diagnosis against evidence.
    """

    def __init__(self, failure_threshold: float = 0.7, model: str = "gpt-4o"):
        self._failure_threshold = failure_threshold
        self._model_name = model
        self.llm = LLM(model_name=model)

    @property
    def name(self) -> str:
        return "SRERootCauseAccuracy"

    @property
    def display_name(self) -> str:
        return "SRE Root Cause Accuracy"

    @property
    def metric_ids(self) -> list[str]:
        return ["sre_root_cause_accuracy"]

    @property
    def required_args(self) -> list[str]:
        return ["diagnosis", "context"]

    @property
    def examples(self) -> list[Any]:
        return []

    def is_failure(self, score: float) -> bool | None:
        return bool(score < self._failure_threshold)

    def _evaluate(self, **kwargs) -> EvalResult:
        start_time = time.perf_counter()
        
        self.validate_args(**kwargs)
        
        diagnosis = kwargs.get("diagnosis", "")
        context = kwargs.get("context", "")
        if isinstance(context, list):
            context = "\n".join(str(c) for c in context)
        
        trajectory = kwargs.get("trajectory", "")
        if isinstance(trajectory, list):
            trajectory = "\n".join(str(t) for t in trajectory)

        # Build messages
        user_prompt = USER_PROMPT_TEMPLATE.replace("{{diagnosis}}", str(diagnosis))
        user_prompt = user_prompt.replace("{{context}}", str(context))
        user_prompt = user_prompt.replace("{{trajectory}}", str(trajectory))

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        metrics = []
        failure = False
        explanation = ""
        score = 0.0

        try:
            # We call _get_completion_content from LLM instance. 
            # It requires response_format if we want JSON.
            response_text = self.llm._get_completion_content(
                messages=messages,
                response_format={"type": "json_object"}
            )
            
            result_json = json.loads(response_text)
            score = float(result_json.get("score", 0.0))
            explanation = result_json.get("reason", "")
            failure = self.is_failure(score)

            metrics.append(
                EvalResultMetric(
                    id="sre_root_cause_accuracy", value=score
                )
            )
        except Exception as e:
            logger.error(f"Error occurred during eval: {e}")
            failure = True
            explanation = f"Error during evaluation: {e}"
            raise e

        end_time = time.perf_counter()
        eval_runtime_ms = int((end_time - start_time) * 1000)

        eval_result = EvalResult(
            name=self.name,
            display_name=self.display_name,
            data=kwargs,
            reason=explanation,
            runtime=eval_runtime_ms,
            model=self._model_name,
            metadata=None,
            metrics=metrics,
            failure=failure,
            datapoint_field_annotations=None,
        )
        return eval_result
