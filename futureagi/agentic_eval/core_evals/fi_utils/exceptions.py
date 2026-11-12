class CustomException(Exception):
    def __init__(self, message: str | None = None, extra_info: dict | None = None):
        self.message = message
        self.extra_info = extra_info
        super().__init__(self.message)

    def __str__(self):
        if self.extra_info:
            return f"{self.message} (Extra Info: {self.extra_info})"
        return self.message


class NoFiApiKeyException(CustomException):
    def __init__(self, message: str = "Please set an Fi Client API key."):
        super().__init__(message)


class NoOpenAiApiKeyException(CustomException):
    def __init__(self, message: str = "Please set an Open API key."):
        super().__init__(message)


class MediaNotAccessibleError(ValueError):
    """Raised when a media URL passed as eval input cannot be fetched."""

    def __init__(self, key: str | None = None):
        suffix = f" for '{key}'" if key else ""
        super().__init__(
            f"Media file is not accessible{suffix}. "
            f"The file could not be downloaded — please ensure "
            f"the URL is valid and accessible."
        )


class CodeEvalSetupError(ValueError):
    """Raised when this install cannot run a code eval at all: no code
    executor, or no Node.js for a JavaScript eval. Its message is always one of
    the backend's own texts (sandbox.SETUP_ERROR_MESSAGES, or a subclass's),
    never one an eval script returned, so callers may show it to users."""


class CustomCodeEvalPausedError(CodeEvalSetupError):
    """Raised before running custom code while the deployment pauses custom
    code evaluations (CUSTOM_CODE_EVALS_PAUSED). Its message is the backend's
    fixed sentence; nothing reaches the code executor."""

    error_code = "CUSTOM_CODE_EVALS_PAUSED"


class CodeEvalInternalServiceError(CodeEvalSetupError):
    """Raised before running a code eval whose code calls one of the platform's
    internal services, such as model serving, which the code sandbox cannot
    reach. Its message is the backend's own text naming the built-in eval to
    use instead."""
