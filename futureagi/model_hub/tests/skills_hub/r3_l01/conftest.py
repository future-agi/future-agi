"""Foundation fixtures explicitly opt into the not-yet-qualified consume slice."""

import pytest


@pytest.fixture(autouse=True)
def enable_context_consumption(settings):
    settings.SKILLS_CONTEXT_CONSUMPTION_ENABLED = True
