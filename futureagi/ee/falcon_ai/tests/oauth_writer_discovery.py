"""Dependency-free source discovery shared by the Falcon writer regression tests.

The inventory is a minimum, not an allowlist: new runtime modules are always read.
Test/migration and configuration exclusions are explicit; credentials are never
imported or evaluated. Only the exact credential-boundary path is write-exempt.
"""

import ast
import json
import os
from pathlib import Path

CHOKEPOINT = "futureagi/ee/falcon_ai/oauth_registration.py"
_EXCLUDED_DIRS = {
    "tests",
    "migrations",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "settings",
}


def _excluded(path):
    return (
        any(part in _EXCLUDED_DIRS or part.startswith(".") for part in path.parts)
        or path.name.startswith("test_")
        or any(word in path.name.lower() for word in ("settings", "values", "compose"))
    )


def discover(root):
    root = Path(root)
    paths = []
    for directory, dirs, files in os.walk(root / "futureagi", followlinks=False):
        dirs[:] = sorted(name for name in dirs if not _excluded(Path(name)))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root)
            if path.suffix == ".py" and not _excluded(relative):
                if path.is_symlink():
                    raise AssertionError("writer census refuses symbolic-link source")
                paths.append(relative.as_posix())
    if not paths:
        raise AssertionError("empty writer census")
    inventory = json.loads(
        Path(__file__).with_name("oauth_writer_scope.json").read_text()
    )
    required = set(inventory["required_paths"])
    if not required or required - set(paths):
        raise AssertionError("writer census shrunk; reviewed runtime paths are missing")
    trees = {}
    for name in sorted(paths):
        try:
            trees[name] = ast.parse((root / name).read_text(), filename=name)
        except (SyntaxError, UnicodeError):
            raise AssertionError(f"unparseable runtime module: {name}") from None
    return trees


def module_name(path):
    parts = Path(path).with_suffix("").parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def import_target(node, path):
    if not node.level:
        return node.module or ""
    module = module_name(path)
    package = module if Path(path).name == "__init__.py" else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if node.level > len(parts):
        return ""
    prefix = parts[: len(parts) - node.level + 1]
    return ".".join(prefix + ([node.module] if node.module else []))


def imported_reference(node, path, exports):
    if isinstance(node, ast.Import):
        return any(exports.get(item.name) for item in node.names)
    if isinstance(node, ast.ImportFrom):
        target = import_target(node, path)
        symbols = exports.get(target, set())
        return any(
            (item.name == "*" and symbols)
            or item.name in symbols
            or exports.get(f"{target}.{item.name}")
            for item in node.names
        )
    return False


def _top_level_bindings(tree):
    """Include conditional exports, but never leak a function's local import."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(node))


def connector_exports(trees):
    exports = {
        "ee.falcon_ai.models": {"MCPConnector"},
        "ee.falcon_ai.serializers_connectors": {
            "MCPConnectorSerializer",
            "MCPConnectorCreateSerializer",
            "MCPConnectorUpdateSerializer",
        },
    }
    # Fixed-point symbol propagation covers nested modules, packages, relative
    # imports, aliased re-exports and cycles without executing any application.
    changed = True
    while changed:
        changed = False
        for path, tree in trees.items():
            known = exports.setdefault(module_name(path), set())
            before = len(known)
            modules = {}
            nodes = list(_top_level_bindings(tree))
            for node in nodes:
                if isinstance(node, ast.Import):
                    for item in node.names:
                        modules[item.asname or item.name] = item.name
                elif isinstance(node, ast.ImportFrom):
                    target = import_target(node, path)
                    symbols = exports.get(target, set())
                    for item in node.names:
                        if item.name == "*":
                            known.update(symbols)
                        elif item.name in symbols:
                            known.add(item.asname or item.name)
                        elif exports.get(f"{target}.{item.name}"):
                            modules[item.asname or item.name] = f"{target}.{item.name}"
            for node in nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                bound = isinstance(value, ast.Name) and value.id in known
                if isinstance(value, ast.Attribute):
                    source_module = modules.get(ast.unparse(value.value))
                    bound |= value.attr in exports.get(source_module, set())
                if bound:
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    known.update(
                        target.id for target in targets if isinstance(target, ast.Name)
                    )
            changed |= len(known) != before
    return exports
