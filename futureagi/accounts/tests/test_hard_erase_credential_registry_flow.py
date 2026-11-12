"""Offline late-registration regressions: real installer, census and SQL policy.

Only Django's registry/signal/connection plumbing is doubled. The full guard
module executes, including signal registration; no protective decision is mocked.
Real ModelBase/class_prepared coverage lives in test_hard_erase_r2f_db.py.
"""

import ast
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services"
POLICY = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
META = runpy.run_path(str(TESTS / "test_hard_erase_r2f_flow.py"))
EXTERNAL_TABLE = "tracer_external_eval_config"


class Refused(Exception):
    pass


class PreparedSignal:
    def __init__(self):
        self.receivers = {}

    def connect(self, receiver, *, weak=True, dispatch_uid=None, **kwargs):
        self.receivers.setdefault(dispatch_uid or receiver, receiver)

    def send(self, sender):
        for receiver in tuple(self.receivers.values()):
            receiver(sender=sender, signal=self)


class Registry:
    def __init__(self):
        self.models = []
        self.prepared = PreparedSignal()

    def get_models(self, *, include_auto_created=False):
        return tuple(
            model for model in self.models
            if include_auto_created or not model._meta.auto_created
        )

    def register(self, model):
        # ModelBase sends class_prepared BEFORE Apps.register_model. A callback
        # that merely rescans get_models() therefore still misses this model.
        self.prepared.send(sender=model)
        self.models.append(model)


def model(registry, name, fields=(), table=None, module="late.models"):
    result = META["fake_model"](
        module, name, [META["field"](name) for name in fields], table
    )
    result._meta.apps = registry
    result._meta.local_many_to_many = []
    return result


def harness(registry=None):
    registry = registry or Registry()

    class Connection:
        def __init__(self, alias="default"):
            self.alias = alias
            self.in_atomic_block = False
            self.execute_wrappers = []

    class Connections(dict):
        def all(self, **kwargs):
            return list(self.values())

    connection = Connection()

    def refuse(reason):
        raise Refused(reason)

    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module and node.module.startswith(("django.", "accounts.")):
                return ast.copy_location(ast.Pass(), node)
            return node

    ns = {
        "__name__": "credential_registry_guard_under_test",
        "apps": registry,
        "class_prepared": registry.prepared,
        "connections": Connections(default=connection),
        "BaseDatabaseWrapper": Connection,
        "models": SimpleNamespace(QuerySet=type("QuerySet", (), {"_raw_delete": Mock()})),
        "CursorWrapper": type("CursorWrapper", (), {}),
        "MigrationExecutor": type("MigrationExecutor", (), {}),
        "compiler": SimpleNamespace(
            SQLCompiler=type("SQLCompiler", (), {"execute_sql": lambda self: None}),
            SQLInsertCompiler=type("SQLInsertCompiler", (), {"execute_sql": lambda self: None}),
        ),
        "_refuse": refuse,
        "protected_models": POLICY["protected_models"],
        "protected_mutations": POLICY["protected_mutations"],
        "_lex": POLICY["_lex"],
        "migration_sql_allowed": lambda connection: False,
        "membership_backfill_kind": lambda sql: None,
        "exact_sql_exception": lambda sql, many, connection: False,
        "install_cursor_guard": Mock(),
        "install_migration_guard": Mock(),
    }
    source = SERVICES / "hard_erase_sql.py"
    tree = Imports().visit(ast.parse(source.read_text()))
    exec(compile(ast.fix_missing_locations(tree), str(source), "exec"), ns)
    ns["install_sql_guard"]()
    return ns, registry, connection


class LateCredentialRegistryTests(unittest.TestCase):
    def assert_raw_refused(self, ns, connection, table):
        for statement in (
            f'DELETE FROM "{table}"',
            f'UPDATE "{table}" SET id=id',
            f'INSERT INTO "{table}" (id) VALUES (1)',
            f'TRUNCATE "{table}"',
            f'COPY "{table}" FROM STDIN',
            f'SELECT * FROM "{table}" FOR UPDATE',
        ):
            for many in (False, True):
                with self.subTest(table=table, statement=statement, many=many):
                    driver = Mock()
                    with self.assertRaisesRegex(Refused, "raw SQL cannot mutate"):
                        ns["guard_execute"](
                            driver, statement, [], many, {"connection": connection}
                        )
                    driver.assert_not_called()

    def test_external_eval_credentials_have_positive_inventory_not_exclusion(self):
        rows = json.loads((SERVICES / "hard_erase_credential_inventory.json").read_text())
        rows = [row for row in rows if row["table"] == EXTERNAL_TABLE]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["module"], "tracer.models.external_eval_config")
        self.assertEqual(rows[0]["model"], "ExternalEvalConfig")
        self.assertIn("credentials", rows[0]["fields"])
        self.assertTrue(rows[0]["review"].strip())
        self.assertNotIn(EXTERNAL_TABLE, POLICY["CREDENTIAL_EXCLUSIONS"])

    def test_external_eval_registered_after_install_refuses_without_reinstall(self):
        ns, registry, connection = harness()
        source = TESTS.parents[1] / "tracer/models/external_eval_config.py"
        cls = next(node for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.ClassDef) and node.name == "ExternalEvalConfig")
        fields = [target.id for node in cls.body if isinstance(node, ast.Assign)
                  for target in node.targets if isinstance(target, ast.Name)]
        self.assertIn("credentials", fields)
        external = model(registry, "ExternalEvalConfig", fields, EXTERNAL_TABLE,
                         "tracer.models.external_eval_config")
        self.assertNotIn(EXTERNAL_TABLE, ns["_TABLES"])
        registry.register(external)
        self.assert_raw_refused(ns, connection, EXTERNAL_TABLE)
        self.assertIn(EXTERNAL_TABLE, ns["_TABLES"])

    def test_uninventoried_late_model_is_protected_by_its_fields(self):
        ns, registry, connection = harness()
        late = model(registry, "Plain", ["access_token"], "late_plain")
        self.assertNotIn((late.__module__, late.__name__), POLICY["INVENTORIED_MODELS"])
        registry.register(late)
        self.assert_raw_refused(ns, connection, "late_plain")

    def test_through_prepared_before_inherited_credential_field_is_not_lost(self):
        ns, registry, connection = harness()
        owner = model(registry, "Plain", [], "late_parent")
        through = model(registry, "Plain_items", [], "late_relation")
        through._meta.auto_created = owner
        owner._meta.local_many_to_many.append(
            SimpleNamespace(remote_field=SimpleNamespace(through=through))
        )
        # Django can prepare the auto-created through model before copying an
        # abstract base's concrete fields onto the owning model.
        registry.register(through)
        owner._meta.concrete_fields.append(META["field"]("encrypted_payload"))
        registry.register(owner)
        for table in ("late_parent", "late_relation"):
            self.assertIn(table, ns["_TABLES"])
            self.assert_raw_refused(ns, connection, table)

    def test_through_prepared_after_protected_owner_is_included(self):
        registry = Registry()
        owner = model(registry, "Plain", ["credentials"], "initial_parent")
        registry.models.append(owner)
        ns, registry, connection = harness(registry)
        through = model(registry, "Plain_items", [], "later_relation")
        through._meta.auto_created = owner
        registry.register(through)
        for table in ("initial_parent", "later_relation"):
            self.assertIn(table, ns["_TABLES"])
            self.assert_raw_refused(ns, connection, table)

    def test_reads_and_ordinary_models_remain_allowed(self):
        ns, registry, connection = harness()
        registry.register(model(registry, "Plain", ["credentials"], "late_protected"))
        ordinary = model(registry, "Measurement", ["value"], "late_measurement")
        relation = model(registry, "Measurement_items", [], "late_measurement_items")
        relation._meta.auto_created = ordinary
        registry.register(relation)
        registry.register(ordinary)
        for statement in ("SELECT * FROM late_protected", "DELETE FROM late_measurement",
                          "DELETE FROM late_measurement_items"):
            driver = Mock(return_value="allowed")
            self.assertEqual(ns["guard_execute"](
                driver, statement, [], False, {"connection": connection}), "allowed")
            driver.assert_called_once()
        self.assertIn("late_protected", ns["_TABLES"])
        self.assertNotIn("late_measurement", ns["_TABLES"])
        self.assertNotIn("late_measurement_items", ns["_TABLES"])

    def test_historical_proxy_and_abstract_models_do_not_pollute_live_census(self):
        ns, registry, _ = harness()
        historical = model(Registry(), "OldCredential", ["secret"], "old_table")
        proxy = model(registry, "ProxyCredential", ["secret"], "proxy_table")
        proxy._meta.proxy = True
        abstract = model(registry, "AbstractCredential", ["secret"], "abstract_table")
        abstract._meta.abstract = True
        for candidate in (historical, proxy, abstract):
            registry.register(candidate)
            self.assertNotIn(candidate._meta.db_table, ns["_TABLES"])

    def test_install_is_idempotent_and_new_connections_keep_late_protection(self):
        ns, registry, connection = harness()
        registry.register(model(registry, "Plain", ["secret"], "late_secret"))
        ns["install_sql_guard"]()
        self.assertEqual(len(registry.prepared.receivers), 1)
        self.assertEqual(connection.execute_wrappers, [ns["guard_execute"]])
        fresh = ns["BaseDatabaseWrapper"]("other")
        self.assertEqual(fresh.execute_wrappers, [ns["guard_execute"]])
        self.assert_raw_refused(ns, fresh, "late_secret")

    def test_late_protection_keeps_transaction_connection_and_kind_authority_checks(self):
        ns, registry, connection = harness()
        registry.register(model(registry, "Plain", ["secret"], "late_secret"))
        statement = "DELETE FROM late_secret"
        driver = Mock(return_value="erased")
        for atomic, owner, kind in (
            (False, connection, "erase"),
            (True, object(), "erase"),
            (True, connection, "seal"),
            (True, connection, "write"),
        ):
            connection.in_atomic_block = atomic
            token = ns["_SQL_AUTH"].set((owner, kind))
            try:
                with self.subTest(atomic=atomic, kind=kind), self.assertRaises(Refused):
                    ns["guard_execute"](driver, statement, [], False, {"connection": connection})
                driver.assert_not_called()
            finally:
                ns["_SQL_AUTH"].reset(token)
        connection.in_atomic_block = True
        with ns["sql_authority"]("default", "erase"):
            self.assertEqual(ns["guard_execute"](
                driver, statement, [], False, {"connection": connection}), "erased")
        driver.assert_called_once()
        self.assertIsNone(ns["_SQL_AUTH"].get())


if __name__ == "__main__":
    unittest.main()
