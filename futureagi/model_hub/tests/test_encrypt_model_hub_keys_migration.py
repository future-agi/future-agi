"""Migration 0125 encrypts every model-hub key at rest, and only an explicit
command takes them back.

The forward step is run against the live models (the way the management
command runs it) and once through the migration executor; the reverse is the
explicit ``model_hub_keys_at_rest --to-legacy --confirm`` command, after which
``migrate`` may go back.
"""

import base64
import importlib
import json
from io import StringIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.apps import apps as live_apps
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

from integrations.services.credentials import CredentialManager
from model_hub.models.api_key import ApiKey, SecretModel, SecretType
from model_hub.models.custom_models import CustomAIModel

PREFIX = "enc::"
MIGRATION = ("model_hub", "0125_encrypt_model_hub_keys_at_rest")
BEFORE = ("model_hub", "0124_widen_model_hub_key_columns")
COMMAND = "model_hub_keys_at_rest"


@pytest.fixture
def migration():
    return importlib.import_module(
        "model_hub.migrations.0125_encrypt_model_hub_keys_at_rest"
    )


def _legacy(clear):
    return base64.b64encode(settings.SECRET_KEY[:16].encode() + clear.encode()).decode()


def _old_reader(stored):
    """The legacy ``ApiKey.decrypt_key``, in effect."""
    try:
        return base64.b64decode(stored)[16:].decode()
    except Exception:
        return None


def _clear_of(stored):
    assert stored.startswith(PREFIX), stored
    return CredentialManager.decrypt(stored[len(PREFIX) :].encode())["v"]


def _raw(model, pk, column):
    table = connection.ops.quote_name(model._meta.db_table)
    col = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {col}::text FROM {table} WHERE id = %s", [str(pk)])
        text = cursor.fetchone()[0]
    return json.loads(text) if column != "key" and text is not None else text


def _api_key(organization, user, provider, *, key=None, config=None, deleted=False):
    row = ApiKey.no_workspace_objects.create(
        organization=organization, user=user, provider=provider, key="placeholder"
    )
    ApiKey._base_manager.filter(pk=row.pk).update(
        key=key, config_json=config, deleted=deleted
    )
    return row


def _custom_model(organization, user, workspace, name, key_config):
    row = CustomAIModel.objects.create(
        user_model_id=name,
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={"key": "placeholder"},
    )
    CustomAIModel._base_manager.filter(pk=row.pk).update(key_config=key_config)
    return row


@pytest.fixture
def legacy_rows(organization, user, workspace):
    """One row of each kind in the legacy encoding, a soft-deleted one too."""
    text = _api_key(
        organization, user, "openai", key=_legacy("sk-test-kar-mig-text-not-real")
    )
    azure_clear = {
        "api_key": "kar-mig-azure-not-real",
        "api_base": "https://kar-mig.openai.azure.example.test",
        "api_version": "2024-06-01",
    }
    azure = _api_key(
        organization,
        user,
        "azure",
        config={k: _legacy(v) for k, v in azure_clear.items()},
    )
    deleted = _api_key(
        organization,
        user,
        "anthropic",
        key=_legacy("sk-test-kar-mig-deleted-not-real"),
        deleted=True,
    )
    custom = _custom_model(
        organization,
        user,
        workspace,
        "kar-mig-custom",
        {"key": _legacy("sk-test-kar-mig-custom-not-real"), "headers": {}},
    )
    return {
        "text": (text, "sk-test-kar-mig-text-not-real"),
        "azure": (azure, azure_clear),
        "deleted": (deleted, "sk-test-kar-mig-deleted-not-real"),
        "custom": (custom, {"key": "sk-test-kar-mig-custom-not-real", "headers": {}}),
    }


def _assert_encrypted(rows):
    text, clear = rows["text"]
    assert _clear_of(_raw(ApiKey, text.pk, "key")) == clear
    deleted, clear = rows["deleted"]
    assert _clear_of(_raw(ApiKey, deleted.pk, "key")) == clear
    azure, clear = rows["azure"]
    stored = _raw(ApiKey, azure.pk, "config_json")
    assert {k: _clear_of(v) for k, v in stored.items()} == clear
    custom, clear = rows["custom"]
    stored = _raw(CustomAIModel, custom.pk, "key_config")
    assert _clear_of(stored["key"]) == clear["key"]
    assert stored["headers"] == {}


# --- forwards ----------------------------------------------------------------


@pytest.mark.django_db
def test_forwards_encrypts_every_stored_key_soft_deleted_rows_included(
    migration, legacy_rows
):
    migration.forwards(live_apps, None)
    _assert_encrypted(legacy_rows)
    # The platform reads back exactly the key it read before.
    text, clear = legacy_rows["text"]
    assert ApiKey._base_manager.get(pk=text.pk).actual_key == clear
    azure, clear = legacy_rows["azure"]
    assert ApiKey._base_manager.get(pk=azure.pk).actual_json == clear


@pytest.mark.django_db
def test_forwards_is_idempotent(migration, legacy_rows):
    migration.forwards(live_apps, None)
    text, _ = legacy_rows["text"]
    azure, _ = legacy_rows["azure"]
    first = (_raw(ApiKey, text.pk, "key"), _raw(ApiKey, azure.pk, "config_json"))
    migration.forwards(live_apps, None)
    assert (_raw(ApiKey, text.pk, "key"), _raw(ApiKey, azure.pk, "config_json")) == (
        first
    )


@pytest.mark.django_db
def test_forwards_never_prints_or_logs_a_key(migration, legacy_rows, capsys, caplog):
    caplog.set_level("DEBUG")
    migration.forwards(live_apps, None)
    out = capsys.readouterr()
    text = out.out + out.err + caplog.text
    for _row, clear in legacy_rows.values():
        values = clear.values() if isinstance(clear, dict) else [clear]
        for value in values:
            if isinstance(value, str) and len(value) > 8:
                assert value not in text
                assert _legacy(value) not in text


@pytest.mark.django_db
def test_a_value_the_legacy_reader_cannot_decode_is_encrypted_as_stored(
    migration, organization, user
):
    # The legacy reader returns None for it; it is still a stored secret and
    # is encrypted as stored.
    row = _api_key(organization, user, "openai", key="not*base64*kar0")
    assert _old_reader("not*base64*kar0") is None
    migration.forwards(live_apps, None)
    assert _clear_of(_raw(ApiKey, row.pk, "key")) == "not*base64*kar0"


@pytest.mark.django_db
def test_a_key_this_deployment_cannot_decrypt_is_left_alone(
    migration, organization, user, capsys
):
    token = Fernet(Fernet.generate_key()).encrypt(b'{"v": "x"}').decode()
    row = _api_key(organization, user, "openai", key=PREFIX + token)
    migration.forwards(live_apps, None)
    assert _raw(ApiKey, row.pk, "key") == PREFIX + token
    assert str(row.pk) in capsys.readouterr().out  # reported by id, for rotation


@pytest.mark.django_db
def test_forwards_never_encrypts_under_a_throwaway_key(
    migration, legacy_rows, settings, capsys
):
    # settings.py makes up a key per process when INTEGRATION_ENCRYPTION_KEY
    # is unset on a local install; anything encrypted with it is lost on the
    # next restart, so the keys stay readable in the old encoding instead.
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    migration.forwards(live_apps, None)
    text, _ = legacy_rows["text"]
    assert not _raw(ApiKey, text.pk, "key").startswith(PREFIX)
    out = capsys.readouterr().out
    assert "INTEGRATION_ENCRYPTION_KEY" in out
    assert COMMAND in out


@pytest.mark.django_db
def test_forwards_leaves_keys_readable_without_an_encryption_key(
    migration, legacy_rows, settings, capsys
):
    # Without INTEGRATION_ENCRYPTION_KEY the keys keep working in the legacy
    # encoding, the upgrade goes on, and the operator is told what to do.
    settings.INTEGRATION_ENCRYPTION_KEY = ""
    migration.forwards(live_apps, None)
    text, clear = legacy_rows["text"]
    assert not _raw(ApiKey, text.pk, "key").startswith(PREFIX)
    assert ApiKey._base_manager.get(pk=text.pk).actual_key == clear
    out = capsys.readouterr().out
    assert "INTEGRATION_ENCRYPTION_KEY" in out
    assert COMMAND in out


# What an operator can leave in INTEGRATION_ENCRYPTION_KEY that Fernet refuses:
# a placeholder such as your-fernet-key, and valid base64 of the wrong length.
# Never a real key.
MALFORMED_KEYS = (
    "your-fernet-key",
    base64.urlsafe_b64encode(b"sixteen-byte-key").decode(),
)
MALFORMED_IDS = ("placeholder", "wrong_length")


_LEGACY_ROW_COLUMN = {
    "text": "key",
    "azure": "config_json",
    "deleted": "key",
    "custom": "key_config",
}


def _raw_rows(rows):
    """Each ``legacy_rows`` row's key column as the database holds it."""
    return {
        name: _raw(type(row), row.pk, _LEGACY_ROW_COLUMN[name])
        for name, (row, _) in rows.items()
    }


@pytest.mark.django_db
@pytest.mark.parametrize("malformed", MALFORMED_KEYS, ids=MALFORMED_IDS)
def test_forwards_under_a_malformed_key_skips_and_reports(
    migration, legacy_rows, settings, capsys, malformed
):
    # Under a malformed key the upgrade goes on, the keys keep working in the
    # legacy encoding, and the operator is told how many rows stay unencrypted
    # and how to fix it.
    settings.INTEGRATION_ENCRYPTION_KEY = malformed
    before = _raw_rows(legacy_rows)
    migration.forwards(live_apps, None)
    assert _raw_rows(legacy_rows) == before
    text, clear = legacy_rows["text"]
    assert ApiKey._base_manager.get(pk=text.pk).actual_key == clear
    azure, clear = legacy_rows["azure"]
    assert ApiKey._base_manager.get(pk=azure.pk).actual_json == clear
    out = capsys.readouterr().out
    assert "4 row(s) left in the old encoding" in out
    assert "INTEGRATION_ENCRYPTION_KEY is not a valid Fernet key" in out
    assert f"{COMMAND} --encrypt" in out
    assert malformed not in out


@pytest.mark.django_db
def test_check_under_a_malformed_key_reports_the_rows_and_names_the_key(
    migration, legacy_rows, organization, user, settings
):
    # A key saved while the deployment had its valid key cannot be read under
    # a malformed one; --check counts it, counts the rows left in the old
    # encoding, and says the fix is the key, not --encrypt alone (which cannot
    # encrypt anything until the key is valid).
    earlier = ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider="groq",
        key="sk-test-karb-saved-under-the-valid-key-not-real",
    )
    assert _raw(ApiKey, earlier.pk, "key").startswith(PREFIX)
    malformed = MALFORMED_KEYS[0]
    settings.INTEGRATION_ENCRYPTION_KEY = malformed

    out = StringIO()
    with pytest.raises(CommandError) as error:
        call_command(COMMAND, "--check", stdout=out)
    assert "4 not encrypted, 1 cannot be decrypted" in out.getvalue()
    message = str(error.value)
    assert "INTEGRATION_ENCRYPTION_KEY is not a valid Fernet key" in message
    assert f"{COMMAND} --encrypt" in message
    assert malformed not in out.getvalue() + message

    before = _raw_rows(legacy_rows)
    out = StringIO()
    call_command(COMMAND, "--encrypt", stdout=out)
    assert _raw_rows(legacy_rows) == before
    said = out.getvalue()
    assert "4 row(s) left in the old encoding" in said
    assert "1 row(s) hold a key this deployment cannot decrypt" in said
    assert "INTEGRATION_ENCRYPTION_KEY is not a valid Fernet key" in said
    assert malformed not in said


@pytest.mark.django_db
def test_a_token_an_old_pod_wrapped_is_reported_and_left_alone(
    migration, organization, user, capsys
):
    # An earlier release's pod that re-saves a row this release encrypted
    # wraps the token in the old encoding. Its key cannot be recovered: the
    # row is counted and reported by id (never a value) so its org can enter
    # the key again, and never encrypted over.
    victim = _api_key(organization, user, "openai", key=_legacy("sk-test-kar-v"))
    migration.forwards(live_apps, None)
    token = _raw(ApiKey, victim.pk, "key")
    wrapped = _api_key(organization, user, "groq", key=_legacy(token))
    capsys.readouterr()

    out = StringIO()
    call_command(COMMAND, "--check", stdout=out)
    assert "0 not encrypted, 1 cannot be decrypted" in out.getvalue()

    migration.forwards(live_apps, None)
    assert _raw(ApiKey, wrapped.pk, "key") == _legacy(token)
    assert str(wrapped.pk) in capsys.readouterr().out


def test_the_migration_is_not_one_transaction(migration):
    assert migration.Migration.atomic is False


@pytest.mark.django_db
def test_forwards_reads_and_commits_in_bounded_batches(migration, organization, user):
    rows = [
        _api_key(organization, user, "openai", key=_legacy(f"sk-test-kar-b{n}-x"))
        for n in range(5)
    ]
    with (
        patch.object(migration, "BATCH_SIZE", 2),
        CaptureQueriesContext(connection) as queries,
    ):
        migration.forwards(live_apps, None)
    selects = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT")
        and "model_hub_apikey" in q["sql"]
    ]
    savepoints = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith("SAVEPOINT")
    ]
    assert len(selects) >= 3, selects
    for sql in selects:
        assert "LIMIT 2" in sql.upper(), sql
        assert "FOR UPDATE" in sql.upper(), sql
    assert len(savepoints) >= len(selects)
    for n, row in enumerate(rows):
        assert _clear_of(_raw(ApiKey, row.pk, "key")) == f"sk-test-kar-b{n}-x"


# --- reverse only by an explicit command -------------------------------------


@pytest.mark.django_db
def test_backwards_refuses_while_encrypted_keys_remain(migration, legacy_rows):
    migration.forwards(live_apps, None)
    with pytest.raises(RuntimeError, match=f"{COMMAND} --to-legacy --confirm"):
        migration.backwards(live_apps, None)
    _assert_encrypted(legacy_rows)


@pytest.mark.django_db
def test_the_explicit_command_restores_the_old_encoding(migration, legacy_rows):
    migration.forwards(live_apps, None)
    with pytest.raises(CommandError, match="--confirm"):
        call_command(COMMAND, "--to-legacy", stdout=StringIO())
    _assert_encrypted(legacy_rows)

    call_command(COMMAND, "--to-legacy", "--confirm", stdout=StringIO())
    text, clear = legacy_rows["text"]
    assert _old_reader(_raw(ApiKey, text.pk, "key")) == clear
    azure, clear = legacy_rows["azure"]
    stored = _raw(ApiKey, azure.pk, "config_json")
    assert {k: _old_reader(v) for k, v in stored.items()} == clear
    migration.backwards(live_apps, None)  # nothing encrypted is left


@pytest.mark.django_db
def test_the_check_and_encrypt_commands_find_and_finish_leftovers(
    migration, legacy_rows
):
    out = StringIO()
    with pytest.raises(CommandError, match="not encrypted"):
        call_command(COMMAND, "--check", stdout=out)
    assert "4 not encrypted" in out.getvalue()

    call_command(COMMAND, "--encrypt", stdout=StringIO())
    _assert_encrypted(legacy_rows)
    out = StringIO()
    call_command(COMMAND, "--check", stdout=out)
    assert "0 not encrypted" in out.getvalue()


@pytest.mark.django_db(transaction=True)
def test_migrate_runs_it_and_restores_every_leaf(organization, user):
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([BEFORE])
        row = _api_key(
            organization, user, "openai", key=_legacy("sk-test-kar-exec-not-real")
        )
        custom = _custom_model(
            organization,
            user,
            None,
            "kar-exec-custom",
            {"key": _legacy("sk-test-kar-exec-custom"), "api_base": _legacy("u")},
        )
        secret = SecretModel.no_workspace_objects.create(
            name="kar exec secret",
            secret_type=SecretType.API_KEY,
            key="placeholder",
            organization=organization,
        )
        SecretModel._base_manager.filter(pk=secret.pk).update(
            key=_legacy("kar-exec-secret")
        )
        MigrationExecutor(connection).migrate([MIGRATION])
        assert _clear_of(_raw(ApiKey, row.pk, "key")) == "sk-test-kar-exec-not-real"
        stored = _raw(CustomAIModel, custom.pk, "key_config")
        assert {k: _clear_of(v) for k, v in stored.items()} == {
            "key": "sk-test-kar-exec-custom",
            "api_base": "u",
        }
        assert _clear_of(_raw(SecretModel, secret.pk, "key")) == "kar-exec-secret"
    finally:
        final = MigrationExecutor(connection)
        final.migrate(final.loader.graph.leaf_nodes())


# --- hosted operators can run the command; application processes cannot -----


def _hosted(monkeypatch, *, service_type, mode):
    monkeypatch.setenv("ENV_TYPE", "production")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    monkeypatch.setenv("SERVICE_TYPE", service_type)
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", mode)
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")


@pytest.mark.parametrize(
    "line", [["--check"], ["--encrypt"], ["--to-legacy", "--confirm"]], ids=" ".join
)
def test_the_hosted_operator_job_may_run_the_command(monkeypatch, line):
    from django.core.management import get_commands

    from model_hub.apps import (
        OPERATOR_STARTUP_MUTATION_COMMANDS,
        explicit_management_mutation_authorized,
    )

    assert get_commands().get(COMMAND) == "model_hub"
    assert COMMAND in OPERATOR_STARTUP_MUTATION_COMMANDS
    _hosted(monkeypatch, service_type="bootstrap", mode="operator")
    assert explicit_management_mutation_authorized(["manage.py", COMMAND, *line])


@pytest.mark.parametrize("service_type", ["backend", "worker", "temporal-worker"])
def test_hosted_application_processes_refuse_the_command(monkeypatch, service_type):
    from model_hub.apps import explicit_management_mutation_authorized

    _hosted(monkeypatch, service_type=service_type, mode="disabled")
    assert not explicit_management_mutation_authorized(["manage.py", COMMAND])
