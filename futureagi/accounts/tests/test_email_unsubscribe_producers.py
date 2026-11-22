"""Each notification email names its own category (TH-9836).

Rick's review asked for the category wiring to be checked at the real
senders, not only at the shared helper. Each test calls the real sender, with
the real email helper and template, and reads the category back out of the
signed link in the delivered email.
"""

from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail

from tfc.utils import email_unsubscribe as eu


@pytest.fixture(autouse=True)
def _clean_outbox(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    mail.outbox = []


def _link_in(message):
    header = message.extra_headers["List-Unsubscribe"]
    token = parse_qs(urlparse(header.strip("<>")).query)["token"][0]
    link = eu.read_token(token)
    assert link is not None
    assert token in message.alternatives[0][0]  # the footer link is the same
    return link


@pytest.mark.django_db
def test_integration_error_alert_is_an_integration_errors_email(user):
    from integrations.temporal.activities import _send_error_notification

    connection = SimpleNamespace(
        organization=user.organization,
        display_name="Langfuse prod",
        get_platform_display=lambda: "Langfuse",
        status_message="Auth failed",
    )

    _send_error_notification(connection)

    (message,) = mail.outbox
    link = _link_in(message)
    assert link.category == eu.INTEGRATION_ERRORS
    assert link.user_id == str(user.pk)


@pytest.mark.django_db
def test_rule_run_email_is_an_automation_rule_runs_email(user):
    from model_hub.utils import annotation_queue_helpers as helpers

    rule = SimpleNamespace(
        pk=1,
        name="Low scores",
        source_type="trace",
        queue=SimpleNamespace(id="q-1", name="Review queue"),
    )
    with mock.patch.object(
        helpers, "_rule_completion_recipients", return_value=[user.email]
    ):
        helpers.send_rule_completion_email(rule, {"matched": 3, "added": 2})

    (message,) = mail.outbox
    assert _link_in(message).category == eu.AUTOMATION_RULE_RUNS


@pytest.mark.django_db
def test_discussion_email_is_an_annotation_discussion_email(user):
    from model_hub.views import annotation_queues as views

    item = SimpleNamespace(
        id="item-1", queue_id="q-1", queue=SimpleNamespace(name="Review queue")
    )
    comment = SimpleNamespace(
        id="comment-1",
        reviewer=SimpleNamespace(name="Ada"),
        action="comment",
        label_id=None,
        target_annotator_id=None,
        comment="Please recheck the label",
    )
    with (
        mock.patch.object(views, "_source_label", return_value="trace item-1"),
        mock.patch.object(views, "_annotation_discussion_url", return_value="/q"),
    ):
        views._send_annotation_discussion_email(
            item=item, comment=comment, thread=None, recipient_emails=[user.email]
        )

    (message,) = mail.outbox
    assert _link_in(message).category == eu.ANNOTATION_DISCUSSION


@pytest.mark.django_db
def test_monitor_alert_is_a_monitor_alerts_email_for_that_monitor(monitor):
    from tracer.utils.monitor import _send_alert_email

    _send_alert_email(monitor, "Errors above threshold", "critical")

    links = [_link_in(message) for message in mail.outbox]
    assert {link.category for link in links} == {eu.MONITOR_ALERTS}
    assert {link.scope for link in links} == {str(monitor.id)}
