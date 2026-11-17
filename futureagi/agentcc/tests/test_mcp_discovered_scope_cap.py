"""A self-registering connector's sign-in asks only for the scopes its
catalog record lists.

A vendor MCP server whose authorization server registers our client itself
(dcr) or reads our client metadata document (cimd) is discovered at each
sign-in, and its protected-resource metadata lists the scopes it supports.
Where the server was added from a catalog record that lists scopes, the
authorize request asks for those of them the metadata also lists, in the
record's order, and the connect row records the same list. A record that
lists none, and a server added by its URL, ask for every scope the metadata
lists, as before. A record none of whose scopes the metadata lists is
refused with one sentence before a client is registered, anything is stored
or the browser is sent anywhere.

The synthetic cases answer the metadata in place of the guarded fetcher. The
shipped cases read the Django copy of the catalog for Deel and Pipedrive and
answer with the scopes their servers' protected-resource metadata listed on
2026-11-12. No vendor is called.
"""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from agentcc.models.mcp_upstream_oauth import (
    AgentccMCPConnectState,
    AgentccMCPUpstreamClient,
)
from agentcc.services import connector_catalog as catalog
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.tests import test_connector_catalog as fixtures
from agentcc.tests.mcp_upstream_oauth_helpers import (
    CALLBACK,
    CONSOLE,
    INTERNAL_SECRET,
    ISSUER,
    START,
    _response,
)
from agentcc.tests.test_connector_catalog import _app, _bundle, _provider, _seed

pytestmark = pytest.mark.django_db

bundle = fixtures.bundle

DOCS = (
    Path(__file__).resolve().parents[3]
    / "agentcc-gateway"
    / "catalog"
    / "connectors"
    / "dist"
    / "emit"
    / "docs"
    / "apps"
)

# Lists three scopes the server offers and one it does not.
CAPPED = "tally-tasks"
CAPPED_SCOPES = ["tasks:read", "projects:read", "archive:read", "tasks:write"]
# Lists none.
OPEN = "plume-notes"
# Lists only scopes the server does not offer.
APART = "quill-crm"
APART_SCOPES = ["billing:read", "billing:write"]
# A server added by its URL, with no catalog record.
BY_URL = "by-url"
# What the synthetic servers' protected-resource metadata lists, in another
# order than the records.
OFFERED = ["admin", "tasks:write", "projects:write", "projects:read", "tasks:read"]

REFUSED = (
    "the server offers none of the access this connector asks for, so the "
    "sign-in did not start"
)

# Deel's protected-resource metadata
# (https://api.letsdeel.com/.well-known/oauth-protected-resource/mcp,
# read 2026-11-12), in its order.
DEEL_URL = "https://api.letsdeel.com/mcp"
DEEL_ISSUER = "https://api.letsdeel.com"
DEEL_OFFERED = [
    "accounting:read",
    "accounting:write",
    "adjustments:read",
    "adjustments:write",
    "agentic-workflows:write",
    "ai:read",
    "ai:write",
    "api-logs:read",
    "approvals:read",
    "approvals:write",
    "apps:read",
    "apps:write",
    "ats.applications.notes:write",
    "ats:read",
    "ats:write",
    "auth:write",
    "benefits:read",
    "benefits:write",
    "candidates:read",
    "candidates:write",
    "compensation-management:read",
    "compensation-management:write",
    "contracts:read",
    "contracts:write",
    "conversation:read",
    "conversation:write",
    "equities:read",
    "equities:write",
    "files:read",
    "files:write",
    "forms:read",
    "global-payroll:read",
    "global-payroll:write",
    "groups:read",
    "groups:write",
    "hr-suite:read",
    "hr-suite:write",
    "i-9:read",
    "i-9:write",
    "immigration:read",
    "immigration:write",
    "invoice-adjustments:read",
    "invoice-adjustments:write",
    "invoice:create",
    "it-agreements:read",
    "it-assets:read",
    "it-assets:write",
    "it-orders:read",
    "it-orders:write",
    "it-policies:read",
    "it-policies:write",
    "it-products:read",
    "it-seats:read",
    "it-seats:write",
    "it-service-requests:read",
    "it-service-requests:write",
    "job-architecture:read",
    "job-architecture:write",
    "jobs:read",
    "jobs:write",
    "knowledge-hub:read",
    "legal-entity:read",
    "legal-entity:write",
    "milestones:read",
    "milestones:write",
    "off-cycle-payments:read",
    "off-cycle-payments:write",
    "on-demand-payroll:read",
    "on-demand-payroll:write",
    "organizations:read",
    "organizations:write",
    "partners:read",
    "partners:write",
    "payments:read",
    "payment-statements:read",
    "payment-statements:write",
    "payslips:read",
    "payslips:write",
    "people:read",
    "people:write",
    "profile:read",
    "profile:write",
    "public-token:write",
    "screenings:read",
    "screenings:write",
    "talents:read",
    "talents:write",
    "tasks:read",
    "tasks:write",
    "time-off:read",
    "time-off:write",
    "timesheets:read",
    "timesheets:write",
    "time-tracking:read",
    "time-tracking:write",
    "tokens:read",
    "tokens:write",
    "treasury-vendorbill:write",
    "Users:read",
    "Users:write",
    "webhooks:read",
    "webhooks:write",
    "withdrawals:read",
    "worker:read",
    "worker:write",
]
# The writes Deel's MCP authorization page names among the scopes its tools
# use (https://developer.deel.com/mcp/authorization, read 2026-11-12).
DEEL_WRITES = ("contracts:write", "timesheets:write", "invoice-adjustments:write")
DEEL_ASKED = [s for s in DEEL_OFFERED if s.endswith(":read") or s in DEEL_WRITES]

# Pipedrive's protected-resource metadata
# (https://mcp.pipedrive.ai/.well-known/oauth-protected-resource/mcp,
# read 2026-11-12), in its order.
PIPEDRIVE_URL = "https://mcp.pipedrive.ai/mcp"
PIPEDRIVE_ISSUER = "https://oauth.pipedrive.com"
PIPEDRIVE_OFFERED = [
    "activities:full",
    "admin",
    "base",
    "contact-fields:full",
    "contacts:full",
    "deal-fields:full",
    "deals:full",
    "leads:full",
    "mail-sending:full",
    "mail:full",
    "product-fields:full",
    "products:full",
    "project-fields:full",
    "projects:full",
    "search:read",
    "users:read",
]
PIPEDRIVE_ASKED = [s for s in PIPEDRIVE_OFFERED if s != "admin"]

# Ashby's protected-resource metadata
# (https://mcp.ashbyhq.com/.well-known/oauth-protected-resource/mcp/v1,
# read 2026-11-13), in its order. Its issuer has a path, and its authorization
# server registers clients dynamically and reads no client metadata document.
ASHBY_URL = "https://mcp.ashbyhq.com/mcp/v1"
ASHBY_ISSUER = "https://mcp-auth.ashbyhq.com/oidc"
ASHBY_OFFERED = ["openid", "mcp", "offline_access"]
# The record lists every scope the metadata lists.
ASHBY_ASKED = list(ASHBY_OFFERED)

# Todoist's protected-resource metadata
# (https://ai.todoist.net/.well-known/oauth-protected-resource/mcp,
# read 2026-11-13). Its authorization server reads a client metadata document
# and registers clients dynamically.
TODOIST_URL = "https://ai.todoist.net/mcp"
TODOIST_ISSUER = "https://todoist.com"
TODOIST_OFFERED = ["data:read_write"]
# The record lists the one scope the metadata lists.
TODOIST_ASKED = list(TODOIST_OFFERED)


class Vendor:
    """A vendor MCP server's metadata, answered in place of the guarded
    fetcher: the path-form protected-resource metadata of ``server_url``,
    listing ``offered`` as its scopes (no such field for None); the issuer's
    authorization server metadata; and client registration where ``dcr``.
    Any other request answers 404."""

    def __init__(self, server_url, issuer, offered, *, cimd=True, dcr=False):
        self.server_url, self.issuer, self.offered = server_url, issuer, offered
        self.cimd, self.dcr = cimd, dcr
        self.requests = []
        self.registrations = []

    def fetch(self, url, *, method="GET", json_body=None, **kwargs):
        self.requests.append((method, url))
        parts = urlsplit(self.server_url)
        resource_metadata = (
            f"{parts.scheme}://{parts.netloc}"
            f"/.well-known/oauth-protected-resource{parts.path}"
        )
        if method == "GET" and url == resource_metadata:
            body = {
                "resource": self.server_url,
                "authorization_servers": [self.issuer],
            }
            if self.offered is not None:
                body["scopes_supported"] = list(self.offered)
            return _response(200, body)
        if method == "GET" and url == (
            self.issuer + "/.well-known/oauth-authorization-server"
        ):
            return _response(200, self.metadata())
        if method == "POST" and self.dcr and url == self.issuer + "/register":
            self.registrations.append(json_body)
            return _response(
                201,
                {
                    "client_id": "synthetic-dcr-client",
                    "token_endpoint_auth_method": "none",
                },
            )
        return _response(404, {"error": "not_found"})

    def metadata(self):
        meta = {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/oauth/authorize",
            "token_endpoint": self.issuer + "/oauth/token",
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": self.cimd,
            "token_endpoint_auth_methods_supported": ["none"],
        }
        if self.dcr:
            meta["registration_endpoint"] = self.issuer + "/register"
        return meta


class PathIssuerVendor(Vendor):
    """A Vendor whose issuer has a path, as Ashby's does: it also answers its
    authorization server metadata at the path-inserted URL (RFC 8414, section
    3.1), the first one discovery reads."""

    def fetch(self, url, *, method="GET", json_body=None, **kwargs):
        parts = urlsplit(self.issuer)
        inserted = (
            f"{parts.scheme}://{parts.netloc}"
            f"/.well-known/oauth-authorization-server{parts.path}"
        )
        if method == "GET" and url == inserted:
            self.requests.append((method, url))
            return _response(200, self.metadata())
        return super().fetch(url, method=method, json_body=json_body, **kwargs)


@pytest.fixture(autouse=True)
def _connect_settings(settings, monkeypatch):
    settings.AGENTCC_MCP_CATALOG_ENABLED = True
    settings.AGENTCC_MCP_CONNECT_CALLBACK_URL = CALLBACK
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = CONSOLE
    monkeypatch.setenv(connect.INTERNAL_SECRET_ENV, INTERNAL_SECRET)
    # No platform client: the sign-in is the self-registering one.
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENTS_FILE", raising=False)
    for app_id in (CAPPED, OPEN, APART, "deel", "pipedrive"):
        name = app_id.upper().replace("-", "_")
        for part in ("ID", "SECRET"):
            monkeypatch.delenv(f"AGENTCC_CONNECTOR_CLIENT_{name}_{part}", raising=False)


def _url(app_id):
    return f"https://mcp.{app_id}.example.com/mcp"


def _sign_in(app_id, scopes):
    return _provider(
        app_id,
        "mcp",
        auth={
            "kind": "oauth2",
            "oauth2": {
                "authorizeUrl": ISSUER + "/oauth/authorize",
                "tokenUrl": ISSUER + "/oauth/token",
                "scopes": scopes,
                "pkce": True,
                "tokenBody": "form",
                "clientAuth": "body",
                "registration": ["cimd", "dcr"],
            },
        },
        tools=[],
        account_modes=["agent", "user"],
        transport={"kind": "http", "url": _url(app_id)},
    )


def _synthetic_app(app_id, name):
    return _app(
        app_id,
        name,
        f"Synthetic {name} app.",
        category="productivity",
        categories=["docs"],
        default="mcp",
    )


@pytest.fixture
def synthetic(bundle):
    """The synthetic catalog: three self-registering records."""
    data = _bundle()
    data["apps"] = [
        _synthetic_app(CAPPED, "Tally Tasks"),
        _synthetic_app(OPEN, "Plume Notes"),
        _synthetic_app(APART, "Quill CRM"),
    ]
    data["providers"] = [
        _sign_in(CAPPED, CAPPED_SCOPES),
        _sign_in(OPEN, []),
        _sign_in(APART, APART_SCOPES),
    ]
    return bundle(data)


def _serve(monkeypatch, org, server_id, url, issuer, offered, **vendor):
    """The org's one per-person server ``server_id`` at ``url``, added from
    the catalog's ``server_id/mcp`` record (by its URL for BY_URL), and the
    vendor its discovery reaches."""
    entry = {"url": url, "transport": "http", "credential": {"mode": "per_user"}}
    if server_id != BY_URL:
        version = catalog.load_bundle().providers[(server_id, "mcp")]["version"]
        entry["catalog_ref"] = f"{server_id}/mcp@{version}"
    _seed(org, {"enabled": True, "servers": {server_id: entry}})
    fake = Vendor(url, issuer, offered, **vendor)
    monkeypatch.setattr("agentcc.services.mcp_safe_fetch.fetch", fake.fetch)
    return fake


def _start(client, server_id):
    return client.post(START.format(server_id), {}, format="json")


def _asked(response):
    """The scopes the started sign-in's authorize request asks for."""
    assert response.status_code == 200, response.json()
    url = response.json()["result"]["authorize_url"]
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    return query.get("scope", "").split()


@pytest.mark.usefixtures("synthetic")
class TestTheRecordsScopes:
    def test_the_sign_in_asks_for_the_records_scopes_the_server_offers(
        self, auth_client, user, monkeypatch
    ):
        _serve(monkeypatch, user.organization, CAPPED, _url(CAPPED), ISSUER, OFFERED)

        asked = _asked(_start(auth_client, CAPPED))

        # The record's order; archive:read is not offered, admin and
        # projects:write are not listed.
        assert asked == ["tasks:read", "projects:read", "tasks:write"]
        row = AgentccMCPConnectState.objects.get(server_id=CAPPED)
        assert row.scopes == asked

    def test_a_record_that_lists_no_scopes_asks_for_every_scope_offered(
        self, auth_client, user, monkeypatch
    ):
        _serve(monkeypatch, user.organization, OPEN, _url(OPEN), ISSUER, OFFERED)

        assert _asked(_start(auth_client, OPEN)) == OFFERED

    def test_a_server_added_by_its_url_asks_for_every_scope_offered(
        self, auth_client, user, monkeypatch
    ):
        _serve(monkeypatch, user.organization, BY_URL, _url(BY_URL), ISSUER, OFFERED)

        assert _asked(_start(auth_client, BY_URL)) == OFFERED

    @pytest.mark.parametrize("offered", [OFFERED, None], ids=["others", "none"])
    def test_a_record_whose_scopes_are_not_offered_starts_nothing(
        self, auth_client, user, monkeypatch, offered
    ):
        vendor = _serve(
            monkeypatch,
            user.organization,
            APART,
            _url(APART),
            ISSUER,
            offered,
            cimd=False,
            dcr=True,
        )

        response = _start(auth_client, APART)

        body = response.json()
        assert (response.status_code, body.get("code")) == (
            502,
            "scopes_not_offered",
        ), body
        text = response.content.decode()
        assert REFUSED in text
        assert [s for s in APART_SCOPES + OFFERED if ":" in s and s in text] == []
        assert not [n for n in response.cookies if n.startswith(connect.COOKIE_PREFIX)]
        # Only the two metadata reads: no client registered, nothing stored.
        assert [method for method, _ in vendor.requests] == ["GET", "GET"]
        assert vendor.registrations == []
        assert not AgentccMCPUpstreamClient.objects.exists()
        assert not AgentccMCPConnectState.objects.exists()


class TestTheShippedRecords:
    """The Django copy of the catalog, as agentcc-catalog build writes it."""

    def test_deel_asks_for_its_read_scopes_and_the_writes_its_tools_use(
        self, auth_client, user, monkeypatch
    ):
        record = catalog.load_bundle().providers[("deel", "mcp")]
        assert catalog.oauth2_by_discovery(record)
        assert record["auth"]["oauth2"]["scopes"] == DEEL_ASKED
        _serve(
            monkeypatch,
            user.organization,
            "deel",
            DEEL_URL,
            DEEL_ISSUER,
            DEEL_OFFERED,
        )

        asked = _asked(_start(auth_client, "deel"))

        assert asked == DEEL_ASKED
        assert len(asked) == 56 and len(asked) <= 64
        writes = [s for s in asked if not s.endswith(":read")]
        assert sorted(writes) == sorted(DEEL_WRITES)

    def test_pipedrive_asks_for_every_scope_offered_but_admin(
        self, auth_client, user, monkeypatch
    ):
        record = catalog.load_bundle().providers[("pipedrive", "mcp")]
        assert catalog.oauth2_by_discovery(record)
        assert record["auth"]["oauth2"]["scopes"] == PIPEDRIVE_ASKED
        _serve(
            monkeypatch,
            user.organization,
            "pipedrive",
            PIPEDRIVE_URL,
            PIPEDRIVE_ISSUER,
            PIPEDRIVE_OFFERED,
        )

        asked = _asked(_start(auth_client, "pipedrive"))

        assert asked == PIPEDRIVE_ASKED
        assert "admin" not in asked

    def test_ashby_asks_for_every_scope_its_server_offers(
        self, auth_client, user, monkeypatch
    ):
        for part in ("ID", "SECRET"):
            monkeypatch.delenv(f"AGENTCC_CONNECTOR_CLIENT_ASHBY_{part}", raising=False)
        record = catalog.load_bundle().providers[("ashby", "mcp")]
        assert catalog.oauth2_by_discovery(record)
        assert record["auth"]["oauth2"]["scopes"] == ASHBY_ASKED
        _serve(
            monkeypatch,
            user.organization,
            "ashby",
            ASHBY_URL,
            ASHBY_ISSUER,
            ASHBY_OFFERED,
        )
        # Its issuer has a path: answer its metadata where discovery reads it.
        vendor = PathIssuerVendor(
            ASHBY_URL, ASHBY_ISSUER, ASHBY_OFFERED, cimd=False, dcr=True
        )
        monkeypatch.setattr("agentcc.services.mcp_safe_fetch.fetch", vendor.fetch)

        asked = _asked(_start(auth_client, "ashby"))

        assert asked == ASHBY_ASKED
        assert "offline_access" in asked

    def test_todoist_asks_for_the_one_scope_its_record_lists(
        self, auth_client, user, monkeypatch
    ):
        for part in ("ID", "SECRET"):
            monkeypatch.delenv(
                f"AGENTCC_CONNECTOR_CLIENT_TODOIST_{part}", raising=False
            )
        record = catalog.load_bundle().providers[("todoist", "mcp")]
        assert catalog.oauth2_by_discovery(record)
        assert record["auth"]["oauth2"]["scopes"] == TODOIST_ASKED
        _serve(
            monkeypatch,
            user.organization,
            "todoist",
            TODOIST_URL,
            TODOIST_ISSUER,
            TODOIST_OFFERED,
            dcr=True,
        )

        asked = _asked(_start(auth_client, "todoist"))

        assert asked == TODOIST_ASKED

    @pytest.mark.parametrize(
        "app_id, asked",
        [("deel", DEEL_ASKED), ("pipedrive", PIPEDRIVE_ASKED)],
        ids=["deel", "pipedrive"],
    )
    def test_the_docs_card_lists_the_scopes_the_sign_in_asks_for(self, app_id, asked):
        card = (DOCS / f"{app_id}.md").read_text()
        rows = [line for line in card.splitlines() if line.startswith("| Scopes |")]
        assert len(rows) == 1, rows
        assert re.findall(r"`([^`]+)`", rows[0]) == asked
