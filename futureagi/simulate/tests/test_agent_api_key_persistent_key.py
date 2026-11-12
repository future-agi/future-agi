"""Persistent-key policy at runtime and before the first migration write.

All keys and credentials in these tests are synthetic. The child process uses
settings.configure, not deployment settings, and returns no credential values.
"""

import importlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from simulate.services.agent_api_key_storage import encrypt_api_key
from simulate.tests.test_agent_api_key_migration import MIGRATION, raw, run, table

POLICY_ERROR = "persistent.*INTEGRATION_ENCRYPTION_KEY"


@pytest.fixture
def persistent_key(settings):
    key = Fernet.generate_key().decode()
    settings.INTEGRATION_ENCRYPTION_KEY = key
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = False
    return key


def _unsafe_key(settings, mode):
    if mode == "ephemeral":
        settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    elif mode == "missing":
        settings.INTEGRATION_ENCRYPTION_KEY = ""
    elif mode == "none":
        settings.INTEGRATION_ENCRYPTION_KEY = None
    else:
        settings.INTEGRATION_ENCRYPTION_KEY = "synthetic-invalid-key"


@pytest.mark.parametrize("mode", ["ephemeral", "missing", "none", "malformed"])
@pytest.mark.parametrize("value", ["synthetic-agent-token", "enc::synthetic-raw"])
def test_encrypt_refuses_unsafe_key(settings, persistent_key, mode, value):
    _unsafe_key(settings, mode)
    with pytest.raises(ValueError, match=POLICY_ERROR) as caught:
        encrypt_api_key(value)
    assert value not in str(caught.value)
    assert "synthetic-invalid-key" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("mode", ["ephemeral", "missing", "none", "malformed"])
def test_migration_preflight_precedes_model_access(settings, persistent_key, mode):
    migration = importlib.import_module(MIGRATION)
    _unsafe_key(settings, mode)
    apps = Mock()
    editor = Mock()
    with pytest.raises(ValueError, match=POLICY_ERROR):
        migration.forwards(apps, editor)
    apps.get_model.assert_not_called()


@pytest.mark.django_db(transaction=True, databases={"default", "default_direct"})
@pytest.mark.parametrize("mode", ["ephemeral", "missing", "none", "malformed"])
@pytest.mark.parametrize("alias", ["default", "default_direct"])
def test_refusal_leaves_every_legacy_row_unchanged(
    settings, persistent_key, monkeypatch, mode, alias
):
    migration = importlib.import_module(MIGRATION)
    monkeypatch.setattr(migration, "BATCH_SIZE", 1)
    with table(alias) as (apps, rows, connection, observer):
        # An empty first batch must not be cleared before a bad key is noticed
        # on a later populated row. Include an already-encrypted replay row.
        first = rows.create(api_key="", encrypted_api_key=None)
        second = rows.create(api_key="synthetic-legacy", deleted=True)
        third = rows.create(
            api_key="synthetic-replay",
            encrypted_api_key=encrypt_api_key("synthetic-replay"),
        )
        seeded = [first, second, third]
        before = [raw(observer, row.pk) for row in seeded]
        _unsafe_key(settings, mode)
        updates = []

        def witness(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith("UPDATE"):
                updates.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(witness):
            with pytest.raises(ValueError, match=POLICY_ERROR):
                run(migration.forwards, apps, connection)
        assert updates == []
        assert [raw(observer, row.pk) for row in seeded] == before


@pytest.mark.django_db(transaction=True, databases={"default", "default_direct"})
def test_persistent_migration_can_be_read_by_a_fresh_process(persistent_key):
    migration = importlib.import_module(MIGRATION)
    with table("default_direct") as (apps, rows, connection, observer):
        row = rows.create(api_key="synthetic-restart-token")
        run(migration.forwards, apps, connection)
        plaintext, ciphertext = raw(observer, row.pk)
        assert plaintext is None
        assert ciphertext.startswith("enc::")
        child = """
import json, sys
from django.conf import settings
payload = json.load(sys.stdin)
settings.configure(INTEGRATION_ENCRYPTION_KEY=payload['key'],
                   INTEGRATION_ENCRYPTION_KEY_EPHEMERAL=False)
sys.path.insert(0, sys.argv[1])
from simulate.services.agent_api_key_storage import decrypt_api_key
assert decrypt_api_key(payload['ciphertext']) == 'synthetic-restart-token'
print('PERSISTENT_READ_OK')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", child,
             str(Path(__file__).resolve().parents[2])],
            input=json.dumps({"key": persistent_key, "ciphertext": ciphertext}),
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert completed.returncode == 0
        assert completed.stdout.strip() == "PERSISTENT_READ_OK"
        assert completed.stderr == ""
