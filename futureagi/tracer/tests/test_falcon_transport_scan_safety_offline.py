"""Ruling 564: a failed component is a rejected source, never a crashed audit."""

import ast
import runpy
import signal
import sys
import textwrap
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
c = runpy.run_path(str(HERE / "falcon_send_census.py"))
a = runpy.run_path(str(HERE / "falcon_send_admission.py"))


@contextmanager
def bounded(seconds=10):
    def expired(*args):
        raise TimeoutError("scan exceeded hard bound")

    previous = signal.signal(signal.SIGALRM, expired)
    start = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    # A component wrapper may reject TimeoutError; it must not hide a slow scan.
    assert time.monotonic() - start < seconds


def deep_sources(depth):
    unary = "not " * depth + "(lookup := getattr)"
    return {
        "assignment": f"setter = getattr(obj, 'setattr', {unary})\nreturn setter",
        "receiver": f"return getattr(({unary}) or obj, 'setattr')",
        "attribute-base": f"value = ({unary}).real\nreturn lookup(obj, 'setattr')",
        "attribute-chain": "value = (lookup := getattr)"
        + ".__call__" * depth
        + "\nreturn lookup(obj, 'setattr')",
        "lambda-callee": f"holder = (lambda: {unary})()\nreturn getattr(obj, 'setattr')",
        "attribute-store": "box"
        + ".field" * depth
        + " = getattr\nreturn getattr(obj, 'setattr')",
    }


def scanner(source):
    tree = ast.parse(source)
    # Construction of the OLD resolver itself recurses on deep attributes. Only
    # its type and transport configuration are inputs to the new scanner.
    aliases = c["LocalAliases"](ast.parse("pass"), {}, c["TRANSPORT_MODULES"])
    return c["_aliases"]["BuiltinSetterLoads"](tree, aliases)


class ScanSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources, _ = c["load_sources"]()

    def tearDown(self):
        c["census"].cache_clear()

    def test_deep_getter_positions_direct_scanner(self):
        limit = sys.getrecursionlimit()
        for depth in (400, 2000):
            for label, body in deep_sources(depth).items():
                with self.subTest(depth=depth, position=label), bounded():
                    source = "def acquire(obj):\n" + textwrap.indent(body, "    ")
                    flags = list(scanner(source).violations())
                    self.assertTrue(flags)
                    self.assertTrue(
                        all(
                            text.startswith("builtin-setter-load ") for _, text in flags
                        )
                    )
                    self.assertEqual(sys.getrecursionlimit(), limit)

    def test_deep_positions_complete_audit_rejects_without_crashing(self):
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
        for depth in (400, 2000):
            for label, body in deep_sources(depth).items():
                with self.subTest(depth=depth, position=label), bounded():
                    lines = source.splitlines(keepends=True)
                    lines[method.body[0].lineno - 1 : method.body[0].lineno - 1] = (
                        textwrap.indent(body + "\n", "        ").splitlines(
                            keepends=True
                        )
                    )
                    sites, errors = c["audit"](
                        {**self.sources, module: "".join(lines)},
                        a["admission"],
                        a["EXCEPTIONS"],
                        a["EXCEPTION_COUNTS"],
                    )
                    self.assertTrue(
                        any(
                            name == module and site.get("structural")
                            for name, site in sites
                        )
                    )
                    self.assertTrue(
                        any(
                            module in error
                            and (
                                "builtin-setter-load" in error
                                or "unscannable:" in error
                            )
                            for error in errors
                        ),
                        errors,
                    )

    def test_each_component_constructor_exception_rejects_source(self):
        classes = (
            "FrozenLocalAliases",
            "FrozenTransportStores",
            "BindingTransportStores",
            "BareSelfStores",
            "BuiltinSetterLoads",
        )
        source = "def run():\n    consume(setattr)\n"
        for name in classes:
            with self.subTest(component=name):
                c["census"].cache_clear()
                with patch.object(
                    c["_aliases"][name],
                    "__init__",
                    side_effect=RuntimeError("synthetic"),
                ):
                    sites, errors = c["audit"](
                        {"broken": source, "healthy": "pass"},
                        lambda *args: "admitted",
                        {},
                    )
                self.assertTrue(
                    any(
                        "unscannable: RuntimeError at broken:" in error
                        for error in errors
                    ),
                    errors,
                )
                # A failed earlier component must not stop the remaining scans.
                if name != "BuiltinSetterLoads":
                    self.assertTrue(
                        any(
                            module == "broken"
                            and site["call"] == "builtin-setter-load setattr"
                            for module, site in sites
                        )
                    )

    def test_component_iteration_exception_keeps_prior_and_later_flags(self):
        def broken(checker):
            yield checker.forms.tree.body[0], "synthetic-before-error"
            raise ValueError("synthetic iteration failure")

        with patch.object(
            c["_aliases"]["BindingTransportStores"], "violations", broken
        ):
            sites, errors = c["audit"](
                {"partial": "consume(setattr)"}, lambda *args: None, {}
            )
        self.assertTrue(
            any(site["call"] == "synthetic-before-error" for _, site in sites)
        )
        self.assertTrue(
            any(site["call"] == "builtin-setter-load setattr" for _, site in sites)
        )
        self.assertTrue(
            any("unscannable: ValueError at partial:" in error for error in errors),
            errors,
        )

    def test_parser_and_send_alias_errors_reject_source(self):
        for error_type in (RuntimeError, RecursionError, MemoryError):
            with self.subTest(error=error_type.__name__):
                c["census"].cache_clear()
                with patch.object(
                    c["LocalAliases"], "resolve", side_effect=error_type("synthetic")
                ):
                    _, errors = c["audit"](
                        {"broken": "client.post('url')"}, lambda *args: "admitted", {}
                    )
                self.assertTrue(
                    any(
                        f"unscannable: {error_type.__name__} at broken:" in error
                        for error in errors
                    ),
                    errors,
                )
        _, errors = c["audit"](
            {"invalid": "def broken(:\n", "valid": "pass"}, lambda *args: "admitted", {}
        )
        self.assertTrue(
            any("unscannable: SyntaxError at invalid:1" in error for error in errors),
            errors,
        )
        self.assertFalse(any("at valid:" in error for error in errors))

    def test_unscannable_cannot_receive_an_exact_exception_or_admission(self):
        source = "consume(setattr)"
        with patch.object(
            c["_aliases"]["BuiltinSetterLoads"],
            "__init__",
            side_effect=RuntimeError("private error details"),
        ):
            sites = c["census"](source)
            exceptions = {
                ("broken", site["owner"], site["call"]): "attempted waiver"
                for site in sites
            }
            _, errors = c["audit"](
                {"broken": source}, lambda *args: "admitted", exceptions
            )
        self.assertTrue(
            any("unscannable: RuntimeError at broken:" in error for error in errors),
            errors,
        )
        self.assertFalse(any("private error details" in error for error in errors))

    def test_admission_exception_rejects_source(self):
        def broken(*args):
            raise LookupError("synthetic admission error")

        _, errors = c["audit"]({"broken": "client.post('url')"}, broken, {})
        self.assertTrue(
            any("unscannable: LookupError at broken:1" in error for error in errors),
            errors,
        )

    def test_opaque_deep_fstring_is_bounded_and_not_a_text_marker(self):
        tools = runpy.run_path(str(HERE / "falcon_send_scan_safety.py"))
        renderer = tools["StackSafeText"]()
        source = "getattr(obj, 'setattr', f'{" + "not " * 2000 + "True}')"
        node = ast.parse(source).body[0].value
        with bounded():
            diagnostic = renderer.diagnostic(node)
        self.assertIn("[at 1:0-", diagnostic)
        self.assertLess(len(diagnostic), 240)
        # A source literal resembling the internal opaque key is not evidence
        # that this entirely shallow diagnostic contains an opaque subtree.
        shallow = ast.parse("getattr(obj, 'setattr', '<JoinedStr:')").body[0].value
        self.assertEqual(renderer.diagnostic(shallow), ast.unparse(shallow))

    def test_shallow_and_previously_renderable_deep_messages_exact(self):
        fixtures = (
            "consume(setattr)",
            "consume((base := object).__setattr__)",
            "return getattr(obj, 'setattr')",
            "return (lookup := getattr)(obj, 'setattr')",
            "setter = getattr(obj, 'setattr', " + "not " * 200 + "(lookup := getattr))",
            "return getattr(("
            + "not " * 200
            + "(lookup := getattr)) or obj, 'setattr')",
            "return getattr(obj, 'setattr', f'{"
            + "not " * 200
            + "(lookup := getattr)}')",
        )
        for body in fixtures:
            with self.subTest(fixture=body[:80]), bounded():
                source = "def run(obj):\n" + textwrap.indent(body, "    ")
                flags = list(scanner(source).violations())
                self.assertTrue(flags)
                for node, diagnostic in flags:
                    self.assertEqual(
                        diagnostic, "builtin-setter-load " + ast.unparse(node)
                    )


if __name__ == "__main__":
    unittest.main()
