import base64
import importlib
import logging

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from integrations.services.credentials import CredentialManager

MIGRATION = "model_hub.migrations.0112_encrypt_api_keys_fernet"


@pytest.fixture(autouse=True)
def _enc_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture
def historical_apps():
    """The model_hub app state as of migration 0112 — the historical models the
    migration is actually handed at runtime, not the live registry. The live models
    carry the encrypt-on-save override that a real ``migrate`` never runs through, so
    testing against them would exercise a different code path than production."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    state = MigrationExecutor(connection).loader.project_state(
        ("model_hub", "0112_encrypt_api_keys_fernet")
    )
    return state.apps


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


def test_prepare_undecryptable_token_is_not_rewrapped(settings, caplog):
    # corruption guard (comment a): a real token whose key was rotated is NOT
    # plaintext — prepare_secret_for_storage must return it UNCHANGED, never wrap the
    # ciphertext as if it were the secret (which would destroy the real secret).
    token = CredentialManager.encrypt_secret("sk-preserve-me")
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()  # rotate
    with caplog.at_level(logging.WARNING):
        stored, plaintext = CredentialManager.prepare_secret_for_storage(token)
    assert stored == token  # byte-for-byte untouched — survives a key rollback
    assert plaintext is None
    assert "sk-preserve-me" not in caplog.text


def test_encrypt_secret_fails_closed_in_deployed_env(settings):
    # fail-closed (comment b): a deployed environment with no key must RAISE, not
    # silently degrade to the reversible legacy encoding. local/test may degrade.
    settings.INTEGRATION_ENCRYPTION_KEY = ""
    settings.ENV_TYPE = "prod"
    with pytest.raises(ImproperlyConfigured):
        CredentialManager.encrypt_secret("sk-prod-secret")


def test_encrypt_secret_allows_legacy_only_in_local(settings):
    # the other half of the gate: local/test may degrade to legacy + warn (proven
    # elsewhere) — but never store raw plaintext.
    settings.INTEGRATION_ENCRYPTION_KEY = ""
    settings.ENV_TYPE = "local"
    stored = CredentialManager.encrypt_secret("sk-local-secret")
    assert stored != "sk-local-secret"  # not raw
    assert not stored.startswith("gAAAAA")  # not Fernet (no key)
    assert CredentialManager.decrypt_secret(stored) == "sk-local-secret"


# --- model save path ------------------------------------------------------------


@pytest.mark.django_db
def test_apikey_save_stores_fernet_and_roundtrips():
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-plaintext")
    obj.save()
    obj.refresh_from_db()
    assert obj.key.startswith("gAAAAA")  # stored encrypted
    assert obj.actual_key == "sk-plaintext"  # decrypts back
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
    assert obj.key != sneaky  # not stored raw
    assert obj.actual_key == sneaky  # encrypted as plaintext, reads back


@pytest.mark.django_db
def test_save_upgrades_legacy_key_to_fernet_in_place():
    # a normal re-save of a legacy row upgrades it to Fernet at rest, from the
    # recovered plaintext — it does not leave reversible base64 in the DB.
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-x")
    obj.save()
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-legacy-upgrade"))
    ApiKey.objects.get(pk=obj.id).save()  # re-save the legacy row
    row = ApiKey.objects.get(pk=obj.id)
    assert row.key.startswith("gAAAAA")  # upgraded in place
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
    assert obj.key != "sk-no-key"  # never stored raw
    assert not obj.key.startswith("gAAAAA")  # cannot be Fernet without a key
    assert obj.actual_key == "sk-no-key"  # dual-read still serves it
    assert any("INTEGRATION_ENCRYPTION_KEY" in r.message for r in caplog.records)


@pytest.mark.django_db
def test_apikey_save_under_rotated_key_does_not_rewrap_token(settings):
    # comment (d): the behavioral proof for the corruption guard, on the real save
    # call-path. Store a Fernet token, rotate the key so it no longer decrypts, then
    # save the row again. The stored ciphertext must be left BYTE-FOR-BYTE unchanged —
    # re-wrapping it (the pre-fix behavior) would lose the secret forever.
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-rotate-me")
    obj.save()
    obj.refresh_from_db()
    token = obj.key
    assert token.startswith("gAAAAA")

    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()  # rotate
    stale = ApiKey.objects.get(pk=obj.id)  # __init__ can no longer decrypt it
    assert stale.actual_key is None
    stale.save()  # must NOT re-encrypt the token

    row = ApiKey.objects.get(pk=obj.id)
    assert row.key == token  # unchanged → recoverable on rollback


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
    assert obj.actual_json == cfg  # decrypts back at every depth


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
    assert "sk-supersecret-1234" not in blob  # never the plaintext
    assert "gAAAAA" not in blob  # nor the ciphertext


@pytest.mark.django_db
def test_apikey_serializer_never_returns_plaintext_or_ciphertext():
    # comment (f): the never-leak contract is the same for ApiKey, not only
    # CustomAIModel — the read serializer exposes only the masked key.
    import json as _json

    from model_hub.models.api_key import ApiKey
    from model_hub.serializers.run_prompt import ApiKeySerializer

    obj = ApiKey(
        provider="openai",
        key="sk-apikey-plaintext-1234",
        config_json={"nested": {"token": "sk-config-plaintext-5678"}},
    )
    obj.save()

    reloaded = ApiKey.objects.get(pk=obj.id)  # DB -> model -> serializer
    blob = _json.dumps(ApiKeySerializer(reloaded).data)
    assert "sk-apikey-plaintext-1234" not in blob  # never the plaintext key
    assert "sk-config-plaintext-5678" not in blob  # never a nested config secret
    assert "gAAAAA" not in blob  # nor any ciphertext


@pytest.mark.django_db
def test_secret_serializer_never_returns_plaintext_or_ciphertext():
    # comment (f): same contract for SecretModel — key is write-only, only a masked
    # form is ever returned.
    import json as _json

    from model_hub.models.api_key import SecretModel
    from model_hub.serializers.develop_dataset import SecretSerializer

    s = SecretModel(name="pinecone-cred", key="sk-secretmodel-plaintext-1234")
    s.save()

    reloaded = SecretModel.objects.get(pk=s.id)  # DB -> model -> serializer
    blob = _json.dumps(SecretSerializer(reloaded).data)
    assert "sk-secretmodel-plaintext-1234" not in blob  # never the plaintext
    assert "gAAAAA" not in blob  # nor the ciphertext


# --- migration transform: reencrypt_value (pure, no registry) -------------------
# The migration's real work is CredentialManager.reencrypt_value applied per field.
# Testing it directly (comment e) exercises the exact transform without coupling to
# the live-vs-historical model registry.


def test_reencrypt_value_upgrades_legacy_scalar_from_plaintext():
    new, changed = CredentialManager.reencrypt_value(_legacy("sk-legacy"))
    assert changed is True
    assert new.startswith("gAAAAA")
    assert CredentialManager.decrypt_secret(new) == "sk-legacy"  # secret, not the blob


def test_reencrypt_value_recurses_dicts_and_lists():
    legacy_cfg = {
        "flat": _legacy("p-flat"),
        "nested": {"creds": _legacy("p-nested")},
        "lst": [_legacy("p-list")],
    }
    new, changed = CredentialManager.reencrypt_value(legacy_cfg)
    assert changed is True
    assert new["flat"].startswith("gAAAAA")
    assert new["nested"]["creds"].startswith("gAAAAA")
    assert new["lst"][0].startswith("gAAAAA")
    assert CredentialManager.decrypt_json(new) == {
        "flat": "p-flat",
        "nested": {"creds": "p-nested"},
        "lst": ["p-list"],
    }


def test_reencrypt_value_leaves_fernet_and_plaintext_untouched():
    fernet = CredentialManager.encrypt_secret("sk-already")
    assert CredentialManager.reencrypt_value(fernet) == (fernet, False)  # no churn
    assert CredentialManager.reencrypt_value("plain-not-a-secret") == (
        "plain-not-a-secret",
        False,
    )


def test_reencrypt_value_never_rewraps_undecryptable_token(settings):
    # the migration must not corrupt a token whose key was rotated (mirror of a).
    token = CredentialManager.encrypt_secret("sk-rotate")
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    assert CredentialManager.reencrypt_value(token) == (token, False)  # left intact


# --- migration end-to-end via historical apps -----------------------------------
# _reencrypt is handed the HISTORICAL model state (the ``historical_apps`` fixture),
# exactly as a real ``migrate`` runs it — not the live registry with its
# encrypt-on-save override (comment e).


@pytest.mark.django_db
def test_migration_0112_reencrypts_legacy_rows_from_plaintext(historical_apps):
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-seed")
    obj.save()
    # Drop a row in the legacy base64 format, bypassing save()'s encryption.
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-old-secret"))

    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(historical_apps, None)

    row = ApiKey.objects.get(pk=obj.id)
    assert row.key.startswith("gAAAAA")  # upgraded to Fernet
    assert row.actual_key == "sk-old-secret"  # from the plaintext, not corrupted

    mig._reencrypt(historical_apps, None)  # idempotent re-run
    assert ApiKey.objects.get(pk=obj.id).actual_key == "sk-old-secret"


@pytest.mark.django_db
def test_migration_noops_without_key_in_local(settings, caplog, historical_apps):
    # local/test with no key: no-op, no crash, and the skip is logged for the operator.
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-x")
    obj.save()
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-x"))
    before = ApiKey.objects.get(pk=obj.id).key

    settings.INTEGRATION_ENCRYPTION_KEY = ""
    settings.ENV_TYPE = "local"
    mig = importlib.import_module(MIGRATION)
    with caplog.at_level(logging.WARNING):
        mig._reencrypt(historical_apps, None)

    assert ApiKey.objects.get(pk=obj.id).key == before
    assert any("INTEGRATION_ENCRYPTION_KEY" in r.message for r in caplog.records)


@pytest.mark.django_db
def test_migration_raises_without_key_in_deployed_env(settings, historical_apps):
    # comment (c): a deployed migrate with no key must RAISE, not silently no-op and
    # mark 0112 applied (which would make "set key later + re-run" a permanent no-op).
    # Raising rolls the migration back → it is re-runnable once the key is set. The
    # seeded row is left untouched.
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-x")
    obj.save()
    ApiKey.objects.filter(pk=obj.id).update(key=_legacy("sk-trapped"))
    before = ApiKey.objects.get(pk=obj.id).key

    settings.INTEGRATION_ENCRYPTION_KEY = ""
    settings.ENV_TYPE = "prod"
    mig = importlib.import_module(MIGRATION)
    with pytest.raises(ImproperlyConfigured):
        mig._reencrypt(historical_apps, None)

    assert ApiKey.objects.get(pk=obj.id).key == before  # nothing written on the raise


@pytest.mark.django_db
def test_migration_leaves_existing_fernet_rows_unchanged(historical_apps):
    from model_hub.models.api_key import ApiKey

    obj = ApiKey(provider="openai", key="sk-fernet")
    obj.save()  # stored as Fernet
    stored = ApiKey.objects.get(pk=obj.id).key
    assert stored.startswith("gAAAAA")

    mig = importlib.import_module(MIGRATION)
    mig._reencrypt(historical_apps, None)

    assert ApiKey.objects.get(pk=obj.id).key == stored  # not re-encrypted


@pytest.mark.django_db
def test_migration_reencrypts_nested_legacy_config_json(historical_apps):
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
    mig._reencrypt(historical_apps, None)

    row = ApiKey.objects.get(pk=obj.id)
    assert row.config_json["flat"].startswith("gAAAAA")
    assert row.config_json["nested"]["creds"].startswith("gAAAAA")
    assert row.config_json["lst"][0].startswith("gAAAAA")
    expected = {"flat": "p-flat", "nested": {"creds": "p-nested"}, "lst": ["p-list"]}
    assert row.actual_json == expected  # recovered plaintext, not the blob

    mig._reencrypt(historical_apps, None)  # idempotent re-run
    assert ApiKey.objects.get(pk=obj.id).actual_json == expected


@pytest.mark.django_db
def test_migration_reencrypts_custom_model_key_config(historical_apps):
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
    mig._reencrypt(historical_apps, None)

    row = CustomAIModel.objects.get(pk=m.id)
    assert row.key_config["api_key"].startswith("gAAAAA")
    assert row.actual_json == {"api_key": "sk-custom"}


@pytest.mark.django_db
def test_secretmodel_save_and_migration_roundtrip(historical_apps):
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
    mig._reencrypt(historical_apps, None)

    row = SecretModel.objects.get(pk=s.id)
    assert row.key.startswith("gAAAAA")
    assert row.actual_key == "pw-old"
