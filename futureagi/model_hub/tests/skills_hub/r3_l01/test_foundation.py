"""Real authentication, isolation and immutable continuation foundation regressions."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import OrgApiKey
from accounts.models.auth_token import AuthToken
from accounts.models.organization_membership import OrganizationMembership
from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_v12 import (
    HubSkillContextCredential,
    HubSkillContextDependency,
    HubSkillContextOperation,
    HubSkillContextQuarantine,
    HubSkillContextResolution,
)
from model_hub.serializers import skills_hub_r3 as r3
from model_hub.serializers import skills_hub_v12 as schema
from model_hub.services.skills_hub.drafts import _tombstone
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.snapshots import snapshot_version
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.credentials import (
    authenticate_credential,
    issue_credential,
    revoke_credential,
)
from model_hub.services.skills_hub.versions import persist_immutable_version_locked

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/"


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture
def principal(user, workspace, organization):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture(params=["jwt", "user", "mcp"])
def human(request, user, workspace, organization):
    client = APIClient()
    if request.param == "jwt":
        token = AuthToken.objects.create(
            user=user, auth_type="access", last_used_at=timezone.now()
        )
        value = generate_encrypted_message(
            {"user_id": str(user.pk), "id": str(token.pk)}
        )
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {value}", HTTP_X_WORKSPACE_ID=str(workspace.pk)
        )
    else:
        key = OrgApiKey.objects.create(
            name="Synthetic foundation actor",
            api_key=f"test-{uuid4()}",
            secret_key=f"test-{uuid4()}",
            type=request.param,
            user=user,
            organization=organization,
            workspace=workspace,
        )
        client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


@pytest.fixture
def published(hub_skill, publish_skill, make_skill_files):
    return publish_skill(
        hub_skill,
        files=make_skill_files(
            extra=[
                {
                    "path": f"references/{i:02}.md",
                    "content": "Review synthetic policy.\n",
                }
                for i in range(30)
            ]
        ),
        labels=["production"],
    ).version


@pytest.fixture
def issue(principal):
    def create(version, *, labels=None, version_ids=None):
        request = {
            "purpose": "consume",
            "grants": [
                {
                    "kind": version.skill.kind,
                    "resource_id": str(version.skill_id),
                    "labels": ["production"] if labels is None else labels,
                    "version_ids": [] if version_ids is None else version_ids,
                }
            ],
            "binding_ids": [],
            "expires_at": timezone.now() + timedelta(days=1),
        }
        return issue_credential(
            principal=principal, request=request, idempotency_key=uuid4()
        )

    return create


@pytest.fixture
def consumer(published, issue):
    issued = issue(published)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Context {issued['secret']}")
    return client, issued


def _resolved(client):
    response = client.get(BASE + "consumption/resolve/", {"name": "help-desk"})
    assert response.status_code == 200, response.data
    return response.data["result"]


def _manifest_url(result):
    return (
        BASE
        + f"consumption/{result['resource_id']}/versions/{result['version']}/manifest/"
    )


def test_issuance_replay_never_returns_or_stores_secret(human, published):
    operation = str(uuid4())
    body = {
        "purpose": "consume",
        "grants": [
            {
                "kind": "skill",
                "resource_id": str(published.skill_id),
                "labels": ["production"],
                "version_ids": [],
            }
        ],
        "binding_ids": [],
        "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
    }
    response = human.post(
        BASE + "contexts/credentials/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=operation,
    )
    assert response.status_code == 201, response.data
    first = response.data["result"]
    assert first["secret_available"] is True
    replay = human.post(
        BASE + "contexts/credentials/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=operation,
    )
    assert replay.status_code == 200
    assert replay.data["result"]["secret"] is None
    assert replay.data["result"]["id"] == first["id"]
    assert first["secret"] not in json.dumps(
        list(HubSkillContextOperation.objects.values()), default=str
    )
    assert first["secret"] not in json.dumps(
        list(HubSkillContextCredential.objects.values()), default=str
    )
    listing = human.get(BASE + "contexts/credentials/")
    assert listing.status_code == 200, listing.data
    assert "secret" not in json.dumps(listing.data)
    body["grants"][0]["labels"] = ["staging"]
    conflict = human.post(
        BASE + "contexts/credentials/",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=operation,
    )
    assert conflict.status_code == 409
    assert conflict.data["code"] == "idempotency_conflict"


def test_label_move_keeps_lease_snapshot_and_all_manifest_pages(
    consumer, published, publish_skill, hub_skill
):
    client, _ = consumer
    first = _resolved(client)
    assert first["version_id"] == str(published.pk)
    newer = publish_skill(hub_skill, labels=["production"]).version
    second = client.get(
        _manifest_url(first),
        {"file_offset": first["next_file_offset"]},
        HTTP_X_CONTEXT_RESOLUTION=first["resolution_id"],
    )
    assert second.status_code == 200, second.data
    result = second.data["result"]
    assert result["version_id"] == first["version_id"]
    assert len(first["files"] + result["files"]) == 31
    assert result["next_file_offset"] is None
    assert _resolved(client)["version_id"] == str(newer.pk)


@pytest.mark.parametrize(
    "change,status,code",
    [
        ("revoke", 403, "context_credential_revoked"),
        ("expire", 401, "context_credential_expired"),
        ("lease", 410, "resolution_expired"),
        ("quarantine", 422, "context_quarantined"),
    ],
)
def test_denial_precedes_304(consumer, published, change, status, code):
    client, issued = consumer
    result = _resolved(client)
    if change == "revoke":
        HubSkillContextCredential.objects.filter(pk=issued["id"]).update(
            revoked_at=timezone.now()
        )
    elif change == "expire":
        HubSkillContextCredential.objects.filter(pk=issued["id"]).update(
            expires_at=timezone.now()
        )
    elif change == "lease":
        HubSkillContextResolution.objects.filter(pk=result["resolution_id"]).update(
            expires_at=timezone.now()
        )
    else:
        HubSkillContextQuarantine.objects.create(
            organization=published.skill.organization,
            workspace=published.skill.workspace,
            version=published,
            revision=1,
            active=True,
            reason="Synthetic safety control",
        )
    response = client.get(
        _manifest_url(result),
        HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"],
        HTTP_IF_NONE_MATCH=f'"{result["digest"]}"',
    )
    assert response.status_code == status, response.data
    assert response.data["code"] == code
    assert "files" not in response.data


def test_lease_is_not_bearer_or_other_credential_authority(consumer, published, issue):
    client, _ = consumer
    result = _resolved(client)
    other = issue(published)
    client.credentials(HTTP_AUTHORIZATION=f"Context {other['secret']}")
    response = client.get(
        _manifest_url(result), HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"]
    )
    assert response.status_code == 404
    client.credentials()
    response = client.get(
        _manifest_url(result), HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"]
    )
    assert response.status_code == 401


def test_missing_lease_and_wrong_version_are_denied(
    consumer, published, publish_skill, hub_skill
):
    client, _ = consumer
    result = _resolved(client)
    assert client.get(_manifest_url(result)).status_code == 428
    second = publish_skill(hub_skill).version
    response = client.get(
        BASE + f"consumption/{hub_skill.pk}/versions/{second.number}/manifest/",
        HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"],
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "url", ["", "resolve/?name=help-desk", "testing-options/", "contexts/credentials/"]
)
def test_global_routes_reject_context_scheme(consumer, url):
    client, _ = consumer
    response = client.get(BASE + url)
    assert response.status_code == 401, response.data
    assert "files" not in response.data


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_X_API_KEY": "synthetic-broad-key"},
        {"HTTP_X_SECRET_KEY": "synthetic-secret"},
        {"HTTP_X_WORKSPACE_ID": str(uuid4())},
        {"HTTP_X_ORGANIZATION_ID": str(uuid4())},
    ],
)
def test_mixed_auth_and_conflicting_scope_fail_closed(consumer, headers):
    client, _ = consumer
    response = client.get(
        BASE + "consumption/resolve/", {"name": "help-desk"}, **headers
    )
    assert response.status_code in {401, 403}
    assert "files" not in response.data


def test_lease_cannot_read_ungranted_kind_or_history(
    consumer, published, publish_skill, hub_skill
):
    client, _ = consumer
    publish_skill(hub_skill)
    response = client.get(
        BASE + "consumption/resolve/",
        {"name": hub_skill.name, "version": published.number},
    )
    assert response.status_code == 404
    response = client.get(BASE + "contexts/resolve/", {"name": hub_skill.name})
    assert response.status_code == 404


def test_utf8_file_continuation_has_exact_identity(consumer, published):
    client, _ = consumer
    result = _resolved(client)
    url = BASE + f"consumption/{published.skill_id}/versions/1/files/"
    response = client.get(
        url,
        {"path": "SKILL.md", "max_bytes": 13},
        HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"],
    )
    assert response.status_code == 200, response.data
    first = response.data["result"]
    assert first["version_id"] == str(published.pk)
    assert first["digest"] == published.digest
    assert first["next_offset"] == len(first["text"])


def test_credential_admin_membership_rechecked(principal, published, issue):
    issued = issue(published)
    OrganizationMembership.no_workspace_objects.filter(
        user_id=principal.user_id, organization_id=principal.scope.organization_id
    ).update(is_active=False)
    with pytest.raises(SkillsHubError) as error:
        revoke_credential(principal=principal, credential_id=issued["id"])
    assert error.value.code == "access_revoked"


@pytest.mark.parametrize(
    "mutate", ["wildcard", "foreign", "wrong_kind", "empty", "purpose", "long_expiry"]
)
def test_invalid_credential_grants(principal, published, mutate):
    grant = {
        "kind": "skill",
        "resource_id": str(published.skill_id),
        "labels": ["production"],
        "version_ids": [],
    }
    request = {
        "purpose": "consume",
        "grants": [grant],
        "binding_ids": [],
        "expires_at": timezone.now() + timedelta(days=1),
    }
    if mutate == "wildcard":
        grant["labels"] = ["*"]
    if mutate == "foreign":
        grant["resource_id"] = str(uuid4())
    if mutate == "wrong_kind":
        grant["kind"] = "agent_context"
    if mutate == "empty":
        grant["labels"] = []
    if mutate == "purpose":
        request["purpose"] = "propose"
    if mutate == "long_expiry":
        request["expires_at"] = timezone.now() + timedelta(days=91)
    with pytest.raises(SkillsHubError) as error:
        issue_credential(principal=principal, request=request, idempotency_key=uuid4())
    assert error.value.code == "invalid_credential_scope"
    assert not HubSkillContextCredential.objects.exists()


def test_common_persistence_context_description_and_quarantine_closure(
    organization, workspace, hub_actor, published, issue
):
    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="support-agent",
        description="Latest mirror must not be used",
        kind="agent_context",
    )
    descriptor = {
        "schema": 1,
        "kind": "agent_context",
        "name": context.name,
        "description": "Retained purpose",
        "entrypoint": "AGENTS.md",
        "owned_paths": ["AGENTS.md"],
        "links": [],
    }
    files = [
        {"path": "AGENTS.md", "content": "Use reviewed support policy."},
        {
            "path": ".futureagi/context.json",
            "content": json.dumps(descriptor, sort_keys=True, separators=(",", ":")),
        },
    ]
    with transaction.atomic():
        context = HubSkill.no_workspace_objects.select_for_update().get(pk=context.pk)
        version, unchanged = persist_immutable_version_locked(
            skill=context,
            actor=hub_actor,
            files=files,
            note="",
            parent_number=None,
            origin_number=None,
            description="Retained purpose",
            frontmatter={},
            checks={"summary": {"errors": 0}},
            source={},
        )
        HubSkillContextDependency.objects.create(
            organization=organization,
            workspace=workspace,
            parent_version=version,
            child_skill=published.skill,
            child_version=published,
            mount="skills/help-desk/",
            requested_selector={"version": 1},
        )
    assert not unchanged
    assert version.digest == manifest_digest(files)
    assert snapshot_version(version)["entrypoint"] == "AGENTS.md"
    assert snapshot_version(version)["description"] == "Retained purpose"
    issued = issue(version, labels=[], version_ids=[str(version.pk)])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Context {issued['secret']}")
    published.skill.deleted = True
    published.skill.save(update_fields=["deleted"])
    response = client.get(
        BASE + "contexts/resolve/", {"name": context.name, "version": 1}
    )
    assert response.status_code == 200, response.data
    HubSkillContextQuarantine.objects.create(
        organization=organization,
        workspace=workspace,
        version=published,
        revision=1,
        active=True,
        reason="Synthetic child block",
    )
    response = client.get(
        BASE + "contexts/resolve/", {"name": context.name, "version": 1}
    )
    assert response.status_code == 422


def test_tombstone_erases_new_private_fields(hub_skill):
    draft = hub_skill.drafts.get()
    draft.context_spec = {"description": "private"}
    draft.resolved_lock = {"links": ["private"]}
    draft.expected_head_version = 7
    draft.head_review_fingerprint = "sha256:" + "a" * 64
    before = draft.revision
    with transaction.atomic():
        _tombstone(draft)
    retained = HubSkillDraft.all_objects.get(pk=draft.pk)
    assert retained.revision == before + 1
    assert (
        retained.files == []
        and retained.context_spec == {}
        and retained.resolved_lock == {}
    )
    assert (
        retained.expected_head_version is None
        and retained.head_review_fingerprint == ""
    )


@pytest.mark.parametrize(
    "attrs",
    [{}, {"label": "production", "version": 1}, {"version": True}, {"label": "*"}],
)
def test_selector_union_rejects_ambiguous_or_invalid(attrs):
    assert not schema.Selector(data=attrs).is_valid()


def test_evidence_old_and_new_fields_are_exclusive_even_empty():
    serializer = r3.EvidenceInput(data={"evidence_run_id": None, "evidence_refs": []})
    assert not serializer.is_valid()
    assert r3.EvidenceInput(data={"evidence_refs": []}).is_valid()


def test_context_credentials_do_not_become_human_principals(published, issue):
    issued = issue(published)
    principal = authenticate_credential(token=issued["secret"])
    assert not hasattr(principal, "user_id")
    assert not hasattr(principal, "api_key_id")
    assert principal.purpose == "consume"


@pytest.mark.parametrize(
    "suffix", ["", "versions/", "labels/", "draft/", "tests/", "test-runs/"]
)
def test_legacy_uuid_routes_do_not_disclose_contexts(
    human, organization, workspace, suffix
):
    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        kind="agent_context",
        name="private-context-identity",
        description="Context only",
    )
    response = human.get(BASE + f"{context.pk}/{suffix}")
    assert response.status_code == 404, response.data
    assert context.name not in json.dumps(response.data)
    listing = human.get(BASE)
    assert listing.status_code == 200
    assert listing.data["result"]["count"] == 0
    assert listing.data["result"]["stats"]["total"] == 0


def test_system_key_cannot_list_or_issue_credentials(user, organization, workspace):
    key = OrgApiKey.objects.create(
        name="Synthetic system actor",
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        type="system",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.get(BASE + "contexts/credentials/")
    assert response.status_code == 403
    assert response.data["code"] == "principal_not_supported"


def test_successful_use_is_observable_and_monotonic(consumer, human, published):
    from model_hub.services.skills_hub.v12.credentials import record_credential_use

    client, issued = consumer
    credential = HubSkillContextCredential.objects.get(pk=issued["id"])
    assert credential.last_used_at is None
    result = _resolved(client)
    credential.refresh_from_db()
    first = credential.last_used_at
    assert first is not None
    principal = authenticate_credential(token=issued["secret"])
    record_credential_use(principal=principal, observed_at=first - timedelta(seconds=1))
    credential.refresh_from_db()
    assert credential.last_used_at == first
    response = client.get(
        BASE + f"consumption/{published.skill_id}/versions/1/files/",
        {"path": "SKILL.md"},
        HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"],
    )
    assert response.status_code == 200
    credential.refresh_from_db()
    assert credential.last_used_at >= first
    listing = human.get(BASE + "contexts/credentials/").data["result"]["results"]
    assert listing[0]["last_used_at"] is not None
    last = credential.last_used_at
    response = client.get(
        BASE + f"consumption/{published.skill_id}/versions/1/files/",
        {"path": "not-in-snapshot.md"},
        HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"],
    )
    assert response.status_code == 404
    credential.refresh_from_db()
    assert credential.last_used_at == last
    client.credentials(HTTP_AUTHORIZATION=f"Context {issued['id']}.{'z' * 43}")
    assert (
        client.get(BASE + "consumption/resolve/", {"name": "help-desk"}).status_code
        == 401
    )
    credential.refresh_from_db()
    assert credential.last_used_at == last


def test_browser_preflight_allows_immutable_continuation(consumer, settings):
    settings.CORS_ALLOWED_ORIGINS = ["https://synthetic.example"]
    settings.MIDDLEWARE = [
        "corsheaders.middleware.CorsMiddleware",
        *settings.MIDDLEWARE,
    ]
    client, _ = consumer
    result = _resolved(client)
    url = _manifest_url(result)
    response = client.options(
        url,
        HTTP_ORIGIN="https://synthetic.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,x-context-resolution",
    )
    assert response.status_code == 200
    allowed = response["Access-Control-Allow-Headers"].lower()
    assert "authorization" in allowed and "x-context-resolution" in allowed
    response = client.get(
        url,
        HTTP_ORIGIN="https://synthetic.example",
        HTTP_X_CONTEXT_RESOLUTION=result["resolution_id"],
    )
    assert response.status_code == 200
    assert response.data["result"]["version_id"] == result["version_id"]


def test_system_key_cannot_compare_quarantined_text(
    published, publish_skill, hub_skill, make_skill_files, organization, workspace
):
    newer = publish_skill(
        hub_skill, files=make_skill_files(body="Sanitized replacement.")
    ).version
    key = OrgApiKey.objects.create(
        name="Synthetic compare reader",
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        type="system",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    url = BASE + f"{hub_skill.pk}/compare/"
    query = {"from": published.number, "to": newer.number}
    assert client.get(url, query).status_code == 200
    HubSkillContextQuarantine.objects.create(
        organization=organization,
        workspace=workspace,
        version=published,
        revision=1,
        active=True,
        reason="Synthetic quarantine",
    )
    response = client.get(url, query)
    assert response.status_code == 422
    assert response.data["code"] == "context_quarantined"
    assert "from_text" not in json.dumps(response.data)


def test_disabled_slice_fails_closed_but_revocation_still_works(
    consumer, principal, settings
):
    client, issued = consumer
    settings.SKILLS_CONTEXT_CONSUMPTION_ENABLED = False
    response = client.get(BASE + "consumption/resolve/", {"name": "help-desk"})
    assert response.status_code == 503
    assert response.data["code"] == "skills_unavailable"
    revoke_credential(principal=principal, credential_id=issued["id"])
    assert HubSkillContextCredential.objects.get(pk=issued["id"]).revoked_at is not None
