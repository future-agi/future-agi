import logging

from django.db import migrations, models

logger = logging.getLogger(__name__)


def _reencrypt(apps, schema_editor):
    """Upgrade legacy base64(SECRET_KEY[:16] + plaintext) secrets to Fernet,
    re-encrypting from the recovered plaintext (never the stored ciphertext).

    Idempotent: already-Fernet values are skipped.

    Fail-closed on a missing key: ``require_encryption_key`` RAISES in every deployed
    environment, so this migration is *not* marked applied and re-runs cleanly once
    the key is configured (no one-shot trap). It only no-ops — with a warning — in
    local / test, where dual-read keeps any legacy rows readable meanwhile.
    """
    from integrations.services.credentials import CredentialManager

    if not CredentialManager.require_encryption_key("re-encrypt stored secrets"):
        logger.warning(
            "0112_encrypt_api_keys_fernet: INTEGRATION_ENCRYPTION_KEY is not set "
            "(local/test); skipping secret re-encryption. Rows stay readable via "
            "dual-read. Set the key and re-run `migrate model_hub` to upgrade at rest."
        )
        return

    ApiKey = apps.get_model("model_hub", "ApiKey")
    SecretModel = apps.get_model("model_hub", "SecretModel")
    CustomAIModel = apps.get_model("model_hub", "CustomAIModel")

    for row in ApiKey.objects.all().iterator():
        upd = []
        nk, ck = CredentialManager.reencrypt_value(row.key)
        if ck:
            row.key = nk
            upd.append("key")
        nj, cj = CredentialManager.reencrypt_value(row.config_json)
        if cj:
            row.config_json = nj
            upd.append("config_json")
        if upd:
            row.save(update_fields=upd)

    for row in SecretModel.objects.all().iterator():
        nk, ck = CredentialManager.reencrypt_value(row.key)
        if ck:
            row.key = nk
            row.save(update_fields=["key"])

    for row in CustomAIModel.objects.all().iterator():
        nj, cj = CredentialManager.reencrypt_value(row.key_config)
        if cj:
            row.key_config = nj
            row.save(update_fields=["key_config"])


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0111_userevalmetric_pinned_version"),
    ]

    operations = [
        migrations.AlterField(
            model_name="apikey",
            name="key",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="secretmodel",
            name="key",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(_reencrypt, migrations.RunPython.noop),
    ]
