"""Personal API-key lifecycle and authentication contracts."""

import base64
import uuid
from unittest.mock import patch

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from accounts.models import Organization, OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.views.keys import GetKeysView, SecretKeyAPIViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    org = Organization.objects.create(name=f"personal-{uuid.uuid4().hex}")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid",
        organization=org,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=org, role="Member"
    )
    return user, org, membership


def key_for(actor, **kwargs):
    user, org, _ = actor
    return OrgApiKey.no_workspace_objects.create(
        user=user, organization=org, type=kwargs.pop("type", "user"), **kwargs
    )


def request_for(key, *, basic=False, secret=None):
    secret = key.secret_key if secret is None else secret
    if basic:
        value = base64.b64encode(f"{key.api_key}:{secret}".encode()).decode()
        return APIRequestFactory().get("/", HTTP_AUTHORIZATION=f"Basic {value}")
    return APIRequestFactory().get(
        "/", HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=secret
    )


def test_bootstrap_is_gone_and_does_not_mint(actor):
    user, org, _ = actor
    request = APIRequestFactory().get("/accounts/keys/")
    request.organization = org
    force_authenticate(request, user=user)
    response = GetKeysView.as_view()(request)
    assert response.status_code == 410
    assert response.data == {
        "code": "org_keys_retired",
        "create_url": "/dashboard/keys",
    }
    assert not OrgApiKey.no_workspace_objects.filter(organization=org).exists()


@pytest.mark.parametrize("basic", [False, True])
@pytest.mark.parametrize("kind", ["system", "mcp", "ingest"])
def test_retired_types_never_authenticate_even_with_person(actor, basic, kind):
    key = key_for(actor, type=kind)
    authenticator = LangfuseBasicAuthentication() if basic else APIKeyAuthentication()
    with pytest.raises(AuthenticationFailed) as caught:
        authenticator.authenticate(request_for(key, basic=basic))
    assert caught.value.get_codes() == "api_key_retired"
    assert getattr(caught.value, "create_url", None) == "/dashboard/keys"


@pytest.mark.parametrize("basic", [False, True])
def test_retired_error_requires_a_verifying_secret(actor, basic):
    key = key_for(actor, type="system", enabled=False)
    authenticator = LangfuseBasicAuthentication() if basic else APIKeyAuthentication()
    with pytest.raises(AuthenticationFailed) as caught:
        authenticator.authenticate(request_for(key, basic=basic, secret="incorrect"))
    assert caught.value.get_codes() != "api_key_retired"
    assert not hasattr(caught.value, "create_url")
    with pytest.raises(AuthenticationFailed) as caught:
        authenticator.authenticate(request_for(key, basic=basic))
    assert caught.value.get_codes() == "api_key_retired"


@pytest.mark.parametrize("change", ["removed", "inactive", "deleted"])
def test_personal_key_cannot_restore_membership(actor, change):
    user, org, membership = actor
    key = key_for(actor)
    if change == "removed":
        membership.delete()
    elif change == "inactive":
        membership.is_active = False
        membership.save(update_fields=["is_active"])
    else:
        membership.deleted = True
        membership.save(update_fields=["deleted"])
    with pytest.raises(AuthenticationFailed):
        APIKeyAuthentication().authenticate(request_for(key))
    assert not OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=org, is_active=True, deleted=False
    ).exists()
    key.refresh_from_db()
    assert not key.enabled


def test_deactivation_disables_personal_keys_and_publishes(
    actor, django_capture_on_commit_callbacks
):
    user, org, _ = actor
    key = key_for(actor)
    with patch("accounts.api_key_revocation.publish_key_revocation") as publish:
        with django_capture_on_commit_callbacks(execute=True):
            user.is_active = False
            user.save(update_fields=["is_active"])
    key.refresh_from_db()
    assert not key.enabled
    assert publish.call_count == 1


def test_interactive_browser_session_creates_only_its_own_personal_key(actor):
    from django.utils import timezone

    from accounts.authentication import generate_encrypted_message
    from accounts.models.auth_token import AuthToken, AuthTokenType

    user, org, _ = actor
    token = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS.value, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    request = APIRequestFactory().post(
        "/accounts/key/generate_secret_key/",
        {"key_name": "browser-created"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
    )
    response = SecretKeyAPIViewSet.as_view({"post": "generate_secret_key"})(request)
    assert response.status_code == 200
    key = OrgApiKey._base_manager.get(pk=response.data["result"]["key_id"])
    assert key.user_id == user.pk and key.organization_id == org.pk
    assert key.type == "user" and key.workspace_id is None
    assert key.secret_key == "" and key.secret_key_hash
    assert not key.secret_key_ciphertext
    assert response.data["result"]["secret_key"]
    assert response["Cache-Control"] == "no-store"


def test_live_role_downgrade_is_visible_on_next_key_request(actor):
    from accounts.api_key_policy import authenticate_api_key
    from tfc.constants.levels import Level

    user, org, membership = actor
    membership.role, membership.level = "Owner", Level.OWNER
    membership.save()
    key = key_for(actor)
    assert (
        authenticate_api_key(key.api_key, key.secret_key).user.organization_role
        == "Owner"
    )
    membership.role, membership.level = "Member", Level.MEMBER
    membership.save()
    assert (
        authenticate_api_key(key.api_key, key.secret_key).user.organization_role
        == "Member"
    )


def test_api_key_cannot_mint_personal_key(actor):
    user, org, _ = actor
    key = key_for(actor)
    request = APIRequestFactory().post(
        "/accounts/key/generate_secret_key/", {"key_name": "not-allowed"}, format="json"
    )
    request.org_api_key = key
    request.organization = org
    force_authenticate(request, user=user)
    response = SecretKeyAPIViewSet.as_view({"post": "generate_secret_key"})(request)
    assert response.status_code == 403
    assert OrgApiKey.no_workspace_objects.filter(organization=org).count() == 1


def test_member_lists_only_own_masked_keys(actor):
    user, org, _ = actor
    own = key_for(actor, name="own")
    other = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid", organization=org
    )
    foreign = OrgApiKey.no_workspace_objects.create(
        user=other, organization=org, type="user", name="someone-else"
    )
    request = APIRequestFactory().get("/accounts/key/get_secret_keys/")
    request.organization = org
    force_authenticate(request, user=user)
    response = SecretKeyAPIViewSet.as_view({"get": "get_secret_keys"})(request)
    assert response.status_code == 200
    rows = response.data["result"]["table"]
    assert [str(row["id"]) for row in rows] == [str(own.pk)]
    rendered = str(response.data)
    for secret in (own.api_key, own.secret_key, foreign.api_key, foreign.secret_key):
        assert secret not in rendered


def test_member_revokes_own_key(actor):
    user, org, _ = actor
    key = key_for(actor)
    request = APIRequestFactory().delete(
        "/accounts/key/delete_secret_key/", {"key_id": str(key.pk)}, format="json"
    )
    request.organization = org
    force_authenticate(request, user=user)
    with patch("accounts.views.keys._publish_key_revocation") as publish:
        response = SecretKeyAPIViewSet.as_view({"delete": "delete_secret_key"})(request)
    assert response.status_code == 200
    key.refresh_from_db()
    assert key.deleted and not key.enabled
    assert publish.call_count == 1
