"""Historical configuration seam; synthetic identity/data and no provider calls.

The ORM schema is owned by a separate lane. Inject ONLY its lookup/digest seam
here; production always lazily imports the real model and canonical helper.
These tests do not claim database immutability or native resource readiness.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import traceback
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from tfc.utils import storage_config as sc

BINDING_IDENTITY_FIELDS = (
    "provider",
    "endpoint",
    "account_identity",
    "region",
    "quarantine_bucket",
    "quarantine_resource_identity",
    "quarantine_owner_identity",
    "quarantine_created_at",
    "quarantine_encryption_mode",
    "quarantine_encryption_key_identity",
    "serving_bucket",
    "serving_resource_identity",
    "serving_owner_identity",
    "serving_created_at",
    "serving_encryption_mode",
    "serving_encryption_key_identity",
)


def digest(values):
    """Independent encoding oracle for the schema's frozen tuple algorithm."""
    parts = []
    for name in BINDING_IDENTITY_FIELDS:
        value = values[name]
        if name.endswith("created_at"):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("synthetic-sensitive-incarnation")
            value = (
                value.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError("synthetic-sensitive-tuple")
        parts.append(f"{len(value.encode('utf-8'))}:{value}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def binding_record(**overrides):
    values = {
        "provider": "s3",
        "endpoint": "https://historical-a.test:9443",
        "account_identity": "synthetic-owner-a",
        "region": "region-a",
        "quarantine_bucket": "historical-quarantine",
        "serving_bucket": "historical-serving",
    }
    for purpose in ("quarantine", "serving"):
        values.update(
            {
                f"{purpose}_resource_identity": f"synthetic:{purpose}:a",
                f"{purpose}_owner_identity": "synthetic-owner-a",
                f"{purpose}_created_at": datetime(2025, 1, 1, tzinfo=UTC),
                f"{purpose}_encryption_mode": "synthetic-provider-managed",
                f"{purpose}_encryption_key_identity": "",
            }
        )
    values.update(overrides)
    return SimpleNamespace(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        identity_sha256=digest(values),
        **values,
    )


def access_mapping():
    return {
        "quarantine": {
            "access_key_ref": "HISTORICAL_Q_ACCESS",
            "secret_key_ref": "HISTORICAL_Q_SECRET",
        },
        "serving": {
            "access_key_ref": "HISTORICAL_S_ACCESS",
            "secret_key_ref": "HISTORICAL_S_SECRET",
            "token_ref": "HISTORICAL_S_TOKEN",
        },
        "posture_credential_ref": "approved.gcs-posture:old",
    }


def credentials():
    return {
        "HISTORICAL_Q_ACCESS": "synthetic-q-access-secret",
        "HISTORICAL_Q_SECRET": "synthetic-q-secret",
        "HISTORICAL_S_ACCESS": "synthetic-s-access-secret",
        "HISTORICAL_S_SECRET": "synthetic-s-secret",
        "HISTORICAL_S_TOKEN": "synthetic-s-session-secret",
    }


class HistoricalSettings:
    def __init__(self, mapping):
        self.FI_ATTACHMENTS_BINDING_ACCESS = mapping

    def __getattr__(self, name):
        raise AssertionError("Historical resolution inspected unrelated settings")


@pytest.fixture
def historical(monkeypatch):
    row = binding_record()
    manager = SimpleNamespace(get=Mock(return_value=copy.deepcopy(row)))
    schema = SimpleNamespace(
        AttachmentStorageBinding=SimpleNamespace(objects=manager),
        BINDING_IDENTITY_FIELDS=BINDING_IDENTITY_FIELDS,
        binding_identity_sha256=digest,
    )
    monkeypatch.setattr(sc, "_binding_schema", lambda: schema)
    settings = HistoricalSettings({row.identity_sha256: access_mapping()})
    # All current defaults are hostile; only explicit historical refs may win.
    monkeypatch.setattr(
        os,
        "environ",
        {
            "STORAGE_BACKEND": "invalid-current-b",
            "S3_ENDPOINT": "https://synthetic-secret@[",
            "MINIO_URL": "https://public-cdn.invalid",
            "S3_ACCESS_KEY": "synthetic-global",
            "MINIO_ROOT_USER": "synthetic-root",
            "SSL_CERT_FILE": "/synthetic/existing-ca.pem",
            **credentials(),
        },
    )
    return row, settings, schema


def resolve(historical, operation="read"):
    row, settings, _ = historical
    return sc.resolve_attachment_binding_config(
        row.id, operation=operation, settings=settings
    )


def materialize(historical, config=None, environ=None):
    return sc.materialize_attachment_binding_config(
        binding=historical[0],
        config=resolve(historical) if config is None else config,
        environ=credentials() if environ is None else environ,
    )


@pytest.mark.parametrize("operation", ["admit", "write", "read", "erase"])
def test_historical_a_survives_invalid_default_b(historical, operation):
    with pytest.raises(sc.StorageConfigurationError):
        sc.resolve_attachment_storage_config({})
    before = copy.deepcopy(vars(historical[0]))
    config = resolve(historical, operation)
    result = materialize(historical, config)
    assert vars(historical[0]) == before
    assert result.storage.backend == "s3"
    assert result.storage.endpoint_url == "https://historical-a.test:9443"
    assert result.storage.region == "region-a"
    assert result.storage.ca_certs == "/synthetic/existing-ca.pem"
    assert result.quarantine_bucket == "historical-quarantine"
    assert result.serving_bucket == "historical-serving"
    assert (
        result.serving_credentials.session_token == credentials()["HISTORICAL_S_TOKEN"]
    )
    historical[2].AttachmentStorageBinding.objects.get.assert_called_with(
        pk=historical[0].id
    )


@pytest.mark.parametrize("provider", ["s3", "gcs", "minio"])
def test_transport_uses_persisted_provider_even_for_gcs(historical, provider):
    row = binding_record(provider=provider)
    historical[2].AttachmentStorageBinding.objects.get.return_value = row
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS = {
        row.identity_sha256: access_mapping()
    }
    resolved = sc.resolve_attachment_binding_config(
        row.id, operation="erase", settings=historical[1]
    )
    assert resolved.transport.backend == provider
    assert resolved.transport.endpoint_url == row.endpoint


def test_rotation_only_changes_external_refs_not_binding(historical):
    row, settings, _ = historical
    before = copy.deepcopy(vars(row))
    first = materialize(historical)
    settings.FI_ATTACHMENTS_BINDING_ACCESS[row.identity_sha256]["serving"][
        "secret_key_ref"
    ] = "ROTATED_S_SECRET"
    second = materialize(
        historical,
        environ={**credentials(), "ROTATED_S_SECRET": "synthetic-rotated-secret"},
    )
    assert first.serving_credentials.secret_key != second.serving_credentials.secret_key
    assert vars(row) == before
    assert first.storage == second.storage
    assert first.serving_bucket == second.serving_bucket


@pytest.mark.parametrize("field", BINDING_IDENTITY_FIELDS)
def test_mutated_identity_field_cannot_be_materialized(historical, field):
    config = resolve(historical)
    old = getattr(historical[0], field)
    setattr(
        historical[0],
        field,
        old.replace(year=2024) if isinstance(old, datetime) else old + "changed",
    )
    with pytest.raises(sc.StorageConfigurationError, match="identity_invalid"):
        materialize(historical, config)


def test_complete_tuple_compared_even_if_digest_collides(historical):
    config = resolve(historical)
    historical[0].serving_encryption_key_identity = "synthetic-foreign-key"
    historical[2].binding_identity_sha256 = lambda values: config.identity_sha256
    with pytest.raises(sc.StorageConfigurationError, match="identity_mismatch"):
        materialize(historical, config)


@pytest.mark.parametrize("change", ["id", "digest", "transport", "persisted"])
def test_foreign_binding_or_config_is_rejected(historical, change):
    config = resolve(historical)
    if change == "id":
        historical[0].id = uuid4()
    elif change == "digest":
        config = replace(config, identity_sha256="f" * 64)
    elif change == "transport":
        config = replace(
            config, transport=replace(config.transport, endpoint="current-b.invalid")
        )
    else:
        row = historical[2].AttachmentStorageBinding.objects.get.return_value
        row.serving_bucket = "foreign-bucket"
        row.identity_sha256 = digest(vars(row))
    with pytest.raises(sc.StorageConfigurationError, match="identity_mismatch"):
        materialize(historical, config)


@pytest.mark.parametrize("reference", ["missing", "empty", "wrong_type"])
def test_declared_missing_token_does_not_fall_back(historical, reference):
    env = credentials()
    env.pop("HISTORICAL_S_TOKEN")
    if reference == "empty":
        env["HISTORICAL_S_TOKEN"] = ""
    elif reference == "wrong_type":
        env["HISTORICAL_S_TOKEN"] = {"synthetic-secret": "must-not-print"}
    env["AWS_SESSION_TOKEN"] = "synthetic-global-token"
    with pytest.raises(sc.StorageConfigurationError):
        materialize(historical, environ=env)


def test_absent_token_reference_is_optional(historical):
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS[historical[0].identity_sha256][
        "serving"
    ].pop("token_ref")
    assert materialize(historical).serving_credentials.session_token is None


@pytest.mark.parametrize(
    "reference",
    [
        "lowercase",
        "BAD/FILE",
        "1INVALID",
        "",
        None,
        [],
        "S3_ACCESS_KEY",
        "MINIO_ROOT_PASSWORD",
        "AWS_SESSION_TOKEN",
    ],
)
def test_invalid_or_shared_data_reference_rejected(historical, reference):
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS[historical[0].identity_sha256][
        "quarantine"
    ]["access_key_ref"] = reference
    with pytest.raises(sc.StorageConfigurationError):
        resolve(historical)


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        [],
        {},
        {"quarantine": {}},
        {"quarantine": {}, "serving": {}, "extra": "synthetic-secret"},
    ],
)
def test_missing_or_malformed_access_mapping(historical, mapping):
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS = {
        historical[0].identity_sha256: mapping
    }
    with pytest.raises(sc.StorageConfigurationError):
        resolve(historical)


def test_foreign_fingerprint_never_selects_access(historical):
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS = {"f" * 64: access_mapping()}
    with pytest.raises(sc.StorageConfigurationError, match="access_missing"):
        resolve(historical)


def test_bad_unrelated_entry_does_not_block_valid_binding(historical):
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS["f" * 64] = ["malformed"]
    assert materialize(historical).storage.region == "region-a"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://host.test",
        "https://host.test/",
        "https://host.test/path",
        "https://host.test?token=synthetic-secret",
        "https://host.test#fragment",
        "https://user:synthetic-secret@host.test",
        "https://HOST.test",
        "https://host.test:443",
        "https://host.test\n",
    ],
)
def test_noncanonical_binding_endpoint_rejected(historical, endpoint):
    if "\n" in endpoint:
        row = historical[2].AttachmentStorageBinding.objects.get.return_value
        row.endpoint = endpoint
    else:
        row = binding_record(endpoint=endpoint)
        historical[2].AttachmentStorageBinding.objects.get.return_value = row
    historical[1].FI_ATTACHMENTS_BINDING_ACCESS[row.identity_sha256] = access_mapping()
    with pytest.raises(sc.StorageConfigurationError):
        resolve(historical)


def test_reference_and_materialized_records_are_frozen_and_redacted(historical):
    config = resolve(historical)
    result = materialize(historical, config)
    for obj, field, value in [
        (config, "identity_sha256", "f" * 64),
        (config.quarantine_credentials, "secret_key_ref", "CHANGED"),
        (result.serving_credentials, "secret_key", "changed"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)
    for value in credentials().values():
        assert value not in repr(config) + repr(result)
    assert "HISTORICAL_Q_SECRET" not in repr(config)
    assert "approved.gcs-posture" not in repr(config)


def test_raw_database_error_and_bad_digest_are_content_free(historical):
    historical[2].AttachmentStorageBinding.objects.get.side_effect = RuntimeError(
        "synthetic-provider-secret"
    )
    with pytest.raises(sc.StorageConfigurationError) as caught:
        resolve(historical)
    assert "synthetic-provider-secret" not in "".join(
        traceback.format_exception(caught.value)
    )
    historical[2].AttachmentStorageBinding.objects.get.side_effect = None
    historical[
        2
    ].AttachmentStorageBinding.objects.get.return_value.identity_sha256 = (
        "synthetic-sensitive-digest"
    )
    with pytest.raises(sc.StorageConfigurationError) as caught:
        resolve(historical)
    assert "synthetic-sensitive-digest" not in "".join(
        traceback.format_exception(caught.value)
    )


def test_operation_validation_precedes_model_lookup(monkeypatch):
    monkeypatch.setattr(
        sc, "_binding_schema", Mock(side_effect=AssertionError("must-not-load"))
    )
    with pytest.raises(sc.StorageConfigurationError, match="operation_invalid"):
        sc.resolve_attachment_binding_config(
            uuid4(), operation="foreign", settings=object()
        )


class ApprovedPosture:
    def __repr__(self):
        raise AssertionError("Credential object must not be represented")


def test_posture_resolution_only_selects_explicit_typed_runtime_object(historical):
    config = resolve(historical)
    runtime = ApprovedPosture()
    assert (
        sc.resolve_attachment_posture_credentials(
            config,
            supplied={config.posture_credential_ref: runtime},
            expected_type=ApprovedPosture,
        )
        is runtime
    )
    assert (
        sc.resolve_attachment_posture_credentials(
            replace(config, posture_credential_ref=None),
            supplied={},
            expected_type=ApprovedPosture,
        )
        is None
    )
    assert (
        materialize(historical, config).storage.backend == "s3"
    )  # no posture dependency for data config
    with pytest.raises(sc.StorageConfigurationError, match="posture_reference_missing"):
        sc.resolve_attachment_posture_credentials(
            config, supplied={}, expected_type=ApprovedPosture
        )


@pytest.mark.parametrize(
    "value",
    [
        "synthetic-token",
        "/synthetic/credential.json",
        {"token": "synthetic-secret"},
        None,
        ApprovedPosture,
        1,
    ],
)
def test_posture_rejects_data_values_paths_mappings_and_constructors(historical, value):
    config = resolve(historical)
    with pytest.raises(
        sc.StorageConfigurationError, match="posture_credentials_invalid"
    ):
        sc.resolve_attachment_posture_credentials(
            config,
            supplied={config.posture_credential_ref: value},
            expected_type=ApprovedPosture,
        )


@pytest.mark.parametrize("raw", ["[", "[]", "null", '{"duplicate":{},"duplicate":{}}'])
def test_malformed_reference_delivery_is_redacted(raw):
    with pytest.raises(sc.StorageConfigurationError, match="access_invalid"):
        sc.attachment_binding_access_settings(
            environ={"FI_ATTACHMENTS_BINDING_ACCESS": raw}
        )


def test_reference_delivery_parses_mapping_only():
    assert sc.attachment_binding_access_settings(environ={}) == {}
    assert sc.attachment_binding_access_settings(
        environ={
            "FI_ATTACHMENTS_BINDING_ACCESS": json.dumps({"f" * 64: access_mapping()})
        }
    ) == {"f" * 64: access_mapping()}


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_settings_import_does_not_touch_default_endpoint_or_binding_models(enabled):
    root = Path(__file__).resolve().parents[2]
    code = """
import builtins, socket, sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError("No network during settings import")
socket.create_connection = forbidden
socket.socket.connect = forbidden
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith("tracer.models"):
        raise AssertionError("No ORM import during settings import")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import django
django.setup()
from django.conf import settings
assert "tfc.utils.storage_client" not in sys.modules
assert "minio" not in sys.modules
assert "boto3" not in sys.modules
assert "google.auth" not in sys.modules
if not settings.FI_ATTACHMENTS_ENABLED:
    assert settings.FI_ATTACHMENTS_STORAGE["resolved"] is None
print("lazy-settings-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root)],
        env={
            "PATH": os.defpath,
            "DJANGO_SETTINGS_MODULE": "tfc.settings.attachments",
            "FI_ATTACHMENTS_ENABLED": enabled,
            "S3_ENDPOINT": "https://synthetic-secret@[",
            "FI_ATTACHMENTS_BINDING_ACCESS": "malformed-synthetic-reference-json",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "lazy-settings-ok" in result.stdout


def test_posture_name_never_dereferences_environment(historical, monkeypatch):
    config = resolve(historical)
    monkeypatch.setenv(config.posture_credential_ref, "synthetic-env-token-not-posture")
    with pytest.raises(sc.StorageConfigurationError, match="posture_reference_missing"):
        sc.resolve_attachment_posture_credentials(
            config, supplied={}, expected_type=ApprovedPosture
        )


def test_posture_factory_is_not_a_supplied_credential(historical):
    config = resolve(historical)

    def forbidden_factory():
        raise AssertionError("Never invoke credential factories")

    with pytest.raises(
        sc.StorageConfigurationError, match="posture_credentials_invalid"
    ):
        sc.resolve_attachment_posture_credentials(
            config,
            supplied={config.posture_credential_ref: forbidden_factory},
            expected_type=type(forbidden_factory),
        )


def test_malformed_secret_value_is_never_represented(historical):
    env = credentials()
    env["HISTORICAL_S_SECRET"] = ApprovedPosture()
    with pytest.raises(sc.StorageConfigurationError, match="credentials_invalid"):
        materialize(historical, environ=env)


@pytest.mark.django_db(transaction=True)
def test_production_settings_resolve_persisted_access_without_current_default():
    """Exercise the real settings import and binding lookup, not provider I/O."""
    from django.db import connection

    from tracer.tests.attachment_fixtures import storage_binding

    binding = storage_binding()
    database = connection.settings_dict
    assert database["NAME"] == database["TEST"]["NAME"]
    code = """
import sys
from uuid import UUID
import django
from django.conf import settings
from tfc.utils.storage_config import resolve_attachment_binding_config, StorageConfigurationError

django.setup()
try:
    settings.FI_ATTACHMENTS_STORAGE["resolved"]
except StorageConfigurationError:
    pass
else:
    raise AssertionError("Invalid admission default must be rejected")
for operation in ("admit", "read", "erase"):
    access = resolve_attachment_binding_config(UUID(sys.argv[1]), operation=operation, settings=settings)
    assert access.identity_sha256 == sys.argv[2]
    assert access.serving_credentials.secret_key_ref == "HISTORICAL_S_SECRET"
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(binding.id), binding.identity_sha256],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
            "PG_DB": database["NAME"],
            "PG_USER": database["USER"],
            "PG_PASSWORD": database["PASSWORD"],
            "PGBOUNCER_HOST": database["HOST"],
            "PGBOUNCER_PORT": str(database["PORT"]),
            "DJANGO_CACHE_BACKEND": "redis",
            "REDIS_CACHE_URL": "redis://localhost:16379/0",
            "FI_ATTACHMENTS_ENABLED": "true",
            "FI_ATTACHMENTS_STORAGE_BACKEND": "invalid-current-default",
            "FI_ATTACHMENTS_BINDING_ACCESS": json.dumps(
                {binding.identity_sha256: access_mapping()}
            ),
        },
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0
