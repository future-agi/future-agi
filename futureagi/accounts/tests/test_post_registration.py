"""
Tests for post-registration functionality in accounts/utils.py.

Run with: pytest accounts/tests/test_post_registration.py -v

Fixes CORE-BACKEND-YRG - HubSpot 404 error when updating non-existent contact
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests


class TestProcessPostRegistration:
    """Tests for process_post_registration function."""

    @patch("tfc.temporal.drop_in.start_activity")
    def test_calls_start_activity_with_correct_args(self, mock_start_activity):
        """Test that process_post_registration calls start_activity correctly."""
        from accounts.utils import process_post_registration

        process_post_registration("user-123", "securePassword123")

        mock_start_activity.assert_called_once_with(
            "run_post_registration_activity",
            args=("user-123", "securePassword123"),
            queue="default",
        )

    @patch("tfc.temporal.drop_in.start_activity")
    def test_handles_uuid_user_id(self, mock_start_activity):
        """Test that function handles UUID-style user IDs."""
        from accounts.utils import process_post_registration

        process_post_registration("550e8400-e29b-41d4-a716-446655440000", "password")

        mock_start_activity.assert_called_once()
        call_args = mock_start_activity.call_args
        assert call_args[1]["args"][0] == "550e8400-e29b-41d4-a716-446655440000"


class TestRunPostRegistration:
    """Tests for _run_post_registration implementation function."""

    @patch("accounts.utils.send_slack_notification")
    @patch("accounts.utils.send_hubspot_notification")
    @patch("accounts.utils.send_signup_email")
    @patch("accounts.models.User.objects.get")
    @patch.dict("os.environ", {"ENV_TYPE": "staging"})
    def test_executes_all_post_registration_steps(
        self,
        mock_user_get,
        mock_send_email,
        mock_hubspot,
        mock_slack,
    ):
        """Test that all post-registration steps are executed."""
        from accounts.utils import _run_post_registration

        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user_get.return_value = mock_user

        # send_hubspot_notification returns (updated, err) tuple
        mock_hubspot.return_value = (True, None)

        _run_post_registration("user-123", "password123")

        mock_user_get.assert_called_once_with(id="user-123")
        mock_send_email.assert_called_once()
        mock_hubspot.assert_called_once()
        mock_slack.assert_called_once()

    @patch("accounts.models.User.objects.get")
    def test_raises_on_user_not_found(self, mock_user_get):
        """Test that function raises when user not found (no error handling)."""
        from accounts.utils import _run_post_registration

        mock_user_get.side_effect = Exception("User not found")

        # Function does not have try/except, so exception propagates
        with pytest.raises(Exception, match="User not found"):
            _run_post_registration("nonexistent-user", "password")

    @patch("accounts.utils.send_signup_email")
    @patch("accounts.models.User.objects.get")
    def test_raises_on_email_error(self, mock_user_get, mock_send_email):
        """Test that function raises on email error (no error handling)."""
        from accounts.utils import _run_post_registration

        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user_get.return_value = mock_user
        mock_send_email.side_effect = Exception("SMTP error")

        # Function does not have try/except, so exception propagates
        with pytest.raises(Exception, match="SMTP error"):
            _run_post_registration("user-123", "password")

    @patch("accounts.utils.send_signup_email")
    @patch("accounts.models.User.objects.get")
    @patch.dict("os.environ", {"ENV_TYPE": "local"})
    def test_skips_hubspot_slack_in_local_env(
        self,
        mock_user_get,
        mock_send_email,
    ):
        """Test that hubspot/slack notifications are skipped in local env."""
        from accounts.utils import _run_post_registration

        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user_get.return_value = mock_user

        with (
            patch("accounts.utils.send_hubspot_notification") as mock_hubspot,
            patch("accounts.utils.send_slack_notification") as mock_slack,
        ):
            _run_post_registration("user-123", "password")

            # These should NOT be called in local environment
            mock_hubspot.assert_not_called()
            mock_slack.assert_not_called()

        mock_send_email.assert_called_once()


@pytest.mark.django_db
class TestTemporalActivityIntegration:
    """Integration tests for Temporal activity registration."""

    def test_activity_is_registered(self):
        """Test that run_post_registration_activity is registered."""
        # Import activities to register them
        import tfc.temporal.background_tasks.activities  # noqa: F401
        from tfc.temporal.drop_in import get_activity_by_name

        activity = get_activity_by_name("run_post_registration_activity")
        assert activity is not None

    @patch("accounts.utils._run_post_registration")
    def test_activity_calls_implementation(self, mock_impl):
        """Test that the activity calls the implementation function."""
        from tfc.temporal.background_tasks.activities import (
            run_post_registration_activity,
        )

        mock_impl.return_value = {"status": "success"}

        result = run_post_registration_activity("user-123", "password")

        mock_impl.assert_called_once_with("user-123", "password")
        assert result == {"status": "success"}


class TestSendHubspotNotification:
    """
    Tests for send_hubspot_notification function.

    Fixes CORE-BACKEND-YRG - The function was attempting to UPDATE contacts
    even when CREATE failed for reasons other than 'contact already exists'.
    This caused 404 errors when trying to update non-existent contacts.
    """

    @patch("accounts.utils.settings")
    @patch("accounts.utils.requests.post")
    def test_create_contact_success(self, mock_post, mock_settings):
        """Test successful contact creation in HubSpot."""
        from accounts.utils import send_hubspot_notification

        mock_settings.HUBSPOT_API_TOKEN = "test-token"
        mock_settings.HUBSPOT_URL = "https://api.hubapi.com/crm/v3/objects/contacts"

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "123"}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        mock_user = Mock()
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.organization = Mock()
        mock_user.organization.display_name = "Test Company"
        mock_user.organization.name = "test-company"
        mock_user.organization_role = "owner"

        updated, err = send_hubspot_notification(mock_user)

        assert updated is True
        assert err is None
        mock_post.assert_called_once()

    @patch("accounts.utils.settings")
    @patch("accounts.utils.requests.patch")
    @patch("accounts.utils.requests.post")
    def test_update_contact_when_already_exists(
        self, mock_post, mock_patch, mock_settings
    ):
        """Test that UPDATE is attempted when CREATE fails with 409 (contact exists)."""
        from accounts.utils import send_hubspot_notification

        mock_settings.HUBSPOT_API_TOKEN = "test-token"
        mock_settings.HUBSPOT_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
        mock_settings.HUBSPOT_UPDATE_URL = (
            "https://api.hubapi.com/crm/v3/objects/contacts/{}?idProperty=email"
        )

        # POST fails with 409 Conflict (contact already exists)
        mock_post_response = Mock()
        mock_post_response.status_code = 409
        mock_post_response.json.return_value = {
            "status": "error",
            "message": "Contact already exists",
        }
        mock_post_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "409 Client Error: Conflict"
        )
        mock_post.return_value = mock_post_response

        # PATCH succeeds
        mock_patch_response = Mock()
        mock_patch_response.status_code = 200
        mock_patch_response.json.return_value = {"id": "123"}
        mock_patch_response.raise_for_status = Mock()
        mock_patch.return_value = mock_patch_response

        mock_user = Mock()
        mock_user.email = "existing@example.com"
        mock_user.name = "Existing User"
        mock_user.organization = Mock()
        mock_user.organization.display_name = "Existing Company"
        mock_user.organization.name = "existing-company"
        mock_user.organization_role = "owner"

        updated, err = send_hubspot_notification(mock_user)

        assert updated is True
        assert err is None
        mock_post.assert_called_once()
        mock_patch.assert_called_once()

    @patch("accounts.utils.settings")
    @patch("accounts.utils.requests.patch")
    @patch("accounts.utils.requests.post")
    def test_no_update_attempt_on_non_409_error(
        self, mock_post, mock_patch, mock_settings
    ):
        """
        Test that UPDATE is NOT attempted when CREATE fails with non-409 error.

        This is the fix for CORE-BACKEND-YRG - previously the code would try
        to UPDATE even on 400/500 errors, causing 404 when contact doesn't exist.
        """
        from accounts.utils import send_hubspot_notification

        mock_settings.HUBSPOT_API_TOKEN = "test-token"
        mock_settings.HUBSPOT_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
        mock_settings.HUBSPOT_UPDATE_URL = (
            "https://api.hubapi.com/crm/v3/objects/contacts/{}?idProperty=email"
        )

        # POST fails with 400 Bad Request (not 409)
        mock_post_response = Mock()
        mock_post_response.status_code = 400
        mock_post_response.json.return_value = {
            "status": "error",
            "message": "Invalid email format",
        }
        mock_post_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "400 Client Error: Bad Request"
        )
        mock_post.return_value = mock_post_response

        mock_user = Mock()
        mock_user.email = "invalid-email"
        mock_user.name = "Test User"
        mock_user.organization = Mock()
        mock_user.organization.display_name = "Test Company"
        mock_user.organization.name = "test-company"
        mock_user.organization_role = "owner"

        updated, err = send_hubspot_notification(mock_user)

        assert updated is False
        assert err is not None
        assert "Create failed" in err
        mock_post.assert_called_once()
        # PATCH should NOT be called for non-409 errors
        mock_patch.assert_not_called()

    @patch("accounts.utils.settings")
    @patch("accounts.utils.requests.patch")
    @patch("accounts.utils.requests.post")
    def test_no_update_attempt_on_500_error(self, mock_post, mock_patch, mock_settings):
        """Test that UPDATE is NOT attempted when CREATE fails with 500 error."""
        from accounts.utils import send_hubspot_notification

        mock_settings.HUBSPOT_API_TOKEN = "test-token"
        mock_settings.HUBSPOT_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
        mock_settings.HUBSPOT_UPDATE_URL = (
            "https://api.hubapi.com/crm/v3/objects/contacts/{}?idProperty=email"
        )

        # POST fails with 500 Internal Server Error
        mock_post_response = Mock()
        mock_post_response.status_code = 500
        mock_post_response.json.return_value = {
            "status": "error",
            "message": "Internal server error",
        }
        mock_post_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error: Internal Server Error"
        )
        mock_post.return_value = mock_post_response

        mock_user = Mock()
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.organization = Mock()
        mock_user.organization.display_name = "Test Company"
        mock_user.organization.name = "test-company"
        mock_user.organization_role = "owner"

        updated, err = send_hubspot_notification(mock_user)

        assert updated is False
        assert err is not None
        mock_post.assert_called_once()
        # PATCH should NOT be called for 500 errors
        mock_patch.assert_not_called()

    @patch("accounts.utils.mixpanel_slack_notfy")
    @patch("accounts.utils.settings")
    @patch("accounts.utils.requests.patch")
    @patch("accounts.utils.requests.post")
    def test_update_fails_after_409_logs_error(
        self, mock_post, mock_patch, mock_settings, mock_slack
    ):
        """Test that failed UPDATE after 409 logs error and notifies Slack."""
        from accounts.utils import send_hubspot_notification

        mock_settings.HUBSPOT_API_TOKEN = "test-token"
        mock_settings.HUBSPOT_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
        mock_settings.HUBSPOT_UPDATE_URL = (
            "https://api.hubapi.com/crm/v3/objects/contacts/{}?idProperty=email"
        )

        # POST fails with 409
        mock_post_response = Mock()
        mock_post_response.status_code = 409
        mock_post_response.json.return_value = {"message": "Contact exists"}
        mock_post_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "409 Conflict"
        )
        mock_post.return_value = mock_post_response

        # PATCH also fails with 404 (shouldn't happen with fix, but test the path)
        mock_patch_response = Mock()
        mock_patch_response.status_code = 404
        mock_patch_response.json.return_value = {"message": "Not found"}
        mock_patch_response.raise_for_status.side_effect = (
            requests.exceptions.HTTPError("404 Not Found")
        )
        mock_patch.return_value = mock_patch_response

        mock_user = Mock()
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.organization = Mock()
        mock_user.organization.display_name = "Test Company"
        mock_user.organization.name = "test-company"
        mock_user.organization_role = "owner"

        updated, err = send_hubspot_notification(mock_user)

        assert updated is False
        assert err is not None
        assert "404" in err or "Not Found" in err or "Not found" in err
        mock_slack.assert_called_once()
