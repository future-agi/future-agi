"""619s: render real invite emails without advertising an absent password.

The fixture freezes member_invite.html at cad5b8b55f before conditional copy;
rendering both templates against the same includes checks exact output bytes.
Only Django's in-memory email backend is used. Resend exercises the production
handler through the existing DB-free containment harness, not Django dispatch.
"""

import re
import socket
from pathlib import Path

import pytest
from django.core import mail
from django.template import Context, Engine
from django.template.loader import render_to_string
from django.test import override_settings
from django.utils.html import escape

from accounts.tests import test_resend_invite_containment as containment
from tfc.utils.email import email_helper

BEFORE = Path(__file__).with_name("fixtures") / "member_invite_before.html"
CONTEXT = {
    "invited_by": "Synthetic inviter",
    "workspace_name": "Synthetic workspace",
    "org_name": "Synthetic org",
    "email": "invitee@example.invalid",
    "uid": "UID123",
    "token": "TOKEN456",
    "base_url": "https://app.example.invalid",
}
# Match the complete credential chip, not unrelated decorative empty spans.
PASSWORD_SPAN = r"<span\b[^>]*padding:7px 12px;[^>]*>\s*</span>"
ABSENT_MARKERS = (
    "Temporary password",
    ">Password</td>",
    "Rotate this password",
    PASSWORD_SPAN,
)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, containment.deny_network)


@pytest.mark.unit
@pytest.mark.parametrize("password_context", [{}, {"password": ""}, {"password": None}])
@pytest.mark.parametrize("marker", ABSENT_MARKERS)
def test_absent_password_has_no_password_copy_or_empty_chip(password_context, marker):
    rendered = render_to_string("member_invite.html", {**CONTEXT, **password_context})
    assert not re.search(marker, rendered), f"Unexpected password content: {marker}"
    assert CONTEXT["email"] in rendered
    assert (
        'href="https://app.example.invalid/auth/jwt/invitation/set-password/UID123/TOKEN456"'
        in rendered
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "password", ["synthetic-password", "<synthetic&password>\"'", "0"]
)
@pytest.mark.parametrize("complete_context", [True, False])
def test_present_password_renders_byte_identically(password, complete_context):
    context = {**CONTEXT, "password": password}
    if not complete_context:
        # ManageTeamView historically omits inviter/org context; preserve it too.
        context.pop("invited_by")
        context.pop("org_name")
    before = (
        Engine.get_default().from_string(BEFORE.read_text()).render(Context(context))
    )
    after = render_to_string("member_invite.html", context)
    assert after.encode() == before.encode()
    assert "Temporary password inside." in after
    assert ">Password</td>" in after
    assert "Rotate this password from account settings after signing in." in after
    assert escape(password) in after


@pytest.mark.unit
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("own_workspace", [False, True])
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CLOUD_DEPLOYMENT="",
    DEFAULT_FROM_EMAIL="noreply@example.invalid",
    DEFAULT_REPLY_TO_EMAIL="",
    APP_BASE_URL="https://app.example.invalid",
)
def test_resend_invite_renders_no_password_copy(active, own_workspace):
    harness = containment.ResendInviteContainmentTests(
        "test_inactive_invitee_resends_link_and_refreshes_only_own_expiry"
    )
    try:
        harness.setUp()
        harness.target.is_active = active
        if not own_workspace:
            harness.workspace.organization = harness.other_org
        mail.outbox = []
        # Retain call/context assertions while executing the real locmem helper.
        harness.email.side_effect = email_helper
        response = harness.resend()
        assert response.status_code == 200
        harness.assert_identity_untouched()
        harness.email.assert_called_once()
        assert "password" not in harness.email.call_args.args[2]
        (message,) = mail.outbox
        html, _mime = message.alternatives[0]
        for marker in ABSENT_MARKERS:
            assert not re.search(marker, html), f"Unexpected password content: {marker}"
        assert "Password" not in message.body
        assert "Rotate this password" not in message.body
        assert (
            "/auth/jwt/invitation/set-password/dGFyZ2V0/synthetic-invitation-token"
            in html
        )
        assert harness.target.email in html
    finally:
        harness.doCleanups()
