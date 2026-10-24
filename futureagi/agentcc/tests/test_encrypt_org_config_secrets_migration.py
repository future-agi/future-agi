"""The 0047 data migration encrypts every org-config secret leaf at rest,
across every version, idempotently and reversibly.

The migration module is imported directly and its walk run against in-memory
instances, so no schema step is needed; one case drives the real DB forward
and back to show it is wired and reversible.
"""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.org_config_secrets import (
    ENCRYPTED_PREFIX,
    _decrypt,
    _encrypt,
    iter_secret_slots,
)
from integrations.services.credentials import CredentialManager

migration = importlib.import_module(
    "agentcc.migrations.0047_encrypt_org_config_secrets"
)


def _config(**sections):
    base = {s: {} for s in ("cache", "a2a", "audit", "alerting", "mcp")}
    base.update(sections)
    return SimpleNamespace(organization_id="org-1", **base)


def _clear_sample():
    return {
        "cache": {
            "redis": {"address": "r:6379", "password": "pw-clear"},
            "s3": {"bucket": "b", "secret_access_key": "sak-clear"},
            "azure_blob": {"connection_string": "cs-clear", "sas_token": "sas-clear"},
            "semantic": {
                "qdrant": {"url": "https://q", "api_key": "qd-clear"},
                "weaviate": {"url": "https://w", "api_key": "wv-clear"},
                "pinecone": {"url": "https://p", "api_key": "pc-clear"},
            },
        },
        "a2a": {"agents": {"b": {"url": "https://a", "auth": {"token": "a2a-clear"}}}},
        "audit": {
            "sinks": [{"url": "https://s", "headers": {"Authorization": "au-clear"}}]
        },
        "alerting": {
            "channels": [{"url": "https://h", "headers": {"X-Tok": "al-clear"}}]
        },
        "mcp": {
            "servers": {
                "gh": {"url": "https://m", "auth": {"token": "mcp-clear"}},
                "kv": {"url": "https://m", "auth": {"key": "mcp-key-clear"}},
                "vault": {
                    "url": "https://m",
                    "credential": {"secret": {"value": "mcp-cred-clear"}},
                },
            },
            "managed_tools": {
                "credentials": {"fagi.x": {"secret": {"value": "mgd-clear"}}}
            },
        },
    }


def _leaf_values(config):
    sections = {
        s: getattr(config, s) for s in ("cache", "a2a", "audit", "alerting", "mcp")
    }
    return {slot.pointer: slot.value for slot in iter_secret_slots(sections)}


def test_walk_finds_every_secret_leaf():
    config = _config(**_clear_sample())
    found = {container[key] for container, key in migration._walk_leaves(config)}
    assert found == {
        "pw-clear",
        "sak-clear",
        "cs-clear",
        "sas-clear",
        "qd-clear",
        "wv-clear",
        "pc-clear",
        "a2a-clear",
        "au-clear",
        "al-clear",
        "mcp-clear",
        "mcp-key-clear",
        "mcp-cred-clear",
        "mgd-clear",
    }


def test_encrypts_in_place_and_leaves_the_rest():
    config = _config(**_clear_sample())
    enc = migration._encrypter(CredentialManager)
    cache = {}
    for container, key in migration._walk_leaves(config):
        container[key] = enc(cache, "org-1", container[key])

    values = _leaf_values(config)
    assert len(values) == 14
    for pointer, value in values.items():
        assert value.startswith(ENCRYPTED_PREFIX), pointer
    # Non-secret settings are untouched.
    assert config.cache["redis"]["address"] == "r:6379"
    assert config.cache["s3"]["bucket"] == "b"
    assert config.a2a["agents"]["b"]["url"] == "https://a"


def test_forwards_is_idempotent_and_reversible(db):
    sample = _clear_sample()
    row = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=_org(), version=1, is_active=True, **sample
    )

    class _Apps:
        def get_model(self, app, model):
            return AgentccOrgConfig

    migration.forwards(_Apps(), None)
    row.refresh_from_db()
    assert row.cache["redis"]["password"].startswith(ENCRYPTED_PREFIX)
    assert "pw-clear" not in str(row.cache)
    first = row.cache["redis"]["password"]

    # A second run changes nothing (already encrypted).
    migration.forwards(_Apps(), None)
    row.refresh_from_db()
    assert row.cache["redis"]["password"] == first

    # Backwards decrypts every leaf.
    migration.backwards(_Apps(), None)
    row.refresh_from_db()
    assert row.cache["redis"]["password"] == "pw-clear"
    assert row.mcp["servers"]["gh"]["auth"]["token"] == "mcp-clear"
    assert row.audit["sinks"][0]["headers"]["Authorization"] == "au-clear"


def test_every_version_of_an_org_is_encrypted(db):
    org = _org()
    sample = _clear_sample()
    v1 = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=org, version=1, is_active=False, cache=sample["cache"]
    )
    v2 = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=org, version=2, is_active=True, cache=sample["cache"]
    )
    v2.deleted = False
    v2.save()
    # A soft-deleted version holds the secret too and must be encrypted.
    v3 = AgentccOrgConfig.all_objects.create(
        organization_id=org,
        version=3,
        is_active=False,
        deleted=True,
        cache=sample["cache"],
    )

    class _Apps:
        def get_model(self, app, model):
            return AgentccOrgConfig

    migration.forwards(_Apps(), None)
    for row in (v1, v2, v3):
        row.refresh_from_db()
        assert row.cache["redis"]["password"].startswith(ENCRYPTED_PREFIX), row.version
    # One org, one clear value -> one blob reused across versions, so a
    # cross-version diff does not show the shared secret as changed.
    assert v1.cache["redis"]["password"] == v2.cache["redis"]["password"]


def _org():
    from accounts.models import Organization

    return Organization.objects.create(name="secrets migration org").id


class _Apps:
    def get_model(self, app, model):
        return AgentccOrgConfig


def _other_key_token(clear):
    """A well-formed ``enc::`` Fernet token under a key this deployment does
    not hold: stored ciphertext that cannot be read, not a clear value."""
    blob = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return ENCRYPTED_PREFIX + blob.decode()


def test_a_clear_value_that_starts_with_the_prefix_is_encrypted(db):
    # A clear secret is any string, one beginning with "enc::" included. Only a
    # value that decrypts to a stored blob is already encrypted; every other one
    # is clear and is encrypted, so none stays in clear at rest and the push
    # (which decrypts) sends it.
    already = _encrypt("already-encrypted-test-value")
    unreadable = _other_key_token("lost-key-test-value")
    row = AgentccOrgConfig.no_workspace_objects.create(
        organization_id=_org(),
        version=1,
        is_active=True,
        cache={
            "redis": {"address": "r:6379", "password": "enc::hunter2"},
            "s3": {"bucket": "b", "secret_access_key": already},
            "azure_blob": {"connection_string": "enc::", "sas_token": unreadable},
        },
        a2a={"agents": {"b": {"url": "https://a", "auth": {"token": "enc::x.y=="}}}},
    )

    migration.forwards(_Apps(), None)
    row.refresh_from_db()

    password = row.cache["redis"]["password"]
    assert password != "enc::hunter2"
    assert _decrypt(password) == "enc::hunter2"
    assert _decrypt(row.cache["azure_blob"]["connection_string"]) == "enc::"
    assert _decrypt(row.a2a["agents"]["b"]["auth"]["token"]) == "enc::x.y=="
    # A real stored ciphertext is left alone (idempotent), and an unreadable
    # one is never wrapped a second time.
    assert row.cache["s3"]["secret_access_key"] == already
    assert row.cache["azure_blob"]["sas_token"] == unreadable

    # Backwards gives each clear value back and leaves the unreadable token.
    migration.backwards(_Apps(), None)
    row.refresh_from_db()
    assert row.cache["redis"]["password"] == "enc::hunter2"
    assert row.cache["azure_blob"]["connection_string"] == "enc::"
    assert row.cache["s3"]["secret_access_key"] == "already-encrypted-test-value"
    assert row.cache["azure_blob"]["sas_token"] == unreadable


# --- bounded batches, one transaction each ---------------------------------

MIGRATION = ("prism", "0047_encrypt_org_config_secrets")
BEFORE = ("prism", "0046_org_config_db_defaults")


def _migrate_back_to(target):
    """Migrate the test database back to ``target`` and return what puts it
    back: for every app the backward step unapplies, the last migration of
    that app it unapplies (the leaf the session had applied). A later prism
    migration, or another app's migration that depends on 0047, is restored
    with it, and an app the step does not touch is left as it was."""
    executor = MigrationExecutor(connection)
    restore = {}
    # The backward plan unapplies each app's latest migration first.
    for step, backwards in executor.migration_plan([target]):
        if backwards:
            restore.setdefault(step.app_label, (step.app_label, step.name))
    executor.migrate([target])
    return list(restore.values())


def _rows_with_one_secret(org, values):
    return [
        AgentccOrgConfig.no_workspace_objects.create(
            organization_id=org,
            version=n,
            is_active=n == len(values),
            cache={"redis": {"address": "r:6379", "password": value}},
        )
        for n, value in enumerate(values, start=1)
    ]


def test_the_migration_is_not_one_transaction():
    # A single transaction would hold every changed row's lock, and the whole
    # backfill's work, until the last org-config row was rewritten.
    assert migration.Migration.atomic is False


def test_forwards_reads_and_commits_in_bounded_batches(db):
    org = _org()
    rows = _rows_with_one_secret(org, ["shared-pw-test-value"] * 5)

    with (
        patch.object(migration, "BATCH_SIZE", 2, create=True),
        CaptureQueriesContext(connection) as queries,
    ):
        migration.forwards(_Apps(), None)

    # A read of the table, as a plain query or a server-side cursor.
    selects = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith(("SELECT", "DECLARE"))
        and "agentcc_org_config" in q["sql"]
    ]
    savepoints = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith("SAVEPOINT")
    ]
    # Five rows at two a batch: at least three batch reads, each bounded, and
    # each batch in a transaction of its own (a savepoint inside the test's).
    assert len(selects) >= 3, selects
    for sql in selects:
        assert "LIMIT 2" in sql.upper(), sql
    assert len(savepoints) == len(selects), (savepoints, selects)

    blobs = set()
    for row in rows:
        row.refresh_from_db()
        password = row.cache["redis"]["password"]
        assert _decrypt(password) == "shared-pw-test-value", row.version
        blobs.add(password)
    # One org, one clear value: the blob is reused across batches too.
    assert len(blobs) == 1


@pytest.mark.django_db(transaction=True)
def test_a_failure_part_way_keeps_the_batches_already_committed():
    # Driven through the migration executor, the way `migrate` runs it, so the
    # migration's own transaction setting is what is tested: a batch that
    # fails rolls back alone, the batches before it stay done, and a re-run
    # finishes the rest.
    restore = _migrate_back_to(BEFORE)
    assert "prism" in dict(restore), restore
    try:
        org = _org()
        rows = _rows_with_one_secret(org, [f"pw-{n}-test-value" for n in range(5)])
        rows.sort(key=lambda row: row.pk)
        last = rows[-1]
        last.cache = {"redis": {"address": "r:6379", "password": "boom-test-value"}}
        last.save(update_fields=["cache"])

        real_encrypt = CredentialManager.encrypt.__func__

        def encrypt(cls, credentials):
            if credentials.get("v") == "boom-test-value":
                raise RuntimeError("injected failure in the last batch")
            return real_encrypt(cls, credentials)

        with (
            patch.object(migration, "BATCH_SIZE", 2, create=True),
            patch.object(CredentialManager, "encrypt", classmethod(encrypt)),
            pytest.raises(RuntimeError, match="injected failure"),
        ):
            MigrationExecutor(connection).migrate([MIGRATION])

        for row in rows[:-1]:
            row.refresh_from_db()
            assert row.cache["redis"]["password"].startswith(ENCRYPTED_PREFIX), (
                row.version
            )
        last.refresh_from_db()
        assert last.cache["redis"]["password"] == "boom-test-value"
    finally:
        MigrationExecutor(connection).migrate(restore)

    last.refresh_from_db()
    assert _decrypt(last.cache["redis"]["password"]) == "boom-test-value"
