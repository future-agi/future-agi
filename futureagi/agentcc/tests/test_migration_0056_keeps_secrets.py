"""Migration 0056 moves inline MCP secrets into the credential vault. A secret
the vault's rules do not accept (a padded value, an api_key with no header, a
header the gateway sets itself, an auth type the vault does not hold, a token
encrypted under a key this deployment does not hold), and a usable secret whose
server id is longer than the vault's 64-character server_id column, are kept:
stored as a revoked credential (never sent), said in the migration's report,
and removed from the config as before. A server id that long no longer stops
the update. A newer refused value is kept beside the older one stored, and a
rerun writes nothing. The report names the command that deletes the kept values
(agentcc_mcp_migrate_secrets --remove-kept), and deleting the org deletes them.

The migration runs by itself on connections["default"] through the executor,
unrecorded. Test values only, not real credentials.
"""

import importlib
import json
from io import StringIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.credential_manager import decrypt_token

pytestmark = pytest.mark.django_db(transaction=True)

M0056 = ("prism", "0056_mcp_secrets_to_vault")
URL = "https://mcp.keep.example/mcp"
SECRET = "test-0056-keep-not-a-real-secret"
LONG_ID = "a-server-name-the-release-accepted-" + "x" * 35  # 70 characters


def _run():
    executor = MigrationExecutor(connections["default"])
    migration = executor.loader.get_migration(*M0056)
    state = executor.loader.project_state(M0056, at_end=False)
    with patch.object(executor, "record_migration"):
        executor.apply_migration(state, migration)


@pytest.fixture
def stable_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY_EPHEMERAL = False
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _org_with(servers):
    org = Organization.objects.create(name="0056 keep org")
    row = AgentccOrgConfig._base_manager.create(
        organization=org, version=1, is_active=True, mcp={"servers": servers}
    )
    return org, row


def _unreadable(clear):
    blob = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return "enc::" + blob.decode()


NOT_ACCEPTED = {
    "padded value": {"type": "bearer", "token": f" {SECRET} "},
    "api_key with no header": {"type": "api_key", "key": SECRET},
    "a header the gateway sets": {"type": "api_key", "key": SECRET, "header": "Host"},
    "an auth type the vault does not hold": {"type": "basic", "token": SECRET},
}


@pytest.mark.usefixtures("stable_key")
@pytest.mark.parametrize("case", sorted(NOT_ACCEPTED))
def test_a_secret_the_vault_does_not_accept_is_kept_revoked(case, capsys):
    auth = NOT_ACCEPTED[case]
    org, row = _org_with({"gh": {"url": URL, "auth": dict(auth)}})

    _run()

    (kept,) = AgentccMCPCredential._base_manager.filter(organization=org)
    assert kept.server_id == "gh"
    assert kept.status == "revoked"
    stored = auth.get("token", auth.get("key"))
    assert decrypt_token(kept.encrypted_secret) == stored
    assert "not accepted" in kept.label
    row.refresh_from_db()
    assert SECRET not in json.dumps(row.mcp)
    report = capsys.readouterr().out
    assert f"org {org.pk}" in report and "kept 1" in report and "gh" in report
    assert SECRET not in report


@pytest.mark.usefixtures("stable_key")
def test_a_token_under_another_key_is_kept_as_it_was_stored(capsys):
    token = _unreadable(SECRET)
    org, row = _org_with(
        {"gh": {"url": URL, "auth": {"type": "bearer", "token": token}}}
    )

    _run()

    (kept,) = AgentccMCPCredential._base_manager.filter(organization=org)
    assert (kept.server_id, kept.status) == ("gh", "revoked")
    # The stored ciphertext, as it was: readable again with the key that wrote it.
    assert decrypt_token(kept.encrypted_secret) == token
    row.refresh_from_db()
    assert token not in json.dumps(row.mcp)
    assert "kept 1" in capsys.readouterr().out


@pytest.mark.usefixtures("stable_key")
def test_a_server_id_longer_than_the_vault_column_does_not_stop_the_update(capsys):
    org, row = _org_with(
        {LONG_ID: {"url": URL, "auth": {"type": "bearer", "token": SECRET}}}
    )

    _run()

    (kept,) = AgentccMCPCredential._base_manager.filter(organization=org)
    assert len(kept.server_id) == 64
    assert kept.server_id.startswith(LONG_ID[:40])
    assert kept.status == "revoked"
    assert decrypt_token(kept.encrypted_secret) == SECRET
    assert LONG_ID in kept.label
    row.refresh_from_db()
    assert SECRET not in json.dumps(row.mcp)
    report = capsys.readouterr().out
    assert LONG_ID in report and "64" in report


def _vault_rows(org):
    return sorted(
        AgentccMCPCredential._base_manager.filter(organization=org).values_list(
            "pk", "server_id", "status", "encrypted_secret"
        )
    )


@pytest.mark.usefixtures("stable_key")
def test_a_rerun_keeps_nothing_twice_and_writes_nothing(capsys):
    org, row = _org_with(
        {
            "gh": {"url": URL, "auth": {"type": "api_key", "key": SECRET}},
            LONG_ID: {"url": URL, "auth": {"type": "bearer", "token": SECRET}},
        }
    )
    _run()
    first = _vault_rows(org)
    row.refresh_from_db()
    config = json.dumps(row.mcp, sort_keys=True)
    capsys.readouterr()

    _run()

    assert len(first) == 2 and _vault_rows(org) == first
    row.refresh_from_db()
    assert json.dumps(row.mcp, sort_keys=True) == config
    assert "kept" not in capsys.readouterr().out


@pytest.mark.usefixtures("stable_key")
def test_a_newer_value_the_vault_refuses_is_kept_beside_the_older_one_stored():
    org = Organization.objects.create(name="0056 keep org")
    older = "test-0056-older-not-a-real-secret"
    AgentccOrgConfig._base_manager.create(
        organization=org,
        version=1,
        is_active=False,
        mcp={
            "servers": {"gh": {"url": URL, "auth": {"type": "bearer", "token": older}}}
        },
    )
    AgentccOrgConfig._base_manager.create(
        organization=org,
        version=2,
        is_active=True,
        mcp={
            "servers": {"gh": {"url": URL, "auth": {"type": "api_key", "key": SECRET}}}
        },
    )

    _run()

    rows = AgentccMCPCredential._base_manager.filter(organization=org)
    by_value = {decrypt_token(row.encrypted_secret): row for row in rows}
    assert sorted(by_value) == sorted([older, SECRET])
    # The active version's value was refused, so neither is sent.
    assert {row.status for row in rows} == {"revoked"}
    assert "not accepted" in by_value[SECRET].label
    assert by_value[older].label == "Moved from the org config"


@pytest.mark.usefixtures("stable_key")
def test_a_usable_secret_still_moves_active():
    org, row = _org_with(
        {"gh": {"url": URL, "auth": {"type": "bearer", "token": SECRET}}}
    )

    _run()

    (moved,) = AgentccMCPCredential._base_manager.filter(organization=org)
    assert (moved.server_id, moved.status) == ("gh", "active")
    assert decrypt_token(moved.encrypted_secret) == SECRET
    row.refresh_from_db()
    assert row.mcp["servers"]["gh"]["credential"]["credential_id"] == str(moved.pk)


# --- removing what 0056 kept ------------------------------------------------


def _command(*args):
    out, err = StringIO(), StringIO()
    call_command("agentcc_mcp_migrate_secrets", *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def _kept_org(name):
    org = Organization.objects.create(name=name)
    AgentccOrgConfig._base_manager.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={
            "servers": {
                "gh": {"url": URL, "auth": {"type": "api_key", "key": SECRET}},
                "ok": {"url": URL, "auth": {"type": "bearer", "token": SECRET}},
            }
        },
    )
    return org


@pytest.mark.usefixtures("stable_key")
def test_the_report_names_the_command_that_deletes_the_kept_values(capsys):
    org = _kept_org("0056 report org")

    _run()

    report = capsys.readouterr().out
    assert f"agentcc_mcp_migrate_secrets --remove-kept --org {org.pk} --apply" in report
    assert SECRET not in report


@pytest.mark.usefixtures("stable_key")
def test_remove_kept_counts_without_apply_and_deletes_only_the_kept_values():
    first, second = _kept_org("0056 remove org one"), _kept_org("0056 remove org two")
    _run()
    vault = AgentccMCPCredential._base_manager
    kept = vault.filter(status="revoked", server_id="gh")
    moved = vault.filter(status="active", server_id="ok")
    assert kept.count() == 2 and moved.count() == 2

    dry = _command("--remove-kept")
    assert f"org {first.pk}: 1 value(s) kept" in dry
    assert f"org {second.pk}: 1 value(s) kept" in dry
    assert "Dry run: nothing is deleted" in dry
    assert kept.count() == 2

    only = _command("--remove-kept", "--org", str(first.pk), "--apply")
    assert f"org {first.pk}: deleted 1 value(s)" in only
    assert str(second.pk) not in only
    assert list(kept.values_list("organization_id", flat=True)) == [second.pk]

    rest = _command("--remove-kept", "--apply")
    assert "Deleted 1 kept value(s) in 1 org(s)." in rest
    assert not kept.exists()
    # The moved, active credentials stay; the command never names a value.
    assert moved.count() == 2
    assert SECRET not in dry + only + rest
    assert "No values kept by migration 0056." in _command("--remove-kept", "--apply")


def test_remove_kept_takes_only_org_and_apply():
    with pytest.raises(CommandError):
        call_command("agentcc_mcp_migrate_secrets", "--remove-kept", "--check")


@pytest.mark.usefixtures("stable_key")
def test_deleting_the_org_deletes_its_kept_values():
    org = _kept_org("0056 cascade org")
    _run()
    assert AgentccMCPCredential._base_manager.filter(
        organization=org, status="revoked"
    ).exists()
    org_id = org.pk
    org.delete()
    assert not AgentccMCPCredential._base_manager.filter(
        organization_id=org_id
    ).exists()


def test_the_command_reads_the_label_the_migration_writes():
    move = importlib.import_module("agentcc.migrations.0056_mcp_secrets_to_vault")
    command = importlib.import_module(
        "agentcc.management.commands.agentcc_mcp_migrate_secrets"
    )
    assert command.KEPT_LABEL == move.KEPT_LABEL
    assert move.REMOVE_KEPT == "agentcc_mcp_migrate_secrets --remove-kept"
