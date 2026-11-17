"""Migration 0010 keeps a connector row it cannot read and converts the rest.

Every credential here is generated per test and marked test-only.
"""

import base64
import secrets

import pytest
from cryptography.fernet import Fernet
from django.db import connection

from agentcc.services.credential_manager import encrypt_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_oauth_at_rest as support
from ee.falcon_ai.tests.test_oauth_at_rest import (
    FIELDS,
    OAUTH_FIELDS,
    assert_encrypted,
    check,
    row,
)
from ee.falcon_ai.tests.test_oauth_at_rest_preflight import (
    convert,
    persistent_test_key,  # noqa: F401 -- pytest fixture
    put,
    refusal,
    snapshot,
)
from integrations.services.credentials import CredentialManager

pytestmark = pytest.mark.django_db
connector = support.connector
COUNT = "Connector credentials: {} connector(s)"


def synthetic():
    return "test-only-" + secrets.token_hex(8)


def legacy_values():
    """What 1.58.0 left: base64 OAuth values and a plain header value."""
    logical, header = synthetic(), synthetic()
    encoded = base64.b64encode(logical.encode()).decode()
    values = dict.fromkeys(OAUTH_FIELDS, encoded)
    values["auth_header_value"] = header
    return logical, header, values


def unreadable(representation, settings):
    """A stored marker value that does not authenticate under the current key."""
    if representation == "wrong_key":
        value = encrypt_token(synthetic())
        # Encrypted under a key this deployment no longer has.
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
        return value
    if representation == "bad_envelope":
        return "enc::" + CredentialManager.encrypt({"other": synthetic()}).decode()
    # 1.58.0 stored a typed header value that began with the marker as typed.
    return "enc::" + synthetic()


def other_row(connector, **fields):
    return MCPConnector._base_manager.create(
        organization_id=connector.organization_id,
        workspace_id=connector.workspace_id,
        name="unreadable-" + secrets.token_hex(8),
        server_url=connector.server_url,
        **fields,
    )


def observed(operation, seen):
    def observe(execute, sql, params, many, context):
        seen.append(sql.lstrip().upper())
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        operation()


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("representation", ["wrong_key", "raw_marker", "bad_envelope"])
def test_unreadable_row_is_kept_as_saved_and_counted(
    connector, field, representation, settings, capsys
):
    bad = unreadable(representation, settings)
    logical, header, values = legacy_values()
    put(connector, **values)
    # The unreadable row's other fields are legacy too: none of them is touched.
    kept = other_row(connector, **{**values, field: bad})
    before = snapshot(kept)
    capsys.readouterr()
    convert()
    output = capsys.readouterr().out
    check(snapshot(kept) == before, "unreadable row changed")
    stored = row(connector)
    for name in OAUTH_FIELDS:
        assert_encrypted(getattr(stored, name), logical)
    assert_encrypted(stored.auth_header_value, header)
    check(COUNT.format(1) in output, "unreadable row not counted")
    for private in (bad, logical, header, str(kept.pk), kept.pk.hex, kept.name):
        check(private not in output, "migration output names a value or row")


def test_rerun_locks_rows_and_never_rewrites_the_kept_row(connector, capsys):
    _, _, values = legacy_values()
    put(connector, **values)
    values["auth_header_value"] = "enc::" + synthetic()
    kept = other_row(connector, deleted=True, **values)
    before = snapshot(kept)
    first, second = [], []
    capsys.readouterr()
    observed(convert, first)
    converted = snapshot(connector)
    observed(convert, second)
    output = capsys.readouterr().out
    check(
        any(sql.startswith("SELECT") and "FOR UPDATE" in sql for sql in first),
        "rows read without a lock",
    )
    check(
        sum(sql.startswith("UPDATE") for sql in first) == 1,
        "kept row rewritten or legacy row missed",
    )
    check(not any(sql.startswith("UPDATE") for sql in second), "rerun rewrote a row")
    check(snapshot(connector) == converted, "rerun changed ciphertext")
    check(snapshot(kept) == before, "unreadable row changed")
    check(output.count(COUNT.format(1)) == 2, "rerun count differs")


@pytest.mark.parametrize("problem", ["ephemeral", "missing"])
def test_key_preflight_still_stops_before_reading_connectors(
    connector, settings, capsys, problem
):
    _, _, values = legacy_values()
    put(connector, **values)
    other_row(connector, auth_header_value="enc::" + synthetic())
    before = snapshot(connector)
    if problem == "ephemeral":
        settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    else:
        settings.INTEGRATION_ENCRYPTION_KEY = ""
    seen = []
    capsys.readouterr()
    error = refusal(lambda: observed(convert, seen))
    output = capsys.readouterr().out
    check(
        str(error) == "Connector credential migration failed",
        "migration error not static",
    )
    check(
        not any("FALCON_AI_MCPCONNECTOR" in sql for sql in seen),
        "connectors read before the key check",
    )
    check(snapshot(connector) == before, "unsafe key changed persistent credentials")
    check("Connector credentials" not in output, "refused run printed a count")
