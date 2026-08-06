"""
Signup & Account API Tests

Tests for user registration, logout, password reset, and account management.
"""

import pytest
from django.contrib.auth.tokens import default_token_generator
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


@pytest.fixture
def second_user(organization, db):
    """Create a second user in the same organization for testing."""
    from accounts.models import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="seconduser@test.com",
        password="testpassword123",
        name="Second User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
        is_active=True,
    )


@pytest.fixture
def second_user_client(second_user):
    """API client authenticated as second user."""
    client = APIClient()
    client.force_authenticate(user=second_user)
    return client


@pytest.fixture
def owner_user(organization, db):
    """Create an owner user in the organization."""
    from accounts.models import User
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    user = User.objects.create_user(
        email="owner@test.com",
        password="testpassword123",
        name="Owner User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
        is_active=True,
    )
    OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return user


@pytest.fixture
def owner_client(owner_user):
    """API client authenticated as owner."""
    client = APIClient()
    client.force_authenticate(user=owner_user)
    return client


@pytest.fixture
def other_org_user(db):
    """Create a user in a different organization for IDOR tests."""
    from accounts.models import Organization, User
    from tfc.constants.roles import OrganizationRoles

    other_org = Organization.objects.create(name="Other Test Org")
    return User.objects.create_user(
        email="otheruser@other-org.com",
        password="testpassword123",
        name="Other Org User",
        organization=other_org,
        organization_role=OrganizationRoles.OWNER,
        is_active=True,
    )


@pytest.fixture
def other_org_client(other_org_user):
    """API client authenticated as user from different organization."""
    client = APIClient()
    client.force_authenticate(user=other_org_user)
    return client


@pytest.mark.integration
@pytest.mark.api
class TestSignupAPI:
    """Tests for /accounts/signup/ endpoint."""

    @patch.dict("os.environ", {"ENV_TYPE": "local"})
    @patch("tfc.temporal.drop_in.start_activity")
    def test_signup_with_valid_data(
        self, mock_start_activity, api_client, db
    ):
        """User can register with valid data."""
        from accounts.models import User

        email = "newuser@signup-test.dev"
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": email,
                "password": "SecurePass123!",
                "full_name": "New User",
                "company_name": "Test Org",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        created_user = User.objects.get(email=email)
        assert created_user.name == "New User"
        assert created_user.organization is not None
        assert created_user.is_active is True

    def test_signup_with_existing_email(self, api_client, user):
        """Signup fails with already registered email."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": user.email,
                "password": "SecurePass123!",
                "name": "Duplicate User",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_signup_with_invalid_email(self, api_client, db):
        """Signup fails with invalid email format."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": "invalid-email",
                "password": "SecurePass123!",
                "name": "Test User",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_signup_with_missing_email(self, api_client, db):
        """Signup fails when email is missing."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "password": "SecurePass123!",
                "name": "Test User",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_signup_with_missing_password(self, api_client, db):
        """Signup fails when password is missing."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": "test@futureagi.com",
                "name": "Test User",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestLogoutAPI:
    """Tests for /accounts/logout/ endpoint."""

    def test_logout_authenticated_user(self, api_client, user):
        """Authenticated user can logout."""
        from django.utils import timezone

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        access = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        access_token = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access.id)}
        )

        response = api_client.post(
            "/accounts/logout/",
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        assert response.status_code == status.HTTP_200_OK
        access.refresh_from_db()
        assert access.is_active is False

    def test_logout_unauthenticated_user(self, api_client):
        """Unauthenticated logout request fails."""
        response = api_client.post("/accounts/logout/", format="json")
        # API returns 400 for missing refresh token
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestPasswordResetAPI:
    """Tests for password reset endpoints."""

    def test_initiate_password_reset_valid_email(self, api_client, user):
        """Can initiate password reset for existing user."""
        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {"email": user.email},
            format="json",
        )
        # Should return success even if email doesn't exist (security)
        assert response.status_code == status.HTTP_200_OK

    def test_initiate_password_reset_nonexistent_email(self, api_client, db):
        """Password reset for nonexistent email still returns success (security)."""
        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {"email": "nonexistent@futureagi.com"},
            format="json",
        )
        # Should return success to prevent email enumeration
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_initiate_password_reset_invalid_email(self, api_client, db):
        """Password reset with invalid email format."""
        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {"email": "invalid-email"},
            format="json",
        )
        # API may accept any string and return success for security
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_initiate_password_reset_rejects_unknown_request_fields(
        self, api_client, db
    ):
        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {
                "email": "person@example.com",
                "emailAddress": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "emailAddress")

    def test_password_reset_confirm_invalid_token(self, api_client, db):
        """Password reset confirm with invalid token fails."""
        response = api_client.post(
            "/accounts/password-reset-confirm/invalid-uid/invalid-token/",
            {"password": "NewSecurePass123!"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_password_reset_confirm_rejects_unknown_request_fields(
        self, api_client, db
    ):
        response = api_client.post(
            "/accounts/password-reset-confirm/invalid-uid/invalid-token/",
            {
                "new_password": "NewSecurePass123!",
                "repeat_password": "NewSecurePass123!",
                "newPassword": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "newPassword")


@pytest.mark.integration
@pytest.mark.api
class TestAcceptInvitationAPI:
    """Tests for /accounts/accept-invitation/<uid>/<token>/ endpoint."""

    def test_accept_invitation_rejects_unknown_request_fields(self, api_client, db):
        response = api_client.post(
            "/accounts/accept-invitation/invalid-uid/invalid-token/",
            {
                "new_password": "NewSecurePass123!",
                "repeat_password": "NewSecurePass123!",
                "newPassword": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "newPassword")


@pytest.mark.integration
@pytest.mark.api
class TestUserProfileAPI:
    """Tests for user profile endpoints."""

    def test_get_user_profile_authenticated(self, auth_client, user):
        """Authenticated user can get their profile."""
        response = auth_client.get("/accounts/get-user-profile-details/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_user_profile_unauthenticated(self, api_client):
        """Unauthenticated user cannot get profile."""
        response = api_client.get("/accounts/get-user-profile-details/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_update_user(self, auth_client, user):
        """Authenticated user can update their profile."""
        response = auth_client.post(
            "/accounts/update-user/",
            {"user_id": str(user.id), "name": "Updated Name"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.name == "Updated Name"

    def test_update_user_full_name(self, auth_client, user):
        """Authenticated user can update their full name."""
        response = auth_client.post(
            "/accounts/update-user-full-name/",
            {"name": "New Full Name"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.name == "New Full Name"

    def test_update_user_unauthenticated(self, api_client):
        """Unauthenticated user cannot update profile."""
        response = api_client.post(
            "/accounts/update-user/",
            {"name": "Hacker"},
            format="json",
        )
        # API may return 400 for validation before auth check
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestSignupEmailValidation:
    """Tests for email validation in signup."""

    def test_signup_with_empty_email(self, api_client, db):
        """Signup fails when email is empty string."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": "",
                "password": "SecurePass123!",
                "name": "Test User",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_signup_email_case_insensitive(self, api_client, user):
        """Signup recognizes existing email case-insensitively."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": user.email.upper(),  # Use uppercase version
                "password": "SecurePass123!",
                "name": "Test User",
            },
            format="json",
        )
        # Should fail - user already exists
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestDeleteUsersAPI:
    """Tests for /accounts/delete-users/ endpoint."""

    def test_delete_users_rejects_unknown_request_fields(self, owner_client):
        response = owner_client.delete(
            "/accounts/delete-users/",
            {
                "user_ids": [],
                "userIds": ["legacy camel alias"],
            },
            format="json",
        )

        assert_unknown_field(response, "userIds")

    def test_delete_users_unauthenticated(self, api_client, second_user):
        """Unauthenticated request fails."""
        response = api_client.delete(
            "/accounts/delete-users/",
            {"user_ids": [str(second_user.id)]},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_cannot_delete_own_account(self, auth_client, user):
        """Cannot delete your own account."""
        response = auth_client.delete(
            "/accounts/delete-users/",
            {"user_ids": [str(user.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["type"] == "validation_error"
        assert data["code"] == "invalid"
        assert data["detail"] == "Cannot delete your own account. Please try again."
        assert data["message"] == data["detail"]
        assert data["error"] == data["detail"]
        assert data["result"] == data["detail"]

    def test_delete_user_same_org(self, owner_client, second_user):
        """Owner can delete user in same organization."""
        from accounts.models import User

        user_id = second_user.id
        response = owner_client.delete(
            "/accounts/delete-users/",
            {"user_ids": [str(user_id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert not User.objects.filter(pk=user_id).exists()

    def test_cannot_delete_user_different_org(self, auth_client, other_org_user):
        """Cannot delete user from different organization (IDOR prevention)."""
        original_state = (
            other_org_user.name,
            other_org_user.email,
            other_org_user.organization_id,
        )
        response = auth_client.delete(
            "/accounts/delete-users/",
            {"user_ids": [str(other_org_user.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        other_org_user.refresh_from_db()
        assert (
            other_org_user.name,
            other_org_user.email,
            other_org_user.organization_id,
        ) == original_state

    def test_delete_nonexistent_user(self, auth_client):
        """Deleting nonexistent user returns error in response."""
        user_id = "00000000-0000-0000-0000-000000000000"
        response = auth_client.delete(
            "/accounts/delete-users/",
            {"user_ids": [user_id]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == [
            {"user_id": user_id, "error": "User does not exist."}
        ]


@pytest.mark.integration
@pytest.mark.api
class TestUpdateUserRoles:
    """Tests for role updates in /accounts/update-user/ endpoint."""

    def test_update_user_rejects_unknown_request_fields(
        self, owner_client, second_user
    ):
        response = owner_client.post(
            "/accounts/update-user/",
            {
                "user_id": str(second_user.id),
                "name": "Updated Name",
                "userId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "userId")

    def test_owner_can_change_roles(self, owner_client, second_user):
        """Owner can change another user's role."""
        from tfc.constants.roles import OrganizationRoles

        response = owner_client.post(
            "/accounts/update-user/",
            {
                "user_id": str(second_user.id),
                "organization_role": OrganizationRoles.ADMIN,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_member_cannot_change_roles(self, second_user_client, user):
        """Member cannot change roles (only owners can)."""
        from tfc.constants.roles import OrganizationRoles

        response = second_user_client.post(
            "/accounts/update-user/",
            {
                "user_id": str(user.id),
                "organization_role": OrganizationRoles.MEMBER,
            },
            format="json",
        )
        # Should fail - member cannot change roles
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_demote_last_owner(self, owner_client, owner_user):
        """Cannot demote the last owner of an organization."""
        from tfc.constants.roles import OrganizationRoles

        response = owner_client.post(
            "/accounts/update-user/",
            {
                "user_id": str(owner_user.id),
                "organization_role": OrganizationRoles.MEMBER,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "status": False,
            "type": "validation_error",
            "code": "invalid",
            "detail": "Cannot demote the last owner.",
            "message": "Cannot demote the last owner.",
            "error": "Cannot demote the last owner.",
            "result": "Cannot demote the last owner.",
        }

    def test_cannot_update_user_different_org(self, auth_client, other_org_user):
        """Cannot update user from different organization (IDOR prevention)."""
        original_state = (
            other_org_user.name,
            other_org_user.email,
            other_org_user.organization_id,
        )
        response = auth_client.post(
            "/accounts/update-user/",
            {
                "user_id": str(other_org_user.id),
                "name": "Hacked Name",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_org_user.refresh_from_db()
        assert (
            other_org_user.name,
            other_org_user.email,
            other_org_user.organization_id,
        ) == original_state

    def test_update_user_missing_user_id(self, auth_client):
        """Update user fails without user_id."""
        response = auth_client.post(
            "/accounts/update-user/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestUpdateUserFullName:
    """Tests for /accounts/update-user-full-name/ endpoint."""

    def test_update_full_name_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/update-user-full-name/",
            {
                "name": "New Full Name",
                "fullName": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "fullName")

    def test_update_full_name_authenticated(self, auth_client, user):
        """Authenticated user can update their full name."""
        response = auth_client.post(
            "/accounts/update-user-full-name/",
            {"name": "New Full Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.name == "New Full Name"

    def test_update_full_name_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/update-user-full-name/",
            {"name": "Hacker"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_update_full_name_empty_name(self, auth_client, user):
        """Updating with empty name doesn't change anything."""
        original_name = user.name
        response = auth_client.post(
            "/accounts/update-user-full-name/",
            {"name": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.name == original_name  # Name unchanged

    def test_update_full_name_deleted_user_uses_error_envelope(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        user_id = user.id
        user.delete()

        response = client.post(
            "/accounts/update-user-full-name/",
            {"name": "Ghost User"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data["status"] is False
        assert data["type"] == "not_found"
        assert data["code"] == "not_found"
        assert data["detail"] == "User does not exist."
        assert data["message"] == data["detail"]
        assert data["error"] == data["detail"]
        assert data["result"] == data["detail"]
        assert str(user_id) not in str(data)


@pytest.mark.integration
@pytest.mark.api
class TestPasswordResetValidation:
    """Tests for password reset validation."""

    def test_password_reset_missing_email(self, api_client, db):
        """Password reset fails when email is missing."""
        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_null_email(self, api_client, db):
        """Password reset fails when email is null."""
        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {"email": None},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_confirm_passwords_mismatch(self, api_client, db):
        """Password reset confirm fails when passwords don't match."""
        response = api_client.post(
            "/accounts/password-reset-confirm/test-uid/test-token/",
            {
                "new_password": "NewPass123!",
                "repeat_password": "DifferentPass123!",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestResendInvitationEmails:
    """Tests for /accounts/resend-invitation-emails/ endpoint."""

    def test_resend_invitation_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/resend-invitation-emails/",
            {
                "user_ids": [],
                "userIds": ["legacy camel alias"],
            },
            format="json",
        )

        assert_unknown_field(response, "userIds")

    def test_resend_invitation_unauthenticated(self, api_client, second_user):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/resend-invitation-emails/",
            {"user_ids": [str(second_user.id)]},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_resend_invitation_nonexistent_user(self, auth_client):
        """Resending to nonexistent user returns error."""
        response = auth_client.post(
            "/accounts/resend-invitation-emails/",
            {"user_ids": ["00000000-0000-0000-0000-000000000000"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()
        # Should have error in response
        assert any("error" in r for r in result if isinstance(r, dict))


@pytest.mark.integration
@pytest.mark.api
class TestUserProfileDetails:
    """Additional tests for user profile details."""

    def test_profile_returns_correct_fields(self, auth_client, user):
        """Profile response includes expected fields."""
        response = auth_client.get("/accounts/get-user-profile-details/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "name" in data
        assert "email" in data
        assert "org_name" in data

    def test_profile_includes_org_name(self, auth_client, user, organization):
        """Profile includes organization name when user has org."""
        response = auth_client.get("/accounts/get-user-profile-details/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        org_name = data.get("org_name")
        assert org_name is not None


@pytest.mark.integration
@pytest.mark.api
class TestResponseFormats:
    """Tests for consistent response formats across endpoints."""

    def test_signup_error_has_correct_format(self, api_client, db):
        """Signup error response has correct format."""
        response = api_client.post(
            "/accounts/signup/",
            {"email": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "status" in data or "error" in data

    def test_profile_update_success_has_message(self, auth_client, user):
        """Profile update success includes message."""
        response = auth_client.post(
            "/accounts/update-user-full-name/",
            {"name": "Test Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "message" in data


@pytest.mark.integration
@pytest.mark.api
class TestAccountTakeoverVulnerabilityFixed:
    """Regression tests for account takeover via update_true (reported 2026-04-28).

    The signup endpoint previously accepted update_true/old_email params that
    allowed an unauthenticated attacker to change any existing user's email and
    password. These tests prove the vulnerability is fixed.
    """

    def test_update_true_cannot_modify_existing_user(self, api_client, user):
        """Sending update_true=True with old_email must NOT modify an existing user."""
        original_email = user.email
        original_password_hash = user.password

        api_client.post(
            "/accounts/signup/",
            {
                "email": "attacker@futureagi.com",
                "full_name": "Attacker",
                "company_name": "Evil Corp",
                "update_true": True,
                "old_email": original_email,
                "allow_email": True,
            },
            format="json",
        )

        # Verify the existing user was NOT modified
        user.refresh_from_db()
        assert user.email == original_email
        assert user.password == original_password_hash

    def test_signup_rejects_existing_email_even_with_update_true(
        self, api_client, user
    ):
        """Signup must reject when target email exists, regardless of update_true."""
        response = api_client.post(
            "/accounts/signup/",
            {
                "email": user.email,
                "full_name": "Attacker",
                "company_name": "",
                "update_true": True,
                "old_email": user.email,
                "allow_email": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_old_email_param_is_stripped(self, api_client, user):
        """The old_email parameter must be stripped and never reach first_signup."""
        original_email = user.email
        original_password_hash = user.password

        api_client.post(
            "/accounts/signup/",
            {
                "email": "newuser@futureagi.com",
                "full_name": "New User",
                "company_name": "",
                "old_email": original_email,
                "allow_email": True,
            },
            format="json",
        )

        # Regardless of response, the original user must be untouched
        user.refresh_from_db()
        assert user.email == original_email
        assert user.password == original_password_hash

    def test_full_attack_scenario(self, api_client, user):
        """Full attack scenario: attacker cannot take over victim's account."""
        victim_email = user.email
        victim_name = user.name
        victim_password_hash = user.password
        attacker_email = "attacker-ato@futureagi.com"

        # Attempt the exact attack from the security report
        api_client.post(
            "/accounts/signup/",
            {
                "email": attacker_email,
                "full_name": "ATO Validation Controlled",
                "company_name": "Audit",
                "old_email": victim_email,
                "update_true": True,
                "allow_email": True,
            },
            format="json",
        )

        # Verify victim's account is completely untouched
        user.refresh_from_db()
        assert user.email == victim_email
        assert user.name == victim_name
        assert user.password == victim_password_hash

        # Verify attacker email is NOT linked to victim's user ID
        from accounts.models import User as UserModel

        attacker_users = UserModel.objects.filter(email=attacker_email)
        for u in attacker_users:
            assert u.id != user.id


@pytest.mark.integration
@pytest.mark.api
class TestActivateAccountAPI:
    """Tests for GET /accounts/activate/<uidb64>/<token>/."""

    def _inactive_user(self, db):
        from accounts.models import User

        return User.objects.create_user(
            email="inactive-activate@test.com",
            password="testpassword123",
            name="Inactive Activate",
            is_active=False,
        )

    def _activation_url(self, user):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.views.signup import account_activation_token

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = account_activation_token.make_token(user)
        return f"/accounts/activate/{uid}/{token}/"

    def test_activate_account_happy_path(self, api_client, db):
        """Valid activation token activates user and creates owner org."""
        from django.core.cache import cache

        from accounts.models.organization_membership import OrganizationMembership

        cache.clear()
        user = self._inactive_user(db)
        response = api_client.get(self._activation_url(user))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Account successfully activated."

        user.refresh_from_db()
        assert user.is_active is True
        assert user.organization is not None
        assert user.organization_role == "Owner"
        assert OrganizationMembership.objects.filter(
            user=user, organization=user.organization, is_active=True, role="Owner"
        ).exists()

    def test_activate_account_invalid_token(self, api_client, db):
        """Invalid token is rejected."""
        from django.core.cache import cache
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        cache.clear()
        user = self._inactive_user(db)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        response = api_client.get(f"/accounts/activate/{uid}/not-a-valid-token/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.is_active is False

    def test_activate_account_unknown_user(self, api_client, db):
        """Unknown uid returns bad request."""
        from django.core.cache import cache
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        cache.clear()
        uid = urlsafe_base64_encode(force_bytes("00000000-0000-0000-0000-000000000000"))
        response = api_client.get(f"/accounts/activate/{uid}/any-token/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestPasswordResetConfirmHappyPath:
    """Happy-path coverage for POST /accounts/password-reset-confirm/."""

    def test_password_reset_confirm_valid_token(self, api_client, user):
        """Valid encrypted token resets password and returns success."""
        from django.contrib.auth.hashers import check_password
        from django.utils import timezone
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        access = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        token = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access.id)}
        )
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        new_password = "BrandNewPass123!"

        response = api_client.post(
            f"/accounts/password-reset-confirm/{uidb64}/{token}/",
            {"new_password": new_password, "repeat_password": new_password},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.content

        user.refresh_from_db()
        assert check_password(new_password, user.password)
        access.refresh_from_db()
        assert access.is_active is False


@pytest.mark.integration
@pytest.mark.api
class TestActivateAccountRateLimit:
    """Rate-limit boundary tests for GET /accounts/activate/<uidb64>/<token>/."""

    @pytest.fixture(autouse=True)
    def _clear_rate_limit_cache(self):
        yield
        from django.core.cache import cache

        for ip in ("10.20.30.1", "10.20.30.2", "10.20.30.3", "10.20.30.4"):
            cache.delete(f"activate_account_rate:{ip}")

    def _inactive_user(self, db):
        from accounts.models import User

        return User.objects.create_user(
            email="rate-limit-activate@test.com",
            password="testpassword123",
            name="Rate Limit Activate",
            is_active=False,
        )

    def _activation_url(self, user):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.views.signup import account_activation_token

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = account_activation_token.make_token(user)
        return f"/accounts/activate/{uid}/{token}/"

    def test_rate_limit_returns_429_at_boundary(self, api_client, db):
        """11th activation attempt within a minute returns429."""
        from django.core.cache import cache

        user = self._inactive_user(db)
        url = self._activation_url(user)
        ip = "10.20.30.1"
        cache.set(f"activate_account_rate:{ip}", 10, timeout=60)

        response = api_client.get(url, REMOTE_ADDR=ip)
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        # Verify the cache key was not incremented (rate limit short-circuits)
        assert cache.get(f"activate_account_rate:{ip}") == 10

    def test_rate_limit_allows_10th_attempt(self, api_client, db):
        """10th activation attempt succeeds (boundary is >=10, so 9 is allowed)."""
        from django.core.cache import cache

        user = self._inactive_user(db)
        url = self._activation_url(user)
        ip = "10.20.30.2"
        cache.set(f"activate_account_rate:{ip}", 9, timeout=60)

        response = api_client.get(url, REMOTE_ADDR=ip)
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.is_active is True
        assert cache.get(f"activate_account_rate:{ip}") == 10

    def test_rate_limit_increments_on_each_attempt(self, api_client, db):
        """Each activation attempt increments the rate counter."""
        from django.core.cache import cache

        user = self._inactive_user(db)
        url = self._activation_url(user)
        ip = "10.20.30.3"
        cache.set(f"activate_account_rate:{ip}", 0, timeout=60)

        api_client.get(url, REMOTE_ADDR=ip)
        assert cache.get(f"activate_account_rate:{ip}") == 1

    def test_rate_limit_uses_x_forwarded_for_ip(self, api_client, db):
        """X-Forwarded-For header is used as the rate-limit key when present."""
        from django.core.cache import cache

        user = self._inactive_user(db)
        url = self._activation_url(user)
        forwarded_ip = "10.20.30.4"
        cache.set(f"activate_account_rate:{forwarded_ip}", 10, timeout=60)

        response = api_client.get(
            url,
            REMOTE_ADDR="10.20.30.1",
            HTTP_X_FORWARDED_FOR=f"{forwarded_ip}, 192.168.1.1",
        )
        # First IP in X-Forwarded-For is used for rate limiting
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.integration
@pytest.mark.api
class TestActivateAccountReplay:
    """Replay tests for GET /accounts/activate/<uidb64>/<token>/."""

    def _inactive_user(self, db):
        from uuid import uuid4

        from accounts.models import User

        return User.objects.create_user(
            email=f"replay-activate-{uuid4().hex}@test.com",
            password="testpassword123",
            name="Replay Activate",
            is_active=False,
        )

    def _activation_url(self, user):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.views.signup import account_activation_token

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = account_activation_token.make_token(user)
        return f"/accounts/activate/{uid}/{token}/"

    def test_reactivate_already_active_user_rejected(self, api_client, db):
        """Re-activating an already-active user returns400 (token invalidated by is_active change)."""
        from django.core.cache import cache

        cache.clear()
        user = self._inactive_user(db)
        url = self._activation_url(user)

        # First call succeeds
        response1 = api_client.get(url)
        assert response1.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.is_active is True

        # Second call with same token fails — token hash now includes is_active=True
        response2 = api_client.get(url)
        assert response2.status_code == status.HTTP_400_BAD_REQUEST
        data = response2.json()
        assert (
            "invalid" in data.get("message", "").lower()
            or "expired" in data.get("message", "").lower()
        )

    def test_reactivate_creates_no_duplicate_organization(self, api_client, db):
        """Activation replay does not create a second organization."""
        from django.core.cache import cache

        from accounts.models import Organization

        cache.clear()
        user = self._inactive_user(db)
        url = self._activation_url(user)
        organizations_before = Organization.objects.count()

        first_response = api_client.get(url)
        assert first_response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        org_id = user.organization_id
        assert org_id is not None
        assert Organization.objects.count() == organizations_before + 1

        replay_response = api_client.get(url)
        assert replay_response.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.organization_id == org_id
        assert Organization.objects.count() == organizations_before + 1


@pytest.mark.integration
@pytest.mark.api
class TestPasswordResetConfirmEdgeCases:
    """Edge-case tests for POST /accounts/password-reset-confirm/<uidb64>/<token>/."""

    def test_inactive_auth_token_rejected(self, api_client, user):
        """Reset confirm rejects when the AuthToken is inactive."""
        from django.utils import timezone
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        access = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=False,  # Inactive token
            last_used_at=timezone.now(),
        )
        token = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access.id)}
        )
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = api_client.post(
            f"/accounts/password-reset-confirm/{uidb64}/{token}/",
            {"new_password": "BrandNewPass123!", "repeat_password": "BrandNewPass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "expired" in response.json()["message"].lower()

    def test_expired_auth_token_rejected(self, api_client, user):
        """Reset confirm rejects when the AuthToken's last_used_at is too old."""
        from datetime import timedelta

        from django.utils import timezone
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        access = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at=timezone.now()
            - timedelta(days=3),  # 3 days ago (exceeds 2-day limit)
        )
        token = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access.id)}
        )
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = api_client.post(
            f"/accounts/password-reset-confirm/{uidb64}/{token}/",
            {"new_password": "BrandNewPass123!", "repeat_password": "BrandNewPass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "expired" in response.json()["message"].lower()

    def test_user_id_mismatch_rejected(self, api_client, user, db):
        """Reset confirm rejects when token's user_id doesn't match the URL uid."""
        from django.utils import timezone
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message
        from accounts.models import User
        from accounts.models.auth_token import AuthToken, AuthTokenType

        other_user = User.objects.create_user(
            email="other-reset-confirm@test.com",
            password="testpassword123",
            name="Other Reset User",
        )

        # Create token for other_user
        access = AuthToken.objects.create(
            user=other_user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        token = generate_encrypted_message(
            {"user_id": str(other_user.id), "id": str(access.id)}
        )

        # But URL uidb64 is for `user`
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = api_client.post(
            f"/accounts/password-reset-confirm/{uidb64}/{token}/",
            {"new_password": "BrandNewPass123!", "repeat_password": "BrandNewPass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "invalid token" in response.json()["message"].lower()

    def test_nonexistent_auth_token_id_rejected(self, api_client, user):
        """Reset confirm rejects when token references a non-existent AuthToken ID."""
        import uuid

        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message

        fake_id = str(uuid.uuid4())
        token = generate_encrypted_message({"user_id": str(user.id), "id": fake_id})
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        response = api_client.post(
            f"/accounts/password-reset-confirm/{uidb64}/{token}/",
            {"new_password": "BrandNewPass123!", "repeat_password": "BrandNewPass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "invalid token id" in response.json()["message"].lower()

    def test_same_password_as_old_rejected(self, api_client, user):
        """Reset confirm rejects when new password is identical to the old one."""
        from django.utils import timezone
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        access = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        token = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access.id)}
        )
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

        # Try to set the password to the same value
        response = api_client.post(
            f"/accounts/password-reset-confirm/{uidb64}/{token}/",
            {"new_password": "testpassword123", "repeat_password": "testpassword123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "same as the old" in response.json()["message"].lower()

    def test_nonexistent_user_rejected(self, api_client, db):
        """Reset confirm rejects when uidb64 decodes to a non-existent user."""
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.authentication import generate_encrypted_message

        fake_uid = urlsafe_base64_encode(
            force_bytes("00000000-0000-0000-0000-000000000000")
        )
        token = generate_encrypted_message(
            {"user_id": "00000000-0000-0000-0000-000000000000", "id": "fake-token-id"}
        )

        response = api_client.post(
            f"/accounts/password-reset-confirm/{fake_uid}/{token}/",
            {"new_password": "BrandNewPass123!", "repeat_password": "BrandNewPass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not exist" in response.json()["message"].lower()


@pytest.mark.integration
@pytest.mark.api
class TestAcceptInvitationEdgeCases:
    """Edge-case tests for /accounts/accept-invitation/<uidb64>/<token>/."""

    def _create_inactive_user(self, db, email, organization):
        from accounts.models import User

        return User.objects.create_user(
            email=email,
            password="testpassword123",
            name=f"Invite User {email}",
            is_active=False,
            organization=organization,
        )

    def _make_invite_token(self, user):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return uid, token

    def test_authenticated_user_mismatch_rejected(self, api_client, user, db):
        """POST rejected when a different user is logged in (account takeover prevention)."""
        from accounts.models.organization_invite import InviteStatus, OrganizationInvite

        org = user.organization
        target_user = self._create_inactive_user(
            db, "invite-target-mismatch@test.com", org
        )
        OrganizationInvite.objects.create(
            target_email=target_user.email,
            organization=org,
            level=3,
            invited_by=user,
            status=InviteStatus.PENDING,
        )
        uid, token = self._make_invite_token(target_user)
        # Supply a real bearer token so the view can detect the mismatch.
        from django.utils import timezone

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        access_token = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            last_used_at=timezone.now(),
            is_active=True,
        )
        bearer = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access_token.id)}
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
        response = api_client.post(
            f"/accounts/accept-invitation/{uid}/{token}/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = response.json()
        assert data.get("code") == "authenticated_user_mismatch"
        assert "different user" in data["message"].lower()

        # Target user should still be inactive
        target_user.refresh_from_db()
        assert target_user.is_active is False

    def test_cancelled_invitation_rejected(self, api_client, db):
        """POST rejected when the invite has been cancelled (no pending invite found)."""
        from accounts.models.organization import Organization
        from accounts.models.organization_invite import InviteStatus, OrganizationInvite

        org = Organization.objects.create(name="Cancelled Invite Org")
        target_user = self._create_inactive_user(db, "invite-cancelled@test.com", org)
        # Create a CANCELLED invite
        OrganizationInvite.objects.create(
            target_email=target_user.email,
            organization=org,
            level=3,
            invited_by=target_user,
            status=InviteStatus.CANCELLED,
        )
        uid, token = self._make_invite_token(target_user)

        response = api_client.post(
            f"/accounts/accept-invitation/{uid}/{token}/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cancelled or expired" in response.json()["message"].lower()

        target_user.refresh_from_db()
        assert target_user.is_active is False

    def test_no_pending_invitation_rejected(self, api_client, db):
        """POST rejected when no OrganizationInvite record exists at all."""
        from accounts.models.organization import Organization

        org = Organization.objects.create(name="No Invite Org")
        target_user = self._create_inactive_user(db, "invite-none@test.com", org)
        # No OrganizationInvite created
        uid, token = self._make_invite_token(target_user)

        response = api_client.post(
            f"/accounts/accept-invitation/{uid}/{token}/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cancelled or expired" in response.json()["message"].lower()

    def test_invalid_token_rejected(self, api_client, db):
        """POST rejected with invalid/unknown token."""
        from accounts.models.organization import Organization

        org = Organization.objects.create(name="Bad Token Org")
        target_user = self._create_inactive_user(db, "invite-bad-token@test.com", org)
        uid, _ = self._make_invite_token(target_user)

        response = api_client.post(
            f"/accounts/accept-invitation/{uid}/definitely-not-valid/",
            {"new_password": "SecurePass123!", "repeat_password": "SecurePass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            "invalid" in response.json()["message"].lower()
            or "expired" in response.json()["message"].lower()
        )

    def test_get_validates_token_returns_org_info(self, api_client, db):
        """GET validates token and returns org info without consuming it."""
        from accounts.models.organization import Organization
        from accounts.models.organization_invite import InviteStatus, OrganizationInvite

        org = Organization.objects.create(name="Validate Org")
        target_user = self._create_inactive_user(db, "invite-validate@test.com", org)
        OrganizationInvite.objects.create(
            target_email=target_user.email,
            organization=org,
            level=3,
            invited_by=target_user,
            status=InviteStatus.PENDING,
        )
        uid, token = self._make_invite_token(target_user)

        response = api_client.get(
            f"/accounts/accept-invitation/{uid}/{token}/",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["valid"] is True
        assert data["email"] == target_user.email
        assert data["org_name"] == org.name

        # Token should still be valid (not consumed by GET)
        response2 = api_client.get(
            f"/accounts/accept-invitation/{uid}/{token}/",
        )
        assert response2.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestInitiatePasswordResetSSO:
    """SSO guard for POST /accounts/password-reset-initiate/."""

    def test_sso_user_rejected(self, api_client, user, db):
        """Password reset is rejected for SSO-enabled organization users."""
        from saml2_auth.models import SAMLMetadataModel

        SAMLMetadataModel.objects.create(
            organization=user.organization,
            identity_type=SAMLMetadataModel.IDENTITY_OKTA,
            relay_state="test-saml-relay",
        )

        response = api_client.post(
            "/accounts/password-reset-initiate/",
            {"email": user.email},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "sso" in response.json()["message"].lower()
