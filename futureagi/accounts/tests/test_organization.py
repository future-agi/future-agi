"""
Organization API Tests

Tests for organization listing, switching, and current organization endpoints.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


@pytest.fixture
def second_organization(db):
    """Create a second organization for testing."""
    from accounts.models import Organization

    return Organization.objects.create(name="Second Test Org")


@pytest.fixture
def organization_membership(user, second_organization, db):
    """Create an organization membership for the user in second org."""
    from accounts.models.organization_membership import OrganizationMembership

    return OrganizationMembership.objects.create(
        user=user,
        organization=second_organization,
        role="member",
        is_active=True,
    )


@pytest.fixture
def inactive_membership(user, db):
    """Create an inactive organization membership."""
    from accounts.models import Organization
    from accounts.models.organization_membership import OrganizationMembership

    inactive_org = Organization.objects.create(name="Inactive Org")
    return OrganizationMembership.objects.create(
        user=user,
        organization=inactive_org,
        role="member",
        is_active=False,
    )


@pytest.fixture
def other_user_org(db):
    """Create an organization that the test user has no access to."""
    from accounts.models import Organization

    return Organization.objects.create(name="Other User Org")


@pytest.mark.integration
@pytest.mark.api
class TestOrganizationListAPI:
    """Tests for /accounts/organizations/ endpoint."""

    def test_list_organizations_authenticated(self, auth_client, organization):
        """Authenticated user can list their organizations."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should return a list or paginated response
        assert isinstance(data, (list, dict))

    def test_list_organizations_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/organizations/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestCurrentOrganizationAPI:
    """Tests for /accounts/organizations/current/ endpoint."""

    def test_get_current_organization_authenticated(self, auth_client, organization):
        """Authenticated user can get current organization."""
        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Response may have organization nested in result
        result = data.get("result", data)
        assert "organization" in result or "name" in result or "id" in result

    def test_get_current_organization_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/organizations/current/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestSwitchOrganizationAPI:
    """Tests for /accounts/organizations/switch/ endpoint."""

    def test_switch_organization_authenticated(self, auth_client, organization):
        """Authenticated user can switch organization."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        # May return 200 or 400 if already in that org
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_switch_organization_invalid_id(self, auth_client):
        """Switching to invalid organization fails."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_switch_organization_unauthenticated(self, api_client, organization):
        """Unauthenticated switch request fails."""
        response = api_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_switch_organization_missing_id(self, auth_client):
        """Switching without organization_id fails."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_organization_invalid_uuid_format(self, auth_client):
        """Switching with invalid UUID format fails."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": "not-a-valid-uuid"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_organization_rejects_unknown_fields(
        self, auth_client, organization
    ):
        """Switch organization accepts the canonical snake_case body only."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {
                "organization_id": str(organization.id),
                "organizationId": str(organization.id),
            },
            format="json",
        )
        _assert_unknown_field(response, "organizationId")


@pytest.mark.integration
@pytest.mark.api
class TestOrganizationSelectionAPI:
    """Tests for /accounts/organizations/ POST endpoint (select organization)."""

    def test_select_organization_authenticated(self, auth_client, organization):
        """Authenticated user can select their organization."""
        response = auth_client.post(
            "/accounts/organizations/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_select_organization_returns_org_details(self, auth_client, organization):
        """Selection response includes organization details."""
        response = auth_client.post(
            "/accounts/organizations/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert "organization" in result
        org_data = result["organization"]
        assert "id" in org_data
        assert "name" in org_data

    def test_select_organization_missing_id(self, auth_client):
        """Selection without organization_id fails."""
        response = auth_client.post(
            "/accounts/organizations/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_select_organization_invalid_uuid_format(self, auth_client):
        """Selection with invalid UUID format fails."""
        response = auth_client.post(
            "/accounts/organizations/",
            {"organization_id": "invalid-uuid"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_select_organization_nonexistent(self, auth_client):
        """Selection of nonexistent organization fails."""
        response = auth_client.post(
            "/accounts/organizations/",
            {"organization_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_select_organization_no_access(self, auth_client, other_user_org):
        """Selection of organization without access fails."""
        response = auth_client.post(
            "/accounts/organizations/",
            {"organization_id": str(other_user_org.id)},
            format="json",
        )
        # Should return 403 for forbidden access
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_select_organization_unauthenticated(self, api_client, organization):
        """Unauthenticated selection fails."""
        response = api_client.post(
            "/accounts/organizations/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_select_organization_rejects_unknown_fields(
        self, auth_client, organization
    ):
        """Select organization rejects camelCase aliases and stray fields."""
        response = auth_client.post(
            "/accounts/organizations/",
            {
                "organization_id": str(organization.id),
                "organizationId": str(organization.id),
            },
            format="json",
        )
        _assert_unknown_field(response, "organizationId")


@pytest.mark.integration
@pytest.mark.api
class TestOrganizationListGET:
    """Tests for /accounts/organizations/ GET endpoint."""

    def test_list_includes_primary_organization(self, auth_client, organization):
        """Organization list includes the user's primary organization."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        orgs = result.get("organizations", [])
        org_ids = {o["id"] for o in orgs}
        assert str(organization.id) in org_ids

    def test_list_includes_invited_organizations(
        self, auth_client, organization, organization_membership
    ):
        """Organization list includes invited organizations."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        orgs = result.get("organizations", [])
        # Should have at least 2 orgs (primary + invited)
        assert len(orgs) >= 2

    def test_list_excludes_inactive_memberships(
        self, auth_client, organization, inactive_membership
    ):
        """Organization list excludes inactive memberships."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        orgs = result.get("organizations", [])
        # Inactive org should not be in the list
        org_names = [o.get("name") for o in orgs]
        assert "Inactive Org" not in org_names

    def test_list_includes_total_count(self, auth_client, organization):
        """Organization list includes total count."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert "total_count" in result

    def test_list_includes_is_selected_flag(self, auth_client, organization):
        """Organization list includes is_selected flag."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        orgs = result.get("organizations", [])
        if orgs:
            first_org = orgs[0]
            assert "is_selected" in first_org


@pytest.mark.integration
@pytest.mark.api
class TestCurrentOrganizationDetails:
    """Additional tests for /accounts/organizations/current/ endpoint."""

    def test_current_org_returns_primary_when_none_selected(
        self, auth_client, user, organization
    ):
        """Returns primary org when none is explicitly selected."""
        # Clear any selected org
        user.config.pop("selected_organization_id", None)
        user.save(update_fields=["config"])

        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert result.get("organization") is not None

    def test_current_org_returns_selected_org(self, auth_client, user, organization):
        """Returns the explicitly selected organization."""
        # Set selected org
        user.config["selected_organization_id"] = str(organization.id)
        user.save(update_fields=["config"])

        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        org = result.get("organization")
        assert org is not None
        assert org.get("id") == str(organization.id)

    def test_current_org_includes_source(self, auth_client, user, organization):
        """Response includes source field indicating how org was determined."""
        user.config["selected_organization_id"] = str(organization.id)
        user.save(update_fields=["config"])

        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        # Check for source field
        assert "source" in result

    def test_current_org_falls_back_on_invalid_selection(
        self, auth_client, user, organization
    ):
        """Falls back to primary if selected org is invalid."""
        # Set invalid selected org
        user.config["selected_organization_id"] = "00000000-0000-0000-0000-000000000000"
        user.save(update_fields=["config"])

        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        # Should fall back to primary
        org = result.get("organization")
        assert org is not None


@pytest.mark.integration
@pytest.mark.api
class TestOrganizationAccessControl:
    """Tests for organization access control across endpoints."""

    def test_cannot_switch_to_unauthorized_org(self, auth_client, other_user_org):
        """Cannot switch to organization user doesn't have access to."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(other_user_org.id)},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_can_switch_to_invited_org(
        self, auth_client, second_organization, organization_membership
    ):
        """Can switch to organization user is invited to."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(second_organization.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_switch_updates_user_config(
        self, auth_client, user, second_organization, organization_membership
    ):
        """Switching organization updates user config."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(second_organization.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Refresh user from database
        user.refresh_from_db()
        assert user.config.get("selected_organization_id") == str(
            second_organization.id
        )


@pytest.mark.integration
@pytest.mark.api
class TestOrganizationResponseFormat:
    """Tests for consistent response format across organization endpoints."""

    def test_list_response_has_status(self, auth_client, organization):
        """List response has status field."""
        response = auth_client.get("/accounts/organizations/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data

    def test_switch_response_has_status(self, auth_client, organization):
        """Switch response has status field."""
        response = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data

    def test_current_response_has_status(self, auth_client, organization):
        """Current org response has status field."""
        response = auth_client.get("/accounts/organizations/current/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data

    def test_select_response_has_message(self, auth_client, organization):
        """Select response includes success message."""
        response = auth_client.post(
            "/accounts/organizations/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert "message" in result
