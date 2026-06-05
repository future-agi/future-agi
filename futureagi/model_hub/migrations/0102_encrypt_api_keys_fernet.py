import base64
import os

from django.db import migrations


def _to_fernet(stored, credential_manager):
    """Convert a single legacy base64(salt+value) string to a Fernet token.

    Returns None when there is nothing to do (empty, already Fernet, or
    undecodable) so callers can treat None as "leave the value untouched".
    """
    if not stored or str(stored).startswith("gAAAAA"):
        return None
    try:
        plaintext = base64.b64decode(stored)[16:].decode()
    except Exception:
        return None
    return credential_manager.encrypt({"v": plaintext}).decode()


def _migrate_json(value, credential_manager):
    """Recursively re-encrypt string leaves of a JSON value. Returns
    (new_value, changed)."""
    if isinstance(value, dict):
        out, changed = {}, False
        for k, v in value.items():
            nv, c = _migrate_json(v, credential_manager)
            out[k] = nv
            changed = changed or c
        return out, changed
    if isinstance(value, list):
        out, changed = [], False
        for item in value:
            nv, c = _migrate_json(item, credential_manager)
            out.append(nv)
            changed = changed or c
        return out, changed
    if isinstance(value, str):
        nv = _to_fernet(value, credential_manager)
        if nv is not None:
            return nv, True
    return value, False


def reencrypt(apps, schema_editor):
    # Only migrate when a persisted key is configured. In local dev
    # INTEGRATION_ENCRYPTION_KEY is regenerated every restart, so re-encrypting
    # under it would make these rows undecryptable after the next restart.
    # The dual-format read path keeps legacy base64 rows working until then.
    if not os.environ.get("INTEGRATION_ENCRYPTION_KEY"):
        print("INTEGRATION_ENCRYPTION_KEY not set in env; skipping re-encryption.")
        return

    from integrations.services.credentials import CredentialManager

    ApiKey = apps.get_model("model_hub", "ApiKey")
    SecretModel = apps.get_model("model_hub", "SecretModel")
    CustomAIModel = apps.get_model("model_hub", "CustomAIModel")

    for row in ApiKey.objects.all().iterator():
        fields = {}
        new_key = _to_fernet(row.key, CredentialManager)
        if new_key is not None:
            fields["key"] = new_key
        if row.config_json:
            new_json, changed = _migrate_json(row.config_json, CredentialManager)
            if changed:
                fields["config_json"] = new_json
        if fields:
            ApiKey.objects.filter(pk=row.pk).update(**fields)

    for row in SecretModel.objects.all().iterator():
        new_key = _to_fernet(row.key, CredentialManager)
        if new_key is not None:
            SecretModel.objects.filter(pk=row.pk).update(key=new_key)

    for row in CustomAIModel.objects.all().iterator():
        if not row.key_config:
            continue
        new_json, changed = _migrate_json(row.key_config, CredentialManager)
        if changed:
            CustomAIModel.objects.filter(pk=row.pk).update(key_config=new_json)


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0101_backfill_composite_v1_config_snapshots"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
