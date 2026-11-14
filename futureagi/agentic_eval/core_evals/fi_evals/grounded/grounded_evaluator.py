import time

from agentic_eval.core_evals.fi_metrics.metric_type import MetricType
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult, EvalResultMetric
from agentic_eval.core_evals.fi_utils.logging import logger

from ..base_evaluator import BaseEvaluator
from .similarity import (
    Comparator,
    CosineSimilarity,
    JaccardSimilarity,
    JaroWincklerSimilarity,
    NormalisedLevenshteinSimilarity,
    PhoneticSimilarity,
    SorensenDiceSimilarity,
)


class GroundedEvaluator(BaseEvaluator):

    _comparator: Comparator
    _failure_threshold = None

    """
    This evaluator runs the requested grounded evaluator on the given data.
    """

    @property
    def _model(self):
        return None

    @property
    def name(self):
        return self._comparator.__class__.__name__

    @property
    def display_name(self):
        return self._comparator.__class__.__name__

    @property
    def metric_ids(self) -> list[str]:
        return [MetricType.SIMILARITY_SCORE.value]

    @property
    def examples(self):
        return None

    def __init__(
        self,
        comparator: dict | None = None,
        failure_threshold: float | None = None,
    ):
        if comparator is None:
            raise ValueError("comparator is a required argument")
        else:
            comparator_str = comparator.get("default", "").lower()
            if comparator_str == "jaccardsimilarity":
                self._comparator = JaccardSimilarity()
            elif comparator_str == "normalisedlevenshteinsimilarity":
                self._comparator = NormalisedLevenshteinSimilarity()
            elif comparator_str == "jarowincklersimilarity":
                self._comparator = JaroWincklerSimilarity()
            elif comparator_str == "sorensendicesimilarity":
                self._comparator = SorensenDiceSimilarity()
            elif comparator_str == "cosinesimilarity":
                self._comparator = CosineSimilarity()
            elif comparator_str == "phoneticsimilarity":
                self._comparator = PhoneticSimilarity()
            else:
                raise ValueError(f"Invalid comparator: {comparator_str}")
        if failure_threshold is not None and isinstance(failure_threshold, dict):
            self._failure_threshold = failure_threshold.get("default", 0.5)
        elif failure_threshold is not None:
            self._failure_threshold = failure_threshold
        else:
            self._failure_threshold = 0.5

    def _process_kwargs(self, required_args, **kwargs):
        required_args_map = {
            key: (
                "\n".join(kwargs[key])
                if key == "context" and isinstance(kwargs[key], list)
                else kwargs[key]
            )
            for key in required_args
        }
        if len(required_args_map) == 2:
            values = list(required_args_map.values())
            if all(isinstance(value, str) for value in values):
                string1, string2 = values
                return string1, string2
            else:
                raise ValueError("Both arguments must be strings.")
        else:
            raise ValueError("Exactly two arguments are required.")

    def to_config(self):
        config = {
            "similarity_function": self._comparator.__class__.__name__,
        }
        if self._failure_threshold is not None:
            config["failure_threshold"] = self._failure_threshold
        return config

    def is_failure(self, score) -> bool | None:
        return (
            bool(score < self._failure_threshold)
            if self._failure_threshold is not None
            else None
        )

    def _evaluate(self, **kwargs) -> EvalResult:
        """
        Run the Function evaluator.
        """
        start_time = time.perf_counter()

        # Validate that correct args were passed
        self.validate_args(**kwargs)
        metrics = []
        try:
            string1, string2 = self._process_kwargs(self.required_args, **kwargs)
            # Calculate the similarity score using the comparator
            similarity_score = self._comparator.compare(string1, string2)
            metrics.append(
                EvalResultMetric(
                    id=MetricType.SIMILARITY_SCORE.value, value=similarity_score
                )
            )
            if self._failure_threshold is None:
                explanation = f"Successfully calculated similarity score of {similarity_score} using {self.display_name}"
            elif bool(similarity_score < self._failure_threshold):
                explanation = f"Evaluation failed as similarity score of {similarity_score} is below the failure threshold of {self._failure_threshold} using {self.display_name}"
            else:
                explanation = f"Evaluation succeeded as similarity score of {similarity_score} is above the failure threshold of {self._failure_threshold} using {self.display_name}"

            failure = self.is_failure(similarity_score)
        except Exception as e:
            logger.error(f"Error occurred during eval: {e}")
            raise e

        end_time = time.perf_counter()
        eval_runtime_ms = int((end_time - start_time) * 1000)
        eval_result = EvalResult(
            name=self.name,
            display_name=self.display_name,
            data=kwargs,
            reason=explanation,
            runtime=eval_runtime_ms,
            model=None,
            metadata=None,
            metrics=metrics,
            failure=failure,
            datapoint_field_annotations=None,
        )
        return eval_result
