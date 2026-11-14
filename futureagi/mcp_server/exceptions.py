"""MCP Server custom exceptions."""


class MCPError(Exception):
    """Base MCP error."""

    pass


class ToolNotFoundError(MCPError):
    """Raised when a tool name is not found in the registry."""

    pass


class ToolDisabledError(MCPError):
    """Raised when a tool is disabled for the user's connection."""

    pass


class RateLimitExceededError(MCPError):
    """Raised when rate limit is exceeded."""

    def __init__(self, message="Rate limit exceeded", retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitBackendUnavailableError(RateLimitExceededError):
    """Raised when a distributed rate-limit decision cannot be made."""

    def __init__(self, retry_after: int = 60) -> None:
        super().__init__(
            "Rate limiting service temporarily unavailable",
            retry_after=retry_after,
        )


class SessionNotFoundError(MCPError):
    """Raised when an MCP session is not found."""

    pass
