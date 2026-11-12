"""Source census of analysis admission; stdlib only, no app/DB/worker imports.

Conservative AST check, not a Python whole-program/data-flow proof. Named task
references, import/assignment aliases, dotted task strings, direct/delay/async
calls, start_activity arguments and schedule dictionaries are inventoried.
Unresolvable runtime-generated names in generic Temporal transport are not
invented call sites. The operator's dynamic debug route is checked explicitly.
"""

import ast
import json
import os
import unittest
from pathlib import Path

from tracer.tests.test_background_analysis_switch import ROOT

TARGETS = frozenset(
    {
        "run_deep_analysis_on_demand",
        "analyze_traces_on_demand",
        "check_and_process_trace_errors",
        "process_trace_error_batch",
    }
)
ADMISSION_NAMES = TARGETS - {"process_trace_error_batch"}
TASKS = "futureagi/tracer/tasks/error_analysis.py"
DEBUG = "futureagi/tfc/management/commands/debug_temporal.py"
ADMITTED = frozenset(
    {
        "run_deep_analysis_on_demand",
        "analyze_traces_on_demand",
        "process_trace_error_batch",
        "analyze_single_trace",
    }
)


def references(node, aliases):
    if isinstance(node, ast.Name):
        return set(aliases.get(node.id, {node.id} & TARGETS))
    if isinstance(node, ast.Attribute):
        return ({node.attr} & TARGETS) | references(node.value, aliases)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value.rsplit(".", 1)[-1]} & TARGETS
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(references(item, aliases) for item in node.elts))
    if isinstance(node, ast.Dict):
        return set().union(*(references(item, aliases) for item in node.values))
    return set()


def negative_gate(node):
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.Not)
        and isinstance(node.operand, ast.Call)
        and isinstance(node.operand.func, ast.Name)
        and node.operand.func.id == "background_analysis_enabled"
        and not node.operand.args
        and not node.operand.keywords
    )


def early_gate(function, site):
    # A top-level returning OFF branch must precede the enclosing statement.
    # A nested/conditional/log-only check does not dominate a later dispatch.
    return any(
        isinstance(stmt, ast.If)
        and stmt.end_lineno < site.lineno
        and negative_gate(stmt.test)
        and any(isinstance(child, ast.Return) for child in stmt.body)
        for stmt in function.body
    )


def debug_gate(function, site):
    for stmt in function.body:
        if not isinstance(stmt, ast.If) or stmt.end_lineno >= site.lineno:
            continue
        condition = stmt.test
        if not (
            isinstance(condition, ast.BoolOp)
            and isinstance(condition.op, ast.And)
            and len(condition.values) == 2
            and negative_gate(condition.values[1])
        ):
            continue
        names = condition.values[0]
        if not (
            isinstance(names, ast.Compare)
            and len(names.ops) == 1
            and isinstance(names.ops[0], ast.In)
        ):
            continue
        if ast.dump(names.left) != ast.dump(
            ast.parse('options.get("test_activity")', mode="eval").body
        ):
            continue
        if references(names.comparators[0], {}) != ADMISSION_NAMES:
            continue
        if any(
            isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call)
            and isinstance(child.exc.func, ast.Name)
            and child.exc.func.id == "CommandError"
            and len(child.exc.args) == 1
            and isinstance(child.exc.args[0], ast.Name)
            and child.exc.args[0].id == "BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE"
            for child in stmt.body
        ):
            return True
    return False


def census_source(source, path):
    tree = ast.parse(source, filename=path)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in TARGETS:
                    aliases[alias.asname or alias.name] = {alias.name}
    # Alias chains are bounded by the number of assignments, not a fixed depth.
    assignments = [
        n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        before = repr(aliases)
        for node in assignments:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    refs = references(node.value, aliases)
                    if refs:
                        aliases.setdefault(target.id, set()).update(refs)
        if before == repr(aliases):
            break

    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.Dict)):
            continue
        refs = references(node, aliases)
        if isinstance(node, ast.Call):
            refs = references(node.func, aliases)
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                refs |= references(arg, aliases)
        dynamic_debug = (
            path == DEBUG
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "start_activity"
        )
        if not refs and not dynamic_debug:
            continue
        # A registration dictionary nested in a call is already represented there.
        if isinstance(node, ast.Dict):
            parent = parents.get(node)
            if isinstance(parent, ast.keyword):
                parent = parents.get(parent)
            if isinstance(parent, ast.Call):
                continue
        function = parents.get(node)
        while function is not None and not isinstance(
            function, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            function = parents.get(function)
        classification = "UNGUARDED"
        if function is not None:
            if early_gate(function, node):
                classification = "gated admission"
            elif dynamic_debug and debug_gate(function, node):
                classification = "gated management admission"
            elif path == TASKS and function.name in ADMITTED:
                classification = "already-admitted chain (not cancelled)"
        rows.append(
            {
                "path": path,
                "line": node.lineno,
                "function": function.name if function else "<module>",
                "targets": sorted(ADMISSION_NAMES if dynamic_debug else refs),
                "form": ast.unparse(node.func)
                if isinstance(node, ast.Call)
                else "schedule dictionary",
                "classification": classification,
            }
        )
    return sorted(rows, key=lambda row: row["line"])


def repository_census():
    rows = []
    candidates = 0
    # Only source files; never read .env, credentials, deployment values or binaries.
    excluded = {".git", ".venv", "venv", "node_modules", "__pycache__", "tests"}
    for directory, dirs, files in os.walk(ROOT.parent):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".py") or name.startswith("test"):
                continue
            if any(word in name.lower() for word in ("secret", "credential", "values")):
                continue
            path = Path(directory) / name
            source = path.read_text()
            if not any(target in source for target in TARGETS):
                continue
            candidates += 1
            rows.extend(census_source(source, path.relative_to(ROOT.parent).as_posix()))
    return candidates, sorted(rows, key=lambda row: (row["path"], row["line"]))


class BackgroundAnalysisCensusTests(unittest.TestCase):
    def test_repository_admission_census(self):
        candidates, rows = repository_census()
        print(
            "BACKGROUND_ANALYSIS_CENSUS="
            + json.dumps(
                {
                    "candidateFiles": candidates,
                    "sites": rows,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.assertTrue(rows)
        self.assertEqual([r for r in rows if r["classification"] == "UNGUARDED"], [])
        self.assertTrue(any(r["form"] == "start_activity" for r in rows))
        self.assertEqual(set().union(*(set(r["targets"]) for r in rows)), TARGETS)

    def test_census_detects_all_call_forms_aliases_and_schedule_registrations(self):
        for target in TARGETS:
            for expression in (
                f"{target}()",
                f"{target}.delay()",
                f"{target}.apply_async(args=())",
                f"start_activity('{target}')",
                f"start_activity(activity_name='{target}')",
                f"ScheduleConfig(activity_name='tracer.tasks.error_analysis.{target}')",
                f"{{'task': 'tracer.tasks.error_analysis.{target}'}}",
                "alias.delay()",
                "start_activity(task_name)",
            ):
                source = (
                    f"from tracer.tasks.error_analysis import {target} as alias\n"
                    f"task_name = '{target}'\ndef fresh():\n    {expression}\n"
                )
                with self.subTest(target=target, expression=expression):
                    rows = census_source(source, "new_caller.py")
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["classification"], "UNGUARDED")
                    self.assertEqual(rows[0]["targets"], [target])

    def test_census_requires_dominating_gate_not_just_mention(self):
        for gate in (
            "if not background_analysis_enabled():\n        return",
            "if False:\n        if not background_analysis_enabled():\n            return",
            "if not background_analysis_enabled():\n        logger.info('off')",
            "pass",
        ):
            source = f"def fresh():\n    {gate}\n    analyze_traces_on_demand.delay()\n"
            rows = census_source(source, "new_caller.py")
            expected = (
                "gated admission"
                if gate.startswith("if not") and "return" in gate
                else "UNGUARDED"
            )
            self.assertEqual(rows[0]["classification"], expected)

    def test_census_fails_if_any_production_admission_gate_is_removed(self):
        for path in (
            "futureagi/tracer/queries/feed.py",
            "futureagi/ai_tools/tools/tracing/analyze_project_traces.py",
            TASKS,
            DEBUG,
        ):
            source = (ROOT.parent / path).read_text()
            changed = source.replace("not background_analysis_enabled()", "False")
            self.assertNotEqual(source, changed)
            self.assertTrue(
                any(
                    r["classification"] == "UNGUARDED"
                    for r in census_source(changed, path)
                ),
                path,
            )

    def test_legacy_gate_precedes_all_work_and_queued_bodies_remain_ungated(self):
        tree = ast.parse((ROOT.parent / TASKS).read_text())
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        entry = functions["check_and_process_trace_errors"]
        # Only docstring and capability import may precede the gate.
        self.assertIsInstance(entry.body[0], ast.Expr)
        self.assertIsInstance(entry.body[1], ast.ImportFrom)
        self.assertEqual(entry.body[1].module, "tfc.capabilities.background_analysis")
        self.assertIsInstance(entry.body[2], ast.If)
        self.assertTrue(negative_gate(entry.body[2].test))
        for name in ADMITTED:
            self.assertNotIn(
                "background_analysis_enabled", ast.unparse(functions[name])
            )

    def test_already_admitted_exemption_is_limited_to_worker_bodies(self):
        source = (
            "def process_trace_error_batch():\n    analyze_traces_on_demand.delay()\n"
        )
        self.assertEqual(
            census_source(source, TASKS)[0]["classification"],
            "already-admitted chain (not cancelled)",
        )
        self.assertEqual(
            census_source(source, "other.py")[0]["classification"], "UNGUARDED"
        )
