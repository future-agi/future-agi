"""Expected evaluation refusals, available without the optional billing package."""


class EvaluationDenied(Exception):
    """A policy refusal, not an evaluator or billing infrastructure failure."""

    code = "rate_limited"

    def __init__(self, message):
        super().__init__(message)
        self.completed_results = None
        self.failed_index = None

    def as_dict(self):
        return {"code": self.code, "message": str(self)}

    def response(self, general_methods):
        response = self._response(general_methods)
        if self.completed_results is not None:
            # Earlier items have already executed and may have been charged.
            # Callers must resume at failed_index, not retry the entire batch.
            response.data.update(
                completed_results=self.completed_results,
                failed_index=self.failed_index,
                message=f"{self} Retry only items at or after failed_index.",
            )
        return response


class EvaluationRateLimited(EvaluationDenied):
    """A legacy rate window refused execution.

    The legacy limiter supplies a window label, not a reset timestamp. Do not
    invent a Retry-After value (a rolling window cannot be inferred from it).
    """

    def __init__(self):
        super().__init__(
            "Evaluation rate limit reached. Wait for the usage window to reset "
            "before retrying."
        )

    def _response(self, general_methods):
        return general_methods.too_many_requests(str(self))


def raise_if_rate_limited(api_call_log):
    """Only a confirmed denial row is a rate limit; missing/error rows are not."""
    if api_call_log is not None and api_call_log.status == "rate_limited":
        raise EvaluationRateLimited()
