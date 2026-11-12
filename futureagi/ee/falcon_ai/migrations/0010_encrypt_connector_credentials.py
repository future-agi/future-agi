"""Encrypt legacy connector credentials, including soft-deleted rows.

Pause AND drain connector writers; roll forward with strict readers. Authenticated
current ciphertext is preserved on replay. Ambiguous unauthenticated markers
abort, never guess. Existing backups/replicas/exports are not rewritten.
"""

import base64

from django.conf import settings
from django.db import migrations, transaction

from agentcc.services.credential_manager import ENCRYPTED_PREFIX, encrypt_token
from integrations.services.credentials import CredentialManager

OAUTH_FIELDS = ("oauth_client_secret", "oauth_access_token", "oauth_refresh_token")


def _legacy_decode(value):
    # Exact old oauth_client._decode_token semantics, including non-strict
    # base64 decoding and raw fallback on Unicode/decoding errors.
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode("utf-8")).decode("utf-8")
    except Exception:
        return value


def _encrypt_plaintext(value):
    if value.startswith(ENCRYPTED_PREFIX):
        # Decoded legacy plaintext may itself start with the ciphertext marker.
        return ENCRYPTED_PREFIX + CredentialManager.encrypt({"v": value}).decode(
            "utf-8"
        )
    return encrypt_token(value)


def _authenticated_ciphertext(value):
    if not value.startswith(ENCRYPTED_PREFIX):
        return False
    # Authentication AND the single-token envelope are required. A raw legacy
    # value starting with the marker is ambiguous and must fail atomically.
    data = CredentialManager.decrypt(value[len(ENCRYPTED_PREFIX) :].encode("utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"v"}
        or not isinstance(data["v"], str)
        or not data["v"]
    ):
        raise ValueError
    return True


def encrypt_connector_credentials(apps, schema_editor):
    try:
        # Frozen migration-local preflight: run even on an empty table, before
        # selecting or rewriting anything. Runtime has the same Falcon policy.
        if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
            raise ValueError
        CredentialManager._get_fernet()
        connector_model = apps.get_model("falcon_ai", "MCPConnector")
        alias = schema_editor.connection.alias
        # Historical base manager is unfiltered, including deleted rows. The
        # migration's connection is authoritative, never a read-router replica.
        rows = connector_model._base_manager.using(alias)
        with transaction.atomic(using=alias):
            for connector in (
                rows.select_for_update()
                .only("pk", *OAUTH_FIELDS, "auth_header_value")
                .iterator(chunk_size=500)
            ):
                updates = {}
                for field in (*OAUTH_FIELDS, "auth_header_value"):
                    value = getattr(connector, field)
                    if not value or _authenticated_ciphertext(value):
                        continue
                    logical = _legacy_decode(value) if field in OAUTH_FIELDS else value
                    updates[field] = _encrypt_plaintext(logical)
                if updates:
                    rows.filter(pk=connector.pk).update(**updates)
    except Exception:
        # No values, backend exception messages, or exception chains in output.
        raise RuntimeError("Connector credential migration failed") from None


class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ('falcon_ai', '0009_resync_builtin_skills'),
    ]
    # No reverse_code: never restore base64/plaintext, even on rollback.
    operations = [migrations.RunPython(encrypt_connector_credentials)]
