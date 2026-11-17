"""Session-wide test setup of the webhooks app.

- WT-27's suite-wide log sweep (``log_sweep``, WH-1b): registered as a session-wide plugin, so it scans every log record
  of the run and names the running test for every item, not only for the tests under this directory.
- Fixtures of the WH-2 API tests that run the real worker (``api_support.Worker``).
"""

from __future__ import annotations

import pytest

from webhooks.tests import log_sweep
from webhooks.tests.api_support import Receiver, client_for
from webhooks.tests.factories import use_test_keys


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("webhooks-log-sweep"):
        config.pluginmanager.register(log_sweep, "webhooks-log-sweep")


@pytest.fixture
def env(monkeypatch):
    use_test_keys(monkeypatch)
    monkeypatch.setenv("FI_WEBHOOKS_ALLOW_HTTP", "true")
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)


@pytest.fixture
def owner(user, organization, workspace):
    return client_for(user, organization, workspace)


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()
