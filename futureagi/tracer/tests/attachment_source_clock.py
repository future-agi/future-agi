"""Deterministic clock shared by isolated attachment source tests."""

from datetime import UTC, datetime, timedelta

import pytest

# Explicit unit-test epoch, not collection-time wall clock. Only the three
# freshly loaded service modules use this clock; Django setup and JWT validation
# retain their own clocks. Each test gets a new clock and can advance it to prove
# that the unchanged production expiry checks still deny access.
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class SourceClock:
    def __init__(self):
        self.value = NOW

    def now(self):
        return self.value

    def advance(self, delta):
        assert delta >= timedelta(0)
        self.value += delta


@pytest.fixture
def source_clock():
    return SourceClock()
