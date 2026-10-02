"""
API Keys Tests

Tests for API key management endpoints.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

SECRET_KEYS_URL = "/accounts/key/get_secret_keys/"

# Names of the rows created by the ``seeded_keys`` fixture, in ascending
# alphabetical order. Assertions filter the response down to these so an
# unrelated system key in the same org cannot change the expected sequence.
SEEDED_NAMES = ["alpha-key", "mike-key", "zulu-key"]


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


def _rows(response):
    return response.json()["result"]["table"]


def _metadata(response):
    return response.json()["result"]["metadata"]


def _seeded_order(response):
    """Key names from the response, restricted to the seeded fixture rows."""
    return [row["key_name"] for row in _rows(response) if row["key_name"] in SEEDED_NAMES]


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
def seeded_keys(db, user, organization):
    """Three ``user``-type keys with deterministic names and created_at order.

    ``created_at`` is back-dated with an explicit UPDATE so ordering assertions
    do not depend on insert timing. Returned oldest-first, which is also
    ascending alphabetical, so name-sort and date-sort expectations differ.
    """
    from accounts.models.user import OrgApiKey

    base = timezone.now() - timedelta(days=3)
    keys = []
    for index, name in enumerate(SEEDED_NAMES):
        key = OrgApiKey.objects.create(
            name=name,
            api_key=f"api-{name}-{uuid.uuid4().hex[:8]}",
            secret_key=f"secret-{name}-{uuid.uuid4().hex[:8]}",
            organization=organization,
            type="user",
            user=user,
        )
        OrgApiKey.objects.filter(id=key.id).update(
            created_at=base + timedelta(days=index)
        )
        keys.append(key)
    return keys


@pytest.fixture
def other_org_user(db):
    """A full **owner** of a different organization, for IDOR tests.

    The role must be ``OrganizationRoles.OWNER`` ("Owner") and backed by an
    active OrganizationMembership. With a lowercase "owner" and no membership,
    ``delete_secret_key`` rejects on its owner-role gate *before* reaching the
    org-scoped query — so the cross-org tests would pass even with the tenant
    filter removed, proving nothing.
    """
    from accounts.models import Organization, User
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    other_org = Organization.objects.create(
        name="Other Test Org",
    )
    other_user = User.objects.create_user(
        email="otheruser@other-org.com",
        password="testpassword123",
        name="Other Org User",
        organization=other_org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=other_user,
        organization=other_org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
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

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/accounts/key/enable_key/"),
            ("post", "/accounts/key/disable_key/"),
            ("delete", "/accounts/key/delete_secret_key/"),
        ],
        ids=["enable", "disable", "delete"],
    )
    def test_mutating_key_routes_reject_anonymous(self, api_client, method, path):
        """Enable, disable and delete all reject an unauthenticated caller."""
        import uuid as _uuid

        response = getattr(api_client, method)(
            path, {"key_id": str(_uuid.uuid4())}, format="json"
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

    def test_get_secret_keys_returns_metadata(self, auth_client, seeded_keys):
        """Metadata reports the real row count and the defaults actually used."""
        response = auth_client.get(SECRET_KEYS_URL)

        assert response.status_code == status.HTTP_200_OK
        metadata = _metadata(response)
        assert metadata["total_rows"] == len(_rows(response))
        assert metadata["total_rows"] >= len(seeded_keys)
        assert metadata["page_number"] == 0
        assert metadata["page_size"] == 20
        assert metadata["total_pages"] == 1

    def test_get_secret_keys_custom_page_size(self, auth_client, seeded_keys):
        """page_size is honoured, not just accepted."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?page_size=2")

        assert response.status_code == status.HTTP_200_OK
        assert len(_rows(response)) == 2
        assert _metadata(response)["page_size"] == 2

    def test_get_secret_keys_page_navigation(self, auth_client, seeded_keys):
        """Consecutive pages return disjoint rows that together cover the set."""
        first = auth_client.get(f"{SECRET_KEYS_URL}?page_number=0&page_size=2")
        second = auth_client.get(f"{SECRET_KEYS_URL}?page_number=1&page_size=2")

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        first_ids = {row["id"] for row in _rows(first)}
        second_ids = {row["id"] for row in _rows(second)}
        assert first_ids and second_ids
        assert first_ids.isdisjoint(second_ids)
        assert {str(key.id) for key in seeded_keys} <= (first_ids | second_ids)

    def test_total_pages_uses_ceiling_not_floor(self, auth_client, seeded_keys):
        """3 rows at page_size=2 is 2 pages, not 1."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?page_size=2")

        metadata = _metadata(response)
        assert metadata["total_rows"] == 3
        assert metadata["total_pages"] == 2

    def test_current_page_index_is_honoured_like_page_number(
        self, auth_client, seeded_keys
    ):
        """The frontend sends current_page_index; the view accepts both spellings."""
        via_alias = auth_client.get(f"{SECRET_KEYS_URL}?current_page_index=1&page_size=2")
        via_legacy = auth_client.get(f"{SECRET_KEYS_URL}?page_number=1&page_size=2")

        assert via_alias.status_code == status.HTTP_200_OK
        assert _metadata(via_alias)["page_number"] == 1
        assert _rows(via_alias) == _rows(via_legacy)

    def test_empty_result_returns_zeroed_metadata(self, auth_client):
        """The short-circuit branch still reports the requested page settings."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?search=no-such-key-name-12345")

        assert response.status_code == status.HTTP_200_OK
        assert _rows(response) == []
        metadata = _metadata(response)
        assert metadata["total_rows"] == 0
        assert metadata["total_pages"] == 0

    @pytest.mark.parametrize(
        "query",
        ["page_size=abc", "current_page_index=abc", "page_number=abc"],
        ids=["bad-page-size", "bad-current-page-index", "bad-page-number"],
    )
    def test_non_integer_pagination_is_rejected(self, auth_client, query):
        """int() on a non-numeric param must surface as 400, not a 500."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?{query}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestGetSecretKeysSearch:
    """Tests for search in get_secret_keys endpoint."""

    def test_search_returns_only_matching_keys(self, auth_client, seeded_keys):
        """Search narrows the set — the match is present and the others are gone."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?search=alpha")

        assert response.status_code == status.HTTP_200_OK
        names = [row["key_name"] for row in _rows(response)]
        assert names == ["alpha-key"]
        assert _metadata(response)["total_rows"] == 1

    def test_search_is_case_insensitive(self, auth_client, seeded_keys):
        """The view filters with name__icontains."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?search=ALPHA")

        assert [row["key_name"] for row in _rows(response)] == ["alpha-key"]

    def test_search_matches_substring_not_just_prefix(self, auth_client, seeded_keys):
        response = auth_client.get(f"{SECRET_KEYS_URL}?search=-key")

        assert sorted(_seeded_order(response)) == SEEDED_NAMES

    def test_search_no_results(self, auth_client):
        """Search with no matches returns empty table."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?search=NonExistentKeyName12345")

        assert response.status_code == status.HTTP_200_OK
        assert _rows(response) == []


@pytest.mark.integration
@pytest.mark.api
class TestGetSecretKeysSorting:
    """Tests for sorting in get_secret_keys endpoint."""

    def test_sort_by_key_name_asc(self, auth_client, seeded_keys):
        """Ascending name order is actually applied."""
        response = auth_client.get(f"{SECRET_KEYS_URL}?sort_field=keyName&sort_order=asc")

        assert response.status_code == status.HTTP_200_OK
        assert _seeded_order(response) == SEEDED_NAMES

    def test_sort_by_key_name_desc(self, auth_client, seeded_keys):
        """Descending name order is the reverse of ascending, not the same list."""
        response = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field=keyName&sort_order=desc"
        )

        assert response.status_code == status.HTTP_200_OK
        assert _seeded_order(response) == list(reversed(SEEDED_NAMES))

    def test_sort_by_created_at_desc_returns_newest_first(
        self, auth_client, seeded_keys
    ):
        """The fixture back-dates created_at, so newest-first is reverse-alphabetical."""
        response = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field=createdAt&sort_order=desc"
        )

        assert _seeded_order(response) == list(reversed(SEEDED_NAMES))

    def test_sort_by_created_at_asc_returns_oldest_first(
        self, auth_client, seeded_keys
    ):
        response = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field=createdAt&sort_order=asc"
        )

        assert _seeded_order(response) == SEEDED_NAMES

    def test_snake_case_sort_alias_matches_camel_case(self, auth_client, seeded_keys):
        """SORT_FIELD_MAP accepts both spellings for the same column."""
        camel = auth_client.get(f"{SECRET_KEYS_URL}?sort_field=keyName&sort_order=asc")
        snake = auth_client.get(f"{SECRET_KEYS_URL}?sort_field=key_name&sort_order=asc")

        assert _seeded_order(camel) == _seeded_order(snake) == SEEDED_NAMES

    def test_sort_by_created_by(self, auth_client, seeded_keys):
        """Sorting on the related user's name resolves without an N+1 blowup."""
        response = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field=createdBy&sort_order=asc"
        )

        assert response.status_code == status.HTTP_200_OK
        assert sorted(_seeded_order(response)) == SEEDED_NAMES

    @pytest.mark.parametrize("sort_field", ["enabled", "type"])
    def test_sort_by_mapped_boolean_and_choice_columns(
        self, auth_client, seeded_keys, sort_field
    ):
        """``enabled`` and ``type`` sort through their SORT_FIELD_MAP entries."""
        response = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field={sort_field}&sort_order=asc"
        )

        assert response.status_code == status.HTTP_200_OK
        assert sorted(_seeded_order(response)) == SEEDED_NAMES

    def test_invalid_sort_field_falls_back_to_created_at_desc(
        self, auth_client, seeded_keys
    ):
        """An unmapped sort_field must behave exactly like the default ordering."""
        fallback = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field=invalidField&sort_order=desc"
        )
        default = auth_client.get(
            f"{SECRET_KEYS_URL}?sort_field=createdAt&sort_order=desc"
        )

        assert fallback.status_code == status.HTTP_200_OK
        assert _seeded_order(fallback) == _seeded_order(default)
        assert _seeded_order(fallback) == list(reversed(SEEDED_NAMES))


@pytest.mark.integration
@pytest.mark.api
class TestGetSecretKeysRowContent:
    """Row-level branches of the get_secret_keys projection."""

    def test_soft_deleted_keys_are_excluded(self, auth_client, seeded_keys):
        """The queryset filters deleted=False."""
        removed = seeded_keys[0]
        removed.deleted = True
        removed.save(update_fields=["deleted"])

        response = auth_client.get(SECRET_KEYS_URL)

        assert removed.name not in _seeded_order(response)
        assert sorted(_seeded_order(response)) == SEEDED_NAMES[1:]

    def test_created_by_is_null_when_key_has_no_user(
        self, auth_client, organization, seeded_keys
    ):
        """`key.user.name if key.user else None` — the else branch."""
        from accounts.models.user import OrgApiKey

        orphan = OrgApiKey.objects.create(
            name="orphan-key",
            api_key=f"api-orphan-{uuid.uuid4().hex[:8]}",
            secret_key=f"secret-orphan-{uuid.uuid4().hex[:8]}",
            organization=organization,
            type="user",
            user=None,
        )

        response = auth_client.get(f"{SECRET_KEYS_URL}?search=orphan")

        rows = _rows(response)
        assert [row["id"] for row in rows] == [str(orphan.id)]
        assert rows[0]["created_by"] is None

    def test_row_exposes_expected_fields_only(self, auth_client, seeded_keys):
        response = auth_client.get(f"{SECRET_KEYS_URL}?search=alpha")

        assert set(_rows(response)[0]) == {
            "id",
            "key_name",
            "api_key",
            "secret_key",
            "created_by",
            "created_at",
            "enabled",
            "type",
        }


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

    def test_generate_key_duplicate_name(self, auth_client, organization, created_key):
        """Duplicate name is rejected and no second row is created."""
        from accounts.models.user import OrgApiKey

        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Fixture Test Key"},  # Same name as created_key fixture
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        surviving = OrgApiKey.all_objects.filter(
            name="Fixture Test Key", organization=organization, deleted=False
        )
        assert [str(key.id) for key in surviving] == [str(created_key)]


@pytest.mark.integration
@pytest.mark.api
class TestEnableDisableKeyStates:
    """Tests for enable/disable key state transitions."""

    def test_enable_already_enabled_key(self, auth_client, created_key):
        """Enabling an already enabled key fails and leaves it enabled."""
        from accounts.models.user import OrgApiKey

        assert OrgApiKey.all_objects.get(id=created_key).enabled is True

        response = auth_client.post(
            "/accounts/key/enable_key/",
            {"key_id": str(created_key)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert OrgApiKey.all_objects.get(id=created_key).enabled is True

    def test_disable_already_disabled_key(self, auth_client, created_key):
        """Disabling twice fails the second time and leaves it disabled."""
        from accounts.models.user import OrgApiKey

        first = auth_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(created_key)},
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK
        assert OrgApiKey.all_objects.get(id=created_key).enabled is False

        response = auth_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(created_key)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert OrgApiKey.all_objects.get(id=created_key).enabled is False

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

    def test_delete_key_as_member_fails(self, mocker, member_client, auth_client):
        """Member (non-owner) cannot delete keys — and the key survives.

        The status code alone would pass even if the view rejected the caller
        *after* soft-deleting the row, so assert the row and the revocation
        side effect too.
        """
        from accounts.models.user import OrgApiKey

        create_response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Key To Delete By Member"},
            format="json",
        )
        key_id = create_response.json().get("result", {}).get("key_id")
        revoke = mocker.patch("accounts.views.keys._publish_key_revocation")

        response = member_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": str(key_id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        revoke.assert_not_called()
        key = OrgApiKey.all_objects.get(id=key_id)
        assert key.deleted is False
        assert key.deleted_at is None

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
        """Cannot enable another org's key — and the key stays disabled.

        The key is disabled by its owner first so the assertion has something
        to prove: if the tenant filter regressed, ``enabled`` would flip to True.
        """
        from accounts.models.user import OrgApiKey

        disabled = auth_client.post(
            "/accounts/key/disable_key/", {"key_id": str(created_key)}, format="json"
        )
        assert disabled.status_code == status.HTTP_200_OK
        assert OrgApiKey.all_objects.get(id=created_key).enabled is False

        response = other_org_client.post(
            "/accounts/key/enable_key/",
            {"key_id": str(created_key)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert OrgApiKey.all_objects.get(id=created_key).enabled is False

    def test_cannot_disable_other_org_key(
        self, other_org_client, auth_client, created_key
    ):
        """Cannot disable another org's key — and the key stays enabled."""
        from accounts.models.user import OrgApiKey

        assert OrgApiKey.all_objects.get(id=created_key).enabled is True

        response = other_org_client.post(
            "/accounts/key/disable_key/",
            {"key_id": str(created_key)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert OrgApiKey.all_objects.get(id=created_key).enabled is True

    def test_cannot_delete_other_org_key(
        self, mocker, other_org_client, auth_client, created_key
    ):
        """Cannot delete another org's key — the row survives and no revocation fires.

        ``delete_secret_key`` soft-deletes and then publishes a revocation for
        the key material. A tenant-filter regression would leak both.
        """
        from accounts.models.user import OrgApiKey

        revoke = mocker.patch("accounts.views.keys._publish_key_revocation")

        response = other_org_client.delete(
            "/accounts/key/delete_secret_key/",
            {"key_id": str(created_key)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # The tenant filter must be what rejects this, not the owner-role gate
        # that runs before it — otherwise the test proves nothing about IDOR.
        assert "No keys are associated" in response.json()["detail"]
        revoke.assert_not_called()
        key = OrgApiKey.all_objects.get(id=created_key)
        assert key.deleted is False
        assert key.deleted_at is None

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
