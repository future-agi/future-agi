"""Usage enforcement helpers.

Provides ``UsageLimitExceeded`` and ``enforce_usage`` so call-sites don't
need to inline the check_usage → raise dance (or the Temporal-specific
``ApplicationError`` variant).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class UsageLimitExceeded(Exception):
    """Raised when an organisation exceeds its usage quota."""

    def __init__(self, reason: str = "Usage limit exceeded"):
        self.reason = reason
        super().__init__(reason)
