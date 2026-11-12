"""An explicit tenant is a destination, never a hint for another tenant."""

import json
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication, generate_encrypted_message
from accounts.models import Organization, OrgApiKey, User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.api_errors import first_error_code

pytestmark = pytest.mark.django_db
MINT = "/accounts/key/generate_secret_key/"
CODE = "organization_unavailable"
MESSAGE = "The selected organization is unavailable. Select an accessible organization and retry."


@pytest.fixture
def actor():
    clear_workspace_context()
    orgs = [Organization.objects.create(name=f"Selector {label}") for label in "ABC"]
    user = User.objects.create(
        email=f"selector-{uuid4().hex}@example.invalid",
        name="Synthetic selector actor",
        organization=orgs[0],
        organization_role=OrganizationRoles.OWNER,
        config={"currentOrganizationId": str(orgs[1].pk)},
    )
    for org in orgs[:2]:
        OrganizationMembership.no_workspace_objects.create(
            user=user, organization=org, role=OrganizationRoles.OWNER,
            level=Level.OWNER, is_active=True,
        )
    token = AuthToken.objects.create(
        user=user, auth_type=AuthTokenType.ACCESS.value, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    yield SimpleNamespace(user=user, a=orgs[0], b=orgs[1], c=orgs[2], bearer=bearer)
    clear_workspace_context()


def target(actor, kind):
    if kind == "missing":
        return str(uuid4())
    if kind == "malformed":
        return "not-an-organization"
    if kind == "empty":
        return ""
    if kind == "whitespace":
        return " "
    if kind in ("inactive", "deleted"):
        OrganizationMembership.no_workspace_objects.create(
            user=actor.user, organization=actor.c, role=OrganizationRoles.OWNER,
            level=Level.OWNER, is_active=kind != "inactive", deleted=kind == "deleted",
        )
    return str(actor.c.pk)


def mint(actor, *, header=None, query=None):
    clear_workspace_context()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {actor.bearer}"}
    if header is not None:
        headers["HTTP_X_ORGANIZATION_ID"] = header
    path = MINT + ("?" + urlencode({"organization_id": query}) if query is not None else "")
    request = APIRequestFactory().post(
        path, {"key_name": "Synthetic destination key", "workspace_id": None},
        format="json", **headers,
    )
    match = resolve(MINT)
    request.resolver_match = match
    return match.func(request, *match.args, **match.kwargs)


def keys():
    # Unscoped readback covers every organization, including a redirected write.
    return list(OrgApiKey._base_manager.order_by("pk").values_list("pk", "organization_id"))


def assert_denied(response, actor, before):
    assert response.status_code == 403, response.data
    assert first_error_code(response.data) == CODE
    body = json.dumps(response.data)
    assert MESSAGE in body
    for org in (actor.a, actor.b, actor.c):
        assert str(org.pk) not in body
    assert keys() == before


@pytest.mark.parametrize("selector", ["header", "query"])
@pytest.mark.parametrize("kind", ["unjoined", "inactive", "deleted", "missing", "malformed", "empty", "whitespace"])
def test_invalid_explicit_tenant_cannot_mint_in_any_org(actor, selector, kind):
    selected = target(actor, kind)
    before = keys()
    response = mint(actor, **{selector: selected})
    assert_denied(response, actor, before)


@pytest.mark.parametrize("selector", ["header", "query", "both"])
def test_accessible_explicit_tenant_is_exact_key_destination(actor, selector):
    kwargs = {name: str(actor.a.pk) for name in ("header", "query") if selector in (name, "both")}
    response = mint(actor, **kwargs)
    assert response.status_code == 200, response.data
    row = OrgApiKey._base_manager.get(pk=response.data["result"]["key_id"])
    assert row.organization_id == actor.a.pk
    assert row.user_id == actor.user.pk
    assert len(keys()) == 1


@pytest.mark.parametrize("fallback", ["config", "membership"])
def test_absent_selector_preserves_fallback(actor, fallback):
    if fallback == "membership":
        User.objects.filter(pk=actor.user.pk).update(config={})
    expected = APIKeyAuthentication()._resolve_organization(
        SimpleNamespace(headers={}, GET={}), actor.user
    )
    if fallback == "config":
        assert expected.pk == actor.b.pk
    response = mint(actor)
    assert response.status_code == 200, response.data
    row = OrgApiKey._base_manager.get(pk=response.data["result"]["key_id"])
    assert row.organization_id == expected.pk
    assert len(keys()) == 1


@pytest.mark.parametrize("query_kind", ["unjoined", "malformed", "empty", "other-accessible"])
def test_second_explicit_selector_cannot_be_ignored(actor, query_kind):
    query = str(actor.b.pk) if query_kind == "other-accessible" else target(actor, query_kind)
    before = keys()
    assert_denied(mint(actor, header=str(actor.a.pk), query=query), actor, before)


@pytest.mark.parametrize("selector", ["header", "query"])
@pytest.mark.parametrize("kind", ["unjoined", "missing", "malformed", "empty", "other-accessible"])
def test_personal_key_never_ignores_or_rebinds_explicit_tenant(actor, selector, kind):
    from rest_framework.exceptions import PermissionDenied

    key = OrgApiKey.no_workspace_objects.create(
        user=actor.user, organization=actor.a, type="user", name="Synthetic pinned key"
    )
    selected = str(actor.b.pk) if kind == "other-accessible" else target(actor, kind)
    request = SimpleNamespace(
        headers={"X-Organization-Id": selected} if selector == "header" else {},
        GET={"organization_id": selected} if selector == "query" else {},
        org_api_key=key,
    )
    before = keys()
    with pytest.raises(PermissionDenied) as caught:
        APIKeyAuthentication()._resolve_organization(request, actor.user)
    assert caught.value.status_code == 403
    assert caught.value.get_codes() == CODE
    assert str(caught.value.detail) == MESSAGE
    assert keys() == before


@pytest.mark.parametrize("selector", ["absent", "header", "query"])
def test_personal_key_matching_or_absent_selector_keeps_its_org(actor, selector):
    key = OrgApiKey.no_workspace_objects.create(
        user=actor.user, organization=actor.a, type="user", name="Synthetic pinned control"
    )
    request = SimpleNamespace(
        headers={"X-Organization-Id": str(actor.a.pk)} if selector == "header" else {},
        GET={"organization_id": str(actor.a.pk)} if selector == "query" else {},
        org_api_key=key,
    )
    assert APIKeyAuthentication()._resolve_organization(request, actor.user).pk == actor.a.pk
