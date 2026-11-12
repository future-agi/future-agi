"""R4 complete-mediation REDs. Synthetic custody; real rows and authorities."""
import json
import os
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from agentcc.services import microsoft_client_identity as identity
from agentcc.tests import test_microsoft_connect_gate as gate
from agentcc.tests.test_microsoft_identity_exclusion import FILE_ID, legacy_registration

pytestmark = pytest.mark.django_db
rig = gate.rig


@pytest.fixture(params=[False, True], ids=["gate-off", "gate-on"])
def identities(rig, settings, monkeypatch, tmp_path, request):
    settings.MICROSOFT_CONNECT_ENABLED = request.param
    path = tmp_path / "chokepoint-fixture.json"
    path.write_text(json.dumps({"microsoft": {
        "client_secret": gate.SECRET, "client_id": FILE_ID,
    }}))
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENTS_FILE", str(path))
    return path


def url_server(config):
    entry = {"url": gate.RUNTIME + "/microsoft-outlook/mcp",
             "credential": {"mode": "per_user"}}
    config.mcp["servers"]["alias"] = entry
    config.save(update_fields=["mcp"])
    return entry


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
def test_unlabelled_registration_resolution_and_connect_refuse_before_custody(
    rig, identities, monkeypatch, client_id,
):
    org, user, config, fake = rig
    entry = url_server(config)
    with gate.no_effects(monkeypatch, fake, vault_entry=False):
        with pytest.raises(gate.oauth.ConnectRefused) as caught:
            gate.vault.create_oauth_client(org, server_id="alias", client_id=client_id,
                client_secret=gate.vault.SealedSecret(gate.SECRET), actor=user)
        assert caught.value.code == gate.clients.OrgClientRequired.code
    row = legacy_registration(org, user, client_id, "alias")
    with gate.no_effects(monkeypatch, fake):
        for call in (
            lambda: gate.clients.server_client(org, "alias"),
            lambda: gate.selection.resolve(org, "alias", entry),
            lambda: gate.selection.choose(org, "alias", entry, cimd=True, dcr=True,
                                          auth_methods=None),
            lambda: gate.selection.issuing(org, "alias", entry, client_id),
        ):
            with pytest.raises(gate.clients.OrgClientRequired):
                call()
        with pytest.raises(gate.oauth.ConnectRefused) as caught:
            gate.oauth.start(org, user, "alias", individual_authenticated=True)
        assert caught.value.code == gate.clients.OrgClientRequired.code
    with gate.no_effects(monkeypatch, fake, vault_entry=False):
        with pytest.raises(gate.oauth.ConnectRefused):
            gate.vault.rotate_oauth_client(row, gate.vault.SealedSecret(gate.SECRET), user)


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
@pytest.mark.parametrize("refresh", [False, True])
def test_same_origin_catalog_removal_cannot_deliver_refresh_or_revoke(
    rig, identities, monkeypatch, client_id, refresh,
):
    org, user, config, fake = rig
    # Persist issuance with the original catalog reference, then erase only
    # routing metadata. Neither token origin nor issuing identity changes.
    row = gate.token_row(org, user, server="alias")
    row.oauth_client_id = client_id
    row.token_expires_at = timezone.now() + timedelta(hours=1 if not refresh else 0)
    row.save(update_fields=["oauth_client_id", "token_expires_at"])
    legacy_registration(org, user, client_id, "alias")
    entry = url_server(config)
    with gate.no_effects(monkeypatch, fake):
        answer = gate.tokens.upstream_token(str(org.pk), str(user.pk), "alias", refresh=refresh)
        assert answer.outcome == "needs_reauth"
        assert "access_token" not in answer.body()
        assert gate.tokens._refresh(row, entry["url"]) is None
        result = gate.mcp_revocation.revoke(org, "alias", row.oauth_issuer,
            client_id, row.encrypted_refresh, row.encrypted_secret)
        assert result.outcome == "pending"
    row.refresh_from_db()
    assert row.status == "active" and row.version == 1 and row.last_used_at is None


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
def test_raw_custody_boundaries_deny_even_without_labels(rig, identities, monkeypatch, client_id):
    org, user, _, _ = rig
    row = legacy_registration(org, user, client_id, "alias")
    client_decrypt = Mock(wraps=gate.clients.decrypt_token)
    vault_decrypt = Mock(wraps=gate.vault.decrypt_token)
    monkeypatch.setattr(gate.clients, "decrypt_token", client_decrypt)
    monkeypatch.setattr(gate.vault, "decrypt_token", vault_decrypt)
    with pytest.raises(gate.clients.OrgClientRequired):
        gate.clients._org_secret(row)
    assert gate.vault._decrypted(row) is None
    client_decrypt.assert_not_called()
    vault_decrypt.assert_not_called()


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
def test_exact_access_token_row_is_checked_at_materialization(
    rig, identities, monkeypatch, client_id,
):
    org, user, config, fake = rig
    url_server(config)
    row = gate.token_row(org, user, server="alias")
    row.oauth_client_id = client_id
    row.save(update_fields=["oauth_client_id"])
    # A caller preflight is insufficient: the row may change between the
    # metadata query and the locked read. Exercise the actual decrypt boundary.
    with gate.no_effects(monkeypatch, fake):
        answer = gate.tokens._served(row, "ok", timezone.now() + timedelta(hours=1))
        assert answer.outcome == "needs_reauth"
        assert "access_token" not in answer.body()


@pytest.mark.parametrize("origin,issuer", [
    ("https://workiq.svc.cloud.microsoft", "https://issuer.example"),
    (gate.RUNTIME, "https://login.microsoftonline.com/organizations/v2.0"),
    ("https://tenant.sharepoint.com", "https://issuer.example"),
])
def test_ambiguous_legacy_microsoft_grant_fails_closed(
    rig, identities, monkeypatch, settings, origin, issuer,
):
    org, user, config, fake = rig
    url_server(config)
    row = gate.token_row(org, user, server="alias")
    row.oauth_client_id, row.origin, row.oauth_issuer = "", origin, issuer
    row.save(update_fields=["oauth_client_id", "origin", "oauth_issuer"])
    # A stored Microsoft issuer holds the org's own server while Microsoft
    # connections are off; either refusal serves nothing.
    held = not settings.MICROSOFT_CONNECT_ENABLED and identity.microsoft_origin(issuer)
    with gate.no_effects(monkeypatch, fake):
        answer = gate.tokens.upstream_token(str(org.pk), str(user.pk), "alias")
        assert answer.outcome == (gate.CODE if held else "needs_reauth")
        assert gate.tokens._refresh(row, origin + "/mcp") is None
        result = gate.mcp_revocation.revoke(org, "alias", issuer, "",
            row.encrypted_refresh, row.encrypted_secret)
        assert result.outcome == "pending"


@pytest.mark.parametrize("source", ["env", "file"])
@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
def test_platform_loader_cannot_materialize_excluded_id_under_another_group(
    rig, identities, monkeypatch, source, client_id,
):
    # 'google' is deliberately unrelated routing metadata, not an exemption.
    if source == "env":
        monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", client_id)
        monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", gate.SECRET)
    else:
        monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", raising=False)
        monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", raising=False)
        identities.write_text(json.dumps({
            "google": {"client_secret": gate.SECRET, "client_id": client_id},
            "microsoft": {"client_secret": gate.SECRET, "client_id": FILE_ID},
        }))
    env_read = Mock(wraps=os.environ.get)
    sealed = Mock(side_effect=AssertionError("excluded secret materialized"))
    monkeypatch.setattr(os.environ, "get", env_read)
    # Observe construction without replacing the holder's type (isinstance).
    monkeypatch.setattr(gate.clients._Sealed, "__init__", sealed)
    with pytest.raises(gate.clients.OrgClientRequired):
        gate.clients._platform_client("google")
    assert all(c.args[0] not in {"AGENTCC_CONNECTOR_CLIENT_MICROSOFT_SECRET",
                               "AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET"}
               for c in env_read.call_args_list)
    assert sealed.call_count == 0


@pytest.mark.parametrize("group", ["microsoft", "google"])
def test_distinct_org_client_still_resolves(rig, identities, settings, group):
    org, user, _, _ = rig
    if group == "microsoft":
        settings.MICROSOFT_CONNECT_ENABLED = True
    gate.registration(org, user, group=group)
    ref = gate.clients.resolve(org, "microsoft-outlook" if group == "microsoft" else "gmail",
        group, token_url=gate.ISSUER + "/token", revocation_url=None)
    assert ref.source == "org" and ref.client_id == "fixture-org-client"
    assert ref.token_exchange_secret() == gate.SECRET


def test_identity_chokepoint_is_label_and_gate_independent(rig, identities):
    for client_id in (gate.PLATFORM_ID, FILE_ID):
        with pytest.raises(gate.clients.OrgClientRequired):
            identity.deny_if_excluded(client_id)
    identity.deny_if_excluded("fixture-distinct-org-client")
