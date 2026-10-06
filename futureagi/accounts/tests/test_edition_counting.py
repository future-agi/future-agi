"""Edition-rule counting against real accounts rows, and race safety (TH-8084)."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest
from django.db import connection

from accounts.models.workspace import Workspace
from tfc.capabilities import edition
from tfc.capabilities.edition import EditionResource
from tfc.capabilities.errors import EnterpriseFeatureRequired
from tfc.capabilities.tests.edition_factories import (
    make_invite,
    make_member,
    make_org,
    make_owner,
    make_user,
)

pytestmark = pytest.mark.edition_rule


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


@pytest.mark.django_db
class TestInviteAcceptIsNeutral:
    def test_accept_moves_a_seat_from_pending_to_active(self, community):
        """AC-08 / C8: the invite held the seat, so accepting needs no check."""
        org = make_org()
        make_owner(org)
        make_member(org)
        invite = make_invite(org, "joiner@x.io")
        assert edition.count(EditionResource.MEMBER, organization=org) == 3

        invite.accept(make_user("joiner@x.io"))

        assert edition.count(EditionResource.MEMBER, organization=org) == 3
        refused = edition.check_creation(
            EditionResource.MEMBER, organization=org, new_member_emails=["next@x.io"]
        )
        assert refused.allowed is False

    def test_pending_invites_on_over_limit_installs_can_still_be_accepted(
        self, community
    ):
        """AC-11: pre-existing invites are part of 'everything they have'."""
        org = make_org()
        make_owner(org)
        for _ in range(3):
            make_member(org)
        invite = make_invite(org, "late@x.io")

        invite.accept(make_user("late@x.io"))

        assert edition.count(EditionResource.MEMBER, organization=org) == 5


def _race_first_workspace(org, owner, lock) -> tuple[list[str], list[str]]:
    """Two threads try to create the install's first workspace at once."""
    barrier = threading.Barrier(2)
    created: list[str] = []
    refused: list[str] = []

    def _worker(name: str) -> None:
        try:
            barrier.wait(timeout=10)
            with lock():
                edition.assert_can_create(EditionResource.WORKSPACE)
                # Widen the window between count and insert.
                time.sleep(0.3)
                Workspace.objects.create(
                    name=name, organization=org, created_by=owner, is_active=True
                )
            created.append(name)
        except EnterpriseFeatureRequired:
            refused.append(name)
        finally:
            connection.close()

    threads = [threading.Thread(target=_worker, args=(f"ws-{i}",)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return created, refused


@contextmanager
def _no_lock():
    yield


@pytest.mark.django_db(transaction=True)
class TestCreationRace:
    def test_concurrent_first_workspace_exactly_one_succeeds(self, community):
        """AC-08 / R4: the advisory lock serialises count-then-insert."""
        org = make_org()
        owner = make_owner(org)

        created, refused = _race_first_workspace(org, owner, edition.creation_lock)

        assert len(created) == 1
        assert len(refused) == 1
        assert Workspace.no_workspace_objects.filter(organization=org).count() == 1

    def test_without_the_lock_the_race_is_real(self, community):
        """Control for the case above: the harness does detect a lost update."""
        org = make_org()
        owner = make_owner(org)

        created, refused = _race_first_workspace(org, owner, _no_lock)

        assert len(created) == 2
        assert refused == []
