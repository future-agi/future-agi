"""Tenant-isolation fixtures and protocol helpers for the SAML acceptance set."""

from __future__ import annotations

import base64
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from saml2_auth.models import SAMLMetadataModel
from saml2_auth.tests.saml_fixtures import (
    IdPKeyPair,
    build_idp_metadata,
    create_idp_keypair,
    signed_response,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.settings.settings import get_assertion_url, get_entity_id


@dataclass(frozen=True)
class SamlTenants:
    org_a: Organization
    org_b: Organization
    a1: User
    a1b: User
    a2: User
    ab: User
    b_only: User
    v: User
    workspace_a: Workspace
    workspace_b: Workspace


@dataclass(frozen=True)
class BoundAttempt:
    """The candidate-then-complete protocol state expected after the fix."""

    response: object
    relay: str
    request_id: str
    binder_cookie: str


def _new_user(email: str, organization: Organization) -> User:
    return User.objects.create_user(
        email=email,
        password="test-password-not-a-secret",
        name=email.split("@", maxsplit=1)[0],
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


def _membership(
    user: User, organization: Organization, *, role: str, level: int
) -> None:
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={"role": role, "level": level, "is_active": True},
    )


def _workspace(organization: Organization, owner: User, name: str) -> Workspace:
    workspace = Workspace.objects.create(
        name=name,
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=owner, organization=organization
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=owner,
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.OWNER,
        is_active=True,
    )
    return workspace


@pytest.fixture(scope="session")
def idp_keypairs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, IdPKeyPair]:
    directory = tmp_path_factory.mktemp("saml-idp-keys")
    return {
        "a": create_idp_keypair(directory, "idp-a"),
        "b": create_idp_keypair(directory, "idp-b"),
        "aws": create_idp_keypair(directory, "idp-aws"),
    }


@pytest.fixture(scope="session")
def idp_metadata(idp_keypairs: dict[str, IdPKeyPair]) -> dict[str, str]:
    return {
        "okta": build_idp_metadata(idp_keypairs["a"], "okta"),
        "google": build_idp_metadata(idp_keypairs["b"], "google"),
        "aws": build_idp_metadata(idp_keypairs["aws"], "aws"),
    }


@pytest.fixture(autouse=True)
def isolated_legacy_metadata_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep the pre-fix disk metadata side effect out of the worktree."""

    monkeypatch.setattr("saml2_auth.views.BASE_DIR", str(tmp_path))


@pytest.fixture
def saml_tenants(db) -> SamlTenants:
    org_a = Organization.objects.create(name="SAML Tenant A")
    org_b = Organization.objects.create(name="SAML Tenant B")
    a1 = _new_user("a1@saml-a.test", org_a)
    a1b = _new_user("a1b@saml-a.test", org_a)
    a2 = _new_user("a2@saml-a.test", org_a)
    ab = _new_user("ab@saml-a.test", org_a)
    b_only = _new_user("b-only@saml-b.test", org_b)
    viewer = _new_user("v@saml-a.test", org_a)

    _membership(a1, org_a, role=OrganizationRoles.OWNER, level=Level.OWNER)
    _membership(a1b, org_a, role=OrganizationRoles.ADMIN, level=Level.ADMIN)
    _membership(a2, org_a, role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    # Create B first so legacy first-membership resolution picks B for ``ab``.
    _membership(ab, org_b, role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    _membership(ab, org_a, role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    _membership(b_only, org_b, role=OrganizationRoles.MEMBER, level=Level.MEMBER)
    _membership(
        viewer, org_a, role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.VIEWER
    )

    workspace_a = _workspace(org_a, a1, "SAML A workspace")
    workspace_b = _workspace(org_b, b_only, "SAML B workspace")
    return SamlTenants(
        org_a=org_a,
        org_b=org_b,
        a1=a1,
        a1b=a1b,
        a2=a2,
        ab=ab,
        b_only=b_only,
        v=viewer,
        workspace_a=workspace_a,
        workspace_b=workspace_b,
    )


@pytest.fixture
def idp_a(saml_tenants: SamlTenants, idp_metadata: dict[str, str]) -> SAMLMetadataModel:
    return SAMLMetadataModel.no_workspace_objects.create(
        organization=saml_tenants.org_a,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
        relay_state=f"{saml_tenants.org_a.name}-relay-{uuid.uuid4().hex}",
        is_enabled=True,
        meta=idp_metadata["okta"],
    )


@pytest.fixture
def idp_b(saml_tenants: SamlTenants, idp_metadata: dict[str, str]) -> SAMLMetadataModel:
    return SAMLMetadataModel.no_workspace_objects.create(
        organization=saml_tenants.org_b,
        identity_type=SAMLMetadataModel.IDENTITY_GOOGLE,
        relay_state=f"{saml_tenants.org_b.name}-relay-{uuid.uuid4().hex}",
        is_enabled=True,
        meta=idp_metadata["google"],
    )


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def signed_a_response(idp_keypairs: dict[str, IdPKeyPair]):
    def _response(*, request_id: str, email: str, mutate=None) -> str:
        return signed_response(
            idp_keypairs["a"],
            request_id=request_id,
            recipient=get_assertion_url,
            destination=get_assertion_url,
            audience=get_entity_id,
            subject_email=email,
            mutate=mutate,
        )

    return _response


def bound_attempt(client: APIClient, *, email: str) -> BoundAttempt:
    """Initiate the new browser-bound protocol and return its retained request ID."""

    response = client.get("/saml2_auth/idp-login/", {"email": email})
    assert response.status_code == 200, response.content
    url = response.json()["result"]["url"]
    redirect_query = parse_qs(urlparse(url).query)
    relay_values = redirect_query.get("RelayState", [])
    request_values = redirect_query.get("SAMLRequest", [])
    assert len(relay_values) == 1, "initiation must use the attempt relay state"
    assert len(request_values) == 1, "initiation must emit one AuthnRequest"
    request_xml = zlib.decompress(base64.b64decode(request_values[0]), -zlib.MAX_WBITS)
    request_id = ElementTree.fromstring(request_xml).attrib["ID"]
    binder_names = [name for name in response.cookies if name.startswith("fai_saml_b_")]
    assert len(binder_names) == 1, (
        "new SAML initiation must set one browser binder cookie"
    )
    return BoundAttempt(
        response=response,
        relay=relay_values[0],
        request_id=request_id,
        binder_cookie=binder_names[0],
    )


def post_acs(client: APIClient, relay: str, payload: str) -> str:
    """Post to ACS and return the candidate key from its required 303 hop."""

    response = client.post(
        "/saml2_auth/acs/", {"RelayState": relay, "SAMLResponse": payload}
    )
    assert response.status_code == 303, response.content
    candidate_keys = parse_qs(urlparse(response["Location"]).query).get("c", [])
    assert len(candidate_keys) == 1, "ACS must redirect to exactly one candidate"
    return candidate_keys[0]


def complete(client: APIClient, candidate_key: str):
    """Complete the same-site GET and return the response with its cookie jar."""

    return client.get("/saml2_auth/complete/", {"c": candidate_key})


@pytest.fixture
def make_saml_token():
    """Create a current/future-compatible SAML credential for boundary tests."""

    def _make(
        user: User, organization: Organization, idp: SAMLMetadataModel | None = None
    ):
        fields = {field.name for field in AuthToken._meta.get_fields()}
        values: dict[str, object] = {
            "user": user,
            "auth_type": AuthTokenType.ACCESS.value,
            "last_used_at": timezone.now(),
            "is_active": True,
        }
        # These fields land in step 6. Their absence today must not turn this
        # RED test into an import/model failure.
        if "auth_origin" in fields:
            values["auth_origin"] = "saml"
        if "scoped_organization" in fields:
            values["scoped_organization"] = organization
        if "origin_idp" in fields:
            values["origin_idp"] = idp
        if "origin_idp_generation" in fields:
            values["origin_idp_generation"] = getattr(idp, "security_generation", 1)
        row = AuthToken.no_workspace_objects.create(**values)
        token = generate_encrypted_message({"user_id": str(user.id), "id": str(row.id)})
        cache.set(
            f"access_token_{row.id}", {"token": token, "user": user}, timeout=3600
        )
        return row, token

    return _make
