"""Model-hub secrets (``SecretModel``) are encrypted at rest.

The secrets a dataset's vector-database and API-call columns use (Pinecone,
Qdrant and Weaviate API keys, request parameters and headers) follow the
provider keys' rules: Fernet ciphertext in the column, masked in every
response, an undecryptable one fails closed, and migration 0125 converts the
stored rows.
"""

import base64
import importlib
import json

import pytest
from cryptography.fernet import Fernet
from django.apps import apps as live_apps
from django.conf import settings
from django.db import connection
from rest_framework import status

from integrations.services.credentials import CredentialManager
from model_hub.models.api_key import SecretModel, SecretType

PREFIX = "enc::"
SECRETS_URL = "/model-hub/secrets/"


def _raw_key(pk):
    table = connection.ops.quote_name(SecretModel._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT "key"::text FROM {table} WHERE id = %s', [str(pk)])
        return cursor.fetchone()[0]


def _legacy(clear):
    return base64.b64encode(settings.SECRET_KEY[:16].encode() + clear.encode()).decode()


def _assert_ciphertext_of(stored, clear):
    assert clear not in stored
    try:
        decoded = base64.b64decode(stored)
    except Exception:
        decoded = b""
    assert clear.encode() not in decoded, "the stored secret base64-decodes to it"
    assert stored.startswith(PREFIX), stored
    assert CredentialManager.decrypt(stored[len(PREFIX) :].encode()) == {"v": clear}


def _secret(organization, workspace, name, *, stored, deleted=False):
    row = SecretModel.no_workspace_objects.create(
        name=name,
        secret_type=SecretType.API_KEY,
        key="placeholder",
        organization=organization,
        workspace=workspace,
    )
    SecretModel._base_manager.filter(pk=row.pk).update(key=stored, deleted=deleted)
    return row


@pytest.mark.django_db
def test_a_secret_is_stored_as_ciphertext_and_listed_masked(auth_client):
    clear = "kar-pinecone-secret-not-real-0010"
    created = auth_client.post(
        SECRETS_URL,
        {"name": "kar pinecone", "secret_type": SecretType.API_KEY, "key": clear},
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED, created.content
    pk = created.json()["id"]
    stored = _raw_key(pk)
    _assert_ciphertext_of(stored, clear)
    assert SecretModel._base_manager.get(pk=pk).actual_key == clear

    listed = auth_client.get(SECRETS_URL)
    assert listed.status_code == status.HTTP_200_OK
    text = json.dumps(listed.json())
    for never in (clear, stored, stored[len(PREFIX) :]):
        assert never not in text


@pytest.mark.django_db
def test_a_secret_longer_than_the_api_limit_is_refused(auth_client):
    created = auth_client.post(
        SECRETS_URL,
        {"name": "kar long", "secret_type": SecretType.API_KEY, "key": "x" * 2501},
        format="json",
    )
    assert created.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_an_undecryptable_secret_fails_closed_and_lists_masked(
    auth_client, organization, workspace
):
    token = Fernet(Fernet.generate_key()).encrypt(b'{"v": "x"}').decode()
    row = _secret(organization, workspace, "kar unreadable", stored=PREFIX + token)

    with pytest.raises(ValueError, match="cannot be decrypted"):
        _ = SecretModel._base_manager.get(pk=row.pk).actual_key

    listed = auth_client.get(SECRETS_URL)
    assert listed.status_code == status.HTTP_200_OK
    assert token not in json.dumps(listed.json())
    payload = listed.json()
    rows = payload if isinstance(payload, list) else payload.get("results", [])
    masked = next(s for s in rows if s["id"] == str(row.pk))
    assert "cannot be decrypted" in masked["masked_key"]


@pytest.mark.django_db
def test_migration_0125_encrypts_stored_secrets_soft_deleted_ones_too(
    organization, workspace
):
    migration = importlib.import_module(
        "model_hub.migrations.0125_encrypt_model_hub_keys_at_rest"
    )
    live = _secret(
        organization, workspace, "kar live", stored=_legacy("kar-secret-live-x")
    )
    gone = _secret(
        organization,
        workspace,
        "kar gone",
        stored=_legacy("kar-secret-gone-x"),
        deleted=True,
    )
    migration.forwards(live_apps, None)
    _assert_ciphertext_of(_raw_key(live.pk), "kar-secret-live-x")
    _assert_ciphertext_of(_raw_key(gone.pk), "kar-secret-gone-x")
    assert SecretModel._base_manager.get(pk=live.pk).actual_key == "kar-secret-live-x"
