"""The organization's own OAuth app in the connect flow (CN11, through CN3's
resolver, in M25x's connect).

An Owner or Admin stores the org's own OAuth client (CN11's routes, now on
the site). A connect to the vendor then identifies itself as that client:
for one of the org's servers always, for a server added from the catalog
where its client group's client is accepted, FutureAGI's platform client
after it, then the client metadata document, then client registration. The
client's secret goes only to the vendor's token and revocation endpoints,
for the token that client issued, and never to a log, a response or a row.

Each test is one property. The vendor is ``FakeVendor`` (a pre-registered
client must prove its secret), answered in place of the guarded fetcher, and
the gateway's cache purge is recorded, so no test reaches a network. Every
value here is a fake.
"""

import copy
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from structlog.testing import capture_logs

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_oauth_clients as oauth_clients
from agentcc.services import gateway_client
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CALLBACK_PATH,
    CLIENT_METADATA,
    CONSOLE,
    INTERNAL_SECRET,
    ISSUER,
    SERVER_ID,
    SERVER_URL,
    START,
    FakeVendor,
    seed_config,
)
from agentcc.tests.test_connector_catalog import _app, _bundle_validator, _provider

pytestmark = pytest.mark.django_db

Status = AgentccMCPCredential.Status

CLIENTS = "/agentcc/connectors/oauth-clients/"
TOKEN_URL = ISSUER + "/token"
ORG_CLIENT_ID = "fake-cn24-org-client-id"
ORG_SECRET = "fake-cn24-org-client-secret-not-real"
OTHER_CLIENT_ID = "fake-cn24-other-client-id"
OTHER_SECRET = "fake-cn24-other-client-secret-not-real"
PLATFORM_ID = "fake-cn24-platform-client-id"
PLATFORM_SECRET = "fake-cn24-platform-client-secret-not-real"
SECRETS = (ORG_SECRET, OTHER_SECRET, PLATFORM_SECRET)
SLATE_REF = "slate-mail/slate-oauth@1.0.0"


@pytest.fixture(autouse=True)
def _settings(settings, monkeypatch):
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)
    for name in (
        "AGENTCC_CONNECTOR_CLIENT_SLATE_ID",
        "AGENTCC_CONNECTOR_CLIENT_SLATE_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENTS_FILE", raising=False)


@pytest.fixture
def vendor():
    fake = FakeVendor(
        confidential={
            ORG_CLIENT_ID: ORG_SECRET,
            OTHER_CLIENT_ID: OTHER_SECRET,
            PLATFORM_ID: PLATFORM_SECRET,
        }
    )
    with patch.object(connect.mcp_safe_fetch, "fetch", fake.fetch):
        yield fake


@pytest.fixture
def purges():
    calls = []

    def purge(org_id, server_id, user_id=None):
        calls.append((org_id, server_id, user_id))
        return True

    with patch.object(connect.gateway_credentials, "purge", purge):
        yield calls


@pytest.fixture
def config(organization, user):
    return seed_config(organization)


@pytest.fixture
def slate_bundle(tmp_path, monkeypatch):
    """A catalog whose one OAuth app, Slate Mail, signs in through the
    vendor's authorization server with a pre-registered client of the
    client group ``slate`` (its declared spec), at ``SERVER_URL``."""
    oauth2 = {
        "authorizeUrl": ISSUER + "/authorize",
        "tokenUrl": TOKEN_URL,
        "scopes": ["mcp"],
        "pkce": True,
        "tokenBody": "form",
        "clientAuth": "basic",
        # Pre-registered only: a vendor MCP record that also takes cimd or dcr
        # signs in as discovered MCP OAuth, without its declared group client.
        "registration": ["preregistered"],
        "clientGroup": "slate",
        "revocation": {
            "url": ISSUER + "/revoke",
            "method": "POST",
            "tokenBody": "form",
            "tokenField": "token",
            "clientAuth": "basic",
        },
    }
    bundle = {
        "schema": catalog.BUNDLE_SCHEMA,
        "version": "2026.10.25.1",
        "sha256": "5" * 64,
        "sourceCommit": "06e27ec2d88b0367a21900e7f477c8df0aab49e7",
        "apps": [
            _app(
                "slate-mail",
                "Slate Mail",
                "Mail for teams.",
                category="communication",
                categories=["email"],
                default="slate-oauth",
            )
        ],
        "providers": [
            _provider(
                "slate-mail",
                "slate-oauth",
                auth={"kind": "oauth2", "oauth2": oauth2},
                tools=[],
                transport={"kind": "http", "url": SERVER_URL},
                account_modes=["user"],
            )
        ],
        "platforms": [],
    }
    _bundle_validator().validate(bundle)
    target = tmp_path / "catalog-slate.json"
    target.write_text(json.dumps(bundle))
    monkeypatch.setattr(catalog, "BUNDLE_PATH", target)
    return bundle


def _org_client(org, actor, *, server_id=SERVER_ID, client_group=None, **values):
    return vault.create_oauth_client(
        org,
        server_id=None if client_group else server_id,
        client_group=client_group,
        client_id=values.get("client_id", ORG_CLIENT_ID),
        client_secret=vault.SealedSecret(values.get("secret", ORG_SECRET)),
        actor=actor,
    )


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _start(client, server_id=SERVER_ID, **body):
    return client.post(START.format(server_id), body, format="json")


def _started(client):
    response = _start(client)
    assert response.status_code == 200, response.json()
    url = response.json()["result"]["authorize_url"]
    (name,) = [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
    return url, name, response.cookies[name]


def _connect(client, vendor):
    """A whole connect: the start, the vendor's sign-in, the callback in the
    browser that started it. Returns the start's authorize URL and the
    callback's response."""
    url, name, cookie = _started(client)
    browser = Client()
    browser.cookies[name] = cookie.value
    code, state = vendor.authorize(url)
    return url, browser.get(
        CALLBACK_PATH, {"code": code, "state": state, "iss": ISSUER}
    )


def _row(org, user):
    return AgentccMCPCredential.objects.get(
        organization=org, server_id=SERVER_ID, scope="user", user=user, kind="oauth"
    )


def _expire(row, within=timedelta(seconds=30)):
    row.token_expires_at = timezone.now() + within
    row.save(update_fields=["token_expires_at"])


def _auths(vendor, endpoint):
    return [a for a in vendor.client_auths if a[0] == endpoint]


def _secrets_in(text):
    return [s for s in SECRETS if s in text]


# --- Property: the site serves the org's OAuth client routes -----------------------


def test_the_site_mounts_the_oauth_client_routes(auth_client, user, config):
    """The routes answer on the app's own URLs, as every other module's do."""
    assert reverse("agentcc-connector-oauth-clients") == CLIENTS
    response = auth_client.get(CLIENTS)

    assert response.status_code == 200, response.content
    assert response.json()["results"] == []


# --- Property: a connect signs in through the org's own client -----------------------


def test_a_connect_signs_in_through_the_orgs_own_client(
    auth_client, organization, user, config, vendor, purges
):
    _org_client(organization, user)

    url, response = _connect(auth_client, vendor)

    assert _query(url)["client_id"] == ORG_CLIENT_ID
    assert response.status_code == 200, response.content
    assert _auths(vendor, "token") == [("token", "basic", ORG_CLIENT_ID, True)]
    assert _row(organization, user).oauth_client_id == ORG_CLIENT_ID


def test_the_connect_row_records_the_orgs_client_and_never_its_secret(
    auth_client, organization, user, config, vendor
):
    _org_client(organization, user)

    url, _, _ = _started(auth_client)

    row = AgentccMCPConnectState.objects.get()
    assert (row.client_kind, row.client_id) == ("org", ORG_CLIENT_ID)
    assert row.client_auth == "client_secret_basic"
    stored = json.dumps(
        {f.name: str(getattr(row, f.name)) for f in row._meta.concrete_fields}
    )
    assert _secrets_in(stored + url) == []


def test_an_org_client_for_another_server_is_never_used(
    auth_client, organization, user, config, vendor
):
    _org_client(organization, user, server_id="shared-one")

    url, _, _ = _started(auth_client)

    assert _query(url)["client_id"] == CLIENT_METADATA
    assert AgentccMCPConnectState.objects.get().client_kind == "cimd"


# --- Property: the secret goes only to the token endpoint, as the server takes it ------


@pytest.mark.parametrize(
    "methods, how",
    [
        (None, "basic"),
        (["client_secret_basic", "client_secret_post"], "basic"),
        (["client_secret_post"], "post"),
    ],
)
def test_the_secret_goes_only_to_the_token_endpoint_by_a_method_it_takes(
    auth_client, organization, user, config, vendor, purges, methods, how
):
    vendor.auth_methods = methods
    _org_client(organization, user)

    _, response = _connect(auth_client, vendor)

    assert response.status_code == 200, response.content
    assert _auths(vendor, "token") == [("token", how, ORG_CLIENT_ID, True)]
    for _method, url, data, json_body, headers in vendor.requests:
        if url == TOKEN_URL:
            continue
        assert _secrets_in(repr((data, json_body, headers))) == [], url


def test_a_server_that_takes_no_client_secret_refuses_the_orgs_client(
    auth_client, organization, user, config, vendor
):
    """The org chose its own client for this server: a server that takes no
    client secret is refused, never signed in to as another client."""
    vendor.auth_methods = ["none"]
    _org_client(organization, user)

    response = _start(auth_client)

    assert response.status_code == 409, response.content
    assert response.json()["code"] == "oauth_client_unavailable"
    assert AgentccMCPConnectState.objects.count() == 0
    assert _auths(vendor, "token") == []


# --- Property: a refresh authenticates as the client that issued the token -----------


def test_a_refresh_proves_the_issuing_clients_secret(
    auth_client, organization, user, config, vendor, purges
):
    _org_client(organization, user)
    _connect(auth_client, vendor)
    _expire(_row(organization, user))

    answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)

    assert answer.outcome == "refreshed"
    assert _auths(vendor, "token")[-1] == ("token", "basic", ORG_CLIENT_ID, True)
    assert vendor.refreshes[-1]["client_id"] == ORG_CLIENT_ID


def test_a_rotated_client_secret_refreshes_the_same_clients_token(
    auth_client, organization, user, config, vendor, purges
):
    client = _org_client(organization, user)
    _connect(auth_client, vendor)
    vendor.confidential[ORG_CLIENT_ID] = OTHER_SECRET
    vault.rotate_oauth_client(client, vault.SealedSecret(OTHER_SECRET), user)
    _expire(_row(organization, user))

    answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)

    assert answer.outcome == "refreshed"
    assert _auths(vendor, "token")[-1] == ("token", "basic", ORG_CLIENT_ID, True)


def test_a_replaced_client_never_refreshes_with_another_clients_secret(
    auth_client, organization, user, config, vendor, purges
):
    """A token is refreshed only as the client that issued it: once the org
    replaces its client, the member signs in again."""
    first = _org_client(organization, user)
    _connect(auth_client, vendor)
    vault.revoke_oauth_client(first, user)
    _org_client(organization, user, client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)
    _expire(_row(organization, user))
    sent = len(vendor.requests)

    answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)

    assert answer.outcome == "needs_reauth"
    later = vendor.requests[sent:]
    assert _secrets_in(repr(later)) == []
    assert [a for a in _auths(vendor, "token") if a[2] == OTHER_CLIENT_ID] == []


def test_an_unreadable_org_client_at_refresh_keeps_the_connection(
    auth_client, organization, user, config, vendor, purges
):
    """An org client that cannot be read for now may still be the token's
    client: the refresh goes out with no secret, the vendor's invalid_client
    does not end the connection, and once the client reads again the token
    refreshes as that client."""
    client = _org_client(organization, user)
    _connect(auth_client, vendor)
    stored = AgentccMCPCredential.objects.filter(pk=client.pk)
    readable = stored.values_list("encrypted_secret", flat=True).get()
    stored.update(encrypted_secret="enc::not-a-ciphertext")
    _expire(_row(organization, user))
    sent = len(vendor.requests)

    answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)

    assert answer.outcome == "ok"
    assert _row(organization, user).status == Status.ACTIVE
    assert _auths(vendor, "token")[-1] == ("token", "none", ORG_CLIENT_ID, False)
    assert _secrets_in(repr(vendor.requests[sent:])) == []

    stored.update(encrypted_secret=readable)
    answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)

    assert answer.outcome == "refreshed"
    assert _auths(vendor, "token")[-1] == ("token", "basic", ORG_CLIENT_ID, True)


def test_a_confidential_client_whose_secret_is_refused_keeps_the_connection(
    auth_client, organization, user, config, vendor, purges
):
    """A refresh that sent the issuing client's own secret and got
    invalid_client is not final: an admin can rotate a wrong secret."""
    _org_client(organization, user)
    _connect(auth_client, vendor)
    vendor.confidential[ORG_CLIENT_ID] = OTHER_SECRET  # not the secret we hold
    _expire(_row(organization, user))

    answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)

    assert answer.outcome == "ok"
    assert _row(organization, user).status == Status.ACTIVE
    assert _auths(vendor, "token")[-1] == ("token", "basic", ORG_CLIENT_ID, False)


# --- Property: a disconnect revokes as the issuing client -----------------------------


def test_disconnect_revokes_as_the_issuing_client(
    auth_client, organization, user, config, vendor, purges
):
    _org_client(organization, user)
    _connect(auth_client, vendor)

    response = auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/")

    assert response.status_code == 200, response.content
    assert _auths(vendor, "revoke") == [("revoke", "basic", ORG_CLIENT_ID, True)]
    assert len(vendor.revoked) == 1


# --- Property: an org client that cannot be read refuses, never another client --------


def test_an_unreadable_org_client_refuses_the_connect(
    auth_client, organization, user, config, vendor
):
    client = _org_client(organization, user)
    AgentccMCPCredential.objects.filter(pk=client.pk).update(
        encrypted_secret="enc::not-a-ciphertext"
    )

    response = _start(auth_client)

    assert response.status_code == 409, response.content
    assert response.json()["code"] == "connector_oauth_client_unreadable"
    assert AgentccMCPConnectState.objects.count() == 0


# --- Property: a catalog server takes its group's client, the org's before ours -------


def test_a_preregistered_only_catalog_server_never_reads_the_platform_client(
    auth_client, organization, user, vendor, purges, slate_bundle, monkeypatch
):
    """Slate Mail's authorization server takes only pre-registered clients,
    so it signs in with the organization's own OAuth app alone
    (connector_org_app_oauth): the platform client is never read, even where
    the deployment configures one for its group."""
    seed_config(
        organization,
        {
            SERVER_ID: {
                "url": SERVER_URL,
                "transport": "http",
                "catalog_ref": SLATE_REF,
                "credential": {"mode": "per_user"},
            }
        },
    )
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLATE_ID", PLATFORM_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLATE_SECRET", PLATFORM_SECRET)
    reads = []
    real = oauth_clients._platform_client
    monkeypatch.setattr(
        oauth_clients,
        "_platform_client",
        lambda group: reads.append(group) or real(group),
    )

    with pytest.raises(oauth_clients.OAuthClientUnavailable):
        connect.connect_clients.choose(
            organization,
            SERVER_ID,
            connect.server_entry(organization.pk, SERVER_ID),
            cimd=True,
            dcr=True,
            auth_methods=["client_secret_basic"],
        )
    response = _start(auth_client)

    assert response.status_code == 409, response.content
    assert response.json()["code"] == "connector_oauth_org_client_required"
    assert reads == []
    assert not vendor.requests
    assert not AgentccMCPConnectState.objects.exists()


def test_a_preregistered_only_catalog_server_signs_in_with_the_orgs_group_client(
    auth_client, organization, user, vendor, purges, slate_bundle, monkeypatch
):
    seed_config(
        organization,
        {
            SERVER_ID: {
                "url": SERVER_URL,
                "transport": "http",
                "catalog_ref": SLATE_REF,
                "credential": {"mode": "per_user"},
            }
        },
    )
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLATE_ID", PLATFORM_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLATE_SECRET", PLATFORM_SECRET)
    _org_client(organization, user, client_group="slate")

    selected = connect.connect_clients.choose(
        organization,
        SERVER_ID,
        connect.server_entry(organization.pk, SERVER_ID),
        cimd=True,
        dcr=True,
        auth_methods=["client_secret_basic"],
    )
    assert (selected.client_id, selected.kind, selected.auth) == (
        ORG_CLIENT_ID,
        "org",
        "client_secret_basic",
    )
    url, response = _connect(auth_client, vendor)
    assert url.startswith(ISSUER + "/authorize?")
    assert _query(url)["client_id"] == ORG_CLIENT_ID
    assert "resource" not in _query(url)
    assert response.status_code == 200, response.content
    # The declared authorization server: nothing is discovered.
    assert [r for r in vendor.requests if r[0] == "GET"] == []
    assert _auths(vendor, "token") == [("token", "basic", ORG_CLIENT_ID, True)]
    assert _row(organization, user).oauth_client_id == ORG_CLIENT_ID


def test_a_catalog_server_that_takes_dcr_never_takes_its_declared_groups_client(
    organization, user, vendor, slate_bundle, tmp_path, monkeypatch
):
    """A vendor MCP record that declares oauth2 and also takes dynamic client
    registration signs in as discovered MCP OAuth: neither the org's client
    for its declared group nor FutureAGI's platform client is chosen, only
    the org's own client for this server, else CIMD or DCR."""
    discovered = copy.deepcopy(slate_bundle)
    oauth2 = discovered["providers"][0]["auth"]["oauth2"]
    oauth2["registration"] = ["preregistered", "dcr"]
    _bundle_validator().validate(discovered)
    target = tmp_path / "catalog-slate-dcr.json"
    target.write_text(json.dumps(discovered))
    monkeypatch.setattr(catalog, "BUNDLE_PATH", target)
    seed_config(
        organization,
        {
            SERVER_ID: {
                "url": SERVER_URL,
                "transport": "http",
                "catalog_ref": SLATE_REF,
                "credential": {"mode": "per_user"},
            }
        },
    )
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLATE_ID", PLATFORM_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_SLATE_SECRET", PLATFORM_SECRET)
    _org_client(organization, user, client_group="slate")

    def choose():
        return connect.connect_clients.choose(
            organization,
            SERVER_ID,
            connect.server_entry(organization.pk, SERVER_ID),
            cimd=True,
            dcr=True,
            auth_methods=["client_secret_basic"],
        )

    entry = connect.server_entry(organization.pk, SERVER_ID)
    assert catalog.oauth2_by_discovery(catalog.provider_record(entry)) is True
    assert choose() is None

    _org_client(organization, user, client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)

    selected = choose()
    assert (selected.client_id, selected.kind, selected.auth) == (
        OTHER_CLIENT_ID,
        "org",
        "client_secret_basic",
    )
    assert not vendor.requests


# --- Property: a client secret never reaches a log line or a response ----------------


def test_no_client_secret_in_any_log_or_response(
    auth_client, organization, user, config, vendor, purges
):
    _org_client(organization, user)
    with capture_logs() as logs:
        url, response = _connect(auth_client, vendor)
        _expire(_row(organization, user))
        answer = tokens.upstream_token(str(organization.pk), str(user.pk), SERVER_ID)
        body = json.dumps(answer.body())
        listed = auth_client.get(CLIENTS)
        gone = auth_client.delete(f"/agentcc/mcp/connect/{SERVER_ID}/")

    assert (response.status_code, listed.status_code, gone.status_code) == (
        200,
        200,
        200,
    )
    seen = repr(logs) + url + body + response.content.decode()
    seen += listed.content.decode() + gone.content.decode()
    assert _secrets_in(seen) == []
