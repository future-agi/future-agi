"""The builder chat waits out the agent's reading, and a reading whose design was not
kept says so."""

from unittest.mock import patch

import pytest

from simulate.models import HostedHarnessConversationMessage
from simulate.services.harness_design_review import READING, REVIEW_KEY
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_conversation import enqueue_message
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_connected_versions import _connect
from simulate.tests.test_harness_scenario_amend import SUITE, _job
from simulate.tests.test_required_variable_names import (  # noqa: F401
    BOTH,
    _contract,
    _store,
    agent,
)

STORE_CONTRACT = "simulate.services.agent_reading_sandbox.store_version_contract"
CONTRACT_KEY = "simulate.services.agent_contracts.version_contract_key"


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


@pytest.mark.django_db
@pytest.mark.usefixtures("storage")
class TestReadingContract:
    def test_a_document_reading_whose_design_was_not_kept_fails_to_be_retried(
        self,
        agent,  # noqa: F811
    ):
        version = _connect(agent)

        with (
            patch(STORE_CONTRACT, return_value=False),
            patch(CONTRACT_KEY, return_value=""),
        ):
            outcome = _store(version, agent, _contract(*BOTH), document_only=True)

        assert (outcome.status, outcome.code, outcome.retryable) == (
            "failed",
            "agent_contract_not_stored",
            True,
        )

    def test_a_document_reading_of_a_version_that_holds_a_design_is_stored(
        self,
        agent,  # noqa: F811
    ):
        version = _connect(agent)

        with (
            patch(STORE_CONTRACT, return_value=False),
            patch(CONTRACT_KEY, return_value="agent-contracts/held.json"),
        ):
            outcome = _store(version, agent, _contract(*BOTH), document_only=True)

        assert outcome.status == "stored"

    def test_a_check_reading_keeps_its_agent_when_the_design_was_not_kept(
        self,
        agent,  # noqa: F811
    ):
        version = _connect(agent)

        with (
            patch(STORE_CONTRACT, return_value=False),
            patch(CONTRACT_KEY, return_value=""),
        ):
            outcome = _store(version, agent, _contract(*BOTH))

        assert outcome.status == "stored"
