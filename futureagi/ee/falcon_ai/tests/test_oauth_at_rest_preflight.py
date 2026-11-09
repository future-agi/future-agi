"""r2b migration and key regressions; all key material is test-only."""

import base64
import importlib
import secrets
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from agentcc.services.credential_manager import encrypt_token
from ee.falcon_ai.credentials import decrypt_falcon_token, encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_oauth_at_rest as support
from ee.falcon_ai.tests.test_oauth_at_rest import (
    FIELDS,
    MIGRATION,
    OAUTH_FIELDS,
    check,
    row,
)
from integrations.services.credentials import CredentialManager

pytestmark = pytest.mark.django_db
connector = support.connector


@pytest.fixture(autouse=True)
def persistent_test_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = False


def convert():
    migration = importlib.import_module(MIGRATION)
    historical = (
        MigrationLoader(connection)
        .project_state([("falcon_ai", "0009_resync_builtin_skills")])
        .apps
    )
    migration.encrypt_connector_credentials(historical, connection.schema_editor())


def snapshot(obj):
    stored = row(obj)
    return tuple(getattr(stored, field) for field in FIELDS)


def put(obj, **fields):
    MCPConnector._base_manager.using(connection.alias).filter(pk=obj.pk).update(
        **fields
    )


def refusal(operation, expected="RuntimeError"):
    caught = None
    try:
        operation()
    except Exception as error:
        caught = error
    check(type(caught).__name__ == expected, "missing typed refusal")
    check(caught.__suppress_context__, "exception chain not suppressed")
    return caught


def test_migration_rerun_is_byte_unchanged(connector):
    value = base64.b64encode(secrets.token_bytes(24).hex().encode()).decode()
    put(connector, **dict.fromkeys(OAUTH_FIELDS, value))
    convert()
    migrated = snapshot(connector)
    convert()
    check(snapshot(connector) == migrated, "rerun changed ciphertext")


def test_migration_mixed_fields_and_rows_preserves_authenticated_bytes(connector):
    cipher = encrypt_token(secrets.token_urlsafe(32))
    logical = "enc::" + secrets.token_urlsafe(32)
    encoded = base64.b64encode(logical.encode()).decode()
    put(connector, oauth_access_token=cipher, oauth_refresh_token=encoded)
    other = MCPConnector._base_manager.create(
        organization_id=connector.organization_id,
        workspace_id=connector.workspace_id,
        name="mixed-" + secrets.token_hex(8),
        server_url=connector.server_url,
        deleted=True,
        **dict.fromkeys(FIELDS, cipher),
    )
    convert()
    stored = row(connector)
    check(stored.oauth_access_token == cipher, "runtime writer ciphertext changed")
    check(
        decrypt_falcon_token(stored.oauth_refresh_token) == logical,
        "legacy prefix lost",
    )
    check(snapshot(other) == (cipher,) * len(FIELDS), "encrypted deleted row changed")


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("representation", ["wrong_key", "raw_marker", "bad_envelope"])
def test_marker_authentication_failure_aborts_all_rows(
    connector, field, representation, settings
):
    if representation == "wrong_key":
        value = encrypt_token(secrets.token_urlsafe(32))
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    elif representation == "bad_envelope":
        value = "enc::" + CredentialManager.encrypt({"other": "test-only"}).decode()
    else:
        value = "enc::" + secrets.token_urlsafe(32)
    put(connector, **dict.fromkeys(FIELDS, base64.b64encode(b"test-only").decode()))
    other = MCPConnector._base_manager.create(
        organization_id=connector.organization_id,
        workspace_id=connector.workspace_id,
        name="invalid-" + secrets.token_hex(8),
        server_url=connector.server_url,
        **{field: value},
    )
    before = (snapshot(connector), snapshot(other))
    error = refusal(convert)
    check(
        str(error) == "Connector credential migration failed",
        "migration error not static",
    )
    check(
        (snapshot(connector), snapshot(other)) == before, "atomic refusal changed rows"
    )


@pytest.mark.parametrize("problem", ["ephemeral", "missing", "malformed"])
def test_key_preflight_migration_refuses_before_any_rewrite(
    connector, settings, problem
):
    put(connector, oauth_access_token=base64.b64encode(b"test-only").decode())
    before = snapshot(connector)
    if problem == "ephemeral":
        settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    else:
        settings.INTEGRATION_ENCRYPTION_KEY = (
            "" if problem == "missing" else "invalid-test-key"
        )
    # A rollback alone is not enough: the unsafe key must be refused before UPDATE.
    updates = []

    def observe(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("UPDATE"):
            updates.append(True)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        refusal(convert)
    check(not updates, "key preflight happened after rewrite")
    check(snapshot(connector) == before, "unsafe key changed persistent credentials")


@pytest.mark.parametrize("problem", ["ephemeral", "missing", "malformed"])
@pytest.mark.parametrize("operation", ["read", "write", "read_empty", "write_empty"])
def test_key_preflight_runtime_refuses(settings, problem, operation):
    plaintext = secrets.token_urlsafe(32)
    ciphertext = encrypt_falcon_token(plaintext)
    if problem == "ephemeral":
        settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    else:
        settings.INTEGRATION_ENCRYPTION_KEY = (
            "" if problem == "missing" else "invalid-test-key"
        )
    function = (
        decrypt_falcon_token if operation.startswith("read") else encrypt_falcon_token
    )
    value = ciphertext if operation.startswith("read") else plaintext
    if operation.endswith("empty"):
        value = ""
    error = refusal(lambda: function(value), "FalconCredentialError")
    check(str(error) == "Connector credential key unavailable", "key error not static")


def test_persistent_key_roundtrip_and_shared_helpers_unchanged(settings):
    value = "enc::" + secrets.token_urlsafe(32)
    check(
        decrypt_falcon_token(encrypt_falcon_token(value)) == value, "roundtrip failed"
    )
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    # Other applications' existing policy is not changed by Falcon's preflight.
    check(
        encrypt_token("shared-test-only").startswith("enc::"), "shared helper changed"
    )


def test_migration_atomic_failure_rolls_back_earlier_write(connector):
    put(connector, oauth_access_token=base64.b64encode(b"test-only").decode())
    before = snapshot(connector)
    migration = importlib.import_module(MIGRATION)
    original = migration._encrypt_plaintext
    calls = []

    def fail_second(value):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("test-only failure")
        return original(value)

    other = MCPConnector._base_manager.create(
        organization_id=connector.organization_id,
        workspace_id=connector.workspace_id,
        name="rollback-" + secrets.token_hex(8),
        server_url=connector.server_url,
        oauth_access_token=base64.b64encode(b"test-only").decode(),
    )
    other_before = snapshot(other)
    with patch.object(migration, "_encrypt_plaintext", side_effect=fail_second):
        refusal(convert)
    check(len(calls) == 2, "fault did not follow first row")
    check(
        snapshot(connector) == before and snapshot(other) == other_before,
        "partial migration committed",
    )
