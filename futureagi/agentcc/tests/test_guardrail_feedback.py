"""One guardrail feedback answer per (log, check, reviewer).

The request drawer loads the reviewer's own saved answer (`mine=true`) and
PATCHes it on Edit. PATCH changes only the answer and the comment, keeps the
org check, and answers in the usual {status, result} envelope. Only the
reviewer who wrote an answer can change it. A second POST by the same
reviewer for the same check updates that answer instead of adding a row, and
the summary counts each reviewer's latest answer once, whatever rows a reviewer
holds for a check.

No test here reaches a network.
"""

import uuid

import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models import AgentccRequestLog
from agentcc.models.guardrail_feedback import AgentccGuardrailFeedback
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

URL = "/agentcc/guardrail-feedback/"
SUMMARY = "/agentcc/guardrail-feedback/summary/"


def _log(organization, request_id="req-fb-1"):
    return AgentccRequestLog.objects.create(
        organization=organization,
        request_id=request_id,
        model="gpt-4o",
        provider="openai",
    )


def _member(organization):
    user = User.objects.create_user(
        email=f"reviewer-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Second Reviewer",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.get(
        organization=organization, is_default=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    return user, workspace


@pytest.fixture
def second_client(organization, workspace):
    user, ws = _member(organization)
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(ws)
    yield client, user
    client.stop_workspace_injection()


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]


def _post(client, log, feedback, check="pii_detection", comment=""):
    return client.post(
        URL,
        {
            "request_log": str(log.id),
            "check_name": check,
            "feedback": feedback,
            "comment": comment,
        },
        format="json",
    )


def _summary_row(client, check="pii_detection"):
    rows = {r["check_name"]: r for r in _result(client.get(SUMMARY))}
    return rows[check]


@pytest.mark.django_db
class TestEdit:
    def test_patch_changes_the_answer_in_the_usual_envelope(
        self, auth_client, organization
    ):
        log = _log(organization)
        created = _result(_post(auth_client, log, "false_positive"))
        patched = _result(
            auth_client.patch(
                f"{URL}{created['id']}/",
                {"feedback": "correct", "comment": "re-checked"},
                format="json",
            )
        )
        assert patched["id"] == created["id"]
        assert patched["feedback"] == "correct"
        assert patched["comment"] == "re-checked"
        assert (
            AgentccGuardrailFeedback.no_workspace_objects.filter(
                request_log=log
            ).count()
            == 1
        )

    def test_patch_cannot_move_an_answer_to_another_orgs_log(
        self, auth_client, organization
    ):
        log = _log(organization)
        other_org = Organization.objects.create(name="Other Org")
        foreign_log = _log(other_org, request_id="req-foreign")
        created = _result(_post(auth_client, log, "correct"))
        response = auth_client.patch(
            f"{URL}{created['id']}/",
            {"request_log": str(foreign_log.id), "check_name": "toxicity"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        row = AgentccGuardrailFeedback.no_workspace_objects.get(id=created["id"])
        assert row.request_log_id == log.id
        assert row.check_name == "pii_detection"

    def test_patch_refuses_an_unknown_answer(self, auth_client, organization):
        log = _log(organization)
        created = _result(_post(auth_client, log, "correct"))
        response = auth_client.patch(
            f"{URL}{created['id']}/", {"feedback": "maybe"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_only_the_reviewer_can_change_their_answer(
        self, auth_client, organization, second_client
    ):
        other, _ = second_client
        log = _log(organization)
        created = _result(_post(auth_client, log, "correct"))
        response = other.patch(
            f"{URL}{created['id']}/", {"feedback": "false_positive"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        row = AgentccGuardrailFeedback.no_workspace_objects.get(id=created["id"])
        assert row.feedback == "correct"


@pytest.mark.django_db
class TestLoadSavedAnswer:
    def test_mine_lists_only_my_answers_for_the_log(
        self, auth_client, organization, second_client
    ):
        other, _ = second_client
        log = _log(organization)
        mine = _result(_post(auth_client, log, "false_positive"))
        _result(_post(other, log, "correct"))
        rows = _result(
            auth_client.get(URL, {"request_log_id": str(log.id), "mine": "true"})
        )
        assert [r["id"] for r in rows] == [mine["id"]]

    def test_without_mine_every_reviewers_answer_is_listed(
        self, auth_client, organization, second_client
    ):
        other, _ = second_client
        log = _log(organization)
        _post(auth_client, log, "false_positive")
        _post(other, log, "correct")
        rows = _result(auth_client.get(URL, {"request_log_id": str(log.id)}))
        assert len(rows) == 2


@pytest.mark.django_db
class TestOneAnswerPerReviewer:
    def test_a_second_post_by_the_same_reviewer_updates_the_answer(
        self, auth_client, organization
    ):
        log = _log(organization)
        first = _result(_post(auth_client, log, "false_positive"))
        second = _result(_post(auth_client, log, "correct", comment="edited"))
        assert second["id"] == first["id"]
        assert second["feedback"] == "correct"
        assert (
            AgentccGuardrailFeedback.no_workspace_objects.filter(
                request_log=log
            ).count()
            == 1
        )

    def test_answer_then_edit_counts_once(self, auth_client, organization):
        # False Positive, then Edit to Correct: one answer, counted once.
        log = _log(organization)
        created = _result(_post(auth_client, log, "false_positive"))
        _result(
            auth_client.patch(
                f"{URL}{created['id']}/", {"feedback": "correct"}, format="json"
            )
        )
        row = _summary_row(auth_client)
        assert row["total_feedback"] == 1
        assert row["correct_count"] == 1
        assert row["correct_rate"] == 100.0

    def test_two_rows_by_one_reviewer_count_once(self, auth_client, organization, user):
        log = _log(organization)
        older = AgentccGuardrailFeedback.no_workspace_objects.create(
            organization=organization,
            request_log=log,
            check_name="pii_detection",
            feedback="false_positive",
            created_by=user,
        )
        AgentccGuardrailFeedback.no_workspace_objects.create(
            organization=organization,
            request_log=log,
            check_name="pii_detection",
            feedback="correct",
            created_by=user,
        )
        assert older.feedback == "false_positive"
        row = _summary_row(auth_client)
        assert row["total_feedback"] == 1
        assert row["correct_count"] == 1
        assert row["false_positive_count"] == 0

    def test_two_reviewers_count_twice(self, auth_client, organization, second_client):
        other, _ = second_client
        log = _log(organization)
        _post(auth_client, log, "false_positive")
        _post(other, log, "correct")
        row = _summary_row(auth_client)
        assert row["total_feedback"] == 2
        assert row["correct_count"] == 1
        assert row["false_positive_count"] == 1

    def test_answers_whose_reviewer_was_deleted_each_count(
        self, auth_client, organization
    ):
        # created_by is SET_NULL when a user is deleted: those answers came
        # from reviewers who cannot be told apart, so each counts.
        log = _log(organization)
        for answer in ("correct", "false_positive"):
            AgentccGuardrailFeedback.no_workspace_objects.create(
                organization=organization,
                request_log=log,
                check_name="pii_detection",
                feedback=answer,
                created_by=None,
            )
        row = _summary_row(auth_client)
        assert row["total_feedback"] == 2

    def test_same_reviewer_other_check_or_log_counts_separately(
        self, auth_client, organization
    ):
        log1 = _log(organization)
        log2 = _log(organization, request_id="req-fb-2")
        _post(auth_client, log1, "correct")
        _post(auth_client, log2, "correct")
        _post(auth_client, log1, "false_negative", check="toxicity")
        assert _summary_row(auth_client)["total_feedback"] == 2
        assert _summary_row(auth_client, "toxicity")["total_feedback"] == 1
