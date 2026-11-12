"""Offline structural regressions for the composed Simulate migration graph.

Run directly with python3 -I -B. No Django settings, database, or credentials
are loaded. These checks do not replace MigrationExecutor/endpoint proof.
"""

import ast
import hashlib
from pathlib import Path
import unittest


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
M1 = "0102_agent_definition_encrypted_api_key"
M2 = "0103_encrypt_agent_definition_api_key"
M3 = "0104_remove_agent_definition_api_key"
LINEAGE = "0102_prompt_optimizer_tenant_lineage"
MERGE = "0105_merge_api_key_at_rest_tenant_lineage"
ENGINE_NOTES = "0102_agent_fix_engine_notes"
CREDENTIALS_MERGE = "0106_merge_c25_credentials_agent_fix_notes"
# Bridged bytes at da89f4e6485cf0efc1976306295069d3c197a7c8.
ACCEPTED_SHA256 = {
    M1: "d5416519f416b1b1f4ddbfedf72f4480f6c071911992ce3e96e5f9f111a925ba",
    M2: "f84e9d24a98288270f98bb0cd46a1fa88255a47e0ce7f1b5b2c2353224ec31c7",
    M3: "db56f6ca1f5f3427d3091ab1a1c09495b59df76545491b6d37c1266f1d7c8d88",
    LINEAGE: "3e723f30dbcbcb3285ac1247a05dd3f1881d93b15845a31fa0edfc1a3fc73068",
}
# Canonical AST pins from a63674d6e19c1310c5a929f781f16d871e0556e2,
# before the bridge. Keep these fixed when refreshing the bridged byte pins.
ACCEPTED_OPERATIONS_SHA256 = {
    M1: "c633d4079e0086824239d2b572a77b668b19f4379d723a5a1401c5b071bc3f58",
    M2: "dae53acefca81f68817b3e4ecf28479907d547006f7f36aa14deb9ec6e1fb946",
    M3: "4201f01f6f316fae9af9614e9443baf47e45e32607e7fdd68776e60c545ed761",
    LINEAGE: "3dd706e6a92f05f3174930e9b86407ab4ddee670743dc0fdc3585347e2e6894f",
}
# Also freeze imports and helper bodies used by the accepted operations.
ACCEPTED_SOURCE_SHA256 = {
    M1: "0365bca7938affea0740de4d718821699986df58d2d57f3dc1d9de23d4a7f81b",
    M2: "f51b561832966d520c278df3299b6637b45f9431ef83f349b1d4e5e711704e4b",
    M3: "fe4a1602e2c937537194058dc1fa6321c770777286435601ff94b2952c659473",
    LINEAGE: "712062a7f25038f948316e5efe41f9276ab2f4c528e19d727af9738b65ff2284",
}
# Literal pairs from LEGACY_APPLIED_NAMES; never import the bridge here.
LEGACY_NAMES = {
    M1: ("simulate", "0100_agent_definition_encrypted_api_key"),
    M2: ("simulate", "0101_encrypt_agent_definition_api_key"),
    M3: ("simulate", "0102_remove_agent_definition_api_key"),
    LINEAGE: ("simulate", "0100_prompt_optimizer_tenant_lineage"),
}


def _canonical(node):
    """Match the bridge test's stable AST encoding across Python versions."""
    if isinstance(node, ast.AST):
        parts = []
        for name in node._fields:
            value = getattr(node, name, None)
            if value is None or value == []:
                continue
            parts.append(f"{name}={_canonical(value)}")
        return f"{type(node).__name__}({','.join(parts)})"
    if isinstance(node, list):
        return "[" + ",".join(_canonical(item) for item in node) + "]"
    return repr(node)


def canonical_sha256(node):
    return hashlib.sha256(_canonical(node).encode()).hexdigest()


def migration_class(name):
    tree = ast.parse((MIGRATIONS / (name + ".py")).read_text())
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration")


def assignment(name, attribute):
    return next(
        n.value
        for n in migration_class(name).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == attribute for t in n.targets)
    )


def local_dependencies(name):
    result = []
    for dependency in assignment(name, "dependencies").elts:
        if isinstance(dependency, ast.Call):
            # Existing 0099's external auth dependency is not a Simulate edge.
            if ast.unparse(dependency) != "migrations.swappable_dependency(settings.AUTH_USER_MODEL)":
                raise AssertionError("Unexpected dynamic migration dependency")
            continue
        app, migration = ast.literal_eval(dependency)
        if app == "simulate":
            result.append(migration)
    return result


def migration_graph():
    return {p.stem: local_dependencies(p.stem) for p in MIGRATIONS.glob("[0-9]*.py")}


def ancestors(graph, name, active=None, cache=None):
    active = set() if active is None else active
    cache = {} if cache is None else cache
    if name in active:
        raise AssertionError("Simulate migration dependency cycle")
    if name in cache:
        return cache[name]
    active = active | {name}
    found = set(graph[name])
    for dependency in graph[name]:
        found.update(ancestors(graph, dependency, active, cache))
    cache[name] = found
    return found


def composed_merge_leaf(graph):
    """Select the terminal merge by graph ancestry, not a numbered filename."""
    merges = {name for name, dependencies in graph.items() if len(dependencies) > 1}
    terminal = {
        name for name in merges
        if not any(name in ancestors(graph, other) for other in merges - {name})
    }
    if len(terminal) != 1:
        raise AssertionError("The composed graph requires exactly one terminal merge")
    return terminal.pop()


class ApiKeyMigrationCompositionTests(unittest.TestCase):
    def _bridge_operations(self, operations, name):
        self.assertIsInstance(operations, ast.List)
        self.assertEqual(len(operations.elts), 1, "No operations outside the envelope")
        wrapper = operations.elts[0]
        self.assertIsInstance(wrapper, ast.Call)
        self.assertEqual(ast.unparse(wrapper.func), "SkipDatabaseIfLegacyApplied")
        self.assertEqual(wrapper.args, [])
        self.assertCountEqual([kw.arg for kw in wrapper.keywords], ["legacy", "operations"])
        arguments = {kw.arg: kw.value for kw in wrapper.keywords}
        self.assertEqual(ast.literal_eval(arguments["legacy"]), LEGACY_NAMES[name])
        self.assertIsInstance(arguments["operations"], ast.List)
        return arguments["operations"]

    def test_accepted_migrations_are_present_and_byte_identical(self):
        for name, expected in ACCEPTED_SHA256.items():
            path = MIGRATIONS / (name + ".py")
            with self.subTest(migration=name, check="bytes"):
                self.assertTrue(path.is_file(), name)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
            # Independent of the byte assertion: repinning a changed envelope
            # must not silently accept changed operations or helper bodies.
            with self.subTest(migration=name, check="pre_bridge_content"):
                tree = ast.parse(path.read_text())
                cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration")
                attributes = []
                for node in cls.body:
                    self.assertIsInstance(node, ast.Assign)
                    self.assertEqual(len(node.targets), 1)
                    self.assertIsInstance(node.targets[0], ast.Name)
                    attributes.append(node.targets[0].id)
                # The bridge adds NO class attributes: only the operations
                # envelope and its module import. In particular, no replaces.
                expected_attributes = {"dependencies", "operations"}
                if name == M2:
                    expected_attributes.add("atomic")
                self.assertCountEqual(attributes, expected_attributes)
                operation_assignment = next(n for n in cls.body if n.targets[0].id == "operations")
                inner = self._bridge_operations(operation_assignment.value, name)
                self.assertEqual(canonical_sha256(inner), ACCEPTED_OPERATIONS_SHA256[name])
                operation_assignment.value = inner
                bridge_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
                                  and n.module == "tfc.migration_bridge"]
                self.assertEqual(len(bridge_imports), 1)
                bridge_import = bridge_imports[0]
                self.assertEqual(bridge_import.level, 0)
                self.assertEqual([(a.name, a.asname) for a in bridge_import.names],
                                 [("SkipDatabaseIfLegacyApplied", None)])
                tree.body.remove(bridge_import)
                self.assertEqual(canonical_sha256(tree), ACCEPTED_SOURCE_SHA256[name])

    def test_encryption_precedes_legacy_column_removal(self):
        self.assertEqual(local_dependencies(M1), ["0101_agent_fix_candidate_override"])
        self.assertEqual(local_dependencies(M2), [M1])
        self.assertEqual(local_dependencies(M3), [M2])
        self.assertIs(ast.literal_eval(assignment(M2, "atomic")), False)

    def test_composed_graph_has_one_leaf(self):
        graph = migration_graph()
        depended_on = {dependency for deps in graph.values() for dependency in deps}
        leaves = set(graph) - depended_on
        self.assertEqual(len(leaves), 1, "Simulate must have exactly one leaf")
        (leaf,) = leaves
        # Migrations with real operations (for example 0109_agent_fix_model_reserved_usd)
        # may follow the composed merge. The merge stays empty and an ancestor of the one leaf.
        merge = composed_merge_leaf(graph)
        self.assertIn(merge, ancestors(graph, leaf) | {leaf})
        self.assertEqual(ancestors(graph, leaf), set(graph) - {leaf})
        self.assertEqual(ast.literal_eval(assignment(merge, "operations")), [])
        self.assertCountEqual(local_dependencies(CREDENTIALS_MERGE), [MERGE, ENGINE_NOTES])
        self.assertEqual(ast.literal_eval(assignment(CREDENTIALS_MERGE, "operations")), [])

    def test_merge_joins_both_branches_without_schema_or_data_operations(self):
        path = MIGRATIONS / (MERGE + ".py")
        self.assertTrue(path.is_file(), "The composed graph requires an explicit merge")
        self.assertCountEqual(local_dependencies(MERGE), [M3, LINEAGE])
        self.assertEqual(ast.literal_eval(assignment(MERGE, "operations")), [])
        self.assertEqual(
            {t.id for n in migration_class(MERGE).body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)},
            {"dependencies", "operations"},
        )

    def test_both_branches_complete_before_the_selected_leaf(self):
        graph = migration_graph()
        self.assertIn(MERGE, graph)
        self.assertTrue({M1, M2, M3, LINEAGE}.issubset(ancestors(graph, MERGE)))
        self.assertTrue({MERGE, ENGINE_NOTES, M1, M2, M3, LINEAGE}.issubset(ancestors(graph, composed_merge_leaf(graph))))

    def test_lineage_remains_independent_of_credential_storage(self):
        self.assertEqual(local_dependencies(LINEAGE), ["0101_agent_fix_candidate_override"])
        operations = self._bridge_operations(assignment(LINEAGE, "operations"), LINEAGE).elts
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(ast.unparse(operation.func), "migrations.AddField")
        arguments = {kw.arg: kw.value for kw in operation.keywords}
        self.assertEqual(ast.literal_eval(arguments["model_name"]), "agentpromptoptimiserrun")
        self.assertEqual(ast.literal_eval(arguments["name"]), "tenant_lineage")

    def test_all_local_dependencies_exist(self):
        graph = migration_graph()
        for name, dependencies in graph.items():
            with self.subTest(migration=name):
                self.assertTrue(set(dependencies).issubset(graph), dependencies)

    def test_composed_graph_is_acyclic(self):
        graph = migration_graph()
        for name in graph:
            with self.subTest(migration=name):
                ancestors(graph, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
