"""One-click unsubscribe for notification emails (TH-9836).

Notification mail (digests, discussion, rule runs, integration errors,
monitor alerts) carries RFC 8058 ``List-Unsubscribe`` headers and a footer
link; transactional mail carries neither. A GET on the link only shows a
confirmation page, since link scanners prefetch URLs; a POST unsubscribes,
or resubscribes with ``action=resubscribe``.
"""

import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.test import override_settings

from model_hub.models.annotation_queues import AnnotationNotificationState
from tfc.utils import email_unsubscribe as eu
from tfc.utils.email import email_helper

URL = "/accounts/notifications/email/unsubscribe/"
# The body a mail client sends with an RFC 8058 one-click unsubscribe.
ONE_CLICK = {"List-Unsubscribe": "One-Click"}


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


def _link(user, category):
    return eu.Link(user.email.lower(), category, None, str(user.pk))


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
    @override_settings(FI_API_PUBLIC_URL="https://api.acme.test")
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
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

        response = api_client.get(URL, {"token": token})

        assert response.status_code == 200
        assert b"Unsubscribe?" in response.content
        assert b"integration error emails" in response.content
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_rfc8058_post_unsubscribes_only_that_category(self, api_client, user):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

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
        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), False)

        assert _send([user.email], eu.INTEGRATION_ERRORS) == []
        assert len(_send([user.email], eu.ANNOTATION_DISCUSSION)) == 1
        assert len(_send([user.email], category=None)) == 1

    def test_resubscribe_restores_the_category(self, api_client, user):
        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), False)
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

        response = api_client.post(
            f"{URL}?token={token}", {"action": "resubscribe"}, format="multipart"
        )

        assert response.status_code == 200
        assert b"Resubscribed" in response.content
        assert len(_send([user.email], eu.INTEGRATION_ERRORS)) == 1

    def test_user_config_keeps_its_other_keys(self, user):
        user.config = {"currentOrganizationId": "org-1"}
        user.save(update_fields=["config"])

        eu.set_subscribed(_link(user, eu.AUTOMATION_RULE_RUNS), False)

        user.refresh_from_db()
        assert user.config == {
            "currentOrganizationId": "org-1",
            eu.USER_CONFIG_KEY: [eu.AUTOMATION_RULE_RUNS],
        }

    def test_digest_category_uses_the_digest_flag(self, api_client, user):
        token = eu.make_token(*_link(user, eu.ANNOTATION_DIGEST))

        api_client.post(f"{URL}?token={token}", ONE_CLICK, format="multipart")

        state = AnnotationNotificationState.objects.get(user=user)
        assert state.digest_enabled is False

    @pytest.mark.parametrize(
        "token",
        ["", "garbage", "eyJlIjoiYUBiLmMiLCJjIjoibW9uaXRvcl9hbGVydHMifQ:bad"],
        ids=["empty", "garbage", "forged"],
    )
    def test_invalid_token_changes_nothing(self, api_client, user, token):
        assert b"Link invalid" in api_client.get(URL, {"token": token}).content
        response = api_client.post(
            f"{URL}?token={token}", ONE_CLICK, format="multipart"
        )
        assert response.status_code == 400
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_unknown_action_is_rejected(self, api_client, user):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

        response = api_client.post(
            f"{URL}?token={token}", {"action": "delete"}, format="multipart"
        )

        assert response.status_code == 400
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_token_cannot_be_retargeted(self, user):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))
        tampered = token.replace(token[2], "A" if token[2] != "A" else "B", 1)

        assert eu.read_token(tampered) is None

    @pytest.mark.parametrize("days, works", [(29, True), (31, False)])
    def test_link_works_for_30_days(self, api_client, user, days, works):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))
        later = time.time() + days * 86400

        with mock.patch("django.core.signing.time.time", return_value=later):
            response = api_client.post(
                f"{URL}?token={token}", ONE_CLICK, format="multipart"
            )
            page = api_client.get(URL, {"token": token}).content

        assert eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS) is works
        assert response.status_code == (200 if works else 400)
        assert (b"older than 30 days" in page) is not works


@pytest.mark.django_db
class TestMonitorAlerts:
    def test_unsubscribe_removes_address_from_that_monitor(self, api_client, monitor):
        token = eu.make_token("lead@example.com", eu.MONITOR_ALERTS, str(monitor.id))

        response = api_client.post(
            f"{URL}?token={token}", ONE_CLICK, format="multipart"
        )

        assert response.status_code == 200
        monitor.refresh_from_db()
        assert monitor.notification_emails == ["oncall@example.com"]
        # Getting alerts back is the monitor editors' call, not the link's.
        assert b"Resubscribe" not in response.content
        assert b"anyone who can edit this monitor" in response.content

    def test_resubscribe_never_re_adds_a_monitor_recipient(self, api_client, monitor):
        token = eu.make_token("lead@example.com", eu.MONITOR_ALERTS, str(monitor.id))
        api_client.post(f"{URL}?token={token}", ONE_CLICK, format="multipart")

        response = api_client.post(
            f"{URL}?token={token}", {"action": "resubscribe"}, format="multipart"
        )

        assert b"Can't resubscribe" in response.content
        monitor.refresh_from_db()
        assert monitor.notification_emails == ["oncall@example.com"]

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
        assert b"adds lead@example.com back" in response.content
        monitor.refresh_from_db()
        assert monitor.notification_emails == ["oncall@example.com"]

    def test_alert_email_carries_a_monitor_scoped_link(self, monitor):
        from tracer.utils.monitor import _send_alert_email

        mail.outbox = []
        _send_alert_email(monitor, "3 errors", "critical")

        assert [m.to for m in mail.outbox] == [
            ["oncall@example.com"],
            ["Lead@Example.com"],
        ]
        link = eu.read_token(_token_from(mail.outbox[0]))
        assert (link.email, link.category, link.scope) == (
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

        response = api_client.post(
            f"{URL}?token={token}", ONE_CLICK, format="multipart"
        )

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


@pytest.mark.django_db
class TestReviewRound2Storage:
    """Rick round 2: opt-outs survive config rewrites and links bind to the
    account they were sent to."""

    def test_stale_config_save_keeps_the_opt_out(self, user):
        from accounts.models.user import User

        # A user object cached before the opt-out, as the auth cache holds one.
        stale = User.objects.get(pk=user.pk)
        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), False)

        stale.config["currentOrganizationId"] = "org-2"  # an org switch
        stale.save(update_fields=["config"])
        stale.save()  # and a full save from the same stale copy

        user.refresh_from_db()
        assert user.config["currentOrganizationId"] == "org-2"
        assert user.config[eu.USER_CONFIG_KEY] == [eu.INTEGRATION_ERRORS]
        assert eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_stale_config_save_keeps_a_resubscribe(self, user):
        from accounts.models.user import User

        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), False)
        stale = User.objects.get(pk=user.pk)
        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), True)

        stale.save(update_fields=["config"])

        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_link_acts_on_the_account_it_was_sent_to(self, api_client, user):
        from accounts.models.user import User

        twin = User.objects.create_user(
            email=user.email.upper(), password="x", name="Twin"
        )
        token = eu.make_token(*_link(twin, eu.INTEGRATION_ERRORS))

        api_client.post(f"{URL}?token={token}", ONE_CLICK, format="multipart")

        twin.refresh_from_db()
        user.refresh_from_db()
        assert eu.INTEGRATION_ERRORS in twin.config[eu.USER_CONFIG_KEY]
        assert eu.USER_CONFIG_KEY not in (user.config or {})

    def test_sent_link_carries_the_account_id(self, user):
        (message,) = _send([user.email], eu.INTEGRATION_ERRORS)

        assert eu.read_token(_token_from(message)).user_id == str(user.pk)

    def test_get_on_a_deleted_monitor_has_nothing_to_change(self, api_client, monitor):
        token = eu.make_token("oncall@example.com", eu.MONITOR_ALERTS, str(monitor.id))
        monitor.deleted = True
        monitor.save(update_fields=["deleted"])

        page = api_client.get(URL, {"token": token})

        assert b"Nothing to change" in page.content
        assert b"<form" not in page.content


@pytest.mark.django_db
class TestReviewRound2MailHelper:
    """Review round 2, checkpoint 2: the notification path of email_helper."""

    def test_preference_reads_do_not_grow_with_recipients(self, user):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        counts = []
        for n in (1, 10, 100):
            recipients = [user.email] + [f"r{i}@example.com" for i in range(n - 1)]
            with CaptureQueriesContext(connection) as queries:
                assert len(_send(recipients, eu.INTEGRATION_ERRORS)) == n
            counts.append(len(queries))

        # 1 recipient is an exact account match; more add one case-insensitive read.
        assert counts[0] <= counts[1] == counts[2] <= 2

    def test_lookup_failure_sends_to_nobody(self, user):
        with mock.patch.object(eu, "_users_for_emails", side_effect=RuntimeError):
            with pytest.raises(RuntimeError):
                _send([user.email, "other@example.com"], eu.INTEGRATION_ERRORS)

        assert mail.outbox == []

    def test_mixed_preferences_send_only_to_subscribed(self, user):
        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), False)

        messages = _send([user.email, "other@example.com"], eu.INTEGRATION_ERRORS)

        assert [m.to for m in messages] == [["other@example.com"]]

    def test_one_message_per_address_whatever_its_case(self, user):
        messages = _send(
            [user.email, user.email.upper(), "Other@Example.com", "other@example.com"],
            eu.INTEGRATION_ERRORS,
        )

        assert [m.to for m in messages] == [[user.email], ["Other@Example.com"]]

    def test_returns_how_many_recipients_were_reached(self, user):
        eu.set_subscribed(_link(user, eu.INTEGRATION_ERRORS), False)
        mail.outbox = []

        reached = email_helper(
            "Integration Error: Langfuse",
            "integration_error.html",
            {"connection_name": "c", "platform": "Langfuse", "error_message": "x"},
            [user.email, "a@example.com", "b@example.com"],
            notification_category=eu.INTEGRATION_ERRORS,
        )

        assert reached == 2

    def test_plain_text_part_carries_the_link(self, user):
        (message,) = _send([user.email], eu.INTEGRATION_ERRORS)

        assert _token_from(message) in message.body

    @override_settings(FI_API_PUBLIC_URL="http://localhost:8000")
    def test_one_click_header_only_over_https(self, user):
        (message,) = _send([user.email], eu.INTEGRATION_ERRORS)

        assert "List-Unsubscribe" in message.extra_headers
        assert "List-Unsubscribe-Post" not in message.extra_headers

    @pytest.mark.parametrize(
        "category, scope",
        [("newsletter", None), (eu.MONITOR_ALERTS, None), (eu.INTEGRATION_ERRORS, "x")],
        ids=["unknown-category", "monitor-without-id", "scope-on-account-category"],
    )
    def test_rejects_an_unsupported_category_or_scope(self, user, category, scope):
        with pytest.raises(ValueError):
            _send([user.email], category, scope)

        assert mail.outbox == []


@pytest.mark.django_db
class TestReviewRound2Page:
    """Review round 2, checkpoint 3: the public page and its contract."""

    @override_settings(FI_API_PUBLIC_URL="https://host.test/api")
    def test_form_posts_to_the_same_public_address_as_the_link(self, api_client, user):
        (message,) = _send([user.email], eu.INTEGRATION_ERRORS)
        link = message.extra_headers["List-Unsubscribe"].strip("<>")

        page = api_client.get(URL, {"token": _token_from(message)}).content.decode()

        assert link.startswith(f"https://host.test/api{URL}?token=")
        assert f"action='{link}'" in page

    def test_post_without_a_button_or_one_click_marker_changes_nothing(
        self, api_client, user
    ):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

        response = api_client.post(f"{URL}?token={token}", {}, format="multipart")

        assert response.status_code == 400
        assert not eu.is_opted_out(user.email, eu.INTEGRATION_ERRORS)

    def test_page_answers_a_browser_that_accepts_only_html(self, api_client, user):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

        response = api_client.get(URL, {"token": token}, HTTP_ACCEPT="text/html")

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")

    def test_page_fits_a_phone_and_follows_light_or_dark(self, api_client, user):
        token = eu.make_token(*_link(user, eu.INTEGRATION_ERRORS))

        page = api_client.get(URL, {"token": token}).content

        assert b"name='viewport'" in page
        assert b"prefers-color-scheme" in page

    def test_monitor_page_names_the_monitor(self, api_client, monitor):
        token = eu.make_token("oncall@example.com", eu.MONITOR_ALERTS, str(monitor.id))

        page = api_client.get(URL, {"token": token}).content.decode()

        assert f"“{monitor.name}”" in page

    def test_digest_page_names_both_tracks(self, api_client, user):
        token = eu.make_token(*_link(user, eu.ANNOTATION_DIGEST))

        page = api_client.get(URL, {"token": token}).content

        assert b"daily and realtime" in page

    def test_contract_declares_the_token_html_and_no_auth(self):
        import json
        from pathlib import Path

        spec = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "api_contracts/openapi/swagger.json"
            ).read_text()
        )
        for path in (
            URL,
            "/accounts/notifications/unsubscribe/",
            "/accounts/notifications/snooze/",
        ):
            for method, operation in spec["paths"][path].items():
                if method == "parameters":
                    continue
                params = {p["name"]: p for p in operation.get("parameters", [])}
                assert params["token"]["in"] == "query", (path, method)
                assert operation["security"] == [], (path, method)
                assert "text/html" in operation["produces"], (path, method)


def test_link_token_is_kept_out_of_traces_and_request_logs(rf):
    from tfc.logging.middleware import CallbackRequestMiddleware
    from tfc.telemetry.middleware import OTelContextMiddleware

    request = rf.get(URL, {"token": "signed-tok-123"})
    span = mock.Mock()
    OTelContextMiddleware(lambda r: None)._capture_request(span, request)
    recorded = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}

    assert "signed-tok-123" not in recorded["http.url"]
    assert "signed-tok-123" not in recorded.get("http.query_params", "")
    assert "signed-tok-123" not in CallbackRequestMiddleware.format_request(request)


@pytest.mark.django_db
def test_monitor_alert_footer_says_why_the_address_gets_it(monitor):
    (message, _) = _send(
        ["oncall@example.com", "x@example.com"], eu.MONITOR_ALERTS, str(monitor.id)
    )
    html = message.alternatives[0][0]

    assert "this address is on this monitor" in html
    assert "because you have an account" not in html
