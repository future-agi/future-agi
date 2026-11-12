"""Fail-closed background capability source checks and independent fixtures."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {
    "explore_trace_legacy": "ai_tools.tools.tracing.explore_trace.ExploreTraceTool",
    "read_trace_span": "ai_tools.tools.tracing.read_trace_span.ReadTraceSpanTool",
    "submit_trace_finding": "ai_tools.tools.tracing.submit_trace_finding.SubmitTraceFindingTool",
    "submit_trace_scores": "ai_tools.tools.tracing.submit_trace_scores.SubmitTraceScoresTool",
}


def check_source(source, *, policy=False, dispatch=False):
    """Deliberately narrow source language, not a best-effort alias analyser.

    Indirect dispatch is forbidden even when an alias looks harmless today.
    Direct dispatch requires a dominating refusal, not just an earlier line.
    Only the BODY of a proven interactive branch exempts legacy operations.
    """
    tree = ast.parse(source)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    errors = []
    protected = {"BACKGROUND_TOOL_ALLOWLIST", "registry", "tool_registry"}
    declarations = 0

    def text(node):
        return ast.unparse(node)

    def interactive(node):
        while node in parents:
            parent = parents[node]
            if isinstance(parent, ast.If):
                conditions = (
                    parent.test.values
                    if isinstance(parent.test, ast.BoolOp)
                    and isinstance(parent.test.op, ast.And)
                    else [parent.test]
                )
                if node in parent.body and any(
                    text(c) in {"guard is None", "background_guard is None"}
                    for c in conditions
                ):
                    return True
                if (
                    node in parent.orelse
                    and text(parent.test) == "background_guard is not None"
                ):
                    return True
            node = parent
        return False

    # Track protected list copies as well as aliases; otherwise list()/slicing
    # and an innocently named local can widen the background execution registry.
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = node.value
                derived = text(value) in protected
                if isinstance(value, ast.Subscript):
                    derived = text(value.value) in protected
                if isinstance(value, ast.Call):
                    derived = text(value.func) == "background_tools" or (
                        text(value.func) in {"list", "tuple", "dict", "set"}
                        and any(text(a) in protected for a in value.args)
                    )
                if derived:
                    protected.update(text(t) for t in node.targets)

    def gated(node):
        while node in parents:
            parent = parents[node]
            for value in vars(parent).values():
                if not isinstance(value, list) or node not in value:
                    continue
                prefix = value[: value.index(node)]
                gate_seen = False
                for stmt in prefix:
                    if (
                        isinstance(stmt, ast.Assign)
                        and len(stmt.targets) == 1
                        and text(stmt.targets[0]) == "denial"
                        and isinstance(stmt.value, ast.Call)
                        and text(stmt.value.func) == "background_tool_denial"
                    ):
                        gate_seen = True
                    elif (
                        gate_seen
                        and isinstance(stmt, ast.If)
                        and text(stmt.test) in {"denial is not None", "denial"}
                        and any(
                            isinstance(s, (ast.Return, ast.Raise)) for s in stmt.body
                        )
                    ):
                        return True
            node = parent
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = (
                node.targets
                if isinstance(node, (ast.Assign, ast.Delete))
                else [node.target]
            )
            for target in targets:
                target_text = text(target)
                if target_text == "BACKGROUND_TOOL_ALLOWLIST":
                    declarations += 1
                    try:
                        valid = (
                            isinstance(node, ast.Assign)
                            and ast.literal_eval(node.value) == ALLOWED
                            and declarations == 1
                        )
                    except (ValueError, TypeError, AttributeError):
                        valid = False
                    if not valid:
                        errors.append("nonliteral/mutated allowlist")
                elif target_text in protected and not interactive(node):
                    # A direct reviewed background_tools result is the only
                    # construction permitted for a protected runtime list.
                    value = getattr(node, "value", None)
                    if not (
                        isinstance(value, ast.Call)
                        and text(value.func) == "background_tools"
                    ):
                        errors.append("protected registry alias/reassignment")
                elif (
                    isinstance(target, (ast.Subscript, ast.Attribute))
                    and text(target.value) in protected
                    and not interactive(node)
                ):
                    errors.append("protected registry mutation")
        if isinstance(node, ast.Attribute) and node.attr in {
            "delay",
            "apply_async",
            "send_task",
            "signature",
            "run",
            "async_execute",
        }:
            parent = parents.get(node)
            direct = isinstance(parent, ast.Call) and parent.func is node
            child = node.attr in {"delay", "apply_async", "send_task", "signature"}
            tool = dispatch and node.attr in {"run", "async_execute"}
            if child or tool:
                if not direct:
                    errors.append("indirect dispatch reference")
                elif interactive(node):
                    continue
                elif child:
                    errors.append("child dispatch")
                elif text(node.value) != "tool" or not gated(parent):
                    errors.append("dispatch without dominating policy refusal")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in protected and alias.asname:
                    errors.append("protected registry import alias")
        if isinstance(node, ast.Attribute) and text(node.value) in protected:
            if (
                not interactive(node) or text(node.value) == "BACKGROUND_TOOL_ALLOWLIST"
            ) and node.attr not in {"get", "items", "keys", "values"}:
                errors.append("protected registry method reference")
        if isinstance(node, ast.Call):
            if text(node.func) in {
                "globals",
                "locals",
                "vars",
                "eval",
                "exec",
                "__import__",
            }:
                errors.append("reflective dispatch/mutation")
            if text(node.func) in {"getattr", "setattr", "delattr"}:
                attr = node.args[1] if len(node.args) > 1 else None
                if not isinstance(attr, ast.Constant) or attr.value in {
                    "delay",
                    "apply_async",
                    "send_task",
                    "run",
                    "async_execute",
                    "BACKGROUND_TOOL_ALLOWLIST",
                    "tools",
                    "_all_tools",
                }:
                    errors.append("indirect dynamic dispatch/mutation")
            if (
                isinstance(node.func, ast.Attribute)
                and text(node.func.value) in protected
                and not interactive(node)
            ):
                if node.func.attr not in {"get", "items", "keys", "values"}:
                    errors.append("protected registry method")
    return errors


NEGATIVE_FIXTURES = {
    "local-alias-child-dispatch": "send = cluster_project_errors.delay\nsend('trace')",
    "getattr-child-dispatch": "getattr(cluster_project_errors, 'delay')('trace')",
    "getattr-dynamic-dispatch": "getattr(cluster_project_errors, method)('trace')",
    "allowlist-update": "BACKGROUND_TOOL_ALLOWLIST.update(extra)",
    "allowlist-subscript": "BACKGROUND_TOOL_ALLOWLIST['new'] = 'other.Tool'",
    "allowlist-alias": "names = BACKGROUND_TOOL_ALLOWLIST\nnames.update(extra)",
    "allowlist-augmented": "BACKGROUND_TOOL_ALLOWLIST |= extra",
    "registry-mutation": "registry.register(unreviewed)",
    "aliased-tool-object": "runner = tool\nrunner.run(params, context)",
    "interactive-alias-escape": "if guard is None:\n    dispatch = task.delay\ndispatch('trace')",
    "background-tools-append": "tools = background_tools(registry, context)\ntools.append(other)",
    "background-tools-attribute": "self.tools = background_tools(registry, context)\nself.tools.extend(extra)",
    "getattr-tool-run-before-gate": "getattr(tool, 'run')(params, context)\ndenial = background_tool_denial(tool, context)",
    "aliased-tool-run-before-gate": "execute = tool.run\nexecute(params, context)\ndenial = background_tool_denial(tool, context)",
    "conditional-gate": "if condition:\n    denial = background_tool_denial(tool, context)\nreturn tool.run(params, context)",
    "aliased-list-mutator": "tools = background_tools(registry, context)\nappend = tools.append\nappend(other)",
    "aliased-import-allowlist": "from ai_tools.background import BACKGROUND_TOOL_ALLOWLIST as names\nnames.update(extra)",
    "interactive-global-mutation": "if guard is None:\n    BACKGROUND_TOOL_ALLOWLIST.update(extra)",
    "setattr-tool-list": "setattr(self, 'tools', extra)",
    "reflective-registry": "globals()['BACKGROUND_TOOL_ALLOWLIST'].update(extra)",
    "dispatch-in-else": "if guard is None:\n    pass\nelse:\n    cluster_project_errors.delay('trace')",
}


class StaticTests(unittest.TestCase):
    def test_negative_fixtures(self):
        for name, source in NEGATIVE_FIXTURES.items():
            with self.subTest(name=name):
                self.assertTrue(check_source(source, policy=True, dispatch=True), name)

    def test_positive_fixtures(self):
        samples = [
            "if guard is None:\n    cluster_project_errors.delay('trace')",
            "denial = background_tool_denial(tool, context)\nif denial is not None:\n    return denial\nreturn tool.run(params, context)",
            "tools = background_tools(registry, context)\nfor tool in tools:\n    print(tool.name)",
        ]
        for source in samples:
            self.assertEqual(check_source(source, dispatch=True), [])

    def test_real_allowlisted_tools_and_policy(self):
        for identity in ALLOWED.values():
            module, _ = identity.rsplit(".", 1)
            path = ROOT / (module.replace(".", "/") + ".py")
            self.assertEqual(check_source(path.read_text()), [], str(path))
        path = ROOT / "ai_tools/background.py"
        self.assertEqual(check_source(path.read_text(), policy=True), [])

    def test_transport_choke_points_are_not_bypassed(self):
        for relative, expected in (
            ("ee/falcon_ai/llm_client.py", 4),
            ("ee/licensing/activation_client.py", 1),
        ):
            tree = ast.parse((ROOT / relative).read_text())
            clients = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "httpx.AsyncClient"
            ]
            self.assertEqual(len(clients), expected, relative)
            for call in clients:
                self.assertTrue(
                    any(
                        k.arg is None
                        and ast.unparse(k.value) == "async_client_options()"
                        for k in call.keywords
                    ),
                    relative,
                )
            self.assertFalse(
                any(
                    isinstance(n, ast.Call)
                    and ast.unparse(n.func)
                    in {"httpx.post", "httpx.get", "httpx.request"}
                    for n in ast.walk(tree)
                ),
                relative,
            )
        source = (ROOT / "agentic_eval/core/embeddings/serving_client.py").read_text()
        for call in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)):
            name = ast.unparse(call.func)
            self.assertNotIn(
                name,
                {
                    "requests.get",
                    "requests.post",
                    "requests.request",
                    "self.session.get",
                    "self.session.post",
                    "self.session.request",
                },
            )

    def test_reachable_helpers_cannot_add_indirect_dispatch(self):
        for relative in (
            "ee/falcon_ai/llm_client.py",
            "ee/falcon_ai/context_manager.py",
            "ee/licensing/managed_ai.py",
            "ee/licensing/activation_client.py",
            "agentic_eval/core/embeddings/serving_client.py",
            "agentic_eval/core/embeddings/embedding_manager.py",
            "agentic_eval/core/database/ch_vector.py",
            "tracer/services/clickhouse/application_read_transport.py",
        ):
            self.assertEqual(check_source((ROOT / relative).read_text()), [], relative)
        tree = ast.parse((ROOT / "tracer/queries/error_analysis.py").read_text())
        for name in ("ingest_trace_error_embeddings", "_embedding_exists_for_detail"):
            node = next(n for n in ast.walk(tree) if getattr(n, "name", None) == name)
            self.assertEqual(check_source(ast.unparse(node)), [], name)

    def test_agent_background_registration_and_dispatch(self):
        tree = ast.parse((ROOT / "ee/falcon_ai/agent.py").read_text())
        for name in ("_run", "_execute_tool", "_run_tool"):
            node = next(n for n in ast.walk(tree) if getattr(n, "name", None) == name)
            self.assertEqual(check_source(ast.unparse(node), dispatch=True), [], name)


if __name__ == "__main__":
    unittest.main()
