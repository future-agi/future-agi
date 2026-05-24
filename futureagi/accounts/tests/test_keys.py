"""
API Keys Tests

Tests for API key management endpoints.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


@pytest.fixture
def created_key(auth_client):
    """Create a test API key and return key_id."""
    response = auth_client.post(
        "/accounts/key/generate_secret_key/",
        {"key_name": "Fixture Test Key"},
        format="json",
    )
    return response.json().get("result", {}).get("key_id")


@pytest.fixture
def other_org_user(db):
    """Create a user in a different organization for IDOR tests."""
    from accounts.models import Organization, User

    other_org = Organization.objects.create(
        name="Other Test Org",
    )
    other_user = User.objects.create_user(
        email="otheruser@other-org.com",
        password="testpassword123",
        name="Other Org User",
        organization=other_org,
        organization_role="owner",
    )
    return other_user


@pytest.fixture
def other_org_client(other_org_user):
    """API client authenticated as user from different organization."""
    client = APIClient()
    client.force_authenticate(user=other_org_user)
    return client


@pytest.fixture
def member_user(user, db):
    """User with member role (not owner)."""
    from accounts.models.organization_membership import OrganizationMembership

    user.organization_role = "member"
    user.save(update_fields=["organization_role"])
    # Also update the OrganizationMembership (source of truth for role checks)
    OrganizationMembership.no_workspace_objects.filter(
        user=user, is_active=True
    ).update(role="Member")
    return user


@pytest.fixture
def member_client(member_user):
    """API client authenticated as member (not owner)."""
    client = APIClient()
    client.force_authenticate(user=member_user)
    return client


@pytest.mark.integration
@pytest.mark.api
class TestGetKeysAPI:
    """Tests for /accounts/keys/ endpoint (GetKeysView)."""

    def test_get_keys_authenticated(self, auth_client):
        """Authenticated user can get their API keys."""
        response = auth_client.get("/accounts/keys/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_keys_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/keys/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestSecretKeyCustomActionsAPI:
    """Tests for /accounts/key/ custom action endpoints.

    The SecretKeyAPIViewSet only exposes custom actions, not standard CRUD.
    Frontend uses these endpoints:
    - GET /accounts/key/get_secret_keys/
    - POST /accounts/key/generate_secret_key/
    - POST /accounts/key/enable_key/
    - POST /accounts/key/disable_key/
    - DELETE /accounts/key/delete_secret_key/
    """

    def test_get_secret_keys_authenticated(self, auth_client):
        """Authenticated user can list secret keys."""
        response = auth_client.get("/accounts/key/get_secret_keys/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_secret_keys_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/key/get_secret_keys/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_generate_secret_key_authenticated(self, auth_client):
        """Authenticated user can generate a secret key."""
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Test API Key"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_generate_secret_key_unauthenticated(self, api_client):
        """Unauthenticated key generation fails."""
        response = api_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Test API Key"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_generate_secret_key_rejects_unknown_request_fields(self, auth_client):
        """Key generation accepts only the documented snake_case body."""
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Test API Key", "keyName": "legacy camel alias"},
            format="json",
        )
        _assert_unknown_field(response, "keyName")

    def test_enable_key_invalid_id(self, auth_client):
        """Enabling nonexistent key fails."""
        response = auth_client.post(
            "/accounts/key/enable_key/",
            {"key_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_enable_key_rejects_unknown_request_fields(self, auth_client):
        """Enable key rejects camelCase aliases."""
        response = auth_client.post(
            "/accounts/key/enable_key/",
            {
                "key_id": "00000000-0000-0000-0000-000000000000",
                "keyId": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "keyId")

    def test_disable_key_invalid_id(self, auth_client):
        """Disabling nonexistent key fails."""
        response = auth_client.post(
            "/accounts/key/disable_key/",
            {"key_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_disable_key_rejects_unknown_request_fields(self, auth_client):
        """Disable key rejects camelCase aliases."""
        response = auth_client.post(
            "/accounts/key/disable_key/",
            {
                "key_id": "00000000-0000-0000-0000-000000000000",
                "keyId": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "keyId")

    def test_delete_key_invalid_id(self, auth_client):
        """Deleting nonexistent key fails."""
        response = auth_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_key_rejects_unknown_request_fields(self, auth_client):
        """Delete key rejects camelCase aliases."""
        response = auth_client.delete(
            "/accounts/key/delete_secret_key/",
            {
                "key_id": "00000000-0000-0000-0000-000000000000",
                "keyId": "legacy camel alias",
            },
            format="json",
        )
        _assert_unknown_field(response, "keyId")

    def test_full_key_lifecycle(self, auth_client):
        """Can create, disable, enable, and delete a secret key."""
        # 1. Generate key
        create_response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Lifecycle Test Key"},
            format="json",
        )
        assert create_response.status_code == status.HTTP_200_OK
        key_id = create_response.json().get("result", {}).get("key_id")
        assert key_id is not None

        # 2. Disable key
        disable_response = auth_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(key_id)},
            format="json",
        )
        assert disable_response.status_code == status.HTTP_200_OK

        # 3. Enable key
        enable_response = auth_client.post(
            "/accounts/key/enable_key/",
            {"key_id": str(key_id)},
            format="json",
        )
        assert enable_response.status_code == status.HTTP_200_OK

        # 4. Delete key
        delete_response = auth_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": str(key_id)},
            format="json",
        )
        assert delete_response.status_code == status.HTTP_200_OK

    def test_get_secret_keys_masks_key_material(self, auth_client):
        """Secret-key list must not return raw key material after creation."""
        create_response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Masked List Contract Key"},
            format="json",
        )
        assert create_response.status_code == status.HTTP_200_OK
        created = create_response.json().get("result", {})
        key_id = created.get("key_id")
        raw_api_key = created.get("api_key")
        raw_secret_key = created.get("secret_key")

        response = auth_client.get(
            "/accounts/key/get_secret_keys/?search=Masked%20List%20Contract%20Key"
        )
        assert response.status_code == status.HTTP_200_OK
        table = response.json().get("result", {}).get("table", [])
        row = next((item for item in table if str(item.get("id")) == str(key_id)), None)
        assert row is not None
        assert row["api_key"] != raw_api_key
        assert row["secret_key"] != raw_secret_key
        assert "*" in row["api_key"]
        assert "*" in row["secret_key"]


@pytest.mark.integration
@pytest.mark.api
class TestGetSecretKeysPagination:
    """Tests for pagination in get_secret_keys endpoint."""

    def test_get_secret_keys_returns_metadata(self, auth_client):
        """Response includes pagination metadata."""
        response = auth_client.get("/accounts/key/get_secret_keys/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "result" in data
        result = data["result"]
        assert "metadata" in result
        assert "table" in result
        metadata = result["metadata"]
        assert "total_rows" in metadata
        assert "total_pages" in metadata
        assert "page_number" in metadata
        assert "page_size" in metadata

    def test_get_secret_keys_custom_page_size(self, auth_client):
        """Can specify custom page size."""
        response = auth_client.get("/accounts/key/get_secret_keys/?page_size=5")
        assert response.status_code == status.HTTP_200_OK

    def test_get_secret_keys_page_navigation(self, auth_client):
        """Can navigate to specific page."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?page_number=0&page_size=10"
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestGetSecretKeysSearch:
    """Tests for search in get_secret_keys endpoint."""

    def test_search_by_key_name(self, auth_client, created_key):
        """Can search keys by name."""
        response = auth_client.get("/accounts/key/get_secret_keys/?search=Fixture")
        assert response.status_code == status.HTTP_200_OK

    def test_search_no_results(self, auth_client):
        """Search with no matches returns empty table."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?search=NonExistentKeyName12345"
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result.get("table") == [] or len(result.get("table", [])) == 0


@pytest.mark.integration
@pytest.mark.api
class TestGetSecretKeysSorting:
    """Tests for sorting in get_secret_keys endpoint."""

    def test_sort_by_key_name_asc(self, auth_client):
        """Can sort keys by name ascending."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?sort_field=keyName&sort_order=asc"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_sort_by_key_name_desc(self, auth_client):
        """Can sort keys by name descending."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?sort_field=keyName&sort_order=desc"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_sort_by_created_at(self, auth_client):
        """Can sort keys by creation date."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?sort_field=createdAt&sort_order=desc"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_sort_by_created_by(self, auth_client):
        """Can sort keys by creator name."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?sort_field=createdBy&sort_order=asc"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_invalid_sort_field_uses_default(self, auth_client):
        """Invalid sort field falls back to default (created_at)."""
        response = auth_client.get(
            "/accounts/key/get_secret_keys/?sort_field=invalidField&sort_order=desc"
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestGenerateSecretKeyValidation:
    """Tests for generate_secret_key validation."""

    def test_generate_key_missing_name(self, auth_client):
        """Generating key without name fails."""
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_generate_key_returns_masked_keys(self, auth_client):
        """Generated key response includes masked versions."""
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Masked Key Test"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert "masked_api_key" in result
        assert "masked_secret_key" in result

    def test_generate_key_duplicate_name(self, auth_client, created_key):
        """Cannot create key with duplicate name."""
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Fixture Test Key"},  # Same name as created_key fixture
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestEnableDisableKeyStates:
    """Tests for enable/disable key state transitions."""

    def test_enable_already_enabled_key(self, auth_client, created_key):
        """Enabling an already enabled key fails."""
        response = auth_client.post(
            "/accounts/key/enable_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        # Key is enabled by default, so this should fail
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_disable_already_disabled_key(self, auth_client, created_key):
        """Disabling an already disabled key fails."""
        # First disable the key
        auth_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        # Try to disable again
        response = auth_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_enable_key_missing_key_id(self, auth_client):
        """Enable key without key_id fails."""
        response = auth_client.post(
            "/accounts/key/enable_key/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_disable_key_missing_key_id(self, auth_client):
        """Disable key without key_id fails."""
        response = auth_client.post(
            "/accounts/key/disable_key/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestDeleteKeyPermissions:
    """Tests for delete key permissions (owner-only)."""

    def test_delete_key_as_member_fails(self, member_client, auth_client):
        """Member (non-owner) cannot delete keys."""
        # First create a key as owner
        create_response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Key To Delete By Member"},
            format="json",
        )
        key_id = create_response.json().get("result", {}).get("key_id")

        # Try to delete as member
        response = member_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": str(key_id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_key_missing_key_id(self, auth_client):
        """Delete key without key_id fails."""
        response = auth_client.delete(
            "/accounts/key/delete_secret_key/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestCrossOrganizationSecurity:
    """Tests for cross-organization security (IDOR prevention)."""

    def test_cannot_enable_other_org_key(
        self, other_org_client, auth_client, created_key
    ):
        """Cannot enable key from another organization."""
        response = other_org_client.post(
            "/accounts/key/enable_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        # Should fail - key doesn't exist for this org
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_disable_other_org_key(
        self, other_org_client, auth_client, created_key
    ):
        """Cannot disable key from another organization."""
        response = other_org_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        # Should fail - key doesn't exist for this org
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_delete_other_org_key(
        self, other_org_client, auth_client, created_key
    ):
        """Cannot delete key from another organization."""
        response = other_org_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        # Should fail - key doesn't exist for this org
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_secret_keys_only_returns_own_org_keys(
        self, other_org_client, auth_client, created_key
    ):
        """Get secret keys only returns keys from user's organization."""
        response = other_org_client.get("/accounts/key/get_secret_keys/")
        assert response.status_code == status.HTTP_200_OK
        # Should not contain the key from another org
        result = response.json().get("result", {})
        table = result.get("table", [])
        key_ids = [k.get("id") for k in table]
        assert str(created_key) not in [str(k) for k in key_ids]


@pytest.mark.integration
@pytest.mark.api
class TestGetKeysViewResponse:
    """Tests for GetKeysView response format."""

    def test_get_keys_response_format(self, auth_client):
        """Get keys returns expected response structure."""
        response = auth_client.get("/accounts/keys/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data
        assert data["status"] == "success"
        assert "data" in data

    def test_get_keys_creates_system_key_if_none(self, auth_client):
        """GetKeysView creates system key if none exists."""
        response = auth_client.get("/accounts/keys/")
        assert response.status_code == status.HTTP_200_OK
        # Should have created a system key
        data = response.json()
        assert data.get("data") is not None
