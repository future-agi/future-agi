"""The AI invite_users tool cannot race past the Community member limit (TH-8084).

verify-final-r3 N2: the tool checked seats outside ``edition.creation_lock()``
and creates users by the legacy ``User.organization`` FK only, so the
``ensure_org_membership`` backstop never runs for it. Two concurrent tool calls
at two used seats could both pass the check and leave four members.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.db import connection

from accounts.models.user import User
from tfc.capabilities import edition
from tfc.capabilities.edition import EditionResource
from tfc.capabilities.tests.edition_factories import (
    make_member,
    make_org,
    make_owner,
    make_workspace,
)
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.edition_rule

RACE_EMAILS = ("ai-race-a@futureagi.com", "ai-race-b@futureagi.com")


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


@pytest.mark.django_db(transaction=True)
def test_concurrent_ai_tool_invites_stop_at_three_members(community, monkeypatch):
    from ai_tools.base import ToolContext
    from ai_tools.tools.users.invite_users import InviteUsersInput, InviteUsersTool

    org = make_org()
    owner = make_owner(org)
    User.objects.filter(pk=owner.pk).update(organization_role=OrganizationRoles.OWNER)
    owner.refresh_from_db()
    make_member(org)
    workspace = make_workspace(org, owner, is_default=True)
    assert edition.count(EditionResource.MEMBER, organization=org) == 2

    real_assert_can_create = edition.assert_can_create

    def slow_assert_can_create(*args, **kwargs):
        decision = real_assert_can_create(*args, **kwargs)
        # Widen the window between the seat check and the user insert.
        time.sleep(0.5)
        return decision

    monkeypatch.setattr(edition, "assert_can_create", slow_assert_can_create)

    barrier = threading.Barrier(len(RACE_EMAILS))
    results: dict[str, object] = {}

    def _invite(email: str) -> None:
        try:
            barrier.wait(timeout=10)
            results[email] = InviteUsersTool().execute(
                InviteUsersInput(emails=[email]),
                ToolContext(user=owner, organization=org, workspace=workspace),
            )
        except Exception as exc:  # reported by the assertions below
            results[email] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=_invite, args=(e,)) for e in RACE_EMAILS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert set(results) == set(RACE_EMAILS), results
    gated = [
        email
        for email, result in results.items()
        if getattr(result, "error_code", None) == "ENTERPRISE_FEATURE_REQUIRED"
    ]
    invited = [
        email
        for email, result in results.items()
        if not isinstance(result, Exception)
        and not result.is_error
        and len(result.data["results"]) == 1
    ]
    assert len(invited) == 1 and len(gated) == 1, {
        email: getattr(result, "content", result) for email, result in results.items()
    }
    assert edition.count(EditionResource.MEMBER, organization=org) == 3
    assert User.objects.filter(email__in=RACE_EMAILS).count() == 1
