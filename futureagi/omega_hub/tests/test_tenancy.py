"""Tenant and organisation isolation (SPEC §2.3, §10): a publisher belongs to one user or one
organisation, and only its owner (or the org's Admins and Owners) can act for it."""

import pytest

from omega_hub.models import Publisher, Submission
from omega_hub.tests.helpers import entry_files, make_person, session_client, submit
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db


@pytest.fixture
def acme(world):
    owner = make_person("acme-owner", org_level=Level.OWNER)
    publisher = Publisher.objects.create(handle="acme-tools", kind="organization", owner_org=owner.organization,
                                         display_name="Acme")
    return owner, publisher


def test_org_admin_can_create_org_publisher_and_member_cannot(world):
    admin = make_person("org-admin", org_level=Level.ADMIN)
    member = make_person("org-member", org_level=Level.MEMBER, organization=admin.organization)
    body = {"handle": "acme-labs", "kind": "organization", "organizationId": str(admin.organization.id), "displayName": "Acme Labs"}
    refused = session_client(member).post("/omega-hub/v1/publishers", body, format="json")
    assert refused.status_code == 403
    created = session_client(admin).post("/omega-hub/v1/publishers", body, format="json")
    assert created.status_code == 201, created.content
    stranger = make_person("stranger")
    other = session_client(stranger).post("/omega-hub/v1/publishers", {**body, "handle": "acme-labs-2"}, format="json")
    assert other.status_code == 403


def test_only_the_publishers_org_admins_can_submit(world, acme):
    owner, _ = acme
    files = entry_files("first-agent")
    member = make_person("acme-member", org_level=Level.MEMBER, organization=owner.organization)
    assert submit(session_client(member), "acme-tools", files).status_code == 403
    assert submit(world.c, "acme-tools", files).status_code == 403
    assert submit(session_client(owner), "acme-tools", files).status_code == 201


def test_submissions_are_invisible_across_tenants(world, acme):
    owner, _ = acme
    created = submit(session_client(owner), "acme-tools", entry_files("first-agent"))
    sub_id = created.json()["id"]
    assert world.c.get(f"/omega-hub/v1/submissions/{sub_id}").status_code == 404
    assert world.c.get(f"/omega-hub/v1/submissions/{sub_id}/bundle").status_code == 404
    assert all(row["id"] != sub_id for row in world.c.get("/omega-hub/v1/submissions").json())
    assert session_client(owner).get(f"/omega-hub/v1/submissions/{sub_id}").status_code == 200
    # A token for another publisher sees nothing either
    from omega_hub.tests.helpers import token_client

    assert token_client(world.token_c).get(f"/omega-hub/v1/submissions/{sub_id}").status_code == 404
    # Maintainers see it in review
    assert world.b.get(f"/omega-hub/v1/submissions/{sub_id}").status_code == 200


def test_user_publisher_belongs_to_one_user(world):
    other = make_person("dave")
    response = session_client(other).patch("/omega-hub/v1/publishers/c-test", {"bio": "mine now"}, format="json")
    assert response.status_code == 403
    assert world.c.patch("/omega-hub/v1/publishers/c-test", {"bio": "Carol's tools."}, format="json").status_code == 200
    second = world.c.post("/omega-hub/v1/publishers", {"handle": "carol-two", "kind": "user", "displayName": "Carol"},
                          format="json")
    assert second.status_code == 409 and second.json()["error"]["code"] == "OMEGA_HUB_ONE_USER_PUBLISHER"


def test_withdraw_only_own(world, acme):
    owner, _ = acme
    sub_id = submit(session_client(owner), "acme-tools", entry_files("first-agent")).json()["id"]
    assert world.c.post(f"/omega-hub/v1/submissions/{sub_id}/withdraw").status_code == 404
    assert session_client(owner).post(f"/omega-hub/v1/submissions/{sub_id}/withdraw").status_code == 200
    assert Submission.objects.get(pk=sub_id).status == "withdrawn"


def test_only_official_publisher_may_use_the_github_channel(world):
    files = entry_files("first-agent")
    refused = submit(world.c, "c-test", files, channel="github", githubCommit="a" * 40)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "OMEGA_HUB_CHANNEL_NOT_ALLOWED"
    from omega_hub.tests.helpers import token_client

    ok = submit(token_client(world.token_a), "future-agi", files, channel="github", githubCommit="b" * 40)
    assert ok.status_code == 201
