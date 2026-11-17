"""Encrypt legacy connector credentials, including soft-deleted rows.

Pause AND drain connector writers; roll forward with strict readers.
Authenticated current ciphertext is preserved on replay. A marker value that
does not authenticate as one encrypted value (written under another key, a raw
value that began with the marker, or a token in the wrong envelope) is never
guessed at: its whole row is left exactly as saved and counted, with no value,
id or name in the output, and every other row is converted. Existing
backups/replicas/exports are not rewritten.
"""

import base64

from django.conf import settings
from django.db import migrations, transaction

from agentcc.services.credential_manager import ENCRYPTED_PREFIX, encrypt_token
from integrations.services.credentials import CredentialManager


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:falcon_ai_mcpconnector",
    ],
    "mitigation": (
        "Before reading any row, even on an empty table, it checks that "
        "INTEGRATION_ENCRYPTION_KEY is a valid Fernet key and not one made up for "
        "this process; if not, it stops the update with a static error and changes "
        "nothing. Then, in one transaction, it reads every falcon_ai_mcpconnector "
        "row, soft-deleted ones included, with SELECT ... FOR UPDATE in chunks of "
        "500 and writes one UPDATE per row that has a value to convert: the OAuth "
        "client secret, access token and refresh token are decoded as 1.58.0 "
        "stored them (base64), the header value is taken as stored, and each is "
        "saved as enc:: plus a Fernet token. Connector writers wait on the locked "
        "rows until the migration commits; the table holds one row per connected "
        "MCP server. On a 1.58.0 database it converts the base64 OAuth values and "
        "any plain header value, and keeps byte for byte a header that 1.58.0 "
        "encrypted with the same key. A row holding an enc:: value that does not "
        "authenticate as one encrypted value (encrypted under another key, such "
        "as the throwaway key 1.58.0 makes per process on a local install without "
        "one, or a typed value "
        "that began with enc::) is left exactly as saved, all of its fields, and "
        "only the number of such rows is printed; that connector refuses its "
        "credential until it is connected again. On a database that already holds "
        "converted rows every authenticated value is skipped, so a second run is "
        "safe: it writes nothing and prints the same count. A failure rolls back "
        "every row. Not reversible: migrating back past it is refused and "
        "plaintext or base64 is never restored, so 1.58.0 code cannot read the "
        "converted OAuth values."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


OAUTH_FIELDS = ("oauth_client_secret", "oauth_access_token", "oauth_refresh_token")
LEGACY, CIPHERTEXT, UNREADABLE = "legacy", "ciphertext", "unreadable"


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


def _stored_state(value):
    if not value.startswith(ENCRYPTED_PREFIX):
        return LEGACY
    # Authentication AND the single-token envelope are required. A marker value
    # failing either is ambiguous (another key, or a raw value that began with
    # the marker): it is never re-encrypted and never decoded. The key itself
    # was checked before any row was read, so only the value can fail here.
    try:
        data = CredentialManager.decrypt(value[len(ENCRYPTED_PREFIX) :].encode("utf-8"))
    except Exception:
        return UNREADABLE
    if (
        not isinstance(data, dict)
        or set(data) != {"v"}
        or not isinstance(data["v"], str)
        or not data["v"]
    ):
        return UNREADABLE
    return CIPHERTEXT


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
        kept = 0
        with transaction.atomic(using=alias):
            for connector in (
                rows.select_for_update()
                .only("pk", *OAUTH_FIELDS, "auth_header_value")
                .iterator(chunk_size=500)
            ):
                stored = {}
                for field in (*OAUTH_FIELDS, "auth_header_value"):
                    value = getattr(connector, field)
                    if value:
                        stored[field] = (value, _stored_state(value))
                if any(state == UNREADABLE for _, state in stored.values()):
                    # Every field of this row stays exactly as saved.
                    kept += 1
                    continue
                updates = {}
                for field, (value, state) in stored.items():
                    if state == CIPHERTEXT:
                        continue
                    logical = _legacy_decode(value) if field in OAUTH_FIELDS else value
                    updates[field] = _encrypt_plaintext(logical)
                if updates:
                    rows.filter(pk=connector.pk).update(**updates)
    except Exception:
        # No values, backend exception messages, or exception chains in output.
        raise RuntimeError("Connector credential migration failed") from None
    if kept:
        # A count only: never a value, id or name of a connector.
        print(
            f"\n  Connector credentials: {kept} connector(s) kept as saved, deleted"
            " ones included: a stored credential does not decrypt with the current"
            " INTEGRATION_ENCRYPTION_KEY. Each must be connected again."
        )


class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ('falcon_ai', '0009_resync_builtin_skills'),
    ]
    # No reverse_code: never restore base64/plaintext, even on rollback.
    operations = [migrations.RunPython(encrypt_connector_credentials)]
