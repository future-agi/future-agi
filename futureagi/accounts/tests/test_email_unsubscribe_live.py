"""End-to-end unsubscribe over real HTTP (TH-9836).

The e2e stack has no mail inbox, so a Playwright flow can't read an emailed
link. This drives the same journey against a live server: send a real
notification email, take the link from it, open the page, press its button,
check the next send, resubscribe, and use a mail client's one-click POST.
"""

import re
from urllib.parse import unquote

import pytest
import requests
from django.core import mail
from django.test import override_settings

from tfc.utils import email_unsubscribe as eu
from tfc.utils.email import email_helper

pytestmark = pytest.mark.django_db(transaction=True)
# A live server's first request also runs app start-up checks.
TIMEOUT = 60


@pytest.fixture(autouse=True)
def _links_point_at_the_live_server(live_server, monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    with override_settings(FI_API_PUBLIC_URL=live_server.url):
        yield


def _send_integration_error(to):
    mail.outbox = []
    email_helper(
        "Integration Error: Langfuse",
        "integration_error.html",
        {"connection_name": "c", "platform": "Langfuse", "error_message": "x"},
        to,
        notification_category=eu.INTEGRATION_ERRORS,
    )
    return list(mail.outbox)


def _header_link(message):
    return message.extra_headers["List-Unsubscribe"].strip("<>")


def _form(page_html):
    """The page's one button: where it posts, and the action it sends."""
    action_url = re.search(r"<form method='post' action='([^']+)'>", page_html)
    action = re.search(r"name='action' value='([^']+)'", page_html)
    return unquote(action_url.group(1)).replace("&amp;", "&"), action.group(1)


def test_footer_link_unsubscribe_then_resubscribe(user):
    (message,) = _send_integration_error([user.email])
    link = _header_link(message)
    assert link in message.alternatives[0][0]  # the footer link is the same URL

    # Opening the link (what a link scanner does too) changes nothing.
    page = requests.get(link, timeout=TIMEOUT)
    assert page.status_code == 200
    assert "Unsubscribe?" in page.text
    assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    # Pressing the page's button unsubscribes from that category only.
    target, action = _form(page.text)
    assert action == "unsubscribe"
    done = requests.post(target, data={"action": action}, timeout=TIMEOUT)
    assert done.status_code == 200
    assert "Unsubscribed" in done.text
    assert _send_integration_error([user.email]) == []
    assert len(mail.outbox) == 0

    # The result page's Resubscribe brings the emails back.
    target, action = _form(done.text)
    assert action == "resubscribe"
    back = requests.post(target, data={"action": action}, timeout=TIMEOUT)
    assert "Resubscribed" in back.text
    assert len(_send_integration_error([user.email])) == 1


def test_mail_client_one_click_unsubscribe(user):
    (message,) = _send_integration_error([user.email])

    response = requests.post(
        _header_link(message),
        data="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    assert eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)
    assert len(_send_integration_error(["someone-else@example.com"])) == 1


def test_monitor_alert_unsubscribe_removes_only_that_address(monitor):
    from tracer.utils.monitor import _send_alert_email

    mail.outbox = []
    _send_alert_email(monitor, "Errors above threshold", "critical")
    by_recipient = {m.to[0]: m for m in mail.outbox}
    assert set(by_recipient) == {"oncall@example.com", "Lead@Example.com"}

    page = requests.get(
        _header_link(by_recipient["oncall@example.com"]), timeout=TIMEOUT
    )
    target, action = _form(page.text)
    done = requests.post(target, data={"action": action}, timeout=TIMEOUT)

    assert "anyone who can edit this monitor" in done.text
    assert "Resubscribe" not in done.text
    monitor.refresh_from_db()
    assert monitor.notification_emails == ["Lead@Example.com"]
    mail.outbox = []
    _send_alert_email(monitor, "Errors above threshold", "critical")
    assert [m.to for m in mail.outbox] == [["Lead@Example.com"]]
