"""
User API Tests

Tests for user authentication, token management, and user info endpoints.
"""

import pytest
from django.core.cache import cache
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


@pytest.fixture
def clear_cache():
    """Clear cache before and after tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def second_user(organization, db):
    """Create a second user in the same organization."""
    from accounts.models import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="seconduser@futureagi.com",
        password="testpassword123",
        name="Second User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
        is_active=True,
    )


@pytest.fixture
def inactive_user(organization, db):
    """Create an inactive user."""
    from accounts.models import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="inactive@futureagi.com",
        password="testpassword123",
        name="Inactive User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
        is_active=False,
    )


@pytest.mark.integration
@pytest.mark.api
class TestTokenObtainAPI:
    """Tests for /accounts/token/ endpoint (login)."""

    def test_login_with_valid_credentials(self, api_client, user, clear_cache):
        """User can login with valid credentials."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": user.email,
                "password": "testpassword123",
            },
            format="json",
        )
        # May return 200 with tokens or require org selection
        assert response.status_code == status.HTTP_200_OK

    def test_login_with_invalid_password(self, api_client, user, clear_cache):
        """Login fails with invalid password."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": user.email,
                "password": "wrongpassword",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_login_with_nonexistent_email(self, api_client, db, clear_cache):
        """Login fails with nonexistent email."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": "nonexistent@futureagi.com",
                "password": "testpassword123",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_login_with_inactive_user(self, api_client, inactive_user, clear_cache):
        """Login fails for inactive user."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": inactive_user.email,
                "password": "testpassword123",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error_code"] == "LOGIN_ACCOUNT_DEACTIVATED"

    def test_login_email_case_insensitive(self, api_client, user, clear_cache):
        """Login works with email in different case."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": user.email.upper(),
                "password": "testpassword123",
            },
            format="json",
        )
        # Should work - email is normalized to lowercase
        assert response.status_code == status.HTTP_200_OK

    def test_login_missing_email(self, api_client, db, clear_cache):
        """Login fails without email."""
        response = api_client.post(
            "/accounts/token/",
            {"password": "testpassword123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_missing_password(self, api_client, user, clear_cache):
        """Login without password - may require org selection or fail."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email},
            format="json",
        )
        # API may return 200 (for org selection) or 400 depending on validation
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.integration
@pytest.mark.api
class TestTokenRefreshAPI:
    """Tests for /accounts/token/refresh/ endpoint."""

    def test_refresh_without_token(self, api_client, clear_cache):
        """Refresh fails without refresh token."""
        response = api_client.post(
            "/accounts/token/refresh/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_with_invalid_token(self, api_client, clear_cache):
        """Refresh fails with invalid token."""
        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": "invalid-token"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestGetUserInfoAPI:
    """Tests for /accounts/user-info/ endpoint."""

    def test_get_user_info_authenticated(self, auth_client, user):
        """Authenticated user can get their info."""
        response = auth_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "email" in data
        assert data["email"] == user.email

    def test_get_user_info_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/user-info/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_user_info_includes_workspace_info(self, auth_client, user):
        """User info includes workspace information."""
        response = auth_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should have workspace-related fields
        assert "default_workspace_id" in data or "ws_enabled" in data

    def test_get_user_info_includes_remember_me(self, auth_client, user):
        """User info includes remember_me setting."""
        response = auth_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "remember_me" in data


@pytest.mark.integration
@pytest.mark.api
class TestFirstChecksAPI:
    """Tests for /accounts/first-checks/ endpoint."""

    def test_first_checks_authenticated(self, auth_client, user):
        """Authenticated user can get first checks."""
        response = auth_client.get("/accounts/first-checks/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        # Should have all check fields
        assert "keys" in result
        assert "dataset" in result
        assert "evaluation" in result
        assert "experiment" in result
        assert "observe" in result
        assert "invite" in result

    def test_first_checks_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/first-checks/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_first_checks_returns_boolean_values(self, auth_client, user):
        """First checks returns boolean values for each check."""
        response = auth_client.get("/accounts/first-checks/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        for key in ["keys", "dataset", "evaluation", "experiment", "observe", "invite"]:
            assert isinstance(result.get(key), bool)

    def test_first_checks_ignores_other_workspace_experiments(
        self, auth_client, user, organization, workspace
    ):
        """Experiment completion should reflect the active workspace."""
        from accounts.models.workspace import Workspace
        from model_hub.models.develop_dataset import Dataset
        from model_hub.models.experiments import ExperimentsTable

        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        other_dataset = Dataset.no_workspace_objects.create(
            name="Other workspace dataset",
            organization=organization,
            workspace=other_workspace,
            user=user,
        )
        ExperimentsTable.no_workspace_objects.create(
            name="Other workspace experiment",
            dataset=other_dataset,
            user=user,
            prompt_config=[],
        )

        response = auth_client.get("/accounts/first-checks/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result["experiment"] is False

        active_dataset = Dataset.no_workspace_objects.create(
            name="Active workspace dataset",
            organization=organization,
            workspace=workspace,
            user=user,
        )
        ExperimentsTable.no_workspace_objects.create(
            name="Active workspace experiment",
            dataset=active_dataset,
            user=user,
            prompt_config=[],
        )

        response = auth_client.get("/accounts/first-checks/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result["experiment"] is True

    def test_first_checks_invites_are_active_workspace_scoped(
        self, auth_client, user, organization, workspace
    ):
        """Invite completion should not be satisfied by another workspace."""
        from accounts.models import User
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import Workspace, WorkspaceMembership
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        other_workspace = Workspace.objects.create(
            name="Invite Other Workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        invitee = User.objects.create_user(
            email="invited-workspace-only@futureagi.com",
            password="testpassword123",
            name="Invited Workspace User",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
            invited_by=user,
        )
        org_membership = OrganizationMembership.no_workspace_objects.create(
            user=invitee,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
            invited_by=user,
        )
        WorkspaceMembership.no_workspace_objects.create(
            workspace=other_workspace,
            user=invitee,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            organization_membership=org_membership,
            invited_by=user,
            is_active=True,
        )

        response = auth_client.get("/accounts/first-checks/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result["invite"] is False

        WorkspaceMembership.no_workspace_objects.create(
            workspace=workspace,
            user=invitee,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            organization_membership=org_membership,
            invited_by=user,
            is_active=True,
        )

        response = auth_client.get("/accounts/first-checks/")

        assert response.status_code == status.HTTP_200_OK
        result = response.json().get("result", {})
        assert result["invite"] is True


@pytest.mark.integration
@pytest.mark.api
class TestUserOnboardingAPI:
    """Tests for /accounts/onboarding/ endpoint."""

    def test_get_onboarding_authenticated(self, auth_client, user):
        """Authenticated user can get onboarding status."""
        response = auth_client.get("/accounts/onboarding/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data.get("result", data)
        assert "role" in result
        assert "goals" in result
        assert "completed" in result

    def test_get_onboarding_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.get("/accounts/onboarding/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_post_onboarding_authenticated(self, auth_client, user):
        """Authenticated user can save onboarding data."""
        response = auth_client.post(
            "/accounts/onboarding/",
            {
                "role": "developer",
                "goals": ["Build AI apps", "Monitor performance"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_post_onboarding_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/onboarding/",
            {
                "role": "developer",
                "goals": ["Build AI apps"],
                "selectedGoals": ["legacy camel alias"],
            },
            format="json",
        )

        assert_unknown_field(response, "selectedGoals")

    def test_post_onboarding_updates_user(self, auth_client, user):
        """Onboarding data is saved to user model."""
        auth_client.post(
            "/accounts/onboarding/",
            {
                "role": "data_scientist",
                "goals": ["Analyze data"],
            },
            format="json",
        )
        user.refresh_from_db()
        assert user.role == "data_scientist"
        assert "Analyze data" in user.goals

    def test_post_onboarding_allows_workspace_viewer_profile_update(
        self, api_client, organization, workspace
    ):
        """Workspace viewers can still save user-owned onboarding data."""
        from accounts.models import User
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import WorkspaceMembership
        from tfc.constants.levels import Level
        from tfc.constants.roles import OrganizationRoles

        viewer = User.objects.create_user(
            email="onboarding-viewer@futureagi.com",
            password="testpassword123",
            name="Onboarding Viewer",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER_VIEW_ONLY,
            is_active=True,
        )
        org_membership = OrganizationMembership.no_workspace_objects.create(
            user=viewer,
            organization=organization,
            role=OrganizationRoles.MEMBER_VIEW_ONLY,
            level=Level.VIEWER,
            is_active=True,
        )
        WorkspaceMembership.no_workspace_objects.create(
            workspace=workspace,
            user=viewer,
            role=OrganizationRoles.WORKSPACE_VIEWER,
            level=Level.WORKSPACE_VIEWER,
            organization_membership=org_membership,
            is_active=True,
        )

        login = api_client.post(
            "/accounts/token/",
            {"email": viewer.email, "password": "testpassword123"},
            format="json",
        )
        assert login.status_code == status.HTTP_200_OK

        response = api_client.post(
            "/accounts/onboarding/",
            {"role": "data_scientist", "goals": ["Run evaluations"]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}",
            HTTP_X_ORGANIZATION_ID=str(organization.id),
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

        assert response.status_code == status.HTTP_200_OK
        viewer.refresh_from_db()
        assert viewer.role == "data_scientist"
        assert viewer.goals == ["Run evaluations"]

    def test_post_onboarding_missing_role(self, auth_client, user):
        """Onboarding fails without role."""
        response = auth_client.post(
            "/accounts/onboarding/",
            {"goals": ["Build apps"]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_onboarding_unauthenticated(self, api_client):
        """Unauthenticated onboarding fails."""
        response = api_client.post(
            "/accounts/onboarding/",
            {"role": "developer", "goals": []},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestManageRedisKeyAPI:
    """Tests for /accounts/redis-key/ endpoint."""

    def test_manage_redis_key_unauthenticated(self, api_client):
        """Unauthenticated request fails."""
        response = api_client.post(
            "/accounts/redis-key/",
            {"key": "test_key", "value": "test_value"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_manage_redis_key_invalid_token(self, auth_client):
        """Request with invalid access_token_id fails."""
        response = auth_client.post(
            "/accounts/redis-key/",
            {
                "access_token_id": "invalid-token",
                "key": "test_key",
                "value": "test_value",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_manage_redis_key_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/accounts/redis-key/",
            {
                "access_token_id": "invalid-token",
                "key": "test_key",
                "value": "test_value",
                "accessTokenId": "legacy camel alias",
            },
            format="json",
        )

        assert_unknown_field(response, "accessTokenId")

    def test_manage_redis_key_missing_key(self, auth_client):
        """Request without key fails."""
        response = auth_client.post(
            "/accounts/redis-key/",
            {"access_token_id": "some-token", "value": "test_value"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestLoginRateLimiting:
    """Tests for login rate limiting and account blocking."""

    def test_failed_login_tracks_attempts(self, api_client, user, clear_cache):
        """Failed login attempts are tracked and error_code is returned."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "wrongpassword"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        result = data.get("result", data)
        # Should include remaining attempts info and structured error_code
        assert (
            "remainingAttempts" in result
            or "remaining_attempts" in result
            or "error" in result
        )
        assert result.get("error_code") == "LOGIN_INVALID_CREDENTIALS"

    def test_login_with_remember_me(self, api_client, user, clear_cache):
        """Login with remember_me flag."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": user.email,
                "password": "testpassword123",
                "remember_me": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestMultiOrgLogin:
    """Tests for multi-organization login flow."""

    def test_login_with_organization_id(
        self, api_client, user, organization, clear_cache
    ):
        """Login rejects unsupported organization_id selection."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": user.email,
                "password": "testpassword123",
                "organization_id": str(organization.id),
            },
            format="json",
        )
        assert_unknown_field(response, "organization_id")

    def test_login_with_invalid_organization_id(self, api_client, user, clear_cache):
        """Login rejects organization_id even when the value is not a real org."""
        response = api_client.post(
            "/accounts/token/",
            {
                "email": user.email,
                "password": "testpassword123",
                "organization_id": "00000000-0000-0000-0000-000000000000",
            },
            format="json",
        )
        assert_unknown_field(response, "organization_id")


@pytest.mark.integration
@pytest.mark.api
class TestResponseFormats:
    """Tests for consistent response formats."""

    def test_user_info_response_format(self, auth_client, user):
        """User info returns expected format."""
        response = auth_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should include basic user fields
        assert "email" in data
        assert "name" in data

    def test_first_checks_response_format(self, auth_client, user):
        """First checks returns proper response format."""
        response = auth_client.get("/accounts/first-checks/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data
        # Status is boolean True for success
        assert data["status"] is True

    def test_onboarding_success_response_format(self, auth_client, user):
        """Onboarding success returns proper format."""
        response = auth_client.post(
            "/accounts/onboarding/",
            {"role": "developer", "goals": ["Test"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data
