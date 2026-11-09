"""The admin is metadata-only and cannot rewrite context or credential storage."""

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from agentcc.admin import AgentccWebhookAdmin
from agentcc.models.webhook import AgentccWebhook
from agentcc.services import webhook_credential_storage as codec
from agentcc.tests.test_webhook_encrypted_fixtures import (
    make_webhook,
    stored_value,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
PRIVATE = {
    "secret",
    "headers",
    "encrypted_secret",
    "encrypted_headers",
    "credential_storage_version",
    "budget_event_keys",
}


def test_form_has_only_metadata_and_cannot_add(user, organization):
    user.is_staff = user.is_superuser = True
    request = RequestFactory().get("/admin/")
    request.user = user
    hook = make_webhook(
        organization=organization,
        name="admin-hook",
        url="https://fixture.invalid/in",
        headers={"X": "synthetic-private"},
        secret="synthetic-signing",
    )
    admin = AgentccWebhookAdmin(AgentccWebhook, AdminSite())
    with patch.object(
        codec, "decrypt_for_delivery", side_effect=AssertionError("admin decrypted")
    ):
        form = admin.get_form(request, hook)
        assert not PRIVATE.intersection(form.base_fields)
        assert not PRIVATE.intersection(admin.get_fields(request, hook))
        assert {"url", "organization"}.issubset(
            admin.get_readonly_fields(request, hook)
        )
        assert not admin.has_add_permission(request)
        html = form(instance=hook).as_p()
    assert "synthetic-private" not in html and "synthetic-signing" not in html


def test_admin_stale_metadata_save_cannot_overwrite_new_context(user, organization):
    user.is_staff = user.is_superuser = True
    request = RequestFactory().post("/admin/")
    request.user = user
    hook = make_webhook(
        organization=organization,
        name="admin-hook",
        url="https://fixture.invalid/in",
        headers={"X": "synthetic-private"},
        secret="synthetic-signing",
    )
    stale = AgentccWebhook.no_workspace_objects.get(pk=hook.pk)
    admin = AgentccWebhookAdmin(AgentccWebhook, AdminSite())
    form = admin.get_form(request, hook)(
        data={
            "name": "renamed",
            "events": "[]",
            "is_active": "on",
            "description": "metadata",
            "url": "https://untrusted.invalid/",
            "encrypted_secret": "attacker",
        },
        instance=stale,
    )
    assert form.is_valid(), form.errors
    from agentcc.services.webhook_credential_updates import update_webhook

    update_webhook(hook, {"url": "https://new.invalid/", "headers": {}, "secret": ""})
    with patch.object(
        codec, "decrypt_for_delivery", side_effect=AssertionError("admin decrypted")
    ):
        admin.save_model(request, form.save(commit=False), form, change=True)
    hook.refresh_from_db()
    assert hook.name == "renamed" and hook.url == "https://new.invalid/"
    assert stored_value(hook, "headers") == {} and stored_value(hook, "secret") == ""
