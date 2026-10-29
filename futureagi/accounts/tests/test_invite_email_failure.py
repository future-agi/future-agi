"""Member creation is atomic; invitation delivery is best-effort and explicit.

Run: pytest accounts/tests/test_invite_email_failure.py
"""

from unittest.mock import patch

import pytest
from django.db import IntegrityError
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from accounts.serializers.contracts import (
    TeamCreateErrorResponseSerializer,
    TeamCreateResponseSerializer,
)
from accounts.views import workspace_management
from tfc.constants.levels import Level

ADD_TEAM_USER_URL = "/accounts/team/users/"
NEW_EMAIL = "newhire-emailfail@futureagi.com"
WARNING = (
    "Member added, but the invitation email could not be sent. "
    "Open Members to resend the invite or copy an invite link if available."
)


def member_payload(email=NEW_EMAIL):
    return {"email": email, "name": "New Hire", "role": "Member"}


def assert_pending_member(email, organization, workspace, invited_by):
    member = User.objects.get(email=email)
    assert member.organization_id == organization.id
    assert member.is_active is False
    assert member.invited_by_id == invited_by.id
    membership = WorkspaceMembership.no_workspace_objects.get(
        user=member, workspace=workspace
    )
    assert membership.is_active is True
    assert membership.role == "workspace_member"
    assert membership.invited_by_id == invited_by.id
    assert membership.organization_membership.user_id == member.id
    assert membership.organization_membership.organization_id == organization.id
    invite = OrganizationInvite.objects.get(
        organization=organization, target_email=email
    )
    assert invite.status == InviteStatus.PENDING
    assert invite.is_expired is False
    assert invite.level == Level.MEMBER
    assert invite.workspace_access == [
        {"workspace_id": str(workspace.id), "level": Level.WORKSPACE_MEMBER}
    ]
    assert invite.invited_by_id == invited_by.id
    return member


def assert_created_response(response):
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["status"] is True
    contract = TeamCreateResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    created = response.data["result"]["created_members"]
    assert all(member["goals"] is None for member in created)
    assert [
        member["goals"] for member in contract.data["result"]["created_members"]
    ] == [None] * len(created)
    assert [member.get("invite_email_warning") for member in created] == [
        member.get("invite_email_warning")
        for member in contract.validated_data["result"]["created_members"]
    ]
    return created


def assert_partial_response(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    message = "Some members could not be added. Correct the errors and try again."
    assert body["status"] is False
    assert body["type"] == "validation_error"
    assert body["code"] == "invalid"
    assert body["message"] == body["detail"] == body["error"] == message
    assert isinstance(body["result"], dict)
    assert body["result"]["message"] == message
    contract = TeamCreateErrorResponseSerializer(data=body)
    assert contract.is_valid(), contract.errors
    assert contract.validated_data["result"] == body["result"]
    return body["result"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestInviteEmailFailureDecoupled:
    @pytest.fixture(autouse=True)
    def dependencies(self):
        # Exercise real API/database writes, never outbound mail/analytics/billing.
        with (
            patch("accounts.views.workspace_management.email_helper") as email,
            patch("accounts.views.workspace_management.logger") as logger,
            patch("accounts.views.workspace_management.track_mixpanel_event"),
            patch(
                "accounts.views.workspace_management.log_and_deduct_cost_for_resource_request",
                None,
            ),
        ):
            self.email = email
            self.logger = logger
            yield

    def test_member_add_succeeds_when_invite_email_fails(
        self, auth_client, user, organization, workspace
    ):
        sensitive_error = "smtp rejected password=do-not-log invite-token=private"
        self.email.side_effect = RuntimeError(sensitive_error)

        response = auth_client.post(
            ADD_TEAM_USER_URL, {"members": [member_payload()]}, format="json"
        )

        created = assert_created_response(response)
        member = assert_pending_member(NEW_EMAIL, organization, workspace, user)
        assert len(created) == 1
        assert created[0]["id"] == str(member.id)
        assert created[0]["invite_email_warning"] == WARNING
        self.email.assert_called_once()
        self.logger.warning.assert_called_once_with(
            "invite_email_failed", user_id=str(member.id)
        )
        assert sensitive_error not in str(response.data)
        assert sensitive_error not in str(self.logger.mock_calls)

    def test_healthy_email_has_no_warning(
        self, auth_client, user, organization, workspace
    ):
        def send_after_persistence(*args, **kwargs):
            assert_pending_member(NEW_EMAIL, organization, workspace, user)

        self.email.side_effect = send_after_persistence
        response = auth_client.post(
            ADD_TEAM_USER_URL, {"members": [member_payload()]}, format="json"
        )

        created = assert_created_response(response)
        assert len(created) == 1
        assert "invite_email_warning" not in created[0]
        assert_pending_member(NEW_EMAIL, organization, workspace, user)
        self.email.assert_called_once()
        self.logger.warning.assert_not_called()

    @pytest.mark.parametrize("failure", [IntegrityError, RuntimeError])
    @pytest.mark.parametrize("operation", ["membership", "invite"])
    def test_persistence_failure_rolls_back_all_member_records(
        self, auth_client, monkeypatch, operation, failure
    ):
        if operation == "membership":
            target = workspace_management.ManageTeamView
            name = "_add_user_to_workspace"
        else:
            target = workspace_management
            name = "persist_pending_org_invite"
        original = getattr(target, name)

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise failure("injected persistence failure")

        monkeypatch.setattr(target, name, fail_after_write)
        response = auth_client.post(
            ADD_TEAM_USER_URL, {"members": [member_payload()]}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        if isinstance(response.data["result"], dict):
            assert response.data["result"]["created_members"] == []
        assert not User.objects.filter(email=NEW_EMAIL).exists()
        assert not WorkspaceMembership.all_objects.filter(
            user__email=NEW_EMAIL
        ).exists()
        assert not OrganizationMembership.all_objects.filter(
            user__email=NEW_EMAIL
        ).exists()
        assert not OrganizationInvite.all_objects.filter(
            target_email=NEW_EMAIL
        ).exists()
        self.email.assert_not_called()

    def test_bulk_invite_only_warns_for_failed_email(
        self, auth_client, user, organization, workspace
    ):
        emails = ["first@futureagi.com", NEW_EMAIL, "last@futureagi.com"]
        self.email.side_effect = [None, RuntimeError("smtp down"), None]
        response = auth_client.post(
            ADD_TEAM_USER_URL,
            {"members": [member_payload(email) for email in emails]},
            format="json",
        )

        created = assert_created_response(response)
        assert [member["email"] for member in created] == emails
        assert [member.get("invite_email_warning") for member in created] == [
            None,
            WARNING,
            None,
        ]
        for email in emails:
            assert_pending_member(email, organization, workspace, user)
        assert self.email.call_count == 3
        assert self.logger.warning.call_count == 1

    @pytest.mark.parametrize("duplicate_org", ["same", "different"])
    def test_partial_batch_preserves_delivery_warning_after_duplicate(
        self, auth_client, user, organization, workspace, duplicate_org
    ):
        duplicate = user
        if duplicate_org == "different":
            duplicate = User.objects.create(
                email="other-org-member@futureagi.com",
                name="Existing member",
                organization=Organization.objects.create(name="Other organization"),
            )
        last_email = "after-duplicate@futureagi.com"
        self.email.side_effect = [RuntimeError("smtp down"), None]

        response = auth_client.post(
            ADD_TEAM_USER_URL,
            {
                "members": [
                    member_payload(),
                    member_payload(duplicate.email),
                    member_payload(last_email),
                ]
            },
            format="json",
        )

        result = assert_partial_response(response)
        assert result["workspace"]["id"] == str(workspace.id)
        assert [item["email"] for item in result["created_members"]] == [
            NEW_EMAIL,
            last_email,
        ]
        assert result["created_members"][0]["invite_email_warning"] == WARNING
        assert "invite_email_warning" not in result["created_members"][1]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["index"] == 1
        assert result["errors"][0]["email"] == duplicate.email
        assert result["errors"][0]["error"]
        if duplicate_org == "same":
            assert result["errors"][0]["error"] == (
                "User is already a member of this organization"
            )
        assert_pending_member(NEW_EMAIL, organization, workspace, user)
        assert_pending_member(last_email, organization, workspace, user)
        assert not OrganizationInvite.objects.filter(
            organization=organization, target_email=duplicate.email
        ).exists()
        assert self.email.call_count == 2

    def test_partial_batch_preserves_warning_and_rolls_back_failed_invite(
        self, auth_client, user, organization, workspace, monkeypatch
    ):
        failed_email = "persistence-failure@futureagi.com"
        last_email = "after-persistence-failure@futureagi.com"
        persist = workspace_management.persist_pending_org_invite

        def fail_one_invite(**kwargs):
            persist(**kwargs)
            if kwargs["target_email"] == failed_email:
                raise IntegrityError("injected persistence failure")

        monkeypatch.setattr(
            workspace_management, "persist_pending_org_invite", fail_one_invite
        )
        self.email.side_effect = [RuntimeError("smtp down"), None]
        response = auth_client.post(
            ADD_TEAM_USER_URL,
            {
                "members": [
                    member_payload(),
                    member_payload(failed_email),
                    member_payload(last_email),
                ]
            },
            format="json",
        )

        result = assert_partial_response(response)
        assert [item["email"] for item in result["created_members"]] == [
            NEW_EMAIL,
            last_email,
        ]
        assert result["created_members"][0]["invite_email_warning"] == WARNING
        assert len(result["errors"]) == 1
        assert result["errors"][0]["email"] == failed_email
        assert result["errors"][0]["index"] == 1
        assert_pending_member(NEW_EMAIL, organization, workspace, user)
        assert_pending_member(last_email, organization, workspace, user)
        assert not User.objects.filter(email=failed_email).exists()
        assert not WorkspaceMembership.all_objects.filter(
            user__email=failed_email
        ).exists()
        assert not OrganizationMembership.all_objects.filter(
            user__email=failed_email
        ).exists()
        assert not OrganizationInvite.all_objects.filter(
            target_email=failed_email
        ).exists()
        assert self.email.call_count == 2
