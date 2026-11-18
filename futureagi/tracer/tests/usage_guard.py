"""A refused eval action must not create even a pending charge."""

import pytest

from tfc.ee_loader import has_ee


@pytest.fixture
def no_new_usage(db):
    """Fail the test if it records any usage (APICallLog) row."""
    if not has_ee("ee"):
        yield
        return
    from ee.usage.models.usage import APICallLog

    logs = APICallLog._base_manager.using("default")
    before = logs.count()
    yield
    assert logs.count() == before
