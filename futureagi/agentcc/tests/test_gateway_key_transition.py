"""Gateway-only transition contract. All keys and plaintext are synthetic."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.core.management.base import CommandError


def crypto():
    from agentcc.services.gateway_key_crypto import KeyTransition

    source, target = Fernet.generate_key(), Fernet.generate_key()
    return (
        KeyTransition(source.decode(), target.decode()),
        Fernet(source),
        Fernet(target),
    )


def test_transition_preserves_exact_plaintext_and_target_ciphertext():
    transform, source, target = crypto()
    clear = b'{"v": "synthetic", "unicode": "\\u2603"}'
    old = source.encrypt(clear)
    new, kind = transform.convert(old, wrapped=False)
    assert kind == "source"
    assert target.decrypt(new) == clear
    assert transform.convert(new, wrapped=False) == (new, "target")
    wrapped, kind = transform.convert("enc::" + old.decode(), wrapped=True)
    assert kind == "source"
    assert target.decrypt(wrapped[5:].encode()) == clear


@pytest.mark.parametrize("value", ["plaintext", "enc::", "enc::broken", 7, {}, []])
def test_transition_never_treats_unreadable_as_plaintext(value):
    transform, _, _ = crypto()
    with pytest.raises(CommandError, match="ciphertext"):
        transform.convert(value, wrapped=True)


@pytest.mark.parametrize("clear", [b"not json", b"[]", b'{"missing": 1}', b'{"v": 7}'])
def test_transition_rejects_unsupported_payload_shape(clear):
    transform, source, _ = crypto()
    with pytest.raises(CommandError, match="payload"):
        transform.convert("enc::" + source.encrypt(clear).decode(), wrapped=True)


def test_transition_wrong_key_and_target_only_verification_fail_closed():
    from agentcc.services.gateway_key_crypto import KeyTransition

    transform, source, target = crypto()
    with pytest.raises(CommandError, match="ciphertext"):
        transform.convert(Fernet(Fernet.generate_key()).encrypt(b"{}"), wrapped=False)
    with pytest.raises(CommandError, match="key"):
        KeyTransition(None, "not-a-canonical-key")


@pytest.mark.parametrize("source,target", [("bad", "bad"), (None, ""), (" ", "x")])
def test_transition_invalid_keys_are_sanitized(source, target):
    from agentcc.services.gateway_key_crypto import KeyTransition

    with pytest.raises(CommandError, match="key"):
        KeyTransition(source, target)


def test_transition_target_only_rejects_source_residue():
    from agentcc.services.gateway_key_crypto import KeyTransition

    source, target = Fernet.generate_key(), Fernet.generate_key()
    verify = KeyTransition(None, target.decode())
    with pytest.raises(CommandError, match="ciphertext"):
        verify.convert(Fernet(source).encrypt(b"{}"), wrapped=False)
    blob = Fernet(target).encrypt(b"{}")
    assert verify.convert(blob, wrapped=False) == (blob, "target")


def test_transition_inventory_matches_every_encrypted_model_field():
    from django.apps import apps

    from agentcc.services.gateway_key_inventory import inventory

    stores = inventory()
    actual = {
        (model.__name__, field.name)
        for model in apps.get_app_config("prism").get_models()
        for field in model._meta.concrete_fields
        if field.name.startswith("encrypted_")
        or field.get_internal_type() == "BinaryField"
    }
    declared = {
        (store.model.__name__, name)
        for store in stores
        if not store.config
        for name in store.fields
    }
    assert declared == actual
    assert len(actual) == 15
    webhook = next(
        store for store in stores if store.model.__name__ == "AgentccWebhook"
    )
    assert webhook.fields == ("encrypted_headers", "encrypted_secret")
    assert not webhook.wrapped
    for name in webhook.fields:
        field = webhook.model._meta.get_field(name)
        assert not field.null and not field.blank
    assert len([store for store in stores if store.config]) == 1


def test_transition_inventory_refuses_unrecognized_storage():
    from django.apps import apps

    from agentcc.services.gateway_key_inventory import inventory

    original = list(apps.get_app_config("prism").get_models())
    future = SimpleNamespace(
        __name__="FutureSecretStore",
        _meta=SimpleNamespace(
            concrete_fields=[SimpleNamespace(name="encrypted_future")]
        ),
    )
    with patch.object(
        apps.get_app_config("prism"), "get_models", return_value=original + [future]
    ):
        with pytest.raises(CommandError, match="inventory"):
            inventory()


@pytest.mark.parametrize(
    "model_name,fields",
    [
        ("AgentccMCPVendorBinding", ("encrypted_bearer",)),
        (
            "AgentccMCPVendorConnection",
            ("encrypted_session", "encrypted_endpoint"),
        ),
        ("AgentccMCPVendorCleanup", ("encrypted_session",)),
        ("AgentccSIEMDestination", ("encrypted_secret",)),
        ("AgentccMCPConnectState", ("encrypted_verifier",)),
    ],
)
def test_transition_new_stores_round_trip_without_source_residue(
    model_name, fields, settings
):
    from agentcc.services.credential_manager import (
        ENCRYPTED_PREFIX,
        decrypt_token,
        encrypt_token,
    )
    from agentcc.services.gateway_key_crypto import KeyTransition
    from agentcc.services.gateway_key_inventory import inventory

    source, target = Fernet.generate_key(), Fernet.generate_key()
    transition = KeyTransition(source.decode(), target.decode())
    store = next(store for store in inventory() if store.model.__name__ == model_name)
    assert store.fields == fields
    assert store.wrapped is True
    assert store.model.ENCRYPTED_FIELDS == fields

    clear = {name: f"synthetic-{model_name}-{name}-☃" for name in fields}
    settings.INTEGRATION_ENCRYPTION_KEY = source.decode()
    row = store.model(**{name: encrypt_token(value) for name, value in clear.items()})
    # A target-only verifier must reject the original row, not silently skip it.
    with pytest.raises(CommandError, match="ciphertext"):
        store.transform(row, transition.verifier())

    changed, counts = store.transform(row, transition)
    assert set(changed) == set(fields)
    assert counts == {"source": len(fields)}
    settings.INTEGRATION_ENCRYPTION_KEY = target.decode()
    for name in fields:
        assert changed[name].startswith(ENCRYPTED_PREFIX)
        assert decrypt_token(changed[name]) == clear[name]
        token = changed[name][len(ENCRYPTED_PREFIX) :].encode()
        with pytest.raises(InvalidToken):
            Fernet(source).decrypt(token)
        setattr(row, name, changed[name])

    # No source-key ciphertext remains in either field of a multi-field store.
    assert store.transform(row, transition.verifier()) == (
        {},
        {"target": len(fields)},
    )
    assert store.transform(row, transition) == ({}, {"target": len(fields)})


def test_transition_org_config_uses_shared_slots_and_preserves_policy():
    from agentcc.services.gateway_key_inventory import transform_config
    from agentcc.services.org_config_secrets import iter_secret_slots

    transform, source, target = crypto()
    clear = {
        "cache": {"redis": {"password": "synthetic"}, "enabled": True},
        "mcp": {
            "servers": [{"url": "https://sink.invalid", "auth": {"token": "synthetic"}}]
        },
    }
    for slot in iter_secret_slots(clear):
        slot.set(
            "enc::" + source.encrypt(json.dumps({"v": slot.value}).encode()).decode()
        )
    changed, counts = transform_config(clear, transform)
    assert counts["source"] == 2
    assert changed["cache"]["enabled"] is True
    for slot in iter_secret_slots(changed):
        assert json.loads(target.decrypt(slot.value[5:].encode()))["v"] == "synthetic"
    assert changed != clear
    assert transform_config(changed, transform)[0] == changed


def test_transition_refuses_ciphertext_outside_declared_config_slots():
    from agentcc.services.gateway_key_inventory import transform_config

    transform, source, _ = crypto()
    unsupported = {
        "routing": {"future": "enc::" + source.encrypt(b'{"v":"synthetic"}').decode()}
    }
    with pytest.raises(CommandError, match="inventory"):
        transform_config(unsupported, transform)


def test_transition_command_phases_are_separate_from_migration_graph():
    from agentcc.management.commands.hosted_gateway_migrate import Command

    parser = Command().create_parser("manage.py", "hosted_gateway_migrate")
    for phase in ("key-transition", "key-verify"):
        options = parser.parse_args([phase])
        assert options.apply is False
        assert options.key_batch_size == 100
        assert options.key_deadline_seconds == 300
