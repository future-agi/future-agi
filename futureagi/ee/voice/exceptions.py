import json


class VapiApiError(Exception):
    """Raised when a VAPI API call returns a non-success status code."""

    def __init__(
        self,
        message: str,
        status_code: int,
        action: str,
        response_body: str = "",
    ):
        self.status_code = status_code
        self.action = action
        self.response_body = response_body
        super().__init__(message)

    def get_provider_message(self) -> str | None:
        """Extract the provider's error message from response_body JSON."""
        if not self.response_body:
            return None
        try:
            data = json.loads(self.response_body)
            msg = data.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        except (json.JSONDecodeError, AttributeError):
            pass
        return None
