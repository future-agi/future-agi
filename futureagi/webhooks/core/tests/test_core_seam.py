"""The seam rule of DESIGN.md section 22.1, checked without Django and without import-linter installed.

Nothing in ``webhooks/core/`` (runtime modules and the core tests) imports Django, Temporal, ``tfc``, ``futureagi``,
the Django app ``webhooks`` outside ``webhooks.core``, or the reference rules in ``conformance/tools`` (``whconf``),
and the runtime modules use the standard library only (``cryptography`` is allowed). ``futureagi/.importlinter``
states the same contract for ``lint-imports``; the last test keeps that file's first contract naming every
first-party top-level package of ``futureagi/`` and its layers contract exhaustive, so a new app or a new adapter
module cannot slip past it.
"""

from __future__ import annotations

import ast
import configparser
import sys
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
FUTUREAGI = CORE.parents[1]
FORBIDDEN = (
    "django",
    "rest_framework",
    "temporalio",
    "tfc",
    "futureagi",
    "whconf",
    "psycopg",
    "urllib3",
    "requests",
)
ALLOWED_THIRD_PARTY = ("cryptography",)


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [
                (a.name, node.level if hasattr(node, "level") else 0)
                for a in node.names
            ]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level))
    return out


def _files(runtime_only: bool) -> list[Path]:
    files = [p for p in CORE.rglob("*.py") if "conformance" not in p.parts]
    if runtime_only:
        files = [p for p in files if "tests" not in p.parts]
    return files


class SeamTests(unittest.TestCase):
    def test_runtime_modules_import_only_stdlib_and_the_core(self):
        stdlib = set(sys.stdlib_module_names) | {"__future__"}
        files = _files(runtime_only=True)
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            for name, level in _imports(path):
                with self.subTest(file=path.name, module=name):
                    if level:  # relative: stays inside webhooks.core
                        continue
                    top = name.split(".")[0]
                    ok = (
                        top in stdlib
                        or top in ALLOWED_THIRD_PARTY
                        or name.startswith("webhooks.core")
                    )
                    self.assertTrue(ok, f"{path.relative_to(CORE)} imports {name}")

    def test_nothing_in_core_imports_the_framework_or_the_reference(self):
        for path in _files(runtime_only=False):
            for name, level in _imports(path):
                if level:
                    continue
                with self.subTest(file=str(path.relative_to(CORE)), module=name):
                    top = name.split(".")[0]
                    self.assertNotIn(top, FORBIDDEN)
                    if top == "webhooks":
                        self.assertTrue(
                            name == "webhooks.core"
                            or name.startswith("webhooks.core."),
                            name,
                        )

    def test_the_core_loads_with_django_absent(self):
        import importlib

        blocked = [m for m in sys.modules if m == "django" or m.startswith("django.")]
        if blocked:
            self.skipTest(
                "Django already imported in this process (the FA test run); the AST checks still apply"
            )
        for mod in (
            "addresses",
            "attempt",
            "dispatch",
            "fanout",
            "ids",
            "ipclass",
            "memstore",
            "payloads",
            "ports",
            "redact",
            "runevent",
            "schedule",
            "signing",
            "watchdog",
        ):
            importlib.import_module(f"webhooks.core.{mod}")
        self.assertFalse(
            any(m == "django" or m.startswith("django.") for m in sys.modules)
        )

    def test_the_import_linter_contract_names_every_first_party_package(self):
        cfg = configparser.ConfigParser()
        cfg.read(FUTUREAGI / ".importlinter", encoding="utf-8")
        forbidden = set(
            cfg["importlinter:contract:webhooks-core-framework-free"][
                "forbidden_modules"
            ].split()
        )
        first_party = {
            d.name
            for d in FUTUREAGI.iterdir()
            if d.is_dir()
            and d.name.isidentifier()
            and d.name not in ("webhooks", "bin", "docker")
            and not d.name.startswith(".")
            and ((d / "__init__.py").exists() or any(d.glob("*.py")))
        }
        self.assertGreaterEqual(len(first_party), 10)
        self.assertEqual(sorted(first_party - forbidden), [])
        layers = cfg["importlinter:contract:webhooks-core-below-adapters"]
        self.assertEqual(layers["exhaustive"].strip(), "true")
        listed = set(layers["layers"].replace(":", " ").split())
        app = {
            p.stem if p.suffix == ".py" else p.name
            for p in (FUTUREAGI / "webhooks").iterdir()
            if (p.suffix == ".py" and p.name != "__init__.py")
            or (p.is_dir() and (p / "__init__.py").exists())
        }
        self.assertEqual(sorted(app - listed), [])


if __name__ == "__main__":
    unittest.main()
