from agentic_eval.core_evals.fi_evals import GroundedEvaluator
from agentic_eval.core_evals.fi_evals.grounded.similarity import Comparator


class AnswerSimilarity(GroundedEvaluator):
    @property
    def required_args(self):
        return ["response", "expected_response"]

    def __init__(self, comparator: Comparator, failure_threshold: float | None = None):
        """
        Initialize the grounded evaluator with a particular comparator.

        Args:
            comparator (Comparator): Concrete comparator to be used for comparison.
            failure_threshold (float): Threshold for failure. If the similarity score is below this threshold it's marked as failed.
        Example:
            >>> AnswerSimilarity(comparator=CosineSimilarity())
            >>> AnswerSimilarity(comparator=CosineSimilarity(), failure_threshold=0.8)

        """
        super().__init__(comparator=comparator, failure_threshold=failure_threshold)
