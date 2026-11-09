"""Offline structural regressions for the composed Simulate migration graph.

Run directly with python3 -I -B. No Django settings, database, or credentials
are loaded. These checks do not replace MigrationExecutor/endpoint proof.
"""

import ast
import hashlib
import unittest
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
M1 = "0100_agent_definition_encrypted_api_key"
M2 = "0101_encrypt_agent_definition_api_key"
M3 = "0102_remove_agent_definition_api_key"
LINEAGE = "0100_prompt_optimizer_tenant_lineage"
MERGE = "0103_merge_api_key_at_rest_tenant_lineage"
ACCEPTED_SHA256 = {
    M1: "06efe60086f0303c552fff09fee0284d6f4a40488c6a2acb5e50f2971b6386d6",
    # FWD-P1 1124fdfad4: accepted 0101 persistent-key preflight revision.
    M2: "234776c8fe4a6a462b6511aa51a0b76d46c639573a28728f7306bccd130a524e",
    M3: "41201afac0cdba92b89813493f964f0130320156154f8c9cb8bb5017df4d8c0b",
    LINEAGE: "1341338aca87c1c01e9b9fea36a103338b7948661369d4c8874af8486614f28e",
}


def migration_class(name):
    tree = ast.parse((MIGRATIONS / (name + ".py")).read_text())
    return next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Migration"
    )


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
            if (
                ast.unparse(dependency)
                != "migrations.swappable_dependency(settings.AUTH_USER_MODEL)"
            ):
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


class ApiKeyMigrationCompositionTests(unittest.TestCase):
    def test_accepted_migrations_are_present_and_byte_identical(self):
        for name, expected in ACCEPTED_SHA256.items():
            with self.subTest(migration=name):
                path = MIGRATIONS / (name + ".py")
                self.assertTrue(path.is_file(), name)
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), expected
                )

    def test_encryption_precedes_legacy_column_removal(self):
        self.assertEqual(local_dependencies(M1), ["0099_harness_versioning"])
        self.assertEqual(local_dependencies(M2), [M1])
        self.assertEqual(local_dependencies(M3), [M2])
        self.assertIs(ast.literal_eval(assignment(M2, "atomic")), False)

    def test_composed_graph_has_one_leaf(self):
        graph = migration_graph()
        depended_on = {dependency for deps in graph.values() for dependency in deps}
        leaves = set(graph) - depended_on
        self.assertEqual(len(leaves), 1, leaves)
        # Later migrations may extend the graph; the API-key merge must stay on
        # the path to the single leaf.
        (leaf,) = leaves
        self.assertTrue(leaf == MERGE or MERGE in ancestors(graph, leaf), leaf)

    def test_merge_joins_both_branches_without_schema_or_data_operations(self):
        path = MIGRATIONS / (MERGE + ".py")
        self.assertTrue(path.is_file(), "The composed graph requires an explicit merge")
        self.assertCountEqual(local_dependencies(MERGE), [M3, LINEAGE])
        self.assertEqual(ast.literal_eval(assignment(MERGE, "operations")), [])
        self.assertEqual(
            {
                t.id
                for n in migration_class(MERGE).body
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name)
            },
            {"dependencies", "operations"},
        )

    def test_both_branches_complete_before_the_selected_leaf(self):
        graph = migration_graph()
        self.assertIn(MERGE, graph)
        self.assertTrue({M1, M2, M3, LINEAGE}.issubset(ancestors(graph, MERGE)))

    def test_lineage_remains_independent_of_credential_storage(self):
        self.assertEqual(local_dependencies(LINEAGE), ["0099_harness_versioning"])
        operations = assignment(LINEAGE, "operations").elts
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(ast.unparse(operation.func), "migrations.AddField")
        arguments = {kw.arg: kw.value for kw in operation.keywords}
        self.assertEqual(
            ast.literal_eval(arguments["model_name"]), "agentpromptoptimiserrun"
        )
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
