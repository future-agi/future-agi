"""Per-tool scopes: each oauth2 tool names the minimum scopes it needs, and
together they equal the provider's auth.oauth2.scopes (apart from protocol
scopes such as offline_access), so a connection asks for nothing its tools
do not use."""

from __future__ import annotations

from conftest import edit_overlay, edit_record, output, provider_dir, read_yaml, run_ok, run_refused, tool
from restgen.l1 import check_provider

GMAIL = "apps/gmail/providers/futureagi-rest/tools.yaml"
READONLY = "https://www.googleapis.com/auth/gmail.readonly"
MODIFY = "https://www.googleapis.com/auth/gmail.modify"


def _record(catalog, app):
    return provider_dir(catalog, app).parent / "futureagi-rest.yaml"


def test_scope_union_equals_the_requested_scopes(generated):
    for app in ("gmail", "google-calendar", "microsoft-outlook", "microsoft-teams", "slack"):
        requested = read_yaml(_record(generated, app))["auth"]["oauth2"]["scopes"]
        needed = {s for t in output(generated, app)["tools"] for s in t["scopes"]}
        assert needed == set(requested) - {"offline_access"}, app


def test_scope_union_requested_scope_no_tool_needs_refused(app_catalog):
    catalog = app_catalog("gmail")
    labels = "https://www.googleapis.com/auth/gmail.labels"
    edit_record(_record(catalog, "gmail"), lambda d: d["auth"]["oauth2"]["scopes"].append(labels))
    err = run_refused(catalog)
    assert f"{GMAIL}: auth.oauth2.scopes requests {labels}, which no tool needs (the scope union must equal it)" in err


def test_scope_union_tool_scope_not_requested_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_record(_record(catalog, "gmail"), lambda d: d["auth"]["oauth2"]["scopes"].remove(MODIFY))
    err = run_refused(catalog)
    assert f"(modify_thread): scopes: {MODIFY} is not in the provider's auth.oauth2.scopes" in err


def test_scope_union_tool_without_scopes_refused(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "list_labels").pop("scopes"))
    err = run_refused(catalog)
    assert "(list_labels): scopes: an oauth2 provider's tool lists the scopes it needs (scopes: [] when none)" in err


def test_scope_union_protocol_scopes_are_exempt(generated):
    requested = read_yaml(_record(generated, "microsoft-outlook"))["auth"]["oauth2"]["scopes"]
    assert "offline_access" in requested
    assert all("offline_access" not in t["scopes"] for t in output(generated, "microsoft-outlook")["tools"])


def test_scope_union_scope_the_spec_does_not_accept_needs_a_source(app_catalog):
    catalog = app_catalog("gmail")
    send = "https://www.googleapis.com/auth/gmail.send"
    edit_overlay(catalog, "gmail", lambda d: tool(d, "list_labels").update(scopes=[READONLY, send]))
    err = run_refused(catalog)
    assert "(list_labels): scopes: the spec says gmail.users.labels.list accepts" in err
    assert f"not {send} (add scopeSource with the vendor page that says otherwise)" in err


def test_scope_union_spec_without_scopes_needs_a_source(app_catalog):
    # Microsoft Graph's description names no scope per operation.
    catalog = app_catalog("microsoft-outlook")
    edit_overlay(catalog, "microsoft-outlook", lambda d: tool(d, "get_message").pop("scopeSource"))
    err = run_refused(catalog)
    assert "(get_message): scopes: the spec names no scopes for GET /me/messages/{message-id}; add scopeSource" in err


def test_scope_union_api_key_provider_tools_list_no_scopes(app_catalog):
    catalog = app_catalog("adyen")
    edit_overlay(catalog, "adyen", lambda d: tool(d, "get_transfer").update(scopes=["transfers.read"]))
    err = run_refused(catalog)
    assert "(get_transfer): scopes: the provider does not use oauth2, so its tools list no scopes" in err


def test_scope_union_counts_curated_items_too():
    generated_tool = {"name": "a", "scopes": ["x"], "inputSchema": {"type": "object"}, "annotations": {}}
    item = {"name": "b", "scopes": ["y"], "inputSchema": {"type": "object"}, "annotations": {}}
    assert check_provider([generated_tool], [item], ["x", "y"], "", "w") == []
    assert check_provider([generated_tool], [], ["x", "y"], "", "w") == [
        "w: auth.oauth2.scopes requests y, which no tool needs (the scope union must equal it)"]  # fmt: skip


def test_scope_union_with_an_unchanged_overlay_passes(app_catalog):
    catalog = app_catalog("gmail")
    assert "ok" in run_ok(catalog, "--check")
