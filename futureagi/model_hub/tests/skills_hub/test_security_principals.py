"""Real JWT/user/MCP/system-key regressions for the private draft boundary."""

import json
from uuid import uuid4

import pytest
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import OrgApiKey
from accounts.models.auth_token import AuthToken
from accounts.models.user import User
from model_hub.models.skills_hub import HubSkill, HubSkillDraft, HubSkillLabelEvent
from model_hub.services.skills_hub import drafts
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.principals import private_user

pytestmark = pytest.mark.django_db
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/"
PRIVATE_TEXT = "Private unpublished instructions, not organization-key content."


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture
def make_client(user, organization, workspace):
    def make(kind):
        client = APIClient()
        if kind == "jwt":
            token = AuthToken.objects.create(
                user=user, auth_type="access", last_used_at=timezone.now()
            )
            value = generate_encrypted_message(
                {"user_id": str(user.pk), "id": str(token.pk)}
            )
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {value}",
                HTTP_X_WORKSPACE_ID=str(workspace.pk),
            )
        else:
            key = OrgApiKey.objects.create(
                name="Synthetic private-boundary test",
                api_key=f"test-{uuid4()}",
                secret_key=f"test-{uuid4()}",
                type=kind,
                user=None if kind == "system" else user,
                organization=organization,
                workspace=workspace,
            )
            client.credentials(
                HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key
            )
        return client

    return make


@pytest.fixture
def private_draft(hub_skill, user, publish_skill, make_skill_files):
    publish_skill(hub_skill, labels=["production"])
    draft = hub_skill.drafts.get(user=user)
    return drafts.save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(body=PRIVATE_TEXT),
        note="Private author note",
        base_version=1,
        revision=draft.revision,
    )


def result(response, status=200):
    assert response.status_code == status, response.data
    return response.data["result"]


@pytest.mark.parametrize(
    "method,suffix,query",
    [
        ("get", "draft/", {}),
        ("head", "draft/", {}),
        ("put", "draft/", {}),
        ("delete", "draft/", {}),
        ("post", "draft/publish/", {}),
        ("get", "compare/", {"from": 1, "to": "draft"}),
        ("get", "compare/", {"from": "draft", "to": 1}),
        ("get", "compare/", {"from": "draft", "to": "draft"}),
    ],
)
def test_system_key_cannot_read_or_change_synthetic_users_draft(
    make_client, private_draft, user, organization, method, suffix, query
):
    # Assert the fixture exercises the actual authentication fallback, not a user
    # whose draft the system key would never have reached in the vulnerable code.
    assert (
        User.objects.filter(organization=organization).order_by("created_at").first()
        == user
    )
    skill = private_draft.skill
    original = HubSkillDraft.all_objects.filter(pk=private_draft.pk).values().get()
    versions_before = list(skill.versions.values())
    labels_before = list(skill.labels.values())
    events_before = list(HubSkillLabelEvent.objects.filter(skill=skill).values())
    payload = query
    if method == "put":
        payload = {
            "files": private_draft.files,
            "base_version": 1,
            "revision": private_draft.revision,
        }
    elif method == "post":
        payload = {
            "revision": private_draft.revision,
            "acknowledge_warnings": True,
            "labels": ["production"],
        }
    response = getattr(make_client("system"), method)(
        BASE + str(skill.pk) + "/" + suffix,
        payload,
        **({} if method in {"get", "head"} else {"format": "json"}),
    )
    assert response.status_code == 403, response.data
    assert response.data["code"] == "principal_not_supported"
    assert PRIVATE_TEXT not in json.dumps(response.data)
    assert (
        HubSkillDraft.all_objects.filter(pk=private_draft.pk).values().get() == original
    )
    assert list(skill.versions.values()) == versions_before
    assert list(skill.labels.values()) == labels_before
    assert (
        list(HubSkillLabelEvent.objects.filter(skill=skill).values()) == events_before
    )


def test_system_key_cannot_seed_a_users_private_draft(make_client):
    count = HubSkill.objects.count()
    response = make_client("system").post(
        BASE,
        {
            "name": "system-created",
            "description": "Use when testing private creation boundaries.",
        },
        format="json",
    )
    assert response.status_code == 403
    assert response.data["code"] == "principal_not_supported"
    assert HubSkill.objects.count() == count
    assert not HubSkillDraft.objects.exists()


def test_system_keys_keep_public_consumption_and_push_without_private_identity(
    make_client, private_draft, make_skill_files
):
    client = make_client("system")
    skill = private_draft.skill
    url = BASE + str(skill.pk) + "/"
    library = result(client.get(BASE))
    assert library["stats"]["my_drafts"] == 0
    assert not library["results"][0]["has_draft"]
    detail = result(client.get(url))
    assert not detail["has_draft"]
    assert detail["drafts"] and not any(row["is_mine"] for row in detail["drafts"])
    assert (
        result(client.get(url + "compare/", {"from": 1, "to": 1}))["summary"][
            "unchanged"
        ]
        == 1
    )
    manifest = result(
        client.get(BASE + "resolve/", {"name": skill.name, "include_content": "true"})
    )
    assert PRIVATE_TEXT not in json.dumps(manifest)
    assert client.get(url + "versions/1/download/").status_code == 200
    pushed = result(
        client.post(
            BASE + "publish/",
            {"files": make_skill_files("system-published")},
            format="json",
        ),
        201,
    )
    assert pushed["version"]["number"] == 1
    assert not HubSkillDraft.objects.filter(skill_id=pushed["skill"]["id"]).exists()
    private_draft.refresh_from_db()
    assert PRIVATE_TEXT in private_draft.files[0]["content"]


@pytest.mark.parametrize("kind", ["jwt", "user", "mcp"])
def test_user_bound_principals_keep_private_authoring(
    make_client, private_draft, kind, make_skill_files, user
):
    client = make_client(kind)
    url = BASE + str(private_draft.skill_id) + "/"
    draft = result(client.get(url + "draft/"))
    assert draft["files"] == private_draft.files and draft["note"] == private_draft.note
    assert result(client.get(url))["has_draft"]
    for refs in ({"from": 1, "to": "draft"}, {"from": "draft", "to": 1}):
        comparison = result(client.get(url + "compare/", refs))
        assert PRIVATE_TEXT in json.dumps(comparison)
    saved = result(
        client.put(
            url + "draft/",
            {
                "files": make_skill_files(body=PRIVATE_TEXT + " Updated."),
                "base_version": 1,
                "revision": draft["revision"],
            },
            format="json",
        )
    )
    published = result(
        client.post(
            url + "draft/publish/",
            {
                "revision": saved["revision"],
                "acknowledge_warnings": True,
            },
            format="json",
        ),
        201,
    )
    assert published["version"]["number"] == 2
    assert not HubSkillDraft.objects.filter(
        skill_id=private_draft.skill_id, user=user
    ).exists()
    result(
        client.put(
            url + "draft/",
            {
                "files": make_skill_files(),
                "base_version": 2,
                "revision": 0,
            },
            format="json",
        )
    )
    assert client.delete(url + "draft/", {}, format="json").status_code == 204
    assert not HubSkillDraft.objects.filter(
        skill_id=private_draft.skill_id, user=user
    ).exists()


@pytest.mark.parametrize(
    "kind,owner",
    [("system", "self"), ("unknown", "self"), ("user", "other"), ("mcp", "none")],
)
def test_private_principal_fails_closed_for_forced_auth_callers(user, kind, owner):
    key = OrgApiKey(
        type=kind, user_id={"self": user.pk, "other": uuid4(), "none": None}[owner]
    )
    assert private_user(user=user, api_key=key) is None
    with pytest.raises(SkillsHubError) as error:
        private_user(user=user, api_key=key, required=True)
    assert error.value.code == "principal_not_supported"
