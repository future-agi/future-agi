"""Explicit empty-overlay dependency for existing telemetry-only unit tests.

Import this fixture only in suites that already mock PostgreSQL. Real overlay
storage and HTTP roundtrips live in test_entity_tag_readback.py.
"""

from unittest.mock import MagicMock

import pytest

from tracer.models.entity_tag_assignment import EntityTagAssignment


@pytest.fixture(autouse=True)
def empty_tag_assignments(monkeypatch):
    manager = MagicMock()
    manager.using.return_value.filter.return_value.values.return_value.__getitem__.return_value = []
    monkeypatch.setattr(EntityTagAssignment, "objects", manager)
    return manager
