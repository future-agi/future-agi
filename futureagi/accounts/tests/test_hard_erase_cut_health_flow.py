"""Cut composition regressions: exact dispatcher binding and sparse source inputs."""

import ast
import runpy
import unittest
from pathlib import Path
from types import FunctionType
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[2]
DISPATCH = runpy.run_path(str(TESTS / "hard_erase_callgraph_dispatch.py"))
RESOLVER = runpy.run_path(str(TESTS / "hard_erase_callgraph_resolver.py"))
SOURCE = runpy.run_path(str(TESTS / "hard_erase_source_reader.py"))
SOURCES = runpy.run_path(str(TESTS / "hard_erase_source_files.py"))

# OSS still runs every binding/mutation control against the same installer AST.
# This is source data, never a substitute module in the product call graph. Its
# minimal module envelope has its own fixed fingerprint; installed EE always
# uses the real source and the unchanged production-module fingerprint.
OSS_DISPATCH_SOURCE = '''from contextvars import ContextVar
_ACTIVE = ContextVar("background_wire_dispatch", default=None)


def install_falcon_dispatch(agent, witness):
    """Bind original provider ids without changing shared AgentLoop source."""
    _state(witness)
    original = agent._execute_tool

    async def dispatch(tool, decoded):
        if not witness.wire_pending:
            refuse(witness)
        call_id = witness.wire_pending.pop(0)
        call = witness.wire_calls[call_id]
        data = require_payload(call.payload, witness, "tool:" + tool.name)
        if call.name != tool.name or not _identical(data, decoded) or call.consumed:
            refuse(witness)
        token = _ACTIVE.set((witness, call_id))
        try:
            result = await original(tool, decoded)
            if not call.consumed:
                refuse(witness)
            return result
        except BaseException:
            witness.failed = True
            raise
        finally:
            _ACTIVE.reset(token)

    agent._execute_tool = dispatch
'''
OSS_MODULE_AST = "cee599a71c830c7372652cf22571ff7e1878864452b514ce7eddfc27b7859749"


class DispatcherBindingTests(unittest.TestCase):
    def fixture(self):
        module = DISPATCH["MODULE"]
        path = ROOT / "futureagi" / (module.replace(".", "/") + ".py")
        if SOURCES["ee_available"](ROOT):
            tree = ast.parse(path.read_text())  # Present EE must not lose a file.
            self.bind = DISPATCH["bind"]
        else:
            # Assert the actual OSS contract too: no installed dispatcher means
            # no invented registration edges or bound arguments in the graph.
            self.assertEqual(DISPATCH["bind"]({}, {}, {}, RESOLVER, {}), ({}, set()))
            tree = ast.parse(OSS_DISPATCH_SOURCE)
            self.bind = FunctionType(
                DISPATCH["bind"].__code__,
                {**DISPATCH["bind"].__globals__, "MODULE_AST": OSS_MODULE_AST},
            )
        install = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "install_falcon_dispatch"
        )
        dispatch = next(n for n in install.body if isinstance(n, ast.AsyncFunctionDef))
        caller_path = ROOT / "futureagi/tracer/tasks/error_analysis.py"
        caller = next(
            n
            for n in ast.walk(ast.parse(caller_path.read_text()))
            if isinstance(n, ast.FunctionDef)
            and n.name == "_falcon_analyze_trace_in_scope"
        )
        original = ast.parse("def original():\n    tool.run(params, context)\n").body[0]
        functions = {
            DISPATCH["INSTALL"]: (str(path), install, module, ""),
            DISPATCH["DISPATCH"]: (
                str(path),
                dispatch,
                module,
                "install_falcon_dispatch.<locals>",
            ),
            DISPATCH["CALLER"]: (
                "caller.py",
                caller,
                "tracer.tasks.error_analysis",
                "",
            ),
            DISPATCH["ORIGINAL"]: (
                "agent.py",
                original,
                "ee.falcon_ai.agent",
                "AgentLoop",
            ),
        }
        environments = {key: {} for key in functions}
        environments[DISPATCH["CALLER"]] = {
            "install_falcon_dispatch": {DISPATCH["INSTALL"]},
            "agent": {DISPATCH["AGENT"]},
        }
        return functions, environments, {}, RESOLVER, {module: tree}

    def test_capture_and_registration_keep_original_issuance_method(self):
        args = self.fixture()
        edges, bound = self.bind(*args)
        self.assertEqual(
            {target for _, target in edges[DISPATCH["INSTALL"]]}, {DISPATCH["DISPATCH"]}
        )
        call = next(
            n
            for n in ast.walk(args[0][DISPATCH["CALLER"]][1])
            if isinstance(n, ast.Call)
            and ast.unparse(n.func) == "install_falcon_dispatch"
        )
        self.assertEqual(bound, {id(call.args[0])})
        self.assertEqual(
            args[1][DISPATCH["DISPATCH"]]["original"], {DISPATCH["ORIGINAL"]}
        )
        closure = args[0][DISPATCH["DISPATCH"]][1]
        marked = [
            ast.unparse(n)
            for n in ast.walk(closure)
            if getattr(n, "issuance_non_row_effect", False)
        ]
        self.assertEqual(marked, ["_ACTIVE.set((witness, call_id))"])
        # The actual unresolved-reference checker accepts ONLY the bound argument.
        args[1][DISPATCH["CALLER"]]["issuer"] = {
            "accounts.services.identity_issuance.issue_membership"
        }
        bad = ast.parse("def bad():\n    unknown(issuer)\n").body[0]
        with self.assertRaisesRegex(ValueError, "unresolved issuance reference"):
            RESOLVER["unresolved_references"](
                "bad",
                bad,
                args[1][DISPATCH["CALLER"]],
                "fixture",
                "",
                {},
                {"accounts.services.identity_issuance.issue_membership"},
                [],
                resolved_dispatch_arguments=bound,
            )

    def test_changed_closure_installer_or_contextvar_binding_refuses(self):
        for location in ("closure", "installer", "module"):
            args = self.fixture()
            target = (
                args[0][DISPATCH["DISPATCH"]][1]
                if location == "closure"
                else args[0][DISPATCH["INSTALL"]][1]
                if location == "installer"
                else args[4][DISPATCH["MODULE"]]
            )
            target.body.append(ast.parse("row.save()").body[0])
            with (
                self.subTest(location=location),
                self.assertRaisesRegex(
                    ValueError, "unresolved issuance dispatcher construct"
                ),
            ):
                self.bind(*args)

    def test_changed_instance_setup_or_escape_refuses(self):
        for statement in (
            "agent._execute_tool = replacement",
            "unknown(agent)",
            "row.save()",
        ):
            args = self.fixture()
            args[0][DISPATCH["CALLER"]][1].body.append(ast.parse(statement).body[0])
            with (
                self.subTest(statement=statement),
                self.assertRaisesRegex(
                    ValueError, "unresolved issuance dispatcher caller"
                ),
            ):
                self.bind(*args)

    def test_unknown_instance_or_missing_original_refuses(self):
        for change in ("unknown", "union", "missing"):
            args = self.fixture()
            if change == "missing":
                del args[0][DISPATCH["ORIGINAL"]]
            else:
                args[1][DISPATCH["CALLER"]]["agent"] = (
                    {"unknown.Agent"}
                    if change == "unknown"
                    else {"unknown.Agent", DISPATCH["AGENT"]}
                )
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "unresolved issuance dispatcher"),
            ):
                self.bind(*args)


class SparseSourceTests(unittest.TestCase):
    def test_working_tree_edits_take_precedence(self):
        with (
            patch.object(Path, "read_text", return_value="edited source"),
            patch.object(SOURCE["subprocess"], "check_output") as git,
        ):
            self.assertEqual(
                SOURCE["read_source"](ROOT, ROOT / "fixture.py"), "edited source"
            )
            git.assert_not_called()

    def test_only_exact_skip_worktree_entry_can_supply_index_source(self):
        for entry in ("S fixture.py\0", "H fixture.py\0", "", "S other.py\0"):
            with (
                self.subTest(entry=entry),
                patch.object(Path, "read_text", side_effect=FileNotFoundError),
                patch.object(
                    SOURCE["subprocess"],
                    "check_output",
                    side_effect=[entry, "index source"],
                ) as git,
            ):
                if entry.startswith("S fixture.py"):
                    self.assertEqual(
                        SOURCE["read_source"](ROOT, ROOT / "fixture.py"), "index source"
                    )
                    self.assertEqual(
                        git.call_args.args[0][-2:], ["show", ":fixture.py"]
                    )
                else:
                    with self.assertRaises(FileNotFoundError):
                        SOURCE["read_source"](ROOT, ROOT / "fixture.py")
                    self.assertEqual(git.call_count, 1)

    def test_excluded_inputs_are_never_read_or_fetched(self):
        for path in (
            "settings.py",
            "secret_helper.py",
            "values.sql",
            ".env",
            "fixture.txt",
        ):
            with (
                self.subTest(path=path),
                patch.object(Path, "read_text") as read,
                patch.object(SOURCE["subprocess"], "check_output") as git,
            ):
                with self.assertRaises(ValueError):
                    SOURCE["read_source"](ROOT, ROOT / path)
                read.assert_not_called()
                git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
