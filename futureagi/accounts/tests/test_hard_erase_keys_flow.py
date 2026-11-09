"""Offline lifecycle key discovery and frozen ORM selection regressions."""

import ast
import runpy
import unittest
from copy import copy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "services"


class Refused(Exception):
    pass


def namespace():
    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module.startswith(("accounts.", "django.")):
                return ast.Pass()
            return node

    tree = ast.fix_missing_locations(
        Imports().visit(ast.parse((ROOT / "hard_erase_keys.py").read_text()))
    )
    graph = runpy.run_path(str(ROOT / "hard_erase_graph.py"))

    def refuse(message):
        raise Refused(message)

    def keys(*, organizations=(), people=()):
        return frozenset(
            (kind, value)
            for kind, values in (("organization", organizations), ("person", people))
            for value in values
            if value is not None
        )

    ns = {
        "copy": copy,
        "MAX_NODES": graph["MAX_NODES"],
        "projection": graph["projection"],
        "identity_keys": keys,
        "_refuse": refuse,
        "_ownership_graph": lambda rows, using: graph["OwnershipGraph"](
            rows, lambda *args: []
        ),
    }
    exec(compile(tree, "hard_erase_keys.py", "exec"), ns)
    return ns


def field(name, *, primary_key=False):
    return SimpleNamespace(
        name=name, attname=name, is_relation=False, primary_key=primary_key
    )


def row(pk, org, user):
    meta = SimpleNamespace(
        label_lower="accounts.servicegrant",
        concrete_fields=[
            field("jti", primary_key=True),
            field("org"),
            field("user"),
            field("workspace"),
        ],
        pk=SimpleNamespace(attname="jti"),
    )
    return SimpleNamespace(
        pk=pk, jti=pk, org=org, user=user, workspace=None, _meta=meta
    )


class KeyDiscoveryTests(unittest.TestCase):
    def test_uuid_grant_keys_cover_both_organization_and_person(self):
        ns = namespace()
        self.assertEqual(
            ns["row_keys"]([row("g", "o", "u")], "default"),
            {
                ("organization", "o"),
                ("person", "u"),
            },
        )


if __name__ == "__main__":
    unittest.main()
