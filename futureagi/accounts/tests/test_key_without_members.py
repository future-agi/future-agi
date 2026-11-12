"""A historical key without an organization user is an authentication refusal."""

import pytest
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership

pytestmark = pytest.mark.django_db


def test_system_key_without_any_users_gets_a_generic_authentication_refusal():
    org = Organization.objects.create(name="no-key-principal")
    key = OrgApiKey.no_workspace_objects.create(organization=org, type="system")
    assert not User.objects.filter(organization=org).exists()
    assert not OrganizationMembership._base_manager.filter(organization=org).exists()
    response = APIClient().get(
        "/api/public/health",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )
    assert response.status_code == 401
    assert response.json()["code"] == "api_key_retired"
    assert response.json()["create_url"] == "/dashboard/keys"
    assert "inactive" not in str(response.json()).lower()
    assert "NoneType" not in str(response.json())
    assert str(org.pk) not in str(response.json())
    assert key.api_key not in str(response.json())
    assert key.secret_key not in str(response.json())

    invalid = APIClient().get(
        "/api/public/health",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY="incorrect",
    )
    unknown = APIClient().get(
        "/api/public/health",
        HTTP_X_API_KEY="unknown",
        HTTP_X_SECRET_KEY="incorrect",
    )
    assert invalid.status_code == unknown.status_code == 401
    assert invalid.json() == unknown.json()
    assert "create_url" not in invalid.json()
    assert not User.objects.filter(organization=org).exists()
    assert not OrganizationMembership._base_manager.filter(organization=org).exists()
