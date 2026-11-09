"""Independent source census; no external receipt/allowlist or host paths."""

import ast
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "oauth_writer_alias_census",
    Path(__file__).with_name("test_oauth_writer_alias_census.py"),
)
alias_census = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alias_census)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


WRITES = {
    "save",
    "save_base",
    "update",
    "bulk_update",
    "bulk_create",
    "create",
    "get_or_create",
    "update_or_create",
    "delete",
    "raw",
    "execute",
    "asave",
    "adelete",
    "aupdate",
    "abulk_update",
    "abulk_create",
    "acreate",
    "aget_or_create",
    "aupdate_or_create",
}


def test_no_connector_persistence_outside_chokepoint():
    root = Path(__file__).resolve().parents[4]
    # A minimum path inventory rejects shrinkage but never limits discovery.
    # Imported/re-exported model aliases are checked across the entire tree.
    trees = alias_census.discovery.discover(root)
    violations = alias_census.writer_violations(trees)
    for name, tree in trees.items():
        if name == alias_census.discovery.CHOKEPOINT:
            continue
        # Retain the original Falcon-specific receiver/credential-field check in
        # addition to the alias-independent import graph.
        if "/ee/falcon_ai/" not in name:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in WRITES:
                continue
            receiver = ast.unparse(node.func.value)
            # Connector references, dynamic kwargs and all OAuth-module writes
            # are forbidden. Generic inherited admin saves get a separate test.
            relevant = any(word in receiver.lower() for word in ("connector", "mcp"))
            relevant |= name.endswith(("oauth_client.py", "/disconnect.py"))
            relevant |= any(
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and (
                    child.value.startswith("oauth_")
                    or child.value in ("auth_header_value", "is_verified")
                )
                for child in ast.walk(node)
            )
            if relevant:
                violations.append((name, node.lineno))
    check(bool(trees), "source census scope is empty")
    check(not violations, f"connector writer outside chokepoint: {violations}")


def test_admin_never_inherits_full_row_or_bulk_delete_persistence():
    from ee.falcon_ai.admin import MCPConnectorAdmin

    check("save_model" in MCPConnectorAdmin.__dict__, "admin inherits full-row save")
    check("delete_queryset" in MCPConnectorAdmin.__dict__, "admin inherits bulk delete")
    check(
        "has_add_permission" in MCPConnectorAdmin.__dict__,
        "admin permits unguarded creation",
    )
