"""
Conftest for simulate app tests.
Provides fixtures specific to simulate models and test data.
"""

import pytest

from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

TEST_INTEGRATION_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture(autouse=True)
def set_integration_encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = TEST_INTEGRATION_ENCRYPTION_KEY


@pytest.fixture(autouse=True)
def set_workspace_context_fixture(request):
    """Ensure workspace context is set for each test via thread-local storage.

    Only applies to tests that use the workspace and organization fixtures.
    """
    if "workspace" in request.fixturenames and "organization" in request.fixturenames:
        workspace = request.getfixturevalue("workspace")
        organization = request.getfixturevalue("organization")
        set_workspace_context(workspace=workspace, organization=organization)
        yield
        clear_workspace_context()
    else:
        yield
