"""Fail-closed transport-store regressions, also loaded by the generative suite.

Every mutation is source-only and receives the complete background module audit.
No fixture imports application code or executes an outbound operation.
"""

import ast
import copy
import runpy
import unittest
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
c = runpy.run_path(str(HERE / "falcon_send_census.py"))
a = runpy.run_path(str(HERE / "falcon_send_admission.py"))


class StructuralStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources, _ = c["load_sources"]()

    def assert_store_rejected(self, fixture, target, nested=""):
        module = "agentic_eval.core.embeddings.serving_client"
        owner = "ModelServingClient._require_serving" + nested
        tree = ast.parse(self.sources[module])
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "ModelServingClient"
        )
        method = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "_require_serving"
        )
        method.body[:0] = ast.parse(fixture).body
        sites, errors = c["audit"](
            {**self.sources, module: ast.unparse(tree)},
            a["admission"],
            a["EXCEPTIONS"],
            a["EXCEPTION_COUNTS"],
        )
        matching = [
            s
            for m, s in sites
            if m == module
            and s["owner"] == owner
            and s.get("structural")
            and target in s["call"]
        ]
        self.assertTrue(matching, "transport store escaped the complete audit")
        for site in matching:
            self.assertIn(
                f"{module}:{site['line']} {owner} {site['call']}: unadmitted", errors
            )

    def test_reverse_imported_and_cached_roots(self):
        for value in ("requests", "self.session"):
            with self.subTest(value=value):
                self.assert_store_rejected(
                    "import requests\nfrom types import SimpleNamespace\n"
                    "holder = SimpleNamespace()\nother = holder\n"
                    f"other.pipe = {value}\noutbound = holder.pipe\n"
                    "outbound.get('https://offline.invalid', timeout=120)",
                    "other.pipe",
                )

    def test_shared_holder_reverse_writes(self):
        for value in ("requests", "self.session"):
            with self.subTest(value=value):
                self.assert_store_rejected(
                    "import requests\nfrom types import SimpleNamespace\n"
                    "holder = SimpleNamespace()\nwriter = holder\nreader = holder\n"
                    f"writer.pipe = {value}\noutbound = reader.pipe\n"
                    "outbound.get('https://offline.invalid', timeout=120)",
                    "writer.pipe",
                )

    def test_reverse_bound_methods_and_with_continuations(self):
        for value in ("requests", "self.session"):
            for continuation in (
                "emit = holder.pipe.get\nemit('https://offline.invalid')",
                "with holder.pipe as outbound:\n    outbound.get('https://offline.invalid')",
            ):
                with self.subTest(value=value, continuation=continuation):
                    self.assert_store_rejected(
                        "import requests\nfrom types import SimpleNamespace\n"
                        "holder = SimpleNamespace()\nother = holder\n"
                        f"other.pipe = {value}\n" + continuation,
                        "other.pipe",
                    )
        self.assert_store_rejected(
            "from types import SimpleNamespace\nholder = SimpleNamespace()\n"
            "other = holder\nother.emit = self.session.get\n"
            "emit = holder.emit\nemit('https://offline.invalid')",
            "other.emit",
        )

    def test_reverse_nested_scope_and_deeper_paths(self):
        for value in ("requests", "self.session"):
            with self.subTest(value=value):
                self.assert_store_rejected(
                    "import requests\nfrom types import SimpleNamespace\n"
                    "holder = SimpleNamespace()\nother = holder\n"
                    f"def save():\n    other.pipe = {value}\n"
                    "save()\noutbound = holder.pipe\noutbound.get('url')",
                    "other.pipe",
                    nested=".save",
                )
                self.assert_store_rejected(
                    "import requests\nfrom types import SimpleNamespace\n"
                    "holder = SimpleNamespace(branch=SimpleNamespace())\n"
                    f"other = holder\nother.branch.pipe = {value}\n"
                    "outbound = holder.branch.pipe\noutbound.get('url')",
                    "other.branch.pipe",
                )

    def test_assignment_shapes_and_constructor_values(self):
        fixtures = (
            ("other.pipe: object = self.session", "other.pipe"),
            ("other.pipe += self.session", "other.pipe"),
            ("outbound = other.pipe = self.session", "other.pipe"),
            ("other.pipe, unused = self.session, None", "other.pipe"),
            ("[unused, [other.pipe]] = [None, [self.session]]", "other.pipe"),
            ("with self.session as other.pipe:\n    pass", "other.pipe"),
            ("with self.session as (other.pipe, unused):\n    pass", "other.pipe"),
            ("import requests\nother.pipe = requests.Session()", "other.pipe"),
            (
                "from requests import Session as Factory\nother.pipe = Factory()",
                "other.pipe",
            ),
            ("from requests import get as emit\nother.pipe = emit", "other.pipe"),
            ("box = [None]\nbox[0] = self.session", "box[0]"),
            ("self.saved.pipe = self.session", "self.saved.pipe"),
            ("holder = self\nholder.pipe = self.session", "holder.pipe"),
            ("setattr(other, 'pipe', self.session)", "setattr"),
            ("name = 'pipe'\nsetattr(self, name, self.session)", "setattr"),
            ("name = 'pipe'\nsetattr(other, name, self.session)", "setattr"),
            ("name = 'get'\ngetattr(self.session, name)", "getattr"),
            ("import requests\nname = 'get'\ngetattr(requests, name)", "getattr"),
            ("name = 'flag'\nsetattr(self.session, name, None)", "setattr"),
        )
        for fixture, target in fixtures:
            with self.subTest(fixture=fixture):
                self.assert_store_rejected(fixture, target)
        self.assert_store_rejected(
            "async def save():\n    async with self.client as other.pipe:\n        pass",
            "other.pipe",
            nested=".save",
        )

    def test_local_may_values_and_leading_underscore_attributes(self):
        for seed in ("session", "client", "connection", "conn", "http", "transport"):
            for prefix in ("", "_", "__"):
                with self.subTest(seed=seed, prefix=prefix):
                    self.assert_store_rejected(
                        f"one = self.{prefix}{seed}\ntwo = one\nother.pipe = two",
                        "other.pipe",
                    )
        for fixture in (
            "one = self.session if enabled else None\ntwo = one\nother.pipe = two",
            "one = [self.session]\ntwo = one\nbox[0] = two",
            "one = None\none += self.session\nother.pipe = one",
            "one = self.session\none = None\ndef save():\n    other.pipe = one",
        ):
            with self.subTest(fixture=fixture):
                self.assert_store_rejected(
                    fixture,
                    "box[0]" if "box[0]" in fixture else "other.pipe",
                    nested=".save" if "def save" in fixture else "",
                )

    def test_direct_self_and_plain_local_stores_remain_allowed(self):
        source = (
            "def run(self):\n    import requests\n"
            "    local = requests\n    self.saved = local\n"
            "    self.saved: object = self.session\n"
            "    local, self.saved = self.session, requests\n"
            "    with self.session as self.saved:\n        pass\n"
            "    setattr(self, 'saved', self.session)\n"
            "    getattr(self.session, 'get')\n"
            "    other.value = 1\n    box[0] = 'plain data'\n"
        )
        self.assertEqual([s for s in c["census"](source) if s.get("structural")], [])

    def test_local_evidence_does_not_leak_between_sibling_scopes(self):
        source = (
            "def left(self):\n    local = self.session\n"
            "def right():\n    local = None\n    other.pipe = local\n"
        )
        self.assertEqual([s for s in c["census"](source) if s.get("structural")], [])

    def test_structural_flags_ignore_send_admission_but_allow_exact_exceptions(self):
        source = "def run(self):\n    other.pipe = self.session\n"
        sites, errors = c["audit"]({"fixture": source}, lambda *args: "admitted", {})
        self.assertEqual(len(sites), 1)
        self.assertEqual(
            len(errors), 1, "send admission must not waive a structural flag"
        )
        key = ("fixture", "run", sites[0][1]["call"])
        _, errors = c["audit"](
            {"fixture": source}, lambda *args: "admitted", {key: "test"}
        )
        self.assertEqual(errors, [])
        _, errors = c["audit"](
            {"fixture": source + "    other.pipe = self.session\n"},
            lambda *args: "admitted",
            {key: "test"},
        )
        self.assertTrue(errors, "an extra store must not inherit the exact exception")
        _, errors = c["audit"](
            {"fixture": source.replace("self.session", "self.connection")},
            lambda *args: "admitted",
            {key: "test"},
        )
        self.assertTrue(errors, "a changed RHS must not inherit the exact exception")

    def test_production_exceptions_match_only_existing_counted_sites(self):
        sites, errors = c["audit"](
            self.sources, a["admission"], a["EXCEPTIONS"], a["EXCEPTION_COUNTS"]
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(a["EXCEPTIONS"]), 17)
        actual = Counter(
            (m, s["owner"], s["call"]) for m, s in sites if s.get("structural")
        )
        self.assertEqual(
            actual, {k: v[0] for k, v in c["STRUCTURAL_EXCEPTIONS"].items()}
        )
        seen = set()
        for module, site in sites:
            key = (module, site["owner"], site["call"])
            if not site.get("structural") or key in seen:
                continue
            seen.add(key)
            with self.subTest(key=key):
                # Never mutate the cached census AST: insert a copied occurrence
                # into a fresh tree, in exactly the same function-level owner.
                owner = next(
                    (
                        p
                        for _, p in c["ancestors"](site)
                        if isinstance(
                            p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                        )
                    ),
                    site["tree"],
                )
                tree = ast.parse(self.sources[module])
                target = (
                    tree
                    if isinstance(owner, ast.Module)
                    else next(
                        n
                        for n in ast.walk(tree)
                        if type(n) is type(owner) and n.lineno == owner.lineno
                    )
                )
                duplicate = copy.deepcopy(site["node"])
                target.body.append(
                    ast.Expr(value=duplicate)
                    if isinstance(duplicate, ast.Call)
                    else duplicate
                )
                _, errors = c["audit"](
                    {
                        **self.sources,
                        module: ast.unparse(ast.fix_missing_locations(tree)),
                    },
                    a["admission"],
                    a["EXCEPTIONS"],
                    a["EXCEPTION_COUNTS"],
                )
                self.assertTrue(
                    any(site["call"] in e and "unadmitted" in e for e in errors), errors
                )


if __name__ == "__main__":
    unittest.main()
