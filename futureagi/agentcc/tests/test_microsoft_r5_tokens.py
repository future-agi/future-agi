"""R5 authoritative selection REDs, plus the C1/C2 all-provider matrix."""
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.db import connection
from django.utils import timezone

from agentcc.services import microsoft_client_identity as identity
from agentcc.tests import test_microsoft_connect_gate as gate
from agentcc.tests.test_microsoft_identity_exclusion import FILE_ID
from agentcc.tests.test_microsoft_secret_chokepoint import identities, url_server

pytestmark = pytest.mark.django_db
rig = gate.rig
PROVIDERS = [
    ("microsoft-excel", "futureagi-rest"),
    ("microsoft-onedrive", "futureagi-rest"),
    ("microsoft-outlook", "futureagi-rest"),
    ("microsoft-outlook", "workiq"),
    ("microsoft-sharepoint", "futureagi-rest"),
    ("microsoft-teams", "futureagi-rest"),
    ("microsoft-teams", "workiq"),
]


def shared_accounts(rig, client_id):
    org, user, config, _ = rig
    entry = url_server(config)
    row = gate.token_row(org, user, server="alias", shared=True)
    row.oauth_client_id = client_id
    row.token_expires_at = timezone.now() + timedelta(hours=1)
    row.save(update_fields=["oauth_client_id", "token_expires_at"])
    entry["credential"] = {"mode": "shared", "kind": "oauth",
                           "state": "active", "credential_id": str(row.pk)}
    config.save(update_fields=["mcp"])
    other = gate.Credential.objects.create(
        organization=org, server_id="alias", scope="shared", kind="oauth",
        account_label="secondary", oauth_client_id="fixture-permitted-secondary",
        encrypted_secret=gate.credential_manager.encrypt_token("fixture-secondary"),
        origin=gate.RUNTIME, oauth_issuer=gate.ISSUER,
        token_expires_at=row.token_expires_at,
    )
    return row, other


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
def test_authoritative_default_shared_row_refused_before_served(
    rig, identities, monkeypatch, client_id,
):
    org, _, _, fake = rig
    row, other = shared_accounts(rig, client_id)
    assert gate.Credential.objects.filter(organization=org, server_id="alias").first().pk == other.pk
    served = Mock(wraps=gate.tokens._served)
    monkeypatch.setattr(gate.tokens, "_served", served)
    with gate.no_effects(monkeypatch, fake):
        answer = gate.tokens.shared_token(str(org.pk), "alias")
        assert answer.outcome == "needs_reauth"
        assert "access_token" not in answer.body()
    served.assert_not_called()
    row.refresh_from_db()
    other.refresh_from_db()
    assert row.last_used_at is None and other.last_used_at is None
    assert row.status == "active" and row.version == 1


@pytest.mark.parametrize("shared", [False, True])
def test_identity_check_and_serving_share_authoritative_lock(
    rig, identities, monkeypatch, shared,
):
    org, user, config, _ = rig
    if shared:
        row, _ = shared_accounts(rig, "fixture-permitted-default")
    else:
        url_server(config)
        row = gate.token_row(org, user, server="alias")
        row.oauth_client_id = "fixture-permitted-default"
        row.token_expires_at = timezone.now() + timedelta(hours=1)
        row.save(update_fields=["oauth_client_id", "token_expires_at"])
    statements, checks = [], []
    original = identity.deny_if_excluded
    served_original = gate.tokens._served

    def sql(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    def check(client_id, **kwargs):
        if client_id == row.oauth_client_id:
            assert connection.in_atomic_block
            assert any('agentcc_mcp_credential' in s and 'FOR UPDATE' in s for s in statements)
            checks.append(client_id)
        return original(client_id, **kwargs)

    def serve(selected, *args, **kwargs):
        assert connection.in_atomic_block
        assert selected.pk == row.pk
        assert checks
        return served_original(selected, *args, **kwargs)

    monkeypatch.setattr(identity, "deny_if_excluded", check)
    monkeypatch.setattr(gate.tokens, "_served", serve)
    with connection.execute_wrapper(sql):
        answer = (gate.tokens.shared_token(str(org.pk), "alias") if shared else
                  gate.tokens.upstream_token(str(org.pk), str(user.pk), "alias"))
    assert answer.outcome == "ok" and answer.access is not None


@pytest.mark.parametrize("app,provider", PROVIDERS)
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, "fixture-org-client"])
def test_all_seven_providers_off_connect_use_refresh_revoke(
    rig, monkeypatch, app, provider, shared, client_id,
):
    org, user, config, fake = rig
    entry = gate.k2_entry(app, catalog_ref=f"{app}/{provider}@1.0.0",
        credential={"mode": "shared", "kind": "oauth"} if shared else {"mode": "per_user"})
    config.mcp["servers"][app] = entry
    row = gate.token_row(org, user, server=app, shared=shared)
    row.oauth_client_id = client_id
    row.save(update_fields=["oauth_client_id"])
    if shared:
        entry["credential"].update(credential_id=str(row.pk), state="active")
    config.save(update_fields=["mcp"])
    with gate.no_effects(monkeypatch, fake):
        gate.refused(lambda: gate.oauth.start(org, user, app,
            mode="shared" if shared else "user", individual_authenticated=True))
        answer = (gate.tokens.shared_token(str(org.pk), app) if shared else
                  gate.tokens.upstream_token(str(org.pk), str(user.pk), app))
        assert answer.outcome == gate.CODE
        assert "access_token" not in answer.body()
        assert gate.tokens._refresh(row, entry["url"]) is None
        result = gate.mcp_revocation.revoke(org, app, row.oauth_issuer,
            row.oauth_client_id, row.encrypted_refresh, row.encrypted_secret)
        assert result.outcome != "confirmed"
    row.refresh_from_db()
    assert row.status == "active" and row.version == 1 and row.last_used_at is None
