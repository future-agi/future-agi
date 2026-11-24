import copy
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from simulate.models import HostedHarnessConversationMessage, HostedHarnessJob
from simulate.services.harness_design_contract import (
    apply_edits_to_contract,
    build_proposal,
)
from simulate.services.harness_design_review import (
    AWAITING_APPROVAL,
    AWAITING_STAGE,
    DISCARD_REASON,
    _effective,
    create_design_review,
    stored_review,
)
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.services.hosted_harness_conversation import enqueue_message
from simulate.tests.test_harness_agent_creation import _body, gates  # noqa: F401
from simulate.tests.test_harness_provider import _v1_payload

CONTRACT = {
    "data_schema": {
        "BOOKINGS": {"type": "object", "properties": {"id": {}, "guest": {}}},
        "HOTELS": {"type": "object"},
    },
    "tools": [
        {"name": "check_otp", "args": ["phone"], "arg_types": {"phone": "text"}},
        {"name": "issue_refund", "args": ["amount"], "requires": ["check_otp"]},
        {
            "name": "notify",
            "args": ["refund_id"],
            "inputs_from": {"refund_id": "issue_refund.id"},
        },
        {"name": "find_hotels", "effects": "reads HOTELS"},
    ],
    "hard_constraints": [
        "Run check_otp before any refund",
        "Refunds over 25 need a manager",
    ],
    "real_use_cases": ["Refund a damaged item", "Cancel a booking", "Change address"],
}
URL = "/simulate/api/harness-environments/{}/design-review/"
APPROVED_KEY = "environment-contracts/org/env/approved.json"


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def waiting(user, workspace):
    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=6),
        idempotency_key=uuid.uuid4().hex,
        workspace=workspace,
        state=HostedHarnessJob.State.RECEIVED,
    )
    create_design_review(job, SimpleNamespace(agent_version_id=None), CONTRACT)
    return job


@pytest.fixture
def stored_contracts():
    with (
        patch(
            "simulate.services.agent_contracts.read_contract",
            return_value=copy.deepcopy(CONTRACT),
        ),
        patch(
            "simulate.services.agent_contracts.store_environment_contract",
            return_value=APPROVED_KEY,
        ) as store,
    ):
        yield store


def _post(client, workspace, path, body=None):
    return client.post(
        path, body or {}, format="json", HTTP_X_WORKSPACE_ID=str(workspace.id)
    )


def _get(client, workspace, path):
    return client.get(path, HTTP_X_WORKSPACE_ID=str(workspace.id))


class TestProposal:
    def test_it_lists_tables_tools_rules_and_use_cases_with_sources(self):
        proposal = build_proposal(CONTRACT, 6)

        assert [table["name"] for table in proposal["tables"]] == ["BOOKINGS", "HOTELS"]
        assert proposal["tables"][0]["columns"] == ["guest", "id"]
        assert proposal["tools"][1]["requires"] == ["check_otp"]
        assert proposal["tools"][2]["inputs_from"] == {"refund_id": "issue_refund.id"}
        assert [rule["id"] for rule in proposal["rules"]] == ["rule-1", "rule-2"]
        assert {rule["source"] for rule in proposal["rules"]} == {"agent_prompt"}
        assert {tool["source"] for tool in proposal["tools"]} == {"agent_code"}
        assert len(proposal["use_cases"]) == 3

    @pytest.mark.parametrize("kind", ["provider", "phone", "livekit"])
    def test_a_hosted_agents_tools_come_from_its_settings(self, kind):
        proposal = build_proposal(CONTRACT, 6, source_kind=kind)

        assert {tool["source"] for tool in proposal["tools"]} == {"agent_settings"}

    @pytest.mark.parametrize("kind", ["github", "archive", "remote", None])
    def test_a_read_agents_tools_come_from_its_code(self, kind):
        proposal = build_proposal(CONTRACT, 6, source_kind=kind)

        assert {tool["source"] for tool in proposal["tools"]} == {"agent_code"}

    def test_the_review_reads_the_source_kind_off_the_build(self):
        job = SimpleNamespace(
            payload={"source": {"kind": "provider"}},
            scenario_count=6,
            save=lambda **_: None,
        )

        create_design_review(job, SimpleNamespace(agent_version_id=None), CONTRACT)

        tools = stored_review(job)["proposal"]["tools"]
        assert {tool["source"] for tool in tools} == {"agent_settings"}

    def test_removing_a_use_case_spreads_its_scenarios_over_the_rest(self):
        proposal = build_proposal(CONTRACT, 6)

        view = _effective(
            proposal, [{"op": "remove", "kind": "use_case", "id": "use-case-3"}]
        )

        assert [case["planned_scenarios"] for case in view["use_cases"]] == [3, 3, 0]
        assert view["use_cases"][2]["removed"] is True

    def test_an_edited_rule_keeps_its_original(self):
        view = _effective(
            build_proposal(CONTRACT, 6),
            [{"op": "edit", "kind": "rule", "id": "rule-2", "text": "Over 50"}],
        )

        edited = view["rules"][1]
        assert (edited["text"], edited["edited"]) == ("Over 50", True)
        assert edited["original_text"] == "Refunds over 25 need a manager"
        assert edited["source"] == "your_change"


class TestEditedContract:
    def test_the_edits_apply_to_a_copy_and_leave_the_agents_contract_alone(self):
        before = copy.deepcopy(CONTRACT)
        edits = [
            {"op": "remove", "kind": "tool", "id": "notify"},
            {"op": "remove", "kind": "table", "id": "HOTELS"},
            {"op": "remove", "kind": "use_case", "id": "use-case-2"},
            {"op": "remove", "kind": "rule", "id": "rule-1"},
            {"op": "edit", "kind": "rule", "id": "rule-2", "text": "Over 50"},
        ]

        edited = apply_edits_to_contract(CONTRACT, edits)

        assert [tool["name"] for tool in edited["tools"]] == [
            "check_otp",
            "issue_refund",
            "find_hotels",
        ]
        assert list(edited["data_schema"]) == ["BOOKINGS"]
        assert edited["real_use_cases"] == ["Refund a damaged item", "Change address"]
        assert edited["hard_constraints"] == ["Over 50"]
        assert CONTRACT == before

    def test_what_the_review_took_out_is_recorded_for_the_build_to_read(self):
        contract = {**CONTRACT, "amendments": ["modality chat -> voice: asked"]}
        edits = [
            {"op": "remove", "kind": "use_case", "id": "use-case-2"},
            {"op": "remove", "kind": "rule", "id": "rule-1"},
            {"op": "edit", "kind": "rule", "id": "rule-2", "text": "Over 50"},
        ]

        edited = apply_edits_to_contract(contract, edits)

        assert edited["amendments"] == [
            "modality chat -> voice: asked",
            "use case removed - Cancel a booking: removed in the design review",
            "rule removed - Run check_otp before any refund: removed in the design "
            "review",
            "rule reworded - Refunds over 25 need a manager: reworded in the design "
            "review to: Over 50",
        ]

    def test_a_review_that_only_drops_a_tool_records_no_amendment(self):
        edited = apply_edits_to_contract(
            CONTRACT, [{"op": "remove", "kind": "tool", "id": "notify"}]
        )

        assert "amendments" not in edited


@pytest.mark.django_db
class TestReviewApi:
    def test_the_design_waits_on_the_environment_and_reads_back(
        self, client, workspace, waiting
    ):
        response = _get(client, workspace, URL.format(waiting.id))

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["environment_id"] == str(waiting.id)
        assert body["status"] == "awaiting_approval"
        assert body["scenario_count"] == 6
        assert len(body["tools"]) == 4
        waiting.refresh_from_db()
        assert waiting.current_stage == AWAITING_STAGE
        assert stored_review(waiting)["source_contract_key"] == ""

    def test_removing_a_tool_shows_the_tools_that_need_it(
        self, client, workspace, waiting
    ):
        response = _get(
            client, workspace, URL.format(waiting.id) + "impact/?kind=tool&id=check_otp"
        )

        assert response.status_code == 200, response.content
        body = response.json()
        assert [tool["name"] for tool in body["tools"]] == ["issue_refund", "notify"]
        assert body["tools"][0]["reason"] == "needs check_otp to run first"
        assert body["tools"][1]["reason"] == "takes its input from issue_refund"
        assert [rule["id"] for rule in body["rules"]] == ["rule-1"]

    def test_edits_apply_and_undo(self, client, workspace, waiting):
        path = URL.format(waiting.id) + "edits/"

        _post(client, workspace, path, {"op": "remove", "kind": "tool", "id": "notify"})
        response = _post(
            client,
            workspace,
            path,
            {"op": "edit", "kind": "rule", "id": "rule-2", "text": "Over 50"},
        )
        assert response.status_code == 200, response.content
        assert [edit["op"] for edit in response.json()["edits"]] == ["remove", "edit"]

        undone = client.delete(
            path + "0/", HTTP_X_WORKSPACE_ID=str(workspace.id), format="json"
        )
        assert undone.status_code == 200, undone.content
        assert [edit["op"] for edit in undone.json()["edits"]] == ["edit"]
        waiting.refresh_from_db()
        assert [edit["op"] for edit in stored_review(waiting)["edits"]] == ["edit"]

    @pytest.mark.parametrize(
        "edit, code",
        [
            (
                {"op": "edit", "kind": "tool", "id": "notify", "text": "x"},
                "design_edit_invalid",
            ),
            ({"op": "remove", "kind": "tool", "id": "nope"}, "design_item_not_found"),
        ],
    )
    def test_edits_that_cannot_apply_are_refused(
        self, client, workspace, waiting, edit, code
    ):
        response = _post(client, workspace, URL.format(waiting.id) + "edits/", edit)

        assert response.json()["error"] == code

    def test_adding_a_rule_is_not_offered(self, client, workspace, waiting):
        response = _post(
            client,
            workspace,
            URL.format(waiting.id) + "edits/",
            {"op": "add", "kind": "rule", "id": "rule-new", "text": "Be kind"},
        )

        assert response.status_code == 400, response.content
        waiting.refresh_from_db()
        assert stored_review(waiting)["edits"] == []

    def test_the_last_use_case_cannot_be_removed(self, client, workspace, waiting):
        path = URL.format(waiting.id) + "edits/"
        for case in ("use-case-1", "use-case-2"):
            _post(
                client,
                workspace,
                path,
                {"op": "remove", "kind": "use_case", "id": case},
            )

        response = _post(
            client,
            workspace,
            path,
            {"op": "remove", "kind": "use_case", "id": "use-case-3"},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "design_keep_one_use_case"

    def test_approving_starts_the_build_once(
        self, client, user, workspace, waiting, stored_contracts
    ):
        with patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow"
        ) as start:
            response = _post(client, workspace, URL.format(waiting.id) + "approve/")
            again = _post(client, workspace, URL.format(waiting.id) + "approve/")

        assert response.status_code == 200, response.content
        assert response.json()["status"] == "approved"
        start.assert_called_once()
        waiting.refresh_from_db()
        assert waiting.state == HostedHarnessJob.State.QUEUED
        assert stored_review(waiting)["approved_by"]["id"] == str(user.pk)
        assert stored_review(waiting)["approved_contract_key"] == ""
        stored_contracts.assert_not_called()
        assert again.status_code == 409
        assert again.json()["error"] == "design_review_closed"

    def test_approving_with_edits_stores_the_edited_design(
        self, client, workspace, waiting, stored_contracts
    ):
        _post(
            client,
            workspace,
            URL.format(waiting.id) + "edits/",
            {"op": "remove", "kind": "tool", "id": "notify"},
        )
        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            response = _post(client, workspace, URL.format(waiting.id) + "approve/")

        assert response.status_code == 200, response.content
        waiting.refresh_from_db()
        assert stored_review(waiting)["approved_contract_key"] == APPROVED_KEY
        stored = stored_contracts.call_args.kwargs["contract"]
        assert [tool["name"] for tool in stored["tools"]] == [
            "check_otp",
            "issue_refund",
            "find_hotels",
        ]

    def test_a_build_that_cannot_start_stays_waiting(
        self, client, workspace, waiting, stored_contracts
    ):
        with patch(
            "simulate.temporal.client.start_hosted_harness_gateway_workflow",
            side_effect=RuntimeError("temporal down"),
        ):
            response = _post(client, workspace, URL.format(waiting.id) + "approve/")

        assert response.status_code == 503
        waiting.refresh_from_db()
        assert waiting.state == HostedHarnessJob.State.RECEIVED
        assert waiting.current_stage == AWAITING_STAGE
        review = stored_review(waiting)
        assert (review["status"], review["approved_by"]) == (AWAITING_APPROVAL, None)

    def test_discarding_builds_nothing_and_removes_the_environment(
        self, client, workspace, waiting
    ):
        response = _post(client, workspace, URL.format(waiting.id) + "discard/")

        assert response.status_code == 200, response.content
        waiting.refresh_from_db()
        assert (waiting.state, waiting.deleted, waiting.cancel_reason) == (
            HostedHarnessJob.State.CANCELED,
            True,
            DISCARD_REASON,
        )
        assert _get(client, workspace, URL.format(waiting.id)).status_code == 404

    def test_the_builder_chat_refuses_a_message_while_the_design_waits(self, waiting):
        with pytest.raises(HostedHarnessError) as refused:
            enqueue_message(
                waiting, content="add a refunds table", client_request_id="r1"
            )

        assert refused.value.code == "design_review_pending"
        assert not HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation__job=waiting
        ).exists()


@pytest.mark.django_db
def test_a_build_from_an_agent_waits_for_approval(
    client,
    user,
    workspace,
    gates,  # noqa: F811
    settings,
):
    settings.HARNESS_DESIGN_REVIEW_ENABLED = True
    settings.HARNESS_VERSIONING_ENABLED = False
    runtime = SimpleNamespace(
        agent_version_id=None,
        source_kind="github",
        credential_names=[],
        credential_fingerprints={},
    )
    with (
        patch(
            "simulate.services.harness_provider._connected_build_runtime",
            return_value=runtime,
        ),
        patch(
            "simulate.services.harness_provider._connected_build_source",
            side_effect=lambda _runtime, payload: payload["source"],
        ),
        patch(
            "simulate.services.harness_design_review.review_contract_for",
            return_value=CONTRACT,
        ),
    ):
        response = client.post(
            "/simulate/api/harness-jobs/",
            _body(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

    assert response.status_code == 202, response.content
    job = HostedHarnessJob.no_workspace_objects.get(id=response.json()["job"]["job_id"])
    assert (job.state, job.current_stage) == (
        HostedHarnessJob.State.RECEIVED,
        AWAITING_STAGE,
    )
    assert stored_review(job)["status"] == AWAITING_APPROVAL
    gates.start.assert_not_called()
