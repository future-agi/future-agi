import copy
import uuid
from unittest.mock import patch

import pytest

from simulate.models import HostedHarnessConversationMessage
from simulate.services.agent_contracts import has_reading_record, stored_read_contract
from simulate.services.harness_design_contract import build_proposal
from simulate.services.harness_design_review import (
    AWAITING_STAGE,
    READING,
    begin_design_review,
    impact,
    review_contract_for,
    stored_review,
)
from simulate.services.run_results_v3 import _errored_reason
from simulate.tests.test_harness_design_review import CONTRACT
from simulate.tests.test_harness_design_review_reading import (  # noqa: F401
    SCHEDULE,
    STORED,
    _received,
    _runtime,
    client,
    review_on,
)
from simulate.tests.test_hosted_harness_conversation import (
    _deliver,
    _post_build_turn,
)

RECORD = {"commit_sha": "a" * 40}


def _read(contract):
    return {**copy.deepcopy(contract), "reading_inputs": dict(RECORD)}


class TestReadingRecord:
    def test_only_a_dict_record_counts(self):
        assert has_reading_record(_read(CONTRACT))
        assert has_reading_record({**CONTRACT, "reading_inputs": {}})
        assert not has_reading_record(CONTRACT)
        assert not has_reading_record({**CONTRACT, "reading_inputs": "a" * 40})
        assert not has_reading_record(None)

    def test_a_stored_contract_without_the_record_is_not_a_reading(self):
        version = uuid.uuid4()
        with patch(STORED, return_value=copy.deepcopy(CONTRACT)):
            assert stored_read_contract(version) is None
        with patch(STORED, return_value=_read(CONTRACT)):
            assert stored_read_contract(version) == _read(CONTRACT)
        with patch(STORED, return_value=None):
            assert stored_read_contract(version) is None

    def test_a_connected_build_reviews_only_a_contract_that_was_read(
        self,
        review_on,  # noqa: F811
    ):
        runtime = _runtime()
        with patch(STORED, return_value=copy.deepcopy(CONTRACT)):
            assert review_contract_for(runtime) is None
        with patch(STORED, return_value=_read(CONTRACT)):
            assert review_contract_for(runtime) == _read(CONTRACT)


@pytest.mark.django_db
class TestStaleVersionContract:
    def test_a_version_whose_contract_has_no_record_is_read_before_review(
        self,
        user,
        workspace,
        review_on,  # noqa: F811
        django_capture_on_commit_callbacks,
    ):
        job = _received(user, workspace)

        with (
            patch(STORED, return_value=copy.deepcopy(CONTRACT)),
            patch(SCHEDULE) as schedule,
            django_capture_on_commit_callbacks(execute=True),
        ):
            begin_design_review(job, _runtime(), None)

        schedule.assert_called_once_with(str(job.id))
        job.refresh_from_db()
        assert stored_review(job)["status"] == READING

    def test_a_version_read_with_its_record_opens_for_review_at_once(
        self,
        user,
        workspace,
        review_on,  # noqa: F811
    ):
        job = _received(user, workspace)

        with patch(STORED, return_value=_read(CONTRACT)), patch(SCHEDULE) as schedule:
            begin_design_review(job, _runtime(), None)

        schedule.assert_not_called()
        job.refresh_from_db()
        assert job.current_stage == AWAITING_STAGE


class TestImpactNamesATool:
    def _job_review(self):
        contract = {
            **CONTRACT,
            "hard_constraints": [
                "Never issue refund before the phone is checked.",
                "Keep replies short.",
            ],
        }
        return {"proposal": build_proposal(contract, 4), "edits": []}

    def test_a_rule_that_says_the_tool_in_words_is_a_dependent(self):
        with patch(
            "simulate.services.harness_design_review.review_for",
            return_value=self._job_review(),
        ):
            result = impact(None, "tool", "issue_refund")

        assert [rule["id"] for rule in result["rules"]] == ["rule-1"]


class TestErroredCallReason:
    def test_an_errored_call_says_why(self):
        assert (
            _errored_reason("error", " Failed to start the provider web bridge ")
            == "Failed to start the provider web bridge"
        )

    def test_nothing_is_said_without_a_message_or_for_another_outcome(self):
        assert _errored_reason("error", "") is None
        assert _errored_reason("error", None) is None
        assert _errored_reason("failed", "boom") is None


@pytest.mark.django_db
def test_a_completed_turn_closes_the_answers_the_builder_took(organization):
    job, conversation, command, capability = _post_build_turn(
        organization, "answers-closed"
    )
    taken, untaken = (
        HostedHarnessConversationMessage.no_workspace_objects.create(
            conversation=conversation,
            sequence=sequence,
            role=HostedHarnessConversationMessage.Role.USER,
            state=state,
            reply_to=uuid.uuid4(),
            client_request_id=f"answer-{sequence}",
        )
        for sequence, state in (
            (2, HostedHarnessConversationMessage.State.DELIVERED),
            (3, HostedHarnessConversationMessage.State.QUEUED),
        )
    )
    conversation.command_acked_through = 2
    conversation.save(update_fields=["command_acked_through", "updated_at"])

    _deliver(conversation, capability, 1, "turn_started", {})
    _deliver(
        conversation,
        capability,
        2,
        "turn_completed",
        {"command_message_id": str(command.id), "outcome": "success"},
    )

    taken.refresh_from_db()
    untaken.refresh_from_db()
    assert taken.state == HostedHarnessConversationMessage.State.COMPLETED
    assert untaken.state == HostedHarnessConversationMessage.State.QUEUED
