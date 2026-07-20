"""Test: a transient invite-email failure must NOT block the member add
(PR #332, issue #159) in accounts.views.workspace_management.ManageTeamView.

Pre-fix: email_helper() ran in-line and uncaught; the exception propagated, so the
member add failed (and under ATOMIC_REQUESTS rolled the new user back too).
Post-fix: user + membership are committed in an inner transaction BEFORE the email,
and email_helper is wrapped in try/except that logs `invite_email_failed`.

Payload mirrors ManageTeamView.post: members=[{email,name,role}] where role must be a
valid OrganizationRoles value ("Member" == OrganizationRoles.MEMBER). The acting user
must be an org Owner (the endpoint is Owner-gated), so we ensure that membership like
the e2e suite does.

Run:  cd futureagi && pytest accounts/tests/test_invite_email_failure.py
"""
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.constants.levels import Level

ADD_TEAM_USER_URL = "/accounts/team/users/"
NEW_EMAIL = "newhire-emailfail@futureagi.com"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestInviteEmailFailureDecoupled:
    @patch("accounts.views.workspace_management.logger")
    @patch(
        "accounts.views.workspace_management.email_helper",
        side_effect=RuntimeError("smtp down"),
    )
    def test_member_add_succeeds_when_invite_email_fails(
        self, mock_email, mock_logger, auth_client, user, organization, workspace
    ):
        # ManageTeamView is Owner-gated -- mirror the e2e suite's owner-membership setup.
        OrganizationMembership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={"role": "Owner", "level": Level.OWNER, "is_active": True},
        )

        resp = auth_client.post(
            ADD_TEAM_USER_URL,
            {
                "workspace": {"name": workspace.name},
                "members": [
                    {"email": NEW_EMAIL, "name": "New Hire", "role": "Member"}
                ],
            },
            format="json",
        )

        # Post-fix: mail outage no longer fails the request. Pre-fix: exception propagated.
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
        # The new member persisted despite the mail failure (the core guarantee of #159).
        assert User.objects.filter(email=NEW_EMAIL).exists()
        # email_helper was actually hit, and the failure was logged, not swallowed.
        assert mock_email.called
        warned = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
        assert "invite_email_failed" in warned
