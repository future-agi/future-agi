"""The MCP credential vault (M14): write-only encrypted credentials, their
references in the org config, push assembly, the console path, the save guard
and the connection probe.

The gateway admin API is faked at its HTTP client: config pushes are recorded
from ``get_gateway_client`` (the real payload builder runs, so a pushed
payload carries what the vault assembles), and the probe's HTTP call is
answered by ``FakeGateway``. Every test user belongs to an org.
"""

import copy
import json
import logging
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from structlog.testing import capture_logs

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.contracts.gateway_admin import (
    MCPManagedToolCredential,
    MCPServerCredential,
)
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credential_placement as placement
from agentcc.services import mcp_credentials as vault
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
CREDENTIALS = "/agentcc/mcp/credentials/"
PROBE = "/agentcc/mcp/probe/"
ORG_CONFIGS = "/agentcc/org-configs/"
CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "api_contracts" / "gateway"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
ADMIN_SCHEMA_ID = "https://futureagi.com/contracts/gateway/agentcc-admin.schema.json"

SECRET = "test-m14-secret-not-a-real-token"
SECRET_2 = "test-m14-rotated-not-a-real-token"
SECRET_3 = "test-m14-console-not-a-real-key"
GITHUB_URL = "https://mcp.github.example.com/mcp"


# --- Fakes and helpers -------------------------------------------------------


@pytest.fixture
def pushes():
    """Every org config the gateway was sent, as (org_id, payload)."""
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


@pytest.fixture(autouse=True)
def personal_eviction():
    with patch(
        "agentcc.services.gateway_admin.mcp_credentials.purge", return_value=True
    ):
        yield


class FakeGateway:
    """The gateway's POST /-/mcp/probe: answers ``result`` (or ``status`` and
    ``body``) and records each request body."""

    def __init__(self, result=None, status=200, body=None):
        self.status = status
        self.body = result if body is None else body
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None):
        self.calls.append((method, urlsplit(url).path, json))
        return httpx.Response(self.status, json=self.body)


@contextmanager
def faked_probe(gateway):
    client = MagicMock()
    client.__enter__.return_value = gateway
    client.__exit__.return_value = False
    with patch(
        "agentcc.services.gateway_admin.mcp_probe.httpx.Client", return_value=client
    ):
        yield gateway


def _golden(name):
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text())


def _admin_validator(definition):
    resources = [json.loads((CONTRACTS_DIR / "agentcc-admin.schema.json").read_text())]
    registry = Registry().with_resources(
        (doc["$id"], Resource.from_contents(doc)) for doc in resources
    )
    return Draft202012Validator(
        {"$ref": f"{ADMIN_SCHEMA_ID}#/$defs/{definition}"}, registry=registry
    )


def _seed(org, mcp=None):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp=mcp
        if mcp is not None
        else {"servers": {"github": {"url": GITHUB_URL, "transport": "http"}}},
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _versions(org):
    return AgentccOrgConfig._base_manager.filter(organization=org).count()


def _org_with_owner(name):
    """Another org with its own owner and default workspace."""
    org = Organization.objects.create(name=name)
    owner = User.objects.create_user(
        email=f"m14-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="M14 Other Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=owner,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    Workspace.objects.create(
        name="Other",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    return org, owner


def _member_client(org, workspace):
    member = User.objects.create_user(
        email=f"m14-member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="M14 Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    return client


def _create(client, server_id="github", value=SECRET, **extra):
    response = client.post(
        CREDENTIALS,
        {"server_id": server_id, "kind": "bearer", "value": value, **extra},
        format="json",
    )
    assert response.status_code == 201, response.json()
    return response.json()["result"]


def _row(credential_id):
    return AgentccMCPCredential.objects.get(pk=credential_id)


def _pushed_credential(pushes, server_id):
    payload = pushes[-1][1]
    return payload["mcp"]["servers"][server_id].get("credential", {})


def _dump(value):
    return json.dumps(value, default=str)


# --- Tests -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestCredentialVault:
    def test_value_never_returned_by_any_endpoint(
        self, auth_client, user, pushes, caplog, gateway_unreachable
    ):
        org = user.organization
        _seed(org)
        caplog.set_level(logging.DEBUG)
        bodies = []
        with capture_logs() as logs:
            created = _create(auth_client)
            bodies.append(created)
            bodies.append(auth_client.get(CREDENTIALS).json())
            rotated = auth_client.post(
                f"{CREDENTIALS}{created['id']}/rotate/",
                {"value": SECRET_2},
                format="json",
            )
            bodies.append(rotated.json())
            console = auth_client.post(
                f"{GATEWAY}/update-mcp-server/",
                {
                    "server_id": "docs",
                    "config": {
                        "url": "https://docs.example.com/mcp",
                        "transport": "http",
                        "auth": {"type": "api_key", "header": "X-Key", "key": SECRET_3},
                    },
                },
                format="json",
            )
            bodies.append(console.json())
            for path in (f"{GATEWAY}/config/", f"{ORG_CONFIGS}active/", ORG_CONFIGS):
                bodies.append(auth_client.get(path).json())
            with faked_probe(FakeGateway(_golden("mcp-probe-v1")["results"][0])):
                probe = auth_client.post(
                    PROBE,
                    {"url": GITHUB_URL, "credential_id": created["id"]},
                    format="json",
                )
            bodies.append(probe.json())
            bodies.append(auth_client.delete(f"{CREDENTIALS}{created['id']}/").json())

        assert rotated.status_code == console.status_code == probe.status_code == 200
        secrets = (SECRET, SECRET_2, SECRET_3)
        rendered = _dump(bodies)
        assert not any(secret in rendered for secret in secrets)
        assert not any(secret in _dump(logs) for secret in secrets)
        assert not any(secret in caplog.text for secret in secrets)
        stored = _dump(
            list(
                AgentccOrgConfig._base_manager.filter(organization=org).values_list(
                    "mcp", flat=True
                )
            )
        )
        assert not any(secret in stored for secret in secrets)
        for row in AgentccMCPCredential.objects.filter(organization=org):
            assert row.encrypted_secret.startswith("enc::")
            assert not any(secret in row.encrypted_secret for secret in secrets)
        # The push is the one place a secret travels, which proves the check
        # above could see one.
        assert SECRET_3 in _dump(pushes)
        assert gateway_unreachable, "this path asks the gateway, which is not running"

    def test_update_mcp_server_encrypts_and_stores_ref(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)

        def save(key):
            response = auth_client.post(
                f"{GATEWAY}/update-mcp-server/",
                {
                    "server_id": "docs",
                    "config": {
                        "url": "https://docs.example.com/mcp",
                        "transport": "http",
                        "auth": {"type": "api_key", "header": "X-API-Key", "key": key},
                    },
                },
                format="json",
            )
            assert response.status_code == 200, response.json()
            return _active(org).mcp["servers"]["docs"]

        entry = save(SECRET)
        assert "auth" not in entry
        credential_id = entry["credential"]["credential_id"]
        assert entry["credential"] == {
            "mode": "shared",
            "credential_id": credential_id,
            "state": "active",
        }
        row = _row(credential_id)
        assert (row.organization_id, row.server_id, row.scope) == (
            org.pk,
            "docs",
            "shared",
        )
        assert (row.kind, row.header_name, row.version) == ("api_key", "X-API-Key", 1)
        assert row.encrypted_secret.startswith("enc::")
        assert decrypt_token(row.encrypted_secret) == SECRET
        assert _pushed_credential(pushes, "docs")["secret"] == {
            "type": "api_key",
            "header": "X-API-Key",
            "value": SECRET,
        }

        # A new value rotates the same credential in place.
        entry = save(SECRET_2)
        assert entry["credential"]["credential_id"] == credential_id
        row.refresh_from_db()
        assert row.version == 2 and decrypt_token(row.encrypted_secret) == SECRET_2
        assert (
            AgentccMCPCredential.objects.filter(
                organization=org, server_id="docs", status="active"
            ).count()
            == 1
        )
        assert _pushed_credential(pushes, "docs")["secret"]["value"] == SECRET_2

    @pytest.mark.parametrize(
        "mcp_patch, path",
        [
            (
                {"servers": {"github": {"auth": {"type": "bearer", "token": SECRET}}}},
                "mcp.servers.github.auth.token",
            ),
            (
                {
                    "servers": {
                        "github": {
                            "auth": {"type": "api_key", "header": "X", "key": SECRET}
                        }
                    }
                },
                "mcp.servers.github.auth.key",
            ),
            (
                {
                    "servers": {
                        "github": {
                            "credential": {
                                "mode": "shared",
                                "secret": {"type": "bearer", "value": SECRET},
                            }
                        }
                    }
                },
                "mcp.servers.github.credential.secret",
            ),
            (
                {
                    "managed_tools": {
                        "credentials": {
                            "fagi.web": {
                                "mode": "shared",
                                "secret": {
                                    "type": "api_key",
                                    "header": "x-api-key",
                                    "value": SECRET,
                                },
                            }
                        }
                    }
                },
                "mcp.managed_tools.credentials.fagi.web.secret",
            ),
        ],
        ids=["auth-token", "auth-key", "credential-secret", "managed-secret"],
    )
    def test_generic_save_with_secret_400(
        self, auth_client, user, pushes, mcp_patch, path
    ):
        org = user.organization
        _seed(org)
        before = _versions(org)

        update = auth_client.post(
            f"{GATEWAY}/update-config/", {"mcp": mcp_patch}, format="json"
        )
        stored = copy.deepcopy(_active(org).mcp)
        for section, value in mcp_patch.items():
            stored.setdefault(section, {})
            for key, item in value.items():
                stored[section][key] = {**stored[section].get(key, {}), **item}
        create = auth_client.post(ORG_CONFIGS, {"mcp": stored}, format="json")

        for response in (update, create):
            assert response.status_code == 400, response.json()
            assert response.json()["code"] == "secret_in_config"
            assert path in json.dumps(response.json())
            assert SECRET not in json.dumps(response.json())
        assert _versions(org) == before
        assert pushes == []

    def test_unchanged_legacy_secret_does_not_block_other_saves(
        self, auth_client, user, pushes
    ):
        """A secret stored before M14 is the migration command's to move; a
        save that leaves it as it is (the console sends the stored section
        back) is not refused, and a changed one is."""
        org = user.organization
        legacy = {"type": "bearer", "token": SECRET}
        _seed(org, {"servers": {"old": {"url": GITHUB_URL, "auth": legacy}}})

        kept = copy.deepcopy(_active(org).mcp)
        kept["servers"]["new"] = {
            "url": "https://n.example.com/mcp",
            "transport": "http",
        }
        assert (
            auth_client.post(ORG_CONFIGS, {"mcp": kept}, format="json").status_code
            == 200
        )

        changed = copy.deepcopy(kept)
        changed["servers"]["old"]["auth"]["token"] = SECRET_2
        response = auth_client.post(ORG_CONFIGS, {"mcp": changed}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "secret_in_config"

    def test_assemble_for_push_shared_active_only(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        github = vault.create(org, "github", kind="bearer", value=SECRET, actor=user)
        managed = vault.create(
            org,
            "fagi.web",
            kind="api_key",
            value=SECRET_2,
            header_name="x-api-key",
            actor=user,
        ).credential
        vault.create(
            org,
            "github",
            kind="bearer",
            value=SECRET_3,
            scope="user",
            user=user,
            actor=user,
        )
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True
        ).update(
            mcp={
                **_active(org).mcp,
                "servers": {
                    **_active(org).mcp["servers"],
                    "gone": {"url": GITHUB_URL},
                },
            }
        )
        gone = vault.store(
            org,
            "gone",
            vault.new_secret("bearer", "test-m14-revoked-not-real"),
            actor=user,
        )
        vault.revoke(gone, user)
        AgentccMCPCredential.objects.create(
            organization=org,
            server_id="oauthy",
            kind="oauth",
            encrypted_secret=encrypt_token("test-m14-oauth-not-real"),
            created_by=user,
        )

        assert vault.assemble_for_push(org.pk) == {
            "fagi.web": {"type": "api_key", "header": "x-api-key", "value": SECRET_2},
            "github": {"type": "bearer", "header": "Authorization", "value": SECRET},
        }
        # Through the M2 hook, each lands where the gateway reads it.
        auth_client.post(f"{GATEWAY}/reload/", {}, format="json")
        payload = pushes[-1][1]["mcp"]
        assert payload["servers"]["github"]["credential"]["secret"]["value"] == SECRET
        assert payload["servers"]["github"]["credential"]["credential_id"] == str(
            github.credential.pk
        )
        assert payload["managed_tools"]["credentials"]["fagi.web"] == {
            "mode": "shared",
            "credential_id": str(managed.pk),
            "state": "active",
            "revision": managed.grant_revision,
            "secret": {"type": "api_key", "header": "x-api-key", "value": SECRET_2},
        }
        assert "secret" not in payload["servers"]["gone"].get("credential", {})
        # Only a reference in state active is pushed, whatever the row says.
        lapsed = copy.deepcopy(_active(org).mcp)
        lapsed["servers"]["github"]["credential"]["state"] = "expired"
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True
        ).update(mcp=lapsed)
        assert set(vault.assemble_for_push(org.pk)) == {"fagi.web"}

    def test_rotate_bumps_version(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        created = _create(auth_client)
        before = _versions(org)

        response = auth_client.post(
            f"{CREDENTIALS}{created['id']}/rotate/", {"value": SECRET_2}, format="json"
        )

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert (result["id"], result["version"]) == (created["id"], 2)
        assert result["rotated_at"] and result["gateway_synced"] is True
        assert decrypt_token(_row(created["id"]).encrypted_secret) == SECRET_2
        assert _versions(org) == before  # the reference did not change
        assert _pushed_credential(pushes, "github")["secret"]["value"] == SECRET_2

        auth_client.delete(f"{CREDENTIALS}{created['id']}/")
        again = auth_client.post(
            f"{CREDENTIALS}{created['id']}/rotate/", {"value": SECRET}, format="json"
        )
        assert again.status_code == 400
        assert again.json()["code"] == "mcp_credential_not_active"

    def test_member_403(self, auth_client, user, workspace, pushes):
        org = user.organization
        _seed(org)
        created = _create(auth_client)
        member = _member_client(org, workspace)
        before = (_versions(org), _row(created["id"]).version, len(pushes))

        writes = [
            member.post(
                CREDENTIALS,
                {"server_id": "github", "kind": "bearer", "value": SECRET_2},
                format="json",
            ),
            member.post(
                f"{CREDENTIALS}{created['id']}/rotate/",
                {"value": SECRET_2},
                format="json",
            ),
            member.delete(f"{CREDENTIALS}{created['id']}/"),
            member.post(f"{CREDENTIALS}fagi.web/use-managed/", {}, format="json"),
            member.post(
                PROBE,
                {"url": GITHUB_URL, "credential_id": created["id"]},
                format="json",
            ),
        ]

        for response in writes:
            assert response.status_code == 403, response.json()
        assert (_versions(org), _row(created["id"]).version, len(pushes)) == before
        assert _row(created["id"]).status == "active"
        # Reads stay open to members, without values.
        listed = member.get(CREDENTIALS)
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()["results"]] == [created["id"]]

    def test_create_refusals(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        before = _versions(org)

        unknown = auth_client.post(
            CREDENTIALS,
            {"server_id": "nope", "kind": "bearer", "value": SECRET},
            format="json",
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "mcp_server_not_found"
        no_header = auth_client.post(
            CREDENTIALS,
            {"server_id": "github", "kind": "api_key", "value": SECRET},
            format="json",
        )
        assert no_header.status_code == 400
        reserved = auth_client.post(
            CREDENTIALS,
            {
                "server_id": "github",
                "kind": "api_key",
                "value": SECRET,
                "header_name": "Mcp-Session-Id",
            },
            format="json",
        )
        assert reserved.status_code == 400
        _create(auth_client)
        twice = auth_client.post(
            CREDENTIALS,
            {"server_id": "github", "kind": "bearer", "value": SECRET_2},
            format="json",
        )
        assert twice.status_code == 400
        assert twice.json()["code"] == "mcp_credential_exists"
        for response in (unknown, no_header, reserved, twice):
            assert SECRET not in json.dumps(response.json())
            assert SECRET_2 not in json.dumps(response.json())
        assert _versions(org) == before + 1
        assert AgentccMCPCredential.objects.filter(organization=org).count() == 1

    def test_list_is_org_scoped(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        own = _create(auth_client)
        other_org, owner = _org_with_owner("M14 Other Org")
        _seed(other_org)
        vault.create(other_org, "github", kind="bearer", value=SECRET_2, actor=owner)

        listed = auth_client.get(CREDENTIALS).json()

        assert [c["id"] for c in listed["results"]] == [own["id"]]
        assert listed["results"][0]["has_value"] is True
        assert "value" not in listed["results"][0]


@pytest.mark.integration
class TestOrgLockAndState:
    def _first_statement(self, queries):
        for query in queries:
            sql = query["sql"].upper()
            if not sql.startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
                return sql
        return ""

    def test_writes_take_org_lock_first(self, user, pushes):
        org = user.organization
        _seed(org)
        _seed_managed(org)

        def first_statement(write, *args, **kwargs):
            with CaptureQueriesContext(connection) as queries:
                result = write(*args, **kwargs)
            return result, self._first_statement(queries.captured_queries)

        statements = {}
        change, statements["create"] = first_statement(
            vault.create, org, "github", kind="bearer", value=SECRET, actor=user
        )
        _, statements["rotate"] = first_statement(
            vault.rotate, change.credential, SECRET_2, user
        )
        _, statements["mark_lapsed"] = first_statement(
            vault.mark_lapsed, change.credential, "needs_reauth", user
        )
        _, statements["create_managed"] = first_statement(
            vault.create, org, "fagi.web", kind="bearer", value=SECRET, actor=user
        )
        _, statements["use_managed"] = first_statement(
            vault.use_managed, org, "fagi.web", user
        )

        for name, first in statements.items():
            assert "FOR NO KEY UPDATE" in first, (name, first)
            assert '"ACCOUNTS_ORGANIZATION"' in first, (name, first)

    def test_revoke_keeps_mode_and_marks_state(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        _seed_managed(org)
        github = _create(auth_client)
        managed = _create(auth_client, server_id="fagi.web", value=SECRET_2)

        for created, read in (
            (github, lambda mcp: mcp["servers"]["github"]["credential"]),
            (managed, lambda mcp: mcp["managed_tools"]["credentials"]["fagi.web"]),
        ):
            response = auth_client.delete(f"{CREDENTIALS}{created['id']}/")
            assert response.status_code == 200, response.json()
            assert response.json()["result"]["status"] == "revoked"
            assert read(_active(org).mcp) == {
                "mode": "shared",
                "credential_id": created["id"],
                "state": "revoked",
            }
            assert "secret" not in read(pushes[-1][1]["mcp"])

        # An expiry and a failed refresh keep the entry the same way.
        for state in ("expired", "needs_reauth"):
            change = vault.create(
                org, "github", kind="bearer", value=SECRET, actor=user
            )
            vault.mark_lapsed(change.credential, state, user)
            assert _active(org).mcp["servers"]["github"]["credential"] == {
                "mode": "shared",
                "credential_id": str(change.credential.pk),
                "state": state,
            }

    def test_use_managed_is_explicit_and_audited(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        _seed_managed(org, enabled=False)
        created = _create(auth_client, server_id="fagi.web")
        url = f"{CREDENTIALS}fagi.web/use-managed/"

        refused = auth_client.post(url, {}, format="json")
        assert refused.status_code == 400
        assert refused.json()["code"] == "managed_tools_disabled"
        assert "fagi.web" in _active(org).mcp["managed_tools"]["credentials"]
        not_managed = auth_client.post(
            f"{CREDENTIALS}github/use-managed/", {}, format="json"
        )
        assert not_managed.status_code == 400

        auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"managed_tools": {"enabled": True}}},
            format="json",
        )
        personal = vault.create(
            org,
            "fagi.web",
            kind="bearer",
            value=SECRET_3,
            scope="user",
            user=user,
            actor=user,
        ).credential
        with capture_logs() as logs:
            response = auth_client.post(url, {}, format="json")

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert result["revoked"] == [created["id"]]
        active = _active(org)
        assert active.mcp["managed_tools"]["credentials"] == {}
        assert active.version == result["version"]
        assert active.created_by_id == user.pk
        assert active.change_description == "Use managed billing for fagi.web"
        assert _row(created["id"]).status == "revoked"
        # A user's own credential for the server is theirs and stays.
        assert _row(personal.pk).status == "active"
        [event] = [log for log in logs if log["event"] == "mcp_credential_use_managed"]
        assert (event["actor_id"], event["server_id"]) == (str(user.pk), "fagi.web")
        assert "fagi.web" not in pushes[-1][1]["mcp"]["managed_tools"].get(
            "credentials", {}
        )
        gone = auth_client.post(url, {}, format="json")
        assert gone.status_code == 404

    def test_use_managed_takes_no_request_body(self, auth_client, user, pushes):
        """The action's contract is an empty body (AgentccEmptyRequestSerializer),
        so the generated contract lists its request schema; a body is refused
        before anything is written."""
        org = user.organization
        _seed(org)
        _seed_managed(org, enabled=True)
        created = _create(auth_client, server_id="fagi.web")
        before = _active(org).version
        pushed = len(pushes)

        refused = auth_client.post(
            f"{CREDENTIALS}fagi.web/use-managed/", {"revoke": False}, format="json"
        )

        assert refused.status_code == 400, refused.json()
        assert _active(org).version == before
        assert "fagi.web" in _active(org).mcp["managed_tools"]["credentials"]
        assert _row(created["id"]).status == "active"
        assert len(pushes) == pushed


@pytest.mark.integration
@pytest.mark.api
class TestVaultOwnedReferences:
    def test_push_carries_only_referenced_credentials(self, auth_client, user, pushes):
        """Review M14-adv2 P2: a generic save deletes a server; its credential
        is no longer referenced, so re-adding the id at another URL with no
        auth pushes no secret, and a new credential for it can be created."""
        org = user.organization
        _seed(org, {"servers": {"docs": {"url": GITHUB_URL, "transport": "http"}}})
        saved = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "docs",
                "config": {
                    "url": GITHUB_URL,
                    "transport": "http",
                    "auth": {"type": "bearer", "token": SECRET},
                },
            },
            format="json",
        )
        assert saved.status_code == 200, saved.json()
        old = _shared(org, "docs")

        deleted = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": None}}},
            format="json",
        )
        assert deleted.status_code == 200, deleted.json()
        readded = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "docs",
                "config": {"url": "https://b.example.com/mcp", "transport": "http"},
            },
            format="json",
        )
        assert readded.status_code == 200, readded.json()

        assert "credential" not in _active(org).mcp["servers"]["docs"]
        assert vault.assemble_for_push(org.pk) == {}
        assert "credential" not in pushes[-1][1]["mcp"]["servers"]["docs"]
        # The unreferenced credential does not block a new one: it is revoked.
        created = _create(auth_client, server_id="docs", value=SECRET_2)
        assert _row(old.pk).status == "revoked"
        assert _pushed_credential(pushes, "docs")["secret"]["value"] == SECRET_2
        assert created["id"] != str(old.pk)

    def test_generic_save_cannot_change_vault_owned_references(
        self, auth_client, user, pushes
    ):
        """Review M14-adv2 P2: only the vault's own actions remove a managed
        server's credential entry or set a reference; a generic save that
        tries is 400 and changes nothing."""
        org = user.organization
        _seed(org)
        _seed_managed(org)
        github = _create(auth_client)
        managed = _create(auth_client, server_id="fagi.web", value=SECRET_2)
        managed_ref = _active(org).mcp["managed_tools"]["credentials"]["fagi.web"]
        before = (_versions(org), copy.deepcopy(_active(org).mcp))
        foreign = str(uuid.uuid4())

        for patch_mcp in (
            {"managed_tools": {"credentials": {"fagi.web": None}}},
            {"managed_tools": {"credentials": None}},
            {"managed_tools": {"credentials": {"fagi.web": {"mode": "per_user"}}}},
            {"managed_tools": {"credentials": {"fagi.web": {"state": "revoked"}}}},
            {
                "managed_tools": {
                    "credentials": {"fagi.web": {"credential_id": foreign}}
                }
            },
            {
                "managed_tools": {
                    "credentials": {
                        "fagi.other": {
                            "credential_id": managed["id"],
                            "state": "active",
                        }
                    }
                }
            },
            {"servers": {"github": {"credential": {"credential_id": foreign}}}},
            {"servers": {"github": {"credential": {"state": "expired"}}}},
            {
                "servers": {
                    "new": {
                        "url": "https://n.example.com/mcp",
                        "credential": {
                            "mode": "shared",
                            "credential_id": github["id"],
                            "state": "active",
                        },
                    }
                }
            },
        ):
            response = auth_client.post(
                f"{GATEWAY}/update-config/", {"mcp": patch_mcp}, format="json"
            )
            assert response.status_code == 400, (patch_mcp, response.json())
            assert response.json()["code"] == "mcp_credential_vault_owned"
        # The same through POST org-configs/ (a whole section).
        whole = copy.deepcopy(_active(org).mcp)
        del whole["managed_tools"]["credentials"]["fagi.web"]
        response = auth_client.post(ORG_CONFIGS, {"mcp": whole}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "mcp_credential_vault_owned"
        assert (_versions(org), _active(org).mcp) == before
        assert _row(managed["id"]).status == "active"

        # Saves that leave the references as they are pass, and dropping a
        # server's reference is a disconnect: nothing is pushed for it.
        kept = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"managed_tools": {"allow_user_tokens": True}}},
            format="json",
        )
        assert kept.status_code == 200, kept.json()
        assert (
            _active(org).mcp["managed_tools"]["credentials"]["fagi.web"] == managed_ref
        )
        dropped = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"github": {"credential": None}}}},
            format="json",
        )
        assert dropped.status_code == 200, dropped.json()
        assert "github" not in vault.assemble_for_push(org.pk)
        # The vault's own action still removes the managed entry.
        moved = auth_client.post(
            f"{CREDENTIALS}fagi.web/use-managed/", {}, format="json"
        )
        assert moved.status_code == 200, moved.json()

    @pytest.mark.parametrize(
        "entry",
        [
            {"mode": "per_user", "state": "needs_reauth"},
            {"mode": "shared", "state": "expired"},
        ],
        ids=["per_user_state", "shared_state_no_id"],
    )
    def test_unchanged_unreferenced_managed_entry_does_not_block_saves(
        self, auth_client, user, pushes, entry
    ):
        """A managed entry with a state and no
        credential_id (a contract-valid shape the vault does not own) that a
        write leaves as it is does not refuse that write; changing its state,
        or giving a new entry one, still does."""
        org = user.organization
        _seed(
            org,
            {
                "servers": {
                    "docs": {"url": GITHUB_URL, "transport": "http"},
                    "old": {"url": "https://o.example.com/mcp", "transport": "http"},
                },
                "managed_tools": {
                    "enabled": True,
                    "credentials": {"fagi.web": copy.deepcopy(entry)},
                },
            },
        )

        for path, body in (
            ("update-config/", {"mcp": {"servers": {"docs": {"timeout_ms": 5000}}}}),
            ("remove-mcp-server/", {"server_id": "old"}),
            ("update-mcp-guardrails/", {"config": {"blocked_tools": ["shell"]}}),
        ):
            response = auth_client.post(f"{GATEWAY}/{path}", body, format="json")
            assert response.status_code == 200, (path, response.json())
        whole = copy.deepcopy(_active(org).mcp)
        whole["servers"]["new"] = {
            "url": "https://n.example.com/mcp",
            "transport": "http",
        }
        response = auth_client.post(ORG_CONFIGS, {"mcp": whole}, format="json")
        assert response.status_code == 200, response.json()
        assert _active(org).mcp["managed_tools"]["credentials"]["fagi.web"] == entry

        for patch_mcp in (
            {"managed_tools": {"credentials": {"fagi.web": {"state": "active"}}}},
            {
                "managed_tools": {
                    "credentials": {"fagi.new": {"mode": "shared", "state": "active"}}
                }
            },
        ):
            response = auth_client.post(
                f"{GATEWAY}/update-config/", {"mcp": patch_mcp}, format="json"
            )
            assert response.status_code == 400, (patch_mcp, response.json())
            assert response.json()["code"] == "mcp_credential_vault_owned"


@pytest.mark.integration
@pytest.mark.api
class TestConsolePath:
    def _save(self, client, org, config, server_id="docs"):
        response = client.post(
            f"{GATEWAY}/update-mcp-server/",
            {"server_id": server_id, "config": config},
            format="json",
        )
        assert response.status_code == 200, response.json()
        return _active(org).mcp["servers"][server_id]

    def test_write_without_new_value_keeps_credential_ref(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _seed(org)
        url = "https://docs.example.com/mcp"
        first = self._save(
            auth_client,
            org,
            {
                "url": url,
                "transport": "sse",
                "auth": {"type": "bearer", "token": SECRET},
            },
        )
        reference = first["credential"]

        # No auth, no transport: both stored ones are kept.
        kept = self._save(auth_client, org, {"url": "https://docs.example.com/v2"})
        assert kept["credential"] == reference
        assert kept["transport"] == "sse"
        # A blank secret is "leave as is" too.
        blank = self._save(
            auth_client, org, {"url": url, "auth": {"type": "bearer", "token": ""}}
        )
        assert blank["credential"] == reference
        row = _row(reference["credential_id"])
        assert (row.version, row.status) == (1, "active")
        assert _pushed_credential(pushes, "docs")["secret"]["value"] == SECRET

        # Explicit removal revokes it.
        removed = self._save(auth_client, org, {"url": url, "auth": {"type": "none"}})
        assert removed["credential"] == {"mode": "none"}
        assert _row(reference["credential_id"]).status == "revoked"
        assert "secret" not in _pushed_credential(pushes, "docs")

    def test_legacy_stored_secret_moves_into_the_vault_on_edit(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _seed(
            org,
            {
                "servers": {
                    "docs": {
                        "url": GITHUB_URL,
                        "transport": "http",
                        "auth": {"type": "bearer", "token": SECRET},
                    }
                }
            },
        )

        entry = self._save(auth_client, org, {"url": GITHUB_URL})

        assert "auth" not in entry
        row = _row(entry["credential"]["credential_id"])
        assert decrypt_token(row.encrypted_secret) == SECRET
        assert SECRET not in _dump(_active(org).mcp)

    def test_vault_owned_fields_and_secret_in_request_refused(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _seed(org)
        reference = self._save(
            auth_client,
            org,
            {"url": GITHUB_URL, "auth": {"type": "bearer", "token": SECRET}},
        )["credential"]
        before = _versions(org)

        for config, code in (
            (
                {
                    "url": GITHUB_URL,
                    "credential": {"secret": {"type": "bearer", "value": SECRET_2}},
                },
                "secret_in_config",
            ),
            (
                {
                    "url": GITHUB_URL,
                    "credential": {**reference, "credential_id": str(uuid.uuid4())},
                },
                "invalid_mcp_credential",
            ),
            (
                {"url": GITHUB_URL, "auth": {"type": "bearer", "token": " padded "}},
                "invalid_mcp_credential",
            ),
        ):
            response = auth_client.post(
                f"{GATEWAY}/update-mcp-server/",
                {"server_id": "docs", "config": config},
                format="json",
            )
            assert response.status_code == 400, response.json()
            assert response.json()["code"] == code
            assert SECRET_2 not in json.dumps(response.json())
        assert _versions(org) == before

    def test_remove_mcp_server_revokes_its_credentials(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        created = _create(auth_client)

        response = auth_client.post(
            f"{GATEWAY}/remove-mcp-server/", {"server_id": "github"}, format="json"
        )

        assert response.status_code == 200, response.json()
        assert _row(created["id"]).status == "revoked"
        assert vault.assemble_for_push(org.pk) == {}

    def test_mcp_api_never_returns_auth_token(
        self, auth_client, user, pushes, gateway_unreachable
    ):
        org = user.organization
        _seed(org)
        saved = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "docs",
                "config": {
                    "url": "https://docs.example.com/mcp",
                    "transport": "http",
                    "auth": {"type": "bearer", "token": SECRET},
                },
            },
            format="json",
        )
        reads = {
            "update": saved.json(),
            "gateway-config": auth_client.get(f"{GATEWAY}/config/").json(),
            "active": auth_client.get(f"{ORG_CONFIGS}active/").json(),
            "versions": auth_client.get(ORG_CONFIGS).json(),
            "credentials": auth_client.get(CREDENTIALS).json(),
        }

        for name, body in reads.items():
            assert SECRET not in json.dumps(body), name
        servers = reads["gateway-config"]["result"]["mcp"]["servers"]
        assert "auth" not in servers["docs"]
        assert set(servers["docs"]["credential"]) == {"mode", "credential_id", "state"}
        assert gateway_unreachable, "this path asks the gateway, which is not running"


@pytest.mark.integration
@pytest.mark.api
class TestProbe:
    def test_probe_view_admin_only_org_scoped_secret_not_stored(
        self, auth_client, user, workspace, pushes, caplog
    ):
        org = user.organization
        golden = _golden("mcp-probe-v1")
        typed = golden["request"]["auth"]
        body = {k: v for k, v in golden["request"].items() if k != "org_id"}
        caplog.set_level(logging.DEBUG)

        for result in golden["results"]:
            with faked_probe(FakeGateway(result)) as gateway, capture_logs() as logs:
                response = auth_client.post(PROBE, body, format="json")
            assert response.status_code == 200, response.json()
            assert response.json()["result"] == result
            assert not list(_admin_validator("MCPProbeResult").iter_errors(result))
            [(method, path, sent)] = gateway.calls
            assert (method, path) == ("POST", "/-/mcp/probe")
            # The org is the session's, and the request is mcp-probe-v1's.
            assert sent == {**body, "org_id": str(org.pk)}
            assert not list(_admin_validator("MCPProbeRequest").iter_errors(sent))
            assert typed["value"] not in _dump(logs) + caplog.text
            assert typed["value"] not in json.dumps(response.json())

        # An org in the body is refused, never used.
        refused = auth_client.post(
            PROBE, {**body, "org_id": str(uuid.uuid4())}, format="json"
        )
        assert refused.status_code == 400
        # Nothing was stored or written.
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        assert _versions(org) == 0 and pushes == []
        # Members cannot probe.
        with faked_probe(FakeGateway(golden["results"][0])) as gateway:
            member = _member_client(org, workspace).post(PROBE, body, format="json")
        assert member.status_code == 403 and gateway.calls == []

    def test_probe_rejects_other_org_credential(self, auth_client, user, pushes):
        org = user.organization
        _seed(org)
        other_org, owner = _org_with_owner("M14 Probe Other Org")
        _seed(other_org)
        foreign = vault.create(
            other_org, "github", kind="bearer", value=SECRET_2, actor=owner
        ).credential
        own = _create(auth_client)
        auth_client.delete(f"{CREDENTIALS}{own['id']}/")

        for credential_id in (str(foreign.pk), own["id"], str(uuid.uuid4())):
            with faked_probe(
                FakeGateway({"ok": True, "latency_ms": 1, "error": None})
            ) as gateway:
                response = auth_client.post(
                    PROBE,
                    {"url": GITHUB_URL, "credential_id": credential_id},
                    format="json",
                )
            assert response.status_code == 404, response.json()
            assert response.json()["code"] == "mcp_credential_not_found"
            assert gateway.calls == []

    def test_probe_resolves_credential_and_sends_inline(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _seed(org)
        created = vault.create(
            org,
            "github",
            kind="api_key",
            value=SECRET,
            header_name="X-API-Key",
            actor=user,
        ).credential
        result = {
            "ok": True,
            "era": "2025-11-25",
            "tool_count": 2,
            "latency_ms": 9,
            "error": None,
        }

        with faked_probe(FakeGateway(result)) as gateway:
            response = auth_client.post(
                PROBE,
                {
                    "url": GITHUB_URL,
                    "transport": "http",
                    "credential_id": str(created.pk),
                },
                format="json",
            )

        assert response.status_code == 200, response.json()
        [(_, _, sent)] = gateway.calls
        assert sent == {
            "org_id": str(org.pk),
            "url": GITHUB_URL,
            "transport": "http",
            "auth": {"type": "api_key", "header": "X-API-Key", "value": SECRET},
        }
        assert "credential_id" not in sent
        assert SECRET not in json.dumps(response.json())

    def test_probe_sends_stored_secret_only_to_its_server_url(
        self, auth_client, user, pushes
    ):
        """Review M14-adv2 P1: a credential_id probe goes only to the URL its
        server is configured with; any other URL, a fagi.* credential (no URL
        in Django) and an orphan whose server is gone are 400 with the
        gateway not called."""
        org = user.organization
        _seed(org)
        _seed_managed(org)
        github = _create(auth_client)
        managed = _create(auth_client, server_id="fagi.web", value=SECRET_2)
        result = {"ok": True, "latency_ms": 3, "error": None}

        refusals = [
            (github["id"], "https://collector.attacker.example/mcp"),
            (github["id"], "https://mcp.github.example.com/other"),
            (github["id"], "http://mcp.github.example.com/mcp"),
            (managed["id"], GITHUB_URL),
        ]
        for credential_id, url in refusals:
            with faked_probe(FakeGateway(result)) as gateway:
                response = auth_client.post(
                    PROBE, {"url": url, "credential_id": credential_id}, format="json"
                )
            assert response.status_code == 400, (url, response.json())
            assert response.json()["code"] == "mcp_probe_url_mismatch"
            assert gateway.calls == []
            assert SECRET not in json.dumps(response.json())

        # The same upstream spelled differently (host case, default port,
        # trailing slash) is the configured one.
        with faked_probe(FakeGateway(result)) as gateway:
            response = auth_client.post(
                PROBE,
                {
                    "url": "https://MCP.github.example.com:443/mcp/",
                    "credential_id": github["id"],
                },
                format="json",
            )
        assert response.status_code == 200, response.json()
        [(_, _, sent)] = gateway.calls
        assert sent["auth"]["value"] == SECRET

        # A typed secret may still test any URL (test-before-save).
        with faked_probe(FakeGateway(result)) as gateway:
            typed = auth_client.post(
                PROBE,
                {
                    "url": "https://new.example.com/mcp",
                    "auth": {"type": "bearer", "value": SECRET_3},
                },
                format="json",
            )
        assert typed.status_code == 200, typed.json()

        # An orphan: its server was removed by a generic save.
        auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"github": None}}},
            format="json",
        )
        with faked_probe(FakeGateway(result)) as gateway:
            orphan = auth_client.post(
                PROBE, {"url": GITHUB_URL, "credential_id": github["id"]}, format="json"
            )
        # W4 retires the removed grant, rather than leaving an active orphan.
        assert _row(github["id"]).status == "revoked"
        assert orphan.status_code == 404
        assert orphan.json()["code"] == "mcp_credential_not_found"
        assert gateway.calls == []

    def test_probe_refuses_a_credential_the_config_does_not_push(
        self, auth_client, user, pushes
    ):
        """A shared credential is
        probed only while the active config's entry for its server names it
        with state active, the rule the push follows. A credential a generic
        save disconnected is now retired in the vault (W4) and pushed nowhere,
        so re-adding its server id, at another host or at the same URL, does not
        make the probe send its secret. A per-user credential is not
        referenced by id and keeps the URL check alone."""
        org = user.organization
        _seed(
            org,
            {
                "servers": {
                    "docs": {"url": GITHUB_URL, "transport": "http"},
                    "github": {"url": GITHUB_URL, "transport": "http"},
                }
            },
        )
        docs = _create(auth_client, server_id="docs")
        result = {"ok": True, "latency_ms": 2, "error": None}

        def probe(url, credential_id):
            with faked_probe(FakeGateway(result)) as gateway:
                response = auth_client.post(
                    PROBE, {"url": url, "credential_id": credential_id}, format="json"
                )
            return response, gateway.calls

        def refused(url, credential_id):
            response, calls = probe(url, credential_id)
            assert _row(credential_id).status == "revoked"
            assert response.status_code == 404, (url, response.json())
            assert response.json()["code"] == "mcp_credential_not_found"
            assert calls == []
            assert SECRET not in json.dumps(response.json())

        def generic_save(mcp_patch):
            response = auth_client.post(
                f"{GATEWAY}/update-config/", {"mcp": mcp_patch}, format="json"
            )
            assert response.status_code == 200, response.json()

        # Disconnected: the server is deleted, then re-added at another host.
        attacker = "https://collector.attacker.example/mcp"
        generic_save({"servers": {"docs": None}})
        generic_save({"servers": {"docs": {"url": attacker, "transport": "http"}}})
        assert _row(docs["id"]).status == "revoked"
        assert vault.assemble_for_push(org.pk) == {}
        refused(attacker, docs["id"])
        # Re-added at the very URL it was created for: still not referenced.
        generic_save({"servers": {"docs": {"url": GITHUB_URL, "transport": "http"}}})
        refused(GITHUB_URL, docs["id"])

        # A server that keeps its URL but loses its reference: the same.
        _create(auth_client, value=SECRET_2)
        github = _shared(org, "github")
        generic_save({"servers": {"github": {"credential": None}}})
        assert "github" not in vault.assemble_for_push(org.pk)
        refused(GITHUB_URL, str(github.pk))

        # Preserve the pre-W4 guard too: a legacy active orphan stored before
        # config removals retired grants must still never send its secret.
        AgentccMCPCredential.objects.filter(pk=github.pk).update(status="active")
        response, calls = probe(GITHUB_URL, str(github.pk))
        assert response.status_code == 400
        assert response.json()["code"] == "mcp_probe_url_mismatch"
        assert calls == []
        assert SECRET_2 not in json.dumps(response.json())

        # The requester's own per-user credential: the URL check alone.
        own = vault.create(
            org,
            "github",
            kind="bearer",
            value=SECRET_3,
            scope="user",
            user=user,
            actor=user,
        ).credential
        response, calls = probe(GITHUB_URL, str(own.pk))
        assert response.status_code == 200, response.json()
        [(_, _, sent)] = calls
        assert (sent["url"], sent["auth"]["value"]) == (GITHUB_URL, SECRET_3)
        response, calls = probe(attacker, str(own.pk))
        assert response.status_code == 400 and calls == []

    def test_probe_rate_limited_per_org(self, auth_client, user, pushes):
        result = {"ok": False, "latency_ms": 1, "error": "unreachable"}
        with faked_probe(FakeGateway(result)) as gateway:
            codes = [
                auth_client.post(PROBE, {"url": GITHUB_URL}, format="json").status_code
                for _ in range(11)
            ]
        assert codes == [200] * 10 + [429]
        assert len(gateway.calls) == 10

    def test_probe_gateway_refusal_and_outage(self, auth_client, user, pushes):
        refusal = {
            "error": "invalid_probe_request",
            "message": "transport must be http or sse",
        }
        with faked_probe(FakeGateway(status=400, body=refusal)):
            refused = auth_client.post(PROBE, {"url": GITHUB_URL}, format="json")
        assert refused.status_code == 400
        assert refused.json()["code"] == "invalid_probe_request"
        with faked_probe(FakeGateway(body={"ok": "maybe"})):
            garbled = auth_client.post(PROBE, {"url": GITHUB_URL}, format="json")
        assert garbled.status_code == 502
        assert garbled.json()["code"] == "gateway_unreachable"


@pytest.mark.integration
class TestRoutes:
    def test_routes_reverse_and_are_included_ahead_of_the_router(self):
        """M3's placeholder, now filled: still one include, ahead of the
        router, so no router pattern can shadow these paths."""
        from django.urls import URLResolver, reverse

        from agentcc import urls as agentcc_urls
        from agentcc import urls_mcp_credentials

        includes = [
            p
            for p in agentcc_urls.urlpatterns
            if isinstance(p, URLResolver) and p.urlconf_name is urls_mcp_credentials
        ]
        assert len(includes) == 1 and str(includes[0].pattern) == ""
        assert agentcc_urls.urlpatterns.index(
            includes[0]
        ) < agentcc_urls.urlpatterns.index(agentcc_urls.router.urls[0])
        some_id = uuid.uuid4()
        assert reverse("agentcc-mcp-credentials") == CREDENTIALS
        assert (
            reverse("agentcc-mcp-credential-detail", kwargs={"credential_id": some_id})
            == f"{CREDENTIALS}{some_id}/"
        )
        assert (
            reverse("agentcc-mcp-credential-rotate", kwargs={"credential_id": some_id})
            == f"{CREDENTIALS}{some_id}/rotate/"
        )
        assert (
            reverse(
                "agentcc-mcp-credential-use-managed", kwargs={"server_id": "fagi.web"}
            )
            == f"{CREDENTIALS}fagi.web/use-managed/"
        )
        assert reverse("agentcc-mcp-probe") == PROBE


@pytest.mark.parametrize(
    "reference",
    [
        {"credentialId": "JUNK", "credential_id": "ROW"},
        {"credentialID": "JUNK", "credential_id": "ROW"},
        {"credentialID": "JUNK", "credentialId": "ROW"},
        {"credentialID": "ROW"},
        {"credential_id": None, "credentialId": "JUNK"},
    ],
)
def test_reference_id_reads_the_spelling_the_contract_reads(reference):
    """A reference carrying two spellings of
    credential_id resolves as the contract resolves it (the first spelling
    present, in its alias order), in every worker, not in a set's
    hash-seeded order."""
    for model in (MCPServerCredential, MCPManagedToolCredential):
        expected = model.model_validate(reference).credential_id
        assert vault._reference_id(reference) == expected, model.__name__


def _seed_managed(org, enabled=True):
    config = _active(org)
    config.mcp = {**config.mcp, "managed_tools": {"enabled": enabled}}
    config.save(update_fields=["mcp"])


def _shared(org, server_id):
    return vault.active_shared(org.pk, server_id)


# --- CN11: where an API key goes (AM-C9, AM-C20) ------------------------------
#
# CN7's credential templates and the push rule's relaxation landed in the same
# CW1 merge (AM-C20 item 2, INTEGRATOR-REQUESTS R2): the push applies the
# gateway's own rule (mcp_credential_placement), so a key the vault places in
# a query parameter or a URL variable is pushed, and these tests run it for
# real.

QUERY_URL = "https://connect.browser.example.com/mcp"
VARIABLE_URL = "https://mcp.snow.example.com/accounts/{token}/mcp?db=main"
PLACED_URLS = {"browser": QUERY_URL, "snow": VARIABLE_URL, "github": GITHUB_URL}


def _seed_placements(org):
    return _seed(
        org,
        {
            "servers": {
                server_id: {"url": url, "transport": "http"}
                for server_id, url in PLACED_URLS.items()
            }
        },
    )


def _create_query_key(org, user, value=SECRET):
    return vault.create(
        org,
        "browser",
        kind="api_key",
        value=value,
        placement="query",
        name="apiKey",
        actor=user,
    ).credential


@pytest.mark.integration
class TestCredentialPlacement:
    """CN11: an API key goes in a header, as before, or in a query parameter
    or a URL variable of its server's url (AM-C9's ``in`` and ``name``)."""

    def test_query_placement_in_push_form(self, auth_client, user, pushes):
        """push_form says where each key goes: ``in`` and ``name`` for a
        query parameter or a URL variable, today's form for a header. Each
        is a valid MCPCredentialSecret, the push carries it whole, and the
        rule the merged push applies accepts it."""
        org = user.organization
        _seed_placements(org)
        query = _create_query_key(org, user)
        variable = vault.create(
            org,
            "snow",
            kind="api_key",
            value=SECRET_2,
            placement="url_variable",
            name="token",
            actor=user,
        ).credential
        vault.create(
            org,
            "github",
            kind="api_key",
            value=SECRET_3,
            header_name="X-API-Key",
            actor=user,
        )

        assembled = vault.assemble_for_push(org.pk)

        assert assembled == {
            "browser": {
                "type": "api_key",
                "in": "query",
                "name": "apiKey",
                "value": SECRET,
            },
            "snow": {
                "type": "api_key",
                "in": "url_variable",
                "name": "token",
                "value": SECRET_2,
            },
            "github": {"type": "api_key", "header": "X-API-Key", "value": SECRET_3},
        }
        validator = _admin_validator("MCPCredentialSecret")
        for server_id, secret in assembled.items():
            assert list(validator.iter_errors(secret)) == []
            assert (
                vault.pushed_placement_problem(secret, PLACED_URLS[server_id]) is None
            )
        servers = pushes[-1][1]["mcp"]["servers"]
        for server_id, secret in assembled.items():
            assert servers[server_id]["credential"]["secret"] == secret
        # The row keeps the placement; the server's url is never rewritten.
        assert _active(org).mcp["servers"]["snow"]["url"] == VARIABLE_URL
        assert (query.header_name, variable.header_name) == (
            "query:apiKey",
            "url_variable:token",
        )
        # A read says where each key goes, never its value.
        listed = {
            c["server_id"]: c for c in auth_client.get(CREDENTIALS).json()["results"]
        }
        assert {
            sid: (c["in"], c["name"], c["header_name"]) for sid, c in listed.items()
        } == {
            "browser": ("query", "apiKey", ""),
            "snow": ("url_variable", "token", ""),
            "github": ("header", "X-API-Key", "X-API-Key"),
        }
        assert not any(s in _dump(listed) for s in (SECRET, SECRET_2, SECRET_3))

    def test_placed_key_pushed_and_misplaced_keys_refused(self, user, pushes):
        """After the CW1 merge the push applies the gateway's rule: a key the
        vault places in a query parameter is stored and pushed whole, and a
        header key rotated into a query parameter moves there. A push the
        gateway could not place (a bearer in a query parameter, a {token} in
        the server's url that nothing fills) is refused at push and its write
        rolls back, so no row and no version is left that the gateway would
        not apply."""
        from agentcc.services import config_push
        from agentcc.services.config_push import MCPCredentialPlacementRefused

        org = user.organization
        _seed_placements(org)

        query = _create_query_key(org, user)
        assert _pushed_credential(pushes, "browser")["secret"] == {
            "type": "api_key",
            "in": "query",
            "name": "apiKey",
            "value": SECRET,
        }
        assert _row(query.pk).header_name == "query:apiKey"

        header = vault.create(
            org,
            "github",
            kind="api_key",
            value=SECRET_2,
            header_name="X-Key",
            actor=user,
        ).credential
        vault.rotate(_row(header.pk), SECRET_3, user, placement="query", name="key")
        assert _pushed_credential(pushes, "github")["secret"] == {
            "type": "api_key",
            "in": "query",
            "name": "key",
            "value": SECRET_3,
        }

        before = (_versions(org), len(pushes))
        misplaced = {
            "browser": {
                "type": "bearer",
                "in": "query",
                "name": "apiKey",
                "value": SECRET,
            },
            "snow": {
                "type": "api_key",
                "in": "url_variable",
                "name": "other",
                "value": SECRET,
            },
        }
        for server_id, field in (
            ("browser", "mcp.servers.browser.credential.secret.type"),
            # snow's url holds {token}, which a variable named other leaves
            # unfilled: refused naming the url, as the gateway does.
            ("snow", "mcp.servers.snow.url"),
        ):
            with (
                patch.object(
                    vault,
                    "assemble_for_push",
                    MagicMock(return_value={server_id: misplaced[server_id]}),
                ),
                pytest.raises(MCPCredentialPlacementRefused) as refused,
            ):
                config_push.push_org_config(str(org.pk), _active(org))
            assert refused.value.param == field
            assert refused.value.code == "mcp_credential_placement_unsupported"
            assert SECRET not in str(refused.value)
        assert (_versions(org), len(pushes)) == before

    def test_rest_create_and_rotate_take_in_and_name(self, auth_client, user, pushes):
        """M14's REST create and rotate take ``in`` and ``name`` (CN11 R4,
        after R2 lets a placed key push): a key placed in a query parameter or
        a URL variable is stored, pushed whole and described with them; a
        rotation moves it; a placement the vault refuses is 400 before
        anything is written; ``in`` must be one of the three placements."""
        org = user.organization
        _seed_placements(org)

        created = auth_client.post(
            CREDENTIALS,
            {
                "server_id": "browser",
                "kind": "api_key",
                "value": SECRET,
                "in": "query",
                "name": "apiKey",
            },
            format="json",
        )
        assert created.status_code == 201, created.json()
        result = created.json()["result"]
        assert (result["in"], result["name"], result["header_name"]) == (
            "query",
            "apiKey",
            "",
        )
        assert _pushed_credential(pushes, "browser")["secret"] == {
            "type": "api_key",
            "in": "query",
            "name": "apiKey",
            "value": SECRET,
        }

        rotated = auth_client.post(
            f"{CREDENTIALS}{result['id']}/rotate/",
            {"value": SECRET_2, "in": "header", "name": "X-Browser-Key"},
            format="json",
        )
        assert rotated.status_code == 200, rotated.json()
        assert (rotated.json()["result"]["in"], rotated.json()["result"]["name"]) == (
            "header",
            "X-Browser-Key",
        )
        assert _pushed_credential(pushes, "browser")["secret"] == {
            "type": "api_key",
            "header": "X-Browser-Key",
            "value": SECRET_2,
        }

        variable = auth_client.post(
            CREDENTIALS,
            {
                "server_id": "snow",
                "kind": "api_key",
                "value": SECRET_3,
                "in": "url_variable",
                "name": "token",
            },
            format="json",
        )
        assert variable.status_code == 201, variable.json()
        assert _pushed_credential(pushes, "snow")["secret"] == {
            "type": "api_key",
            "in": "url_variable",
            "name": "token",
            "value": SECRET_3,
        }

        before = (_versions(org), len(pushes))
        for body in (
            {"server_id": "github", "kind": "bearer", "in": "query", "name": "k"},
            {
                "server_id": "github",
                "kind": "api_key",
                "in": "url_variable",
                "name": "t",
            },
            {"server_id": "github", "kind": "api_key", "in": "cookie", "name": "k"},
        ):
            refused = auth_client.post(
                CREDENTIALS, {**body, "value": SECRET}, format="json"
            )
            assert refused.status_code == 400, refused.json()
            assert SECRET not in _dump(refused.json())
        assert (_versions(org), len(pushes)) == before
        assert not AgentccMCPCredential.objects.filter(
            organization=org, server_id="github"
        ).exists()

    def test_header_forms_are_unchanged(self, user, pushes):
        """A bearer and a header API key push exactly as before placements,
        so their fingerprints do not change: an explicit header placement is
        header_name by another name."""
        org = user.organization
        _seed_placements(org)
        vault.create(org, "github", kind="bearer", value=SECRET, actor=user)
        vault.create(
            org,
            "browser",
            kind="api_key",
            value=SECRET_2,
            placement="header",
            name="X-Browser-Key",
            actor=user,
        )

        assert vault.assemble_for_push(org.pk) == {
            "github": {"type": "bearer", "header": "Authorization", "value": SECRET},
            "browser": {
                "type": "api_key",
                "header": "X-Browser-Key",
                "value": SECRET_2,
            },
        }
        assert _pushed_credential(pushes, "browser")["secret"] == {
            "type": "api_key",
            "header": "X-Browser-Key",
            "value": SECRET_2,
        }
        assert vault.active_shared(org.pk, "browser").header_name == "X-Browser-Key"

    @pytest.mark.parametrize(
        "server_id, fields, message",
        [
            (
                "browser",
                {"kind": "bearer", "placement": "query", "name": "k"},
                "api_key",
            ),
            ("browser", {"placement": "query"}, "name"),
            ("browser", {"placement": "query", "name": "api key"}, "name"),
            ("browser", {"placement": "query", "name": "k" * 65}, "name"),
            (
                "browser",
                {"placement": "query", "name": "k", "header_name": "X-Key"},
                "header_name",
            ),
            ("browser", {"placement": "body", "name": "k"}, "placement"),
            ("snow", {"placement": "url_variable", "name": "1token"}, "name"),
            ("snow", {"placement": "url_variable", "name": "account"}, "{account}"),
            ("browser", {"placement": "url_variable", "name": "token"}, "{token}"),
            ("github", {"placement": "header", "name": "Host"}, "header"),
            (
                "github",
                {"placement": "header", "name": "X-A", "header_name": "X-B"},
                "header_name",
            ),
            ("fagi.web", {"placement": "query", "name": "k"}, "managed"),
        ],
        ids=[
            "bearer-in-query",
            "query-without-name",
            "query-name-space",
            "query-name-long",
            "query-with-header",
            "unknown-placement",
            "variable-name-digit",
            "variable-not-in-url",
            "no-variable-in-url",
            "reserved-header",
            "two-header-names",
            "managed-server",
        ],
    )
    def test_placement_refusals(self, user, pushes, server_id, fields, message):
        """Refused by the vault before anything is written, even where the
        merged push would take a placed key."""
        org = user.organization
        _seed_placements(org)
        _seed_managed(org)
        before = (_versions(org), len(pushes))
        fields = {"kind": "api_key", **fields}

        with pytest.raises(vault.MCPCredentialError) as refused:
            vault.create(org, server_id, value=SECRET, actor=user, **fields)

        assert message in str(refused.value)
        assert SECRET not in str(refused.value)
        assert AgentccMCPCredential.objects.filter(organization=org).count() == 0
        assert (_versions(org), len(pushes)) == before

    def test_per_user_key_goes_in_a_header(self, user):
        """Nothing fetches a per-user key placed elsewhere, so none is stored."""
        org = user.organization
        _seed_placements(org)
        with pytest.raises(vault.MCPCredentialError, match="only a shared credential"):
            vault.create(
                org,
                "browser",
                kind="api_key",
                value=SECRET,
                placement="query",
                name="apiKey",
                scope="user",
                user=user,
                actor=user,
            )
        assert AgentccMCPCredential.objects.filter(organization=org).count() == 0

    @pytest.mark.parametrize(
        "url",
        [
            "https://{token}.snow.example.com/mcp",
            "https://mcp.snow.example.com:{token}/mcp",
            "https://{token}@mcp.snow.example.com/mcp",
            "https://mcp.snow.example.com/mcp#{token}",
            "https://{token}.snow.example.com/mcp/{token}",
        ],
        ids=["host", "port", "userinfo", "fragment", "host-and-path"],
    )
    def test_url_variable_never_sets_the_origin(self, user, pushes, url):
        """A secret URL variable fills a path or query only: one the server's
        url puts in its scheme, user info, host, port or fragment is refused,
        so a secret never chooses where it is sent."""
        org = user.organization
        _seed(org, {"servers": {"snow": {"url": url, "transport": "http"}}})

        with pytest.raises(vault.MCPCredentialError) as refused:
            vault.create(
                org,
                "snow",
                kind="api_key",
                value=SECRET,
                placement="url_variable",
                name="token",
                actor=user,
            )

        assert "path or query" in str(refused.value)
        assert AgentccMCPCredential.objects.filter(organization=org).count() == 0

    def test_rotate_to_a_header_keeps_the_header_it_has(self, user, pushes):
        """``placement="header"`` with no header named keeps a header key's
        own header; a key placed elsewhere must name the header it moves to."""
        org = user.organization
        _seed_placements(org)
        header = vault.create(
            org, "github", kind="api_key", value=SECRET, header_name="X-Key", actor=user
        ).credential
        query = _create_query_key(org, user, value=SECRET_2)

        vault.rotate(_row(header.pk), SECRET_3, user, placement="header")

        assert _row(header.pk).header_name == "X-Key"
        assert vault.assemble_for_push(org.pk)["github"] == {
            "type": "api_key",
            "header": "X-Key",
            "value": SECRET_3,
        }
        with pytest.raises(vault.MCPCredentialError, match="needs header_name"):
            vault.rotate(_row(query.pk), SECRET_3, user, placement="header")
        assert _row(query.pk).header_name == "query:apiKey"

    def test_placement_survives_rotate(self, auth_client, user, pushes):
        org = user.organization
        _seed_placements(org)
        created = _create_query_key(org, user)

        # M14's rotate route sends no placement: the row keeps its own.
        response = auth_client.post(
            f"{CREDENTIALS}{created.pk}/rotate/", {"value": SECRET_2}, format="json"
        )

        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert (result["in"], result["name"], result["version"]) == (
            "query",
            "apiKey",
            2,
        )
        assert _pushed_credential(pushes, "browser")["secret"] == {
            "type": "api_key",
            "in": "query",
            "name": "apiKey",
            "value": SECRET_2,
        }
        # A rotation that names a header moves the key into that header.
        vault.rotate(_row(created.pk), SECRET_3, user, header_name="X-Browser-Key")
        assert vault.assemble_for_push(org.pk)["browser"] == {
            "type": "api_key",
            "header": "X-Browser-Key",
            "value": SECRET_3,
        }
        # A rotation into a URL variable is checked against the server's url.
        with pytest.raises(vault.MCPCredentialError, match="path or query"):
            vault.rotate(
                _row(created.pk), SECRET, user, placement="url_variable", name="token"
            )
        assert decrypt_token(_row(created.pk).encrypted_secret) == SECRET_3

    def test_probe_refuses_a_key_placed_outside_a_header(
        self, auth_client, user, pushes
    ):
        """The probe sends a credential in a header only: a stored key placed
        in a query parameter is refused here, not sent in a header it was
        never meant for."""
        org = user.organization
        _seed_placements(org)
        created = _create_query_key(org, user)

        with faked_probe(FakeGateway(_golden("mcp-probe-v1")["results"][0])) as fake:
            response = auth_client.post(
                PROBE,
                {"url": QUERY_URL, "credential_id": str(created.pk)},
                format="json",
            )

        assert response.status_code == 400, response.json()
        assert response.json()["code"] == "mcp_probe_credential_placement"
        assert SECRET not in _dump(response.json())
        assert fake.calls == []

    def test_origin_rules_hold_for_placed_keys(self, auth_client, user, pushes):
        """A key in a query parameter goes to its server's url like a header
        key: a save that moves the server to another origin while keeping it
        is refused."""
        org = user.organization
        _seed_placements(org)
        _create_query_key(org, user)
        moved = copy.deepcopy(_active(org).mcp)
        moved["servers"]["browser"]["url"] = "https://attacker.example.com/mcp"

        generic = auth_client.post(
            f"{GATEWAY}/update-config/", {"mcp": moved}, format="json"
        )
        console = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "browser",
                "config": {"url": "https://attacker.example.com/mcp"},
            },
            format="json",
        )

        assert (generic.status_code, console.status_code) == (400, 400)
        assert _active(org).mcp["servers"]["browser"]["url"] == QUERY_URL

    @pytest.mark.parametrize(
        "secret, url, field",
        [
            ({"type": "api_key", "header": "X-Key", "value": SECRET}, GITHUB_URL, None),
            (
                {"type": "bearer", "header": "Authorization", "value": SECRET},
                GITHUB_URL,
                None,
            ),
            (
                {
                    "type": "api_key",
                    "in": "header",
                    "name": "X-Other",
                    "header": "X-Key",
                    "value": SECRET,
                },
                GITHUB_URL,
                "header",
            ),
            (
                {"type": "api_key", "in": "query", "name": "apiKey", "value": SECRET},
                QUERY_URL,
                None,
            ),
            (
                {"type": "api_key", "in": "query", "name": "api key", "value": SECRET},
                QUERY_URL,
                "name",
            ),
            (
                {"type": "bearer", "in": "query", "name": "apiKey", "value": SECRET},
                QUERY_URL,
                "type",
            ),
            (
                {"type": "api_key", "in": "query", "name": "apiKey", "value": SECRET},
                None,
                "in",
            ),
            (
                {
                    "type": "api_key",
                    "in": "url_variable",
                    "name": "token",
                    "value": SECRET,
                },
                VARIABLE_URL,
                None,
            ),
            (
                {
                    "type": "api_key",
                    "in": "url_variable",
                    "name": "token",
                    "value": SECRET,
                },
                QUERY_URL,
                "name",
            ),
            (
                {"type": "api_key", "in": "body", "name": "k", "value": SECRET},
                QUERY_URL,
                "in",
            ),
            (
                {
                    "type": "api_key",
                    "header": "X-Key",
                    "value": SECRET,
                    "parts": [{"in": "header", "name": "X-App", "value": SECRET_2}],
                },
                GITHUB_URL,
                None,
            ),
            (
                {
                    "type": "api_key",
                    "header": "X-Key",
                    "value": SECRET,
                    "parts": [{"in": "header", "name": "X-App", "value": SECRET_2}],
                },
                None,
                "parts",
            ),
        ],
        ids=[
            "header",
            "bearer",
            "header-name-differs",
            "query",
            "query-bad-name",
            "query-bearer",
            "query-managed",
            "url-variable",
            "url-variable-not-in-url",
            "unknown-in",
            "parts",
            "parts-managed",
        ],
    )
    def test_pushed_placement_problem(self, secret, url, field):
        """The rule the merged push applies in place of the header-only one
        (INTEGRATOR-REQUESTS R2, IR-CN7-2: the gateway's own): what the vault
        stores passes, and so do parts on a customer server (the gateway
        applies them; no vault writer stores them yet, R5); a managed server
        takes a header only; no reason quotes a value."""
        problem = vault.pushed_placement_problem(secret, url)
        assert (problem[0] if problem else None) == field
        if problem:
            assert SECRET not in problem[1] and SECRET_2 not in problem[1]

    def test_pushed_placement_problem_with_a_catalog_ref_and_no_catalog_fails_closed(
        self,
    ):
        """AM-C23 (d): the vault's alias is the catalog rule, and its catalog
        defaults to None (no catalog loaded), so a catalog_ref with no catalog
        is refused on catalog_ref, as the gateway refuses it, never pushed."""
        assert vault.pushed_placement_problem is placement.pushed_placement_problem
        secret = {"type": "api_key", "header": "Close-API-Key", "value": SECRET}
        url = "https://mcp.example.com/mcp"
        problem = vault.pushed_placement_problem(
            secret, url, None, "close/api-key@1.0.0"
        )
        assert problem is not None and problem[0] == "catalog_ref"
        assert SECRET not in problem[1]
