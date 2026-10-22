"""Domain errors shared by metric commands and their HTTP adapters."""


class MetricInputError(ValueError):
    def __init__(self, detail: str | dict[str, str]):
        self.detail = detail
        super().__init__(str(detail))
