"""Archived-skill recovery through mounted routes and real authentication."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.urls import include, path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.skills_hub import HubSkill, HubSkillLabelEvent
from model_hub.models.skills_hub_v12 import HubSkillContextOperation
from model_hub.services.skills_hub.v12.credentials import (
    canonical_digest,
    issue_credential,
)
from model_hub.services.skills_hub.v12.safety import get_version_safety, set_quarantine
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/archived/"
SUMMARY_FIELDS = {
    "id",
    "kind",
    "name",
    "description",
    "tags",
    "latest_version",
    "release_revision",
    "security_revision",
    "protected_label_names",
    "archived",
}


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture
def admin(user, organization, workspace):
    key = OrgApiKey.objects.create(
        name="Synthetic recovery user",
        type="user",
        user=user,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


@pytest.fixture
def archived(hub_skill, publish_skill, make_skill_files):
    # Keep production on v1 while latest is v2, so restore cannot conflate them.
    publish_skill(hub_skill, labels=["production"])
    publish_skill(hub_skill, files=make_skill_files(body="Second retained policy."))
    HubSkill._base_manager.filter(pk=hub_skill.pk).update(
        deleted=True,
        deleted_at=timezone.now(),
        protected_label_names=["production"],
    )
    hub_skill.refresh_from_db()
    return hub_skill


@pytest.fixture
def consumer(principal, context, context_version):
    version = context_version(context)
    issued = issue_credential(
        principal=principal,
        idempotency_key=uuid4(),
        request={
            "purpose": "consume",
            "grants": [
                {
                    "kind": "agent_context",
                    "resource_id": str(context.pk),
                    "labels": [],
                    "version_ids": [str(version.pk)],
                }
            ],
            "binding_ids": [],
            "expires_at": timezone.now() + timedelta(days=1),
        },
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Context " + issued["secret"])
    return client


def result(response):
    assert response.status_code == 200, response.data
    return response.data["result"]


def review(client, skill):
    return result(client.get(f"{BASE}{skill.pk}/restore-review/"))


def restore_body(reviewed):
    return {
        "expected_release_revision": reviewed["resource"]["release_revision"],
        "expected_review_fingerprint": reviewed["review_fingerprint"],
    }


def restore(client, skill, body, key=None):
    return client.post(
        f"{BASE}{skill.pk}/restore/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(key if key is not None else uuid4()),
    )


def assert_conflict(response, code):
    assert response.status_code == 409, response.data
    assert response.data["code"] == code


def test_admin_list_has_archive_metadata_and_excludes_live_and_context_resources(
    admin, archived, context
):
    HubSkill._base_manager.create(
        organization_id=archived.organization_id,
        workspace_id=archived.workspace_id,
        name="live-skill",
        kind="skill",
    )
    HubSkill._base_manager.filter(pk=context.pk).update(deleted=True)
    page = result(admin.get(BASE))
    assert {key: value for key, value in page.items() if key != "results"} == {
        "count": 1,
        "page": 1,
        "page_size": 25,
        "total_pages": 1,
    }
    (row,) = page["results"]
    assert set(row) == SUMMARY_FIELDS | {"archived_at", "labels"}
    assert row["id"] == str(archived.pk)
    assert row["kind"] == "skill"
    assert row["archived"] is True
    assert row["latest_version"] == 2
    assert parse_datetime(row["archived_at"]) == archived.deleted_at
    assert row["labels"] == [{"name": "production", "version": 1}]


def test_list_pagination_and_legacy_null_timestamp(admin, archived):
    other = HubSkill._base_manager.create(
        organization_id=archived.organization_id,
        workspace_id=archived.workspace_id,
        name="zz-archived-without-versions",
        kind="skill",
        deleted=True,
    )
    page = result(admin.get(BASE, {"page": 2, "page_size": 1}))
    assert page["count"] == 2
    assert page["total_pages"] == 2
    assert page["page"] == 2
    assert page["page_size"] == 1
    (row,) = page["results"]
    assert row["id"] == str(other.pk)
    assert row["archived_at"] is None
    assert row["latest_version"] is None
    assert row["labels"] == []
    assert result(admin.get(BASE, {"page": 3, "page_size": 1}))["results"] == []
    assert result(admin.get(BASE, {"page_size": 100}))["count"] == 2


@pytest.mark.parametrize(
    "query",
    [
        {"page": 0},
        {"page": "no"},
        {"page_size": 0},
        {"page_size": 101},
        {"page_size": "1.5"},
    ],
)
def test_invalid_pagination_is_rejected(admin, query):
    response = admin.get(BASE, query)
    assert response.status_code == 400, response.data


@pytest.mark.parametrize("endpoint", ["list", "review", "restore"])
def test_member_cannot_list_review_or_restore(
    admin, archived, user, organization, workspace, endpoint
):
    reviewed = review(admin, archived)
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role="workspace_member", level=Level.WORKSPACE_MEMBER)
    if endpoint == "list":
        response = admin.get(BASE)
    elif endpoint == "review":
        response = admin.get(f"{BASE}{archived.pk}/restore-review/")
    else:
        response = restore(admin, archived, restore_body(reviewed))
    assert response.status_code == 403, response.data
    assert response.data["code"] == "quarantine_forbidden"
    archived.refresh_from_db()
    assert archived.deleted
    assert not HubSkillContextOperation.objects.filter(operation="unarchive").exists()


@pytest.mark.parametrize("endpoint", ["list", "review", "restore"])
def test_context_credential_cannot_list_review_or_restore(
    admin, consumer, archived, endpoint
):
    reviewed = review(admin, archived)
    if endpoint == "list":
        response = consumer.get(BASE)
    elif endpoint == "review":
        response = consumer.get(f"{BASE}{archived.pk}/restore-review/")
    else:
        response = restore(consumer, archived, restore_body(reviewed))
    assert response.status_code == 403, response.data
    archived.refresh_from_db()
    assert archived.deleted
    assert not HubSkillContextOperation.objects.filter(operation="unarchive").exists()


def test_workspace_admin_can_restore(admin, archived, user, organization, workspace):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role="workspace_admin", level=Level.WORKSPACE_ADMIN)
    assert result(admin.get(BASE))["count"] == 1
    reviewed = review(admin, archived)
    assert result(restore(admin, archived, restore_body(reviewed)))["archived"] is False


def test_review_restore_and_replay_keep_versions_labels_and_history(admin, archived):
    versions = list(archived.versions.order_by("number").values())
    labels = list(archived.labels.order_by("name").values())
    history = list(
        HubSkillLabelEvent.objects.filter(skill=archived).order_by("pk").values()
    )
    production = archived.labels.select_related("version").get(name="production")
    reviewed = review(admin, archived)
    assert set(reviewed["resource"]) == SUMMARY_FIELDS
    assert reviewed["name_collision"] is False
    assert reviewed["retained_labels"] == [
        {
            "name": "production",
            "version": production.version.number,
            "version_id": str(production.version_id),
            "digest": production.version.digest,
            "is_protected": True,
        }
    ]
    assert reviewed["review_fingerprint"] == canonical_digest(
        {
            "resource": reviewed["resource"],
            "retained_labels": reviewed["retained_labels"],
        }
    )
    key = uuid4()
    body = restore_body(reviewed)
    response = restore(admin, archived, body, key)
    restored = result(response)
    assert set(restored) == SUMMARY_FIELDS
    assert restored == {
        **reviewed["resource"],
        "archived": False,
        "release_revision": archived.release_revision + 1,
    }
    archived.refresh_from_db()
    assert archived.deleted is False
    assert archived.deleted_at is None
    assert archived.release_revision == restored["release_revision"]
    assert list(archived.versions.order_by("number").values()) == versions
    assert list(archived.labels.order_by("name").values()) == labels
    assert list(
        HubSkillLabelEvent.objects.filter(skill=archived).order_by("pk").values()
    ) == history
    retained = archived.labels.select_related("version").get(name="production")
    assert retained.version_id == production.version_id
    assert retained.version.digest == production.version.digest
    operation = HubSkillContextOperation.objects.get(
        operation="unarchive", idempotency_key=key
    )
    assert operation.response["note"] == ""
    assert operation.response["retained_labels"] == reviewed["retained_labels"]
    assert result(admin.get(BASE))["count"] == 0
    replay = restore(admin, archived, body, key)
    assert replay.status_code == response.status_code
    assert replay.data == response.data
    assert_conflict(
        restore(admin, archived, {**body, "note": "Different recovery"}, key),
        "idempotency_conflict",
    )
    archived.refresh_from_db()
    assert archived.release_revision == restored["release_revision"]
    assert HubSkillContextOperation.objects.filter(operation="unarchive").count() == 1


@pytest.mark.parametrize(
    "change,code",
    [("fingerprint", "impact_changed"), ("revision", "release_conflict")],
)
def test_stale_review_fails_without_restoring(admin, archived, change, code):
    reviewed = review(admin, archived)
    body = restore_body(reviewed)
    if change == "fingerprint":
        # Retained consequences changed while the release revision stayed fixed.
        archived.labels.filter(name="production").update(
            version=archived.versions.get(number=2)
        )
    else:
        HubSkill._base_manager.filter(pk=archived.pk).update(
            release_revision=archived.release_revision + 1
        )
    assert_conflict(restore(admin, archived, body), code)
    archived.refresh_from_db()
    assert archived.deleted
    assert archived.deleted_at is not None
    assert not HubSkillContextOperation.objects.filter(operation="unarchive").exists()


def test_changed_then_restored_safety_policy_invalidates_review(
    admin, archived, principal
):
    reviewed = review(admin, archived)
    version = archived.versions.get(number=1)
    for revision, active in enumerate([True, False]):
        set_quarantine(
            principal=principal,
            version_id=version.pk,
            revision=revision,
            active=active,
            reason="Review a policy change even when its state is restored",
            replacement_version_id=None,
        )
    current = review(admin, archived)
    assert current["retained_labels"] == reviewed["retained_labels"]
    assert (
        current["resource"]["release_revision"]
        == reviewed["resource"]["release_revision"]
    )
    assert (
        current["resource"]["security_revision"]
        == reviewed["resource"]["security_revision"] + 2
    )
    assert current["review_fingerprint"] != reviewed["review_fingerprint"]
    assert_conflict(restore(admin, archived, restore_body(reviewed)), "impact_changed")
    archived.refresh_from_db()
    assert archived.deleted


def test_live_namesake_reports_collision_and_cannot_be_overwritten(admin, archived):
    reviewed = review(admin, archived)
    duplicate = HubSkill._base_manager.create(
        organization_id=archived.organization_id,
        workspace_id=archived.workspace_id,
        name=archived.name,
        kind="skill",
    )
    current = review(admin, archived)
    assert current["name_collision"] is True
    assert current["review_fingerprint"] == reviewed["review_fingerprint"]
    assert_conflict(restore(admin, archived, restore_body(reviewed)), "name_taken")
    archived.refresh_from_db()
    duplicate.refresh_from_db()
    assert archived.deleted
    assert not duplicate.deleted
    assert duplicate.latest_version_number == 0


def test_foreign_workspace_is_not_listed_and_returns_not_found(
    admin, organization, hub_other_workspace
):
    foreign = HubSkill._base_manager.create(
        organization=organization,
        workspace=hub_other_workspace,
        kind="skill",
        name="foreign-archived",
        deleted=True,
        deleted_at=timezone.now(),
    )
    assert result(admin.get(BASE))["results"] == []
    response = admin.get(f"{BASE}{foreign.pk}/restore-review/")
    assert response.status_code == 404, response.data
    response = restore(
        admin,
        foreign,
        {"expected_release_revision": 0, "expected_review_fingerprint": "unknown"},
    )
    assert response.status_code == 404, response.data
    foreign.refresh_from_db()
    assert foreign.deleted


def test_archived_context_is_not_a_skill_restore_target(admin, context):
    HubSkill._base_manager.filter(pk=context.pk).update(deleted=True)
    assert result(admin.get(BASE))["results"] == []
    response = admin.get(f"{BASE}{context.pk}/restore-review/")
    assert response.status_code == 404, response.data
    response = restore(
        admin,
        context,
        {"expected_release_revision": 0, "expected_review_fingerprint": "unknown"},
    )
    assert response.status_code == 404, response.data


def test_restore_does_not_clear_quarantine(admin, archived, principal):
    version = archived.versions.get(number=1)
    set_quarantine(
        principal=principal,
        version_id=version.pk,
        revision=0,
        active=True,
        reason="Retained policy requires investigation",
        replacement_version_id=None,
    )
    reviewed = review(admin, archived)
    result(restore(admin, archived, restore_body(reviewed)))
    assert get_version_safety(principal=principal, version_id=version.pk)["quarantined"]


@pytest.mark.parametrize(
    "patch,status",
    [
        ({"expected_release_revision": -1}, 400),
        ({"expected_release_revision": True}, 400),
        ({"expected_release_revision": 1.5}, 400),
        ({"expected_review_fingerprint": None}, 400),
        ({"note": "x" * 2001}, 413),
    ],
)
def test_restore_input_validation(admin, archived, patch, status):
    body = {**restore_body(review(admin, archived)), **patch}
    response = restore(admin, archived, body)
    assert response.status_code == status, response.data
    archived.refresh_from_db()
    assert archived.deleted


@pytest.mark.parametrize("key,status", [(None, 428), ("not-a-uuid", 400)])
def test_restore_requires_valid_idempotency_header(admin, archived, key, status):
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    response = admin.post(
        f"{BASE}{archived.pk}/restore/",
        restore_body(review(admin, archived)),
        format="json",
        **headers,
    )
    assert response.status_code == status, response.data
    archived.refresh_from_db()
    assert archived.deleted
