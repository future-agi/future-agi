"""Ruling 472: non-call builtin setter loads reject the complete audit."""

import ast
import runpy
import unittest
from pathlib import Path

HERE = Path(__file__).parent
s = runpy.run_path(str(HERE / "test_falcon_transport_stores_offline.py"))
c = s["c"]
w = runpy.run_path(str(HERE / "test_falcon_transport_walrus_offline.py"))


class SetterLoadTests(w["WalrusNormalizationTests"]):
    setUpClass = classmethod(s["StructuralStoreTests"].setUpClass.__func__)
    assert_store_rejected = s["StructuralStoreTests"].assert_store_rejected

    def rejected(self, fixture, nested=""):
        self.assert_store_rejected(fixture, "builtin-setter-load", nested)

    def test_both_reviewer_walrus_setters_reject_complete_audit(self):
        for invoke in (
            "(setter := setattr)\nsetter(box, 'ref', self)",
            "(setter := setattr)(box, 'ref', self)",
        ):
            with self.subTest(invoke=invoke):
                self.rejected(
                    "from types import SimpleNamespace\nholder = SimpleNamespace()\n"
                    "other = holder\nbox = SimpleNamespace()\n"
                    "setattr(self, 'saved', self.session)\n"
                    + invoke
                    + "\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')"
                )

    def test_every_non_call_position_rejects_complete_audit(self):
        for setter in ("setattr", "delattr", "object.__setattr__", "type.__setattr__"):
            for template, nested in (
                ("setter = VALUE", ""),
                ("setter: object = VALUE", ""),
                ("(setter := VALUE)", ""),
                ("consume(VALUE)", ""),
                ("consume(callback=VALUE)", ""),
                ("consume([VALUE])", ""),
                ("consume((VALUE,))", ""),
                ("consume({VALUE})", ""),
                ("consume({'setter': VALUE})", ""),
                ("consume({VALUE: 'setter'})", ""),
                ("return VALUE", ""),
                ("yield VALUE", ""),
                ("def save(setter=VALUE):\n    pass", ".save"),
                ("def save(*, setter=VALUE):\n    pass", ".save"),
                ("save = lambda setter=VALUE: None", ""),
                ("save = lambda: VALUE", ""),
                ("class Box:\n    setter = VALUE", ".Box"),
                ("global setter\nsetter = VALUE", ""),
                (
                    "setter = None\ndef save():\n    nonlocal setter\n    setter = VALUE",
                    ".save",
                ),
                ("box.setter = VALUE", ""),
                ("box[0] = VALUE", ""),
                ("VALUE", ""),
            ):
                with self.subTest(setter=setter, template=template):
                    self.rejected(template.replace("VALUE", setter), nested)

    def test_qualified_and_imported_builtin_loads_reject_complete_audit(self):
        for fixture in (
            "import builtins\nsetter = builtins.setattr",
            "import builtins as b\nsetter = b.delattr",
            "import builtins as b\nsetter = b.object.__setattr__",
            "import builtins as b\nsetter = b.type.__setattr__",
            "from builtins import setattr as put\nconsume(put)",
            "from builtins import delattr as remove\nconsume(remove)",
            "from builtins import object as base\nconsume(base.__setattr__)",
            "from builtins import type as meta\nconsume(meta.__setattr__)",
        ):
            with self.subTest(fixture=fixture):
                self.rejected(fixture)

    def test_literal_getattr_setter_acquisition_rejects_complete_audit(self):
        # A reflective acquisition is a setter load even if its result is called
        # immediately: it is not a direct builtin setter call in the source.
        for receiver in ("builtins", "obj", "object", "type"):
            for name in ("setattr", "delattr", "__setattr__"):
                value = f"getattr({receiver}, {name!r})"
                for template in (
                    "setter = VALUE",
                    "(setter := VALUE)",
                    "consume(VALUE)",
                    "consume([VALUE])",
                    "return VALUE",
                    "VALUE(box, 'ref', None)",
                ):
                    with self.subTest(receiver=receiver, name=name, template=template):
                        self.rejected(
                            "import builtins\n" + template.replace("VALUE", value)
                        )
        self.rejected("import builtins as b\nsetter = b.getattr(b, 'setattr')")
        self.rejected(
            "from builtins import getattr as lookup\nsetter = lookup(obj, '__setattr__')"
        )
        self.rejected("getter = getattr\nsetter = getter(obj, 'delattr')")

    def test_walrus_getter_acquisitions_reject_complete_audit(self):
        for setup, getter in (
            ("", "(lookup := getattr)"),
            ("", "(outer := (lookup := getattr))"),
            ("import builtins", "(lookup := builtins.getattr)"),
            ("import builtins as b", "(outer := (lookup := b.getattr))"),
            ("from builtins import getattr as read_attr", "(lookup := read_attr)"),
            ("getter = getattr", "(outer := (lookup := getter))"),
        ):
            for receiver, name in (
                ("obj", "setattr"),
                ("builtins", "delattr"),
                ("object", "__setattr__"),
                ("type", "__setattr__"),
            ):
                for target in (receiver, f"(target := (inner := {receiver}))"):
                    acquisition = f"{getter}({target}, {name!r})"
                    with self.subTest(setup=setup, acquisition=acquisition):
                        self.assert_store_rejected(
                            "import builtins\n" + setup + "\nreturn " + acquisition,
                            "builtin-setter-load "
                            + ast.unparse(ast.parse(acquisition, mode="eval").body),
                        )

    def test_reviewer_walrus_getter_sends_reject_complete_audit(self):
        for acquisition in (
            "(lookup := getattr)(builtins, 'setattr')",
            "(outer := (lookup := getattr))(builtins, 'setattr')",
            "(lookup := builtins.getattr)(builtins, 'setattr')",
            "(lookup := getattr)(object, '__setattr__')",
        ):
            with self.subTest(acquisition=acquisition):
                self.assert_store_rejected(
                    "import builtins\nfrom types import SimpleNamespace\n"
                    "holder = SimpleNamespace()\nother = holder\n"
                    "class Box:\n    pass\nbox = Box()\n"
                    "setattr(self, 'saved', self.session)\n"
                    + acquisition
                    + "(box, 'ref', self)\nother.pipe = box.ref.saved\n"
                    "emit = holder.pipe.get\nemit('offline')",
                    "builtin-setter-load "
                    + ast.unparse(ast.parse(acquisition, mode="eval").body),
                )

    def test_walrus_identity_is_its_value_in_all_expression_positions(self):
        source = (
            "lookup = setattr\n"
            "(lookup := (inner := getattr))((target := object), '__setattr__')\n"
            "consume((argument := (nested := type.__setattr__)))\n"
            "consume((lookup := unknown))\n"
        )
        tree = ast.parse(source)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
        scanner = c["_aliases"]["BuiltinSetterLoads"](tree, aliases)
        expected = {2: {"getattr"}, 3: {"type.__setattr__"}, 4: set()}
        for node in ast.walk(tree):
            if isinstance(node, ast.NamedExpr):
                with self.subTest(expression=ast.unparse(node)):
                    identity = (
                        {"object"}
                        if node.target.id == "target"
                        else expected[node.lineno]
                    )
                    self.assertEqual(scanner.names(node), identity)

    def test_getattr_default_setter_load_is_not_mistaken_for_direct_call(self):
        self.rejected("setter = getattr(obj, 'fallback', setattr)")
        self.rejected("consume(getattr(obj, 'fallback', type.__setattr__))")

    def test_conditional_and_indexed_call_targets_still_load_the_setter(self):
        for expression in (
            "(setattr if enabled else callback)",
            "[setattr][0]",
            "(setter := setattr)",
        ):
            with self.subTest(expression=expression):
                self.rejected(expression + "(box, 'ref', None)")

    def test_identity_propagation_keeps_alias_loads_and_getter_acquisition(self):
        for fixture, expected in (
            ("first = setattr\nsecond = first\nconsume(second)", "second"),
            ("(first := delattr)\nsecond = first\nconsume(second)", "second"),
            ("second = first\nfirst = setattr\nconsume(second)", "second"),
            ("first = second\nsecond = setattr\nconsume(first)", "first"),
            ("first = setattr\nfirst = None\nconsume(first)", "first"),
            ("pair = (None, setattr)\nfirst, second = pair\nconsume(second)", "second"),
            ("box.put = setattr\ncopy = box\nconsume(copy.put)", "copy.put"),
            (
                "import builtins as b\nbase = b.object\nconsume(base.__setattr__)",
                "base.__setattr__",
            ),
            ("(lookup := getattr)\nlookup(obj, 'setattr')", "lookup(obj, 'setattr')"),
        ):
            with self.subTest(fixture=fixture):
                self.assert_store_rejected(fixture, "builtin-setter-load " + expected)

    def test_identity_scanner_never_expands_unrelated_aliases(self):
        source = (
            "def run(client):\n    a0 = client\n"
            + "".join(
                f"    a{i} = (client, a{i - 1}, a{i - 1})\n" for i in range(1, 65)
            )
            + "    (put := setattr)\n    copy = put\n    consume(copy)\n    return a64\n"
            + "def sibling():\n    consume(copy)\n"
        )
        tree = ast.parse(source)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])

        def forbidden(*args):
            self.fail("setter identity scanning must never expand arbitrary aliases")

        aliases.names = aliases.expand = forbidden
        scanner = c["_aliases"]["BuiltinSetterLoads"](tree, aliases)
        flags = [(node.lineno, text) for node, text in scanner.violations()]
        self.assertTrue(any(text == "builtin-setter-load copy" for _, text in flags))
        self.assertFalse(any(line >= tree.body[-1].lineno for line, _ in flags), flags)
        self.assertFalse(
            any(text.startswith("builtin-setter-load a") for _, text in flags)
        )

    def test_direct_builtin_calls_keep_existing_analysis(self):
        for call in (
            "setattr(box, 'ref', None)",
            "delattr(box, 'ref')",
            "object.__setattr__(box, 'ref', None)",
            "type.__setattr__(Box, 'ref', None)",
            "builtins.setattr(box, 'ref', None)",
            "builtins.delattr(box, 'ref')",
            "builtins.object.__setattr__(box, 'ref', None)",
            "builtins.type.__setattr__(Box, 'ref', None)",
            "getattr(obj, 'unrelated')",
        ):
            with self.subTest(call=call):
                sites = c["census"]("import builtins\ndef run():\n    " + call)
                self.assertFalse(
                    any(
                        site["call"].startswith("builtin-setter-load") for site in sites
                    )
                )
        self.assert_store_rejected("setattr(box, 'ref', self)", "bare-self-store")
        self.assert_store_rejected(
            "setattr(box, 'ref', self.session)", "transport-store"
        )


if __name__ == "__main__":
    unittest.main()
