"""Keep creation centralized and legacy key types confined to storage vocabulary."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def production_sources():
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if (
            any(
                part in {"tests", "migrations", ".venv", "node_modules", "__pycache__"}
                for part in relative.parts
            )
            or path.name.startswith("test_")
            or path.name == "conftest.py"
        ):
            continue
        yield relative, ast.parse(path.read_text(), filename=str(relative))


def test_only_session_issuer_constructs_personal_keys():
    violations = []
    for path, tree in production_sources():
        aliases = {"OrgApiKey"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases.update(
                    name.asname or name.name
                    for name in node.names
                    if name.name == "OrgApiKey"
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            source = ast.unparse(node.func)
            constructs = (
                source in aliases
                or any(source.startswith(alias + ".") for alias in aliases)
                and source.rsplit(".", 1)[-1]
                in {
                    "create",
                    "get_or_create",
                    "update_or_create",
                    "bulk_create",
                    "create_with_context",
                }
            )
            if constructs and str(path) != "accounts/views/keys.py":
                violations.append(f"{path}:{node.lineno}")
    assert violations == []


def test_retired_key_type_literals_have_one_storage_definition():
    violations = []
    for path, tree in production_sources():
        if str(path) == "accounts/api_key_constants.py":
            continue
        # A persona or telemetry event can be called system. This guard covers
        # key-bearing modules, not unrelated product vocabularies.
        if not any(
            isinstance(node, ast.Name) and node.id == "OrgApiKey"
            for node in ast.walk(tree)
        ):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in {"type", "type__in"}:
                if any(
                    isinstance(item, ast.Constant) and item.value in {"system", "mcp"}
                    for item in ast.walk(node.value)
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ):
                    violations.append(f"{path}:{node.lineno}")
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == "type"
            ):
                if any(
                    isinstance(item, ast.Constant) and item.value in {"system", "mcp"}
                    for item in node.comparators
                ):
                    violations.append(f"{path}:{node.lineno}")
    assert violations == []


def test_no_ai_key_issuer_registered():
    import ai_tools.tools  # noqa: F401
    from ai_tools.registry import registry

    assert registry.get("create_api_key") is None
