"""Who may approve and act (SPEC §5.1, §10): no bypass role, no self-approval, token scope."""

import json

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission

from omega_hub.admin import HUB_MODELS
from omega_hub.models import AuditEvent, Submission
from omega_hub.permissions import is_maintainer
from omega_hub.tests.conftest import grant_maintainer
from omega_hub.tests.helpers import (approve_payload, entry_files, make_person, run_checks, session_client, submit,
                                     token_client)
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
DISCLOSURE = json.loads(entry_files("first-agent")["omega.hub.json"])["disclosure"]


def submitted_by(client, world, token=None):
    files = entry_files("first-agent")
    created = submit(token_client(token) if token else client, "future-agi", files, name="first-agent", version="1.0.0")
    assert created.status_code == 201, created.content
    _, report, _ = run_checks(files)
    return created.json()["id"], report


def test_submitter_cannot_approve_their_own_submission(world):
    sub_id, report = submitted_by(None, world, token=world.token_a)
    response = world.a.post(f"/omega-hub/v1/review/submissions/{sub_id}/decision",
                            approve_payload(report, disclosure=DISCLOSURE), format="json")
    assert response.status_code == 403 and response.json()["error"]["code"] == "OMEGA_HUB_SELF_APPROVAL"
    assert Submission.objects.get(pk=sub_id).status == "in_review"


def test_non_maintainer_cannot_approve(world):
    sub_id, report = submitted_by(None, world, token=world.token_a)
    response = world.c.post(f"/omega-hub/v1/review/submissions/{sub_id}/decision",
                            approve_payload(report, disclosure=DISCLOSURE), format="json")
    assert response.status_code == 403 and response.json()["error"]["code"] == "OMEGA_HUB_NOT_MAINTAINER"
    assert world.c.get("/omega-hub/v1/review/queue").status_code == 403


def test_permission_without_admin_level_in_the_maintainer_org_is_not_enough(world):
    member = make_person("member", org_level=Level.MEMBER, organization=world.maint_org)
    grant_maintainer(member.user)
    assert not is_maintainer(member.user)
    outsider = make_person("outsider", org_level=Level.OWNER)
    grant_maintainer(outsider.user)
    assert not is_maintainer(outsider.user)
    sub_id, report = submitted_by(None, world, token=world.token_a)
    for person in (member, outsider):
        response = session_client(person).post(f"/omega-hub/v1/review/submissions/{sub_id}/decision",
                                               approve_payload(report, disclosure=DISCLOSURE), format="json")
        assert response.status_code == 403


def test_superuser_is_not_a_maintainer_without_both_conditions(world):
    root = make_person("root", org_level=Level.ADMIN, organization=world.maint_org)
    root.user.is_superuser = True
    root.user.save()
    assert root.user.has_perm("omega_hub.hub_maintainer")  # Django says yes for superusers...
    assert not is_maintainer(root.user)  # ...the Hub does not.


def test_admin_level_without_the_permission_is_not_enough(world):
    admin_user = make_person("admin", org_level=Level.ADMIN, organization=world.maint_org)
    assert not is_maintainer(admin_user.user)


def test_no_maintainer_org_configured_means_no_maintainers(world, hub_settings):
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = None
    assert not is_maintainer(world.maint_b.user)


def test_hub_token_is_refused_outside_its_scope(world):
    client = token_client(world.token_a)
    assert client.get("/omega-hub/v1/me").status_code == 200
    for method, path in (("get", "/omega-hub/v1/review/queue"), ("post", "/omega-hub/v1/reports"),
                         ("get", "/omega-hub/v1/me/tokens"), ("post", "/omega-hub/v1/publishers")):
        response = getattr(client, method)(path, {}, format="json")
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "OMEGA_HUB_TOKEN_SCOPE", path


def test_hub_token_for_another_publisher_cannot_submit(world):
    files = entry_files("first-agent")
    response = submit(token_client(world.token_c), "future-agi", files)
    assert response.status_code == 403 and response.json()["error"]["code"] == "OMEGA_HUB_TOKEN_SCOPE"


def test_token_stops_working_when_its_user_no_longer_acts_for_the_org_publisher(world):
    """Review finding: the token was checked only against its publisher, so a user removed from the
    organisation kept publish rights through a token minted earlier, for up to 365 days."""
    from accounts.models.organization_membership import OrganizationMembership
    from omega_hub.models import Publisher
    from omega_hub.services import tokens

    org_pub = Publisher.objects.create(handle="c-org", kind="organization", owner_org=world.carol.organization,
                                       display_name="C Org")
    _, plaintext = tokens.create_token(world.carol.user, org_pub, "ci")
    client = token_client(plaintext)
    files = entry_files("first-agent")
    assert client.get("/omega-hub/v1/me").status_code == 200
    membership = OrganizationMembership.no_workspace_objects.filter(user=world.carol.user, organization=world.carol.organization)

    membership.update(is_active=False)
    for response in (submit(client, "c-org", files, name="first-agent", version="1.0.0"), client.get("/omega-hub/v1/me")):
        assert response.status_code == 403, response.content
        assert response.json()["error"]["code"] == "OMEGA_HUB_NOT_PUBLISHER_OWNER"

    membership.update(is_active=True, level=Level.MEMBER, role="Member")
    assert submit(client, "c-org", files, name="first-agent", version="1.0.0").status_code == 403

    membership.update(level=Level.ADMIN, role="Admin")
    assert submit(client, "c-org", files, name="first-agent", version="1.0.0").status_code == 201
    assert not Submission.objects.filter(publisher=org_pub).exclude(status="received").exists()


def test_bearer_hub_token_does_not_authenticate(world):
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {world.token_a}")
    response = client.get("/omega-hub/v1/me")
    assert response.status_code == 401


def test_revoked_or_unknown_token_is_401(world):
    world.c.delete(f"/omega-hub/v1/me/tokens/{world.token_c_obj.pk}")
    assert token_client(world.token_c).get("/omega-hub/v1/me").status_code == 401
    assert token_client("ohp_" + "x" * 40).get("/omega-hub/v1/me").status_code == 401


def test_every_hub_admin_refuses_add_change_and_delete(world, rf):
    request = rf.get("/admin/")
    request.user = world.maint_a.user
    for model in HUB_MODELS:
        model_admin = admin.site._registry[model]
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)


def test_a_grant_made_outside_the_command_is_audited(world):
    person = make_person("granted", org_level=Level.ADMIN, organization=world.maint_org)
    perm = Permission.objects.get(content_type__app_label="omega_hub", codename="hub_maintainer")
    person.user.user_permissions.add(perm)
    event = AuditEvent.objects.get(action="maintainer.granted", target_id=str(person.user.pk))
    assert event.data["via"] == "django-admin"
    person.user.user_permissions.remove(perm)
    assert AuditEvent.objects.filter(action="maintainer.revoked", target_id=str(person.user.pk)).count() == 1
