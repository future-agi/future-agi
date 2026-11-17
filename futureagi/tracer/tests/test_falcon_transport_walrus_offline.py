"""Normalize the entire walrus syntax class before identities."""

import ast
import runpy
import signal
import sys
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).parent
s = runpy.run_path(str(HERE / "test_falcon_transport_stores_offline.py"))
c = s["c"]
f = runpy.run_path(str(HERE / "falcon_send_walrus_fixtures.py"))
POSITIONS = (
    "consume([(base := object).__setattr__ for item in items])",
    "consume({(base := object).__setattr__ for item in items})",
    "consume({item: (base := object).__setattr__ for item in items})",
    "consume(((base := object).__setattr__ for item in items))",
    "save = lambda arg=(base := object).__setattr__: None",
    "save = lambda: (base := object).__setattr__",
    "@decorate((base := object).__setattr__)\ndef save():\n    pass",
    "consume(box[(base := object).__setattr__])",
    'consume(f"{(base := object).__setattr__}")',
    'consume(f"{value:{(base := object).__setattr__}}")',
    # The acquisition is AFTER the walrus; the emitted binding must survive.
    "consume([(lookup := getattr) for item in items])\nlookup(obj, 'setattr')",
    "consume([[(lookup := getattr) for j in items] for i in items])\nlookup(obj, 'setattr')",
    "save = lambda arg=(lookup := getattr): None\nlookup(obj, 'setattr')",
    "@decorate((lookup := getattr))\ndef save():\n    pass\nlookup(obj, 'setattr')",
    "consume(box[(lookup := getattr)])\nlookup(obj, 'setattr')",
    "consume(f\"{(lookup := getattr)}\")\nlookup(obj, 'setattr')",
    "consume([(getter := (lookup := getattr))(obj, 'setattr') for item in items])",
    "consume([(lookup := base.getattr) for base in [builtins]])\nlookup(obj, 'setattr')",
    "class Box:\n    lookup = (getter := getattr)\n    lookup(obj, 'setattr')",
    "global lookup\n(lookup := getattr)\nlookup(obj, 'setattr')",
    "lookup = None\ndef save():\n    nonlocal lookup\n    (lookup := getattr)\n    lookup(obj, 'setattr')",
)


e = runpy.run_path(str(HERE / "falcon_send_walrus_equivalence.py"))


@contextmanager
def bounded_scan(seconds=3):
    def expired(*args):
        raise AssertionError(f"setter scan exceeded its hard {seconds}-second bound")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def scan(source):
    tree = ast.parse(source)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
    scanner = c["_aliases"]["BuiltinSetterLoads"](tree, aliases)
    assert not any(isinstance(n, ast.NamedExpr) for n in ast.walk(scanner.tree))
    for _, binding in scanner.walrus_bindings:
        assert not any(isinstance(n, ast.NamedExpr) for n in ast.walk(binding))
    return list(scanner.violations())


class WalrusNormalizationTests(unittest.TestCase):
    setUpClass = classmethod(s["StructuralStoreTests"].setUpClass.__func__)
    assert_store_rejected = s["StructuralStoreTests"].assert_store_rejected

    def test_r14_seven_dotted_prefix_variants_complete_audit(self):
        for label, source, ordinary in e["PREFIX_PAIRS"]:
            with self.subTest(label=label):
                self.assert_store_rejected(
                    e["send_fixture"](ordinary), "builtin-setter-load"
                )
                self.assert_store_rejected(
                    e["send_fixture"](source), "builtin-setter-load"
                )

    def test_walrus_ordinary_spelling_equivalence(self):
        pairs = [
            (group + "/" + label, source, e["ordinary_reviewer_fixture"](source))
            for group in ("A_SENDS", "A_MINIMAL", "A_POSITIONS", "B_SENDS")
            for label, source in f[group]
        ]
        pairs.extend(
            (
                f"position/{i}",
                "import builtins\n" + source,
                "import builtins\n" + ordinary,
            )
            for i, (source, ordinary) in enumerate(
                zip(POSITIONS, e["ORDINARY_POSITIONS"], strict=True)
            )
        )
        self.assertEqual(len(pairs), 165)
        pairs.extend(e["PREFIX_PAIRS"])
        for depth in (400, 2000):
            pairs.append((f"deep/{depth}", *e["deep_pair"](depth)))
        self.assertEqual(len(pairs), 174)
        for label, source, ordinary in pairs:
            with self.subTest(label=label), bounded_scan():
                self.assertNotIn(":=", ordinary)
                # Source-level rejection is the invariant, not
                # relocated/duplicated diagnostics introduced by ordinary stores.
                expected = scan(ordinary)
                self.assertTrue(expected, "ordinary control must actually reject")
                actual = scan(source)
                self.assertTrue(
                    actual, "walrus source admitted its rejected ordinary spelling"
                )
                self.assertGreaterEqual(len(actual), len(expected))

    def test_normalized_prefix_fanout_is_bounded(self):
        # Rebuilding the legacy expander after desugaring would newly traverse
        # exponentially many duplicate holder aliases. Prefix evidence must be
        # finite too, not just the later builtin-identity propagation.
        source = (
            "def run(client):\n    a0 = client\n"
            + "".join(
                f"    (a{i} := (client, a{i - 1}, a{i - 1}))\n" for i in range(1, 65)
            )
            + "    consume(a64.field)\n    consume(setattr)\n"
        )
        with bounded_scan(1):
            self.assertTrue(scan(source))

    def test_normalized_prefixes_retain_known_constructor_evidence(self):
        for constructor in ("requests.Session", "httpx.Client", "boto3.client"):
            module = constructor.split(".")[0]
            source = (
                f"def run(obj):\n    import {module}\n"
                f"    {constructor}().lookup = getattr\n"
                f"    holder = {constructor}()\n"
                "    holder.lookup(obj, 'setattr')\n"
            )
            with self.subTest(constructor=constructor), bounded_scan(1):
                self.assertTrue(scan(source))
        # An arbitrary factory return must not acquire builtin identity.
        self.assertFalse(scan("getter = factory()\ngetter(obj, 'setattr')"))

    def test_deep_unary_getter_stack_safe(self):
        limit = sys.getrecursionlimit()
        for depth in (400, 2000):
            with self.subTest(depth=depth), bounded_scan():
                source, _ = e["deep_pair"](depth)
                # 2000 deliberately tests this scanner in isolation: the frozen
                # OR components retain their inherited recursive scope walkers.
                flags = scan(source)
                self.assertEqual(
                    [(node.lineno, text) for node, text in flags],
                    [(3, "builtin-setter-load lookup(obj, 'setattr')")],
                )
                self.assertEqual(sys.getrecursionlimit(), limit)

    def test_deep_unary_400_complete_audit(self):
        module = "agentic_eval.core.embeddings.serving_client"
        source = self.sources[module]
        tree = ast.parse(source)
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
        fixture = "not " * 400 + "(lookup := getattr)\nlookup(obj, 'setattr')\n"
        lines = source.splitlines(keepends=True)
        index = method.body[0].lineno - 1
        # No recursive ast.unparse of the deliberately deep source.
        lines[index:index] = textwrap.indent(fixture, "        ").splitlines(
            keepends=True
        )
        sites, errors = c["audit"](
            {**self.sources, module: "".join(lines)},
            s["a"]["admission"],
            s["a"]["EXCEPTIONS"],
            s["a"]["EXCEPTION_COUNTS"],
        )
        matching = [
            site
            for name, site in sites
            if name == module
            and site["call"] == "builtin-setter-load lookup(obj, 'setattr')"
        ]
        self.assertTrue(matching)
        for site in matching:
            self.assertIn(
                f"{module}:{site['line']} {site['owner']} {site['call']}: unadmitted",
                errors,
            )

    def test_r13_lens_a_complete_send_fixtures(self):
        for label, fixture in f["A_SENDS"]:
            with self.subTest(label=label):
                self.assert_store_rejected(fixture, "builtin-setter-load")

    def test_r13_lens_a_minimal_acquisitions_complete_audit(self):
        for label, fixture in f["A_MINIMAL"]:
            with self.subTest(label=label):
                self.assert_store_rejected(fixture, "builtin-setter-load", ".acquire")

    def test_r13_lens_a_all_load_positions_complete_audit(self):
        for label, fixture in f["A_POSITIONS"]:
            nested = ".save" if label.endswith("/default") else ""
            if label.endswith("/class_body"):
                nested = ".Stored"
            with self.subTest(label=label):
                self.assert_store_rejected(fixture, "builtin-setter-load", nested)

    def test_r13_lens_b_all_binding_variants_complete_audit(self):
        for label, fixture in f["B_SENDS"]:
            with self.subTest(label=label):
                self.assert_store_rejected(fixture, "builtin-setter-load")

    def test_walrus_in_every_expression_position_complete_audit(self):
        for fixture in POSITIONS:
            nested = ".Box" if fixture.startswith("class Box:") else ""
            if fixture.startswith(("lookup = None\ndef save", "@decorate((base :=")):
                nested = ".save"
            with self.subTest(fixture=fixture):
                self.assert_store_rejected(
                    "import builtins\n" + fixture, "builtin-setter-load", nested
                )

    def test_normalized_modules_and_all_fixtures_have_no_namedexpr(self):
        fixtures = [
            source
            for group in ("A_SENDS", "A_MINIMAL", "A_POSITIONS", "B_SENDS")
            for _, source in f[group]
        ]
        fixtures.extend(POSITIONS)
        for source in [*self.sources.values(), *fixtures]:
            with self.subTest(source=source[:100]):
                tree = ast.parse(source)
                before = ast.dump(tree, include_attributes=True)
                parents = {
                    child: node
                    for node in ast.walk(tree)
                    for child in ast.iter_child_nodes(node)
                }
                aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
                scanner = c["_aliases"]["BuiltinSetterLoads"](tree, aliases)
                self.assertFalse(
                    any(isinstance(n, ast.NamedExpr) for n in ast.walk(scanner.tree))
                )
                self.assertEqual(ast.dump(tree, include_attributes=True), before)
                walruses = sum(isinstance(n, ast.NamedExpr) for n in ast.walk(tree))
                if walruses:
                    self.assertEqual(len(scanner.walrus_bindings), walruses)
                    for scope, binding in scanner.walrus_bindings:
                        self.assertIsInstance(binding, ast.Assign)
                        self.assertIsInstance(
                            scope,
                            (
                                ast.Module,
                                ast.FunctionDef,
                                ast.AsyncFunctionDef,
                                ast.ClassDef,
                                ast.Lambda,
                            ),
                        )
                        self.assertFalse(
                            any(isinstance(n, ast.NamedExpr) for n in ast.walk(binding))
                        )

    def test_walrus_binding_scope_and_original_diagnostics(self):
        source = (
            "def outer():\n"
            "    consume([(lookup := getattr) for item in items])\n"
            "    lookup(obj, 'setattr')\n"
            "    save = lambda arg=(getter := getattr): None\n"
            "    getter(obj, 'setattr')\n"
            "    fn = lambda: (inside := getattr)(obj, 'setattr')\n"
            "def sibling():\n"
            "    lookup(obj, 'setattr')\n"
        )
        tree = ast.parse(source)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
        scanner = c["_aliases"]["BuiltinSetterLoads"](tree, aliases)
        flags = list(scanner.violations())
        self.assertEqual({node.lineno for node, _ in flags}, {3, 5, 6})
        self.assertTrue(all(node in parents for node, _ in flags))
        destinations = {
            binding.targets[0].id: scope for scope, binding in scanner.walrus_bindings
        }
        self.assertIs(destinations["lookup"], tree.body[0])
        self.assertIs(destinations["getter"], tree.body[0])
        self.assertIsInstance(destinations["inside"], ast.Lambda)


if __name__ == "__main__":
    unittest.main()
