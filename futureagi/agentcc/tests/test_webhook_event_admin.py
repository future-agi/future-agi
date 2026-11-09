"""Historical delivery errors use the same projection in the admin and API."""

from unittest.mock import patch

import pytest
from django.contrib.admin import ModelAdmin
from django.contrib.admin.helpers import AdminForm, AdminReadonlyField
from django.contrib.admin.sites import AdminSite
from django.forms.models import model_to_dict
from django.test import RequestFactory
from django.utils.text import capfirst

from agentcc.admin import AgentccWebhookEventAdmin
from agentcc.models.webhook import AgentccWebhookEvent
from agentcc.services import webhook_credential_storage as codec
from agentcc.services import webhook_delivery as delivery
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
_CANARY = "synthetic-admin-header-canary"


@pytest.fixture
def event_admin_request(user):
    user.is_staff = user.is_superuser = True
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


@pytest.fixture
def historical_event(organization):
    webhook = make_webhook(
        organization=organization,
        name="historical-event-hook",
        url="https://fixture.invalid/in",
        headers={"X-Synthetic-Token": _CANARY},
    )
    return AgentccWebhookEvent.objects.create(
        organization=organization,
        webhook=webhook,
        event_type="request.completed",
        payload={"test": True},
        status=AgentccWebhookEvent.FAILED,
    )


def legacy_event_admin():
    model_admin = ModelAdmin(AgentccWebhookEvent, AdminSite())
    model_admin.readonly_fields = ("created_at", "updated_at")
    return model_admin


@pytest.mark.parametrize(
    "historical_error,expected",
    [
        (f"Invalid header value: {_CANARY}", delivery.WEBHOOK_FAILED),
        (f"HTTP 403: receiver reflected {_CANARY}", "HTTP 403"),
    ],
    ids=["historical-header-error", "historical-http-body"],
)
def test_historical_error_is_projected_not_rendered_raw(
    event_admin_request, historical_event, historical_error, expected
):
    event = historical_event
    event.last_error = historical_error
    event.save(update_fields=["last_error"])
    event.refresh_from_db()
    legacy = legacy_event_admin()
    model_admin = AgentccWebhookEventAdmin(AgentccWebhookEvent, AdminSite())

    with patch.object(
        codec, "decrypt_for_delivery", side_effect=AssertionError("admin decrypted")
    ):
        old_form = legacy.get_form(event_admin_request, event)(instance=event)
        assert _CANARY in str(old_form["last_error"])
        old_display = AdminReadonlyField(
            old_form, "last_error", is_first=True, model_admin=legacy
        )
        assert _CANARY in old_display.contents()

        form = model_admin.get_form(event_admin_request, event)(instance=event)
        assert "last_error" not in form.fields
        assert "last_error" not in model_admin.get_fields(event_admin_request, event)
        assert "display_last_error" not in form.fields
        assert "display_last_error" in model_admin.get_readonly_fields(
            event_admin_request, event
        )
        display = AdminReadonlyField(
            form, "display_last_error", is_first=True, model_admin=model_admin
        )
        with patch(
            "agentcc.admin.shown_delivery_error", wraps=delivery.shown_delivery_error
        ) as projection:
            assert display.contents() == expected
        projection.assert_called_once_with(historical_error)
        assert capfirst(display.field["label"]) == old_form.fields["last_error"].label

        admin_form = AdminForm(
            form,
            model_admin.get_fieldsets(event_admin_request, event),
            {},
            model_admin.get_readonly_fields(event_admin_request, event),
            model_admin=model_admin,
        )
        readonly_html = "".join(
            field.contents()
            for fieldset in admin_form
            for line in fieldset
            for field in line
            if field.is_readonly
        )
        assert _CANARY not in form.as_p() + readonly_html
    event.refresh_from_db()
    assert event.last_error == historical_error


@pytest.mark.parametrize(
    "error",
    [
        "",
        delivery.WEBHOOK_INACTIVE,
        delivery.WEBHOOK_TIMEOUT,
        delivery.WEBHOOK_UNREACHABLE,
        delivery.WEBHOOK_FAILED,
        delivery.WEBHOOK_HEADERS_INVALID,
        delivery.WEBHOOK_CREDENTIALS_UNAVAILABLE,
        delivery.WEBHOOK_PRIVATE_URL_ERROR,
        "HTTP 503",
    ],
    ids=[
        "empty",
        "inactive",
        "timeout",
        "unreachable",
        "failed",
        "invalid-headers",
        "unavailable-credentials",
        "private-url",
        "http-status",
    ],
)
def test_event_admin_preserves_public_error_messages(error):
    model_admin = AgentccWebhookEventAdmin(AgentccWebhookEvent, AdminSite())
    event = AgentccWebhookEvent(last_error=error)
    assert model_admin.display_last_error(event) == error


def test_event_admin_keeps_other_fields_and_changelist(
    event_admin_request, historical_event
):
    model_admin = AgentccWebhookEventAdmin(AgentccWebhookEvent, AdminSite())
    legacy = legacy_event_admin()
    old_fields = legacy.get_fields(event_admin_request, historical_event)
    expected = tuple(
        "display_last_error" if field == "last_error" else field for field in old_fields
    )
    assert model_admin.fields == expected
    old_form = legacy.get_form(event_admin_request, historical_event)
    form = model_admin.get_form(event_admin_request, historical_event)
    assert set(form.base_fields) == set(old_form.base_fields) - {"last_error"}
    assert set(model_admin.readonly_fields) == {
        "created_at",
        "updated_at",
        "display_last_error",
    }
    assert model_admin.list_display == (
        "event_type",
        "webhook",
        "status",
        "attempts",
        "created_at",
    )
    assert model_admin.list_filter == ("status", "event_type")
    assert model_admin.search_fields == ("event_type",)


def test_event_form_ignores_submitted_raw_error(event_admin_request, historical_event):
    event = historical_event
    event.last_error = f"Invalid header value: {_CANARY}"
    event.save(update_fields=["last_error"])
    model_admin = AgentccWebhookEventAdmin(AgentccWebhookEvent, AdminSite())
    data = model_to_dict(event)
    data.update(last_error="forged raw error", attempts=2)
    form = model_admin.get_form(event_admin_request, event, change=True)(
        data=data, instance=event
    )
    assert form.is_valid()
    assert "last_error" not in form.cleaned_data
    updated = form.save(commit=False)
    assert updated.last_error == f"Invalid header value: {_CANARY}"
    assert updated.attempts == 2
