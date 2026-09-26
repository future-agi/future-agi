import pytest

from ai_tools.base import ToolContext
from ee.falcon_ai.models import Conversation, Message
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture
def conversation(user, workspace):
    """Create a test conversation."""
    return Conversation.objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        title="Test Conversation",
    )


@pytest.fixture
def message(conversation):
    """Create a test assistant message."""
    return Message.objects.create(
        conversation=conversation,
        role="assistant",
        content="Hello, I am Falcon AI.",
    )


@pytest.fixture
def user_message(conversation):
    """Create a test user message."""
    return Message.objects.create(
        conversation=conversation,
        role="user",
        content="Help me analyze my data",
    )


@pytest.fixture
def falcon_context(user, workspace):
    """ToolContext for Falcon AI tests."""
    org = user.organization
    set_workspace_context(workspace=workspace, organization=org, user=user)
    ctx = ToolContext(user=user, organization=org, workspace=workspace)
    yield ctx
    clear_workspace_context()
