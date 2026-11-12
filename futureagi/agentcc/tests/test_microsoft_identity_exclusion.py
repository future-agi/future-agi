"""Identity, not row source, excludes the held Microsoft platform app."""
import json
import os
from unittest.mock import Mock

import pytest

from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.tests import test_microsoft_connect_gate as gate

pytestmark = pytest.mark.django_db
rig = gate.rig
FILE_ID = "fixture-file-platform-id-not-real"


@pytest.fixture
def identities(rig, monkeypatch, tmp_path, settings):
    settings.MICROSOFT_CONNECT_ENABLED = True
    path = tmp_path / "identity-fixture.json"
    path.write_text(json.dumps({"microsoft": {
        "client_id": FILE_ID, "client_secret": gate.SECRET,
    }}))
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENTS_FILE", str(path))
    return path


def legacy_registration(org, user, client_id, server_id=None):
    """A persisted pre-policy row, not a bypass of the registration API."""
    return gate.Credential.objects.create(
        organization=org, created_by=user,
        server_id=gate.clients.org_client_key(
            **({"server_id": server_id} if server_id else {"client_group": "microsoft"})
        ),
        scope="shared", kind="oauth_client", oauth_client_id=client_id,
        encrypted_secret=gate.credential_manager.encrypt_token(gate.SECRET),
    )


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
@pytest.mark.parametrize("server_id", [None, "alias"])
def test_same_platform_id_cannot_be_registered_or_rotated(
    rig, identities, monkeypatch, client_id, server_id,
):
    org, user, _, fake = rig
    key = {"server_id": server_id} if server_id else {"client_group": "microsoft"}
    browser = gate.bearer(user)
    with gate.no_effects(monkeypatch, fake, vault_entry=False):
        with pytest.raises(gate.oauth.ConnectRefused) as caught:
            gate.vault.create_oauth_client(org, **key, client_id=client_id,
                client_secret=gate.vault.SealedSecret(gate.SECRET), actor=user)
        assert caught.value.code == gate.clients.OrgClientRequired.code
        assert str(caught.value) == microsoft.ORG_CLIENT_MESSAGE
        response = browser.post("/agentcc/connectors/oauth-clients/", {
            **key, "client_id": client_id, "client_secret": gate.SECRET,
        }, format="json")
        assert response.status_code == 409
        assert response.json()["code"] == gate.clients.OrgClientRequired.code
    row = legacy_registration(org, user, client_id, server_id)
    with gate.no_effects(monkeypatch, fake, vault_entry=False):
        with pytest.raises(gate.oauth.ConnectRefused) as caught:
            gate.vault.rotate_oauth_client(row, gate.vault.SealedSecret(gate.SECRET), user)
        assert caught.value.code == gate.clients.OrgClientRequired.code
        response = browser.post(f"/agentcc/connectors/oauth-clients/{row.pk}/rotate/",
            {"client_secret": gate.SECRET}, format="json")
        assert response.status_code == 409
    row.refresh_from_db()
    assert row.version == 1 and row.status == "active"


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
@pytest.mark.parametrize("server_id", [None, "alias"])
def test_legacy_same_id_cannot_resolve_choose_or_issue(
    rig, identities, monkeypatch, client_id, server_id,
):
    org, user, config, fake = rig
    legacy_registration(org, user, client_id, server_id)
    server = server_id or gate.OUTLOOK
    entry = config.mcp["servers"][server]
    env_read = Mock(wraps=os.environ.get)
    file_secrets = Mock(wraps=gate.clients._parse_clients_file)
    monkeypatch.setattr(os.environ, "get", env_read)
    monkeypatch.setattr(gate.clients, "_parse_clients_file", file_secrets)
    calls = [
        lambda: gate.selection.resolve(org, server, entry),
        lambda: gate.selection.choose(org, server, entry, cimd=True, dcr=True, auth_methods=None),
        lambda: gate.selection.issuing(org, server, entry, client_id),
    ]
    if server_id:
        calls.append(lambda: gate.clients.server_client(org, server_id))
    with gate.no_effects(monkeypatch, fake):
        for call in calls:
            with pytest.raises(gate.clients.OrgClientRequired) as caught:
                call()
            assert str(caught.value) == microsoft.ORG_CLIENT_MESSAGE
    file_secrets.assert_not_called()
    assert all(call.args[0] != "AGENTCC_CONNECTOR_CLIENT_MICROSOFT_SECRET"
               for call in env_read.call_args_list)


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
@pytest.mark.parametrize("shared", [False, True])
def test_platform_token_with_same_id_org_row_reads_no_secret(
    rig, identities, monkeypatch, client_id, shared,
):
    org, user, _, fake = rig
    legacy_registration(org, user, client_id)
    row = gate.token_row(org, user, shared=shared)
    row.oauth_client_id = client_id
    row.save(update_fields=["oauth_client_id"])
    with gate.no_effects(monkeypatch, fake):
        answer = (gate.tokens.shared_token(str(org.pk), gate.OUTLOOK) if shared else
                  gate.tokens.upstream_token(str(org.pk), str(user.pk), gate.OUTLOOK))
        assert answer.outcome == "needs_reauth"
        assert gate.tokens._refresh(row, gate.RUNTIME + "/microsoft-outlook/mcp") is None
        result = gate.mcp_revocation.revoke(org, gate.OUTLOOK, gate.ISSUER,
            client_id, row.encrypted_refresh, row.encrypted_secret)
        assert result.outcome != "confirmed"
    assert not fake.client_auths
    row.refresh_from_db()
    assert row.status == "active" and row.version == 1


def test_unknown_remote_metadata_is_not_fetched_for_excluded_identity(
    rig, identities, monkeypatch,
):
    org, user, _, fake = rig
    legacy_registration(org, user, FILE_ID, "microsoft-remote")
    with gate.no_effects(monkeypatch, fake):
        result = gate.mcp_revocation.revoke(org, "microsoft-remote", gate.ISSUER,
            FILE_ID, "unused-fixture-refresh", "unused-fixture-access")
        assert result.outcome != "confirmed"


@pytest.mark.parametrize("document", [
    '{"microsoft":{"client_id":"one","client_id":"two","client_secret":"unused"}}',
    '{"microsoft":{"client_id":"one","client_secret":"unused"},"microsoft":{}}',
    '{"microsoft":{"client_id":null,"client_secret":"unused"}}',
    '{"microsoft":{"client_id":"one","client_secret":"unterminated}',
])
def test_unreadable_platform_identity_fails_closed_before_org_secret(
    rig, identities, monkeypatch, document,
):
    org, user, config, fake = rig
    # Create a valid org app first; corrupt only the synthetic metadata source.
    gate.registration(org, user)
    identities.write_text(document)
    with gate.no_effects(monkeypatch, fake):
        with pytest.raises(gate.clients.OrgClientRequired):
            gate.selection.resolve(org, gate.OUTLOOK, config.mcp["servers"][gate.OUTLOOK])


def test_file_identity_escape_and_secret_skip_do_not_hide_the_id(
    rig, identities, monkeypatch,
):
    org, user, config, fake = rig
    identities.write_text(
        '{"micro\\u0073oft":{"client_secret":"escaped \\\" client_id not an ID",'
        '"client_id":"fixture-file-platform-id-not-real"}}'
    )
    legacy_registration(org, user, FILE_ID)
    with gate.no_effects(monkeypatch, fake):
        with pytest.raises(gate.clients.OrgClientRequired):
            gate.selection.resolve(org, gate.OUTLOOK, config.mcp["servers"][gate.OUTLOOK])


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
def test_manual_mcp_registration_refuses_identity_before_config_or_discovery(
    rig, identities, monkeypatch, tmp_path, client_id,
):
    from agentcc.tests import test_connector_catalog as fixtures

    org, user, _, fake = rig
    app_id = "microsoft-fixture-mcp"
    data = fixtures._bundle()
    data["apps"] = [fixtures._app(app_id, "Microsoft Fixture", "Synthetic app.",
        category="productivity", categories=["docs"], default="vendor-mcp")]
    data["providers"] = [fixtures._provider(app_id, "vendor-mcp",
        auth={"kind": "mcp_oauth"}, account_modes=["user", "agent"],
        transport={"kind": "http", "url": "https://mcp.vendor.example/mcp"},
        tools=[fixtures._tool("search", posture="auto")])]
    path = tmp_path / "manual-identity-catalog.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(gate.catalog, "BUNDLE_PATH", path)
    writer = Mock(wraps=gate.apps.write_config_version_with)
    discovery = Mock(wraps=gate.oauth.discover)
    monkeypatch.setattr(gate.apps, "write_config_version_with", writer)
    monkeypatch.setattr(gate.oauth, "discover", discovery)
    with gate.no_effects(monkeypatch, fake):
        with pytest.raises(gate.oauth.ConnectRefused) as caught:
            gate.apps.configure(org, app_id, {
                "provider": "vendor-mcp", "mode": "per_user", "registration": "manual",
                "discovery_url": "https://mcp.vendor.example/mcp", "client_id": client_id,
                "client_secret": gate.vault.SealedSecret(gate.SECRET),
            }, user)
        assert caught.value.code == gate.clients.OrgClientRequired.code
        assert str(caught.value) == microsoft.ORG_CLIENT_MESSAGE
    writer.assert_not_called()
    discovery.assert_not_called()


def test_distinct_org_id_still_connects_with_both_excluded_ids_configured(rig, identities):
    org, user, _, fake = rig
    gate.registration(org, user)
    started = gate.oauth.start(org, user, gate.OUTLOOK, individual_authenticated=True)
    code, state = fake.authorize(started.authorize_url)
    result = gate.oauth.complete({"code": code, "state": state, "iss": gate.ISSUER},
        {started.cookie_name: started.cookie_value})
    assert result.ok
    assert fake.client_auths and all(item[-1] for item in fake.client_auths)
