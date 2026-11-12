#!/usr/bin/env python3
"""Check the source migration DAG without importing Django or application code.

Run with ``python3 -I -B scripts/check-migration-leaves.py``. Third-party Django
migrations are explicit external boundaries, not claimed as checked. Unknown
local dependencies, dynamic graph declarations and cycles fail closed.
"""

import argparse
import ast
import json
from pathlib import Path


EXTERNAL_APPS = frozenset({"auth", "contenttypes", "admin", "sessions"})


def assignments(body):
    return {
        target.id: node.value
        for node in body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def app_label(directory):
    path = directory.parent / "apps.py"
    labels = set()
    if path.exists():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                values = assignments(node.body)
                if "name" in values:
                    name = ast.literal_eval(values["name"])
                    label = ast.literal_eval(values["label"]) if "label" in values else name.rsplit(".", 1)[-1]
                    labels.add(label)
    if len(labels) > 1:
        raise ValueError(f"{path}: ambiguous AppConfig labels")
    return next(iter(labels), directory.parent.name)


def dependencies(value, user_app, path):
    if not isinstance(value, (ast.List, ast.Tuple)):
        raise ValueError(f"{path}: dynamic dependencies")
    result = []
    for node in value.elts:
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "migrations.swappable_dependency"
            and len(node.args) == 1
            and not node.keywords
            and ast.unparse(node.args[0]) == "settings.AUTH_USER_MODEL"
        ):
            result.append((user_app, "__first__"))
            continue
        try:
            edge = ast.literal_eval(node)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}: unsupported dependency {ast.unparse(node)}") from exc
        if not isinstance(edge, (tuple, list)) or len(edge) != 2 or not all(isinstance(x, str) for x in edge):
            raise ValueError(f"{path}: invalid dependency {edge!r}")
        result.append(tuple(edge))
    return result


def scan(root):
    settings = root / "tfc/settings/settings.py"
    user_model = assignments(ast.parse(settings.read_text()).body).get("AUTH_USER_MODEL")
    if user_model is None:
        raise ValueError(f"{settings}: AUTH_USER_MODEL missing")
    user_app = ast.literal_eval(user_model).split(".", 1)[0]
    nodes, directories = {}, {}
    for directory in sorted(root.glob("**/migrations")):
        if any(part in {".venv", "node_modules", "__pycache__"} for part in directory.parts):
            continue
        files = [p for p in sorted(directory.glob("*.py")) if not p.name.startswith(("_", "~"))]
        if not files:
            continue
        label = app_label(directory)
        if label in directories:
            raise ValueError(f"duplicate migration app label: {label}")
        directories[label] = str(directory.relative_to(root))
        for path in files:
            tree = ast.parse(path.read_text(), filename=str(path))
            classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration"]
            if len(classes) != 1:
                raise ValueError(f"{path}: expected one Migration class")
            values = assignments(classes[0].body)
            if "dependencies" not in values:
                raise ValueError(f"{path}: dependencies missing")
            for field in ("run_before", "replaces"):
                if field in values and ast.literal_eval(values[field]):
                    raise ValueError(f"{path}: nonempty {field} needs explicit graph support")
            nodes[(label, path.stem)] = {
                "path": str(path.relative_to(root)),
                "dependencies": dependencies(values["dependencies"], user_app, path),
            }
    if not nodes:
        raise ValueError("no migration nodes found")
    if user_app not in directories:
        raise ValueError(f"AUTH_USER_MODEL app not found: {user_app}")
    roots = {}
    for app in directories:
        roots[app] = [key for key, row in nodes.items() if key[0] == app and not any(dep[0] == app for dep in row["dependencies"])]
    parents = {key: set() for key in nodes}
    children = {key: set() for key in nodes}
    errors, external = [], []
    for key, row in nodes.items():
        for dep in row["dependencies"]:
            if dep[0] not in directories:
                if dep[0] not in EXTERNAL_APPS:
                    errors.append(f"{row['path']}: missing app {dep[0]}")
                else:
                    external.append({"source": row["path"], "dependency": list(dep)})
                continue
            if dep[1] == "__first__":
                if len(roots[dep[0]]) != 1:
                    errors.append(f"{row['path']}: ambiguous root for {dep[0]}")
                    continue
                dep = roots[dep[0]][0]
            if dep not in nodes:
                errors.append(f"{row['path']}: missing migration {dep[0]}.{dep[1]}")
                continue
            parents[key].add(dep)
            children[dep].add(key)
    # Kahn's algorithm covers cross-app cycles too, without recursive depth limits.
    counts = {key: len(value) for key, value in parents.items()}
    ready = [key for key, count in counts.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for child in children[key]:
            counts[child] -= 1
            if counts[child] == 0:
                ready.append(child)
    if visited != len(nodes):
        errors.append("cycle or cycle-dependent nodes: " + ", ".join(".".join(key) for key in sorted(counts) if counts[key]))
    apps = {}
    for app, directory in sorted(directories.items()):
        keys = [key for key in nodes if key[0] == app]
        leaves = sorted(key[1] for key in keys if not any(child[0] == app for child in children[key]))
        apps[app] = {"directory": directory, "migration_count": len(keys), "leaves": leaves}
        if len(leaves) != 1:
            errors.append(f"{app}: expected one leaf, found {len(leaves)}: {', '.join(leaves)}")
    return {
        "method": "AST_ONLY_NO_DJANGO",
        "scope": "all source migration apps under backend root; third-party migrations are external",
        "status": "PASS" if not errors else "FAIL",
        "migration_count": len(nodes),
        "app_count": len(apps),
        "apps": apps,
        "external_dependencies": external,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "futureagi")
    args = parser.parse_args()
    try:
        report = scan(args.root.resolve())
    except (ValueError, SyntaxError, OSError) as exc:
        report = {"status": "FAIL", "method": "AST_ONLY_NO_DJANGO", "errors": [str(exc)]}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
