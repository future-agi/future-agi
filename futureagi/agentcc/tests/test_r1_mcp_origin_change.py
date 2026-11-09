"""IC-R1-9 (R1 preamble P2; X6-R1 ORCHESTRATOR-RULINGS 2026-10-19 03:50 item
5 (e), M14 IR-9): keep-on-blank does not follow an origin change. A save that
moves an MCP server's url to another scheme, host or port while it sends no
new secret is refused (400 mcp_credential_origin_changed) with a message
asking to re-enter the credential, on the console's update-mcp-server and on
the generic writers (update-config, POST org-configs/) alike: an org editor
who cannot read a secret must not be able to send it to a host they control.
A path or query change keeps the stored secret.

IC-R1-12 (X6-R1 ruling 2026-10-19 13:01, the P2 review of IC-R1-9):
activating an older version runs the same origin check against the active
version. The vault rotates a re-entered credential in place under the same
id, so an older version whose server is at another origin still references
the live credential; activating it would push the current secret there.

IC-R1-12, the R1A review's P1: comparing with the active version alone is
defeated by first activating a version without the server, or removing it
with a save. So a shared credential is bound to the origin its secret was
entered or rotated for (``AgentccMCPCredential.origin``), the push and the
probe send it only to its server at that origin, and an activation of a
version that puts its server anywhere else is refused.

Pushes are recorded from ``get_gateway_client``, so the real payload builder
runs and a pushed payload carries what the vault assembles."""

import copy
import importlib
import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.core.management import call_command

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_credentials as vault

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
CODE = "mcp_credential_origin_changed"
SECRET = "test-r1-origin-not-a-real-token"
SECRET_2 = "test-r1-origin-reentered-not-a-real-token"
SECRET_3 = "test-r1-origin-rotated-not-a-real-token"
URL = "https://docs.example.com/mcp"
MOVED = {
    "host": "https://collector.example.net/mcp",
    "scheme": "http://docs.example.com/mcp",
    "port": "https://docs.example.com:8443/mcp",
}
SAME_ORIGIN = {
    "path": "https://docs.example.com/v2",
    "query": "https://docs.example.com/mcp?tenant=a",
    "default_port_and_case": "https://DOCS.example.com:443/mcp",
}


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


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _versions(org):
    return AgentccOrgConfig._base_manager.filter(organization=org).count()


def _pushed_docs(pushes):
    return pushes[-1][1]["mcp"]["servers"]["docs"]


def _console(client, config):
    return client.post(
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": "docs", "config": config},
        format="json",
    )


def _saved_with_secret(client, org, pushes):
    """docs saved through the console with SECRET: its reference, pushed."""
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, mcp={"servers": {}}
    )
    response = _console(
        client,
        {"url": URL, "transport": "http", "auth": {"type": "bearer", "token": SECRET}},
    )
    assert response.status_code == 200, response.json()
    reference = _active(org).mcp["servers"]["docs"]["credential"]
    assert _pushed_docs(pushes)["credential"]["secret"]["value"] == SECRET
    return reference


def _refused(response, org, before, pushes, pushed_before):
    body = response.json()
    assert response.status_code == 400, body
    assert body["code"] == CODE, body
    assert "re-enter" in json.dumps(body), body
    assert SECRET not in json.dumps(body)
    assert _versions(org) == before
    assert _active(org).mcp["servers"]["docs"]["url"] == URL
    assert len(pushes) == pushed_before


@pytest.mark.integration
@pytest.mark.api
class TestConsoleSaveAcrossOrigins:
    @pytest.mark.parametrize("moved", MOVED.values(), ids=MOVED.keys())
    @pytest.mark.parametrize(
        "blank", [None, {"type": "bearer", "token": ""}], ids=["no_auth", "empty_token"]
    )
    def test_origin_change_with_a_blank_secret_is_refused(
        self, auth_client, user, pushes, moved, blank
    ):
        org = user.organization
        _saved_with_secret(auth_client, org, pushes)
        before, pushed_before = _versions(org), len(pushes)
        config = {"url": moved} if blank is None else {"url": moved, "auth": blank}

        _refused(_console(auth_client, config), org, before, pushes, pushed_before)

    @pytest.mark.parametrize("url", SAME_ORIGIN.values(), ids=SAME_ORIGIN.keys())
    def test_same_origin_keeps_the_stored_secret(self, auth_client, user, pushes, url):
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)

        response = _console(auth_client, {"url": url})

        assert response.status_code == 200, response.json()
        entry = _active(org).mcp["servers"]["docs"]
        assert (entry["url"], entry["credential"]) == (url, reference)
        assert _pushed_docs(pushes)["url"] == url
        assert _pushed_docs(pushes)["credential"]["secret"]["value"] == SECRET

    def test_origin_change_with_a_new_secret_or_none_is_saved(
        self, auth_client, user, pushes
    ):
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)

        response = _console(
            auth_client,
            {"url": MOVED["host"], "auth": {"type": "bearer", "token": SECRET_2}},
        )

        assert response.status_code == 200, response.json()
        entry = _active(org).mcp["servers"]["docs"]
        assert entry["url"] == MOVED["host"]
        assert entry["credential"]["credential_id"] == reference["credential_id"]
        assert (
            AgentccMCPCredential.objects.get(pk=reference["credential_id"]).version == 2
        )
        assert _pushed_docs(pushes)["credential"]["secret"]["value"] == SECRET_2

        response = _console(auth_client, {"url": URL, "auth": {"type": "none"}})
        assert response.status_code == 200, response.json()
        assert _active(org).mcp["servers"]["docs"]["credential"] == {"mode": "none"}
        assert "secret" not in _pushed_docs(pushes).get("credential", {})

    def test_legacy_inline_secret_does_not_follow_an_origin_change(
        self, auth_client, user, pushes
    ):
        """A secret saved in clear before the vault, which the console moves
        into the vault on an edit, does not move to another origin either."""
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp={
                "servers": {
                    "docs": {
                        "url": URL,
                        "transport": "http",
                        "auth": {"type": "bearer", "token": SECRET},
                    }
                }
            },
        )
        before = _versions(org)

        _refused(_console(auth_client, {"url": MOVED["host"]}), org, before, pushes, 0)
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()


@pytest.mark.integration
@pytest.mark.api
class TestGenericSaveAcrossOrigins:
    @pytest.mark.parametrize("moved", MOVED.values(), ids=MOVED.keys())
    def test_update_config_moving_a_referenced_server_is_refused(
        self, auth_client, user, pushes, moved
    ):
        org = user.organization
        _saved_with_secret(auth_client, org, pushes)
        before, pushed_before = _versions(org), len(pushes)

        response = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": {"url": moved}}}},
            format="json",
        )

        _refused(response, org, before, pushes, pushed_before)

    def test_post_org_configs_moving_a_referenced_server_is_refused(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _saved_with_secret(auth_client, org, pushes)
        before, pushed_before = _versions(org), len(pushes)
        mcp = copy.deepcopy(_active(org).mcp)
        mcp["servers"]["docs"]["url"] = MOVED["host"]

        response = auth_client.post(ORG_CONFIGS, {"mcp": mcp}, format="json")

        _refused(response, org, before, pushes, pushed_before)

    def test_path_change_or_dropping_the_reference_is_saved(
        self, auth_client, user, pushes
    ):
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)

        path_only = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": {"url": SAME_ORIGIN["path"]}}}},
            format="json",
        )
        assert path_only.status_code == 200, path_only.json()
        assert _active(org).mcp["servers"]["docs"]["credential"] == reference
        assert _pushed_docs(pushes)["credential"]["secret"]["value"] == SECRET

        # The same write that moves the server drops its reference: nothing
        # is sent to the new origin.
        dropped = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": {"url": MOVED["host"], "credential": None}}}},
            format="json",
        )
        assert dropped.status_code == 200, dropped.json()
        entry = _active(org).mcp["servers"]["docs"]
        assert (entry["url"], "credential" in entry) == (MOVED["host"], False)
        assert "secret" not in _pushed_docs(pushes).get("credential", {})


EDITOR_TOKEN = "test-r1-origin-editor-not-a-real-token"


def _activate(client, config):
    return client.post(f"{ORG_CONFIGS}{config.pk}/activate/", format="json")


def _pushed_after(pushes, pushed_before):
    """What the pushes since ``pushed_before`` sent for docs: its url and
    which of this file's test secrets went with it, by name (never the value
    itself), or None."""
    names = {
        SECRET: "SECRET",
        SECRET_2: "SECRET_2",
        SECRET_3: "SECRET_3",
        EDITOR_TOKEN: "EDITOR_TOKEN",
    }
    sent = []
    for _, payload in pushes[pushed_before:]:
        docs = payload["mcp"]["servers"].get("docs", {})
        secret = docs.get("credential", {}).get("secret", {}).get("value")
        sent.append(
            (docs.get("url"), None if secret is None else names.get(secret, "other"))
        )
    return sent


@pytest.mark.integration
@pytest.mark.api
class TestActivationAcrossOrigins:
    def test_older_version_at_another_origin_is_refused(
        self, auth_client, user, pushes
    ):
        """The P2 review's scenario: an editor moves docs to their host with a
        token of their own (a new secret, so the save is allowed and the
        credential is rotated in place), an admin moves it back with the real
        token (rotated in place again, same id), and the editor activates the
        moved version. It is refused: the real secret is never pushed to the
        editor's host."""
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)
        moved = _console(
            auth_client,
            {"url": MOVED["host"], "auth": {"type": "bearer", "token": EDITOR_TOKEN}},
        )
        assert moved.status_code == 200, moved.json()
        older = _active(org)
        back = _console(
            auth_client, {"url": URL, "auth": {"type": "bearer", "token": SECRET_2}}
        )
        assert back.status_code == 200, back.json()
        for version in (older, _active(org)):
            credential = version.mcp["servers"]["docs"]["credential"]
            assert credential["credential_id"] == reference["credential_id"]
        before, pushed_before = _versions(org), len(pushes)

        response = _activate(auth_client, older)

        assert response.status_code == 400, (
            response.status_code,
            "pushed (url, secret) since the activation:",
            _pushed_after(pushes, pushed_before),
        )
        _refused(response, org, before, pushes, pushed_before)
        assert SECRET_2 not in json.dumps(response.json())
        assert _active(org).pk != older.pk

    def test_older_version_at_the_same_origin_is_activated(
        self, auth_client, user, pushes
    ):
        org = user.organization
        _saved_with_secret(auth_client, org, pushes)
        older = _active(org)
        path = _console(auth_client, {"url": SAME_ORIGIN["path"]})
        assert path.status_code == 200, path.json()
        pushed_before = len(pushes)

        response = _activate(auth_client, older)

        assert response.status_code == 200, response.json()
        assert _active(org).pk == older.pk
        assert _pushed_after(pushes, pushed_before) == [(URL, "SECRET")]
        assert _pushed_docs(pushes)["credential"]["secret"]["value"] == SECRET

    def test_older_version_at_another_origin_without_the_credential_is_activated(
        self, auth_client, user, pushes
    ):
        """An older version that does not reference the active credential sends
        it nowhere, so it activates."""
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True, mcp={"servers": {}}
        )
        unauthenticated = _console(
            auth_client,
            {"url": MOVED["host"], "transport": "http", "auth": {"type": "none"}},
        )
        assert unauthenticated.status_code == 200, unauthenticated.json()
        older = _active(org)
        secured = _console(
            auth_client, {"url": URL, "auth": {"type": "bearer", "token": SECRET}}
        )
        assert secured.status_code == 200, secured.json()
        pushed_before = len(pushes)

        response = _activate(auth_client, older)

        assert response.status_code == 200, response.json()
        assert _active(org).pk == older.pk
        assert _pushed_after(pushes, pushed_before) == [(MOVED["host"], None)]


# IC-R1-12, the R1A review's P1: the credential is bound to its origin.

CREDENTIALS = "/agentcc/mcp/credentials/"
DOCS_ORIGIN = "https://docs.example.com:443"
PORT_ORIGIN = "https://docs.example.com:8443"


def _row(credential_id):
    return AgentccMCPCredential.objects.get(pk=credential_id)


def _reload(client):
    response = client.post(f"{GATEWAY}/reload/", {}, format="json")
    assert response.status_code == 200, response.json()
    return response


def _force_active_url(org, server_id, url):
    """Put ``server_id`` at ``url`` in the active version past every write
    check (an ORM update): it stands for any path that reaches that state."""
    active = _active(org)
    mcp = copy.deepcopy(active.mcp)
    mcp["servers"][server_id]["url"] = url
    AgentccOrgConfig.no_workspace_objects.filter(pk=active.pk).update(mcp=mcp)


def _rotate(client, credential_id, value):
    return client.post(
        f"{CREDENTIALS}{credential_id}/rotate/", {"value": value}, format="json"
    )


@pytest.mark.integration
@pytest.mark.api
class TestCredentialBoundToItsOrigin:
    def test_push_sends_a_secret_only_to_the_origin_it_was_entered_for(
        self, auth_client, user, pushes
    ):
        """However the active version came to put the server at another
        origin (forced here past every write check), the push does not send
        the stored secret there; a path change at the same origin keeps it."""
        org = user.organization
        _saved_with_secret(auth_client, org, pushes)

        _force_active_url(org, "docs", SAME_ORIGIN["path"])
        pushed_before = len(pushes)
        _reload(auth_client)
        assert _pushed_after(pushes, pushed_before) == [(SAME_ORIGIN["path"], "SECRET")]

        sent = {}
        for name, url in MOVED.items():
            _force_active_url(org, "docs", url)
            pushed_before = len(pushes)
            _reload(auth_client)
            sent[name] = (
                _pushed_after(pushes, pushed_before),
                "docs" in vault.assemble_for_push(org.pk),
            )
        assert sent == {name: ([(url, None)], False) for name, url in MOVED.items()}

    def test_probe_sends_a_stored_secret_only_where_the_push_does(
        self, auth_client, user, pushes
    ):
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)
        credential_id = reference["credential_id"]
        assert vault.probe_auth(org, credential_id, user, URL)["value"] == SECRET

        _force_active_url(org, "docs", MOVED["host"])

        with pytest.raises(vault.MCPProbeURLMismatch):
            vault.probe_auth(org, credential_id, user, MOVED["host"])

    def test_entering_a_secret_binds_it_to_its_server_origin(
        self, auth_client, user, pushes, gateway_unreachable
    ):
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)
        credential_id = reference["credential_id"]
        assert _row(credential_id).origin == DOCS_ORIGIN

        # Re-entered with a URL at another origin: bound there.
        moved = _console(
            auth_client,
            {"url": MOVED["port"], "auth": {"type": "bearer", "token": SECRET_2}},
        )
        assert moved.status_code == 200, moved.json()
        assert _row(credential_id).origin == PORT_ORIGIN

        # Rotated while the active version sends it: bound to its server's
        # origin there, and pushed there.
        pushed_before = len(pushes)
        rotated = _rotate(auth_client, credential_id, SECRET)
        assert rotated.status_code == 200, rotated.json()
        assert _row(credential_id).origin == PORT_ORIGIN
        # pushes intercepts set_org_config before HTTP; prove that actual
        # boundary was reached once by the rotation.
        assert len(pushes) == pushed_before + 1
        assert _pushed_after(pushes, pushed_before) == [(MOVED["port"], "SECRET")]
        assert gateway_unreachable, "this path asks the gateway, which is not running"

    def test_create_binds_and_removal_revokes_before_rotation(
        self, auth_client, user, pushes
    ):
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp={"servers": {"docs": {"url": URL, "transport": "http"}}},
        )
        created = auth_client.post(
            CREDENTIALS,
            {"server_id": "docs", "kind": "bearer", "value": SECRET},
            format="json",
        )
        assert created.status_code == 201, created.json()
        credential_id = created.json()["result"]["id"]
        assert _row(credential_id).origin == DOCS_ORIGIN

        # W4 retires the grant when docs leaves the active config. Rotation
        # cannot revive it; reconnecting requires a new grant with its server.
        removed = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": None}}},
            format="json",
        )
        assert removed.status_code == 200, removed.json()
        pushed_before = len(pushes)
        rotated = _rotate(auth_client, credential_id, SECRET_2)
        assert rotated.status_code == 400, rotated.json()
        assert rotated.json()["code"] == "mcp_credential_not_active"
        row = _row(credential_id)
        assert (row.status, row.origin) == ("revoked", DOCS_ORIGIN)
        assert len(pushes) == pushed_before

    def test_migration_binds_only_the_credentials_the_active_version_sends(
        self, auth_client, user, pushes
    ):
        """0057 binds a credential that existed before the column to its
        server's origin when the active version sends it, and leaves any other
        unbound: sent nowhere until its secret is re-entered."""
        migration = importlib.import_module(
            "agentcc.migrations.0057_mcp_credential_origin"
        )
        org = user.organization
        docs = _saved_with_secret(auth_client, org, pushes)["credential_id"]
        wiki = auth_client.post(
            f"{GATEWAY}/update-mcp-server/",
            {
                "server_id": "wiki",
                "config": {
                    "url": MOVED["host"],
                    "transport": "http",
                    "auth": {"type": "bearer", "token": SECRET_2},
                },
            },
            format="json",
        )
        assert wiki.status_code == 200, wiki.json()
        wiki = _active(org).mcp["servers"]["wiki"]["credential"]["credential_id"]
        removed = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"wiki": None}}},
            format="json",
        )
        assert removed.status_code == 200, removed.json()
        AgentccMCPCredential.objects.filter(pk__in=[docs, wiki]).update(origin="")

        migration.bind_sent_credentials(apps, None)

        assert (_row(docs).origin, _row(wiki).origin) == (DOCS_ORIGIN, "")
        # Origin backfill must not revive the grant W4 revoked on removal.
        assert _row(wiki).status == "revoked"
        pushed_before = len(pushes)
        _reload(auth_client)
        assert _pushed_after(pushes, pushed_before) == [(URL, "SECRET")]

    def test_moving_a_legacy_secret_into_the_vault_binds_it(self, user, pushes):
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp={
                "servers": {
                    "docs": {
                        "url": URL,
                        "transport": "http",
                        "auth": {"type": "bearer", "token": SECRET},
                    }
                }
            },
        )

        call_command(
            "agentcc_mcp_migrate_secrets",
            "--apply",
            "--org",
            str(org.pk),
            stdout=StringIO(),
            stderr=StringIO(),
        )

        row = AgentccMCPCredential.objects.get(organization=org, server_id="docs")
        assert (row.status, row.origin) == ("active", DOCS_ORIGIN)
        assert _pushed_after(pushes, len(pushes) - 1) == [(URL, "SECRET")]


# IC-R1-12, the R1A review's P1: an activation is refused unless every server
# whose credential it would send is at the origin that credential is bound to.


def _moved_and_back(client, org, pushes):
    """The review's history: docs saved with SECRET (v2), moved by an editor
    to the collector host with a token of their own (v3, the credential
    rotated in place and bound there), and moved back by an admin with the
    real token SECRET_2 (v4, rotated in place again, bound to docs). Returns
    v1 (no servers) and v3."""
    reference = _saved_with_secret(client, org, pushes)
    first = AgentccOrgConfig.no_workspace_objects.get(organization=org, version=1)
    moved = _console(
        client,
        {"url": MOVED["host"], "auth": {"type": "bearer", "token": EDITOR_TOKEN}},
    )
    assert moved.status_code == 200, moved.json()
    older = _active(org)
    back = _console(client, {"url": URL, "auth": {"type": "bearer", "token": SECRET_2}})
    assert back.status_code == 200, back.json()
    credential = older.mcp["servers"]["docs"]["credential"]
    assert credential["credential_id"] == reference["credential_id"]
    return first, older


def _activation_refused(response, org, pushes, pushed_before, active_before):
    body = response.json()
    assert response.status_code == 400, (
        response.status_code,
        "pushed (url, secret) since the activation:",
        _pushed_after(pushes, pushed_before),
    )
    assert body["code"] == CODE, body
    assert "re-enter" in json.dumps(body), body
    assert "reconnect" in json.dumps(body), body
    assert "docs" in json.dumps(body), body
    for secret in (SECRET, SECRET_2, SECRET_3, EDITOR_TOKEN):
        assert secret not in json.dumps(body)
    assert _active(org).pk == active_before.pk
    assert len(pushes) == pushed_before


@pytest.mark.integration
@pytest.mark.api
class TestActivationFollowsTheCredentialOrigin:
    def test_activating_a_version_without_the_server_first(
        self, auth_client, user, pushes
    ):
        """Review probe A: v1 has no servers, so comparing with the active
        version found nothing to compare; the moved version is still refused."""
        org = user.organization
        first, older = _moved_and_back(auth_client, org, pushes)
        step = _activate(auth_client, first)
        assert step.status_code == 200, step.json()
        active, pushed_before = _active(org), len(pushes)

        response = _activate(auth_client, older)

        _activation_refused(response, org, pushes, pushed_before, active)

    def test_removing_the_server_with_a_save_first(self, auth_client, user, pushes):
        """Review probe B: update-config drops docs, then the moved version."""
        org = user.organization
        _first, older = _moved_and_back(auth_client, org, pushes)
        removed = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": None}}},
            format="json",
        )
        assert removed.status_code == 200, removed.json()
        active, pushed_before = _active(org), len(pushes)

        response = _activate(auth_client, older)

        _activation_refused(response, org, pushes, pushed_before, active)

    def test_removed_credential_cannot_be_rotated_or_activated(
        self, auth_client, user, pushes
    ):
        """Removing docs revokes its grant. Neither rotation nor activating
        the moved version revives it. Reconnect creates a new, bound grant;
        the real secret never reaches the editor's origin."""
        org = user.organization
        reference = _saved_with_secret(auth_client, org, pushes)
        moved = _console(
            auth_client,
            {"url": MOVED["host"], "auth": {"type": "bearer", "token": EDITOR_TOKEN}},
        )
        assert moved.status_code == 200, moved.json()
        older = _active(org)
        removed = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"mcp": {"servers": {"docs": None}}},
            format="json",
        )
        assert removed.status_code == 200, removed.json()
        active, pushed_before = _active(org), len(pushes)
        rotated = _rotate(auth_client, reference["credential_id"], SECRET_3)
        assert rotated.status_code == 400, rotated.json()
        assert rotated.json()["code"] == "mcp_credential_not_active"
        assert _row(reference["credential_id"]).status == "revoked"
        assert len(pushes) == pushed_before

        response = _activate(auth_client, older)

        _activation_refused(response, org, pushes, pushed_before, active)

        # Re-entered with its server, it is bound and sent there again.
        again = _console(
            auth_client,
            {
                "url": URL,
                "transport": "http",
                "auth": {"type": "bearer", "token": SECRET_3},
            },
        )
        assert again.status_code == 200, again.json()
        assert _pushed_after(pushes, len(pushes) - 1) == [(URL, "SECRET_3")]
        new_id = _active(org).mcp["servers"]["docs"]["credential"]["credential_id"]
        assert new_id != reference["credential_id"]
        assert (_row(new_id).status, _row(new_id).origin) == ("active", DOCS_ORIGIN)
        assert _row(reference["credential_id"]).status == "revoked"

    def test_the_version_a_secret_was_entered_for_activates_and_no_other(
        self, auth_client, user, pushes
    ):
        """A legitimate move: docs is re-entered at port 8443 (bound there),
        then its path changes. The version it was entered for activates and
        is sent the secret; the version at the old origin is refused."""
        org = user.organization
        _saved_with_secret(auth_client, org, pushes)
        first_docs = _active(org)
        moved = _console(
            auth_client,
            {"url": MOVED["port"], "auth": {"type": "bearer", "token": SECRET_2}},
        )
        assert moved.status_code == 200, moved.json()
        entered = _active(org)
        path = _console(auth_client, {"url": "https://docs.example.com:8443/v2"})
        assert path.status_code == 200, path.json()
        pushed_before = len(pushes)

        response = _activate(auth_client, entered)

        assert response.status_code == 200, response.json()
        assert _pushed_after(pushes, pushed_before) == [(MOVED["port"], "SECRET_2")]
        active, pushed_before = _active(org), len(pushes)
        _activation_refused(
            _activate(auth_client, first_docs), org, pushes, pushed_before, active
        )

    def test_managed_and_unauthenticated_servers_are_not_refused(
        self, auth_client, user, pushes
    ):
        """No origin is bound for a fagi.* credential (the gateway's catalog
        holds its URL) or a server with no credential: an older version with
        both activates."""
        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp={
                "servers": {"docs": {"url": MOVED["host"], "transport": "http"}},
                "managed_tools": {"enabled": True},
            },
        )
        managed = vault.create(
            org,
            "fagi.web",
            kind="api_key",
            value=SECRET_2,
            header_name="x-api-key",
            actor=user,
        )
        older = _active(org)
        assert _row(managed.credential.pk).origin == ""
        moved = _console(auth_client, {"url": URL, "transport": "http"})
        assert moved.status_code == 200, moved.json()
        pushed_before = len(pushes)

        response = _activate(auth_client, older)

        assert response.status_code == 200, response.json()
        payload = pushes[pushed_before][1]["mcp"]
        assert payload["managed_tools"]["credentials"]["fagi.web"]["secret"] == {
            "type": "api_key",
            "header": "x-api-key",
            "value": SECRET_2,
        }
