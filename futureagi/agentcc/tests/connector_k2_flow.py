"""Fixtures and the real-route sign-in flow for the K2 sign-in tests (W4b,
G14b): settings that keep K2 off unless a test sets the runtime URL, the
loopback authorization servers, the synthetic pilot bundle, the recorded
gateway purge, and the console start, the vendor's consent, the callback and
the gateway's internal token fetch; and what the Disconnect tests observe of a
revocation. Every value is a fake.

It imports nothing new, so a test module using it collects on a build without
K2 sign-in and fails there on what it asserts.
"""

import json
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import pytest
from django.test import Client

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_oauth_clients as oauth_clients
from agentcc.services import gateway_client
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.tests.connector_k2_helpers import (
    GOOGLE_CLIENT_ID,
    GOOGLE_SECRET,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_SECRET,
    PLATFORM_ID,
    PLATFORM_SECRET,
    RUNTIME,
    LoopbackAuthorizationServers,
    write_k2_bundle,
)
from agentcc.tests.mcp_upstream_oauth_helpers import (
    ADMIN_TOKEN,
    CALLBACK,
    CALLBACK_PATH,
    CONSOLE,
    INTERNAL_SECRET,
    START,
    seed_config,
)
from agentcc.tests.test_mcp_connections import LIST

SETTING = "AGENTCC_CONNECTORS_RUNTIME_URL"
CLIENTS = "/agentcc/connectors/oauth-clients/"
INTERNAL_TOKEN = "/agentcc/internal/mcp/upstream-token/"
GMAIL, OUTLOOK = "gmail", "microsoft-outlook"


@pytest.fixture
def k2_settings(settings, monkeypatch):
    """K2 off, the connect settings on, and no platform client configured."""
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    setattr(settings, SETTING, "")
    monkeypatch.delenv(SETTING, raising=False)
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN)
    for group in ("GOOGLE", "MICROSOFT"):
        for part in ("ID", "SECRET"):
            monkeypatch.delenv(
                f"AGENTCC_CONNECTOR_CLIENT_{group}_{part}", raising=False
            )
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENTS_FILE", raising=False)


@pytest.fixture
def servers(monkeypatch):
    fake = LoopbackAuthorizationServers(
        {
            GOOGLE_CLIENT_ID: GOOGLE_SECRET,
            MICROSOFT_CLIENT_ID: MICROSOFT_SECRET,
            PLATFORM_ID: PLATFORM_SECRET,
        },
        redirect_uri=CALLBACK,
    ).start()
    monkeypatch.setenv("AGENTCC_MCP_EGRESS_PRIVATE_ALLOW", f"127.0.0.1:{fake.port}")
    yield fake
    fake.stop()


@pytest.fixture
def k2_bundle(tmp_path, monkeypatch, servers):
    target = write_k2_bundle(tmp_path / "catalog-k2.json", servers.origin)
    monkeypatch.setattr(catalog, "BUNDLE_PATH", target)
    return target


@pytest.fixture
def runtime(settings):
    setattr(settings, SETTING, RUNTIME)
    return RUNTIME


@pytest.fixture
def purges(monkeypatch):
    calls = []

    def purge(org_id, server_id, user_id=None):
        calls.append((org_id, server_id, user_id))
        return True

    monkeypatch.setattr(connect.gateway_credentials, "purge", purge)
    return calls


def seed(org, **servers):
    config = seed_config(org, servers)
    config.mcp["enabled"] = True
    config.save(update_fields=["mcp"])
    return config


def register(
    org, actor, group="google", client_id=GOOGLE_CLIENT_ID, secret=GOOGLE_SECRET
):
    """The org's own OAuth app registration, stored through the vault."""
    return vault.create_oauth_client(
        org,
        client_group=group,
        client_id=client_id,
        client_secret=vault.SealedSecret(secret),
        actor=actor,
    )


def query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def listed(client, server_id):
    """The caller's "Your accounts" row for ``server_id``, or None."""
    response = client.get(LIST)
    assert response.status_code == 200, response.status_code
    rows = response.json()["result"]["connections"]
    return next((r for r in rows if r["server_id"] == server_id), None)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def consent(authorize_url):
    """The member's browser at the vendor's sign-in page: where its 302 sends
    them back, as query parameters. No proxy: the page is on loopback."""
    opener = build_opener(ProxyHandler({}), _NoRedirect)
    try:
        opener.open(Request(authorize_url), timeout=10)
    except Exception as answer:  # urllib raises for the unfollowed 302
        location = answer.headers.get("Location")
        assert getattr(answer, "code", None) == 302 and location
        return query(location)
    raise AssertionError("the authorization endpoint did not redirect")


def sign_in(client, servers, server_id, subject="member-1"):
    """A whole sign-in: the start, the vendor's consent, the callback in the
    browser that started it. Returns the authorize URL and the callback page."""
    started = client.post(START.format(server_id), {}, format="json")
    assert started.status_code == 200, started.json().get("code")
    url = started.json()["result"]["authorize_url"]
    (name,) = [n for n in started.cookies if n.startswith(connect.COOKIE_PREFIX)]
    servers.subject = subject
    back = consent(url)
    browser = Client()
    browser.cookies[name] = started.cookies[name].value
    return url, browser.get(CALLBACK_PATH, back)


def fetch(org, user, server_id):
    """The gateway's token fetch, over the internal route it calls."""
    response = Client().post(
        INTERNAL_TOKEN,
        data=json.dumps(
            {"org_id": str(org.pk), "user_id": str(user.pk), "server_id": server_id}
        ),
        content_type="application/json",
        HTTP_X_AGENTCC_MCP_INTERNAL=INTERNAL_SECRET,
    )
    assert response.status_code == 200, response.status_code
    return response.json()


def account(org, user, server_id):
    return AgentccMCPCredential.objects.get(
        organization=org, server_id=server_id, scope="user", user=user, kind="oauth"
    )


# --- Revocation ------------------------------------------------------------------


def platform_reads(monkeypatch):
    """Every read of FutureAGI's platform client, by group."""
    reads = []
    real = oauth_clients._platform_client

    def spy(group):
        reads.append(group)
        return real(group)

    monkeypatch.setattr(oauth_clients, "_platform_client", spy)
    return reads


def platform_has_the_same_app(monkeypatch):
    """The operator's environment holds the very app the org registered, so
    the loopback server would accept a revocation sent as it."""
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", GOOGLE_CLIENT_ID)
    monkeypatch.setenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", GOOGLE_SECRET)


def published_metadata(monkeypatch, servers):
    """Off a pilot's declared spec, the issuer's published metadata names the
    revocation endpoint, as Google's does; here it is the loopback server's."""

    def metadata(issuer):
        return {
            "issuer": issuer,
            "revocation_endpoint": servers.origin + "/google/revoke",
        }

    monkeypatch.setattr(connect, "authorization_server_metadata", metadata)


def assert_revocation_waits(
    gone, org, user, servers, purges, reason="org_registration_missing"
):
    """Disconnect finished locally; nothing was sent; the revocation waits."""
    row = account(org, user, GMAIL)
    reasons = list(
        row.cleanup_receipts.filter(step="vendor_revocation").values_list(
            "reason", flat=True
        )
    )
    assert gone.status_code == 200
    assert [r for r in servers.requests if r[2] == "revoke"] == []
    assert servers.revoked == []
    assert gone.json()["result"]["upstream_revocation"] == "pending"
    assert gone.json()["result"]["cleanup_pending"] is True
    assert (row.status, row.oauth_revoke_pending) == ("revoked", True)
    assert reasons[-1] == reason
    assert (str(org.pk), GMAIL, str(user.pk)) in purges
