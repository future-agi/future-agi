"""One-click unsubscribe for notification emails (TH-9836).

Notification mail (digests, discussion, rule runs, integration errors,
monitor alerts) carries RFC 8058 ``List-Unsubscribe`` headers and a footer
link; transactional mail carries neither. A GET on the link only shows a
confirmation page, since link scanners prefetch URLs; a POST unsubscribes,
or resubscribes with ``action=resubscribe``.
"""

import re
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.test import override_settings

from model_hub.models.annotation_queues import AnnotationNotificationState
from tfc.utils import email_unsubscribe as eu
from tfc.utils.email import email_helper

URL = "/accounts/notifications/email/unsubscribe/"


def _send(to, category=None, scope=None, template="integration_error.html"):
    mail.outbox = []
    email_helper(
        "Integration Error: Langfuse",
        template,
        {"connection_name": "c", "platform": "Langfuse", "error_message": "x"},
        to,
        notification_category=category,
        unsubscribe_scope=scope,
    )
    return list(mail.outbox)


def _token_from(message):
    header = message.extra_headers["List-Unsubscribe"]
    return parse_qs(urlparse(header.strip("<>")).query)["token"][0]


@pytest.fixture
def monitor(db, organization, workspace):
    from model_hub.models.ai_model import AIModel
    from tracer.models.monitor import UserAlertMonitor
    from tracer.models.project import Project

    project = Project.objects.create(
        name="Unsub Observe",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    return UserAlertMonitor.objects.create(
        organization=organization,
        workspace=workspace,
        project=project,
        name="Errors",
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=0.1,
        alert_frequency=60,
        notification_emails=["oncall@example.com", "Lead@Example.com"],
    )


@pytest.mark.django_db
class TestHeaders:
    @override_settings(BASE_URL="https://api.acme.test")
    def test_notification_mail_carries_one_click_headers_and_footer(self, user):
        (message,) = _send([user.email], eu.INTEGRATION_ERRORS)

        header = message.extra_headers["List-Unsubscribe"]
        assert header.startswith(f"<https://api.acme.test{URL}?token=")
        assert (
            message.extra_headers["List-Unsubscribe-Post"]
            == "List-Unsubscribe=One-Click"
        )
        html = message.alternatives[0][0]
        assert header.strip("<>") in html
        assert "Unsubscribe</a>" in html

    def test_transactional_mail_has_no_unsubscribe(self, user):
        (message,) = _send([user.email], category=None)

        assert "List-Unsubscribe" not in message.extra_headers
        assert "List-Unsubscribe-Post" not in message.extra_headers
        assert "Unsubscribe</a>" not in message.alternatives[0][0]

    def test_shared_notification_goes_out_one_message_per_recipient(self, user):
        messages = _send([user.email, "other@example.com"], eu.INTEGRATION_ERRORS)

        assert [m.to for m in messages] == [[user.email], ["other@example.com"]]
        assert _token_from(messages[0]) != _token_from(messages[1])

    def test_one_failed_recipient_does_not_stop_or_fail_the_rest(self, user):
        from tfc.utils import email as email_module

        real_send = email_module._send

        def flaky(subject, template, context, to, *args, **kwargs):
            if to == ["bounce@example.com"]:
                raise RuntimeError("mailgun 5xx")
            return real_send(subject, template, context, to, *args, **kwargs)

        with mock.patch.object(email_module, "_send", side_effect=flaky):
            messages = _send(["bounce@example.com", user.email], eu.INTEGRATION_ERRORS)

        assert [m.to for m in messages] == [[user.email]]

    def test_raises_when_no_recipient_was_reached(self, user):
        from tfc.utils import email as email_module

        with mock.patch.object(email_module, "_send", side_effect=RuntimeError("down")):
            with pytest.raises(RuntimeError):
                _send([user.email], eu.INTEGRATION_ERRORS)

    def test_digest_template_shows_one_unsubscribe_link(self, user):
        mail.outbox = []
        email_helper(
            "Your annotation queue — 2 items pending",
            "annotation_daily_digest.html",
            {
                "user_name": "Ada",
                "annotator_total": 2,
                "reviewer_total": 0,
                "annotator_buckets": [],
                "reviewer_buckets": [],
                "queues_url": "https://app.test/q",
            },
            [user.email],
            notification_category=eu.ANNOTATION_DIGEST,
        )
        html = mail.outbox[0].alternatives[0][0]
        assert html.count("Unsubscribe</a>") == 1
        assert _token_from(mail.outbox[0]) in html


@pytest.mark.django_db
class TestOneClick:
    def test_get_shows_confirmation_and_changes_nothing(self, api_client, user):
        token = eu.make_token(user.email, eu.INTEGRATION_ERRORS)

        response = api_client.get(URL, {"token": token})

        assert response.status_code == 200
        assert b"Unsubscribe?" in response.content
        assert b"integration error emails" in response.content
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_rfc8058_post_unsubscribes_only_that_category(self, api_client, user):
        token = eu.make_token(user.email, eu.INTEGRATION_ERRORS)

        response = api_client.post(
            f"{URL}?token={token}",
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        assert b"Unsubscribed" in response.content
        assert b"Resubscribe" in response.content
        assert eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)
        assert not eu.is_opted_out(user.email, eu.ANNOTATION_DISCUSSION)

    def test_opted_out_address_gets_other_categories_and_transactional(self, user):
        eu.set_subscribed(user.email, eu.INTEGRATION_ERRORS, None, False)

        assert _send([user.email], eu.INTEGRATION_ERRORS) == []
        assert len(_send([user.email], eu.ANNOTATION_DISCUSSION)) == 1
        assert len(_send([user.email], category=None)) == 1

    def test_resubscribe_restores_the_category(self, api_client, user):
        eu.set_subscribed(user.email, eu.INTEGRATION_ERRORS, None, False)
        token = eu.make_token(user.email, eu.INTEGRATION_ERRORS)

        response = api_client.post(
            f"{URL}?token={token}", {"action": "resubscribe"}, format="multipart"
        )

        assert response.status_code == 200
        assert b"Resubscribed" in response.content
        assert len(_send([user.email], eu.INTEGRATION_ERRORS)) == 1

    def test_user_config_keeps_its_other_keys(self, user):
        user.config = {"currentOrganizationId": "org-1"}
        user.save(update_fields=["config"])

        eu.set_subscribed(user.email, eu.AUTOMATION_RULE_RUNS, None, False)

        user.refresh_from_db()
        assert user.config == {
            "currentOrganizationId": "org-1",
            eu.USER_CONFIG_KEY: [eu.AUTOMATION_RULE_RUNS],
        }

    def test_digest_category_uses_the_digest_flag(self, api_client, user):
        token = eu.make_token(user.email, eu.ANNOTATION_DIGEST)

        api_client.post(f"{URL}?token={token}", {}, format="multipart")

        state = AnnotationNotificationState.objects.get(user=user)
        assert state.digest_enabled is False

    @pytest.mark.parametrize(
        "token",
        ["", "garbage", "eyJlIjoiYUBiLmMiLCJjIjoibW9uaXRvcl9hbGVydHMifQ:bad"],
        ids=["empty", "garbage", "forged"],
    )
    def test_invalid_token_changes_nothing(self, api_client, user, token):
        assert b"Link invalid" in api_client.get(URL, {"token": token}).content
        response = api_client.post(f"{URL}?token={token}", {}, format="multipart")
        assert response.status_code == 400
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_unknown_action_is_rejected(self, api_client, user):
        token = eu.make_token(user.email, eu.INTEGRATION_ERRORS)

        response = api_client.post(
            f"{URL}?token={token}", {"action": "delete"}, format="multipart"
        )

        assert response.status_code == 400
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_token_cannot_be_retargeted(self, user):
        token = eu.make_token(user.email, eu.INTEGRATION_ERRORS)
        tampered = token.replace(token[2], "A" if token[2] != "A" else "B", 1)

        assert eu.read_token(tampered) is None


@pytest.mark.django_db
class TestMonitorAlerts:
    def test_unsubscribe_removes_address_from_that_monitor(self, api_client, monitor):
        token = eu.make_token("lead@example.com", eu.MONITOR_ALERTS, str(monitor.id))

        response = api_client.post(f"{URL}?token={token}", {}, format="multipart")

        assert response.status_code == 200
        monitor.refresh_from_db()
        assert monitor.notification_emails == ["oncall@example.com"]

        undo = re.search(rb"name='undo' value='([^']+)'", response.content)
        api_client.post(
            f"{URL}?token={token}",
            {"action": "resubscribe", "undo": undo.group(1).decode()},
            format="multipart",
        )
        monitor.refresh_from_db()
        assert sorted(monitor.notification_emails) == [
            "lead@example.com",
            "oncall@example.com",
        ]

    def test_old_link_cannot_put_a_removed_address_back(self, api_client, monitor):
        # The owner removed lead@ in the UI; lead@ still has an old alert email.
        monitor.notification_emails = ["oncall@example.com"]
        monitor.save(update_fields=["notification_emails"])
        token = eu.make_token("lead@example.com", eu.MONITOR_ALERTS, str(monitor.id))

        page = api_client.get(URL, {"token": token})
        response = api_client.post(
            f"{URL}?token={token}", {"action": "resubscribe"}, format="multipart"
        )

        assert b"Resubscribe" not in page.content
        assert b"Can't resubscribe" in response.content
        monitor.refresh_from_db()
        assert monitor.notification_emails == ["oncall@example.com"]

    def test_undo_respects_the_recipient_cap(self, api_client, monitor):
        token = eu.make_token("lead@example.com", eu.MONITOR_ALERTS, str(monitor.id))
        api_client.post(f"{URL}?token={token}", {}, format="multipart")
        monitor.notification_emails = [f"r{i}@example.com" for i in range(5)]
        monitor.save(update_fields=["notification_emails"])

        response = api_client.post(
            f"{URL}?token={token}",
            {
                "action": "resubscribe",
                "undo": eu.make_undo_token("lead@example.com", str(monitor.id)),
            },
            format="multipart",
        )

        assert b"most recipients" in response.content
        monitor.refresh_from_db()
        assert len(monitor.notification_emails) == 5

    def test_alert_email_carries_a_monitor_scoped_link(self, monitor):
        from tracer.utils.monitor import _send_alert_email

        mail.outbox = []
        _send_alert_email(monitor, "3 errors", "critical")

        assert [m.to for m in mail.outbox] == [
            ["oncall@example.com"],
            ["Lead@Example.com"],
        ]
        email, category, scope = eu.read_token(_token_from(mail.outbox[0]))
        assert (email, category, scope) == (
            "oncall@example.com",
            eu.MONITOR_ALERTS,
            str(monitor.id),
        )

    def test_monitor_token_without_scope_is_rejected(self):
        assert eu.read_token(eu.make_token("a@b.c", eu.MONITOR_ALERTS)) is None

    def test_deleted_monitor_has_nothing_to_change(self, api_client, monitor):
        token = eu.make_token("oncall@example.com", eu.MONITOR_ALERTS, str(monitor.id))
        monitor.deleted = True
        monitor.save(update_fields=["deleted"])

        response = api_client.post(f"{URL}?token={token}", {}, format="multipart")

        assert b"Nothing to change" in response.content


@pytest.mark.django_db
def test_legacy_digest_link_shows_confirmation_instead_of_acting(api_client, user):
    from model_hub.utils.annotation_digest import _unsubscribe_token

    response = api_client.get(
        "/accounts/notifications/unsubscribe/",
        {"token": _unsubscribe_token(user.id)},
    )

    assert response.status_code == 200
    assert b"Unsubscribe?" in response.content
    assert URL.encode() in response.content
    state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
    assert state.digest_enabled is True
