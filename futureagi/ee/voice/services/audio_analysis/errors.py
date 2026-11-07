"""Terminal failures carry public reason codes, never storage/decoder details."""

from .constants import FAILED_REASONS


class AudioDeterministicError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        self.state = "failed" if reason in FAILED_REASONS else "unavailable"
        super().__init__(reason)


class AudioProvenanceError(AudioDeterministicError):
    pass


class AudioLimitError(AudioDeterministicError):
    def __init__(self):
        super().__init__("limit_exceeded")


class AudioModelUnavailableError(AudioDeterministicError):
    def __init__(self):
        super().__init__("model_unavailable")


class StaleAnalysisError(AudioDeterministicError):
    pass
