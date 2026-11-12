"""Alias-independent writer census and regression probes for the census itself."""

import ast
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "oauth_writer_discovery", Path(__file__).with_name("oauth_writer_discovery.py")
)
discovery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(discovery)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


_WRITES = {
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


def _model_reference(node):
    if isinstance(node, ast.Import):
        return any(item.name.startswith("ee.falcon_ai") for item in node.names)
    if isinstance(node, ast.ImportFrom):
        model_module = node.module in (
            "ee.falcon_ai.models",
            "ee.falcon_ai.serializers_connectors",
        ) or (node.level > 0 and node.module in ("models", "serializers_connectors"))
        return (
            model_module
            and any("Connector" in item.name or item.name == "*" for item in node.names)
        ) or (
            node.module == "ee.falcon_ai"
            and any(
                item.name in ("models", "serializers_connectors") for item in node.names
            )
        )
    if isinstance(node, ast.ClassDef):
        return node.name == "MCPConnector"
    if isinstance(node, ast.Attribute):
        return node.attr in ("MCPConnector", "falcon_mcp_connectors")
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_model"
    ):
        return any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.lower() in ("mcpconnector", "falcon_ai.mcpconnector")
            for arg in node.args
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "falcon_ai_mcpconnector" in node.value
    return False


def _writers(source, model_reference=_model_reference):
    tree = ast.parse(source) if isinstance(source, str) else source
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    module_bound = any(
        model_reference(child)
        for node in tree.body
        for child in ([node] if isinstance(node, definitions) else ast.walk(node))
    )
    # A local import binds only that function/class scope; module-level imports
    # bind every scope. No reliance on variable spellings such as 'connector'.
    scopes = (
        [tree]
        if module_bound
        else [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and any(model_reference(child) for child in ast.walk(node))
        ]
    )
    return {
        node.lineno
        for scope in scopes
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _WRITES
            or isinstance(node.func, ast.Name)
            and node.func.id == "erase_and_delete"
        )
    }


def writer_violations(trees):
    exports = discovery.connector_exports(trees)
    violations = []
    for path, tree in trees.items():
        if path == discovery.CHOKEPOINT:
            continue

        def reference(node, path=path):
            return _model_reference(node) or discovery.imported_reference(
                node, path, exports
            )

        violations.extend((path, line) for line in _writers(tree, reference))
    return violations


def test_all_connector_bound_scopes_use_chokepoint():
    root = Path(__file__).resolve().parents[4]
    trees = discovery.discover(root)
    violations = writer_violations(trees)
    check(bool(trees), "empty writer census")
    check(not violations, f"aliased connector writer bypasses chokepoint: {violations}")


def test_census_detects_aliases_dynamic_fields_and_inherited_operations():
    for operation in (
        "save()",
        "save_base()",
        "update(**changes)",
        "bulk_update(rows, columns)",
        "update_or_create(defaults=changes)",
    ):
        source = (
            "from ee.falcon_ai.models import MCPConnector as Row\n"
            "def write(record, changes, rows, columns):\n"
            f"    record.{operation}\n"
        )
        check(bool(_writers(source)), "census misses aliased writer")
    check(
        bool(
            _writers(
                "def migration_bypass(apps):\n"
                "    row_type = apps.get_model('falcon_ai', 'MCPConnector')\n"
                "    row_type.objects.update(is_verified=True)\n"
            )
        ),
        "census misses dynamic model lookup",
    )


def test_census_detects_module_relative_and_async_aliases():
    for source in (
        "import ee.falcon_ai.models as storage\nx = storage.MCPConnector()\nx.save()",
        "from ee.falcon_ai import models as storage\nx = storage.MCPConnector()\nx.save()",
        "from .models import MCPConnector as Row\nrecord.update(**changes)",
        "Row = apps.get_model('falcon_ai.MCPConnector')\nRow.objects.update(**changes)",
        "def local_write(record):\n    from .models import MCPConnector\n    record.save()",
        "def relation_write(org):\n    org.falcon_mcp_connectors.update(**changes)",
        "from ee.falcon_ai.models import MCPConnector\nasync def write(row):\n    await row.asave()",
    ):
        check(bool(_writers(source)), "census misses indirect connector writer")


def test_census_does_not_confuse_other_tables_with_connectors():
    check(
        not _writers("from ee.falcon_ai.models import Skill\nSkill.objects.create()"),
        "census incorrectly labels another table",
    )
