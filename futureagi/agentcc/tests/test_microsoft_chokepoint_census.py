"""Static custody census: adding a client/token decrypt requires an explicit guard."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "services"
# Generic decrypt primitives have no client identity. Complete mediation belongs
# at these typed boundaries, not at an unrelated caller's routing label check.
DECRYPTS = {
    "connector_oauth_clients.py": {"_org_secret"},
    "mcp_credentials.py": {"_decrypted"},
    "mcp_upstream_tokens.py": {"_refresh", "_served"},
    "mcp_revocation.py": {"revoke"},
    "mcp_upstream_oauth.py": {"_exchange_code"},
}
OTHER_CUSTODY = {
    "connector_oauth_clients.py": {"_env_client", "token_exchange_secret"},
    "microsoft_client_identity.py": {"platform_file"},
    "mcp_vendor_credentials.py": {"vendor_client"},
}


def calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and (
        isinstance(n.func, ast.Name) and n.func.id == name
        or isinstance(n.func, ast.Attribute) and n.func.attr == name
    )]


def definitions(path):
    return {n.name: n for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_entire_agentcc_tree_has_no_unclassified_decrypts():
    from agentcc.tests.microsoft_custody_census import CLASSIFIED, discover, unclassified

    sites = discover(ROOT.parent)
    assert unclassified(sites) == []
    runtime = {(path, symbol) for path, symbol, _ in sites if not path.startswith("tests/")}
    assert runtime == set(CLASSIFIED)


def test_locked_selection_guards_the_row_passed_to_protected_serving():
    funcs = definitions(ROOT / "mcp_upstream_tokens.py")
    selected = funcs["_policy_selected_answer"]
    assert calls(selected, "select_for_update")
    assert calls(selected, "_microsoft_refusal")
    assert calls(selected, "_fresh")
    assert calls(funcs["_microsoft_refusal"], "deny_if_excluded")
    assert calls(funcs["_policy_answer"], "atomic")
    assert calls(funcs["_policy_answer"], "lock_org")
    assert calls(funcs["_policy_answer"], "lock_identity")
    for name in ("shared_token", "upstream_token"):
        assert calls(funcs[name], "_policy_answer")
    assert min(n.lineno for n in calls(selected, "_microsoft_refusal")) < min(
        n.lineno for n in calls(selected, "_fresh")
    )


def test_every_typed_client_or_token_decrypt_has_the_identity_chokepoint():
    for filename, expected in DECRYPTS.items():
        funcs = definitions(ROOT / filename)
        actual = {name for name, fn in funcs.items() if calls(fn, "decrypt_token")}
        assert actual == expected, (filename, actual ^ expected)
        for name in expected:
            fn = funcs[name]
            checks = calls(fn, "deny_if_excluded")
            assert checks, (filename, name, "unguarded typed custody boundary")
            assert min(c.lineno for c in checks) < min(c.lineno for c in calls(fn, "decrypt_token"))


def test_platform_and_reveal_boundaries_route_through_same_chokepoint():
    for filename, expected in OTHER_CUSTODY.items():
        funcs = definitions(ROOT / filename)
        for name in expected:
            assert calls(funcs[name], "deny_if_excluded"), (filename, name)
    # No alternate caller may bypass the guarded org-client decrypt helper.
    sites = []
    for path in ROOT.glob("*.py"):
        for name, fn in definitions(path).items():
            if calls(fn, "_org_secret"):
                sites.append((path.name, name))
                assert calls(fn, "deny_if_excluded")
    assert sites == [("connector_oauth_clients.py", "_org_client")]
