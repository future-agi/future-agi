"""The GitHub push webhook: size and signature first, nothing from the database."""

import hashlib
import hmac
import json
import logging
from unittest.mock import PropertyMock, patch

import pytest
from django.core.cache import cache
from django.http import HttpRequest

from simulate.tasks import harness_agent_changes as tasks
from simulate.views.harness_github_webhook import _WebhookThrottle

URL = "/simulate/api/harness/github/webhook/"
SECRET = "e2e-only-webhook-test-value"
DELIVERY = "6f1c2a40-93d8-11ef-8f1a-2b3c4d5e6f70"
PUSH = {
    "ref": "refs/heads/main",
    "after": "f" * 40,
    "deleted": False,
    "repository": {"id": 1296269, "full_name": "acme/agent", "default_branch": "main"},
    "pusher": {"email": "dev@example.test"},
}


@pytest.fixture(autouse=True)
def configured(settings):
    settings.GITHUB_APP_WEBHOOK_SECRET = SECRET
    settings.HARNESS_VERSIONING_ENABLED = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def enqueued():
    with patch.object(tasks.observe_harness_github_push, "apply_async") as mocked:
        yield mocked


def _sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client, body=None, *, signature="sign", event="push", **extra):
    body = json.dumps(PUSH).encode() if body is None else body
    headers = {"HTTP_X_GITHUB_EVENT": event, "HTTP_X_GITHUB_DELIVERY": DELIVERY}
    if signature == "sign":
        signature = _sign(body)
    if signature is not None:
        headers["HTTP_X_HUB_SIGNATURE_256"] = signature
    headers.update(extra)
    return client.post(URL, data=body, content_type="application/json", **headers)


def _reasons(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("harness_webhook_rejected")
    ]


def test_unsigned_is_204_and_touches_nothing(
    db, client, enqueued, caplog, django_assert_num_queries
):
    caplog.set_level(logging.INFO)
    with django_assert_num_queries(0):
        response = _post(client, signature=None)
    assert response.status_code == 204
    enqueued.assert_not_called()
    assert _reasons(caplog) == [
        "harness_webhook_rejected reason=bad_signature delivery=unsigned"
    ]
    assert DELIVERY not in caplog.text


def test_unset_secret_or_wrong_signature_schedules_nothing(
    client, settings, enqueued, caplog
):
    caplog.set_level(logging.INFO)
    body = json.dumps(PUSH).encode()
    assert _post(client, body, signature=_sign(body, "other")).status_code == 204
    settings.GITHUB_APP_WEBHOOK_SECRET = ""
    assert _post(client, body).status_code == 204
    enqueued.assert_not_called()
    assert _reasons(caplog) == [
        "harness_webhook_rejected reason=bad_signature delivery=unsigned",
        "harness_webhook_rejected reason=not_configured delivery=unsigned",
    ]


def test_ping_and_other_events_schedule_nothing(client, enqueued, caplog):
    caplog.set_level(logging.INFO)
    assert _post(client, event="ping").status_code == 204
    assert _post(client, event="issues").status_code == 204
    enqueued.assert_not_called()
    assert _reasons(caplog) == [
        f"harness_webhook_rejected reason=ignored_event delivery={DELIVERY}"
    ]


def test_a_signed_push_schedules_its_repository_and_ref_only(client, enqueued):
    response = _post(client)

    assert response.status_code == 204
    enqueued.assert_called_once()
    kwargs = enqueued.call_args.kwargs
    assert kwargs["args"] == ["acme/agent", "refs/heads/main", "main", 1296269]
    assert kwargs["task_id"].startswith("harness-github-push-")
    text = json.dumps(kwargs, default=str)
    assert DELIVERY not in text
    assert "dev@example.test" not in text


def test_pushes_to_one_branch_coalesce(client, enqueued):
    _post(client)
    _post(client)
    first, second = (call.kwargs["task_id"] for call in enqueued.call_args_list)
    assert first == second


def test_flag_off_schedules_nothing(client, settings, enqueued, caplog):
    caplog.set_level(logging.INFO)
    settings.HARNESS_VERSIONING_ENABLED = False
    assert _post(client).status_code == 204
    enqueued.assert_not_called()
    assert "reason=versioning_disabled" in caplog.text


def test_size_is_checked_before_the_body_is_read(client, enqueued, caplog):
    caplog.set_level(logging.INFO)
    with patch.object(
        HttpRequest, "body", new_callable=PropertyMock, side_effect=AssertionError
    ):
        too_large = _post(client, CONTENT_LENGTH=str(25 * 1024 * 1024 + 1))
        missing = _post(client, CONTENT_LENGTH="")
    assert (too_large.status_code, missing.status_code) == (204, 204)
    enqueued.assert_not_called()
    assert _reasons(caplog) == [
        "harness_webhook_rejected reason=too_large delivery=unsigned",
        "harness_webhook_rejected reason=no_length delivery=unsigned",
    ]


def test_a_non_ascii_signature_is_refused(client, enqueued, caplog):
    caplog.set_level(logging.INFO)
    assert _post(client, signature="sha256=\xe9").status_code == 204
    enqueued.assert_not_called()
    assert _reasons(caplog) == [
        "harness_webhook_rejected reason=bad_signature delivery=unsigned"
    ]


def test_only_refused_requests_are_throttled_by_their_address(client, enqueued):
    with patch.object(_WebhookThrottle, "rate", "1/min"):
        first = _post(
            client,
            signature=None,
            HTTP_X_REAL_IP="203.0.113.1",
            HTTP_X_FORWARDED_FOR="203.0.113.1",
        )
        forged = _post(
            client,
            signature="sha256=" + "0" * 64,
            HTTP_X_REAL_IP="203.0.113.2",
            HTTP_X_FORWARDED_FOR="203.0.113.2",
        )
        signed = [_post(client).status_code for _ in range(3)]
    assert (first.status_code, forged.status_code) == (204, 429)
    assert signed == [204] * 3
    assert enqueued.call_count == 3


def test_bad_json_after_a_valid_signature(client, enqueued, caplog):
    caplog.set_level(logging.INFO)
    assert _post(client, b"{not json").status_code == 204
    enqueued.assert_not_called()
    assert "reason=bad_json" in caplog.text


def test_a_delivery_id_is_logged_only_when_signed_and_well_formed(
    client, enqueued, caplog
):
    caplog.set_level(logging.INFO)
    _post(client, event="issues", HTTP_X_GITHUB_DELIVERY="<script>")
    _post(client, signature=None, HTTP_X_GITHUB_DELIVERY="<script>")
    assert _reasons(caplog) == [
        "harness_webhook_rejected reason=ignored_event delivery=invalid",
        "harness_webhook_rejected reason=bad_signature delivery=unsigned",
    ]
    assert "<script>" not in caplog.text


@pytest.mark.parametrize(
    "push",
    [
        {**PUSH, "ref": "refs/heads/a/../main"},
        {**PUSH, "ref": "main"},
        {**PUSH, "deleted": True},
        {**PUSH, "repository": {"full_name": "../etc"}},
    ],
)
def test_unsafe_or_deleted_refs_are_ignored(client, enqueued, caplog, push):
    caplog.set_level(logging.INFO)
    assert _post(client, json.dumps(push).encode()).status_code == 204
    enqueued.assert_not_called()
    assert "reason=ignored_ref" in caplog.text


def test_a_lost_enqueue_still_answers_204(client, enqueued):
    enqueued.side_effect = RuntimeError("temporal down")
    assert _post(client).status_code == 204


def test_the_body_is_kept_off_tracing_spans():
    from tfc.telemetry.middleware import SENSITIVE_BODY_PATH_PREFIXES

    assert URL.startswith(SENSITIVE_BODY_PATH_PREFIXES)
