"""Capability-service test isolation fixtures."""

from tfc.capabilities.testing import (  # noqa: F401 - pytest fixture registration
    check_capability_isolation,
    preserve_capability_service,
)
