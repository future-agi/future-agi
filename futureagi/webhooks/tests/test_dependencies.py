"""Every third-party package the webhooks code imports is installed by a base install (no extras).

The cloud run of 665bc08db2 under FA's pytest configuration (``uv sync --locked``, no extras) failed with
``ModuleNotFoundError: No module named 'tldextract'``: ``webhooks.config.public_suffix`` imported it lazily, and FA got
it only through ``presidio-analyzer`` in the optional ``pii`` extra. A base image (``Dockerfile.oss`` without
``EXTRAS=pii``, CI's ``uv sync --no-dev`` job) would have failed every URL check at the first delivery. This test
walks the base dependency closure in ``uv.lock`` (the project's own dependencies, never its extras or dev group) and
requires every import in the webhooks app, lazy ones included, to land inside it. Stdlib only: no Django, no install.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # futureagi/
SCANNED = [ROOT / "webhooks", ROOT / "tfc" / "utils" / "ssrf_post.py"]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _modules() -> list[Path]:
    out: list[Path] = []
    for p in SCANNED:
        files = [p] if p.is_file() else sorted(p.rglob("*.py"))
        out += [f for f in files if "tests" not in f.relative_to(ROOT).parts]
    return out


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _first_party(name: str, path: Path) -> bool:
    for d in [ROOT, *[p for p in path.parents if ROOT in p.parents]]:
        if (d / name).is_dir() or (d / f"{name}.py").is_file():
            return True
    return False


def base_closure(lock: dict) -> set[str]:
    """Normalised names of every package a base install of the project gets: its dependencies, recursively, with the
    extras each edge asks for, and never the project's own optional-dependencies or dev group."""
    by_name: dict[str, list[dict]] = {}
    for pkg in lock["package"]:
        by_name.setdefault(_norm(pkg["name"]), []).append(pkg)
    (root,) = [p for p in lock["package"] if p.get("source", {}).get("virtual") == "."]
    seen: set[str] = set()
    todo = [
        (_norm(d["name"]), tuple(d.get("extra", ())))
        for d in root.get("dependencies", [])
    ]
    done: set[tuple[str, tuple[str, ...]]] = set()
    while todo:
        name, extras = todo.pop()
        if (name, extras) in done:
            continue
        done.add((name, extras))
        seen.add(name)
        for pkg in by_name.get(name, []):
            edges = list(pkg.get("dependencies", []))
            for x in extras:
                edges += pkg.get("optional-dependencies", {}).get(x, [])
            todo += [(_norm(d["name"]), tuple(d.get("extra", ()))) for d in edges]
    return seen


def _distributions(import_name: str) -> set[str]:
    found = importlib.metadata.packages_distributions().get(import_name) or [
        import_name
    ]
    return {_norm(d) for d in found}


def missing_from_base(lock: dict) -> dict[str, list[str]]:
    closure = base_closure(lock)
    missing: dict[str, list[str]] = {}
    for path in _modules():
        for name in _imports(path):
            if name in sys.stdlib_module_names or _first_party(name, path):
                continue
            if not _distributions(name) & closure:
                missing.setdefault(name, []).append(str(path.relative_to(ROOT)))
    return missing


def test_every_third_party_import_is_in_a_base_install():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    missing = missing_from_base(lock)
    assert not missing, (
        "imported by the webhooks code but installed only through an extra or the dev group; declare each in "
        f"pyproject.toml [project].dependencies and in uv.lock: {missing}"
    )


def test_the_closure_follows_edge_extras_and_leaves_out_project_extras_and_dev():
    """The walk on a lock built by hand: a base edge's extra is followed (a[x] reaches b), while the project's own
    extra (c) and dev group (d) stay out, as does what only they reach (e)."""
    lock = {
        "package": [
            {
                "name": "proj",
                "source": {"virtual": "."},
                "dependencies": [{"name": "A_Lib", "extra": ["x"]}],
                "optional-dependencies": {"pii": [{"name": "c"}]},
                "dev-dependencies": {"dev": [{"name": "d"}]},
            },
            {
                "name": "a-lib",
                "dependencies": [],
                "optional-dependencies": {"x": [{"name": "b"}], "y": [{"name": "e"}]},
            },
            {"name": "b", "dependencies": []},
            {"name": "c", "dependencies": [{"name": "e"}]},
            {"name": "d", "dependencies": []},
            {"name": "e", "dependencies": []},
        ]
    }
    assert base_closure(lock) == {"a-lib", "b"}


def test_the_real_lock_keeps_extra_only_and_dev_only_packages_out():
    closure = base_closure(tomllib.loads((ROOT / "uv.lock").read_text()))
    assert {"django", "temporalio", "psycopg", "cryptography", "tldextract"} <= closure
    assert "presidio-analyzer" not in closure  # the pii extra
    assert "pytest" not in closure  # the dev group
