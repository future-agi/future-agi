"""TH-5475: invited users must reach the same signup destinations as owners.

Accepting an invitation for a user that an invite created queues a dedicated
reporting activity. The activity reports to HubSpot, the Slack signup feed and
Mixpanel once per user, and never runs owner onboarding (generated-password
email, demo dataset, demo traces).
"""

from unittest.mock import ANY, patch

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient
from temporalio.common import WorkflowIDConflictPolicy

import tfc.temporal.background_tasks.activities  # noqa: F401  (registers activities)
from accounts.models.user import User
from accounts.utils import INVITE_SIGNUP_REPORTED_KEY
from analytics.utils import MixpanelEvents
from tfc.constants.levels import Level
from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

INVITE_URL = "/accounts/organization/invite/"
ACTIVITY = "run_invite_acceptance_reporting_activity"
PASSWORD = "SecurePass123!"


def _invite(auth_client, workspace, email):
    resp = auth_client.post(
        INVITE_URL,
        {
            "emails": [email],
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(workspace.id), "level": Level.WORKSPACE_MEMBER}
            ],
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    return User.objects.get(email=email)


def _accept(invitee):
    uid = urlsafe_base64_encode(force_bytes(invitee.pk))
    token = default_token_generator.make_token(invitee)
    return APIClient().post(
        f"/accounts/accept-invitation/{uid}/{token}/",
        {"new_password": PASSWORD, "repeat_password": PASSWORD},
        format="json",
    )


def _reporting_calls(start_activity):
    return [
        call
        for call in start_activity.call_args_list
        if call.args and call.args[0] == ACTIVITY
    ]


@pytest.mark.django_db
class TestInviteAcceptanceQueuesReporting:
    def test_accepting_an_invite_queues_one_reporting_run_per_user(
        self, auth_client, workspace, user
    ):
        invitee = _invite(auth_client, workspace, "reported-invitee@example.com")
        assert invitee.invited_by_id == user.id

        with patch("tfc.temporal.drop_in.start_activity") as start_activity:
            resp = _accept(invitee)

        assert resp.status_code == status.HTTP_200_OK
        calls = _reporting_calls(start_activity)
        assert len(calls) == 1
        assert calls[0].kwargs == {
            "args": (str(invitee.id),),
            "queue": "default",
            "task_id": f"invite-signup-report-{invitee.id}",
            "id_conflict_policy": WorkflowIDConflictPolicy.USE_EXISTING,
        }

    def test_acceptance_completes_when_reporting_cannot_be_queued(
        self, auth_client, workspace
    ):
        invitee = _invite(auth_client, workspace, "queue-down-invitee@example.com")

        with patch(
            "tfc.temporal.drop_in.start_activity",
            side_effect=RuntimeError("temporal unavailable"),
        ):
            resp = _accept(invitee)

        assert resp.status_code == status.HTTP_200_OK
        invitee.refresh_from_db()
        assert invitee.is_active is True

    def test_user_not_created_by_an_invite_is_not_reported(
        self, auth_client, workspace
    ):
        invitee = _invite(auth_client, workspace, "no-inviter@example.com")
        User.objects.filter(pk=invitee.pk).update(invited_by=None)
        invitee.refresh_from_db()

        with patch("tfc.temporal.drop_in.start_activity") as start_activity:
            resp = _accept(invitee)

        assert resp.status_code == status.HTTP_200_OK
        assert _reporting_calls(start_activity) == []


@pytest.mark.django_db
class TestInviteAcceptanceReportingActivity:
    @pytest.fixture
    def invitee(self, user, organization):
        return User.objects.create_user(
            email="accepted-invitee@example.com",
            password=PASSWORD,
            name="Accepted Invitee",
            organization=organization,
            invited_by=user,
            is_active=True,
            config={"currentOrganizationId": str(organization.id)},
        )

    @pytest.fixture
    def destinations(self, monkeypatch):
        monkeypatch.setenv("ENV_TYPE", "staging")
        with (
            patch(
                "accounts.utils.send_hubspot_notification", return_value=(True, None)
            ) as hubspot,
            patch("accounts.utils.send_slack_notification") as slack,
            patch("analytics.utils.track_mixpanel_event") as mixpanel,
            patch("accounts.utils.send_signup_email") as signup_email,
            patch("accounts.user_onboard.upload_demo_dataset") as demo_dataset,
            patch("accounts.user_onboard.create_demo_traces_and_spans") as demo_traces,
        ):
            yield {
                "hubspot": hubspot,
                "slack": slack,
                "mixpanel": mixpanel,
                "owner_onboarding": (signup_email, demo_dataset, demo_traces),
            }

    def test_activity_is_registered_on_the_default_queue(self):
        assert _ACTIVITY_REGISTRY[ACTIVITY]["queue"] == "default"

    def test_reports_hubspot_slack_and_mixpanel_once(self, invitee, destinations):
        run = _ACTIVITY_REGISTRY[ACTIVITY]["func"]

        run(str(invitee.id))

        destinations["hubspot"].assert_called_once()
        assert destinations["hubspot"].call_args.args[0].id == invitee.id
        destinations["slack"].assert_called_once_with(ANY, updated=True, err=None)
        destinations["mixpanel"].assert_called_once()
        event, properties = destinations["mixpanel"].call_args.args
        assert event == MixpanelEvents.SIGNUP.value
        assert properties["signup_origin"] == "invite_acceptance"
        for onboarding_step in destinations["owner_onboarding"]:
            onboarding_step.assert_not_called()

        invitee.refresh_from_db()
        assert invitee.config[INVITE_SIGNUP_REPORTED_KEY] is True
        assert invitee.config["currentOrganizationId"] == str(
            invitee.organization_id
        )

        run(str(invitee.id))

        destinations["hubspot"].assert_called_once()
        destinations["slack"].assert_called_once()
        destinations["mixpanel"].assert_called_once()
