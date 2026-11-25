"""Concurrent creation of an organization's default workspace.

Two first-login requests (or a request and the onboarding seeding beside it)
can both miss the default workspace and both insert it. The loser's INSERT
fails with a duplicate key on ``(organization, name)``. Both callers must still
get the winner's workspace, without an ERROR log, and a failed insert must not
break the caller's transaction.
"""

import threading
import time
import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.views.organization_selection import SwitchOrganizationView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

# The message Postgres raised in production for the losing INSERT.
NAME_INDEX_CONFLICT = (
    'duplicate key value violates unique constraint "accounts_workspace_'
    'organization_id_name_b2628086_uniq"\nDETAIL:  Key (organization_id, '
    "name)=(00000000-0000-0000-0000-000000000000, Default Workspace) "
    "already exists."
)
THREAD_TIMEOUT_S = 30


def _organization():
    return Organization.objects.create(name=f"race-{uuid.uuid4().hex[:8]}")


def _owner(organization):
    user = User.objects.create_user(
        email=f"race-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="race",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return user


def _run_in_thread(target):
    """Run ``target`` on its own DB connection and return its outcome."""
    outcome = {}

    def runner():
        try:
            outcome["value"] = target()
        except Exception as exc:  # noqa: BLE001 - surfaced to the test thread
            outcome["error"] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=runner)
    thread.start()
    return thread, outcome


def _commit_concurrent_default(organization, creator):
    """Commit a default workspace from another connection, as a racing request."""

    def insert():
        workspace = Workspace(
            name="Default Workspace",
            organization=organization,
            is_default=True,
            is_active=True,
            created_by=creator,
        )
        workspace.save()
        return workspace

    thread, outcome = _run_in_thread(insert)
    thread.join(THREAD_TIMEOUT_S)
    assert "error" not in outcome, outcome.get("error")
    return outcome["value"]


def _lose_the_insert_race(organization, creator):
    """Make the default-workspace INSERT lose to a concurrent committed one.

    The read before the insert really misses; the insert then finds the other
    writer's committed row and fails on the ``(organization, name)`` index,
    exactly as the production error reported.
    """
    state = {"winner": None}

    def losing_insert(*args, **kwargs):
        state["winner"] = _commit_concurrent_default(organization, creator)
        raise IntegrityError(NAME_INDEX_CONFLICT)

    return patch.object(Workspace.objects, "create", side_effect=losing_insert), state


@pytest.mark.django_db(transaction=True)
def test_login_that_loses_the_insert_race_gets_the_winners_workspace():
    organization = _organization()
    winner_user = _owner(organization)
    user = _owner(organization)
    race, state = _lose_the_insert_race(organization, winner_user)

    with race, patch("accounts.authentication.logger") as auth_logger:
        workspace = APIKeyAuthentication()._get_or_create_default_workspace(
            user, organization
        )

    assert state["winner"] is not None, "the insert path was not exercised"
    assert workspace is not None
    assert workspace.id == state["winner"].id
    assert WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace, is_active=True
    ).exists()
    auth_logger.error.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_org_switch_that_loses_the_insert_race_gets_the_winners_workspace():
    organization = _organization()
    winner_user = _owner(organization)
    user = _owner(organization)
    race, state = _lose_the_insert_race(organization, winner_user)

    with race, patch(
        "accounts.views.organization_selection.logger"
    ) as selection_logger:
        workspace = SwitchOrganizationView()._resolve_workspace_for_org(
            user, organization
        )

    assert state["winner"] is not None, "the insert path was not exercised"
    assert workspace is not None
    assert workspace.id == state["winner"].id
    selection_logger.error.assert_not_called()


def _wait_for_a_lock_waiter():
    """Poll until another session is blocked on a row lock (the losing INSERT)."""
    deadline = time.monotonic() + THREAD_TIMEOUT_S
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.05)
    return False


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_first_logins_get_the_same_default_workspace():
    organization = _organization()
    first_user = _owner(organization)
    second_user = _owner(organization)
    auth = APIKeyAuthentication()
    first_inserted = threading.Event()
    release_first = threading.Event()

    def first_login():
        # Hold the first login's transaction open so its row is uncommitted
        # while the second login reads, misses and inserts.
        with transaction.atomic():
            workspace = auth._get_or_create_default_workspace(first_user, organization)
            first_inserted.set()
            release_first.wait(THREAD_TIMEOUT_S)
        return workspace

    def second_login():
        return auth._get_or_create_default_workspace(second_user, organization)

    with patch("accounts.authentication.logger") as auth_logger:
        first, first_outcome = _run_in_thread(first_login)
        try:
            assert first_inserted.wait(THREAD_TIMEOUT_S)
            second, second_outcome = _run_in_thread(second_login)
            blocked = _wait_for_a_lock_waiter()
        finally:
            release_first.set()
        first.join(THREAD_TIMEOUT_S)
        second.join(THREAD_TIMEOUT_S)

    assert blocked, "the second login never waited on the first one's insert"
    assert "error" not in first_outcome, first_outcome.get("error")
    assert "error" not in second_outcome, second_outcome.get("error")
    assert first_outcome["value"] is not None
    assert second_outcome["value"] is not None
    assert first_outcome["value"].id == second_outcome["value"].id
    assert (
        Workspace.objects.filter(organization=organization, is_default=True).count()
        == 1
    )
    assert WorkspaceMembership.no_workspace_objects.filter(
        user=second_user, workspace=second_outcome["value"], is_active=True
    ).exists()
    auth_logger.error.assert_not_called()


@pytest.mark.django_db
def test_an_unrelated_name_clash_fails_without_breaking_the_transaction():
    organization = _organization()
    user = _owner(organization)
    # A non-default workspace already holds the name, so no default exists to
    # fall back to and the clash is a real failure.
    Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=False,
        created_by=user,
    )

    with patch("accounts.authentication.logger") as auth_logger:
        workspace = APIKeyAuthentication()._get_or_create_default_workspace(
            user, organization
        )

    assert workspace is None
    auth_logger.error.assert_called_once()
    # The test runs inside a transaction: this query fails if the failed
    # INSERT was not isolated in a savepoint.
    assert Workspace.objects.filter(organization=organization).count() == 1
