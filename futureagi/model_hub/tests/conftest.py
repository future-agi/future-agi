"""
Conftest for model_hub app tests.
Provides fixtures specific to model_hub models and test data.
"""

import pytest
from django.urls import get_resolver

from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture(scope="module")
def urlconf_loaded_without_workspace_context():
    """Import the URLconf, and with it every view module, with no workspace context.

    A view's class-level queryset goes through BaseModelManager, which filters
    by the thread-local workspace context when the view module is imported.
    When a test body's ``reverse()`` is the session's first URLconf import, the
    ``user`` fixture's context is still set, and those querysets stay pinned to
    that test's workspace for the rest of the session: later suites (dataset
    optimization, the structured knowledge base) then get 404s and empty lists
    in every other workspace. Modules that call ``reverse()`` in test bodies
    request this fixture, so the import happens here, outside any context.
    """
    clear_workspace_context()
    get_resolver().url_patterns  # noqa: B018 - imports the URLconf
    yield


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


@pytest.fixture
def prompt_template(db, organization, workspace):
    """Create a PromptTemplate for testing."""
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.no_workspace_objects.create(
        name="Test Prompt Template",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    """Create a PromptVersion linked to prompt_template."""
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
            ],
            "configuration": {},
        },
        variable_names={},
    )
