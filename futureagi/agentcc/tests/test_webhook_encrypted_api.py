"""Encrypted webhook API compatibility, scope and atomic update regressions."""

from unittest.mock import patch

import pytest

from agentcc.models.webhook import AgentccWebhook
from agentcc.serializers.webhook import AgentccWebhookSerializer
from agentcc.services import webhook_credential_storage as codec
from agentcc.services.secret_mask import MASKED_SECRET
from agentcc.tests.test_webhook_encrypted_fixtures import (
    make_webhook,
    stored_value,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.django_db]
URL = "/agentcc/webhooks/"
DESTINATION = "https://hooks.example.test/in?Case=X"
OUTPUT = {
    "id",
    "organization",
    "name",
    "url",
    "events",
    "is_active",
    "headers",
    "description",
    "created_at",
    "updated_at",
}


@pytest.fixture(autouse=True)
def dns(provider_dns):
    return provider_dns({"hooks.example.test": ["93.184.216.34"]}, passthrough=True)


def hook_for(organization, **values):
    return make_webhook(
        organization=organization,
        name="encrypted-hook",
        url=DESTINATION,
        headers=values.pop("headers", {"Authorization": "synthetic-token"}),
        secret=values.pop("secret", "synthetic-signing"),
        **values,
    )


def snapshot(hook):
    hook.refresh_from_db()
    return (
        hook.url,
        bytes(hook.encrypted_headers),
        bytes(hook.encrypted_secret),
        hook.credential_storage_version,
    )


@pytest.mark.parametrize("credentials", [{}, {"headers": {}, "secret": ""}])
def test_create_defaults_and_explicit_empty_are_authenticated(auth_client, credentials):
    response = auth_client.post(
        URL,
        {
            "name": "empty-credentials",
            "url": DESTINATION,
            **credentials,
        },
        format="json",
    )
    assert response.status_code == 200
    data = response.json()["result"]
    assert set(data) == OUTPUT and data["headers"] == {}
    hook = AgentccWebhook.no_workspace_objects.get(pk=data["id"])
    assert hook.encrypted_headers and hook.encrypted_secret
    assert hook.credential_storage_version == 1
    assert stored_value(hook, "headers") == {}
    assert stored_value(hook, "secret") == ""
    assert not hasattr(hook, "headers") and not hasattr(hook, "secret")


@pytest.mark.parametrize(
    "headers,expected",
    [
        ([], {}),
        ("", {}),
        (False, {}),
        (0, {}),
        ([["X", "first"], ["X", "last"]], {"X": "last"}),
        (
            {"Nested": {"a": [1, False, None]}, "Number": 2},
            {"Nested": {"a": [1, False, None]}, "Number": 2},
        ),
    ],
)
def test_ordinary_header_normalization_is_unchanged(
    auth_client, organization, headers, expected
):
    hook = hook_for(organization)
    response = auth_client.patch(
        f"{URL}{hook.pk}/", {"headers": headers}, format="json"
    )
    assert response.status_code == 200
    hook.refresh_from_db()
    assert stored_value(hook, "headers") == expected
    assert response.json()["result"]["headers"] == dict.fromkeys(
        expected, MASKED_SECRET
    )


@pytest.mark.parametrize("field", ["headers", "secret"])
@pytest.mark.parametrize("method", ["post", "patch", "put"])
def test_null_is_rejected_before_mutation(auth_client, organization, field, method):
    hook = hook_for(organization)
    before = snapshot(hook)
    path = URL if method == "post" else f"{URL}{hook.pk}/"
    response = getattr(auth_client, method)(
        path,
        {
            "name": "null-input",
            "url": DESTINATION,
            field: None,
        },
        format="json",
    )
    assert response.status_code == 400
    assert snapshot(hook) == before


@pytest.mark.parametrize(
    "secret,expected", [("  sign  ", "sign"), (" \t ", ""), (123, "123")]
)
def test_secret_keeps_legacy_charfield_semantics(
    auth_client, organization, secret, expected
):
    hook = hook_for(organization)
    response = auth_client.patch(f"{URL}{hook.pk}/", {"secret": secret}, format="json")
    assert response.status_code == 200
    hook.refresh_from_db()
    assert stored_value(hook, "secret") == expected


@pytest.mark.parametrize("secret", ["s" * 256, True, {}, []])
def test_secret_keeps_legacy_limits_and_types(auth_client, organization, secret):
    hook = hook_for(organization)
    before = snapshot(hook)
    response = auth_client.patch(f"{URL}{hook.pk}/", {"secret": secret}, format="json")
    assert response.status_code == 400
    assert snapshot(hook) == before


@pytest.mark.parametrize("method", ["patch", "put"])
def test_omitted_credentials_preserve_ciphertexts(auth_client, organization, method):
    hook = hook_for(organization)
    before = snapshot(hook)
    response = getattr(auth_client, method)(
        f"{URL}{hook.pk}/",
        {
            "name": "renamed",
            "url": DESTINATION,
            "description": "changed",
        },
        format="json",
    )
    assert response.status_code == 200
    assert snapshot(hook) == before
    assert set(response.json()["result"]) == OUTPUT


def test_read_only_decrypts_headers_for_the_existing_mask(auth_client, organization):
    hook = hook_for(organization)
    original = codec.decrypt_for_delivery
    fields = []

    def traced(context, **kwargs):
        fields.append(context.field)
        assert context.field == "headers"
        return original(context, **kwargs)

    with patch.object(codec, "decrypt_for_delivery", side_effect=traced):
        response = auth_client.get(f"{URL}{hook.pk}/")
    assert response.status_code == 200
    assert fields == ["headers"]
    assert set(response.json()["result"]) == OUTPUT
    assert response.json()["result"]["headers"] == {"Authorization": MASKED_SECRET}
    assert "synthetic-token" not in response.content.decode()
    assert "synthetic-signing" not in response.content.decode()


def test_secret_corruption_is_not_read_as_part_of_get(auth_client, organization):
    hook = hook_for(organization)
    AgentccWebhook.no_workspace_objects.filter(pk=hook.pk).update(
        encrypted_secret=b"bad"
    )
    response = auth_client.get(f"{URL}{hook.pk}/")
    assert response.status_code == 200
    assert set(response.json()["result"]) == OUTPUT


def test_same_url_metadata_service_does_not_decrypt(organization):
    hook = hook_for(organization)
    before = snapshot(hook)
    serializer = AgentccWebhookSerializer(
        hook, data={"description": "new"}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    with patch.object(
        codec, "decrypt_for_delivery", side_effect=AssertionError("unexpected decrypt")
    ):
        serializer.save()
    assert snapshot(hook) == before


def test_stale_instance_preserves_latest_secret_and_resolves_latest_mask(organization):
    hook = hook_for(organization)
    stale = AgentccWebhook.no_workspace_objects.get(pk=hook.pk)
    first = AgentccWebhookSerializer(
        hook,
        data={
            "headers": {"Authorization": "new-token"},
            "secret": "new-signing",
        },
        partial=True,
    )
    assert first.is_valid(), first.errors
    first.save()
    second = AgentccWebhookSerializer(
        stale,
        data={
            "headers": {"authorization": MASKED_SECRET},
            "description": "later",
        },
        partial=True,
    )
    assert second.is_valid(), second.errors
    second.save()
    hook.refresh_from_db()
    assert stored_value(hook, "headers") == {"authorization": "new-token"}
    assert stored_value(hook, "secret") == "new-signing"


def test_failed_second_encryption_rolls_back_url_and_both_credentials(
    auth_client, organization
):
    hook = hook_for(organization)
    before = snapshot(hook)
    original = codec.encrypt_for_storage

    def fail_secret(context, value):
        if context.field == "secret":
            raise codec.WebhookCredentialStorageError(codec.ErrorCode.CRYPTO_FAILURE)
        return original(context, value)

    with patch.object(codec, "encrypt_for_storage", side_effect=fail_secret):
        response = auth_client.patch(
            f"{URL}{hook.pk}/",
            {
                "url": DESTINATION + "more",
                "headers": {"X": "new"},
                "secret": "new",
            },
            format="json",
        )
    assert response.status_code >= 400
    assert snapshot(hook) == before


def test_unknown_mask_on_create_refuses(auth_client):
    response = auth_client.post(
        URL,
        {
            "name": "mask-create",
            "url": DESTINATION,
            "headers": {"X": MASKED_SECRET},
        },
        format="json",
    )
    assert response.status_code == 400
    assert not AgentccWebhook.no_workspace_objects.filter(name="mask-create").exists()


def test_preserved_headers_require_reentry_before_move(auth_client, organization):
    hook = hook_for(organization, headers="unsupported-original")
    before = snapshot(hook)
    response = auth_client.patch(
        f"{URL}{hook.pk}/",
        {
            "url": DESTINATION + "more",
            "secret": "new",
        },
        format="json",
    )
    assert response.status_code == 400
    assert snapshot(hook) == before
    response = auth_client.patch(
        f"{URL}{hook.pk}/",
        {
            "url": DESTINATION + "more",
            "headers": {},
            "secret": "",
        },
        format="json",
    )
    assert response.status_code == 200
    hook.refresh_from_db()
    assert stored_value(hook, "headers") == {} and stored_value(hook, "secret") == ""


def test_cross_organization_ciphertext_is_not_rendered_or_reused(
    auth_client, organization
):
    hook = hook_for(organization)
    foreign = codec.StorageContext(
        "00000000-0000-0000-0000-000000000123", str(hook.pk), "headers", hook.url
    )
    hook.encrypted_headers = codec.encrypt_for_storage(foreign, {"X": "foreign-token"})
    hook.save(update_fields=["encrypted_headers"])
    before = snapshot(hook)
    for method, body in [("get", None), ("patch", {"headers": {"X": MASKED_SECRET}})]:
        response = getattr(auth_client, method)(f"{URL}{hook.pk}/", body, format="json")
        assert response.status_code >= 400
        assert "foreign-token" not in response.content.decode()
    assert snapshot(hook) == before


def test_foreign_scope_blocks_read_update_delete_and_test(auth_client, organization):
    from accounts.models import Organization

    other = Organization.objects.create(name="foreign-webhook-org")
    hook = hook_for(other)
    for method, suffix, data in [
        ("get", "", None),
        ("patch", "", {"secret": "stolen"}),
        ("delete", "", None),
        ("post", "test/", {}),
    ]:
        response = getattr(auth_client, method)(
            f"{URL}{hook.pk}/{suffix}", data, format="json"
        )
        assert response.status_code == 404
    hook.refresh_from_db()
    assert not hook.deleted and stored_value(hook, "secret") == "synthetic-signing"
