"""Browse-only Microsoft hold: real declarations, synthetic bundle, no OAuth."""

import copy
import json
from pathlib import Path

import pytest
from django.test import override_settings

from agentcc import mcp_features
from agentcc.services import connector_catalog as catalog
from agentcc.tests import test_connector_catalog as fixtures
from agentcc.views.connector_catalog import ConnectorAppSerializer

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("drift_free")]
bundle = fixtures.bundle
drift_free = fixtures.drift_free
CATALOG = fixtures.CATALOG
MICROSOFT = {
    "microsoft-excel": ("futureagi-rest",),
    "microsoft-onedrive": ("futureagi-rest",),
    "microsoft-outlook": ("futureagi-rest", "workiq"),
    "microsoft-sharepoint": ("futureagi-rest",),
    "microsoft-teams": ("futureagi-rest", "workiq"),
}


@pytest.fixture
def held_bundle(bundle, settings):
    settings.AGENTCC_MCP_CATALOG_ENABLED = True
    settings.AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED = True
    settings.AGENTCC_MCP_K2_APPS_ENABLED = True
    # Read public declarations only, never a settings or credentials file.
    path = Path(__file__).resolve().parents[1] / "connector_catalog" / "catalog.json"
    real = json.loads(path.read_text())
    apps = [copy.deepcopy(app) for app in real["apps"] if app["id"] in MICROSOFT]
    providers = [
        copy.deepcopy(provider)
        for provider in real["providers"]
        if provider["app"] in MICROSOFT
    ]
    assert {app["id"] for app in apps} == set(MICROSOFT)
    assert {(p["app"], p["id"]) for p in providers} == {
        (app_id, provider_id)
        for app_id, provider_ids in MICROSOFT.items()
        for provider_id in provider_ids
    }
    for provider in providers:
        provider.setdefault("x-gateway", {})["connectionEnablement"] = {
            "schemaVersion": 1,
            "enabled": False,
            "operations": [],
        }
    data = fixtures._bundle()
    data["apps"].extend(apps)
    data["providers"].extend(providers)
    bundle(data)
    return data


def _rows(client, **query):
    response = client.get(CATALOG, query)
    assert response.status_code == 200, response.json()
    return {app["id"]: app for app in response.json()["results"]}


def _assert_held(app):
    assert app["status"] == "coming_soon"
    assert app["providers"] == []
    assert app["default_provider"] is None
    assert "connect" not in app
    # The declared list/detail contract must preserve the new status too.
    assert ConnectorAppSerializer(app).data["status"] == "coming_soon"


def test_five_microsoft_apps_visible_with_seven_disabled_providers(
    auth_client, held_bundle
):
    rows = _rows(auth_client)
    assert set(rows) == set(fixtures.ALL) | set(MICROSOFT)
    for app_id in MICROSOFT:
        _assert_held(rows[app_id])
        assert rows[app_id]["servers"] == []
        assert rows[app_id]["connected"] is False
        detail = auth_client.get(f"{CATALOG}{app_id}/")
        assert detail.status_code == 200, detail.json()
        assert detail.json()["result"] == rows[app_id]
    plans = catalog.load_bundle().apps
    for app_id, provider_ids in MICROSOFT.items():
        for provider_id in provider_ids:
            assert isinstance(plans[app_id].plans[provider_id], catalog.Unavailable)


def test_non_microsoft_unavailable_apps_show_coming_soon(auth_client, held_bundle):
    rows = _rows(auth_client)
    for app_id in FIXTURE_UNFINISHED:
        _assert_held(rows[app_id])
        detail = auth_client.get(f"{CATALOG}{app_id}/")
        assert detail.status_code == 200, detail.json()
        assert detail.json()["result"] == rows[app_id]
    for app_id in fixtures.LISTED:
        assert "status" not in rows[app_id]
        assert rows[app_id]["providers"]


def test_held_cards_keep_search_category_connected_and_provider_filters(
    auth_client, held_bundle
):
    assert set(_rows(auth_client, q="microsoft")) == set(MICROSOFT)
    assert set(_rows(auth_client, q="microsoft", connected="false")) == set(MICROSOFT)
    assert _rows(auth_client, q="microsoft", connected="true") == {}
    excel = _rows(auth_client)["microsoft-excel"]
    assert "microsoft-excel" in _rows(auth_client, category=excel["ee_category"])
    # These are filters for addable providers, not unavailable declarations.
    assert _rows(auth_client, q="microsoft", kind="rest") == {}
    assert _rows(auth_client, q="microsoft", tier="listed") == {}


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_microsoft_server_remains_listed_for_cleanup(
    auth_client, user, held_bundle, enabled
):
    provider = next(
        p for p in held_bundle["providers"] if p["app"] == "microsoft-excel"
    )
    reference = f"microsoft-excel/{provider['id']}@{provider['version']}"
    fixtures._seed(
        user.organization,
        {
            "enabled": True,
            "servers": {
                "existing-excel": {
                    "url": "https://mcp.example.test/mcp",
                    "transport": "http",
                    "catalog_ref": reference,
                    "credential": {"mode": "none"},
                    "enabled": enabled,
                }
            },
        },
    )
    before = fixtures._versions(user.organization)
    app = _rows(auth_client)["microsoft-excel"]
    _assert_held(app)
    assert app["servers"] == [
        {
            "server_id": "existing-excel",
            "catalog_ref": reference,
            "provider": provider["id"],
            "version": provider["version"],
            "credential_mode": "none",
            "enabled": enabled,
            "connected": enabled,
            "virtual_server": None,
        }
    ]
    assert app["connected"] is enabled
    assert fixtures._versions(user.organization) == before


def test_client_group_also_identifies_a_held_app(auth_client, held_bundle, bundle):
    data = copy.deepcopy(held_bundle)
    for app in data["apps"]:
        if app["id"] == "microsoft-excel":
            app["id"] = "office-sheets"
            app["group"] = "other"
    for provider in data["providers"]:
        if provider["app"] == "microsoft-excel":
            provider["app"] = "office-sheets"
            assert provider["auth"]["oauth2"]["clientGroup"] == "microsoft"
    bundle(data)
    _assert_held(_rows(auth_client)["office-sheets"])


def test_disabled_microsoft_provider_cannot_fall_back_to_a_servable_plan(
    auth_client, held_bundle, bundle
):
    # An otherwise servable synthetic MCP shape isolates the enablement check
    # from REST/runtime refusal; no security/authority function is replaced.
    data = copy.deepcopy(held_bundle)
    data["providers"].append(
        fixtures._provider(
            "microsoft-excel",
            "synthetic-mcp",
            auth={"kind": "none"},
            tools=[fixtures._tool("read_sheet")],
            **{
                "x-gateway": {
                    "connectionEnablement": {
                        "schemaVersion": 1,
                        "enabled": False,
                        "operations": [],
                    }
                }
            },
        )
    )
    bundle(data)
    _assert_held(_rows(auth_client)["microsoft-excel"])
    assert isinstance(
        catalog.load_bundle().apps["microsoft-excel"].plans["synthetic-mcp"],
        catalog.Unavailable,
    )


def test_microsoft_app_with_an_available_provider_is_not_coming_soon(
    auth_client, held_bundle, bundle
):
    data = copy.deepcopy(held_bundle)
    data["providers"].append(
        fixtures._provider(
            "microsoft-excel",
            "synthetic-mcp",
            auth={"kind": "none"},
            tools=[fixtures._tool("read_sheet")],
        )
    )
    bundle(data)
    app = _rows(auth_client)["microsoft-excel"]
    assert "status" not in app
    assert [p["id"] for p in app["providers"]] == ["synthetic-mcp"]


def test_held_cards_stay_coming_soon_with_k2_apps_off(
    auth_client, held_bundle, settings
):
    settings.AGENTCC_MCP_K2_APPS_ENABLED = False
    rows = _rows(auth_client)
    # The flag decides what can be connected, not whether the app exists.
    _assert_held(rows["microsoft-outlook"])
    _assert_held(rows["microsoft-teams"])


@pytest.mark.parametrize("auth", ["malformed-auth", ["oauth2"], {"kind": "oauth2", "oauth2": "malformed-spec"},
    *[{"kind": "oauth2", "oauth2": {"clientGroup": value}} for value in ([], {}, 7, True, None)],
])
@pytest.mark.parametrize("upstream,k2", [(False, True), (True, False), (True, True)])
def test_malformed_held_auth_is_isolated_from_listing_and_detail(
    auth_client, held_bundle, bundle, settings, auth, upstream, k2,
):
    data = copy.deepcopy(held_bundle)
    for provider in data["providers"]:
        if provider["app"] == "microsoft-excel":
            provider["auth"] = auth
    # Deliberately invalid source: bypass only the fixture's JSON-schema check,
    # not any application/security/visibility gate.
    bundle(raw=json.dumps(data))
    settings.AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED = upstream
    settings.AGENTCC_MCP_K2_APPS_ENABLED = k2
    rows = _rows(auth_client)
    assert "microsoft-excel" not in rows
    assert "acme-docs" in rows
    assert auth_client.get(f"{CATALOG}microsoft-excel/").status_code == 404
    # A valid held sibling app is Coming soon whatever the sign-in flags.
    _assert_held(rows["microsoft-onedrive"])


def test_malformed_held_sibling_does_not_hide_valid_provider(
    auth_client, held_bundle, bundle,
):
    data = copy.deepcopy(held_bundle)
    record = copy.deepcopy(next(p for p in data["providers"] if p["app"] == "microsoft-excel"))
    record["id"] = "malformed-sibling"
    record["auth"] = "malformed-auth"
    data["providers"].append(record)
    bundle(raw=json.dumps(data))
    _assert_held(_rows(auth_client)["microsoft-excel"])


# Every unfinished catalog app is a Coming-soon card, like Microsoft, whatever
# the sign-in flags; only retired or unreadable records stay absent. Slack left
# this list when vendor MCP servers that take only a pre-registered client
# became a sign-in with the organization's own OAuth app; BigQuery's is in
# Google's pilot client group, which keeps its refusal.
REAL = Path(__file__).resolve().parents[1] / "connector_catalog" / "catalog.json"
UNFINISHED = {
    "google-bigquery": catalog.OAUTH_NOT_BUILT,
    "google-docs": catalog.KIND_NOT_SERVED,
}
# fern-files is added by its query-parameter key, so it is not among them.
FIXTURE_UNFINISHED = ("delta-mail", "echo-chat", "hydra-db")


def _real():
    # Read public declarations only, never a settings or credentials file.
    return json.loads(REAL.read_text())


@pytest.fixture
def real_bundle(bundle, settings):
    for name in (
        mcp_features.CATALOG,
        mcp_features.CREDENTIAL_VAULT,
        mcp_features.VIRTUAL_SERVERS,
        mcp_features.UPSTREAM_OAUTH,
        mcp_features.K2_APPS,
    ):
        setattr(settings, name, True)
    data = _real()
    bundle(raw=json.dumps(data))
    return data


def test_unfinished_real_apps_show_coming_soon(auth_client, real_bundle):
    rows = _rows(auth_client, limit=200)
    plans = catalog.load_bundle().apps
    for app_id, code in UNFINISHED.items():
        assert all(
            isinstance(plan, catalog.Unavailable)
            for plan in plans[app_id].plans.values()
        )
        assert code in {plan.code for plan in plans[app_id].plans.values()}
        _assert_held(rows[app_id])
        assert rows[app_id]["servers"] == []
        assert rows[app_id]["connected"] is False
        detail = auth_client.get(f"{CATALOG}{app_id}/")
        assert detail.status_code == 200, detail.json()
        assert detail.json()["result"] == rows[app_id]


def test_unfinished_real_apps_are_refused_as_before(auth_client, real_bundle):
    entries = catalog.load_bundle().apps
    for app_id, code in UNFINISHED.items():
        plan = entries[app_id].plans[entries[app_id].default_provider]
        assert plan.code == code
        response = auth_client.post(
            fixtures._add(app_id), fixtures._key(), format="json"
        )
        assert response.status_code == 409, response.json()
        assert response.json()["code"] == code


def test_sunset_and_unreadable_apps_stay_absent(auth_client, held_bundle, bundle):
    data = copy.deepcopy(held_bundle)
    tools = [fixtures._tool("read_page")]
    data["apps"].append(
        fixtures._app(
            "iris-old",
            "Iris Old",
            "A retired wiki.",
            category="productivity",
            categories=["docs"],
            default="iris-mcp",
            lifecycle={"status": "sunset"},
        )
    )
    data["providers"].append(
        fixtures._provider("iris-old", "iris-mcp", auth={"kind": "none"}, tools=tools)
    )
    data["apps"].append(
        fixtures._app(
            "jade-wiki",
            "Jade Wiki",
            "An unreadable wiki.",
            category="productivity",
            categories=["docs"],
            default="jade-mcp",
        )
    )
    data["providers"].append(
        fixtures._provider(
            "jade-wiki",
            "jade-mcp",
            auth={"kind": "none"},
            tools=tools,
            transport={"kind": "stdio", "url": "https://mcp.jade-wiki.example.com/mcp"},
        )
    )
    # Deliberately retired/invalid source: bypass only the fixture's
    # JSON-schema check, not any application/visibility gate.
    bundle(raw=json.dumps(data))
    plans = catalog.load_bundle().apps
    assert plans["iris-old"].plans["iris-mcp"].code == catalog.SUNSET
    assert plans["jade-wiki"].plans["jade-mcp"].code == catalog.DATA_INVALID
    rows = _rows(auth_client)
    for app_id in ("iris-old", "jade-wiki"):
        assert app_id not in rows
        assert auth_client.get(f"{CATALOG}{app_id}/").status_code == 404
    for app_id in FIXTURE_UNFINISHED:
        _assert_held(rows[app_id])


def test_real_catalog_lists_every_app_neither_sunset_nor_invalid(
    auth_client, real_bundle
):
    expected = {
        app["id"]
        for app in real_bundle["apps"]
        if app["lifecycle"]["status"] != "sunset"
    }
    assert len(expected) == 71
    response = auth_client.get(CATALOG, {"limit": 200})
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["count"] == len(expected)
    assert {app["id"] for app in body["results"]} == expected


def test_sign_in_flags_off_still_list_every_app_as_coming_soon(auth_client, bundle):
    with override_settings(
        AGENTCC_MCP_CATALOG_ENABLED=True,
        AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED=False,
        AGENTCC_MCP_K2_APPS_ENABLED=False,
    ):
        data = _real()
        bundle(raw=json.dumps(data))
        rows = _rows(auth_client, limit=200)
        assert set(rows) == {app["id"] for app in data["apps"]}
        assert len(rows) == 71
        held = {app_id for app_id, row in rows.items() if "status" in row}
        for app_id, row in rows.items():
            if app_id in held:
                _assert_held(row)
            else:
                assert row["providers"], app_id
        assert not [app_id for app_id in held if rows[app_id]["providers"]]
        assert set(MICROSOFT) <= held
        assert set(UNFINISHED) <= held
