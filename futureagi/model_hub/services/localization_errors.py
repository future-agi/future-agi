"""Safe failure codes shared by the optional harness and its task runner."""

from enum import StrEnum


class LocalizationFailureCode(StrEnum):
    MISSING_TERMINAL = "missing_terminal"
    API_ERROR = "api_error"
    MAX_TURNS = "max_turns"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TERMINAL_ERROR = "terminal_error"
    UNEXPECTED_SUBTYPE = "unexpected_subtype"
    UNEXPECTED_TERMINAL_REASON = "unexpected_terminal_reason"
    MISSING_FINDINGS = "missing_findings"
    TIMEOUT = "timeout"


class LocalizationFailure(RuntimeError):
    """Never include SDK text, case data or provider responses in this error."""

    def __init__(self, code: LocalizationFailureCode) -> None:
        self.code = code
        super().__init__(f"Error localization failed ({code.value}).")
