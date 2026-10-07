"""POST /accounts/team/users/ is all-or-nothing under the Community rule (R2).

The seat count and every write of the request (organization settings,
workspace, users, memberships, invites) share one transaction and the
edition lock. A refusal leaves nothing behind and sends no invitation email.

The race tests drive the mounted endpoint from two threads with real access
tokens. The second request starts once the first has passed its seat check,
and the first resumes once the second has either finished or is queued on
the edition advisory lock, so the interleaving is the same on every run.
Every wait is bounded.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_invite import OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.capabilities import edition
from tfc.capabilities.edition import EDITION_LOCK_KEY, EditionResource
from tfc.capabilities.tests.edition_factories import make_member
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.edition_rule, pytest.mark.django_db(transaction=True)]

TEAM_URL = "/accounts/team/users/"
WAIT_SECONDS = 20


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


def _jwt_client(email, password="testpassword123"):
    client = APIClient()
    login = client.post(
        "/accounts/token/", {"email": email, "password": password}, format="json"
    )
    assert login.status_code == 200, login.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    return client


def _members(*emails):
    return [
        {
            "email": email,
            "name": email.split("@")[0],
            "role": OrganizationRoles.WORKSPACE_MEMBER,
        }
        for email in emails
    ]


def _edition_lock_waiters() -> int:
    """Sessions queued on (not holding) the edition advisory lock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND NOT granted AND classid::bigint = %s AND objid::bigint = %s "
            "AND objsubid = 1",
            [EDITION_LOCK_KEY >> 32, EDITION_LOCK_KEY & 0xFFFFFFFF],
        )
        return cursor.fetchone()[0]


def _race(monkeypatch, owner, first_payload, second_payload):
    """POST ``first_payload`` here and ``second_payload`` from another thread.

    The second request starts right after the first has passed its Community
    seat check, before it writes a member.
    """
    resolve(TEAM_URL)  # import the URLconf before any lock is held
    client = _jwt_client(owner.email)
    responses = {}
    timed_out = []

    def _second():
        try:
            responses["second"] = client.post(TEAM_URL, second_payload, format="json")
        finally:
            connection.close()

    worker = threading.Thread(target=_second, daemon=True)
    main = threading.current_thread()
    real_assert_can_create = edition.assert_can_create

    def _assert_then_admit_second(resource, **kwargs):
        decision = real_assert_can_create(resource, **kwargs)
        if (
            threading.current_thread() is main
            and resource is EditionResource.MEMBER
            and worker.ident is None
        ):
            worker.start()
            deadline = time.monotonic() + WAIT_SECONDS
            while worker.is_alive() and not _edition_lock_waiters():
                if time.monotonic() > deadline:
                    timed_out.append(True)
                    break
                time.sleep(0.05)
            # Evidence only (visible with -s): which interleaving ran.
            print(
                "race: second request "
                + ("queued on the edition lock" if worker.is_alive() else "finished")
            )
        return decision

    monkeypatch.setattr(edition, "assert_can_create", _assert_then_admit_second)
    responses["first"] = client.post(TEAM_URL, first_payload, format="json")
    worker.join(WAIT_SECONDS)

    assert worker.ident is not None, "the first request never passed a seat check"
    assert not timed_out, "second request neither finished nor queued on the lock"
    assert not worker.is_alive(), "second request did not finish"
    return responses["first"], responses["second"]


def _assert_gate(response, feature):
    assert response.status_code == 402, response.content
    body = response.json()
    assert body["code"] == "ENTERPRISE_FEATURE_REQUIRED"
    assert body["enterprise_gate"]["feature"] == feature


def _assert_added(emails, organization):
    for email in emails:
        assert User.objects.filter(email=email, organization=organization).exists()
        assert OrganizationMembership.all_objects.filter(
            user__email=email, organization=organization, is_active=True
        ).exists()
        assert OrganizationInvite.all_objects.filter(
            organization=organization, target_email=email, status="Pending"
        ).exists()


def _assert_left_nothing(emails):
    for email in emails:
        assert not User.objects.filter(email=email).exists(), email
        assert not OrganizationMembership.all_objects.filter(
            user__email=email
        ).exists(), email
        assert not WorkspaceMembership.all_objects.filter(
            user__email=email
        ).exists(), email
        assert not OrganizationInvite.all_objects.filter(
            target_email=email
        ).exists(), email


def _outcome(first, second, first_emails, second_emails):
    """(winner emails, loser emails, first won?) after asserting one of each."""
    assert sorted([first.status_code, second.status_code]) == [201, 402], (
        first.content,
        second.content,
    )
    if first.status_code == 201:
        _assert_gate(second, "members")
        return first_emails, second_emails, True
    _assert_gate(first, "members")
    return second_emails, first_emails, False


def _recipients():
    return sorted(address for message in mail.outbox for address in message.to)


def test_last_seat_race_leaves_no_losing_user_or_settings(
    community, monkeypatch, organization, user, workspace
):
    """R2 / AC-08: two adds for the last seat; the loser leaves no row behind."""
    make_member(organization)  # owner + 1 = 2 of 3 seats
    original_org_name = Organization.objects.get(pk=organization.pk).display_name
    first_emails = ["first-joiner@example.com"]
    second_emails = ["second-joiner@example.com"]
    first_payload = {
        "org_name": "Renamed by first",
        "workspace": {
            "name": workspace.name,
            "display_name": "Workspace renamed by first",
            "description": "first",
        },
        "members": _members(*first_emails),
    }

    first, second = _race(
        monkeypatch, user, first_payload, {"members": _members(*second_emails)}
    )

    winners, losers, first_won = _outcome(first, second, first_emails, second_emails)
    _assert_added(winners, organization)
    _assert_left_nothing(losers)
    assert _recipients() == winners
    organization.refresh_from_db()
    workspace.refresh_from_db()
    assert organization.display_name == (
        "Renamed by first" if first_won else original_org_name
    )
    assert workspace.display_name == (
        "Workspace renamed by first" if first_won else "Test Workspace"
    )
    assert edition.count(EditionResource.MEMBER, organization=organization) == 3


def test_batch_race_rolls_back_the_whole_losing_batch(
    community, monkeypatch, organization, user
):
    """R2 / C8: a batch is all-or-nothing, including a refusal after its first
    member, and no invitation email leaves for a rolled-back member."""
    first_emails = ["batch-one@example.com", "batch-two@example.com"]
    second_emails = ["solo@example.com"]

    first, second = _race(
        monkeypatch,
        user,
        {"members": _members(*first_emails)},
        {"members": _members(*second_emails)},
    )

    winners, losers, _ = _outcome(first, second, first_emails, second_emails)
    _assert_added(winners, organization)
    _assert_left_nothing(losers)
    assert _recipients() == sorted(winners)
    assert edition.count(EditionResource.MEMBER, organization=organization) == 3


def test_workspace_refusal_rolls_back_org_settings_and_members(
    community, organization, user
):
    """R2: a refused second workspace leaves the organization untouched."""
    before = Organization.objects.get(pk=organization.pk)
    client = _jwt_client(user.email)

    response = client.post(
        TEAM_URL,
        {
            "org_name": "Renamed then refused",
            "workspace": {"name": "second-workspace"},
            "members": _members("ws-joiner@example.com"),
        },
        format="json",
    )

    _assert_gate(response, "workspaces")
    after = Organization.objects.get(pk=organization.pk)
    assert (after.display_name, after.is_new) == (before.display_name, before.is_new)
    assert not Workspace.all_objects.filter(name="second-workspace").exists()
    _assert_left_nothing(["ws-joiner@example.com"])
    assert mail.outbox == []


@pytest.mark.parametrize("mode", ["cloud", "licensed"])
def test_cloud_and_licensed_team_adds_take_no_edition_lock(
    mode, monkeypatch, organization, user
):
    """R2: only the Community rule serialises the team endpoint."""
    monkeypatch.setattr(edition, "is_cloud", lambda: mode == "cloud")
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: True)
    client = _jwt_client(user.email)
    emails = [f"{mode}-joiner@example.com"]

    with CaptureQueriesContext(connection) as queries:
        response = client.post(TEAM_URL, {"members": _members(*emails)}, format="json")

    assert response.status_code == 201, response.content
    assert not any("pg_advisory" in q["sql"] for q in queries.captured_queries)
    _assert_added(emails, organization)
    assert _recipients() == emails


@pytest.mark.parametrize("failure_kind", ["email", "org_analytics"])
def test_after_commit_delivery_failure_does_not_turn_committed_team_into_500(
    failure_kind, community, monkeypatch, organization, user
):
    """An after-commit side effect cannot undo the already-persisted users.

    Do not respond with a misleading retryable 500 or skip later invitations.
    The pending invite remains available for the existing resend flow.
    """
    from accounts.views import workspace_management as team_views

    client = _jwt_client(user.email)
    client.raise_request_exception = False
    emails = ["delivery-one@example.com", "delivery-two@example.com"]
    attempts = []
    if failure_kind == "email":
        original_email_helper = team_views.email_helper

        def sometimes_fails(*args, **kwargs):
            attempts.append(args[3][0])
            if len(attempts) == 1:
                raise RuntimeError("controlled test mail failure")
            return original_email_helper(*args, **kwargs)

        monkeypatch.setattr(team_views, "email_helper", sometimes_fails)
    else:

        def fails_analytics(*args, **kwargs):
            raise RuntimeError("controlled test analytics failure")

        monkeypatch.setattr(
            team_views.mixpanel_tracker, "update_org_details", fails_analytics
        )

    response = client.post(
        TEAM_URL,
        {"org_name": "Committed team name", "members": _members(*emails)},
        format="json",
    )
    assert response.status_code == 201
    _assert_added(emails, organization)
    if failure_kind == "email":
        assert attempts == emails
        assert _recipients() == emails[1:]
    else:
        assert _recipients() == emails
    organization.refresh_from_db()
    assert organization.display_name == "Committed team name"


def test_cloud_email_failure_keeps_existing_immediate_error_behavior(
    monkeypatch, organization, user
):
    from accounts.views import workspace_management as team_views

    monkeypatch.setattr(edition, "is_cloud", lambda: True)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: True)

    def fails_email(*args, **kwargs):
        raise RuntimeError("controlled test Cloud mail failure")

    monkeypatch.setattr(team_views, "email_helper", fails_email)
    client = _jwt_client(user.email)
    email = "cloud-delivery@example.com"
    response = client.post(TEAM_URL, {"members": _members(email)}, format="json")
    assert response.status_code == 400
    _assert_added([email], organization)
