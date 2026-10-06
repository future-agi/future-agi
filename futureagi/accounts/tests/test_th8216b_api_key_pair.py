"""TH-8216 B1 D7 / R-S03: the API-key path needs X-Api-Key AND X-Secret-Key.

Real HTTP requests through ``APIKeyAuthentication`` (no force_authenticate,
no workspace injection) against an API-key protected Workbench endpoint.
The X-Workspace-Id cases record current behaviour only: the effective-context
mechanism is an open auth-owner decision.
"""

import pytest
from django.urls import get_resolver, reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace
from agent_playground.models.graph import Graph
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.fixture(autouse=True, scope="module")
def _import_views_without_workspace_context():
    """Load the URLconf (and every view module) before any workspace context.

    Views with class-level querysets capture the context active at import; the
    root ``user`` fixture sets one, so the first real request of this module
    must not be the one that imports them (it leaked into
    test_cross_org_isolation.py).
    """
    clear_workspace_context()
    _ = get_resolver().url_patterns


@pytest.fixture
def key_pair(db, organization, user):
    return OrgApiKey.no_workspace_objects.create(
        name="th8216b pair",
        organization=organization,
        user=user,
        type="user",
        enabled=True,
    )


@pytest.fixture
def other_key_pair(db, organization, user):
    return OrgApiKey.no_workspace_objects.create(
        name="th8216b other pair",
        organization=organization,
        user=user,
        type="user",
        enabled=True,
    )


@pytest.fixture
def own_graph(db, organization, workspace, user):
    return Graph.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="Own Graph",
        created_by=user,
    )


@pytest.fixture
def foreign_workspace(db):
    org = Organization.objects.create(name="Foreign Org")
    owner = User.objects.create_user(
        email="th8216b-foreign@futureagi.com",
        password="testpassword123",
        name="Foreign Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=owner, organization=org, defaults={"is_active": True}
    )
    workspace = Workspace.objects.create(
        name="Foreign Workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    Graph.no_workspace_objects.create(
        organization=org, workspace=workspace, name="Foreign Graph", created_by=owner
    )
    return workspace


def _get(headers):
    client = APIClient()
    return client.get(reverse("graph-list"), **headers)


def _graph_names(response):
    return sorted(row["name"] for row in response.json()["result"]["graphs"])


@pytest.mark.django_db
class TestApiKeyPairIsRequired:
    def test_both_keys_authenticate(self, key_pair, own_graph):
        response = _get(
            {
                "HTTP_X_API_KEY": key_pair.api_key,
                "HTTP_X_SECRET_KEY": key_pair.secret_key,
            }
        )

        assert response.status_code == status.HTTP_200_OK
        assert _graph_names(response) == ["Own Graph"]

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param(lambda k: {"HTTP_X_API_KEY": k.api_key}, id="api-key-only"),
            pytest.param(
                lambda k: {"HTTP_X_SECRET_KEY": k.secret_key}, id="secret-key-only"
            ),
            pytest.param(lambda k: {}, id="neither"),
        ],
    )
    def test_either_key_alone_is_unauthenticated(self, key_pair, own_graph, headers):
        response = _get(headers(key_pair))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response["WWW-Authenticate"] == "ApiKey"

    def test_mismatched_pair_is_rejected(self, key_pair, other_key_pair, own_graph):
        response = _get(
            {
                "HTTP_X_API_KEY": key_pair.api_key,
                "HTTP_X_SECRET_KEY": other_key_pair.secret_key,
            }
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid API key or secret key" in response.content.decode()

    def test_disabled_pair_is_rejected(self, key_pair, own_graph):
        key_pair.enabled = False
        key_pair.save()

        response = _get(
            {
                "HTTP_X_API_KEY": key_pair.api_key,
                "HTTP_X_SECRET_KEY": key_pair.secret_key,
            }
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestWorkspaceHeaderCurrentBehaviour:
    """Characterisation only (auth-owner decision): no behaviour is asserted as desired."""

    def test_foreign_workspace_header_is_ignored_not_rejected(
        self, key_pair, own_graph, foreign_workspace
    ):
        response = _get(
            {
                "HTTP_X_API_KEY": key_pair.api_key,
                "HTTP_X_SECRET_KEY": key_pair.secret_key,
                "HTTP_X_WORKSPACE_ID": str(foreign_workspace.id),
            }
        )

        # Silently falls back to the key owner's default workspace: no foreign
        # data, but also no explicit failure for the conflicting header.
        assert response.status_code == status.HTTP_200_OK
        assert _graph_names(response) == ["Own Graph"]

    def test_unknown_workspace_header_is_ignored_not_rejected(
        self, key_pair, own_graph
    ):
        response = _get(
            {
                "HTTP_X_API_KEY": key_pair.api_key,
                "HTTP_X_SECRET_KEY": key_pair.secret_key,
                "HTTP_X_WORKSPACE_ID": "00000000-0000-4000-8000-000000000000",
            }
        )

        assert response.status_code == status.HTTP_200_OK
        assert _graph_names(response) == ["Own Graph"]
