"""Sender, Reply-To, links and delivery detection of app emails.

A self-hosted install sends through the operator's own mail domain. Its
messages must not claim a futureagi.com sender, which fails DMARC there, and
must not point replies at Future AGI's support inbox, since a reply quotes the
invite link or generated password it answers. No database or network needed:
the test settings' locmem backend keeps what was sent.
"""

import pytest
from django.core import mail
from django.test import override_settings

from tfc.utils.email import (
    DEFAULT_FROM_EMAIL,
    DEFAULT_REPLY_TO,
    email_delivery_configured,
    email_helper,
)

SELF_HOSTED = {"CLOUD_DEPLOYMENT": "", "DEFAULT_REPLY_TO_EMAIL": ""}
MAILGUN_DOMAIN = {
    "MAILGUN_API_KEY": "key-test",
    "MAILGUN_SENDER_DOMAIN": "mg.example.com",
}


def _send(**kwargs):
    mail.outbox = []
    email_helper(
        "Reset Password",
        "reset_password.html",
        {"uid": "UID123", "token": "TOKEN456"},
        ["ada@example.com"],
        **kwargs,
    )
    (message,) = mail.outbox
    return message


@pytest.mark.unit
class TestSender:
    @override_settings(
        **SELF_HOSTED, DEFAULT_FROM_EMAIL="Acme AI <noreply@mg.example.com>"
    )
    def test_self_hosted_sends_as_default_from_email(self):
        headers = _send().message()

        assert headers["From"] == "Acme AI <noreply@mg.example.com>"
        assert headers["Reply-To"] is None

    @override_settings(**SELF_HOSTED, DEFAULT_FROM_EMAIL=None, ANYMAIL=MAILGUN_DOMAIN)
    def test_self_hosted_without_one_sends_from_the_mailgun_domain(self):
        assert _send().from_email == "Future AGI <noreply@mg.example.com>"

    @pytest.mark.parametrize(
        "configured", [None, "noreply@mail.futureagi.com"], ids=["unset", "bare"]
    )
    def test_cloud_keeps_its_own_sender(self, configured):
        # Cloud's values set DEFAULT_FROM_EMAIL to the bare address; mail must
        # still show the "Future AGI" display name.
        with override_settings(CLOUD_DEPLOYMENT="US", DEFAULT_FROM_EMAIL=configured):
            assert _send().from_email == DEFAULT_FROM_EMAIL

    @override_settings(**SELF_HOSTED, DEFAULT_FROM_EMAIL="noreply@mg.example.com")
    def test_an_explicit_sender_wins(self):
        assert _send(from_email="billing@example.com").from_email == (
            "billing@example.com"
        )


@pytest.mark.unit
class TestReplyTo:
    @override_settings(**SELF_HOSTED, DEFAULT_FROM_EMAIL="noreply@mg.example.com")
    def test_self_hosted_sets_no_reply_to(self):
        message = _send()

        assert message.reply_to == []
        assert "Reply-To" not in message.message()

    @override_settings(
        CLOUD_DEPLOYMENT="",
        DEFAULT_FROM_EMAIL="noreply@mg.example.com",
        DEFAULT_REPLY_TO_EMAIL="help@example.com",
    )
    def test_self_hosted_uses_default_reply_to_email(self):
        assert _send().reply_to == ["help@example.com"]

    @override_settings(CLOUD_DEPLOYMENT="US", DEFAULT_REPLY_TO_EMAIL="")
    def test_cloud_replies_go_to_support(self):
        assert _send().reply_to == [DEFAULT_REPLY_TO]

    @override_settings(CLOUD_DEPLOYMENT="US", DEFAULT_REPLY_TO_EMAIL="")
    def test_reply_to_can_be_turned_off_per_message(self):
        assert _send(reply_to=False).reply_to == []


@pytest.mark.unit
class TestLinks:
    """The templates build their buttons from ``base_url``, which carries a
    scheme; ``app_url`` is a bare host and made links that do not open."""

    @pytest.mark.parametrize(
        "template, path",
        [
            ("reset_password.html", "/auth/jwt/verify/UID123/TOKEN456"),
            ("verify_email.html", "/auth/verify/UID123/TOKEN456"),
            (
                "member_invite.html",
                "/auth/jwt/invitation/set-password/UID123/TOKEN456",
            ),
            ("existing_user_invite.html", "/dashboard"),
            ("user_onboard.html", "/dashboard"),
            ("send_credentials.html", "/auth/jwt/login"),
        ],
    )
    @override_settings(
        **SELF_HOSTED,
        DEFAULT_FROM_EMAIL="noreply@mg.example.com",
        APP_BASE_URL="http://localhost:3000",
    )
    def test_buttons_link_to_an_absolute_url(self, template, path):
        mail.outbox = []
        email_helper(
            "Subject",
            template,
            {"uid": "UID123", "token": "TOKEN456", "app_url": "localhost:3000"},
            ["ada@example.com"],
        )
        (html, _mime) = mail.outbox[0].alternatives[0]

        assert f'href="http://localhost:3000{path}' in html


@pytest.mark.unit
class TestEmailDeliveryConfigured:
    @pytest.mark.parametrize(
        "backend, configured",
        [
            ("django.core.mail.backends.console.EmailBackend", False),
            ("django.core.mail.backends.dummy.EmailBackend", False),
            ("", False),
            ("anymail.backends.mailgun.EmailBackend", True),
            ("django.core.mail.backends.smtp.EmailBackend", True),
            ("django.core.mail.backends.locmem.EmailBackend", True),
        ],
    )
    def test_backend(self, backend, configured):
        with override_settings(EMAIL_BACKEND=backend):
            assert email_delivery_configured() is configured
