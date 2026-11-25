"""Domain errors shared by metric commands and their HTTP adapters."""


class MetricInputError(ValueError):
    def __init__(self, detail: str | dict[str, str]):
        self.detail = detail
        super().__init__(str(detail))


class MetricBusyError(MetricInputError):
    """An interactive read was refused by its concurrency guard; retry later."""

    def __init__(self, detail: str, retry_after: int = 15):
        self.retry_after = retry_after
        super().__init__(detail)
