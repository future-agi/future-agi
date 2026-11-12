"""Opt-in fixture for discovery-ON regression modules, not launch-OFF tests."""

import pytest


@pytest.fixture(autouse=True)
def discovery_enabled(settings):
    settings.GATEWAY_PROVIDER_MODEL_DISCOVERY_ENABLED = True
