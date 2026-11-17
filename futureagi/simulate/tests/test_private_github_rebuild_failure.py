"""A rebuild of an environment whose private GitHub source this deployment no longer
reads.

The rebuild runs as a turn in the environment's chat, and the chat's sandbox reads the
source when it starts. When that read is refused for the rebuild's own turn, the
rebuild fails at once with the refusal's reason, instead of waiting to be closed as
not started. A start refused for a chat message ahead of the rebuild, and any other
failed start, leave the rebuild waiting, as before.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationMessage,
)
from simulate.services.harness_versioning import rebuild
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import HostedHarnessGateway
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_nudges import (  # noqa: F401 - fixture
    started as started,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_rebuild import _queued
from simulate.tests.test_harness_versioning_rebuild import (  # noqa: F401 - fixture
    inline as inline,
)
from simulate.tests.test_harness_versioning_rebuild import (  # noqa: F401 - fixture
    nudges_on as nudges_on,
)

pytestmark = pytest.mark.django_db

REFUSED = "github_private_unavailable"
ENVIRONMENT_SENTENCE = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Upload the code to keep using this environment."
)
CREATE_TIME_REFUSAL = (
    "Private GitHub repositories are not available on Future AGI cloud. "
    "Use a public repository or upload the code."
)


def _gateway() -> HostedHarnessGateway:
    gateway = HostedHarnessGateway.__new__(HostedHarnessGateway)
    gateway.client = MagicMock(runtime_name="r", runtime_digest="d")
    return gateway


def _dispatched(user, workspace, storage, key, auth_client, capture):
    """An environment whose answered rebuild was handed to its chat, not yet started."""
    environment, nudge, _a1, _a2 = _queued(
        user, workspace, storage, key, auth_client, capture
    )
    assert rebuild.dispatch_nudge(environment.id, start_runtime=False)
    nudge.refresh_from_db()
    assert nudge.state == "queued"
    assert nudge.command_message_id
    conversation = HostedHarnessConversation.no_workspace_objects.get(job=environment)
    return environment, nudge, conversation


def _start_refused(environment, conversation, error, capture):
    with (
        patch.object(HostedHarnessGateway, "_start_chat_sandbox", side_effect=error),
        capture(execute=True),
        pytest.raises(HostedHarnessError) as raised,
    ):
        _gateway().ensure_conversation_runtime(
            environment,
            conversation=conversation,
            endpoint_base_url="https://platform.example",
        )
    return raised.value


def test_a_refused_private_source_ends_the_waiting_rebuild_with_its_reason(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, conversation = _dispatched(
        user,
        workspace,
        storage,
        "r-private-refused",
        auth_client,
        django_capture_on_commit_callbacks,
    )

    raised = _start_refused(
        environment,
        conversation,
        HostedHarnessError(
            REFUSED, CREATE_TIME_REFUSAL, status_code=422, retryable=False
        ),
        django_capture_on_commit_callbacks,
    )

    # The chat start itself still fails as it did.
    assert raised.code == REFUSED
    conversation.refresh_from_db()
    assert conversation.state == HostedHarnessConversation.State.DEGRADED
    nudge.refresh_from_db()
    assert nudge.state == "failed"
    assert nudge.error["code"] == REFUSED
    assert nudge.error["message"] == ENVIRONMENT_SENTENCE
    # Its builder message will not run in a later sandbox.
    assert (
        HostedHarnessConversationMessage.no_workspace_objects.get(
            id=nudge.command_message_id
        ).state
        == HostedHarnessConversationMessage.State.FAILED
    )


def test_a_refused_start_for_a_message_ahead_of_the_rebuild_leaves_it_waiting(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, conversation = _dispatched(
        user,
        workspace,
        storage,
        "r-private-refused-ahead",
        auth_client,
        django_capture_on_commit_callbacks,
    )
    # A chat message waits ahead of the rebuild's builder message, so the start that
    # is refused runs for that message and not for the rebuild.
    messages = HostedHarnessConversationMessage.no_workspace_objects
    command = messages.get(id=nudge.command_message_id)
    messages.filter(id=command.id).update(sequence=command.sequence + 1000)
    messages.create(
        conversation=conversation,
        sequence=command.sequence,
        role=HostedHarnessConversationMessage.Role.USER,
        content="Which tool does the agent call first?",
    )

    raised = _start_refused(
        environment,
        conversation,
        HostedHarnessError(
            REFUSED, CREATE_TIME_REFUSAL, status_code=422, retryable=False
        ),
        django_capture_on_commit_callbacks,
    )

    assert raised.code == REFUSED
    nudge.refresh_from_db()
    assert nudge.state == "queued"
    assert nudge.error is None


def test_another_failed_start_leaves_the_rebuild_waiting_as_before(
    user,
    workspace,
    storage,
    scheduled,
    started,
    inline,
    auth_client,
    django_capture_on_commit_callbacks,
):
    environment, nudge, conversation = _dispatched(
        user,
        workspace,
        storage,
        "r-start-failed",
        auth_client,
        django_capture_on_commit_callbacks,
    )

    raised = _start_refused(
        environment,
        conversation,
        HostedHarnessError(
            "github_clone_failed",
            "fatal: unable to access the repository",
            status_code=502,
            retryable=True,
        ),
        django_capture_on_commit_callbacks,
    )

    assert raised.code == "github_clone_failed"
    nudge.refresh_from_db()
    assert nudge.state == "queued"
    assert nudge.error is None
