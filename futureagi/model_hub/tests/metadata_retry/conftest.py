"""Shared synthetic evaluation fixture for metadata transport regressions."""

import pytest
from django.db import connections

from model_hub.tests.test_eval_metadata_retry import evaluation_case  # noqa: F401


@pytest.fixture
def replica_connection(settings, monkeypatch):
    """Expose the test harness's independent mirrored connection to the router.

    Reuse default_direct's wrapper so pytest-django can manage both connections
    regardless of test collection order. No replication service is needed, and
    no session-wide database configuration leaks into unrelated tests.
    """
    replica = connections["default_direct"]
    prior = connections["replica"] if "replica" in connections else None
    monkeypatch.setitem(settings.DATABASES, "replica", replica.settings_dict)
    monkeypatch.setitem(connections.databases, "replica", replica.settings_dict)
    connections["replica"] = replica
    try:
        yield replica
    finally:
        replica.close()
        if prior is None:
            del connections["replica"]
        else:
            connections["replica"] = prior
