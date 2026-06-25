import base64
import importlib
import logging

import pytest
from cryptography.fernet import Fernet
from django.apps import apps as django_apps

from integrations.services.credentials import CredentialManager

MIGRATION = "model_hub.migrations.0112_encrypt_api_keys_fernet"


@pytest.fixture(autouse=True)
def _enc_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _legacy(plaintext):
    """Reproduce the old base64(SECRET_KEY[:16] + plaintext) at-rest format, using
    the same SECRET_KEY source the model code reads."""
    from django.conf import settings as dj_settings

    salt = dj_settings.SECRET_KEY[:16].encode()
    return base64.b64encode(salt + plaintext.encode()).decode()


# --- leaf crypto contract (now the public CredentialManager interface) ----------


def test_encrypt_roundtrips_to_fernet():
    enc = CredentialManager.encrypt_secret("sk-secret")
    assert enc.startswith("gAAAAA")
    assert CredentialManager.decrypt_secret(enc) == "sk-secret"


def test_encrypt_is_idempotent():
    # An already-Fernet value must not be double-encrypted (the leaf guard).
    enc = CredentialManager.encrypt_secret("sk-secret")
    assert CredentialManager.encrypt_secret(enc) == enc
    assert (
        CredentialManager.decrypt_secret(CredentialManager.encrypt_secret(enc))
        == "sk-secret"
    )


def test_detection_verifies_by_decrypt_not_prefix():
    # A plaintext that merely STARTS with the Fernet prefix is not a token: it must
    # be classified as plaintext (so save encrypts it), not as already-encrypted.
    sneaky = "gAAAAAnot-a-real-token"
    assert CredentialManager.is_fernet(sneaky) is False
    assert CredentialManager.is_encrypted(sneaky) is False
    real = CredentialManager.encrypt_secret("sk-real")
    assert CredentialManager.is_fernet(real) is True


def test_decrypt_reads_legacy_and_reencrypts_from_plaintext():
    legacy = _legacy("sk-legacy")
    assert CredentialManager.is_encrypted(legacy)
    # dual-read recovers the real plaintext (not the ciphertext blob)
    assert CredentialManager.decrypt_secret(legacy) == "sk-legacy"
    # re-encrypting from the recovered plaintext yields Fernet of the SECRET,
    # never Fernet of the base64 blob (the corruption this guards against)
    upgraded = CredentialManager.encrypt_secret(
        CredentialManager.decrypt_secret(legacy)
    )
    assert upgraded.startswith("gAAAAA")
    assert CredentialManager.decrypt_secret(upgraded) == "sk-legacy"


def test_empty_and_none_secrets_are_noops():
    # boundary: empty / None must not crash or produce a value
    assert CredentialManager.encrypt_secret(None) is None
    assert CredentialManager.encrypt_secret("") is None
    assert CredentialManager.decrypt_secret(None) is None
    assert CredentialManager.decrypt_secret("") is None


def test_decrypt_garbage_is_predictable_not_a_crash():
    # invalid input: neither Fernet nor legacy -> None, never an exception
    assert CredentialManager.decrypt_secret("not-encrypted-or-valid-$$$") is None
    assert CredentialManager.is_encrypted("not-encrypted-or-valid-$$$") is False


def test_decrypt_wrong_key_logs_and_returns_none(settings, caplog):
    # rotation: a real token that won't decrypt under the current key must surface a
    # warning (without the secret) and return None, not silently become "".
    token = CredentialManager.encrypt_secret("sk-rotate")
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()  # rotate
    with caplog.at_level(logging.WARNING):
        result = CredentialManager.decrypt_secret(token)
    assert result is None
    assert any("decrypt" in r.message.lower() for r in caplog.records)
    assert "sk-rotate" not in caplog.text


# --- model save path ------------------------------------------------------------


@pytest.mark.django_db
def test_apikey_save_stores_fernet_and_roundtrips():
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-plaintext")
    obj.save()
    obj.refresh_from_db()
    assert obj.key.startswith("gAAAAA")            # stored encrypted
    assert obj.actual_key == "sk-plaintext"        # decrypts back
    # idempotent: saving again doesn't double-encrypt or corrupt
    obj.save()
    obj.refresh_from_db()
    assert obj.actual_key == "sk-plaintext"


@pytest.mark.django_db
def test_save_plaintext_with_fernet_prefix_is_encrypted_not_stored_raw():
    # the exact bug this PR exists to prevent: a plaintext secret beginning with the
    # Fernet prefix must NOT be trusted by prefix and stored raw — it gets encrypted.
    from model_hub.models.api_key import ApiKey

    sneaky = "gAAAAAplaintext-not-a-real-token"
    obj = ApiKey(provider="openai", key=sneaky)
    obj.save()
    obj.refresh_from_db()
    assert obj.key != sneaky                       # not stored raw
    assert obj.actual_key == sneaky                # encrypted as plaintext, reads back


@pytest.mark.django_db
def test_save_upgrades_legacy_key_to_fernet_in_place():
    # a normal re-save of a legacy row upgrades it to Fernet at rest, from the
    # recovered plaintext — it does not leave reversible base64 in the DB.
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-x")
    obj.save()
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-legacy-upgrade"))
    ApiKey.objects.get(pk=obj.id).save()           # re-save the legacy row
    row = ApiKey.objects.get(pk=obj.id)
    assert row.key.startswith("gAAAAA")            # upgraded in place
    assert row.actual_key == "sk-legacy-upgrade"


@pytest.mark.django_db
def test_apikey_save_without_encryption_key_stores_legacy_not_plaintext(
    settings, caplog
):
    # dependency-failure: with INTEGRATION_ENCRYPTION_KEY unset, save must NOT crash
    # (pre-PR saves worked) and must NOT store plaintext — it degrades to the legacy
    # encoding + a warning, consistent with the migration's no-op. dual-read serves it.
    from model_hub.models.api_key import ApiKey

    settings.INTEGRATION_ENCRYPTION_KEY = ""
    with caplog.at_level(logging.WARNING):
        obj = ApiKey(provider="openai", key="sk-no-key")
        obj.save()
    obj.refresh_from_db()
    assert obj.key != "sk-no-key"                  # never stored raw
    assert not obj.key.startswith("gAAAAA")        # cannot be Fernet without a key
    assert obj.actual_key == "sk-no-key"           # dual-read still serves it
    assert any("INTEGRATION_ENCRYPTION_KEY" in r.message for r in caplog.records)


@pytest.mark.django_db
def test_apikey_save_encrypts_nested_config_json():
    # real path: config_json secrets are encrypted at every depth (dict/list) and
    # round-trip back — encrypt/decrypt recurse, so the proof must too.
    from model_hub.models.api_key import ApiKey

    cfg = {"api_key": "sk-flat", "vertex": {"creds": "sk-nested"}, "ids": ["sk-list"]}
    obj = ApiKey(provider="openai", config_json=cfg)
    obj.save()
    obj.refresh_from_db()

    assert obj.config_json["api_key"].startswith("gAAAAA")
    assert obj.config_json["vertex"]["creds"].startswith("gAAAAA")
    assert obj.config_json["ids"][0].startswith("gAAAAA")
    assert obj.actual_json == cfg                  # decrypts back at every depth


@pytest.mark.django_db
def test_secretmodel_keyless_save_leaves_actual_key_none():
    # SecretModel previously dropped the keyless branch, so _actual_key could read
    # stale. A keyless save must leave actual_key None.
    from model_hub.models.api_key import SecretModel

    s = SecretModel(name="empty-secret", key=None)
    s.save()
    assert s.actual_key is None
    s.refresh_from_db()
    assert s.actual_key is None


# --- serializer contract --------------------------------------------------------


@pytest.mark.django_db
def test_custom_model_serializer_never_returns_plaintext():
    # contract pin (masking predates this PR — behavior-preservation, not fix-proving):
    # the serialized config never leaks the raw secret nor the ciphertext.
    import json as _json

    from accounts.models import Organization
    from model_hub.models.custom_models import CustomAIModel
    from model_hub.serializers.custom_models import CustomAIModelSerializer

    org = Organization.objects.create(name="serializer-secret-org")
    m = CustomAIModel(
        user_model_id="m-ser",
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=org,
        key_config={"api_key": "sk-supersecret-1234"},
    )
    m.save()

    reloaded = CustomAIModel.objects.get(pk=m.id)  # DB -> model -> serializer
    blob = _json.dumps(CustomAIModelSerializer(reloaded).data)
    assert "sk-supersecret-1234" not in blob       # never the plaintext
    assert "gAAAAA" not in blob                     # nor the ciphertext


# --- migration ------------------------------------------------------------------


@pytest.mark.django_db
def test_migration_0112_reencrypts_legacy_rows_from_plaintext():
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-seed")
    obj.save()
    # Drop a row in the legacy base64 format, bypassing save()'s encryption.
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-old-secret"))

    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(django_apps, None)

    row = ApiKey.objects.get(pk=obj.id)
    assert row.key.startswith("gAAAAA")            # upgraded to Fernet
    assert row.actual_key == "sk-old-secret"       # from the plaintext, not corrupted

    mig._reencrypt(django_apps, None)              # idempotent re-run
    assert ApiKey.objects.get(pk=obj.id).actual_key == "sk-old-secret"


@pytest.mark.django_db
def test_migration_noops_without_encryption_key(settings, caplog):
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-x")
    obj.save()
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-x"))
    before = ApiKey.objects.get(pk=obj.id).key

    settings.INTEGRATION_ENCRYPTION_KEY = ""       # dependency-failure: key absent
    mig = importlib.import_module(MIGRATION)
    with caplog.at_level(logging.WARNING):
        mig._reencrypt(django_apps, None)

    # no-op, no crash, and the skip is logged so an operator sees it
    assert ApiKey.objects.get(pk=obj.id).key == before
    assert any("INTEGRATION_ENCRYPTION_KEY" in r.message for r in caplog.records)


@pytest.mark.django_db
def test_migration_leaves_existing_fernet_rows_unchanged():
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-fernet")
    obj.save()  # stored as Fernet
    stored = ApiKey.objects.get(pk=obj.id).key
    assert stored.startswith("gAAAAA")

    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(django_apps, None)

    assert ApiKey.objects.get(pk=obj.id).key == stored  # not re-encrypted


@pytest.mark.django_db
def test_migration_reencrypts_nested_legacy_config_json():
    # the migration must match the model's recursion: a legacy secret nested inside
    # config_json (dict/list) is upgraded from its plaintext, not left behind.
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-seed")
    obj.save()
    legacy_cfg = {
        "flat": _legacy("p-flat"),
        "nested": {"creds": _legacy("p-nested")},
        "lst": [_legacy("p-list")],
    }
    ApiKey.objects.filter(pk=obj.id).update(config_json=legacy_cfg)

    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(django_apps, None)

    row = ApiKey.objects.get(pk=obj.id)
    assert row.config_json["flat"].startswith("gAAAAA")
    assert row.config_json["nested"]["creds"].startswith("gAAAAA")
    assert row.config_json["lst"][0].startswith("gAAAAA")
    expected = {"flat": "p-flat", "nested": {"creds": "p-nested"}, "lst": ["p-list"]}
    assert row.actual_json == expected             # recovered plaintext, not the blob

    mig._reencrypt(django_apps, None)              # idempotent re-run
    assert ApiKey.objects.get(pk=obj.id).actual_json == expected


@pytest.mark.django_db
def test_migration_reencrypts_custom_model_key_config():
    # CustomAIModel.key_config flows through the same encrypt path — the migration
    # upgrades its legacy secrets too.
    from accounts.models import Organization
    from model_hub.models.custom_models import CustomAIModel

    org = Organization.objects.create(name="Fernet key_config org")
    m = CustomAIModel(
        user_model_id="m1",
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=org,
        key_config={"api_key": "sk-seed"},
    )
    m.save()
    CustomAIModel.objects.filter(pk=m.id).update(
        key_config={"api_key": _legacy("sk-custom")}
    )

    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(django_apps, None)

    row = CustomAIModel.objects.get(pk=m.id)
    assert row.key_config["api_key"].startswith("gAAAAA")
    assert row.actual_json == {"api_key": "sk-custom"}


@pytest.mark.django_db
def test_secretmodel_save_and_migration_roundtrip():
    # the sibling secret store: save() stores Fernet, and the migration upgrades a
    # legacy SecretModel.key row from its plaintext.
    from model_hub.models.api_key import SecretModel

    s = SecretModel(name="db-pw", key="pw-plain")
    s.save()
    s.refresh_from_db()
    assert s.key.startswith("gAAAAA")
    assert s.actual_key == "pw-plain"

    SecretModel.objects.filter(pk=s.id).update(key=_legacy("pw-old"))
    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(django_apps, None)

    row = SecretModel.objects.get(pk=s.id)
    assert row.key.startswith("gAAAAA")
    assert row.actual_key == "pw-old"
