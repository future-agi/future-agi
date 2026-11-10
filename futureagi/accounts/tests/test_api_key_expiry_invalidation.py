"""Expiry edits evict collector snapshots only after their transaction commits."""

import hashlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from accounts.admin import OrgApiKeyAdmin
from accounts.models import OrgApiKey

pytestmark = pytest.mark.django_db


@pytest.fixture
def key(user, workspace):
    return OrgApiKey.no_workspace_objects.create(
        name="expiry-invalidation",
        organization=user.organization,
        workspace=workspace,
        user=user,
        type="user",
    )


@pytest.mark.parametrize("old_expiry", [None, "future"])
@pytest.mark.parametrize("new_expiry", ["past", "near", None])
@pytest.mark.parametrize("save_path", ["model-full", "model-partial", "admin"])
def test_saved_expiry_invalidates_committed_credentials(
    key,
    old_expiry,
    new_expiry,
    save_path,
    django_capture_on_commit_callbacks,
):
    now = timezone.now()
    # Simulate the snapshot the collector cached before the edit.
    OrgApiKey.no_workspace_objects.filter(pk=key.pk).update(
        expires_at=None if old_expiry is None else now + timedelta(days=1),
    )
    key.refresh_from_db()
    key.expires_at = {
        "past": now - timedelta(seconds=1),
        "near": now + timedelta(seconds=10),
        None: None,
    }[new_expiry]
    digest = hashlib.sha256(f"{key.api_key}:{key.secret_key}".encode()).hexdigest()
    with patch("redis.Redis.from_url") as factory:
        publisher = factory.return_value.__enter__.return_value.publish
        with django_capture_on_commit_callbacks(execute=True):
            if save_path == "admin":
                OrgApiKeyAdmin(OrgApiKey, AdminSite()).save_model(
                    RequestFactory().post("/admin/accounts/orgapikey/"),
                    key,
                    None,
                    True,
                )
            else:
                key.save(
                    **(
                        {"update_fields": ["expires_at"]}
                        if save_path == "model-partial"
                        else {}
                    )
                )
            # The DB transaction is still open; no old-row refill is possible.
            publisher.assert_not_called()
        publisher.assert_called_once_with("fi:auth:revoke", digest)
    key.refresh_from_db()
    assert (
        key.expires_at
        == {
            "past": now - timedelta(seconds=1),
            "near": now + timedelta(seconds=10),
            None: None,
        }[new_expiry]
    )


def test_rollback_does_not_invalidate(key, django_capture_on_commit_callbacks):
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(ValueError, match="rollback"):
                with transaction.atomic():
                    key.expires_at = timezone.now() - timedelta(seconds=1)
                    key.save(update_fields=["expires_at"])
                    raise ValueError("rollback")
        factory.assert_not_called()
    key.refresh_from_db()
    assert key.expires_at is None


@pytest.mark.parametrize("fields", [[], ["name"]])
def test_unsaved_expiry_does_not_invalidate(
    key, fields, django_capture_on_commit_callbacks
):
    key.expires_at = timezone.now() - timedelta(seconds=1)
    key.name = "changed-name"
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            key.save(update_fields=fields)
        factory.assert_not_called()
    key.refresh_from_db()
    assert key.expires_at is None


def test_deferred_expiry_is_not_written(key, django_capture_on_commit_callbacks):
    key = OrgApiKey.no_workspace_objects.only(
        "id", "api_key", "secret_key", "name"
    ).get(pk=key.pk)
    key.name = "deferred-rename"
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            key.save()
        factory.assert_not_called()


def test_detached_force_update_invalidates(key, django_capture_on_commit_callbacks):
    expiry = timezone.now() - timedelta(seconds=1)
    detached = OrgApiKey(
        pk=key.pk,
        api_key=key.api_key,
        secret_key=key.secret_key,
        expires_at=expiry,
    )
    assert detached._state.adding is True
    digest = hashlib.sha256(f"{key.api_key}:{key.secret_key}".encode()).hexdigest()
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            detached.save(force_update=True, update_fields=["expires_at"])
            factory.assert_not_called()
        factory.return_value.__enter__.return_value.publish.assert_called_once_with(
            "fi:auth:revoke",
            digest,
        )
    key.refresh_from_db()
    assert key.expires_at == expiry


@pytest.mark.parametrize("instance_shape", ["detached", "unsaved-credentials"])
@pytest.mark.parametrize("credential_field", [None, "api_key", "secret_key"])
def test_partial_expiry_invalidates_persisted_credentials(
    key, instance_shape, credential_field, django_capture_on_commit_callbacks
):
    persisted_pair = [key.api_key, key.secret_key]
    expiry = timezone.now() - timedelta(seconds=1)
    if instance_shape == "detached":
        instance = OrgApiKey(pk=key.pk, expires_at=expiry)
    else:
        instance = key
        instance.expires_at = expiry
        instance.api_key = "unsaved-api-key"
        instance.secret_key = "unsaved-secret-key"
    fields = ["expires_at"]
    if credential_field is not None:
        fields.append(credential_field)
        setattr(instance, credential_field, "persisted-credential-edit")
        persisted_pair[0 if credential_field == "api_key" else 1] = (
            "persisted-credential-edit"
        )
    digest = hashlib.sha256(":".join(persisted_pair).encode()).hexdigest()
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            instance.save(force_update=True, update_fields=iter(fields))
            factory.assert_not_called()
            # Later in-memory edits must not change the callback's identity.
            instance.api_key = "later-api-key"
            instance.secret_key = "later-secret-key"
        factory.return_value.__enter__.return_value.publish.assert_called_once_with(
            "fi:auth:revoke", digest
        )
    key.refresh_from_db()
    assert key.expires_at == expiry
    assert [key.api_key, key.secret_key] == persisted_pair


def test_create_invalidates_a_preexisting_negative_cache(
    user,
    workspace,
    django_capture_on_commit_callbacks,
):
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            created = OrgApiKey.no_workspace_objects.create(
                name="new-negative-cache",
                organization=user.organization,
                workspace=workspace,
                user=user,
                type="user",
            )
            factory.assert_not_called()
        digest = hashlib.sha256(
            f"{created.api_key}:{created.secret_key}".encode()
        ).hexdigest()
        factory.return_value.__enter__.return_value.publish.assert_called_once_with(
            "fi:auth:revoke",
            digest,
        )


@pytest.mark.parametrize("positional", [False, True])
def test_iterable_update_fields_preserves_the_expiry_write(
    key,
    positional,
    django_capture_on_commit_callbacks,
):
    expiry = timezone.now() + timedelta(seconds=10)
    key.expires_at = expiry
    fields = iter(["name", "expires_at"])
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            if positional:
                key.save(False, False, None, fields)
            else:
                key.save(update_fields=fields)
        factory.return_value.__enter__.return_value.publish.assert_called_once()
    key.refresh_from_db()
    assert key.expires_at == expiry


def test_callback_captures_credentials_at_save(key, django_capture_on_commit_callbacks):
    digest = hashlib.sha256(f"{key.api_key}:{key.secret_key}".encode()).hexdigest()
    with patch("redis.Redis.from_url") as factory:
        with django_capture_on_commit_callbacks(execute=True):
            key.expires_at = timezone.now() - timedelta(seconds=1)
            key.save(update_fields=["expires_at"])
            key.api_key = "not-persisted"
            key.secret_key = "not-persisted"
        factory.return_value.__enter__.return_value.publish.assert_called_once_with(
            "fi:auth:revoke",
            digest,
        )
