from .metric import Metric


class Passed(Metric):
    """
    Boolean metric indicating whether the evaluation passed the specified criteria.
    """

    @staticmethod
    def compute(passed: int | bool):
        """
        Computes the result.

        Returns:
            bool: Whether the evaluation passed or not.
        """
        return bool(passed)
