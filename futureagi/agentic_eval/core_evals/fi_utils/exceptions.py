

class CustomException(Exception):
    def __init__(
        self, message: str | None = None, extra_info: dict | None = None
    ):
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
