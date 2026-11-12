"""WALK-FORGOT-PASSWORD-ADMIN-TEXT: what a self-hosted install that cannot send
email answers a password-reset request with.

The person who asked is signed out and usually cannot reach the host, so they
get one plain sentence telling them to ask their administrator. The host
recovery steps (the manage.py command, MAILGUN_API_KEY and
OSS_RETURN_PASSWORD_RESET_LINK) are for that administrator and go to the
server log instead. Installs that can mail the link, or that opted in to
returning it, answer as before.
"""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from rest_framework import status

RESET_URL = "/accounts/password-reset-initiate/"
UNKNOWN_EMAIL = "nobody-walk@futureagi.com"
USER_COPY = (
    "This site can't send email, so we couldn't send a reset link. "
    "Ask your administrator to reset your password."
)
# Host-only detail that must never reach a signed-out caller.
ADMIN_ONLY = ("manage.py", "MAILGUN", "OSS_RETURN_PASSWORD_RESET_LINK")
RECOVERY_COMMAND = "reset_password --email <address>"


@pytest.fixture
def no_outbound_email():
    """The reset path mails and tracks; neither may leave the test."""
    with (
        patch("accounts.views.signup.email_helper") as mail,
        patch("accounts.views.signup.track_mixpanel_event"),
    ):
        yield mail


@pytest.fixture
def reset_logger():
    with patch("accounts.views.signup.logger") as logger:
        yield logger


@contextmanager
def _deployment(*, delivery, link_in_response=False, oss=True):
    """Self-hosted or Cloud, with or without email delivery, with or without
    the operator's opt-in to returning the link."""
    opt_in = "true" if link_in_response else "false"
    with (
        patch("accounts.views.signup.is_oss", return_value=oss),
        patch(
            "accounts.views.signup.email_delivery_configured",
            return_value=delivery,
        ),
        patch.dict(os.environ, {"OSS_RETURN_PASSWORD_RESET_LINK": opt_in}),
    ):
        yield


def _request_reset(api_client, email):
    return api_client.post(RESET_URL, {"email": email}, format="json")


def _recovery_warnings(logger):
    """The warnings that carry the host recovery command."""
    return [
        call for call in logger.warning.call_args_list if RECOVERY_COMMAND in str(call)
    ]


@pytest.mark.unit
class TestUnavailableCopy:
    def test_the_requester_is_told_what_happened_and_what_to_do(self):
        from accounts.views.signup import oss_reset_unavailable_message

        assert oss_reset_unavailable_message() == USER_COPY

    @pytest.mark.parametrize("setup", ["standalone", "distributed", "helm"])
    def test_no_setup_puts_host_detail_in_the_copy(self, setup):
        from accounts.views.signup import oss_reset_unavailable_message

        with patch("tfc.utils.install_setup.current_setup", return_value=setup):
            message = oss_reset_unavailable_message()

        for detail in ADMIN_ONLY:
            assert detail not in message
        assert "`" not in message


@pytest.mark.integration
@pytest.mark.api
class TestOssWithoutEmail:
    @pytest.fixture
    def address(self, request, user):
        return user.email if request.param == "known" else UNKNOWN_EMAIL

    @pytest.mark.parametrize("address", ["known", "unknown"], indirect=True)
    def test_the_answer_is_the_plain_sentence_marked_not_sent(
        self, api_client, address, no_outbound_email, reset_logger
    ):
        with _deployment(delivery=False):
            response = _request_reset(api_client, address)

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["message"] == USER_COPY
        assert result["email_unavailable"] is True
        assert "reset_link" not in result
        body = response.content.decode()
        for detail in ADMIN_ONLY:
            assert detail not in body
        no_outbound_email.assert_not_called()

    def test_known_and_unknown_addresses_get_the_same_answer(
        self, api_client, user, no_outbound_email, reset_logger
    ):
        """With the link withheld the endpoint must not tell an anonymous
        caller who has an account here."""
        with _deployment(delivery=False):
            known = _request_reset(api_client, user.email)
            unknown = _request_reset(api_client, UNKNOWN_EMAIL)

        assert known.status_code == unknown.status_code == status.HTTP_200_OK
        assert known.json() == unknown.json()

    @pytest.mark.parametrize("address", ["known", "unknown"], indirect=True)
    def test_the_administrator_gets_the_recovery_steps_in_the_log(
        self, api_client, address, no_outbound_email, reset_logger
    ):
        with _deployment(delivery=False):
            _request_reset(api_client, address)

        warnings = _recovery_warnings(reset_logger)
        assert len(warnings) == 1
        logged = str(warnings[0])
        assert f"manage.py {RECOVERY_COMMAND}" in logged
        assert "MAILGUN_API_KEY" in logged
        assert "OSS_RETURN_PASSWORD_RESET_LINK" in logged

    @pytest.mark.parametrize("address", ["known", "unknown"], indirect=True)
    def test_the_log_names_no_address_and_no_link_is_minted(
        self, api_client, address, user, no_outbound_email, reset_logger
    ):
        from accounts.models.auth_token import AuthToken, AuthTokenType

        with _deployment(delivery=False):
            _request_reset(api_client, address)

        assert address not in str(reset_logger.mock_calls)
        assert not AuthToken.objects.filter(
            user=user, auth_type=AuthTokenType.PASSWORD_RESET.value
        ).exists()


@pytest.mark.integration
@pytest.mark.api
class TestOtherPathsUnchanged:
    def test_with_email_the_link_is_mailed(
        self, api_client, user, no_outbound_email, reset_logger
    ):
        with _deployment(delivery=True):
            response = _request_reset(api_client, user.email)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {
            "message": (
                f"If an account matches {user.email}, you will receive an email "
                "with instructions on how to reset your password shortly."
            )
        }
        no_outbound_email.assert_called_once()
        assert no_outbound_email.call_args.args[1] == "reset_password.html"
        assert _recovery_warnings(reset_logger) == []

    def test_with_the_opt_in_the_link_is_returned(
        self, api_client, user, no_outbound_email, reset_logger
    ):
        with _deployment(delivery=False, link_in_response=True):
            response = _request_reset(api_client, user.email)

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert set(result) == {"message", "reset_link"}
        assert result["message"] == "Use the link below to reset your password."
        no_outbound_email.assert_not_called()
        assert _recovery_warnings(reset_logger) == []

    def test_cloud_mails_the_link(
        self, api_client, user, no_outbound_email, reset_logger
    ):
        with _deployment(delivery=False, oss=False):
            response = _request_reset(api_client, user.email)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.json()["result"]) == {"message"}
        no_outbound_email.assert_called_once()
        assert _recovery_warnings(reset_logger) == []
