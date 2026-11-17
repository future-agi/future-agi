"""The migrations that encrypt stored secrets in place need a stable key.

0047, 0050, 0051 and 0056 (org-config secrets, their other spellings, alert
channel URLs, MCP secrets into the vault) and 0076 and 0077 (webhook headers and
secrets) encrypt with INTEGRATION_ENCRYPTION_KEY. When a migration has something
to encrypt, it refuses to run unless that key is set, is a Fernet key, and is not
one made up for this process (INTEGRATION_ENCRYPTION_KEY_EPHEMERAL): a key no
later process has would leave every stored secret unreadable. The refusal names
INTEGRATION_ENCRYPTION_KEY and asks for a stable key, and the stored value is
left as it was. With nothing to encrypt the migration runs, so a fresh install
is never blocked; with a stable key it encrypts as it always has, the key an
install generates included.

Each migration is applied by itself through the migration executor, on its own
project state and on connections["default"] itself, so it runs inside the
migration boundary the way ``migrate`` runs it. It is not recorded again, and
nothing else is migrated: 0075-0077 refuse their reverse by design. The webhook
cases put back the two readable columns 0077 drops (only its schema steps, never
its reverse), and whatever happens they return the table to today's shape.

Test values only, not real credentials.
"""

import base64
import copy
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.db import connections, migrations
from django.db.migrations.executor import MigrationExecutor

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.credential_manager import decrypt_token
from agentcc.services.org_config_secrets import _decrypt

pytestmark = pytest.mark.django_db(transaction=True)

M0047 = ("prism", "0047_encrypt_org_config_secrets")
M0050 = ("prism", "0050_encrypt_org_config_secret_aliases")
M0051 = ("prism", "0051_encrypt_alert_channel_urls")
M0056 = ("prism", "0056_mcp_secrets_to_vault")
M0076 = ("prism", "0076_encrypt_webhook_credentials")
M0077 = ("prism", "0077_remove_webhook_plaintext")

CLEAR = "test-key-guard-not-a-real-secret"
HEADER = "Bearer test-key-guard-not-a-real-header"
SLACK = "https://hooks.slack.com/services/T0TEST/B0TEST/testkeyguardnotreal"
MCP_URL = "https://mcp.keyguard.example/mcp"
HOOK_URL = "https://hooks.keyguard.example/receive"

REFUSING_KEYS = ["made_up", "unset", "malformed"]


def _executor():
    return MigrationExecutor(connections["default"])


def _run(target, migration=None):
    """Apply ``target`` (or ``migration`` on ``target``'s state) by itself
    through the executor, without recording it."""
    executor = _executor()
    if migration is None:
        migration = executor.loader.get_migration(*target)
    state = executor.loader.project_state(target, at_end=False)
    with patch.object(executor, "record_migration"):
        executor.apply_migration(state, migration)


def _key(settings, shape):
    """Set the deployment's key to ``shape``."""
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = False
    if shape == "unset":
        settings.INTEGRATION_ENCRYPTION_KEY = ""
    elif shape == "malformed":
        settings.INTEGRATION_ENCRYPTION_KEY = "test-not-a-fernet-key"
    elif shape == "made_up":
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
        settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
    elif shape == "stable":
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    elif shape == "install":
        # What ./bin/install writes (bin/lib/secrets.sh, gen_fernet_key): 32
        # random bytes, URL-safe base64, kept in the install's .env.
        settings.INTEGRATION_ENCRYPTION_KEY = base64.urlsafe_b64encode(
            os.urandom(32)
        ).decode()
    else:
        raise AssertionError(shape)


def _assert_refused(caught):
    text = str(caught.value)
    assert "INTEGRATION_ENCRYPTION_KEY" in text and "stable" in text, text


def _org():
    return Organization.objects.create(name="key guard org")


# --- org-config secrets: 0047, 0050, 0051, 0056 -------------------------------


SEEDS = {
    M0047: {"cache": {"redis": {"address": "r:6379", "password": CLEAR}}},
    M0050: {"cache": {"s3": {"bucket": "b", "secretAccessKey": CLEAR}}},
    M0051: {"alerting": {"channels": [{"name": "ops", "type": "slack", "url": SLACK}]}},
    M0056: {
        "mcp": {
            "servers": {
                "gh": {
                    "transport": "http",
                    "url": MCP_URL,
                    "auth": {"type": "bearer", "token": CLEAR},
                }
            }
        }
    },
}
CLEAR_OF = {M0047: CLEAR, M0050: CLEAR, M0051: SLACK}


def _stored(row, target):
    if target == M0047:
        return row.cache["redis"]["password"]
    if target == M0050:
        return row.cache["s3"]["secretAccessKey"]
    if target == M0051:
        return row.alerting["channels"][0]["url"]
    return row.mcp["servers"]["gh"]


def _config(org, sections):
    return AgentccOrgConfig._base_manager.create(
        organization=org, version=1, is_active=True, **sections
    )


@pytest.mark.parametrize("target", list(SEEDS))
@pytest.mark.parametrize("shape", REFUSING_KEYS)
def test_a_secret_is_not_encrypted_without_a_stable_key(settings, target, shape):
    org = _org()
    row = _config(org, copy.deepcopy(SEEDS[target]))
    before = _stored(row, target)
    _key(settings, shape)
    with pytest.raises(Exception) as caught:
        _run(target)
    _assert_refused(caught)
    row.refresh_from_db()
    assert _stored(row, target) == before
    assert not AgentccMCPCredential.objects.filter(organization=org).exists()


@pytest.mark.parametrize("target", list(SEEDS))
def test_with_nothing_to_encrypt_a_made_up_key_does_not_block(settings, target):
    _key(settings, "made_up")
    _run(target)
    assert not AgentccOrgConfig._base_manager.exists()


@pytest.mark.parametrize("target", list(SEEDS))
@pytest.mark.parametrize("shape", ["stable", "install"])
def test_a_stable_key_encrypts_as_before(settings, target, shape):
    org = _org()
    row = _config(org, copy.deepcopy(SEEDS[target]))
    _key(settings, shape)
    _run(target)
    row.refresh_from_db()
    if target == M0056:
        (vault,) = AgentccMCPCredential.objects.filter(organization=org)
        assert decrypt_token(vault.encrypted_secret) == CLEAR
        server = _stored(row, target)
        assert server["credential"]["credential_id"] == str(vault.pk)
        assert CLEAR not in str(row.mcp)
        return
    value = _stored(row, target)
    assert value.startswith("enc::") and CLEAR_OF[target] not in value
    assert _decrypt(value) == CLEAR_OF[target]


# --- webhook headers and secrets: 0076, 0077 ------------------------------------


def _schema_only(name="0077_remove_webhook_plaintext"):
    """0077's schema steps without its checks: dropping the readable columns,
    the storage columns NOT NULL, the two check constraints."""
    real = _executor().loader.get_migration(*M0077)
    schema = migrations.Migration(name, "prism")
    schema.atomic = True
    schema.operations = [
        op for op in real.operations if not isinstance(op, migrations.RunPython)
    ]
    return schema


def _delete_webhooks(apps, schema_editor):
    model = apps.get_model("prism", "AgentccWebhook")
    model._base_manager.using(schema_editor.connection.alias).all().delete()


def _readable_columns():
    connection = connections["default"]
    with connection.cursor() as cursor:
        names = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, "agentcc_webhook"
            )
        }
    return {"headers", "secret"} & names


class _BeforeTheDrop:
    """The webhook table as 0076 left it: the readable columns back and the
    storage columns nullable, through 0077's own schema steps reversed (never
    its reverse, which refuses). On exit it deletes the test's rows and then,
    in a transaction of its own, drops the columns again, whatever state the
    table was left in."""

    def __enter__(self):
        if not _readable_columns():
            executor = _executor()
            state = executor.loader.project_state(M0077, at_end=False)
            with patch.object(executor.recorder, "record_unapplied"):
                executor.unapply_migration(state, _schema_only())
        return self

    def apps(self):
        return _executor().loader.project_state(M0077, at_end=False).apps

    def __exit__(self, *exc):
        if _readable_columns():
            # The rows go first, committed on their own: deleting them leaves
            # deferred constraint checks that an ALTER TABLE in the same
            # transaction refuses.
            cleanup = migrations.Migration("0077_key_guard_test_rows", "prism")
            cleanup.operations = [
                migrations.RunPython(_delete_webhooks, migrations.RunPython.noop)
            ]
            _run(M0077, cleanup)
            _run(M0077, _schema_only("0077_key_guard_test_restore"))
        assert not _readable_columns()
        return False


def _webhook(apps, org):
    model = apps.get_model("prism", "AgentccWebhook")
    return model._base_manager.create(
        organization_id=org.pk,
        name="key guard hook",
        url=HOOK_URL,
        headers={"Authorization": HEADER},
        secret=CLEAR,
        events=["budget.exceeded"],
    )


def _reread(apps, row):
    return apps.get_model("prism", "AgentccWebhook")._base_manager.get(pk=row.pk)


@pytest.mark.parametrize("shape", REFUSING_KEYS)
def test_webhook_credentials_are_not_encrypted_without_a_stable_key(settings, shape):
    org = _org()
    with _BeforeTheDrop() as table:
        apps = table.apps()
        row = _webhook(apps, org)
        _key(settings, shape)
        with pytest.raises(Exception) as caught:
            _run(M0076)
        _assert_refused(caught)
        row = _reread(apps, row)
        assert row.headers == {"Authorization": HEADER} and row.secret == CLEAR
        assert row.encrypted_headers is None and row.encrypted_secret is None
        assert row.credential_storage_version is None


def test_webhook_encryption_with_nothing_to_encrypt_does_not_block(settings):
    with _BeforeTheDrop():
        _key(settings, "made_up")
        _run(M0076)


@pytest.mark.parametrize("shape", ["stable", "install"])
def test_webhook_credentials_encrypt_as_before_with_a_stable_key(settings, shape):
    org = _org()
    with _BeforeTheDrop() as table:
        apps = table.apps()
        row = _webhook(apps, org)
        _key(settings, shape)
        _run(M0076)
        row = _reread(apps, row)
        assert row.credential_storage_version == 1
        assert bytes(row.encrypted_headers) and bytes(row.encrypted_secret)
        assert row.secret == CLEAR


@pytest.mark.parametrize("shape", REFUSING_KEYS)
def test_0077_stops_before_it_drops_the_readable_columns(settings, shape):
    """0077 checks every row and then drops the readable columns. Without a
    stable key it refuses before anything is dropped. In the made-up case the
    rows were encrypted under that same key, so 0077's own check passes and,
    without the guard, it drops the only copy a later process could read."""
    org = _org()
    with _BeforeTheDrop() as table:
        apps = table.apps()
        row = _webhook(apps, org)
        _key(settings, "stable")
        _run(M0076)
        if shape == "made_up":
            settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = True
        else:
            _key(settings, shape)
        with pytest.raises(Exception) as caught:
            _run(M0077)
        _assert_refused(caught)
        assert _readable_columns() == {"headers", "secret"}
        row = _reread(apps, row)
        assert row.headers == {"Authorization": HEADER} and row.secret == CLEAR


def test_0077_with_nothing_to_check_does_not_block(settings):
    with _BeforeTheDrop():
        _key(settings, "made_up")
        _run(M0077)
        assert not _readable_columns()


@pytest.mark.parametrize("shape", ["stable", "install"])
def test_0077_with_a_stable_key_drops_the_readable_columns_as_before(settings, shape):
    org = _org()
    with _BeforeTheDrop() as table:
        _webhook(table.apps(), org)
        _key(settings, shape)
        _run(M0076)
        _run(M0077)
        assert not _readable_columns()
