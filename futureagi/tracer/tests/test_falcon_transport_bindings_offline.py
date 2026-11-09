"""Ruling 365: complete-audit binding/namespace mutations; never execute sources."""

import ast
import runpy
import unittest
from pathlib import Path

HERE = Path(__file__).parent
s = runpy.run_path(str(HERE / "test_falcon_transport_stores_offline.py"))
c = s["c"]


class BindingStoreTests(unittest.TestCase):
    setUpClass = classmethod(s["StructuralStoreTests"].setUpClass.__func__)
    assert_store_rejected = s["StructuralStoreTests"].assert_store_rejected

    def assert_binding_clear(self, source):
        # Ruling 389 gives the OR precedence over r3a7-only negative verdicts.
        # Still prove binding-scope/deletion precision in that component, and
        # require the census to retain exactly the frozen component's flags.
        tree = ast.parse(source)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
        stores = c["_aliases"]
        self.assertEqual(
            list(stores["BindingTransportStores"](tree, aliases).violations()), []
        )
        expected = [
            (node.lineno, text)
            for node, text in stores["FrozenTransportStores"](
                tree, aliases
            ).violations()
        ]
        actual = [
            (site["line"], site["call"])
            for site in c["census"](source)
            if site.get("structural") and not site["call"].startswith("bare-self-store")
        ]
        self.assertCountEqual(actual, expected)

    def rejected(self, fixture, target="other.pipe", nested=""):
        self.assert_store_rejected(
            "from types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\n"
            + fixture
            + "\nemit = holder.pipe.get\nemit('https://offline.invalid')",
            target,
            nested,
        )

    def test_walrus_and_iteration_bindings(self):
        fixtures = (
            "if (outbound := self.session):\n    pass\nother.pipe = outbound",
            "if (pair := (self.session, None)):\n    pass\noutbound, unused = pair\nother.pipe = outbound",
            "[(outbound := self.session) for unused in [None]]\nother.pipe = outbound",
            "for outbound in [self.session]:\n    other.pipe = outbound",
            "for (outbound, unused) in [(self.session, None)]:\n    other.pipe = outbound",
            "[setattr(other, 'pipe', outbound) for outbound in [self.session]]",
            "{setattr(other, 'pipe', outbound) for outbound in [self.session]}",
            "{0: setattr(other, 'pipe', outbound) for outbound in [self.session]}",
            "tuple(setattr(other, 'pipe', outbound) for outbound in [self.session])",
            "[setattr(other, 'pipe', second) for outbound in [self.session] for second in [outbound]]",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.rejected(
                    fixture, "setattr" if "setattr" in fixture else "other.pipe"
                )
        self.rejected(
            "async def save():\n    async for outbound in self.session:\n        other.pipe = outbound",
            nested=".save",
        )
        self.rejected(
            "async def save():\n    return [setattr(other, 'pipe', outbound) async for outbound in self.session]",
            "setattr",
            ".save",
        )
        self.rejected(
            "if (outbound := self.session):\n    pass\nname = 'get'\ngetattr(outbound, name)",
            "getattr",
        )

    def test_defaults_use_defining_scope_and_bind_body_parameters(self):
        for params in (
            "outbound=self.session",
            "outbound=self.session, /",
            "*, outbound=self.session",
            "unused=None, *, outbound=self.session",
            "outbound=seed",
            "seed=seed",
        ):
            name = "seed" if params == "seed=seed" else "outbound"
            with self.subTest(params=params):
                self.rejected(
                    f"seed = self.session\ndef save({params}):\n    other.pipe = {name}\nsave()",
                    nested=".save",
                )
        for params in (
            "outbound=self.session",
            "*, outbound=self.session",
            "seed=seed",
        ):
            name = "seed" if params == "seed=seed" else "outbound"
            with self.subTest(lambda_params=params):
                self.rejected(
                    f"seed = self.session\nsave = lambda {params}: setattr(other, 'pipe', {name})\nsave()",
                    "setattr",
                )
        self.rejected(
            "async def save(outbound=self.session):\n    other.pipe = outbound",
            nested=".save",
        )

    def test_with_except_and_match_bindings(self):
        for fixture in (
            "with self.session as outbound:\n    other.pipe = outbound",
            "with self.session as (outbound, unused):\n    other.pipe = outbound",
            "try:\n    pass\nexcept self.session as outbound:\n    other.pipe = outbound",
            "try:\n    raise self.session\nexcept Exception as outbound:\n    other.pipe = outbound",
            "match self.session:\n    case outbound:\n        other.pipe = outbound",
            "match [self.session]:\n    case [*outbound]:\n        other.pipe = outbound",
            "match self.session:\n    case object() as outbound:\n        other.pipe = outbound",
            "match {'value': self.session}:\n    case {'value': outbound}:\n        other.pipe = outbound",
            "match {'value': self.session}:\n    case {**outbound}:\n        other.pipe = outbound",
            "match self.session:\n    case Point(outbound):\n        other.pipe = outbound",
        ):
            with self.subTest(fixture=fixture):
                self.rejected(fixture)
        self.rejected(
            "async def save():\n    async with self.session as outbound:\n        other.pipe = outbound",
            nested=".save",
        )

    def test_self_candidate_paths_survive_allowed_stores(self):
        for store in (
            "setattr(self, 'saved', self.session)",
            "self.saved = self.session",
            "self.saved: object = self.session",
            "self.saved = self.session if True else None",
            "self.saved = self.session or None",
            "self.saved = []\nself.saved += [self.session]",
            "self.saved = next(iter([self.session]))",
            "self.saved, unused = self.session, None",
        ):
            with self.subTest(store=store):
                self.rejected(store + "\nother.pipe = self.saved")
        self.rejected(
            "self.saved = []\nself.saved += [self.session]\nother.pipe = self.saved[0]"
        )
        self.rejected(
            "setattr(self, 'saved', self.session)\nname = 'get'\ngetattr(self.saved, name)",
            "getattr",
        )
        self.rejected(
            "setattr(self, 'saved', self.session)\ndef save():\n    other.pipe = self.saved",
            nested=".save",
        )

    def test_reviewer_self_alias_variants_reject_complete_audit(self):
        # Both historical checkers missed these: a superset assertion is not
        # sufficient. Each fixture must independently reject the complete audit.
        fixtures = (
            "setattr(self, 'saved', self.session)\nlocal = self",
            "setattr(self, 'saved', self.session)\none = self\nlocal = one",
            "setattr(self, 'saved', self.session)\nlocal: object = self",
            "local = self\nsetattr(self, 'saved', self.session)",
            "self.saved = (outbound := self.session)\nlocal = self",
            "self.saved = self.session if True else None\nlocal = self",
            "self.saved = self.session or None\nlocal = self",
            "setattr(self, 'saved', self.session)\n(local := self)",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                # Ruling 441 rejects the alias-creation store itself, rather
                # than inferring an arbitrary later self-prefixed read.
                self.rejected(fixture + "\nother.pipe = local.saved", "bare-self-store")

    def test_self_alias_binding_forms_and_dotted_prefixes_reject_complete_audit(self):
        for alias in (
            "local = unused = self",
            "local, unused = self, None",
            "[unused, [local]] = [None, [self]]",
            "pair = (self, None)\nlocal, unused = pair",
            "with self as local:\n    other.pipe = local.saved",
            "with self as (local, unused):\n    other.pipe = local.saved",
            "with self as one, one as local:\n    other.pipe = local.saved",
            "box.link = self\nlocal = box\nother.pipe = local.link.saved",
            "local = self\nlocal = None",
            "one = self\nlocal = one\none = local",
            "local = self\nlocal = local.loop",
        ):
            fixture = "setattr(self, 'saved', self.session)\n" + alias
            if "other.pipe" not in alias:
                fixture += "\nother.pipe = local.saved"
            with self.subTest(alias=alias):
                self.rejected(fixture, "bare-self-store")
        self.rejected(
            "setattr(self, 'saved', self.session)\nasync def save():\n"
            "    async with self as local:\n        other.pipe = local.saved",
            "bare-self-store",
            nested=".save",
        )

    def test_expression_self_stores_survive_aliases_in_complete_audit(self):
        for store in (
            "self.saved: object = self.session if True else None",
            "self.saved = []\nself.saved += [self.session]",
            "self.saved = next(iter([self.session]))",
            "self.saved, unused = self.session if True else None, None",
            "setattr(self, 'saved', self.session or None)",
        ):
            with self.subTest(store=store):
                self.rejected(
                    store + "\nlocal = self\nother.pipe = local.saved",
                    "bare-self-store",
                )
        self.rejected(
            "setattr(self, 'saved', self.session)\nlocal = self\n"
            "def save():\n    other.pipe = local.saved",
            "bare-self-store",
        )
        self.rejected(
            "setattr(self, 'saved', self.session)\none = self\nlocal = one\n"
            "del one\nother.pipe = local.saved",
            "bare-self-store",
        )

    def test_self_aliases_preserve_lexical_scope_and_delete_kills(self):
        for body in (
            "setattr(self, 'saved', self.session)\nlocal = self\ndel local\nother.pipe = local.saved",
            "setattr(self, 'saved', self.session)\nlocal = self\ndel self.saved\nother.pipe = local.saved",
            "setattr(self, 'saved', self.session)\nlocal = self\ndef save(local=None):\n    other.pipe = local.saved",
            "setattr(self, 'saved', self.session)\ndef one():\n    local = self\ndef two():\n    other.pipe = local.saved",
            "setattr(self, 'saved', self.session)\ndef save(self):\n    local = self\n    other.pipe = local.saved",
        ):
            with self.subTest(body=body):
                source = "def run(self):\n" + "\n".join(
                    "    " + line for line in body.splitlines()
                )
                self.assert_binding_clear(source)
                # Deletion/shadowing can kill downstream evidence, never the
                # historical fact that the original bare-self store occurred.
                self.assertTrue(
                    any(
                        site["call"].startswith("bare-self-store")
                        for site in c["census"](source)
                    )
                )

    def test_namespace_stores_and_actual_destination_evidence(self):
        for declaration, prefix in (("global", ""), ("nonlocal", "outbound = None\n")):
            fixture = (
                prefix
                + f"def save():\n    {declaration} outbound\n    outbound = self.session\nsave()\nother.pipe = outbound"
            )
            with self.subTest(declaration=declaration):
                self.rejected(fixture, "outbound <- self.session", ".save")
                self.rejected(fixture)
        self.rejected(
            "global outbound\noutbound = self.session", "outbound <- self.session"
        )
        fixture = "class Store:\n    saved = self.session\nother.pipe = Store.saved"
        self.rejected(fixture, "saved <- self.session", ".Store")
        self.rejected(fixture)
        self.rejected(
            "def save():\n    global outbound\n    import requests as outbound",
            "outbound",
            ".save",
        )
        self.rejected(
            "class Store:\n    from requests import Session as saved", "saved", ".Store"
        )

    def test_import_binding_and_delete_kills(self):
        for source in (
            "import requests as outbound",
            "from requests import Session as outbound",
        ):
            self.rejected(source + "\nother.pipe = outbound")
        for source in (
            "(outbound := self.session)\ndel outbound\nother.pipe = outbound",
            "setattr(self, 'saved', self.session)\ndel self.saved\nother.pipe = self.saved",
            "try:\n    pass\nexcept self.session as outbound:\n    pass\nother.pipe = outbound",
        ):
            with self.subTest(source=source):
                self.assert_binding_clear(
                    "def run(self):\n"
                    + "\n".join("    " + line for line in source.splitlines())
                )
        self.rejected(
            "(outbound := self.session)\ndel outbound\n(outbound := self.session)\nother.pipe = outbound"
        )

    def test_comprehension_and_default_scopes_do_not_borrow_siblings(self):
        for source in (
            "def run(self):\n    [outbound for outbound in [self.session]]\n    outbound = None\n    other.pipe = outbound",
            "def run(self):\n    outbound = self.session\n    def save(outbound=None):\n        other.pipe = outbound",
            "def run(self):\n    seed = None\n    def save(outbound=seed):\n        seed = self.session\n        other.pipe = outbound",
            "def run(self):\n    self.saved = self.session\ndef sibling(self):\n    other.pipe = self.saved",
        ):
            with self.subTest(source=source):
                self.assert_binding_clear(source)

    def test_iteration_unpacks_rows_not_the_iterable_container(self):
        for iterable in (
            "[(None, None), (self.session, None)]",
            "[(None, self.session), (None, None)]",
        ):
            with self.subTest(iterable=iterable):
                self.rejected(
                    f"for outbound, unused in {iterable}:\n    other.pipe = outbound"
                )
                self.rejected(
                    f"[setattr(other, 'pipe', outbound) for outbound, unused in {iterable}]",
                    "setattr",
                )

    def test_declarations_cover_other_binding_forms(self):
        for fixture in (
            "global outbound\n(outbound := self.session)",
            "global outbound\nfor outbound in [self.session]:\n    pass",
            "global outbound\nwith self.session as outbound:\n    pass",
            "global outbound\nmatch self.session:\n    case outbound:\n        pass",
        ):
            with self.subTest(fixture=fixture):
                self.rejected(fixture, "outbound <-")
        self.rejected(
            "def save(arg: (outbound := self.session)):\n    pass\nother.pipe = outbound"
        )
        self.rejected(
            "class Store:\n    class Inner:\n        saved = self.session\nother.pipe = Store.Inner.saved"
        )
        self.rejected("type Saved = self.session\nother.pipe = Saved")

    def test_delete_does_not_erase_other_branches_or_restore_old_evidence(self):
        self.rejected(
            "(outbound := self.session)\nif enabled:\n    del outbound\nother.pipe = outbound"
        )
        for source in (
            "def run(self):\n    def save(outbound=self.session):\n        del outbound\n        other.pipe = outbound",
            "def run(self):\n    (outbound := self.session)\n    if enabled:\n        del outbound\n        other.pipe = outbound",
            "def run(self):\n    try:\n        pass\n    except self.session as outbound:\n        outbound = self.session\n    other.pipe = outbound",
        ):
            with self.subTest(source=source):
                self.assert_binding_clear(source)

    def test_ast_binding_node_coverage(self):
        aliases = runpy.run_path(str(HERE / "falcon_send_aliases.py"))
        expected = set(
            "Assign AnnAssign AugAssign NamedExpr For AsyncFor comprehension With AsyncWith ExceptHandler MatchAs MatchStar MatchMapping FunctionDef AsyncFunctionDef ClassDef Lambda Import ImportFrom Delete arg TypeAlias TypeVar ParamSpec TypeVarTuple".split()
        )
        self.assertEqual(set(aliases.get("BINDING_NODE_TYPES", ())), expected)
        # Exhaustively classify the interpreter's statement/expression/pattern
        # schema, not just the nodes present in our fixtures. New AST forms fail
        # until reviewed as binding or nonbinding (Python 3.12 is the test pin).
        nonbinding = set(
            "Return Delete Raise Assert Global Nonlocal Expr Pass Break Continue If While Try TryStar Match With AsyncWith For AsyncFor FunctionDef AsyncFunctionDef ClassDef Assign AugAssign AnnAssign Import ImportFrom TypeAlias BoolOp NamedExpr BinOp UnaryOp Lambda IfExp Dict Set ListComp SetComp DictComp GeneratorExp Await Yield YieldFrom Compare Call FormattedValue JoinedStr Constant Attribute Subscript Starred Name List Tuple Slice MatchValue MatchSingleton MatchSequence MatchMapping MatchClass MatchStar MatchAs MatchOr".split()
        )
        actual = {
            kind.__name__
            for base in (ast.stmt, ast.expr, ast.pattern)
            for kind in base.__subclasses__()
        }
        self.assertEqual(actual, nonbinding)
        self.assertEqual(
            {kind.__name__ for kind in ast.AST.__subclasses__()},
            set(
                "mod stmt expr expr_context boolop operator unaryop cmpop comprehension excepthandler arguments arg keyword alias withitem match_case pattern type_ignore type_param slice".split()
            ),
        )
        self.assertEqual(
            {
                kind.__name__: kind._fields
                for base in (ast.excepthandler, ast.type_param)
                for kind in base.__subclasses__()
            },
            {
                "ExceptHandler": ("type", "name", "body"),
                "TypeVar": ("name", "bound"),
                "ParamSpec": ("name",),
                "TypeVarTuple": ("name",),
            },
        )
        self.assertEqual(
            ast.arguments._fields,
            (
                "posonlyargs",
                "args",
                "vararg",
                "kwonlyargs",
                "kw_defaults",
                "kwarg",
                "defaults",
            ),
        )
        self.assertEqual(ast.alias._fields, ("name", "asname"))
        self.assertEqual(
            ast.comprehension._fields, ("target", "iter", "ifs", "is_async")
        )


if __name__ == "__main__":
    unittest.main()
