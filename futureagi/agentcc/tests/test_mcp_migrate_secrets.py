"""``agentcc_mcp_migrate_secrets --apply`` (M14): every plaintext MCP secret in
every config version moves into the credential vault, the configs keep only
references, ``--check`` proves none is left, and a second run changes
nothing. The bare command is a dry run (IC-R1-10, test_r1_mcp_migrate_dry_run.py).
Config pushes are recorded from ``get_gateway_client``."""

import copy
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault
from agentcc.services.credential_manager import decrypt_token
from agentcc.services.org_config_writer import (
    MCPConfigRejected,
    activate_config_version,
    write_config_version_with,
)

COMMAND = "agentcc_mcp_migrate_secrets"
OLD = "test-m14-old-not-a-real-token"
MID = "test-m14-mid-not-a-real-token"
CUR = "test-m14-current-not-a-real-token"
DOCS = "test-m14-docs-not-a-real-key"
WEB = "test-m14-web-not-a-real-key"
GONE = "test-m14-gone-not-a-real-key"
UNUSED = "test-m14-unused-not-a-real-token"
ALL = (OLD, MID, CUR, DOCS, WEB, GONE, UNUSED)
URL = "https://mcp.example.com/mcp"


@pytest.fixture
def pushes():
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


def _bearer(token):
    return {"url": URL, "transport": "http", "auth": {"type": "bearer", "token": token}}


def _version(org, version, mcp, *, active=False, deleted=False):
    return AgentccOrgConfig._base_manager.create(
        organization=org, version=version, is_active=active, deleted=deleted, mcp=mcp
    )


def _run(*args):
    out, err = StringIO(), StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _check_fails():
    with pytest.raises(CommandError) as raised:
        _run("--check")
    return str(raised.value)


def _rows(org):
    return list(
        AgentccOrgConfig._base_manager.filter(organization=org).order_by("version")
    )


def _credential(org, server_id):
    return AgentccMCPCredential.objects.get(organization=org, server_id=server_id)


@pytest.fixture
def legacy_org(user):
    """Three versions holding secrets: v1, a deleted v2, and the active v3."""
    org = user.organization
    _version(org, 1, {"servers": {"github": _bearer(OLD)}})
    _version(
        org,
        2,
        {
            "servers": {
                "github": _bearer(MID),
                "gone": {
                    "url": URL,
                    "auth": {"type": "api_key", "header": "X-Key", "key": GONE},
                },
            }
        },
        deleted=True,
    )
    _version(
        org,
        3,
        {
            "enabled": True,
            "servers": {
                "github": _bearer(CUR),
                "docs": {
                    "url": URL,
                    "credential": {
                        "mode": "shared",
                        "secret": {
                            "type": "api_key",
                            "header": "x-api-key",
                            "value": DOCS,
                        },
                    },
                },
                # Never sent by the gateway: an auth of type none.
                "plain": {"url": URL, "auth": {"type": "none", "token": UNUSED}},
            },
            "managed_tools": {
                "credentials": {
                    "fagi.web": {
                        "mode": "shared",
                        "secret": {
                            "type": "api_key",
                            "header": "x-api-key",
                            "value": WEB,
                        },
                    }
                }
            },
        },
        active=True,
    )
    return org


@pytest.mark.integration
class TestMigrateSecrets:
    def test_migrate_moves_history_secrets_and_check_clean(self, legacy_org, pushes):
        org = legacy_org
        clean = Organization.objects.create(name="No MCP secrets")
        _version(clean, 1, {"servers": {"open": {"url": URL}}}, active=True)
        failure = _check_fails()
        assert "mcp.servers.github.auth.token" in failure or "plaintext" in failure

        out, _ = _run("--apply")

        rows = _rows(org)
        stored = json.dumps([row.mcp for row in rows])
        assert not any(secret in stored for secret in ALL)
        check_out, _ = _run("--check")
        assert "No plaintext MCP secrets" in check_out
        # The vault holds each server's newest secret, encrypted.
        github, docs, web, gone = (
            _credential(org, key) for key in ("github", "docs", "fagi.web", "gone")
        )
        assert decrypt_token(github.encrypted_secret) == CUR
        assert (github.kind, github.status, github.scope) == (
            "bearer",
            "active",
            "shared",
        )
        assert (decrypt_token(docs.encrypted_secret), docs.header_name) == (
            DOCS,
            "x-api-key",
        )
        assert decrypt_token(web.encrypted_secret) == WEB
        # A server only an older version holds is stored revoked.
        assert (decrypt_token(gone.encrypted_secret), gone.status) == (GONE, "revoked")
        assert not AgentccMCPCredential.objects.filter(
            organization=org, server_id="plain"
        ).exists()
        # Every version references the vault instead.
        new_active = rows[-1]
        assert (new_active.version, new_active.is_active) == (4, True)
        assert (
            new_active.change_description
            == "Move MCP secrets into the MCP credential vault"
        )
        for row in rows:
            github_ref = row.mcp["servers"]["github"]
            assert "auth" not in github_ref
            assert github_ref["credential"] == {
                "mode": "shared",
                "credential_id": str(github.pk),
                "state": "active",
            }
        assert rows[1].mcp["servers"]["gone"]["credential"]["state"] == "revoked"
        assert new_active.mcp["managed_tools"]["credentials"]["fagi.web"] == {
            "mode": "shared",
            "credential_id": str(web.pk),
            "state": "active",
        }
        assert new_active.mcp["servers"]["plain"] == {"url": URL}
        # The new active version was pushed, with the vault's secrets.
        payload = pushes[-1][1]["mcp"]
        assert payload["servers"]["github"]["credential"]["secret"]["value"] == CUR
        assert (
            payload["managed_tools"]["credentials"]["fagi.web"]["secret"]["value"]
            == WEB
        )
        # One line per migrated org, the org with no secret not among them.
        assert out.count(f"org {org.pk}:") == 1
        assert "credential(s) stored" in out
        assert str(clean.pk) not in out
        # A rollback to an older version carries no secret and uses the vault's.
        activate_config_version(rows[0])
        assert (
            pushes[-1][1]["mcp"]["servers"]["github"]["credential"]["secret"]["value"]
            == CUR
        )
        # An org without secrets was not touched.
        assert len(_rows(clean)) == 1

    def test_migrate_idempotent(self, legacy_org, pushes):
        org = legacy_org
        _run("--apply")
        snapshot = [
            (row.pk, row.version, row.is_active, row.mcp, row.updated_at)
            for row in _rows(org)
        ]
        credentials = list(
            AgentccMCPCredential.objects.filter(organization=org).values_list(
                "pk", "status", "version", "encrypted_secret"
            )
        )
        pushed = len(pushes)

        out, err = _run("--apply")

        assert [
            (row.pk, row.version, row.is_active, row.mcp, row.updated_at)
            for row in _rows(org)
        ] == snapshot
        assert (
            list(
                AgentccMCPCredential.objects.filter(organization=org).values_list(
                    "pk", "status", "version", "encrypted_secret"
                )
            )
            == credentials
        )
        assert len(pushes) == pushed
        assert (out, err) == ("", "")

    def test_vault_credential_already_present_wins(self, user, pushes):
        org = user.organization
        config = _version(org, 1, {"servers": {"github": {"url": URL}}}, active=True)
        kept = vault.create(
            org, "github", kind="bearer", value=MID, actor=user
        ).credential
        AgentccOrgConfig._base_manager.filter(pk=config.pk).update(
            mcp={"servers": {"github": _bearer(OLD)}}
        )

        _run("--apply")

        assert list(
            AgentccMCPCredential.objects.filter(organization=org).values_list(
                "pk", flat=True
            )
        ) == [kept.pk]
        assert decrypt_token(_credential(org, "github").encrypted_secret) == MID
        assert OLD not in json.dumps([row.mcp for row in _rows(org)])

    def test_unreferenced_vault_credential_does_not_win(self, user, pushes):
        """Review M14-recheck3 P3: a vault credential the active config does
        not reference (a generic save disconnected it) is revoked, as
        create() does, and is never bound into a migrated entry; the active
        version's own secret becomes the server's credential."""
        org = user.organization
        keep = {"url": "https://keep.example.com/mcp", "transport": "http"}
        _version(
            org,
            1,
            {"servers": {"docs": {"url": URL, "transport": "http"}, "keep": keep}},
            active=True,
        )
        orphan = vault.create(
            org, "docs", kind="bearer", value=MID, actor=user
        ).credential

        def disconnect(config):
            del config.mcp["servers"]["docs"]

        write_config_version_with(org, disconnect, description="Remove docs")
        # A version saved before M14 that holds docs' secret in clear is the
        # active one (a database from before the vault). Activation refuses
        # such a version since the W3b merge (M14 IR-1), so the state is set
        # directly, as that database would hold it.
        legacy = _version(
            org,
            9,
            {
                "servers": {
                    "docs": {**_bearer(CUR), "url": "https://b.example.com/mcp"},
                    "keep": keep,
                }
            },
        )
        AgentccOrgConfig._base_manager.filter(organization=org, is_active=True).update(
            is_active=False
        )
        legacy.is_active = True
        legacy.save(update_fields=["is_active"])

        _run("--apply")

        orphan.refresh_from_db()
        assert orphan.status == "revoked"
        current = AgentccMCPCredential.objects.get(
            organization=org, server_id="docs", status="active"
        )
        assert decrypt_token(current.encrypted_secret) == CUR
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=org, is_active=True, deleted=False
        )
        assert active.mcp["servers"]["docs"]["credential"] == {
            "mode": "shared",
            "credential_id": str(current.pk),
            "state": "active",
        }
        assert str(orphan.pk) not in json.dumps([row.mcp for row in _rows(org)][-2:])
        payload = pushes[-1][1]["mcp"]["servers"]["docs"]
        assert payload["credential"]["secret"]["value"] == CUR
        assert MID not in json.dumps(pushes[-1][1])

    def test_entry_left_without_a_secret_does_not_block_saves(
        self, auth_client, user, pushes
    ):
        """Review M14-recheck3 P2: a managed entry whose secret the gateway
        never sent (an api_key with no header) keeps its mode and state and
        gets no reference; afterwards an unrelated MCP save still passes."""
        org = user.organization
        _version(
            org,
            1,
            {
                "servers": {"docs": {"url": URL, "transport": "http"}},
                "managed_tools": {
                    "enabled": True,
                    "credentials": {
                        "fagi.web": {
                            "mode": "shared",
                            "state": "active",
                            "secret": {"type": "api_key", "value": UNUSED},
                        }
                    },
                },
            },
            active=True,
        )

        _run("--apply")

        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=org, is_active=True, deleted=False
        )
        assert active.mcp["managed_tools"]["credentials"]["fagi.web"] == {
            "mode": "shared",
            "state": "active",
        }
        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"mcp": {"servers": {"docs": {"timeout_ms": 5000}}}},
            format="json",
        )
        assert response.status_code == 200, response.json()
        assert "No plaintext MCP secrets" in _run("--check")[0]

    def test_access_default_only_for_orgs_with_servers_before_the_vault(
        self, user, pushes
    ):
        before = user.organization
        _version(before, 1, {"servers": {"docs": {"url": URL}}}, active=True)
        explicit = Organization.objects.create(name="Explicit deny")
        _version(
            explicit,
            1,
            {"servers": {"docs": {"url": URL}}, "access": {"default": "deny"}},
            active=True,
        )
        after = Organization.objects.create(name="Servers after the vault")
        _version(after, 1, {"servers": {"docs": {"url": URL}}}, active=True)
        cutoff = timezone.now()
        AgentccOrgConfig._base_manager.filter(
            organization__in=[before, explicit]
        ).update(created_at=cutoff - timedelta(days=30))
        AgentccOrgConfig._base_manager.filter(organization=after).update(
            created_at=cutoff + timedelta(seconds=1)
        )

        _run("--apply", "--servers-before", cutoff.isoformat())

        def access(org):
            config = AgentccOrgConfig.no_workspace_objects.get(
                organization=org, is_active=True
            )
            return config.version, config.mcp.get("access")

        assert access(before) == (2, {"default": "allow"})
        assert access(explicit) == (1, {"default": "deny"})
        assert access(after) == (1, None)
        # The default cutoff is the vault migration's applied time, after
        # every row of this test: a later run opens nothing.
        before_rows = len(_rows(before))
        _run("--apply")
        assert len(_rows(before)) == before_rows
        assert access(after) == (1, None)

    def test_check_names_paths_never_values(self, legacy_org):
        failure = _check_fails()
        out = StringIO()
        with pytest.raises(CommandError):
            call_command(COMMAND, "--check", stdout=out)
        printed = out.getvalue() + failure
        assert "mcp.servers.github.auth.token" in printed
        assert "mcp.managed_tools.credentials.fagi.web.secret" in printed
        assert not any(secret in printed for secret in ALL)

    @pytest.mark.parametrize(
        "active_github",
        [
            # The admin moved github to another host and removed its auth.
            {"url": "https://b.example.com/mcp", "transport": "http"},
            # The active auth is one the gateway never sent (type none).
            {
                "url": "https://b.example.com/mcp",
                "transport": "http",
                "auth": {"type": "none", "token": UNUSED},
            },
        ],
        ids=["auth_removed", "auth_unusable"],
    )
    def test_secret_only_in_history_is_stored_revoked(
        self, user, pushes, active_github
    ):
        """Review M14-adv2 P1: a secret only an older version holds must not
        become an active credential that the push sends to the server's new
        host; the active entry gets no reference at all."""
        org = user.organization
        old = _version(
            org,
            1,
            {
                "servers": {
                    "github": {
                        "url": "https://a.example.com/mcp",
                        "auth": {"type": "bearer", "token": OLD},
                    }
                }
            },
        )
        _version(
            org, 2, {"servers": {"github": copy.deepcopy(active_github)}}, active=True
        )

        _run(
            "--apply",
            "--servers-before",
            (timezone.now() - timedelta(days=365)).isoformat(),
        )

        row = _credential(org, "github")
        assert (decrypt_token(row.encrypted_secret), row.status) == (OLD, "revoked")
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=org, is_active=True, deleted=False
        )
        # No auth and no credential reference: the server stays without auth.
        assert active.mcp["servers"]["github"] == {
            "url": "https://b.example.com/mcp",
            "transport": "http",
        }
        assert vault.assemble_for_push(org.pk) == {}
        vault.push_active_config(org.pk)
        assert "credential" not in pushes[-1][1]["mcp"]["servers"]["github"]
        # W4 refuses rollback to a retired reference, rather than silently
        # activating the server without its credential. Nothing is pushed.
        old.refresh_from_db()
        assert old.mcp["servers"]["github"]["credential"] == {
            "mode": "shared",
            "credential_id": str(row.pk),
            "state": "revoked",
        }
        pushed_before = len(pushes)
        with pytest.raises(MCPConfigRejected) as rejected:
            activate_config_version(old)
        assert rejected.value.code == "mcp_credential_origin_changed"
        assert "github" in str(rejected.value) and "reconnect" in str(rejected.value)
        assert len(pushes) == pushed_before
        active.refresh_from_db()
        assert active.is_active
        assert "secret" not in pushes[-1][1]["mcp"]["servers"]["github"].get(
            "credential", {}
        )
        assert "No plaintext MCP secrets" in _run("--check")[0]


def _ng01(value):
    """A value as the release line stored it inline (NG-01's writer and
    migrations 0047/0050): enc:: plus Fernet ciphertext."""
    from agentcc.services.org_config_secrets import _encrypt

    return _encrypt(value)


def _unreadable(clear):
    """A well-formed enc:: Fernet token under a key this deployment does not
    hold."""
    from cryptography.fernet import Fernet

    blob = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return "enc::" + blob.decode()


@pytest.mark.integration
class TestReleaseLineCiphertext:
    """MCP-APPS-PLAN.md ruling 3: the release line's NG-01 enc:: MCP values
    move into the vault as the clear value the push sent. Migration 0056 moves
    them at migrate; --apply moves one an old pod wrote during the rollout.
    The vault never stores a value that starts with enc:: (new_secret), so a
    command that read the ciphertext literally would remove the secret and
    store nothing."""

    @pytest.fixture
    def release_org(self, user):
        org = user.organization
        _version(org, 1, {"servers": {"github": _bearer(_ng01(OLD))}})
        _version(
            org,
            2,
            {
                "servers": {
                    "github": _bearer(_ng01(CUR)),
                    "docs": {
                        "url": URL,
                        "credential": {
                            "mode": "shared",
                            "secret": {
                                "type": "api_key",
                                "header": "x-api-key",
                                "value": _ng01(DOCS),
                            },
                        },
                    },
                    "lost": _bearer(_unreadable(GONE)),
                },
                "managed_tools": {
                    "credentials": {
                        "fagi.web": {
                            "mode": "shared",
                            "secret": {
                                "type": "api_key",
                                "header": "x-api-key",
                                "value": _ng01(WEB),
                            },
                        }
                    }
                },
            },
            active=True,
        )
        return org

    def test_apply_stores_each_ciphertext_as_its_clear_value(self, release_org, pushes):
        org = release_org
        assert "plaintext" in _check_fails()

        _run("--apply")

        github, docs, web = (
            _credential(org, key) for key in ("github", "docs", "fagi.web")
        )
        assert (decrypt_token(github.encrypted_secret), github.status) == (
            CUR,
            "active",
        )
        assert (decrypt_token(docs.encrypted_secret), docs.header_name) == (
            DOCS,
            "x-api-key",
        )
        assert decrypt_token(web.encrypted_secret) == WEB
        # An unreadable token is removed and stored nowhere, as the push
        # dropped it.
        assert not AgentccMCPCredential.objects.filter(
            organization=org, server_id="lost"
        ).exists()
        rows = _rows(org)
        stored = json.dumps([row.mcp for row in rows])
        assert "enc::" not in stored
        for row in rows:
            assert row.mcp["servers"]["github"]["credential"] == {
                "mode": "shared",
                "credential_id": str(github.pk),
                "state": "active",
            }
        assert rows[-1].mcp["servers"]["lost"] == {
            "url": URL,
            "transport": "http",
        }
        payload = pushes[-1][1]["mcp"]
        assert payload["servers"]["github"]["credential"]["secret"]["value"] == CUR
        assert payload["servers"]["docs"]["credential"]["secret"]["value"] == DOCS
        assert "No plaintext MCP secrets" in _run("--check")[0]

    def test_the_dry_run_counts_ciphertext_as_secrets_to_store(self, release_org):
        org = release_org
        out, _ = _run()
        assert f"org {org.pk}: would store 3 credential(s) (3 active, 0 revoked)" in (
            out
        )
        # v1's older github token is superseded, and the unreadable one is
        # removed: neither is stored.
        assert "remove 2 superseded or unused secret(s)" in out
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
