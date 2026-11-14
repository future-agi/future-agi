"""
Conftest for simulate app tests.
Provides fixtures specific to simulate models and test data.
"""

import os
from pathlib import Path

import pytest

from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

TEST_INTEGRATION_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture
def temporal_test_server_options():
    """Use CI's verified binary; preserve the SDK default for local test runs."""
    path = os.environ.get("TEMPORAL_TEST_SERVER")
    if path is None:
        return {}
    if not Path(path).is_file() or not os.access(path, os.X_OK):
        raise pytest.UsageError(
            f"TEMPORAL_TEST_SERVER is not an executable file: {path!r}"
        )
    # The SDK guarantees that existing_path never attempts a download.
    return {"test_server_existing_path": path}


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
