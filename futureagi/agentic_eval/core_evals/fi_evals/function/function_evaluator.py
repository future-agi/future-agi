import time

from agentic_eval.core_evals.fi_metrics.metric_type import MetricType
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult, EvalResultMetric
from agentic_eval.core_evals.fi_utils.logging import logger

from ..base_evaluator import BaseEvaluator
from .functions import operations


class FunctionEvaluator(BaseEvaluator):
    _display_name: str
    _function_name: str
    _function_arguments: dict

    """
    This evaluator runs the requested Function on the given data.
    """

    @property
    def _model(self):
        return None

    @property
    def name(self):
        return self._function_name

    @property
    def display_name(self):
        return self._display_name

    @property
    def metric_ids(self) -> list[str]:
        return [MetricType.PASSED.value]

    @property
    def default_function_arguments(self):
        return {}

    @property
    def required_args(self):
        return []  # validate_args function is implemented explicitly

    @property
    def examples(self):
        return None

    def validate_args(self, **kwargs) -> None:
        return

    def __init__(
        self,
        function_name: str | None = None,
        function_arguments: dict | None = None,
        display_name=None,
    ):
        if function_name is None:
            raise ValueError("function_name is a required argument")
        if function_arguments is None:
            function_arguments = self.default_function_arguments
        if function_name not in operations.keys():
            raise ValueError(f"Unsupported function: {function_name}")
        else:
            self._function_name = function_name
            self._function_arguments = function_arguments
            self._display_name = display_name or function_name
        self.cost = {
            "total_cost": 0,
            "prompt_cost": 0,
            "completion_cost": 0,
        }

    def is_failure(self, eval_response) -> bool | None:
        return (
            not eval_response["result"]
            if eval_response is not None and "result" in eval_response
            else None
        )

    def to_config(self) -> dict | None:
        if not self._function_arguments:
            return None
        else:
            return self._function_arguments

    def _evaluate(self, **kwargs) -> EvalResult:
        """
        Run the Function evaluator.
        """
        start_time = time.perf_counter()

        # Validate that correct args were passed
        self.validate_args(**kwargs)
        metrics: list[EvalResultMetric] = []
        try:
            # Evaluate the dataset using Function
            operator = operations.get(self._function_name)
            if (operator is None) or (not callable(operator)):
                raise ValueError(f"Unsupported function: {self._function_name}")
            eval_response = operator(**kwargs, **self._function_arguments)
            result_value = eval_response["result"]
            if result_value is None:
                reason = eval_response.get("reason", "Unknown error")
                raise ValueError(
                    f"Evaluation failed for '{self._function_name}': {reason}"
                )
            metrics.append(
                EvalResultMetric(id=MetricType.PASSED.value, value=float(result_value))
            )
            explanation = eval_response["reason"]
            failure = self.is_failure(eval_response)
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error occurred during eval: {e}")
            raise ValueError(
                f"Evaluation failed for '{self._function_name}': {e}"
            ) from e

        end_time = time.perf_counter()
        eval_runtime_ms = int((end_time - start_time) * 1000)
        eval_result: EvalResult = {
            "name": self.name,
            "display_name": self.display_name,
            "data": kwargs,
            "reason": explanation,
            "runtime": eval_runtime_ms,
            "model": None,
            "metadata": None,
            "metrics": metrics,
            "failure": failure,
            "datapoint_field_annotations": None,
        }
        return eval_result
