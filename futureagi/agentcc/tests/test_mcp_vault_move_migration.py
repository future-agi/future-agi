"""Migration 0056 moves the MCP secrets the release line stored inline in org
configs into the MCP credential vault (one MCP secret store).

On the release line an org config's ``mcp`` section held each server's
``auth.token`` or ``auth.key``, its ``credential.secret`` and each managed
tool's ``managed_tools.credentials.<fagi.id>.secret`` inline, encrypted under
``enc::`` by NG-01 (its writer, migrations 0047 and 0050), or in clear in a row
neither reached. Here rows are seeded as the release line wrote them, the
migration's ``forwards`` runs over the live app registry, and afterwards:

- every version (active, older, soft-deleted) holds references only;
- each vault row decrypts to the value the release push sent, and the push
  now carries the same value from the vault;
- the non-MCP sections keep their ``enc::`` values byte for byte;
- ``agentcc_mcp_migrate_secrets --check`` passes, where it failed before, and
  ``--apply`` has nothing left to do;
- a second run changes nothing.

One case drives the real migration executor back to 0055 and forward again.

Test values only, not real credentials.
"""

import importlib
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.apps import apps as django_apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.org_config_secrets import _encrypt

MIGRATION_MODULE = "agentcc.migrations.0056_mcp_secrets_to_vault"
MIGRATION = ("prism", "0056_mcp_secrets_to_vault")
BEFORE = ("prism", "0055_ee_agent_key_mint_idempotency")
COMMAND = "agentcc_mcp_migrate_secrets"
LABEL = "Moved from the org config"

GH = "test-s2v-gh-not-a-real-token"
KV = "test-s2v-kv-not-a-real-key"
CRED = "test-s2v-cred-not-a-real-token"
MANAGED = "test-s2v-managed-not-a-real-key"
CAMEL = "test-s2v-camel-not-a-real-key"
OLD = "test-s2v-old-not-a-real-token"
MID = "test-s2v-mid-not-a-real-token"
GONE = "test-s2v-gone-not-a-real-key"
UNUSED = "test-s2v-unused-not-a-real-token"
KEPT = "test-s2v-kept-not-a-real-token"
REDIS = "test-s2v-redis-not-a-real-password"
URL = "https://mcp.s2v.example/mcp"


@pytest.fixture
def move():
    return importlib.import_module(MIGRATION_MODULE)


def _forwards(move):
    move.forwards(django_apps, None)


def _org(name="s2v vault move org"):
    return Organization.objects.create(name=name)


def _bearer(token, url=URL):
    return {"transport": "http", "url": url, "auth": {"type": "bearer", "token": token}}


def _api_key(key, header="X-API-Key", url=URL):
    return {
        "transport": "http",
        "url": url,
        "auth": {"type": "api_key", "header": header, "key": key},
    }


def _version(org, version, mcp, *, active=False, deleted=False, **sections):
    return AgentccOrgConfig._base_manager.create(
        organization=org,
        version=version,
        is_active=active,
        deleted=deleted,
        mcp=mcp,
        **sections,
    )


def _rows(org):
    return list(
        AgentccOrgConfig._base_manager.filter(organization=org).order_by("version")
    )


def _vault(org):
    return {
        row.server_id: row
        for row in AgentccMCPCredential.objects.filter(organization=org)
    }


def _other_key_token(clear):
    """A well-formed ``enc::`` Fernet token under a key this deployment does
    not hold: stored ciphertext nobody here can read."""
    blob = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return "enc::" + blob.decode()


def _run(*args):
    out, err = StringIO(), StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _reference(credential, mode="shared"):
    return {
        "mode": mode,
        "credential_id": str(credential.pk),
        "state": credential.status,
    }


def _release_org():
    """An org as the release line left it: the active v2 holds every MCP
    secret form under NG-01's ``enc::``, and a Redis password (a non-MCP
    secret, which stays inline); v1 holds an older token for gh."""
    org = _org()
    _version(org, 1, {"servers": {"gh": _bearer(_encrypt(OLD))}})
    active = _version(
        org,
        2,
        {
            "enabled": True,
            "servers": {
                "gh": _bearer(_encrypt(GH)),
                "kv": _api_key(_encrypt(KV)),
                "vault": {
                    "transport": "http",
                    "url": URL,
                    "credential": {
                        "mode": "shared",
                        "secret": {"type": "bearer", "value": _encrypt(CRED)},
                    },
                },
            },
            "managed_tools": {
                "enabled": True,
                "credentials": {
                    "fagi.search": {
                        "mode": "shared",
                        "secret": {
                            "type": "api_key",
                            "header": "X-Search-Key",
                            "value": _encrypt(MANAGED),
                        },
                    }
                },
            },
        },
        active=True,
        cache={
            "redis": {"address": "redis.s2v.example:6379", "password": _encrypt(REDIS)}
        },
    )
    return org, active


@pytest.mark.django_db
class TestTheReleaseLinesInlineSecretsMoveIntoTheVault:
    def test_each_ng01_ciphertext_is_stored_in_the_vault_as_its_clear_value(self, move):
        org, active = _release_org()
        _forwards(move)

        vault = _vault(org)
        assert sorted(vault) == ["fagi.search", "gh", "kv", "vault"]
        expected = {
            "gh": ("bearer", "", GH),
            "kv": ("api_key", "X-API-Key", KV),
            "vault": ("bearer", "", CRED),
            "fagi.search": ("api_key", "X-Search-Key", MANAGED),
        }
        for server_id, (kind, header, clear) in expected.items():
            row = vault[server_id]
            assert (row.kind, row.header_name) == (kind, header), server_id
            assert row.scope == "shared" and row.user_id is None, server_id
            assert row.status == "active", server_id
            assert row.label == LABEL, server_id
            assert row.created_by_id is None, server_id
            assert row.encrypted_secret.startswith("enc::"), server_id
            assert clear not in row.encrypted_secret, server_id
            assert decrypt_token(row.encrypted_secret) == clear, server_id

    def test_every_version_keeps_references_only(self, move):
        org, active = _release_org()
        _forwards(move)

        vault = _vault(org)
        v1, v2 = _rows(org)
        servers = v2.mcp["servers"]
        assert servers["gh"] == {
            "transport": "http",
            "url": URL,
            "credential": _reference(vault["gh"]),
        }
        assert servers["kv"]["credential"] == _reference(vault["kv"])
        assert "auth" not in servers["kv"]
        assert servers["vault"]["credential"] == _reference(vault["vault"])
        managed = v2.mcp["managed_tools"]
        assert managed["enabled"] is True
        assert managed["credentials"]["fagi.search"] == _reference(vault["fagi.search"])
        # v1's older token is superseded by the active one: stored nowhere,
        # and v1's entry points at the server's credential.
        assert v1.mcp["servers"]["gh"]["credential"] == _reference(vault["gh"])
        stored = json.dumps([row.mcp for row in (v1, v2)])
        assert "enc::" not in stored
        for value in (GH, KV, CRED, MANAGED, OLD):
            assert value not in stored
        for row in vault.values():
            assert decrypt_token(row.encrypted_secret) != OLD

    def test_the_non_mcp_sections_are_left_byte_for_byte(self, move):
        org, active = _release_org()
        cache = json.dumps(active.cache, sort_keys=True)
        _forwards(move)
        active.refresh_from_db()
        assert json.dumps(active.cache, sort_keys=True) == cache
        assert active.cache["redis"]["password"].startswith("enc::")

    def test_the_push_carries_the_same_secrets_from_the_vault(self, move):
        org, active = _release_org()
        before = _build_payload(org.id, active)
        # The release push decrypted each inline leaf.
        assert before["mcp"]["servers"]["gh"]["auth"]["token"] == GH
        assert before["mcp"]["servers"]["kv"]["auth"]["key"] == KV

        _forwards(move)
        # The deploy's next migration, 0057, binds each moved credential to
        # its server's origin; the push sends an unbound one nowhere.
        importlib.import_module(
            "agentcc.migrations.0057_mcp_credential_origin"
        ).bind_sent_credentials(django_apps, None)
        active.refresh_from_db()
        after = _build_payload(org.id, active)

        servers = after["mcp"]["servers"]
        assert "auth" not in servers["gh"]
        assert servers["gh"]["credential"]["secret"] == {
            "type": "bearer",
            "header": "Authorization",
            "value": GH,
        }
        assert servers["kv"]["credential"]["secret"] == {
            "type": "api_key",
            "header": "X-API-Key",
            "value": KV,
        }
        assert servers["vault"]["credential"]["secret"]["value"] == CRED
        managed = after["mcp"]["managed_tools"]["credentials"]["fagi.search"]
        assert managed["secret"] == {
            "type": "api_key",
            "header": "X-Search-Key",
            "value": MANAGED,
        }
        assert after["cache"]["redis"]["password"] == REDIS

    def test_a_clear_value_no_ng01_pass_reached_moves_too(self, move):
        org = _org()
        _version(org, 1, {"servers": {"gh": _bearer(GH)}}, active=True)
        _forwards(move)
        row = _vault(org)["gh"]
        assert decrypt_token(row.encrypted_secret) == GH
        assert _rows(org)[0].mcp["servers"]["gh"]["credential"] == _reference(row)


@pytest.mark.django_db
class TestTheDeployGate:
    def test_check_fails_before_the_move_and_passes_after(self, move):
        _release_org()
        with pytest.raises(CommandError, match="plaintext MCP secret"):
            _run("--check")
        _forwards(move)
        out, _ = _run("--check")
        assert "No plaintext MCP secrets" in out

    def test_apply_after_the_move_has_nothing_left_to_do(self, move):
        org, _ = _release_org()
        _forwards(move)
        rows = [
            (row.version, json.dumps(row.mcp, sort_keys=True)) for row in _rows(org)
        ]
        credentials = AgentccMCPCredential.objects.count()

        with patch("agentcc.services.config_push.get_gateway_client") as client:
            _run("--apply")
        client.return_value.set_org_config.assert_not_called()

        assert [
            (row.version, json.dumps(row.mcp, sort_keys=True)) for row in _rows(org)
        ] == rows
        assert AgentccMCPCredential.objects.count() == credentials

    def test_a_second_run_changes_nothing(self, move):
        org, _ = _release_org()
        _forwards(move)
        rows = [json.dumps(row.mcp, sort_keys=True) for row in _rows(org)]
        vault = {
            key: (row.pk, row.status, row.encrypted_secret)
            for key, row in _vault(org).items()
        }
        _forwards(move)
        assert [json.dumps(row.mcp, sort_keys=True) for row in _rows(org)] == rows
        assert {
            key: (row.pk, row.status, row.encrypted_secret)
            for key, row in _vault(org).items()
        } == vault


@pytest.mark.django_db
class TestTheCommandsRulesHold:
    def test_every_version_is_moved_and_an_older_only_secret_is_kept_revoked(
        self, move
    ):
        org = _org()
        _version(
            org,
            1,
            {"servers": {"gh": _bearer(_encrypt(OLD)), "gone": _api_key(GONE)}},
        )
        _version(org, 2, {"servers": {"gh": _bearer(_encrypt(MID))}}, deleted=True)
        _version(org, 3, {"servers": {"gh": _bearer(_encrypt(GH))}}, active=True)
        _forwards(move)

        vault = _vault(org)
        assert vault["gh"].status == "active"
        assert decrypt_token(vault["gh"].encrypted_secret) == GH
        # A server only an older version holds gets a revoked credential, as
        # remove-mcp-server leaves one.
        assert vault["gone"].status == "revoked"
        assert decrypt_token(vault["gone"].encrypted_secret) == GONE
        v1, v2, v3 = _rows(org)
        assert v1.mcp["servers"]["gone"]["credential"] == _reference(vault["gone"])
        assert v1.mcp["servers"]["gone"]["credential"]["state"] == "revoked"
        # The soft-deleted version is moved too.
        assert v2.deleted is True
        assert v2.mcp["servers"]["gh"]["credential"] == _reference(vault["gh"])
        stored = json.dumps([row.mcp for row in (v1, v2, v3)])
        for value in (OLD, MID, GH, GONE):
            assert value not in stored
        assert "enc::" not in stored
        assert sorted(vault) == ["gh", "gone"]

    def test_a_token_this_deployment_cannot_read_is_removed_not_stored(self, move):
        org = _org()
        _version(
            org,
            1,
            {
                "servers": {
                    "gh": _bearer(_other_key_token(GH)),
                    "plain": {"url": URL, "auth": {"type": "none", "token": UNUSED}},
                }
            },
            active=True,
        )
        _forwards(move)
        assert _vault(org) == {}
        servers = _rows(org)[0].mcp["servers"]
        # Nothing usable to point at: no auth, no reference, no secret.
        assert servers["gh"] == {"transport": "http", "url": URL}
        assert servers["plain"] == {"url": URL}
        _run("--check")

    def test_a_clear_value_that_starts_with_the_prefix_is_not_stored(self, move):
        # The vault never stores a value that starts with enc:: (new_secret);
        # a clear legacy one is removed like any value the vault refuses.
        org = _org()
        _version(org, 1, {"servers": {"gh": _bearer("enc::s2v-legacy")}}, active=True)
        _forwards(move)
        assert _vault(org) == {}
        assert "auth" not in _rows(org)[0].mcp["servers"]["gh"]

    def test_a_camel_case_managed_tools_secret_moves_under_its_spelling(self, move):
        org = _org()
        _version(
            org,
            1,
            {
                "managedTools": {
                    "credentials": {
                        "fagi.docs": {
                            "mode": "shared",
                            "secret": {
                                "type": "api_key",
                                "header": "X-Docs-Key",
                                "value": _encrypt(CAMEL),
                            },
                        }
                    }
                }
            },
            active=True,
        )
        _forwards(move)
        row = _vault(org)["fagi.docs"]
        assert decrypt_token(row.encrypted_secret) == CAMEL
        assert (row.kind, row.header_name, row.status) == (
            "api_key",
            "X-Docs-Key",
            "active",
        )
        mcp = _rows(org)[0].mcp
        assert mcp["managedTools"]["credentials"]["fagi.docs"] == _reference(row)
        _run("--check")

    def test_a_credential_the_active_config_already_sends_is_kept(self, move):
        org = _org()
        kept = AgentccMCPCredential.objects.create(
            organization=org,
            server_id="gh",
            kind="bearer",
            encrypted_secret=encrypt_token(KEPT),
        )
        _version(org, 1, {"servers": {"gh": _bearer(_encrypt(OLD))}})
        _version(
            org,
            2,
            {
                "servers": {
                    "gh": {
                        "transport": "http",
                        "url": URL,
                        "credential": _reference(kept),
                    }
                }
            },
            active=True,
        )
        _forwards(move)
        assert list(AgentccMCPCredential.objects.filter(organization=org)) == [kept]
        kept.refresh_from_db()
        assert kept.status == "active"
        assert decrypt_token(kept.encrypted_secret) == KEPT
        v1, v2 = _rows(org)
        assert v1.mcp["servers"]["gh"]["credential"] == _reference(kept)
        assert OLD not in json.dumps(v1.mcp)

    def test_an_active_credential_no_config_sends_is_revoked(self, move):
        org = _org()
        stale = AgentccMCPCredential.objects.create(
            organization=org,
            server_id="gh",
            kind="bearer",
            encrypted_secret=encrypt_token(KEPT),
        )
        _version(org, 1, {"servers": {"gh": _bearer(_encrypt(GH))}}, active=True)
        _forwards(move)
        stale.refresh_from_db()
        assert stale.status == "revoked"
        current = AgentccMCPCredential.objects.get(
            organization=org, server_id="gh", status="active"
        )
        assert decrypt_token(current.encrypted_secret) == GH
        assert _rows(org)[0].mcp["servers"]["gh"]["credential"] == _reference(current)

    def test_one_org_never_sees_anothers_secret(self, move):
        first, second = _org("s2v org one"), _org("s2v org two")
        _version(first, 1, {"servers": {"gh": _bearer(_encrypt(GH))}}, active=True)
        _version(second, 1, {"servers": {"gh": _bearer(_encrypt(KV))}}, active=True)
        _forwards(move)
        assert decrypt_token(_vault(first)["gh"].encrypted_secret) == GH
        assert decrypt_token(_vault(second)["gh"].encrypted_secret) == KV
        assert _rows(first)[0].mcp["servers"]["gh"]["credential"] == _reference(
            _vault(first)["gh"]
        )
        assert _rows(second)[0].mcp["servers"]["gh"]["credential"] == _reference(
            _vault(second)["gh"]
        )


def _vault_applied_at():
    return (
        MigrationRecorder(connection)
        .migration_qs.filter(app="prism", name="0035_mcp_credential")
        .values_list("applied", flat=True)
        .first()
    )


def _backdate(row, when):
    AgentccOrgConfig._base_manager.filter(pk=row.pk).update(created_at=when)


@pytest.mark.django_db
class TestTheAccessDefaultForServersFromBeforeTheVault:
    """M10's compatibility rule, as agentcc_mcp_migrate_secrets --apply sets it
    and --check requires it: an org with MCP servers in a version created
    before the vault's migration was applied gets mcp.access.default allow,
    unless it states a default."""

    def test_an_org_with_servers_from_before_the_vault_gets_allow(self, move):
        org = _org()
        old = _version(org, 1, {"servers": {"open": {"url": URL}}})
        _backdate(old, _vault_applied_at() - timedelta(days=1))
        _version(org, 2, {"servers": {"gh": _bearer(_encrypt(GH))}}, active=True)
        with pytest.raises(CommandError, match="mcp.access.default"):
            _run("--check")
        _forwards(move)
        active = _rows(org)[1]
        assert active.mcp["access"] == {"default": "allow"}
        assert "access" not in _rows(org)[0].mcp
        _run("--check")

    def test_a_stated_default_is_left_as_it_is(self, move):
        org = _org()
        old = _version(org, 1, {"servers": {"open": {"url": URL}}})
        _backdate(old, _vault_applied_at() - timedelta(days=1))
        _version(
            org,
            2,
            {"access": {"default": "deny"}, "servers": {"open": {"url": URL}}},
            active=True,
        )
        _forwards(move)
        assert _rows(org)[1].mcp["access"] == {"default": "deny"}
        _run("--check")

    def test_an_org_whose_servers_came_after_the_vault_gets_no_default(self, move):
        org = _org()
        _version(org, 1, {"servers": {"gh": _bearer(_encrypt(GH))}}, active=True)
        _forwards(move)
        assert "access" not in _rows(org)[0].mcp
        _run("--check")


def test_the_migration_is_one_transaction_per_org(move):
    # The whole move in one transaction would hold every org's config lock
    # until the last org was moved.
    assert move.Migration.atomic is False
    assert move.Migration.dependencies == [BEFORE]


def _prism_leaf():
    (leaf,) = [
        node
        for node in MigrationExecutor(connection).loader.graph.leaf_nodes()
        if node[0] == "prism"
    ]
    return leaf


def _migrate_back_to(target):
    """Unapply down to ``target`` and return what puts the session's leaves
    back (as test_encrypt_org_config_secrets_migration does)."""
    executor = MigrationExecutor(connection)
    restore = {}
    for step, backwards in executor.migration_plan([target]):
        if backwards:
            restore.setdefault(step.app_label, (step.app_label, step.name))
    executor.migrate([target])
    return list(restore.values())


@pytest.mark.django_db(transaction=True)
def test_migrate_runs_the_move_and_its_reverse_leaves_the_vault(move):
    leaf = _prism_leaf()
    restore = _migrate_back_to(BEFORE)
    try:
        # The session's prism leaf (0057, or a later one) depends on
        # 0056: going back to BEFORE unapplies it, and the restore re-applies
        # it, whatever the leaf is, even when a check here fails.
        assert ("prism", "0057_mcp_credential_origin") <= leaf, leaf
        assert leaf in restore, restore
        # Restore the retirement leaf too, not only the earlier vault origin.
        retirement = ("prism", "0058_retire_ee_credentials")
        assert retirement in restore, restore
        plan = MigrationExecutor(connection).loader.graph.forwards_plan(retirement)
        assert MIGRATION in plan
        assert ("prism", "0057_mcp_credential_origin") in plan
        org = _org()
        _version(org, 1, {"servers": {"gh": _bearer(_encrypt(GH))}}, active=True)
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATION])
        # Read the vault as 0056 left it: the live model has the columns a
        # later migration adds (0057's origin), which this state lacks.
        credential = executor.loader.project_state([MIGRATION]).apps.get_model(
            "prism", "AgentccMCPCredential"
        )
        row = credential._base_manager.get(organization_id=org.pk, server_id="gh")
        assert decrypt_token(row.encrypted_secret) == GH
        assert _rows(org)[0].mcp["servers"]["gh"]["credential"] == _reference(row)

        # The reverse is a no-op: the references and the vault row stay.
        MigrationExecutor(connection).migrate([BEFORE])
        assert _rows(org)[0].mcp["servers"]["gh"]["credential"] == _reference(row)
        assert AgentccMCPCredential.objects.filter(pk=row.pk).exists()
    finally:
        MigrationExecutor(connection).migrate(restore)
