"""Microsoft gate/exclusion regressions. Real org/member/config/vault rows;
only outbound transport is synthetic.
No authority, membership, contract or release gate is replaced.
"""
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone

from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState as State
from agentcc.models.mcp_vendor import AgentccMCPVendorBinding as Binding
from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as VendorConnection
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_catalog_oauth as cards
from agentcc.services import connector_k2 as k2
from agentcc.services import connector_mcp_oauth_app as apps
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import mcp_connect_clients as selection
from agentcc.services import mcp_connections, mcp_credentials as vault
from agentcc.services import mcp_revocation, mcp_upstream_oauth as oauth
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.services import mcp_vendor_connections as vendors
from agentcc.services import mcp_vendor_credentials as grants
from agentcc.services import credential_manager, gateway_client
from agentcc.tests.connector_k2_helpers import (
    PILOTS,
    REAL_BUNDLE,
    RUNTIME,
    k2_entry,
    serve_at_gateway,
)
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN, CALLBACK, INTERNAL_SECRET, ISSUER, FakeVendor, seed_config,
)
from agentcc.tests.test_mcp_personal_boundary import bearer

pytestmark = pytest.mark.django_db
CODE = "microsoft_coming_soon"
MESSAGE = "Microsoft connections are coming soon."
OUTLOOK = "microsoft-outlook"
PLATFORM_ID = "fixture-platform-client-not-real"
SECRET = "fixture-client-secret-not-real"


@pytest.fixture
def rig(settings, monkeypatch, tmp_path, organization, user):
    for name in ("CATALOG", "CREDENTIAL_VAULT", "VIRTUAL_SERVERS", "UPSTREAM_OAUTH", "K2_APPS", "VENDORS"):
        setattr(settings, f"AGENTCC_MCP_{name}_ENABLED", True)
    settings.MICROSOFT_CONNECT_ENABLED = False
    settings.AGENTCC_CONNECTORS_RUNTIME_URL = RUNTIME
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_VENDOR_SUBJECT_KEY = "fixture-subject-key-long-enough-not-real"
    monkeypatch.setenv(oauth.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)
    bundle = json.loads(REAL_BUNDLE.read_text())
    for provider in bundle["providers"]:
        spec = provider.get("auth", {}).get("oauth2")
        if spec and spec.get("clientGroup") in ("microsoft", "google"):
            spec.update(authorizeUrl=ISSUER + "/authorize", tokenUrl=ISSUER + "/token")
            if spec.get("revocation"):
                spec["revocation"]["url"] = ISSUER + "/revoke"
            elif spec.get("clientGroup") == "microsoft":
                # A synthetic revocation endpoint makes the exclusion RED non-vacuous.
                spec["revocation"] = {"url": ISSUER + "/revoke", "method": "POST", "tokenBody": "form", "tokenField": "token", "clientAuth": "body"}
        if provider.get("id") == "futureagi-rest" and provider.get("app") in PILOTS:
            # The gate is under test, not the gateway's K2 admission.
            serve_at_gateway(provider)
    path = tmp_path / "catalog-gate.json"
    path.write_text(json.dumps(bundle))
    monkeypatch.setattr(catalog, "BUNDLE_PATH", path)
    platform = tmp_path / "platform-fixture.json"
    platform.write_text(json.dumps({"microsoft": {"client_id": PLATFORM_ID, "client_secret": SECRET}}))
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_MICROSOFT_ID", PLATFORM_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_MICROSOFT_SECRET", SECRET)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENTS_FILE", str(platform))
    entries = {OUTLOOK: k2_entry(OUTLOOK), "gmail": k2_entry("gmail")}
    entries["alias"] = k2_entry(OUTLOOK)
    # Added by URL: held by its Microsoft host, never by the id the org chose.
    entries["microsoft-remote"] = {"url": "https://mcp.fixture.microsoft.com/mcp", "credential": {"mode": "per_user"}}
    entries["partner"] = {"url": "https://api.pipedream.com/mcp", "catalog_ref": entries[OUTLOOK]["catalog_ref"], "credential": {"mode": "vendor_end_user", "vendor": "pipedream"}}
    config = seed_config(organization, entries)
    config.mcp["enabled"] = True
    config.save(update_fields=["mcp"])
    fake = FakeVendor(confidential={"fixture-org-client": SECRET, PLATFORM_ID: SECRET})
    monkeypatch.setattr(oauth.mcp_safe_fetch, "fetch", fake.fetch)
    monkeypatch.setattr(oauth.gateway_credentials, "purge", Mock(return_value=True))
    return organization, user, config, fake


def registration(org, user, group="microsoft", server_id=None):
    with override_settings(MICROSOFT_CONNECT_ENABLED=True):
        return vault.create_oauth_client(org, client_group=None if server_id else group,
            server_id=server_id, client_id="fixture-org-client",
            client_secret=vault.SealedSecret(SECRET), actor=user)


@contextmanager
def no_effects(monkeypatch, fake, *, vault_entry=True):
    """Observe actual SQL and secret seams, not replacement authority functions."""
    import re
    import sys

    def session_metadata_write(statement):
        # No blanket table exemption. Match only ORM placeholders,
        # the exact metadata columns and the originating auth/fixture frame.
        touch = re.fullmatch(
            r'UPDATE "accounts_auth_token" SET "last_used_at" = %s WHERE [^;]+',
            statement,
        )
        insert = re.fullmatch(
            r'INSERT INTO "accounts_auth_token" \(("[a-z_]+"(?:, "[a-z_]+")*)\) '
            r'VALUES \((%s(?:, %s)*)\)(?: RETURNING "accounts_auth_token"\."id")?',
            statement,
        )
        fixture_columns = {
            "created_at", "updated_at", "deleted", "deleted_at", "id", "user_id",
            "last_used_at", "auth_type", "is_active", "auth_origin",
            "scoped_organization_id", "origin_idp_id", "origin_idp_generation",
        }
        fixture_insert = False
        if insert:
            columns = re.findall(r'"([a-z_]+)"', insert.group(1))
            fixture_insert = (set(columns) == fixture_columns
                and len(columns) == len(fixture_columns)
                and len(insert.group(2).split(", ")) == len(columns))
        if not (touch or fixture_insert):
            return False
        frame = sys._getframe(1)
        try:
            while frame is not None:
                if fixture_insert and frame.f_code is bearer.__code__:
                    return True
                if (touch and frame.f_globals.get("__name__") == "accounts.authentication"
                        and frame.f_code.co_name == "_touch_non_saml_access_token"):
                    return True
                frame = frame.f_back
            return False
        finally:
            del frame

    spies, writes = [], []
    seams = [(clients, n) for n in ("_org_secret", "_env_client", "_clients_file", "decrypt_token")]
    seams += [(m, n) for m in (oauth, tokens, vendors, grants, credential_manager, mcp_revocation)
              for n in ("encrypt_token", "decrypt_token") if hasattr(m, n)]
    seams += [(vault, "_sealed_ciphertext")]
    if vault_entry:
        seams += [(vault, "create_oauth_client"), (vault, "rotate_oauth_client")]
    with monkeypatch.context() as patcher:
        for module, name in seams:
            spy = Mock(wraps=getattr(module, name))
            patcher.setattr(module, name, spy)
            spies.append(spy)
        def sql(execute, sql, params, many, context):
            statement = sql.strip()
            verb = statement.split(None, 1)[0].upper() if statement else ""
            if verb in {"INSERT", "UPDATE", "DELETE"}:
                if many or not session_metadata_write(statement):
                    writes.append(verb)
            return execute(sql, params, many, context)
        before = len(fake.requests)
        with connection.execute_wrapper(sql):
            yield
        assert writes == []
        assert len(fake.requests) == before
        assert all(spy.call_count == 0 for spy in spies)


def refused(call):
    with pytest.raises(Exception) as caught:
        call()
    assert getattr(caught.value, "code", None) == CODE
    assert str(caught.value) == MESSAGE
    assert getattr(caught.value, "status", 409) == 409


@pytest.mark.parametrize("path", ["k2", "registration", "start", "remote", "shared", "resolve", "server-client", "client-source", "choose", "issuing", "catalog-add", "configure", "discovery"])
def test_off_refuses_before_effects(rig, monkeypatch, path):
    org, user, config, fake = rig
    registration(org, user)
    entry = config.mcp["servers"][OUTLOOK]
    plan = catalog.load_bundle().apps[OUTLOOK].plans["futureagi-rest"]
    data = {"provider": "futureagi-rest", "mode": "per_user", "registration": "manual",
            "discovery_url": entry["url"], "client_id": "fixture-org-client", "client_secret": vault.SealedSecret(SECRET)}
    calls = {
        "k2": lambda: k2.sign_in(org, OUTLOOK, entry),
        "registration": lambda: k2.registration(org, OUTLOOK, entry),
        "start": lambda: oauth.start(org, user, OUTLOOK, individual_authenticated=True),
        "shared": lambda: oauth.start(org, user, OUTLOOK, mode="shared", individual_authenticated=True),
        "resolve": lambda: selection.resolve(org, OUTLOOK, entry),
        "remote": lambda: oauth.start(org, user, "microsoft-remote", individual_authenticated=True),
        "server-client": lambda: clients.server_client(org, "alias"),
        "client-source": lambda: clients.client_source(None, registration=["preregistered"], client_group="microsoft", token_url=ISSUER + "/token", revocation_url=None),
        "choose": lambda: selection.choose(org, OUTLOOK, entry, cimd=True, dcr=True, auth_methods=None),
        "issuing": lambda: selection.issuing(org, OUTLOOK, entry, PLATFORM_ID),
        "catalog-add": lambda: cards.add(org, OUTLOOK, catalog.load_bundle().apps[OUTLOOK], plan, mode="user", values={}, actor=user, credential=None),
        "configure": lambda: apps.configure(org, OUTLOOK, data, user),
        "discovery": lambda: apps.test_discovery(OUTLOOK, data),
    }
    with no_effects(monkeypatch, fake):
        refused(calls[path])


@pytest.mark.parametrize("key", [{"client_group": "microsoft"}, {"server_id": "alias"}])
def test_create_rotate_routes_and_vault_are_gated(rig, monkeypatch, key):
    org, user, _, fake = rig
    row = registration(org, user, server_id=key.get("server_id"))
    client = bearer(user)
    with no_effects(monkeypatch, fake):
        for path, data in [("/agentcc/connectors/oauth-clients/", {**key, "client_id": "new-fixture", "client_secret": SECRET}),
                           (f"/agentcc/connectors/oauth-clients/{row.pk}/rotate/", {"client_secret": SECRET})]:
            response = client.post(path, data, format="json")
            assert response.status_code == 409
            assert response.json()["code"] == CODE
    with no_effects(monkeypatch, fake, vault_entry=False):
        refused(lambda: vault.create_oauth_client(org, **key, client_id="new-fixture", client_secret=vault.SealedSecret(SECRET), actor=user))
        refused(lambda: vault.rotate_oauth_client(row, vault.SealedSecret(SECRET), user))


def test_callback_off_retains_inflight_state_and_has_409(rig, monkeypatch, settings):
    org, user, _, fake = rig
    registration(org, user)
    settings.MICROSOFT_CONNECT_ENABLED = True
    started = oauth.start(org, user, OUTLOOK, individual_authenticated=True)
    code, state = fake.authorize(started.authorize_url)
    settings.MICROSOFT_CONNECT_ENABLED = False
    browser = Client()
    browser.cookies[started.cookie_name] = started.cookie_value
    with no_effects(monkeypatch, fake):
        response = browser.get("/agentcc/mcp/connect/callback/", {"state": state, "code": code, "iss": ISSUER})
        assert response.status_code == 409
        assert MESSAGE in response.content.decode()
        assert response["Cache-Control"] == "no-store"
        assert State.objects.filter(pk=started.connect_id).exists()


def token_row(org, user, server=OUTLOOK, *, shared=False):
    return Credential.objects.create(organization=org, user=None if shared else user,
        server_id=server, scope="shared" if shared else "user", kind="oauth",
        encrypted_secret=credential_manager.encrypt_token("fixture-access"),
        encrypted_refresh=credential_manager.encrypt_token("fixture-refresh"),
        oauth_client_id=PLATFORM_ID, oauth_issuer=ISSUER, origin=RUNTIME,
        token_expires_at=timezone.now() + timedelta(seconds=30))


@pytest.mark.parametrize("shared", [False, True])
def test_existing_token_off_no_read_refresh_audit_or_mutation(rig, monkeypatch, shared):
    org, user, _, fake = rig
    row = token_row(org, user, shared=shared)
    body = {"org_id": str(org.pk), "server_id": OUTLOOK}
    body.update({"scope": "shared"} if shared else {"user_id": str(user.pk)})
    with no_effects(monkeypatch, fake):
        answer = tokens.answer_request(body)
        assert answer.outcome == CODE
        assert "connect_hint" not in answer.body()
        response = Client().post("/agentcc/internal/mcp/upstream-token/", json.dumps(body), content_type="application/json", HTTP_X_AGENTCC_MCP_INTERNAL=INTERNAL_SECRET)
        assert response.status_code == 409
        assert response.json()["error"] == CODE
    row.refresh_from_db()
    assert row.status == "active" and row.version == 1


def test_catalog_and_list_off_are_visible_without_connect_or_secret_reads(rig, monkeypatch):
    org, user, _, fake = rig
    registration(org, user)
    plan = catalog.load_bundle().apps[OUTLOOK].plans["futureagi-rest"]
    with no_effects(monkeypatch, fake):
        card = cards.provider_card(org, plan)
        assert card["connect"]["state"] == "unavailable"
        assert card["connect"]["reason"] == CODE
        row = next(r for r in mcp_connections.own_connections(org, user) if r["server_id"] == OUTLOOK)
        assert row["start"] == {"state": "unavailable", "reason": CODE}
        assert row["start_url"] is None and row["connected"] is False


def test_vendor_binding_start_and_delivery_off(rig, monkeypatch):
    org, user, _, fake = rig
    credential = Credential.objects.create(organization=org, server_id="partner", scope="shared", kind="oauth_client", origin="https://api.pipedream.com", encrypted_secret=credential_manager.encrypt_token(json.dumps({"client_id": "fixture-vendor", "client_secret": SECRET})))
    data = {"server_id": "partner", "vendor": "pipedream", "credential_id": str(credential.pk), "project_id": "proj_fixture", "environment": "development", "app_slug": OUTLOOK, "enabled": True}
    client = bearer(user)
    with no_effects(monkeypatch, fake):
        response = client.post("/agentcc/mcp/vendor-bindings/", data, format="json")
        assert response.status_code == 409 and response.json()["error"] == CODE
    binding = Binding.objects.create(organization=org, credential=credential, **{k: v for k, v in data.items() if k != "credential_id"})
    identity = {"source": "member", "id": str(user.pk)}
    subject = grants.subject_key(org.pk, "member", str(user.pk))
    VendorConnection.objects.create(binding=binding, end_user_key=subject, source="member", user=user, status="active", vendor_account_ref="fixture-account")
    with no_effects(monkeypatch, fake):
        refused(lambda: vendors.start(binding.pk, identity))
        body = {"org_id": str(org.pk), "server_id": "partner", "vendor": "pipedream", "end_user": identity}
        assert grants.answer_request(body)["error"] == CODE
        response = Client().post("/agentcc/mcp/vendors/internal/credential/", json.dumps(body), content_type="application/json", HTTP_X_AGENTCC_MCP_INTERNAL=INTERNAL_SECRET)
        assert response.status_code == 409 and response.json()["error"] == CODE


@pytest.mark.parametrize("app,provider,mode", [(OUTLOOK, "futureagi-rest", "shared"), ("microsoft-excel", "futureagi-rest", "user"), ("microsoft-teams", "workiq", "user")])
@pytest.mark.parametrize("source", ["env-and-file", "file-only"])
def test_on_platform_cannot_connect_before_discovery(rig, monkeypatch, settings, app, provider, mode, source):
    org, user, config, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = True
    if source == "file-only":
        monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_MICROSOFT_ID")
        monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_MICROSOFT_SECRET")
    entry = k2_entry(app, catalog_ref=f"{app}/{provider}@1.0.0", credential={"mode": "shared", "kind": "oauth"} if mode == "shared" else {"mode": "per_user"})
    config.mcp["servers"][app] = entry
    config.save(update_fields=["mcp"])
    with no_effects(monkeypatch, fake):
        assert selection.resolve(org, app, entry) is None
        with pytest.raises(clients.OrgClientRequired):
            selection.choose(org, app, entry, cimd=True, dcr=True, auth_methods=None)
        with pytest.raises(oauth.ConnectRefused) as caught:
            oauth.start(org, user, app, mode=mode, individual_authenticated=True)
        assert caught.value.code == clients.OrgClientRequired.code


@pytest.mark.parametrize("has_org_client", [False, True])
def test_on_platform_refresh_and_revoke_read_no_secret(rig, monkeypatch, settings, has_org_client):
    org, user, _, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = True
    if has_org_client:
        registration(org, user)  # A different org client must not be decrypted either.
    row = token_row(org, user)
    with no_effects(monkeypatch, fake):
        assert tokens.upstream_token(str(org.pk), str(user.pk), OUTLOOK).outcome == "needs_reauth"
        assert tokens._refresh(row, RUNTIME + "/microsoft-outlook/mcp") is None
        result = mcp_revocation.revoke(org, OUTLOOK, ISSUER, PLATFORM_ID, row.encrypted_refresh, row.encrypted_secret)
        assert result.outcome != "confirmed"
    assert not fake.client_auths


@pytest.mark.parametrize("app,group,enabled", [(OUTLOOK, "microsoft", True), ("gmail", "google", False), ("gmail", "google", True)])
def test_org_client_connect_callback_and_token_work_without_platform(rig, monkeypatch, settings, app, group, enabled):
    org, user, _, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = enabled
    registration(org, user, group)
    platform_read = Mock(wraps=clients._platform_client)
    monkeypatch.setattr(clients, "_platform_client", platform_read)
    started = oauth.start(org, user, app, individual_authenticated=True)
    code, state = fake.authorize(started.authorize_url)
    result = oauth.complete({"code": code, "state": state, "iss": ISSUER}, {started.cookie_name: started.cookie_value})
    assert result.ok
    assert tokens.upstream_token(str(org.pk), str(user.pk), app).access is not None
    row = Credential.objects.get(organization=org, user=user, server_id=app, kind="oauth")
    row.token_expires_at = timezone.now() + timedelta(seconds=30)
    row.save(update_fields=["token_expires_at"])
    assert tokens.upstream_token(str(org.pk), str(user.pk), app).outcome == "refreshed"
    row.refresh_from_db()
    assert mcp_revocation.revoke(org, app, row.oauth_issuer, row.oauth_client_id, row.encrypted_refresh, row.encrypted_secret).outcome == "confirmed"
    assert platform_read.call_count == 0
    assert fake.client_auths and all(item[-1] for item in fake.client_auths)


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_gate_defaults_and_non_boolean_values_fail_closed(rig, settings, value):
    org, _, config, _ = rig
    settings.MICROSOFT_CONNECT_ENABLED = value
    refused(lambda: k2.sign_in(org, OUTLOOK, config.mcp["servers"][OUTLOOK]))
    del settings.MICROSOFT_CONNECT_ENABLED
    refused(lambda: k2.sign_in(org, OUTLOOK, config.mcp["servers"][OUTLOOK]))


@pytest.mark.parametrize("app,group", [("custom-app", "microsoft"), ("microsoft-custom", "custom-group")])
def test_group_or_app_prefix_blocks_resolver(rig, monkeypatch, app, group):
    org, _, _, fake = rig
    with no_effects(monkeypatch, fake):
        refused(lambda: clients.resolve(org, app, group, token_url=ISSUER + "/token", revocation_url=None))


@pytest.mark.parametrize("enabled", [False, True])
def test_non_microsoft_platform_resolution_unchanged(rig, monkeypatch, settings, enabled):
    org, _, _, _ = rig
    settings.MICROSOFT_CONNECT_ENABLED = enabled
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", "fixture-google-client")
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", SECRET)
    ref = clients.resolve(org, "gmail", "google", token_url=ISSUER + "/token", revocation_url=None)
    assert ref.source == "platform" and ref.client_id == "fixture-google-client"
    assert clients.client_source(ref, registration=["preregistered"], client_group="google", token_url=ISSUER + "/token", revocation_url=None) == "platform"


def test_runtime_values_cannot_lift_platform_exclusion(rig, monkeypatch, settings):
    org, _, _, fake = rig
    settings.MICROSOFT_CONNECT_ENABLED = True
    settings.MICROSOFT_PLATFORM_CLIENT_LIFTED = True
    monkeypatch.setenv("MICROSOFT_PLATFORM_CLIENT_LIFTED", "true")
    with no_effects(monkeypatch, fake):
        assert clients.resolve(org, OUTLOOK, "microsoft", token_url=ISSUER + "/token", revocation_url=None) is None
