"""Complete-audit regressions and a historical superset oracle.

Historical checkers are byte-exact test fixtures, usable without git history.
Each prior fixture is replayed, not approximated by a hand-maintained sample.
"""

import ast
import hashlib
import runpy
import unittest
from collections import Counter
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
ROUNDS = (
    "a003ce333bbe5a4e829e3f196e9f750c7d56574e",
    "58804abb1fdca11858673e83ab1a1a0e4e7b5574",
)
s = runpy.run_path(str(HERE / "test_falcon_transport_stores_offline.py"))
c = s["c"]


def committed(revision, name):
    # Exact originals, not rewritten implementations. The r3a7 pair is used
    # only as an independent test oracle; production census imports r3a6 only.
    if name == "falcon_send_bindings.py":
        filename = "falcon_send_frozen_bindings_r3a7.py"
    else:
        filename = (
            "falcon_send_frozen_r3a6.py"
            if revision == ROUNDS[0]
            else "falcon_send_frozen_r3a7.py"
        )
    return (HERE / filename).read_text()


@lru_cache(maxsize=2)
def historical(revision):
    namespace = {"__file__": str(HERE / "falcon_send_aliases.py")}
    source = committed(revision, "falcon_send_aliases.py")
    if revision == ROUNDS[1]:
        bindings = {}
        exec(
            compile(
                committed(revision, "falcon_send_bindings.py"),
                "<r3a7-bindings>",
                "exec",
            ),
            bindings,
        )
        with patch.object(runpy, "run_path", return_value=bindings):
            exec(compile(source, "<r3a7-aliases>", "exec"), namespace)
    else:
        exec(compile(source, "<r3a6-aliases>", "exec"), namespace)
    return namespace


def historical_flags(source, revision):
    module = historical(revision)
    tree = ast.parse(source)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    aliases = module["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
    return Counter(
        (node.lineno, node.col_offset, text)
        for node, text in module["TransportStores"](tree, aliases).violations()
    )


def structural_flags(sites):
    return Counter(
        (site["line"], site["node"].col_offset, site["call"])
        for site in sites
        if site.get("structural")
    )


class CompositionTests(unittest.TestCase):
    setUpClass = classmethod(s["StructuralStoreTests"].setUpClass.__func__)
    assert_store_rejected = s["StructuralStoreTests"].assert_store_rejected

    def test_all_seven_self_alias_regressions_in_complete_audit(self):
        fixtures = (
            "self.saved = self.session\nlocal = self\nother.pipe = local.saved",
            "self.saved: object = self.session\nlocal = self\nother.pipe = local.saved",
            "self.saved = self.session\none = self\nlocal = one\nother.pipe = local.saved",
            "local = self\nself.saved = self.session\nother.pipe = local.saved",
            "self.saved = self.session\nlocal, unused = self, None\nother.pipe = local.saved",
            "self.saved = self.session\nwith self as local:\n    other.pipe = local.saved",
            "self.saved = self.session\nlocal = self\nname = 'get'\ngetattr(local.saved, name)",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assert_store_rejected(
                    "from types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\n"
                    + fixture,
                    "getattr" if "getattr" in fixture else "other.pipe",
                )

    def test_class_body_read_fallback_in_complete_audit(self):
        for binding in ("HTTPAdapter = None", "HTTPAdapter: object", "del HTTPAdapter"):
            with self.subTest(binding=binding):
                self.assert_store_rejected(
                    "class Store:\n    other.pipe = HTTPAdapter\n    " + binding,
                    "other.pipe <- HTTPAdapter",
                    ".Store",
                )
        fixture = "class Store:\n    saved = HTTPAdapter\n    HTTPAdapter = None\nother.pipe = Store.saved"
        self.assert_store_rejected(fixture, "saved <- HTTPAdapter", ".Store")
        self.assert_store_rejected(fixture, "other.pipe <- Store.saved")

    def test_class_local_and_enclosing_evidence_are_both_retained(self):
        for source in (
            "from requests import Session as Factory\nclass Store:\n    other.pipe = Factory\n    Factory = None",
            "def run(self):\n    seed = self.session\n    class Store:\n        other.pipe = seed\n        seed = None",
            "class Store:\n    from requests import Session as Factory\n    other.pipe = Factory",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    any(
                        "other.pipe" in site["call"]
                        for site in c["census"](source)
                        if site.get("structural")
                    )
                )

    def test_superset_on_full_audit_and_every_prior_fixture(self):
        # Replay all r3a4-r3a7 fixture-generating test methods with each census
        # entry point instrumented. This covers future additions automatically.
        prior = runpy.run_path(
            str(HERE / "test_falcon_transport_generative_offline.py")
        )
        classes = [
            prior[name]
            for name in ("GenerativeTests", "StructuralStoreTests", "BindingStoreTests")
        ]
        dictionaries = {}
        for cls in classes:
            for name in dir(cls):
                function = getattr(cls, name)
                namespace = getattr(function, "__globals__", {})
                for scope in (namespace, namespace.get("s", {})):
                    census = scope.get("c")
                    if isinstance(census, dict) and "census" in census:
                        dictionaries[id(census)] = census
                        dictionaries[id(census["audit"].__globals__)] = census[
                            "audit"
                        ].__globals__
        seen = set()

        def instrument(original):
            def checked(source):
                sites = original(source)
                if source not in seen:
                    seen.add(source)
                    actual = structural_flags(sites)
                    for revision in ROUNDS:
                        expected = historical_flags(source, revision)
                        self.assertFalse(
                            expected - actual,
                            f"lost {revision} flags: {expected - actual}",
                        )
                return sites

            return checked

        with ExitStack() as stack:
            for namespace in dictionaries.values():
                stack.enter_context(
                    patch.dict(namespace, {"census": instrument(namespace["census"])})
                )
            # Preserve the original historical-graph assertion, then replay
            # the full integrated superset through the same frozen checkers.
            integrated_sources = self.sources
            historical_names = runpy.run_path(
                str(HERE / "falcon_send_integration_fixture.py")
            )["HISTORICAL_MODULES"]
            self.sources = {name: integrated_sources[name] for name in historical_names}
            self.assertEqual(len(self.sources), 38)
            self.sources = integrated_sources
            for source in self.sources.values():
                instrument(c["census"])(source)
            result = unittest.TestResult()
            suite = unittest.TestSuite(
                unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes
            )
            suite.run(result)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.failures, [])
        self.assertGreater(len(seen), len(self.sources))
        print(
            f"SUPERSET: {len(seen)} sources, {result.testsRun} prior tests, both historical checkers"
        )

    def test_class_lookup_cannot_replace_an_existing_reflection_key(self):
        fixture = "class Store:\n    name = 'x'\n    setattr(self.session, name, HTTPAdapter)\n    HTTPAdapter = None"
        self.assert_store_rejected(fixture, "dynamic-attribute setattr", ".Store")
        self.assert_store_rejected(fixture, "transport-store setattr", ".Store")
        source = "from requests.adapters import HTTPAdapter\n" + fixture
        actual = structural_flags(c["census"](source))
        for revision in ROUNDS:
            self.assertFalse(historical_flags(source, revision) - actual)

    def test_self_prefix_growth_cannot_replace_existing_reflection_key(self):
        fixture = (
            "setattr(self, 'saved', self.session)\nlocal = self\n"
            "name = 'pipe'\nsetattr(self.session, name, local.saved)"
        )
        self.assert_store_rejected(fixture, "dynamic-attribute setattr")
        self.assert_store_rejected(fixture, "bare-self-store local <- self")

    def test_self_store_closure_is_finite_and_rejects_alias_creation(self):
        source = (
            "def run(self):\n    setattr(self, 'saved', self.session)\n"
            "    hop0 = self\n"
            + "".join(f"    hop{i} = hop{i - 1}\n" for i in range(1, 65))
            + "    hop0 = hop0.loop\n    other.pipe = hop64.saved\n"
        )
        tree = ast.parse(source)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        module = c["_aliases"]
        forms = module["BindingForms"](tree, parents)
        aliases = module["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
        checker = module["BareSelfStores"](tree, aliases, forms)
        before = dict(checker.evidence)
        flags = list(checker.violations())
        self.assertEqual(flags, list(checker.violations()))
        self.assertEqual(dict(checker.evidence), before)
        self.assertTrue(any("hop64 <- hop63" in text for _, text in flags))
        self.assertLessEqual(len(checker.evidence), len(forms.events))
        self.assertTrue(
            any(
                site["call"] == "bare-self-store other.pipe <- local"
                for site in c["census"](
                    "def run(self):\n    setattr(self, 'saved', self.session)\n"
                    "    local = self\n    other.pipe = local"
                )
            )
        )

    def test_union_is_by_construction_and_preserves_occurrence_counts(self):
        stores = c["TransportStores"]
        first, second = ast.parse("pass\npass").body
        a, b, shared = (first, "first"), (second, "second"), (first, "shared")
        for left, right in (
            ([a], []),
            ([], [b]),
            ([a, shared, shared], [b, shared]),
            ([a], [a, a]),
        ):
            with self.subTest(left=left, right=right):

                class Checker:
                    def __init__(self, rows):
                        self.rows = rows

                    def violations(self):
                        return iter(self.rows)

                tree = ast.parse("")
                aliases = c["LocalAliases"](tree, {}, c["TRANSPORT_MODULES"])
                with patch.dict(
                    stores.__init__.__globals__,
                    {
                        "FrozenTransportStores": lambda *args, rows=left: Checker(rows),
                        "BindingTransportStores": lambda *args, rows=right, **kwargs: (
                            Checker(rows)
                        ),
                    },
                ):
                    actual = Counter(stores(tree, aliases).violations())
                self.assertEqual(actual, Counter(left) | Counter(right))
        source = (
            "def run(self):\n    other.pipe, other.pipe = self.session, self.session"
        )
        sites, errors = c["audit"](
            {"fixture": source},
            lambda *args: "admitted",
            {
                (
                    "fixture",
                    "run",
                    "transport-store other.pipe <- self.session",
                ): "one occurrence only"
            },
        )
        self.assertEqual(sum(structural_flags([site for _, site in sites]).values()), 2)
        self.assertTrue(any("unadmitted" in error for error in errors))

    def test_frozen_r3a6_checker_is_preserved_unchanged(self):
        frozen = HERE / "falcon_send_frozen_r3a6.py"
        self.assertTrue(
            frozen.is_file(), "ruling 389 requires the unchanged frozen checker"
        )
        self.assertEqual(
            hashlib.sha256(frozen.read_bytes()).hexdigest(),
            "d69c73d9eaf2aea4a1068e87b403d0736a387f6e51312ac74431a825e5a3a45e",
        )
        self.assertEqual(
            hashlib.sha256(
                (HERE / "falcon_send_frozen_r3a7.py").read_bytes()
            ).hexdigest(),
            "fdd781a0849fd8955742685af27fe1c8f19ef7498ced5b8e7680bd20c600b181",
        )
        self.assertEqual(
            hashlib.sha256(
                (HERE / "falcon_send_frozen_bindings_r3a7.py").read_bytes()
            ).hexdigest(),
            "09c963b74c29638036f71a2001007b715e5a7c34657e9010e15cd566f6e6c82d",
        )


if __name__ == "__main__":
    unittest.main()
