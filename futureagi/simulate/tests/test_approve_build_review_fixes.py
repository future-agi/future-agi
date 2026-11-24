"""The builder chat waits out the agent's reading."""

import pytest

from simulate.models import HostedHarnessConversationMessage
from simulate.services.harness_design_review import READING, REVIEW_KEY
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_conversation import enqueue_message
from simulate.tests.test_harness_scenario_amend import SUITE, _job


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.mark.django_db
def test_the_builder_chat_refuses_a_message_while_the_agent_is_read(user, workspace):
    job = _job(user, workspace, SUITE)
    job.payload = {
        **job.payload,
        "metadata": {REVIEW_KEY: {"status": READING, "agent_version_id": "v1"}},
    }
    job.save(update_fields=["payload"])

    with pytest.raises(HostedHarnessError) as refused:
        enqueue_message(job, content="add a refunds table", client_request_id="r1")

    assert (refused.value.code, refused.value.status_code) == (
        "design_review_pending",
        409,
    )
    assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job=job
    ).exists()
