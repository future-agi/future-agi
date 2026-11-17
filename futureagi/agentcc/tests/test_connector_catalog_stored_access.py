"""The catalog's server card carries each tool's stored access.

An added catalog app has its own virtual server, which holds what the gateway
enforces for each tool: included (allowed), included under the approval
policy (approval_required), excluded (excluded) or denied by a rule for
every principal (denied). The server card lists each tool that virtual
server names with that access, so the connector drawer shows the stored
access rather than the catalog's live policy: a tool listed at add counts
even when the provider declares none, and an app added before tool approvals
still shows the write tools its virtual server denies as denied. When the
virtual server holds a shape the card cannot read exactly (a pattern, an
allow list, a rule with no match, an override, another approval policy), the
card names no tool's access, and the drawer shows the catalog's tools with
its note that a later edit is not shown. The bundle, the faked probe and the
helpers are test_connector_catalog's.
"""

import copy
import json

import pytest

from accounts.models import Organization
from agentcc.tests import test_connector_catalog as fixtures
from agentcc.views.connector_catalog import ConnectorAppSerializer

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.integration,
    pytest.mark.api,
    pytest.mark.usefixtures("drift_free"),
]
bundle = fixtures.bundle
drift_free = fixtures.drift_free
pushes = fixtures.pushes
probe = fixtures.probe
probe_budget = fixtures.probe_budget

CATALOG = fixtures.CATALOG
KITE = "kite-files"
KITE_HOST = "mcp.kite-files.example.com"
KITE_REF = "kite-files/kite-mcp@1.0.0"
# What Kite Files lists when it is added: two read-only tools and a
# destructive one. Its provider declares none.
KITE_LISTING = [
    fixtures._probe_tool(fixtures._tool("echo")),
    fixtures._probe_tool(fixtures._tool("get_weather")),
    fixtures._probe_tool(
        fixtures._tool("delete_file", read_only=False, destructive=True)
    ),
]
KITE_TOOLS = [
    {"name": "delete_file", "access": "approval_required"},
    {"name": "echo", "access": "allowed"},
    {"name": "get_weather", "access": "allowed"},
]
ACME_SERVER = {
    "url": "https://mcp.acme-docs.example.com/mcp",
    "transport": "http",
    "catalog_ref": "acme-docs/acme-mcp@1.2.0",
    "credential": {"mode": "shared"},
}
ACME_INCLUDE = [
    "acme-docs/create_page",
    "acme-docs/get_page",
    "acme-docs/search_pages",
]


def _kite_bundle():
    """The fixture bundle and Kite Files, whose one provider declares no
    tools (its tools section has no items, as the E2E mock's provider) and
    takes a bearer key."""
    data = fixtures._bundle()
    data["apps"].append(
        fixtures._app(
            KITE,
            "Kite Files",
            "Files with read-only and destructive tools.",
            category="productivity",
            categories=["files"],
            default="kite-mcp",
        )
    )
    provider = fixtures._provider(
        KITE,
        "kite-mcp",
        auth=fixtures._header_key("Authorization", prefix="Bearer ", label="API key"),
        tools=[],
    )
    del provider["tools"]["items"]
    data["providers"].append(provider)
    return data


def _add_kite(client, probe):
    probe.listings[KITE_HOST] = copy.deepcopy(KITE_LISTING)
    return fixtures._ok(
        client.post(fixtures._add(KITE), fixtures._key(), format="json"), 201
    )["result"]


def _detail(client, app_id):
    return fixtures._ok(client.get(f"{CATALOG}{app_id}/"))["result"]


def _virtual_server(app_id, name, *, include, approval=None, rules=None):
    vs = {
        "id": "vs_01M5F7JBJH5XDKQ57KQFMC2NY1",
        "title": name,
        "description": f"{name}, added from the catalog.",
        "members": [app_id],
        "tools": {"include": [f"{app_id}/{tool}" for tool in include]},
    }
    if approval is not None:
        vs["approval"] = {
            "require": "listed",
            "tools": [f"{app_id}/{tool}" for tool in approval],
            "validity": "once",
        }
    if rules is not None:
        vs["access"] = {"rules": rules}
    return vs


class TestTheServerCardListsStoredAccess:
    def test_each_tool_with_its_stored_access_after_an_add(
        self, auth_client, user, bundle, pushes, probe
    ):
        bundle(_kite_bundle())
        _add_kite(auth_client, probe)
        fixtures._ok(
            auth_client.post(
                fixtures._add("acme-docs"), fixtures._key(), format="json"
            ),
            201,
        )

        # Kite Files declares no tools; the ones it listed at add count, each
        # with the access its virtual server stores.
        kite = _detail(auth_client, KITE)
        assert kite["providers"][0]["tools"] == []
        assert kite["servers"][0]["tools"] == KITE_TOOLS
        # Acme Docs stores all three: allowed, under an approval, denied.
        acme = _detail(auth_client, "acme-docs")
        assert acme["servers"][0]["tools"] == [
            {"name": "archive_page", "access": "denied"},
            {"name": "create_page", "access": "approval_required"},
            {"name": "delete_page", "access": "approval_required"},
            {"name": "get_page", "access": "allowed"},
            {"name": "list_recent", "access": "approval_required"},
            {"name": "run_query", "access": "denied"},
            {"name": "search_pages", "access": "allowed"},
        ]
        # The list read carries the same cards as the one-app read.
        listed = {
            app["id"]: app for app in fixtures._ok(auth_client.get(CATALOG))["results"]
        }
        assert listed[KITE]["servers"] == kite["servers"]
        assert listed["acme-docs"]["servers"] == acme["servers"]

    def test_a_write_tool_an_older_virtual_server_denies_stays_denied(
        self, auth_client, user, bundle
    ):
        """An app added before tool approvals keeps the virtual server it was
        added with: its write tools sit in the deny rule, while the catalog's
        policy today would let them run after an approval."""
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {
                    "acme-docs": _virtual_server(
                        "acme-docs",
                        "Acme Docs",
                        include=["get_page", "search_pages"],
                        rules=[
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
                        ],
                    )
                },
            },
        )
        acme = _detail(auth_client, "acme-docs")
        live = {
            tool["name"]: (tool["gateway_posture"], tool["reason"])
            for tool in acme["providers"][0]["tools"]
        }
        assert live["create_page"] == ("allow", "approval_required")
        assert live["delete_page"] == ("allow", "approval_required")
        assert acme["servers"][0]["tools"] == [
            {"name": "archive_page", "access": "denied"},
            {"name": "create_page", "access": "denied"},
            {"name": "delete_page", "access": "denied"},
            {"name": "get_page", "access": "allowed"},
            {"name": "list_recent", "access": "denied"},
            {"name": "run_query", "access": "denied"},
            {"name": "search_pages", "access": "allowed"},
        ]

    def test_a_deny_for_every_principal_wins_and_only_this_servers_names_count(
        self, auth_client, user, bundle
    ):
        """A deny for every principal wins over an include and an approval; a
        rule for some principals and another server's tool are not this
        server's stored access for a tool."""
        vs = _virtual_server(
            "acme-docs",
            "Acme Docs",
            include=["create_page", "get_page"],
            approval=["create_page", "get_page"],
            rules=[
                {"match": {}, "deny": ["acme-docs/create_page"]},
                {"match": {"key_ids": ["key-1"]}, "deny": ["acme-docs/get_page"]},
            ],
        )
        vs["tools"]["include"].append("bolt-crm/list_deals")
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {"acme-docs": vs},
            },
        )
        assert _detail(auth_client, "acme-docs")["servers"][0]["tools"] == [
            {"name": "create_page", "access": "denied"},
            {"name": "get_page", "access": "approval_required"},
        ]

    def test_an_excluded_tool_reads_excluded_not_denied(
        self, auth_client, user, bundle
    ):
        """An exclude wins over an include, an approval and a rule, as in the
        gateway: the virtual server does not serve a tool it excludes, so the
        card shows it excluded, and a tool it only excludes is listed too. An
        exclude is an edit to the virtual server, not the deny an add wrote,
        so it stays apart from a tool a rule denies."""
        vs = _virtual_server(
            "acme-docs",
            "Acme Docs",
            include=["create_page", "get_page", "search_pages"],
            approval=["create_page", "get_page"],
            rules=[
                {
                    "match": {},
                    "deny": ["acme-docs/archive_page", "acme-docs/run_query"],
                }
            ],
        )
        vs["tools"]["exclude"] = ["acme-docs/get_page", "acme-docs/run_query"]
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {"acme-docs": vs},
            },
        )
        assert _detail(auth_client, "acme-docs")["servers"][0]["tools"] == [
            {"name": "archive_page", "access": "denied"},
            {"name": "create_page", "access": "approval_required"},
            {"name": "get_page", "access": "excluded"},
            {"name": "run_query", "access": "excluded"},
            {"name": "search_pages", "access": "allowed"},
        ]

    @pytest.mark.parametrize("require", ["absent", "none", ""])
    def test_an_approval_list_counts_only_when_the_policy_requires_it(
        self, auth_client, user, bundle, require
    ):
        """With no require, or require none or empty, the gateway asks for no
        approval, so a tool on the approval list is allowed."""
        vs = _virtual_server(
            "acme-docs",
            "Acme Docs",
            include=["create_page", "get_page"],
            approval=["create_page"],
        )
        if require == "absent":
            del vs["approval"]["require"]
        else:
            vs["approval"]["require"] = require
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {"acme-docs": vs},
            },
        )
        assert _detail(auth_client, "acme-docs")["servers"][0]["tools"] == [
            {"name": "create_page", "access": "allowed"},
            {"name": "get_page", "access": "allowed"},
        ]

    def test_no_tools_when_the_approval_policy_reads_annotations(
        self, auth_client, user, bundle
    ):
        """Under require destructive the gateway asks for an approval by each
        tool's annotations, which the stored virtual server does not hold, so
        the card names no tool's access."""
        vs = _virtual_server(
            "acme-docs",
            "Acme Docs",
            include=["create_page", "get_page"],
            approval=["create_page"],
        )
        vs["approval"]["require"] = "destructive"
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {"acme-docs": vs},
            },
        )
        server = _detail(auth_client, "acme-docs")["servers"][0]
        assert "tools" not in server
        assert server["virtual_server"]["slug"] == "acme-docs"

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            pytest.param("tools", {"include": ["acme-docs/*"]}, id="include-pattern"),
            pytest.param(
                "tools",
                {"include": ACME_INCLUDE, "exclude": ["acme-docs/delete_*"]},
                id="exclude-pattern",
            ),
            pytest.param(
                "tools",
                {"include": ACME_INCLUDE, "exclude": ["*/delete_*"]},
                id="exclude-pattern-for-every-server",
            ),
            pytest.param(
                "approval",
                {"require": "listed", "tools": ["acme-docs/*"], "validity": "once"},
                id="approval-pattern",
            ),
            pytest.param(
                "approval",
                {
                    "require": "always",
                    "tools": ["acme-docs/create_page"],
                    "validity": "once",
                },
                id="another-approval-policy",
            ),
            pytest.param(
                "access",
                {"rules": [{"deny": ["acme-docs/create_page"]}]},
                id="rule-with-no-match",
            ),
            pytest.param(
                "access",
                {"rules": [{"match": None, "deny": ["acme-docs/create_page"]}]},
                id="rule-with-a-null-match",
            ),
            pytest.param(
                "access",
                {"rules": [{"match": {}, "allow": ["acme-docs/get_page"]}]},
                id="allow-list-for-every-principal",
            ),
            pytest.param(
                "access",
                {"rules": [{"match": {}, "deny": ["acme-docs/create_*"]}]},
                id="deny-pattern-for-every-principal",
            ),
            pytest.param(
                "overrides",
                {"acme-docs/create_page": {"hidden": True}},
                id="override",
            ),
        ],
    )
    def test_no_tools_when_the_card_cannot_read_the_virtual_server_exactly(
        self, auth_client, user, bundle, key, value
    ):
        """The gateway reads a "*" as any run of characters, a rule with no
        match as one for every principal, an allow list as a narrowing, and
        an override as a hidden or renamed tool. The card reads exact names
        only, so for any of these it names no tool's access, and the drawer
        shows the catalog's tools with its note that a later edit is not
        shown."""
        vs = _virtual_server(
            "acme-docs",
            "Acme Docs",
            include=["create_page", "get_page", "search_pages"],
            approval=["create_page"],
        )
        vs[key] = copy.deepcopy(value)
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {"acme-docs": vs},
            },
        )
        acme = _detail(auth_client, "acme-docs")
        server = acme["servers"][0]
        assert "tools" not in server
        assert server["virtual_server"]["slug"] == "acme-docs"
        assert {tool["name"] for tool in acme["providers"][0]["tools"]} >= {
            "create_page",
            "get_page",
        }

    def test_an_empty_match_and_another_servers_override_are_read(
        self, auth_client, user, bundle
    ):
        """A rule whose match lists name no key, agent or user applies to
        every principal, as a match of {} does, and an override for another
        member's tool says nothing about this server's tools."""
        vs = _virtual_server(
            "acme-docs",
            "Acme Docs",
            include=["create_page", "get_page"],
            rules=[
                {
                    "match": {"key_ids": [], "user_ids": []},
                    "deny": ["acme-docs/create_page"],
                }
            ],
        )
        vs["members"].append("bolt-crm")
        vs["overrides"] = {"bolt-crm/list_deals": {"hidden": True}}
        fixtures._seed(
            user.organization,
            {
                "enabled": True,
                "servers": {"acme-docs": copy.deepcopy(ACME_SERVER)},
                "virtual_servers": {"acme-docs": vs},
            },
        )
        assert _detail(auth_client, "acme-docs")["servers"][0]["tools"] == [
            {"name": "create_page", "access": "denied"},
            {"name": "get_page", "access": "allowed"},
        ]

    def test_no_secret_in_the_card(
        self, auth_client, user, workspace, bundle, pushes, probe
    ):
        bundle(_kite_bundle())
        _add_kite(auth_client, probe)
        member_client, _ = fixtures._member(user.organization, workspace)
        bodies = []
        for client in (auth_client, member_client):
            bodies.append(fixtures._ok(client.get(CATALOG)))
            bodies.append(fixtures._ok(client.get(f"{CATALOG}{KITE}/")))
        cards = [
            server
            for body in bodies
            for app in body.get("results") or [body.get("result")]
            for server in app["servers"]
        ]
        assert len(cards) == 4
        fields = set(ConnectorAppSerializer().fields["servers"].child.fields)
        for card in cards:
            assert set(card) <= fields
            assert card["tools"] == KITE_TOOLS
        assert fixtures.SECRET not in json.dumps(bodies)
        # The key reached the probe and the push, so the check could see it.
        assert fixtures.SECRET in json.dumps(probe.calls)
        assert fixtures.SECRET in json.dumps(pushes)

    def test_another_orgs_servers_never_appear(
        self, auth_client, user, bundle, pushes, probe
    ):
        bundle(_kite_bundle())
        other = Organization.objects.create(name="Drawer Other Org")
        fixtures._seed(
            other,
            {
                "enabled": True,
                "servers": {
                    KITE: {
                        "url": f"https://{KITE_HOST}/mcp",
                        "transport": "http",
                        "catalog_ref": KITE_REF,
                        "credential": {"mode": "none"},
                    }
                },
                "virtual_servers": {
                    KITE: _virtual_server(
                        KITE,
                        "Kite Files",
                        include=["purge_all"],
                        approval=["purge_all"],
                    )
                },
            },
        )
        stored = copy.deepcopy(fixtures._active(other).mcp)
        kite = _detail(auth_client, KITE)
        assert (kite["connected"], kite["servers"]) == (False, [])

        # Once this org adds the app, its card lists its own tools only.
        _add_kite(auth_client, probe)
        assert _detail(auth_client, KITE)["servers"][0]["tools"] == KITE_TOOLS
        assert "purge_all" not in json.dumps(fixtures._ok(auth_client.get(CATALOG)))
        assert fixtures._active(other).mcp == stored
