"""The connector catalog API and "add from catalog" (CN9, CN-ADD).

The bundle is a fixture written for each test and validated against the
published connector-catalog-bundle/v1 and connector-manifest/v1 schemas
(agentcc-gateway/catalog/connectors/schema), so each case controls its own
records; TestTheSiteServesTheRealCatalog reads CN6's generated Django copy.
Config pushes are recorded at the gateway admin client, so the real payload
builder runs. The gateway's probe (POST /-/mcp/probe), which lists a
provider's tools at add, is faked at its HTTP transport, so mcp_probe's own
contract check (MCPProbeResult) reads every answer. The site serves the
routes: agentcc/urls.py mounts them (CN-ADD, MCP-APPS-PLAN section 2.3).
"""

import copy
import hashlib
import json
import logging
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
from django.core.cache import cache
from django.urls import resolve, reverse
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from structlog.testing import capture_logs

from accounts.models import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_catalog as catalog
from agentcc.services.connector_tool_listing import ListedTool
from agentcc.services.credential_manager import encrypt_token
from agentcc.views.mcp_probe import MCPProbeOrgThrottle
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

SCHEMA_DIR = (
    Path(__file__).resolve().parents[3]
    / "agentcc-gateway"
    / "catalog"
    / "connectors"
    / "schema"
)
CATALOG = "/agentcc/connectors/catalog/"
CREDENTIALS = "/agentcc/mcp/credentials/"
ORG_CONFIGS = "/agentcc/org-configs/"
GATEWAY = "/agentcc/gateways/default"
PROBE = "/agentcc/mcp/probe/"
SECRET = "test-cn9-secret-not-a-real-key"
SECRET_2 = "test-cn9-bearer-not-a-real-token"
RESPONSE_DRIFT = "API response does not match declared serializer."
# What the fixture bundle lists: an app with a provider the org can add now.
LISTED = ["acme-docs", "bolt-crm", "cobalt-search", "gale-notes"]
BOLT_VARIABLES = {"accountHost": "a.bolt.example.com", "workspace": "w"}


def _add(app):
    return f"/agentcc/connectors/apps/{app}/add/"


def _key(value=SECRET):
    return {"credential": {"value": value}}


# --- The fixture bundle --------------------------------------------------------


def _app(app_id, name, description, *, category, categories, default, **extra):
    record = {
        "schema": "connector-manifest/v1#app",
        "id": app_id,
        "version": "1.0.0",
        "name": name,
        "description": description,
        "vendor": {
            "name": extra.pop("vendor", name),
            "domains": [f"{app_id}.example.com"],
        },
        "eeCategory": category,
        "group": "other",
        "categories": categories,
        "docsUrl": f"https://docs.{app_id}.example.com/mcp",
        "defaultProvider": default,
        "lifecycle": {"status": "active"},
        "sources": [],
    }
    record.update(extra)
    return record


def _tool(name, *, read_only=True, destructive=False, posture=None, generic=False):
    tool = {
        "name": name,
        "title": name.replace("_", " ").capitalize(),
        "description": f"The {name} tool.",
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": read_only,
            "openWorldHint": True,
        },
    }
    if posture:
        tool["posture"] = posture
    if generic:
        tool["generic"] = True
        tool["genericArguments"] = ["operation"]
    return tool


def _provider(app_id, provider_id, *, auth, tools, transport=None, **extra):
    record = {
        "schema": "connector-manifest/v1#provider",
        "app": app_id,
        "id": provider_id,
        "version": extra.pop("version", "1.0.0"),
        "kind": "vendor_mcp",
        "publisher": "vendor",
        "vendorStatus": {
            "label": "ga",
            "source": f"https://docs.{app_id}.example.com/mcp",
        },
        "transport": transport
        or {"kind": "http", "url": f"https://mcp.{app_id}.example.com/mcp"},
        "hosted": True,
        "trusted": False,
        "accountModes": extra.pop("account_modes", ["agent"]),
        "auth": auth,
        "tools": {
            "source": "upstream_mcp",
            "defaultPosture": extra.pop("default_posture", "ask"),
            "items": tools,
        },
        "pricing": {"model": "vendor_plan"},
        "quality": {"tier": extra.pop("tier", "listed")},
        "lifecycle": {"status": "active"},
        "sources": [],
    }
    record.update(extra)
    return record


def _header_key(name, **part):
    return {
        "kind": "api_key",
        "credentialParts": [{"in": "header", "name": name, "secret": True, **part}],
    }


ACME_TOOLS = [
    _tool("search_pages", posture="auto"),
    _tool("get_page", posture="auto"),
    _tool("list_recent"),
    _tool("create_page", read_only=False, posture="auto"),
    _tool("delete_page", read_only=False, destructive=True, posture="auto"),
    _tool("run_query", posture="auto", generic=True),
    _tool("archive_page", posture="deny"),
]


def _bundle():
    """Five apps an org can add now (one per credential form the gateway
    and the vault apply today: a header key, a bearer, none, a key with a
    constant part the catalog supplies, a key in a query parameter or a URL
    variable) and three it cannot, each for one reason; echo-chat and
    fern-files also have providers that cannot be added, each for one
    reason (IR-CN9-3)."""
    apps = [
        _app(
            "acme-docs",
            "Acme Docs",
            "Team wiki pages: search, read and edit.",
            category="productivity",
            categories=["docs", "knowledge"],
            default="acme-mcp",
            tags=["wiki"],
        ),
        _app(
            "bolt-crm",
            "Bolt CRM",
            "Leads, deals and accounts for sales teams.",
            category="data",
            categories=["crm", "sales"],
            default="bolt-mcp",
            vendor="Bolt Labs",
        ),
        _app(
            "cobalt-search",
            "Cobalt Search",
            "Web search with cited sources.",
            category="research",
            categories=["search"],
            default="cobalt-mcp",
        ),
        _app(
            "delta-mail",
            "Delta Mail",
            "Mailboxes over OAuth.",
            category="communication",
            categories=["email"],
            default="delta-mcp",
        ),
        _app(
            "echo-chat",
            "Echo Chat",
            "A key plus a fixed scope header.",
            category="communication",
            categories=["chat"],
            default="echo-mcp",
        ),
        _app(
            "fern-files",
            "Fern Files",
            "A key sent as a query parameter or in the URL.",
            category="productivity",
            categories=["files"],
            default="fern-mcp",
        ),
        _app(
            "gale-notes",
            "Gale Notes",
            "Notes whose tools all need approval or write.",
            category="productivity",
            categories=["notes"],
            default="gale-mcp",
        ),
        _app(
            "hydra-db",
            "Hydra DB",
            "A per-user instance host.",
            category="data",
            categories=["database"],
            default="hydra-mcp",
        ),
    ]
    providers = [
        _provider(
            "acme-docs",
            "acme-mcp",
            version="1.2.0",
            account_modes=["agent", "user"],
            auth=_header_key(
                "X-Acme-Key",
                label="API key",
                help="Create a key under Settings, API keys.",
                helpUrl="https://docs.acme-docs.example.com/keys",
            ),
            tools=copy.deepcopy(ACME_TOOLS),
            **{"x-gateway": {"timeoutMs": 20000}},
        ),
        _provider(
            "acme-docs",
            "acme-local",
            kind="vendor_local",
            transport={"kind": "stdio", "command": "acme-mcp"},
            hosted=False,
            auth={"kind": "none"},
            tools=[_tool("search_pages", posture="auto")],
        ),
        _provider(
            "bolt-crm",
            "bolt-mcp",
            transport={
                "kind": "http",
                "url": "https://{accountHost}/mcp/{workspace}?team={team}",
                "urlVariables": {
                    "accountHost": {
                        "description": "Your account host, such as acme.bolt.example.com.",
                        "pattern": "^[a-z0-9-]+[.]bolt[.]example[.]com$",
                        "scope": "org",
                    },
                    "workspace": {
                        "description": "The workspace to reach.",
                        "pattern": "^[A-Za-z0-9_:]{1,40}$",
                        "scope": "org",
                    },
                    "team": {
                        "description": "The team.",
                        "pattern": "^[a-z]{2,20}$",
                        "default": "main",
                        "scope": "org",
                    },
                },
            },
            auth=_header_key("Authorization", prefix="Bearer ", label="Access token"),
            default_posture="auto",
            tools=[_tool("list_deals"), _tool("update_deal", read_only=False)],
        ),
        _provider(
            "cobalt-search",
            "cobalt-mcp",
            tier="verified",
            transport={
                "kind": "http",
                "url": "https://mcp.cobalt-search.example.com/sse",
                "variant": "sse",
            },
            auth={"kind": "none"},
            default_posture="auto",
            tools=[_tool("web_search")],
        ),
        _provider(
            "delta-mail",
            "delta-mcp",
            account_modes=["user"],
            auth={
                "kind": "oauth2",
                "oauth2": {
                    "authorizeUrl": "https://auth.delta-mail.example.com/authorize",
                    "tokenUrl": "https://auth.delta-mail.example.com/token",
                    "scopes": ["mail.read"],
                    "pkce": True,
                    "tokenBody": "form",
                    "clientAuth": "body",
                },
            },
            default_posture="auto",
            tools=[_tool("search_mail")],
        ),
        _provider(
            "echo-chat",
            "echo-mcp",
            auth={
                "kind": "api_key",
                "credentialParts": [
                    {"in": "header", "name": "Echo-Key", "secret": True},
                    {
                        "in": "header",
                        "name": "Echo-Scope",
                        "secret": False,
                        "value": "read",
                    },
                ],
            },
            default_posture="auto",
            tools=[_tool("list_channels")],
        ),
        _provider(
            "fern-files",
            "fern-mcp",
            auth={
                "kind": "api_key",
                "credentialParts": [{"in": "query", "name": "api_key", "secret": True}],
            },
            default_posture="auto",
            tools=[_tool("list_files")],
        ),
        _provider(
            "fern-files",
            "fern-path",
            transport={
                "kind": "http",
                "url": "https://mcp.fern-files.example.com/{apiKey}/mcp?region={region}",
                "urlVariables": {
                    "region": {
                        "description": "Your region.",
                        "pattern": "^[a-z]{2}$",
                        "default": "us",
                        "scope": "org",
                    }
                },
            },
            auth={
                "kind": "api_key",
                "credentialParts": [
                    {"in": "url_variable", "name": "apiKey", "secret": True}
                ],
            },
            default_posture="auto",
            tools=[_tool("list_files")],
        ),
        # What the gateway or the vault cannot take yet, one reason each.
        _provider(
            "echo-chat",
            "echo-two-keys",
            auth={
                "kind": "api_key",
                "credentialParts": [
                    {"in": "header", "name": "Echo-Key", "secret": True},
                    {"in": "header", "name": "Echo-App-Key", "secret": True},
                ],
            },
            default_posture="auto",
            tools=[_tool("list_channels")],
        ),
        _provider(
            "echo-chat",
            "echo-own-scope",
            auth={
                "kind": "api_key",
                "credentialParts": [
                    {"in": "header", "name": "Echo-Key", "secret": True},
                    {"in": "header", "name": "Echo-Scope", "secret": False},
                ],
            },
            default_posture="auto",
            tools=[_tool("list_channels")],
        ),
        _provider(
            "echo-chat",
            "echo-reserved",
            auth={
                "kind": "api_key",
                "credentialParts": [
                    {"in": "header", "name": "Echo-Key", "secret": True},
                    {
                        "in": "header",
                        "name": "X-Agentcc-Scope",
                        "secret": False,
                        "value": "read",
                    },
                ],
            },
            default_posture="auto",
            tools=[_tool("list_channels")],
        ),
        _provider(
            "fern-files",
            "fern-host",
            transport={
                "kind": "http",
                "url": "https://{apiKey}.fern-files.example.com/mcp",
            },
            auth={
                "kind": "api_key",
                "credentialParts": [
                    {"in": "url_variable", "name": "apiKey", "secret": True}
                ],
            },
            default_posture="auto",
            tools=[_tool("list_files")],
        ),
        _provider(
            "gale-notes",
            "gale-mcp",
            auth={"kind": "none"},
            tools=[
                _tool("list_notes"),
                _tool("write_note", read_only=False, posture="auto"),
            ],
        ),
        _provider(
            "hydra-db",
            "hydra-mcp",
            transport={
                "kind": "http",
                "url": "https://{instance}.hydra-db.example.com/mcp",
                "urlVariables": {
                    "instance": {
                        "description": "Your own instance.",
                        "pattern": "^[a-z0-9-]{1,30}$",
                        "scope": "user",
                    }
                },
            },
            auth={"kind": "none"},
            default_posture="auto",
            tools=[_tool("query")],
        ),
    ]
    return {
        "schema": "connector-catalog-bundle/v1",
        "version": "2026.10.20.1",
        "sha256": "5" * 64,
        "sourceCommit": "06e27ec2d88b0367a21900e7f477c8df0aab49e7",
        "apps": apps,
        "providers": providers,
        "platforms": [],
    }


# --- The gateway's probe -------------------------------------------------------


def _probe_tool(tool, **hints):
    """A fixture tool as mcp-probe-v1 reports it (MCPProbeTool); ``hints``
    replace or (as None) drop a hint."""
    annotations = tool["annotations"]
    listed = {
        "name": tool["name"],
        "title": tool["title"],
        "description": tool["description"],
        "read_only_hint": annotations["readOnlyHint"],
        "destructive_hint": annotations["destructiveHint"],
        "idempotent_hint": annotations["idempotentHint"],
        "open_world_hint": annotations["openWorldHint"],
    }
    listed.update(hints)
    return {key: value for key, value in listed.items() if value is not None}


# What each fixture provider lists when it is added, by its URL's host (the
# bolt host is the org's own, so a key matches a host and its subdomains):
# the tools it declares, as the probe reports them.
LISTINGS = {
    "mcp.acme-docs.example.com": [_probe_tool(tool) for tool in ACME_TOOLS],
    "bolt.example.com": [
        _probe_tool(_tool("list_deals")),
        _probe_tool(_tool("update_deal", read_only=False)),
    ],
    "mcp.cobalt-search.example.com": [_probe_tool(_tool("web_search"))],
    "mcp.gale-notes.example.com": [
        _probe_tool(_tool("list_notes")),
        _probe_tool(_tool("write_note", read_only=False)),
    ],
}


def _listing(tools):
    """An ok mcp-probe-v1 result for ``tools``."""
    return {
        "ok": True,
        "era": "2025-11-25",
        "server_info": {"name": "fixture-upstream"},
        "tool_count": len(tools),
        "tools_hash": hashlib.sha256(
            json.dumps(tools, sort_keys=True).encode()
        ).hexdigest(),
        "tools": tools,
        "latency_ms": 12,
        "error": None,
    }


class FakeProbe:
    """The gateway's POST /-/mcp/probe at the admin transport, as
    test_mcp_credentials fakes it: answers each probe with the listing for
    its URL's host (``listings``), or ``answer`` (status, body) when set, and
    records each request body. A shared credential's revoke also reaches the
    gateway's POST /-/mcp/credentials/purge (W4): recorded in ``purges``."""

    def __init__(self):
        self.listings = copy.deepcopy(LISTINGS)
        self.answer = None
        self.calls = []
        self.purges = []

    def request(self, method, url, headers=None, params=None, json=None):
        if (method, urlsplit(url).path) == ("POST", "/-/mcp/credentials/purge"):
            self.purges.append(copy.deepcopy(json))
            return httpx.Response(200, json={"purged": 0})
        assert (method, urlsplit(url).path) == ("POST", "/-/mcp/probe")
        self.calls.append(copy.deepcopy(json))
        if self.answer is not None:
            status, body = self.answer
            return httpx.Response(status, json=body)
        host = urlsplit(json["url"]).hostname
        tools = next(
            (
                tools
                for key, tools in self.listings.items()
                if host == key or host.endswith(f".{key}")
            ),
            [],
        )
        return httpx.Response(200, json=_listing(tools))


def _bundle_validator():
    """The published bundle schema, which refers to the manifest schema by
    its $id; resolved locally, never fetched."""
    documents = [
        json.loads((SCHEMA_DIR / name).read_text())
        for name in ("connector-catalog-bundle.v1.json", "connector-manifest.v1.json")
    ]
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents
    )
    return Draft202012Validator(documents[0], registry=registry)


# --- Fixtures and helpers ------------------------------------------------------


@pytest.fixture(autouse=True)
def probe():
    """The gateway's probe, faked at its HTTP client (FakeProbe)."""
    gateway = FakeProbe()
    client = MagicMock()
    client.__enter__.return_value = gateway
    client.__exit__.return_value = False
    with patch(
        "agentcc.services.gateway_admin.mcp_probe.httpx.Client", return_value=client
    ):
        yield gateway


@pytest.fixture(autouse=True)
def probe_budget(monkeypatch):
    """Adds share the probe's 10 a minute per org; tests send more, so the
    budget is lifted here and TestAddBudget restores it. The throttle's
    counts live in the cache, cleared for each test."""
    cache.clear()
    monkeypatch.setattr(MCPProbeOrgThrottle, "rate", "1000/min")
    yield
    cache.clear()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """Write a bundle (the fixture one by default) where the service reads
    it; each write is a new file, so the reader's cache sees it."""
    validator = _bundle_validator()
    written = []

    def write(data=None, *, raw=None):
        target = tmp_path / f"catalog-{len(written)}.json"
        if raw is not None:
            target.write_text(raw)
        else:
            data = _bundle() if data is None else data
            validator.validate(data)
            target.write_text(json.dumps(data))
        written.append(target)
        monkeypatch.setattr(catalog, "BUNDLE_PATH", target)
        return data

    write()
    return write


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


@pytest.fixture
def drift_free(settings):
    """Fails the test when a response does not match its declared
    serializer. validated_request checks responses only in DEBUG, and only
    logs a mismatch, so this turns DEBUG on and fails on the log."""
    settings.DEBUG = True
    with capture_logs() as logs:
        yield
    drift = [entry for entry in logs if entry.get("event") == RESPONSE_DRIFT]
    assert not drift, drift


def _client_for(org, workspace, *, level, role):
    person = User.objects.create_user(
        email=f"cn9-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="CN9 Person",
        organization=org,
        organization_role=role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person, organization=org, role=role, level=level, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=person,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=person)
    client.set_workspace(workspace)
    return client, person


def _member(org, workspace):
    return _client_for(
        org, workspace, level=Level.MEMBER, role=OrganizationRoles.MEMBER
    )


def _seed(org, mcp):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, mcp=mcp
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _versions(org):
    return AgentccOrgConfig._base_manager.filter(organization=org).count()


def _ok(response, status=200):
    assert response.status_code == status, response.json()
    return response.json()


def _refused(response, status, code):
    body = response.json()
    assert response.status_code == status, body
    assert body["code"] == code, body
    return body


def _listed(client, **query):
    body = _ok(client.get(CATALOG, query))
    return [app["id"] for app in body["results"]]


def _connect(
    client, server_id, *, kind="api_key", header_name="X-Acme-Key", value=SECRET
):
    """Store the shared credential the add response describes, through the
    vault's own endpoint (M14)."""
    return _ok(
        client.post(
            CREDENTIALS,
            {
                "server_id": server_id,
                "kind": kind,
                "header_name": header_name,
                "value": value,
            },
            format="json",
        ),
        201,
    )


# --- Acceptance tests (CONNECTORS-PLAN.md section 3.4, CN9) ---------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free")
class TestConnectorCatalog:
    def test_lists_by_category_and_search(self, auth_client, bundle):
        body = _ok(auth_client.get(CATALOG))
        assert body["catalog"] == {
            "version": "2026.10.20.1",
            "sha256": "5" * 64,
            "source_commit": "06e27ec2d88b0367a21900e7f477c8df0aab49e7",
        }
        # Only what an org can add now is listed, sorted by name; the four
        # apps the gateway cannot run yet are not shown at all, echo-chat and
        # fern-files among them: each of their keys needs a placement the
        # add's listing cannot send (CN-ADD).
        assert [app["id"] for app in body["results"]] == LISTED
        assert body["count"] == 4
        acme = body["results"][0]
        # Its stdio provider is not listed: only the vendor's remote server is.
        assert [provider["id"] for provider in acme["providers"]] == ["acme-mcp"]
        assert acme["default_provider"] == "acme-mcp"
        assert (acme["ee_category"], acme["categories"], acme["tags"]) == (
            "productivity",
            ["docs", "knowledge"],
            ["wiki"],
        )
        assert acme["connected"] is False and acme["servers"] == []

        # category matches the EE category or an open category facet.
        assert _listed(auth_client, category="data") == ["bolt-crm"]
        assert _listed(auth_client, category="docs") == ["acme-docs"]
        assert _listed(auth_client, category="communication") == []
        assert _listed(auth_client, category="notes") == ["gale-notes"]
        assert _listed(auth_client, category="email") == []
        # q searches the name, description, tags and vendor, case-insensitively.
        assert _listed(auth_client, q="BOLT") == ["bolt-crm"]
        assert _listed(auth_client, q="wiki") == ["acme-docs"]
        assert _listed(auth_client, q="bolt labs") == ["bolt-crm"]
        assert _listed(auth_client, q="cited sources") == ["cobalt-search"]
        assert _listed(auth_client, q="no such thing") == []
        assert _listed(auth_client, q="search", category="research") == [
            "cobalt-search"
        ]
        # kind and tier narrow by provider.
        assert _listed(auth_client, kind="vendor_mcp") == LISTED
        assert _listed(auth_client, kind="vendor_local") == []
        assert _listed(auth_client, tier="verified") == ["cobalt-search"]
        assert _listed(auth_client, connected="true") == []
        assert _listed(auth_client, connected="false") == LISTED
        # Paginated.
        page = _ok(auth_client.get(CATALOG, {"limit": 2, "page": 2}))
        assert (page["count"], page["total_pages"], page["current_page"]) == (4, 2, 2)
        assert [app["id"] for app in page["results"]] == ["cobalt-search", "gale-notes"]
        # Bad filters are refused, never ignored.
        for query in (
            {"kind": "server"},
            {"tier": "gold"},
            {"connected": "yes"},
            {"category": "Not A Slug"},
            {"limit": 500},
            {"sort": "name"},
        ):
            assert auth_client.get(CATALOG, query).status_code == 400, query

        # One app, and the apps the catalog does not list.
        detail = _ok(auth_client.get(f"{CATALOG}bolt-crm/"))["result"]
        assert detail["id"] == "bolt-crm"
        provider = detail["providers"][0]
        assert provider["catalog_ref"] == "bolt-crm/bolt-mcp@1.0.0"
        assert provider["credential"] == {
            "mode": "shared",
            "kind": "bearer",
            "header_name": "",
            "in": "header",
            "name": "Authorization",
            "label": "Access token",
            "help": "",
            "help_url": "",
        }
        assert [v["name"] for v in provider["url_variables"]] == [
            "accountHost",
            "workspace",
            "team",
        ]
        # gale-notes declares no tool the gateway runs, but the catalog does
        # not deny the tools it leaves undeclared, so its live listing decides
        # at add, and it is listed.
        gale = _ok(auth_client.get(f"{CATALOG}gale-notes/"))["result"]
        assert [p["id"] for p in gale["providers"]] == ["gale-mcp"]
        for app_id in (
            "delta-mail",
            "echo-chat",
            "fern-files",
            "hydra-db",
            "no-such-app",
            "Bad_Id",
        ):
            _refused(
                auth_client.get(f"{CATALOG}{app_id}/"), 404, "connector_app_not_found"
            )

    def test_connected_reflects_catalog_ref_and_user_rows(
        self, auth_client, user, workspace, bundle, pushes, probe
    ):
        org = user.organization
        _seed(
            org,
            {
                "enabled": True,
                "servers": {
                    "acme-docs": {
                        "url": "https://mcp.acme-docs.example.com/mcp",
                        "transport": "http",
                        "catalog_ref": "acme-docs/acme-mcp@1.2.0",
                        "credential": {"mode": "shared"},
                    },
                    "bolt-crm": {
                        "url": "https://acme.bolt.example.com/mcp/sales?team=main",
                        "transport": "http",
                        "catalog_ref": "bolt-crm/bolt-mcp@1.0.0",
                        "credential": {"mode": "per_user"},
                    },
                    "cobalt-search": {
                        "url": "https://mcp.cobalt-search.example.com/sse",
                        "transport": "sse",
                        "catalog_ref": "cobalt-search/cobalt-mcp@1.0.0",
                        "credential": {"mode": "none"},
                        "enabled": False,
                    },
                    # Same URL, no catalog_ref: a custom server, not a connector.
                    "wiki": {
                        "url": "https://mcp.acme-docs.example.com/mcp",
                        "transport": "http",
                    },
                    # A connector the org added is shown with its server.
                    "gale-notes": {
                        "url": "https://mcp.gale-notes.example.com/mcp",
                        "transport": "http",
                        "catalog_ref": "gale-notes/gale-mcp@1.0.0",
                        "credential": {"mode": "none"},
                    },
                    # So is one the catalog no longer lists (no provider of
                    # it can be added now), so it can be removed.
                    "echo-chat": {
                        "url": "https://mcp.echo-chat.example.com/mcp",
                        "transport": "http",
                        "catalog_ref": "echo-chat/echo-mcp@1.0.0",
                        "credential": {"mode": "shared"},
                    },
                },
            },
        )
        member_client, member = _member(org, workspace)

        def state(client):
            body = _ok(client.get(CATALOG))
            return {
                app["id"]: (app["connected"], [s["connected"] for s in app["servers"]])
                for app in body["results"]
            }

        # The shared server has no credential yet; the per-user one has no
        # row for anyone; the disabled no-auth one is not connected; the
        # no-auth one that is enabled is; the apps the org has not added
        # have no server.
        assert state(auth_client) == {
            "acme-docs": (False, [False]),
            "bolt-crm": (False, [False]),
            "cobalt-search": (False, [False]),
            "echo-chat": (False, [False]),
            "gale-notes": (True, [True]),
        }
        echo = _ok(auth_client.get(f"{CATALOG}echo-chat/"))["result"]
        assert (echo["providers"], echo["default_provider"]) == ([], None)
        server = _ok(auth_client.get(f"{CATALOG}acme-docs/"))["result"]["servers"][0]
        assert server == {
            "server_id": "acme-docs",
            "catalog_ref": "acme-docs/acme-mcp@1.2.0",
            "provider": "acme-mcp",
            "version": "1.2.0",
            "credential_mode": "shared",
            "enabled": True,
            "connected": False,
            "virtual_server": None,
        }

        # A shared credential the config references makes it connected for
        # everyone in the org.
        _connect(auth_client, "acme-docs")
        assert state(auth_client)["acme-docs"] == (True, [True])
        assert state(member_client)["acme-docs"] == (True, [True])
        assert _listed(auth_client, connected="true") == ["acme-docs", "gale-notes"]

        # A per-user server is connected for a user with an active row of
        # their own, and for nobody else.
        row = AgentccMCPCredential.objects.create(
            organization=org,
            server_id="bolt-crm",
            scope=AgentccMCPCredential.Scope.USER,
            user=member,
            kind=AgentccMCPCredential.Kind.BEARER,
            encrypted_secret=encrypt_token("test-cn9-user-token-not-real"),
        )
        assert state(member_client)["bolt-crm"] == (True, [True])
        assert state(auth_client)["bolt-crm"] == (False, [False])
        row.status = AgentccMCPCredential.Status.REVOKED
        row.save(update_fields=["status"])
        assert state(member_client)["bolt-crm"] == (False, [False])

        # Revoking the shared credential disconnects the app again.
        credential = AgentccMCPCredential.objects.get(
            organization=org, server_id="acme-docs", status="active"
        )
        _ok(auth_client.delete(f"{CREDENTIALS}{credential.pk}/"))
        assert state(auth_client)["acme-docs"] == (False, [False])
        # ...and evicts its grant revision on every gateway replica.
        assert probe.purges[-1] == {
            "org_id": str(org.pk),
            "server_id": "acme-docs",
            "revision": credential.grant_revision,
        }

    @pytest.mark.parametrize("kind", ["bearer", "api_key", "header"])
    def test_personal_static_credentials_keep_catalogue_connection_state(
        self, auth_client, user, workspace, bundle, kind
    ):
        org = user.organization
        _seed(
            org,
            {
                "enabled": True,
                "servers": {
                    "bolt-crm": {
                        "url": "https://acme.bolt.example.com/mcp/sales?team=main",
                        "transport": "http",
                        "catalog_ref": "bolt-crm/bolt-mcp@1.0.0",
                        "credential": {"mode": "per_user"},
                    }
                },
            },
        )
        member_client, member = _member(org, workspace)
        credential = AgentccMCPCredential.objects.create(
            organization=org,
            server_id="bolt-crm",
            scope=AgentccMCPCredential.Scope.USER,
            user=member,
            kind=kind,
            encrypted_secret=encrypt_token("test-k2-personal-static-not-real"),
        )

        def detail(client):
            return _ok(client.get(f"{CATALOG}bolt-crm/"))["result"]

        mine = detail(member_client)
        assert mine["connected"] is True
        assert mine["servers"][0]["connected"] is True
        assert not mine["servers"][0].get("start_url")
        assert detail(auth_client)["connected"] is False
        listed = _ok(member_client.get(CATALOG))["results"]
        assert next(app for app in listed if app["id"] == "bolt-crm")["connected"]

        credential.status = AgentccMCPCredential.Status.REVOKED
        credential.save(update_fields=["status"])
        assert detail(member_client)["connected"] is False

    def test_add_writes_server_with_catalog_ref_admin_only(
        self, auth_client, user, workspace, bundle, pushes, probe
    ):
        org = user.organization
        before = _versions(org)
        result = _ok(
            auth_client.post(
                _add("acme-docs"),
                {"provider": "acme-mcp", "mode": "agent", **_key()},
                format="json",
            ),
            201,
        )["result"]

        # The provider's tools were listed once, through the gateway's probe,
        # with the org's key in the header the catalog names (CN-ADD).
        assert probe.calls == [
            {
                "org_id": str(org.pk),
                "url": "https://mcp.acme-docs.example.com/mcp",
                "transport": "http",
                "auth": {"type": "api_key", "header": "X-Acme-Key", "value": SECRET},
            }
        ]
        # One version holds the server, its credential and its virtual server.
        assert _versions(org) == before + 1
        row = AgentccMCPCredential.objects.get(
            organization=org, server_id="acme-docs", status="active"
        )
        assert (row.scope, row.kind, row.header_name) == (
            "shared",
            "api_key",
            "X-Acme-Key",
        )
        # Bound to the origin the key was listed at (IC-R1-12), or the push
        # would hold it back.
        assert row.origin == "https://mcp.acme-docs.example.com:443"
        mcp = _active(org).mcp
        vs_id = mcp["virtual_servers"]["acme-docs"]["id"]
        assert vs_id.startswith("vs_")
        assert mcp["enabled"] is True
        assert mcp["servers"]["acme-docs"] == {
            "url": "https://mcp.acme-docs.example.com/mcp",
            "transport": "http",
            "era": "auto",
            "catalog_ref": "acme-docs/acme-mcp@1.2.0",
            "credential": {
                "mode": "shared",
                "credential_id": str(row.pk),
                "state": "active",
            },
            "timeout_ms": 20000,
        }
        assert mcp["virtual_servers"]["acme-docs"]["members"] == ["acme-docs"]
        assert _active(org).change_description == (
            "Add connector acme-docs/acme-mcp@1.2.0 from the catalog: 7 tools "
            f"listed, tools_hash {result['baseline']['tools_hash']}"
        )

        # The gateway was sent exactly that, with the key in that header (no
        # direct push: the config writer pushed the version it saved).
        pushed = pushes[-1][1]["mcp"]
        assert pushed["servers"]["acme-docs"]["credential"]["secret"] == {
            "type": "api_key",
            "header": "X-Acme-Key",
            "value": SECRET,
        }
        assert pushed["servers"]["acme-docs"]["catalog_ref"] == (
            "acme-docs/acme-mcp@1.2.0"
        )
        assert pushed["virtual_servers"]["acme-docs"]["id"] == vs_id
        assert pushed["enabled"] is True

        assert result["app"] == "acme-docs" and result["provider"] == "acme-mcp"
        assert result["catalog_ref"] == "acme-docs/acme-mcp@1.2.0"
        assert result["server_id"] == "acme-docs" and result["mode"] == "agent"
        assert result["virtual_server"] == {"slug": "acme-docs", "id": vs_id}
        assert result["mcp_enabled"] is True and result["gateway_synced"] is True
        assert result["version"] == _active(org).version
        assert result["credential"] == {
            "mode": "shared",
            "kind": "api_key",
            "header_name": "X-Acme-Key",
            "in": "header",
            "name": "X-Acme-Key",
            "label": "API key",
            "help": "Create a key under Settings, API keys.",
            "help_url": "https://docs.acme-docs.example.com/keys",
            "connected": True,
        }
        listed = probe.listings["mcp.acme-docs.example.com"]
        assert result["baseline"] == {
            "tool_count": 7,
            "tools_hash": _listing(listed)["tools_hash"],
        }
        assert [tool["name"] for tool in result["tools"]] == [
            tool["name"] for tool in listed
        ]
        assert _ok(auth_client.get(f"{CATALOG}acme-docs/"))["result"]["connected"]

        # The provider choice sticks: a second add is refused, and so is an
        # add over a server id a custom server already uses, each before the
        # provider is called.
        _refused(
            auth_client.post(_add("acme-docs"), _key(), format="json"),
            409,
            "connector_already_added",
        )
        mcp = _active(org).mcp
        mcp["servers"]["bolt-crm"] = {"url": "https://bolt.example.com/mcp"}
        AgentccOrgConfig.no_workspace_objects.filter(pk=_active(org).pk).update(mcp=mcp)
        _refused(
            auth_client.post(
                _add("bolt-crm"),
                {"url_variables": BOLT_VARIABLES, **_key(SECRET_2)},
                format="json",
            ),
            409,
            "connector_server_id_taken",
        )
        assert len(probe.calls) == 1

        # An org Admin may add (the no-credential provider) ...
        admin_client, _ = _client_for(
            org, workspace, level=Level.ADMIN, role=OrganizationRoles.ADMIN
        )
        _ok(
            admin_client.post(
                _add("cobalt-search"), {"provider": "cobalt-mcp"}, format="json"
            ),
            201,
        )
        # ... and a member may not, nor reach the provider.
        member_client, _ = _member(org, workspace)
        version = _versions(org)
        response = member_client.post(_add("gale-notes"), {}, format="json")
        assert response.status_code == 403
        assert _versions(org) == version and len(probe.calls) == 2

    def test_url_variables_checked_against_pattern(
        self, auth_client, user, bundle, pushes, probe
    ):
        org = user.organization
        good = {"accountHost": "acme.bolt.example.com", "workspace": "sales:emea"}

        def add(variables):
            return auth_client.post(
                _add("bolt-crm"),
                {"url_variables": variables, **_key(SECRET_2)},
                format="json",
            )

        before = _versions(org)
        for variables, code in (
            ({"workspace": "sales"}, "url_variable_missing"),
            ({**good, "accountHost": "evil.example.com"}, "url_variable_invalid"),
            (
                {**good, "accountHost": "acme.bolt.example.com.evil.example"},
                "url_variable_invalid",
            ),
            ({**good, "workspace": "two words"}, "url_variable_invalid"),
            ({**good, "workspace": "x" * 41}, "url_variable_invalid"),
            ({**good, "workspace": "sales/../admin"}, "url_variable_invalid"),
            ({**good, "team": "Main"}, "url_variable_invalid"),
            ({**good, "region": "eu"}, "url_variable_unknown"),
        ):
            _refused(add(variables), 400, code)
        # Refused before the provider is called.
        assert _versions(org) == before and pushes == [] and probe.calls == []
        # A non-string value is refused by the request contract.
        assert add({**good, "workspace": 7}).status_code == 400

        result = _ok(add(good), 201)["result"]
        # The host takes the value as is; the path and query take it
        # percent-encoded; an unset variable takes its default. The listing
        # was taken at that URL, with the bearer token.
        url = "https://acme.bolt.example.com/mcp/sales%3Aemea?team=main"
        values = {**good, "team": "main"}
        assert (result["url"], result["url_variables"]) == (url, values)
        assert [(call["url"], call["auth"]) for call in probe.calls] == [
            (url, {"type": "bearer", "header": "Authorization", "value": SECRET_2})
        ]
        server = _active(org).mcp["servers"]["bolt-crm"]
        assert (server["url"], server["url_variables"]) == (url, values)
        assert (server["credential"]["mode"], server["credential"]["state"]) == (
            "shared",
            "active",
        )
        assert pushes[-1][1]["mcp"]["servers"]["bolt-crm"]["credential"]["secret"] == {
            "type": "bearer",
            "header": "Authorization",
            "value": SECRET_2,
        }

    def test_write_tools_default_deny_before_approvals(
        self, auth_client, user, bundle, pushes, probe
    ):
        org = user.organization
        detail = _ok(auth_client.get(f"{CATALOG}acme-docs/"))["result"]
        policy = {
            tool["name"]: (tool["gateway_posture"], tool["reason"])
            for tool in detail["providers"][0]["tools"]
        }
        assert policy == {
            "search_pages": ("allow", None),
            "get_page": ("allow", None),
            # ask needs approvals (M26), which the gateway does not run yet.
            "list_recent": ("deny", "approval_not_enforced"),
            "create_page": ("deny", "write_tool"),
            "delete_page": ("deny", "destructive_tool"),
            "run_query": ("deny", "generic_tool"),
            "archive_page": ("deny", "posture_deny"),
        }

        # The listing names the declared tools: each keeps the posture the
        # catalog declares, which the listed annotations agree with.
        result = _ok(auth_client.post(_add("acme-docs"), _key(), format="json"), 201)
        assert {
            tool["name"]: (tool["gateway_posture"], tool["reason"])
            for tool in result["result"]["tools"]
        } == policy
        vs = _active(org).mcp["virtual_servers"]["acme-docs"]
        # Only the allowed tools are composed (a tool the upstream adds later
        # is left out too), and every tool the posture denies is refused by a
        # rule for every principal, so widening the include list later does
        # not let them through.
        assert vs["tools"] == {
            "include": ["acme-docs/get_page", "acme-docs/search_pages"]
        }
        assert vs["access"] == {
            "rules": [
                {
                    "match": {},
                    "deny": [
                        "acme-docs/archive_page",
                        "acme-docs/create_page",
                        "acme-docs/delete_page",
                        "acme-docs/list_recent",
                        "acme-docs/run_query",
                    ],
                }
            ]
        }
        assert "approval" not in vs
        # A provider whose listed tools are all denied is refused at add: it
        # would expose no tool. Nothing is written.
        version = _versions(org)
        _refused(
            auth_client.post(_add("gale-notes"), {}, format="json"),
            409,
            "connector_no_tool_allowed",
        )
        assert _versions(org) == version
        assert "gale-notes" not in _active(org).mcp["servers"]
        # One whose write tool is denied keeps its read tool.
        _ok(
            auth_client.post(
                _add("bolt-crm"),
                {"url_variables": BOLT_VARIABLES, **_key(SECRET_2)},
                format="json",
            ),
            201,
        )
        vs = _active(org).mcp["virtual_servers"]["bolt-crm"]
        assert vs["tools"] == {"include": ["bolt-crm/list_deals"]}
        assert vs["access"]["rules"][0]["deny"] == ["bolt-crm/update_deal"]

    def test_listed_tools_set_the_posture(
        self, auth_client, user, bundle, pushes, probe
    ):
        """CN-ADD: the live listing decides each tool the catalog does not
        declare. A read-only tool is allowed; a write or destructive tool,
        or one whose hints say neither (MCP's defaults), is denied; a hint
        counts only as a JSON boolean; a name outside MCP's rule is written
        nowhere; a name listed twice is denied if either copy is."""
        org = user.organization
        read = _tool("export_pages")
        probe.listings["mcp.acme-docs.example.com"] += [
            _probe_tool(read),
            _probe_tool(
                _tool("purge"),
                read_only_hint=None,
                destructive_hint=None,
                idempotent_hint=None,
                open_world_hint=None,
            ),
            _probe_tool(_tool("rename", read_only=False)),
            _probe_tool(_tool("wipe", read_only=False, destructive=True)),
            _probe_tool(_tool("quoted"), read_only_hint="true"),
            _probe_tool(_tool("odd_name"), name="odd*name"),
            _probe_tool(_tool("dup")),
            _probe_tool(_tool("dup", read_only=False)),
            # The catalog declares archive_page deny: listed read-only, it
            # stays denied.
            _probe_tool(_tool("archive_page")),
        ]
        result = _ok(auth_client.post(_add("acme-docs"), _key(), format="json"), 201)
        tools = {
            tool["name"]: (tool["gateway_posture"], tool["reason"])
            for tool in result["result"]["tools"]
        }
        assert {
            name: tools[name]
            for name in (
                "export_pages",
                "purge",
                "rename",
                "wipe",
                "quoted",
                "odd*name",
                "archive_page",
            )
        } == {
            "export_pages": ("allow", None),
            "purge": ("deny", "destructive_tool"),
            "rename": ("deny", "write_tool"),
            "wipe": ("deny", "destructive_tool"),
            "quoted": ("deny", "write_tool"),
            "odd*name": ("deny", "invalid_tool_name"),
            "archive_page": ("deny", "posture_deny"),
        }
        purge = next(t for t in result["result"]["tools"] if t["name"] == "purge")
        assert (
            purge["read_only"],
            purge["destructive"],
            purge["idempotent"],
            purge["open_world"],
        ) == (False, True, False, True)
        vs = _active(org).mcp["virtual_servers"]["acme-docs"]
        assert vs["tools"]["include"] == [
            "acme-docs/export_pages",
            "acme-docs/get_page",
            "acme-docs/search_pages",
        ]
        deny = vs["access"]["rules"][0]["deny"]
        assert {"acme-docs/dup", "acme-docs/purge", "acme-docs/quoted"} <= set(deny)
        assert not any("*" in name for name in deny + vs["tools"]["include"])
        assert result["result"]["baseline"]["tool_count"] == 16

    def test_a_catalog_deny_default_denies_the_undeclared(
        self, auth_client, user, bundle, pushes, probe
    ):
        """A section whose defaultPosture is deny denies every listed tool
        it does not declare; with none it declares allowed, the provider is
        not addable at all. The not-addable case is read first: once the org
        has added the app, the catalog lists it as connected (and
        removable), whatever its provider's postures."""
        data = _bundle()
        acme = data["providers"][0]
        assert acme["id"] == "acme-mcp"
        acme["tools"]["defaultPosture"] = "deny"

        none_allowed = copy.deepcopy(data)
        for tool in none_allowed["providers"][0]["tools"]["items"]:
            tool["posture"] = "deny"
        bundle(none_allowed)
        _refused(
            auth_client.get(f"{CATALOG}acme-docs/"), 404, "connector_app_not_found"
        )

        bundle(data)
        probe.listings["mcp.acme-docs.example.com"].append(
            _probe_tool(_tool("export_pages"))
        )
        result = _ok(auth_client.post(_add("acme-docs"), _key(), format="json"), 201)
        tools = {t["name"]: t["reason"] for t in result["result"]["tools"]}
        assert tools["export_pages"] == "posture_deny"
        assert tools["search_pages"] is None

    def test_member_cannot_add(self, user, workspace, bundle, pushes):
        org = user.organization
        _seed(
            org,
            {
                "enabled": True,
                "servers": {
                    "cobalt-search": {
                        "url": "https://mcp.cobalt-search.example.com/sse",
                        "transport": "sse",
                        "catalog_ref": "cobalt-search/cobalt-mcp@1.0.0",
                        "credential": {"mode": "none"},
                    }
                },
            },
        )
        member_client, _ = _member(org, workspace)
        before = _versions(org)
        for response in (
            member_client.post(_add("acme-docs"), {}, format="json"),
            member_client.post(_add("no-such-app"), {}, format="json"),
            member_client.delete(_add("cobalt-search")),
        ):
            assert response.status_code == 403, response.json()
        assert _versions(org) == before and pushes == []
        assert "cobalt-search" in _active(org).mcp["servers"]
        # A member reads the catalog.
        assert _listed(member_client) == LISTED
        assert (
            _ok(member_client.get(f"{CATALOG}acme-docs/"))["result"]["id"]
            == "acme-docs"
        )

    def test_no_secret_in_any_response(
        self, auth_client, user, workspace, bundle, pushes, probe, caplog
    ):
        """The add request carries the key: it reaches the probe and the
        push, and no response, log, refusal or stored config."""
        org = user.organization
        caplog.set_level(logging.DEBUG)
        bodies = []
        with capture_logs() as logs:
            bodies.append(
                _ok(auth_client.post(_add("acme-docs"), _key(), format="json"), 201)
            )
            bodies.append(
                _ok(
                    auth_client.post(
                        _add("bolt-crm"),
                        {
                            "url_variables": {
                                "accountHost": "acme.bolt.example.com",
                                "workspace": "sales",
                            },
                            **_key(SECRET_2),
                        },
                        format="json",
                    ),
                    201,
                )
            )
            member_client, _ = _member(org, workspace)
            for client in (auth_client, member_client):
                bodies.append(_ok(client.get(CATALOG)))
                bodies.append(_ok(client.get(CATALOG, {"connected": "true"})))
                for app_id in ("acme-docs", "bolt-crm"):
                    bodies.append(_ok(client.get(f"{CATALOG}{app_id}/")))
            # Refusals echo no secret either: one taken, a key sent where none
            # is taken, a key the vault refuses, a key the provider refuses,
            # a key in a URL variable, and a member's add.
            bodies.append(
                auth_client.post(_add("acme-docs"), _key(), format="json").json()
            )
            bodies.append(
                auth_client.post(_add("cobalt-search"), _key(), format="json").json()
            )
            bodies.append(
                _refused(
                    auth_client.post(
                        _add("acme-docs"), _key(f" {SECRET}"), format="json"
                    ),
                    400,
                    "connector_credential_invalid",
                )
            )
            bodies.append(
                auth_client.post(
                    _add("gale-notes"),
                    {"url_variables": {"key": SECRET}},
                    format="json",
                ).json()
            )
            bodies.append(
                member_client.post(_add("gale-notes"), _key(), format="json").json()
            )
            bodies.append(_ok(auth_client.delete(_add("acme-docs"))))
            probe.answer = (
                200,
                {"ok": False, "latency_ms": 3, "error": "auth_required"},
            )
            bodies.append(
                _refused(
                    auth_client.post(_add("acme-docs"), _key(), format="json"),
                    400,
                    "connector_credential_rejected",
                )
            )
            bodies.append(_ok(auth_client.delete(_add("bolt-crm"))))

        rendered = json.dumps(bodies, default=str)
        for secret in (SECRET, SECRET_2):
            assert secret not in rendered
            assert secret not in json.dumps(logs, default=str)
            assert secret not in caplog.text
        stored = json.dumps(
            list(
                AgentccOrgConfig._base_manager.filter(organization=org).values_list(
                    "mcp", "change_description"
                )
            )
        )
        assert SECRET not in stored and SECRET_2 not in stored
        # The probe and the push are the places a secret travels, which
        # proves the checks above could have seen one.
        for sent in (json.dumps(probe.calls), json.dumps(pushes)):
            assert SECRET in sent and SECRET_2 in sent


# --- What cannot be added now, the org's MCP switches, and removal --------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free")
class TestConnectorAddRules:
    def test_unavailable_providers_refused_with_their_reason(
        self, auth_client, user, bundle, pushes, probe
    ):
        org = user.organization
        for app_id, provider_id, code in (
            ("delta-mail", None, "connector_oauth_not_built"),
            ("echo-chat", "echo-two-keys", "connector_credential_parts_not_applied"),
            ("echo-chat", "echo-own-scope", "connector_credential_parts_not_applied"),
            (
                "echo-chat",
                "echo-reserved",
                "connector_credential_placement_not_applied",
            ),
            ("fern-files", "fern-host", "connector_credential_placement_not_applied"),
            # The listing at add sends the key in a header, alone (CN-ADD).
            ("echo-chat", None, "connector_tool_listing_not_applied"),
            ("fern-files", None, "connector_tool_listing_not_applied"),
            ("fern-files", "fern-path", "connector_tool_listing_not_applied"),
            ("hydra-db", None, "connector_user_url_variable_not_built"),
            ("acme-docs", "acme-local", "connector_kind_not_served"),
        ):
            data = {"provider": provider_id} if provider_id else {}
            body = _refused(
                auth_client.post(_add(app_id), {**data, **_key()}, format="json"),
                409,
                code,
            )
            assert body["message"]
        _refused(
            auth_client.post(_add("no-such-app"), {}, format="json"),
            404,
            "connector_app_not_found",
        )
        _refused(
            auth_client.post(_add("acme-docs"), {"provider": "nope"}, format="json"),
            404,
            "connector_provider_not_found",
        )
        # Each person's own account needs per-user credentials (M25), which
        # the gateway does not resolve yet; an undeclared mode is invalid.
        _refused(
            auth_client.post(_add("acme-docs"), {"mode": "user"}, format="json"),
            409,
            "connector_per_user_not_built",
        )
        _refused(
            auth_client.post(_add("cobalt-search"), {"mode": "user"}, format="json"),
            400,
            "connector_account_mode_not_declared",
        )
        # The credential: required for a key provider, refused for one that
        # needs none, and checked as the vault checks it.
        for app_id, data, code in (
            ("acme-docs", {}, "connector_credential_required"),
            ("cobalt-search", _key(), "connector_credential_not_taken"),
            ("acme-docs", _key(f"{SECRET}\n"), "connector_credential_invalid"),
            ("acme-docs", _key("x" * 8193), None),
            ("acme-docs", {"credential": {"value": 7}}, None),
            ("acme-docs", {"credential": {}}, None),
            ("acme-docs", {"credential": SECRET}, None),
        ):
            response = auth_client.post(_add(app_id), data, format="json")
            if code is None:
                assert response.status_code == 400, response.json()
            else:
                _refused(response, 400, code)
        for data in ({"mode": "team"}, {"extra": 1}):
            response = auth_client.post(_add("acme-docs"), data, format="json")
            assert response.status_code == 400
        # None of it reached the provider or wrote anything.
        assert probe.calls == [] and pushes == []
        assert not AgentccOrgConfig._base_manager.filter(organization=org).exists()
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()

    @pytest.mark.parametrize(
        "status, body, http_status, code",
        [
            (
                200,
                {"ok": False, "latency_ms": 3, "error": "auth_required"},
                400,
                "connector_credential_rejected",
            ),
            (
                200,
                {"ok": False, "latency_ms": 3, "error": "egress_refused"},
                502,
                "connector_tool_listing_failed",
            ),
            (
                200,
                {"ok": False, "latency_ms": 15000, "error": "timeout"},
                502,
                "connector_tool_listing_failed",
            ),
            # A gateway built before mcp-probe-v1 reported the tools.
            (
                200,
                {
                    "ok": True,
                    "era": "2025-11-25",
                    "tool_count": 2,
                    "tools_hash": "a" * 64,
                    "latency_ms": 9,
                    "error": None,
                },
                502,
                "connector_tool_listing_not_supported",
            ),
            (
                200,
                {**_listing([_probe_tool(_tool("search_pages"))]), "tool_count": 2},
                502,
                "connector_tool_listing_failed",
            ),
            (
                400,
                {"error": "invalid_probe_request", "message": "transport must be http"},
                502,
                "connector_tool_listing_failed",
            ),
            (503, {"error": "unavailable"}, 502, "gateway_unreachable"),
            (200, {"not": "a probe result"}, 502, "gateway_unreachable"),
            # A gateway that runs with MCP off has no /-/mcp/* route, the
            # probe's among them (mcp_admin reads a 404 the same way).
            (404, {"error": "not found"}, 404, "gateway_mcp_disabled"),
        ],
    )
    def test_no_listing_no_add(
        self, auth_client, user, bundle, pushes, probe, status, body, http_status, code
    ):
        """When no listing can be taken, nothing is added: no config version,
        no stored credential, no push."""
        org = user.organization
        probe.answer = (status, body)
        response = auth_client.post(_add("acme-docs"), _key(), format="json")
        _refused(response, http_status, code)
        assert SECRET not in json.dumps(response.json())
        assert len(probe.calls) == 1 and pushes == []
        assert not AgentccOrgConfig._base_manager.filter(organization=org).exists()
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()

    def test_sse_era_and_no_auth_provider(
        self, auth_client, user, bundle, pushes, probe
    ):
        org = user.organization
        result = _ok(auth_client.post(_add("cobalt-search"), {}, format="json"), 201)
        # Listed over HTTP+SSE, the era written, with no credential.
        assert probe.calls == [
            {
                "org_id": str(org.pk),
                "url": "https://mcp.cobalt-search.example.com/sse",
                "transport": "sse",
            }
        ]
        assert not AgentccMCPCredential.objects.filter(organization=org).exists()
        server = _active(org).mcp["servers"]["cobalt-search"]
        assert server == {
            "url": "https://mcp.cobalt-search.example.com/sse",
            "transport": "sse",
            "era": "2024-11-05",
            "catalog_ref": "cobalt-search/cobalt-mcp@1.0.0",
            "credential": {"mode": "none"},
        }
        assert result["result"]["credential"] == {
            "mode": "none",
            "kind": None,
            "header_name": "",
            "in": "",
            "name": "",
            "label": "",
            "help": "",
            "help_url": "",
            "connected": True,
        }
        assert _ok(auth_client.get(f"{CATALOG}cobalt-search/"))["result"]["connected"]

    def test_access_default_allow_refused(
        self, auth_client, user, bundle, pushes, probe
    ):
        org = user.organization
        _seed(org, {"enabled": True, "access": {"default": "allow"}})
        _refused(
            auth_client.post(_add("cobalt-search"), {}, format="json"),
            409,
            "connector_mcp_access_default_allow",
        )
        assert _versions(org) == 1 and pushes == [] and probe.calls == []

    @pytest.mark.parametrize(
        "mcp, code",
        [
            ({"enabled": False}, "connector_mcp_disabled"),
            (
                {"servers": {"wiki": {"url": "https://wiki.example.com/mcp"}}},
                "connector_mcp_disabled",
            ),
            ({"managed_tools": {"enabled": True}}, "connector_mcp_disabled"),
            (
                {"virtualServers": {"team": {"members": []}}},
                "connector_mcp_config_spelling",
            ),
        ],
    )
    def test_mcp_switch_left_to_the_admin(
        self, auth_client, user, bundle, pushes, probe, mcp, code
    ):
        """An org whose MCP is off, or whose MCP setup would be switched on by
        an add, is refused before the provider is called: the gateway runs
        none of its servers unless mcp.enabled is true."""
        org = user.organization
        _seed(org, mcp)
        _refused(auth_client.post(_add("cobalt-search"), {}, format="json"), 409, code)
        assert _versions(org) == 1 and pushes == [] and probe.calls == []

    def test_first_connector_switches_mcp_on(self, auth_client, user, bundle, pushes):
        org = user.organization
        _seed(org, {"access": {"default": "deny"}, "pinning": {"mode": "warn"}})
        _ok(auth_client.post(_add("cobalt-search"), {}, format="json"), 201)
        mcp = _active(org).mcp
        assert mcp["enabled"] is True
        assert mcp["access"] == {"default": "deny"} and mcp["pinning"] == {
            "mode": "warn"
        }
        # Already on: kept, and other servers stay as they were.
        mcp["servers"]["wiki"] = {"url": "https://wiki.example.com/mcp"}
        AgentccOrgConfig.no_workspace_objects.filter(pk=_active(org).pk).update(mcp=mcp)
        _ok(auth_client.post(_add("acme-docs"), _key(), format="json"), 201)
        mcp = _active(org).mcp
        assert mcp["enabled"] is True
        assert mcp["servers"]["wiki"] == {"url": "https://wiki.example.com/mcp"}

    @pytest.mark.parametrize(
        "headers, status", [(["X-Request-Source"], 201), (["Authorization"], 400)]
    )
    def test_forward_headers_from_the_manifest(
        self, auth_client, user, bundle, pushes, headers, status
    ):
        """x-gateway.forwardHeaders is written as forward_headers, and the
        config writer's own rule refuses a credential header there."""
        org = user.organization
        data = _bundle()
        assert data["providers"][3]["id"] == "cobalt-mcp"
        data["providers"][3]["x-gateway"] = {"forwardHeaders": headers}
        bundle(data)
        response = auth_client.post(_add("cobalt-search"), {}, format="json")
        if status == 201:
            _ok(response, 201)
            assert (
                _active(org).mcp["servers"]["cobalt-search"]["forward_headers"]
                == headers
            )
        else:
            _refused(response, 400, "forward_header_refused")
            assert not AgentccOrgConfig._base_manager.filter(organization=org).exists()
            assert pushes == []

    def test_gateway_sync_failure_is_reported(self, auth_client, user, bundle):
        org = user.organization
        client = MagicMock()
        client.set_org_config.side_effect = RuntimeError("gateway down")
        with patch(
            "agentcc.services.config_push.get_gateway_client", return_value=client
        ):
            result = _ok(
                auth_client.post(_add("cobalt-search"), {}, format="json"), 201
            )
        assert result["result"]["gateway_synced"] is False
        assert result["result"]["gateway_warning"]
        assert "cobalt-search" in _active(org).mcp["servers"]

    def test_delete_removes_server_virtual_server_and_credentials(
        self, auth_client, user, bundle, pushes
    ):
        org = user.organization
        _ok(auth_client.post(_add("acme-docs"), _key(), format="json"), 201)
        mcp = _active(org).mcp
        # Another virtual server that also composes the connector.
        mcp["servers"]["wiki"] = {"url": "https://wiki.example.com/mcp"}
        mcp["virtual_servers"]["team"] = {
            "id": "vs_01JBCDEFGHJKMNPQRSTVWXYZ00",
            "members": ["wiki", "acme-docs", "acme-docs@work"],
        }
        AgentccOrgConfig.no_workspace_objects.filter(pk=_active(org).pk).update(mcp=mcp)

        result = _ok(auth_client.delete(_add("acme-docs")))["result"]
        assert result["app"] == "acme-docs"
        assert result["removed_servers"] == ["acme-docs"]
        assert result["removed_virtual_servers"] == ["acme-docs"]
        assert len(result["revoked_credentials"]) == 1
        assert result["gateway_synced"] is True
        mcp = _active(org).mcp
        assert "acme-docs" not in mcp["servers"]
        assert "acme-docs" not in mcp["virtual_servers"]
        assert mcp["virtual_servers"]["team"]["members"] == ["wiki"]
        assert mcp["servers"]["wiki"] == {"url": "https://wiki.example.com/mcp"}
        assert not AgentccMCPCredential.objects.filter(
            organization=org, server_id="acme-docs", status="active"
        ).exists()
        assert "acme-docs" not in pushes[-1][1]["mcp"]["servers"]
        # Nothing left to remove; a custom server of the same id is not a connector.
        _refused(auth_client.delete(_add("acme-docs")), 404, "connector_not_added")
        _refused(auth_client.delete(_add("wiki")), 404, "connector_not_added")
        _refused(auth_client.delete(_add("Bad_Id")), 404, "connector_not_added")

    def test_catalog_unavailable(self, auth_client, user, bundle, pushes, monkeypatch):
        org = user.organization
        _ok(auth_client.post(_add("cobalt-search"), {}, format="json"), 201)
        for raw in ("{not json", json.dumps({"schema": "something-else"})):
            bundle(raw=raw)
            for response in (
                auth_client.get(CATALOG),
                auth_client.get(f"{CATALOG}acme-docs/"),
                auth_client.post(_add("acme-docs"), {}, format="json"),
            ):
                _refused(response, 503, "connector_catalog_unavailable")
        broken = _bundle()
        broken["providers"][0]["app"] = "not-an-app"
        bundle(raw=json.dumps(broken))
        _refused(auth_client.get(CATALOG), 503, "connector_catalog_unavailable")
        monkeypatch.setattr(
            catalog, "BUNDLE_PATH", Path(catalog.BUNDLE_PATH).with_name("missing.json")
        )
        _refused(auth_client.get(CATALOG), 503, "connector_catalog_unavailable")
        # Removal needs no catalog.
        _ok(auth_client.delete(_add("cobalt-search")))
        assert "cobalt-search" not in _active(org).mcp["servers"]


# --- Keys in a query parameter or a URL variable, constant parts (IR-CN9-3) -----


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free")
class TestCatalogCredentialPlacement:
    """CN7's call site and CN11's vault fields landed in the CW1 merge
    (AM-C20 item 2): the push places a catalog server's key in a query
    parameter or a URL variable, and the gateway adds a constant part from
    the catalog entry the server's catalog_ref names. An add lists the
    provider's tools with the key through the probe, which sends one header
    only, so on this build such a provider is planned and checked but not
    addable (connector_tool_listing_not_applied; CN8-IR-2 comes with the
    probe that places a key as the push does). A server of one the org
    already has keeps its placement at the push."""

    def _store(self, client, server_id, **fields):
        return _ok(
            client.post(
                CREDENTIALS,
                {"server_id": server_id, "kind": "api_key", "value": SECRET, **fields},
                format="json",
            ),
            201,
        )["result"]

    def test_listing_needs_a_header_key_alone(
        self, auth_client, user, bundle, pushes, probe
    ):
        org = user.organization
        for app_id, provider_id, needs in (
            ("echo-chat", "echo-mcp", "also needs its fixed Echo-Scope header part"),
            ("fern-files", "fern-mcp", "takes its key in a query parameter"),
            ("fern-files", "fern-path", "takes its key in the URL"),
        ):
            body = _refused(
                auth_client.post(
                    _add(app_id), {"provider": provider_id, **_key()}, format="json"
                ),
                409,
                "connector_tool_listing_not_applied",
            )
            assert body["message"].endswith(f"this provider {needs}."), body
        assert probe.calls == [] and pushes == []
        assert not AgentccOrgConfig._base_manager.filter(organization=org).exists()

    def test_constant_part_comes_from_the_catalog(
        self, auth_client, user, bundle, pushes
    ):
        org = user.organization
        _seed(
            org,
            {
                "enabled": True,
                "servers": {
                    "echo-chat": {
                        "url": "https://mcp.echo-chat.example.com/mcp",
                        "transport": "http",
                        "catalog_ref": "echo-chat/echo-mcp@1.0.0",
                        "credential": {"mode": "shared"},
                    }
                },
            },
        )
        self._store(auth_client, "echo-chat", header_name="Echo-Key")
        pushed = pushes[-1][1]["mcp"]["servers"]["echo-chat"]
        assert pushed["catalog_ref"] == "echo-chat/echo-mcp@1.0.0"
        assert pushed["credential"]["secret"] == {
            "type": "api_key",
            "header": "Echo-Key",
            "value": SECRET,
        }
        # The constant is never pushed or stored: the gateway adds Echo-Scope
        # from echo-chat/echo-mcp's record.
        assert "Echo-Scope" not in json.dumps(pushes)
        assert "Echo-Scope" not in json.dumps(_active(org).mcp)
        # Listed because the org has it, connected, and removable.
        assert _ok(auth_client.get(f"{CATALOG}echo-chat/"))["result"]["connected"]
        _ok(auth_client.delete(_add("echo-chat")))

    def test_query_key_pushed_whole(self, auth_client, user, bundle, pushes):
        org = user.organization
        _seed(
            org,
            {
                "enabled": True,
                "servers": {
                    "fern-files": {
                        "url": "https://mcp.fern-files.example.com/mcp",
                        "transport": "http",
                        "catalog_ref": "fern-files/fern-mcp@1.0.0",
                        "credential": {"mode": "shared"},
                    }
                },
            },
        )
        stored = self._store(
            auth_client, "fern-files", **{"in": "query", "name": "api_key"}
        )
        assert (stored["in"], stored["name"]) == ("query", "api_key")
        pushed = pushes[-1][1]["mcp"]["servers"]["fern-files"]
        assert pushed["url"] == "https://mcp.fern-files.example.com/mcp"
        assert pushed["credential"]["secret"] == {
            "type": "api_key",
            "in": "query",
            "name": "api_key",
            "value": SECRET,
        }
        assert SECRET not in json.dumps(_active(org).mcp)
        assert _ok(auth_client.get(f"{CATALOG}fern-files/"))["result"]["connected"]

    def test_url_variable_key_stays_in_the_url(self, auth_client, user, bundle, pushes):
        org = user.organization
        url = "https://mcp.fern-files.example.com/{apiKey}/mcp?region=eu"
        _seed(
            org,
            {
                "enabled": True,
                "servers": {
                    "fern-files": {
                        "url": url,
                        "transport": "http",
                        "catalog_ref": "fern-files/fern-path@1.0.0",
                        "url_variables": {"region": "eu"},
                        "credential": {"mode": "shared"},
                    }
                },
            },
        )
        self._store(
            auth_client, "fern-files", **{"in": "url_variable", "name": "apiKey"}
        )
        pushed = pushes[-1][1]["mcp"]["servers"]["fern-files"]
        assert pushed["url"] == url
        assert pushed["credential"]["secret"] == {
            "type": "api_key",
            "in": "url_variable",
            "name": "apiKey",
            "value": SECRET,
        }
        assert SECRET not in json.dumps(_active(org).mcp)
        assert _ok(auth_client.get(f"{CATALOG}fern-files/"))["result"]["connected"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free")
class TestConnectorOrgScope:
    def test_another_orgs_connectors_are_never_seen_or_removed(
        self, auth_client, user, bundle, pushes
    ):
        other = Organization.objects.create(name="CN9 Other Org")
        _seed(
            other,
            {
                "enabled": True,
                "servers": {
                    "cobalt-search": {
                        "url": "https://mcp.cobalt-search.example.com/sse",
                        "transport": "sse",
                        "catalog_ref": "cobalt-search/cobalt-mcp@1.0.0",
                        "credential": {"mode": "none"},
                    }
                },
            },
        )
        stored = copy.deepcopy(_active(other).mcp)
        body = _ok(auth_client.get(CATALOG))
        cobalt = next(app for app in body["results"] if app["id"] == "cobalt-search")
        assert (cobalt["connected"], cobalt["servers"]) == (False, [])
        assert _listed(auth_client, connected="true") == []
        _refused(auth_client.delete(_add("cobalt-search")), 404, "connector_not_added")
        # The first add in this org is its own, and leaves the other org alone.
        _ok(auth_client.post(_add("cobalt-search"), {}, format="json"), 201)
        assert _active(other).mcp == stored
        assert {org_id for org_id, _ in pushes} == {str(user.organization.pk)}

    def test_a_catalog_of_hundreds_of_apps(self, auth_client, user, bundle):
        data = _bundle()
        for i in range(300):
            app_id = f"bulk-{i:03d}"
            data["apps"].append(
                _app(
                    app_id,
                    f"Bulk {i:03d}",
                    f"Bulk app number {i}.",
                    category="data" if i % 2 else "research",
                    categories=["bulk"],
                    default="bulk-mcp",
                )
            )
            data["providers"].append(
                _provider(
                    app_id,
                    "bulk-mcp",
                    auth={"kind": "none"},
                    default_posture="auto",
                    tools=[_tool("read_rows")],
                )
            )
        bundle(data)
        first = _ok(auth_client.get(CATALOG, {"limit": 200}))
        second = _ok(auth_client.get(CATALOG, {"limit": 200, "page": 2}))
        names = [app["name"] for app in first["results"] + second["results"]]
        assert first["count"] == 304 and len(names) == 304
        assert names == sorted(names, key=str.casefold)
        assert _listed(auth_client, q="bulk 042") == ["bulk-042"]
        assert len(_listed(auth_client, category="bulk", limit=200)) == 200
        assert _ok(auth_client.get(CATALOG, {"category": "data"}))["count"] == 151


@pytest.mark.integration
@pytest.mark.api
class TestAddBudget:
    def test_adds_share_the_probes_budget(
        self, auth_client, user, bundle, pushes, probe, monkeypatch
    ):
        """An add calls the gateway's probe, so adds and Test connection
        share its 10 a minute per org: the 11th call is refused before the
        provider is called. Reads are not counted."""
        monkeypatch.setattr(MCPProbeOrgThrottle, "rate", "10/min")
        cache.clear()
        _ok(auth_client.post(_add("cobalt-search"), {}, format="json"), 201)
        for _ in range(8):
            _refused(
                auth_client.post(_add("cobalt-search"), {}, format="json"),
                409,
                "connector_already_added",
            )
        probe.answer = (200, {"ok": True, "latency_ms": 1, "error": None})
        assert (
            auth_client.post(
                PROBE,
                {"url": "https://mcp.cobalt-search.example.com/sse"},
                format="json",
            ).status_code
            == 200
        )
        calls = len(probe.calls)
        assert auth_client.post(
            _add("acme-docs"), _key(), format="json"
        ).status_code == (429)
        assert len(probe.calls) == calls
        assert "acme-docs" not in _active(user.organization).mcp["servers"]
        for _ in range(3):
            _ok(auth_client.get(CATALOG))
        assert auth_client.delete(_add("cobalt-search")).status_code == 200


# --- The URL renderer ----------------------------------------------------------


class TestRenderURL:
    def _variables(self, **patterns):
        return {
            name: catalog.URLVariable(
                name=name,
                description=name,
                pattern=pattern,
                default=None,
                regex=__import__("re").compile(pattern),
            )
            for name, pattern in patterns.items()
        }

    def test_host_variables_must_render_a_dns_name(self):
        variables = self._variables(host="^.+$")
        for value in (
            "x.example.com@evil.example",
            "x.example.com:99999",
            "10.0.0.1",
            "169.254.169.254",
            "example.123",
            "localhost",
            "x..example.com",
        ):
            with pytest.raises(catalog.ConnectorInvalid) as raised:
                catalog.render_url("https://{host}/mcp", variables, {"host": value})
            assert raised.value.code == "url_variable_invalid", value
        url, values = catalog.render_url(
            "https://{host}/mcp", variables, {"host": "Tenant-1.Example.com"}
        )
        assert url == "https://Tenant-1.Example.com/mcp"
        assert values == {"host": "Tenant-1.Example.com"}

    def test_path_and_query_values_are_percent_encoded(self):
        variables = self._variables(db="^.+$", q="^.+$")
        url, _ = catalog.render_url(
            "https://db.example.com/{db}/mcp?x={q}",
            variables,
            {"db": "a/b?c#d", "q": "1&admin=true"},
        )
        assert url == "https://db.example.com/a%2Fb%3Fc%23d/mcp?x=1%26admin%3Dtrue"

    def test_the_secret_variable_is_never_the_orgs(self):
        """A key placed in a URL variable stays {name} for the gateway to
        fill; the org can never set it."""
        variables = self._variables(region="^[a-z]{2}$")
        template = "https://mcp.example.com/{apiKey}/mcp?region={region}"
        url, values = catalog.render_url(
            template, variables, {"region": "eu"}, secret="apiKey"
        )
        assert (url, values) == (
            "https://mcp.example.com/{apiKey}/mcp?region=eu",
            {"region": "eu"},
        )
        with pytest.raises(catalog.ConnectorInvalid) as raised:
            catalog.render_url(
                template, variables, {"region": "eu", "apiKey": SECRET}, "apiKey"
            )
        assert raised.value.code == "url_variable_unknown"
        assert SECRET not in str(raised.value)

    def test_served_eras_come_from_the_contract(self):
        assert {"auto", "2026-07-28", "2025-03-26"} <= catalog.SERVED_ERAS


class TestPlan:
    """plan_for on single records: the era written is the one the gateway
    speaks, and a record it cannot read is refused alone."""

    def _plan(self, **transport):
        bundle = _bundle()
        app, provider = bundle["apps"][2], copy.deepcopy(bundle["providers"][3])
        assert provider["id"] == "cobalt-mcp"
        provider["transport"] = {
            "kind": "http",
            "url": "https://mcp.cobalt-search.example.com/mcp",
            **transport,
        }
        return catalog.plan_for(app, provider)

    def test_era_and_variant(self):
        plan = self._plan(era="2025-06-18")
        assert (plan.transport, plan.era) == ("http", "2025-06-18")
        plan = self._plan()
        assert (plan.transport, plan.era) == ("http", "auto")
        for transport in ({"variant": "sse"}, {"variant": "sse", "era": "2024-11-05"}):
            plan = self._plan(**transport)
            assert (plan.transport, plan.era) == ("sse", "2024-11-05")
        for transport, code in (
            ({"variant": "sse", "era": "2025-03-26"}, "connector_catalog_data_invalid"),
            ({"era": "2024-11-05"}, "connector_catalog_data_invalid"),
            ({"variant": "websocket"}, "connector_catalog_data_invalid"),
            ({"era": "2099-01-01"}, "connector_era_not_served"),
        ):
            assert self._plan(**transport).code == code, transport

    def test_credential_parts_by_the_gateways_rule(self):
        """IR-CN9-3 and AM-C23 (e): each part is checked by the rule the push
        places it with (check_catalog_secret, the provider's declared parts),
        the org's variables filled by a placeholder. A key the listing at add
        cannot send is planned, then refused for that alone."""
        bundle = _bundle()
        app = next(a for a in bundle["apps"] if a["id"] == "fern-files")
        fern = next(p for p in bundle["providers"] if p["id"] == "fern-mcp")

        def plan(**auth_part):
            provider = copy.deepcopy(fern)
            provider["auth"]["credentialParts"][0].update(auth_part)
            return provider

        credential = catalog._credential_plan(plan()["auth"])
        assert (credential.kind, credential.placement, credential.name) == (
            "api_key",
            "query",
            "api_key",
        )
        assert catalog.plan_for(app, plan()).code == (
            "connector_tool_listing_not_applied"
        )
        # A parameter the URL already sets would be sent twice.
        provider = plan()
        provider["transport"]["url"] = (
            "https://mcp.fern-files.example.com/mcp?api_key=x"
        )
        assert catalog.plan_for(app, provider).code == (
            "connector_credential_placement_not_applied"
        )
        # Only a header part takes a prefix.
        assert catalog.plan_for(app, plan(prefix="Key ")).code == (
            "connector_credential_prefix_not_applied"
        )
        # A name the vault cannot store for a query parameter.
        assert catalog.plan_for(app, plan(name="api key")).code == (
            "connector_credential_placement_not_applied"
        )
        # A constant query part is added by the gateway like a header one.
        provider = plan()
        provider["auth"]["credentialParts"].append(
            {"in": "query", "name": "format", "secret": False, "value": "json"}
        )
        credential = catalog._credential_plan(provider["auth"])
        assert credential.constants == (("query", "format", "json"),)
        # A header key alone is addable, and the push's rule takes it.
        provider = plan(**{"in": "header", "name": "X-Fern-Key"})
        added = catalog.plan_for(app, provider)
        assert isinstance(added, catalog.Plan), added
        assert added.credential.listing_problem() is None
        assert (
            added.credential.placement_problem(
                "https://mcp.fern-files.example.com/mcp",
                added.catalog_ref,
                added.parts,
            )
            is None
        )
        # The push's rule names the provider: under another catalog_ref, or
        # another major version, the key is refused.
        for ref in ("fern-files/other@1.0.0", "fern-files/fern-mcp@2.0.0"):
            field, _ = added.credential.placement_problem(
                "https://mcp.fern-files.example.com/mcp", ref, added.parts
            )
            assert field == "catalog_ref", ref

    def test_plan_time_tool_rules(self):
        """Tools are listed at add, so a provider is refused at plan time for
        its tools only when the catalog denies every tool it does not
        declare and allows none it declares."""
        bundle = _bundle()
        app = next(a for a in bundle["apps"] if a["id"] == "gale-notes")
        gale = next(p for p in bundle["providers"] if p["id"] == "gale-mcp")
        assert isinstance(catalog.plan_for(app, gale), catalog.Plan)
        gale["tools"]["defaultPosture"] = "deny"
        assert catalog.plan_for(app, gale).code == "connector_no_tool_allowed"
        gale["tools"]["items"] = []
        assert catalog.plan_for(app, gale).code == "connector_no_tool_allowed"
        gale["tools"]["defaultPosture"] = "ask"
        plan = catalog.plan_for(app, gale)
        assert isinstance(plan, catalog.Plan) and plan.card["tools"] == []

    def test_unreadable_record_refused_alone(self):
        bundle = _bundle()
        del bundle["providers"][3]["tools"]["items"][0]["annotations"]
        bundle["providers"][0]["x-gateway"] = {"timeoutMs": "20000"}
        assert catalog.plan_for(bundle["apps"][2], bundle["providers"][3]).code == (
            "connector_catalog_data_invalid"
        )
        assert catalog.plan_for(bundle["apps"][0], bundle["providers"][0]).code == (
            "connector_catalog_data_invalid"
        )


def _listed_tool(name="t", **hints):
    fields = {
        "read_only_hint": None,
        "destructive_hint": None,
        "idempotent_hint": None,
        "open_world_hint": None,
    }
    fields.update(hints)
    return ListedTool(name=name, title="", description="", **fields)


class TestListedToolPolicy:
    """listed_tool_policy on single tools (CN-ADD)."""

    ALLOWED = catalog.ToolPolicy("t", "allow", None)

    @pytest.mark.parametrize(
        "hints, want",
        [
            ({"read_only_hint": True}, ("allow", None)),
            ({"read_only_hint": True, "destructive_hint": False}, ("allow", None)),
            (
                {"read_only_hint": True, "destructive_hint": True},
                ("deny", "destructive_tool"),
            ),
            ({"read_only_hint": False}, ("deny", "destructive_tool")),
            (
                {"read_only_hint": False, "destructive_hint": False},
                ("deny", "write_tool"),
            ),
            ({}, ("deny", "destructive_tool")),
            ({"destructive_hint": False}, ("deny", "write_tool")),
        ],
    )
    def test_annotations_decide_an_undeclared_tool(self, hints, want):
        for default in ("ask", "auto"):
            policy = catalog.listed_tool_policy(_listed_tool(**hints), None, default)
            assert (policy.gateway_posture, policy.reason) == want, default

    def test_the_catalog_only_tightens(self):
        read = _listed_tool(read_only_hint=True)
        write = _listed_tool(read_only_hint=False, destructive_hint=False)
        declared_deny = catalog.ToolPolicy("t", "deny", "approval_not_enforced")
        policy = catalog.listed_tool_policy(read, declared_deny, "auto")
        assert (policy.gateway_posture, policy.reason) == (
            "deny",
            "approval_not_enforced",
        )
        # A declared allow does not lift a listed write.
        policy = catalog.listed_tool_policy(write, self.ALLOWED, "auto")
        assert (policy.gateway_posture, policy.reason) == ("deny", "write_tool")
        assert catalog.listed_tool_policy(
            read, self.ALLOWED, "deny"
        ).gateway_posture == ("allow")
        policy = catalog.listed_tool_policy(read, None, "deny")
        assert (policy.gateway_posture, policy.reason) == ("deny", "posture_deny")

    @pytest.mark.parametrize(
        "name", ["", "*", "a*", "a/b", "a b", "x" * 129, "caf\u00e9", "a:b"]
    )
    def test_a_name_outside_mcps_rule_is_denied(self, name):
        tool = _listed_tool(name, read_only_hint=True)
        policy = catalog.listed_tool_policy(tool, None, "auto")
        assert (policy.gateway_posture, policy.reason) == ("deny", "invalid_tool_name")
        vs = catalog._virtual_server(
            {"id": "app", "name": "App", "description": "App."}, [policy]
        )
        assert vs["tools"] == {"include": []} and "access" not in vs

    def test_a_name_denied_once_is_never_included(self):
        policies = [
            catalog.ToolPolicy("dup", "allow", None),
            catalog.ToolPolicy("dup", "deny", "write_tool"),
            catalog.ToolPolicy("ok", "allow", None),
        ]
        vs = catalog._virtual_server(
            {"id": "app", "name": "App", "description": "App."}, policies
        )
        assert vs["tools"] == {"include": ["app/ok"]}
        assert vs["access"] == {"rules": [{"match": {}, "deny": ["app/dup"]}]}


# --- The site's routes and CN6's real Django copy (AM-C22, IR-CN9-2) ------------


# The apps of CN6's Django copy an org can add on this build, each by its one
# addable provider: a vendor MCP server with one key the listing at add sends
# in a header (CN-ADD). The tripwire for a catalog or a plan_for change.
REAL_ADDABLE = {
    "airtable": "pat",
    "apollo": "api-key",
    "atlassian": "service-account-key",
    "cloudflare": "api-token",
    "databricks": "genie-one-pat",
    "datadog": "access-token",
    "exa": "api-key",
    "firecrawl": "api-key",
    "github": "remote-pat",
    "gitlab": "pat",
    "google-maps": "grounding-lite",
    "langsmith": "api-key",
    "linear": "api-key",
    "monday-com": "api-token",
    "neon": "api-key",
    "notion": "mcp-token",
    "parallel-search": "api-key",
    "parallel-task": "api-key",
    "perplexity": "api-key",
    "snowflake": "pat",
    "stripe": "agent-key",
    "supabase": "pat",
    "x-twitter": "app-bearer",
}


# OAuth app registration is available before sign-in/tool discovery, including
# providers with no declared tools. This is not a claim of a connected account.
REAL_OAUTH_CONFIGURABLE = {
    "calendly": "mcp",
    "datadog": "mcp",
    "gitlab": "mcp",
    "grafana": "cloud-mcp",
    "hex": "mcp",
    "monday-com": "mcp",
    "mongodb-atlas": "mcp",
    "neon": "mcp",
    "pylon": "mcp",
    "thoughtspot": "mcp",
    "wix": "mcp",
}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free")
class TestTheSiteServesTheRealCatalog:
    """The site mounts the catalog routes (agentcc/urls.py), and the service
    reads CN6's generated Django copy, the bundle of CN12's batch A
    (IR-CN9-2). Recorded without test accounts, its providers declare no
    tools; CN-ADD lists them at add with the org's own key, so every vendor
    MCP provider whose key goes in a header, alone, is addable (it relaxes
    AM-C22, which waited for CN31's authenticated snapshots). Discovered MCP
    OAuth providers expose registration before tool discovery; declared REST
    OAuth providers remain separate and are not enabled by this path."""

    def test_the_site_mounts_the_catalog_routes(
        self, auth_client, settings, pushes, probe
    ):
        assert settings.ROOT_URLCONF == "tfc.urls"
        for route, name in (
            (CATALOG, "agentcc-connector-catalog"),
            (f"{CATALOG}linear/", "agentcc-connector-catalog-app"),
            (_add("linear"), "agentcc-connector-add"),
        ):
            assert resolve(route).url_name == name, route
        assert reverse("agentcc-connector-catalog") == CATALOG
        assert reverse("agentcc-connector-add", args=["linear"]) == _add("linear")
        assert _ok(auth_client.get(CATALOG))["count"] == len(
            REAL_ADDABLE.keys() | REAL_OAUTH_CONFIGURABLE.keys()
        )
        assert _ok(auth_client.get(f"{CATALOG}linear/"))["result"]["id"] == "linear"
        _refused(
            auth_client.post(_add("close"), {"provider": "api-key"}, format="json"),
            409,
            "connector_tool_listing_not_applied",
        )
        assert pushes == [] and probe.calls == []

    def test_routes_and_the_generated_copy(self, auth_client, user, pushes, probe):
        assert catalog.BUNDLE_PATH.name == "catalog.json"
        assert catalog.BUNDLE_PATH.parent.name == "connector_catalog"
        raw = json.loads(catalog.BUNDLE_PATH.read_text())
        _bundle_validator().validate(raw)
        apps = {app["id"] for app in raw["apps"]}
        assert {"close", "github", "linear", "notion", "slack", "stripe"} <= apps

        body = _ok(auth_client.get(CATALOG, {"limit": 200}))
        listed = {
            app["id"]: {provider["id"] for provider in app["providers"]}
            for app in body["results"]
        }
        expected = {app_id: {p} for app_id, p in REAL_ADDABLE.items()}
        for app_id, provider_id in REAL_OAUTH_CONFIGURABLE.items():
            expected.setdefault(app_id, set()).add(provider_id)
        assert listed == expected
        for app in body["results"]:
            for provider in app["providers"]:
                assert provider["kind"] == "vendor_mcp", app["id"]
                credential = provider["credential"]
                if credential["kind"] == "oauth":
                    assert provider["id"] == REAL_OAUTH_CONFIGURABLE[app["id"]]
                    assert "user" in provider["account_modes"]
                    assert provider["connect"]["configure_oauth"] is True
                    assert provider["tools"] == []
                    continue
                assert provider["account_modes"] == ["agent"], app["id"]
                assert (credential["mode"], credential["in"]) == ("shared", "header")
                assert credential["kind"] in ("bearer", "api_key"), app["id"]
        # notion's default provider is its OAuth one: the card names none.
        notion = next(app for app in body["results"] if app["id"] == "notion")
        assert notion["default_provider"] is None
        for app_id, provider_id, code in (
            ("close", "api-key", "connector_tool_listing_not_applied"),
            ("browserbase", "api-key", "connector_tool_listing_not_applied"),
            ("tavily", "api-key", "connector_tool_listing_not_applied"),
            ("datadog", "api-and-app-key", "connector_credential_parts_not_applied"),
            ("github", "remote", "connector_oauth_not_built"),
            ("notion", "mcp", "connector_oauth_not_built"),
        ):
            _refused(
                auth_client.post(
                    _add(app_id), {"provider": provider_id, **_key()}, format="json"
                ),
                409,
                code,
            )
        assert pushes == [] and probe.calls == []

    def test_a_real_provider_is_added_end_to_end(
        self, auth_client, user, pushes, probe
    ):
        """linear/api-key through the site's routes: the listing is taken
        with the org's bearer token, its read-only tool is allowed and its
        write tool denied, and the push carries the token for the gateway's
        embedded catalog to place (Authorization: Bearer)."""
        org = user.organization
        probe.listings = {
            "mcp.linear.app": [
                {"name": "list_issues", "title": "List issues", "read_only_hint": True},
                {
                    "name": "create_issue",
                    "title": "Create an issue",
                    "read_only_hint": False,
                    "destructive_hint": False,
                },
            ]
        }
        result = _ok(
            auth_client.post(
                _add("linear"),
                {"provider": "api-key", **_key(SECRET_2)},
                format="json",
            ),
            201,
        )["result"]
        assert probe.calls == [
            {
                "org_id": str(org.pk),
                "url": "https://mcp.linear.app/mcp",
                "transport": "http",
                "auth": {
                    "type": "bearer",
                    "header": "Authorization",
                    "value": SECRET_2,
                },
            }
        ]
        assert result["catalog_ref"].startswith("linear/api-key@1.")
        assert [(t["name"], t["gateway_posture"]) for t in result["tools"]] == [
            ("list_issues", "allow"),
            ("create_issue", "deny"),
        ]
        vs = _active(org).mcp["virtual_servers"]["linear"]
        assert vs["tools"] == {"include": ["linear/list_issues"]}
        assert vs["access"]["rules"] == [{"match": {}, "deny": ["linear/create_issue"]}]
        pushed = pushes[-1][1]["mcp"]["servers"]["linear"]
        assert pushed["catalog_ref"] == result["catalog_ref"]
        assert pushed["credential"]["secret"] == {
            "type": "bearer",
            "header": "Authorization",
            "value": SECRET_2,
        }
        assert result["gateway_synced"] is True
        assert _ok(auth_client.get(f"{CATALOG}linear/"))["result"]["connected"]

    def test_the_k2_pilots_are_in_the_copy_and_none_is_addable(
        self, auth_client, user, pushes
    ):
        """CW2d: CN14c's Gmail and Google Calendar and CN15c's Outlook and
        Teams reach the Django copy, each defaulting to FutureAGI's own
        connector (futureagi-rest, IR-CN14c-7), and none is addable from the
        catalog: a rest provider is a kind the catalog service does not serve
        (CN9 plan_for), and Google's and Microsoft's MCP servers need the OAuth
        connect flow (M25x, R5). So none of them is listed (AM-C23: nothing
        an org cannot connect today)."""
        raw = json.loads(catalog.BUNDLE_PATH.read_text())
        apps = {app["id"]: app for app in raw["apps"]}
        providers = {(p["app"], p["id"]): p for p in raw["providers"]}
        pilots = {
            "gmail": ("google-mcp", 13),
            "google-calendar": ("google-mcp", 8),
            "microsoft-outlook": ("workiq", 22),
            "microsoft-teams": ("workiq", 11),
        }
        for app_id, (mcp_id, tools) in pilots.items():
            assert apps[app_id]["defaultProvider"] == "futureagi-rest", app_id
            rest = providers[(app_id, "futureagi-rest")]
            assert rest["kind"] == "rest", app_id
            assert len(rest["tools"]["items"]) == tools, app_id
            assert providers[(app_id, mcp_id)]["tools"]["defaultPosture"] == "deny"
            entry = catalog.load_bundle().apps[app_id]
            plan = entry.plans["futureagi-rest"]
            assert (
                isinstance(plan, catalog.Unavailable)
                and plan.code == catalog.KIND_NOT_SERVED
            )
            plan = entry.plans[mcp_id]
            assert (
                isinstance(plan, catalog.Unavailable)
                and plan.code == catalog.OAUTH_NOT_BUILT
            )
            assert entry.card["default_provider"] is None, entry.card
            _refused(
                auth_client.get(f"{CATALOG}{app_id}/"), 404, "connector_app_not_found"
            )
            for provider_id, code in (
                ("futureagi-rest", "connector_kind_not_served"),
                (mcp_id, "connector_oauth_not_built"),
            ):
                _refused(
                    auth_client.post(
                        _add(app_id), {"provider": provider_id}, format="json"
                    ),
                    409,
                    code,
                )
        # Google's client group is one OAuth client: each member declares
        # Google's revocation endpoint (IR-CN14c-3).
        for ref in (
            ("gmail", "futureagi-rest"),
            ("google-calendar", "futureagi-rest"),
            ("google-bigquery", "mcp"),
        ):
            oauth = providers[ref]["auth"]["oauth2"]
            assert oauth["clientGroup"] == "google", ref
            assert (
                oauth["revocation"]["url"] == "https://oauth2.googleapis.com/revoke"
            ), ref
        listed = {app["id"] for app in _ok(auth_client.get(CATALOG))["results"]}
        assert not listed & set(pilots)
        assert pushes == []
