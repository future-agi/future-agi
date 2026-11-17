"""Offline execution of boundary control flow, without importing the Django app.

AST-extract the actual functions; stand-ins represent Collector/transactions.
This verifies orchestration, NOT database locking or receiver behavior.
"""

import ast
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / "services" / "hard_erase.py"


class Refused(Exception):
    pass


class Model:
    _meta = SimpleNamespace(
        label_lower="accounts.user", pk=SimpleNamespace(attname="pk")
    )
    pk = "selected"


class QuerySet:
    model = Model


def load_functions(**overrides):
    tree = ast.parse(SOURCE.read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    namespace = {
        "contextmanager": contextmanager,
        "HardEraseRefused": Refused,
        "models": SimpleNamespace(Model=Model, QuerySet=QuerySet),
        "DEFAULT_DB_ALIAS": "default",
        "_IDENTITY": {"accounts.user", "accounts.organization"},
    }
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    namespace.update(overrides)
    return namespace


class HardEraseFlow(unittest.TestCase):
    def test_refusal_never_enters_original_delete(self):
        original = Mock()

        @contextmanager
        def refused(*args, **kwargs):
            raise Refused("protected")
            yield  # pragma: no cover

        ns = load_functions(_original_delete=original, locked_preflight=refused)
        collector = SimpleNamespace(
            origin=Model(),
            using="default",
            data={Model: [Model()]},
            _hard_erase_roots=[Model()],
        )
        with self.assertRaises(Refused):
            ns["guarded_delete"](collector)
        original.assert_not_called()

    def test_original_delete_is_inside_preflight_transaction(self):
        events = []
        checked = object()

        @contextmanager
        def preflight(*args, **kwargs):
            events.append("locked-and-checked")
            yield checked
            events.append("transaction-end")

        def original(collector):
            self.assertIs(collector, checked)
            events.append("receivers-and-delete")
            return (1, {})

        ns = load_functions(_original_delete=original, locked_preflight=preflight)
        origin = Model()
        c = SimpleNamespace(
            origin=origin,
            using="default",
            data={Model: [origin]},
            _hard_erase_roots=[origin],
        )
        self.assertEqual(ns["guarded_delete"](c), (1, {}))
        self.assertEqual(
            events, ["locked-and-checked", "receivers-and-delete", "transaction-end"]
        )
        self.assertIsNone(origin.pk)

    def test_nonidentity_deletion_remains_available(self):
        original = Mock(return_value=(1, {}))
        preflight = Mock(
            side_effect=AssertionError("must not guard normal membership deletion")
        )
        ns = load_functions(_original_delete=original, locked_preflight=preflight)
        c = SimpleNamespace(data={}, fast_deletes=[])
        self.assertEqual(ns["guarded_delete"](c), (1, {}))
        original.assert_called_once_with(c)

    def test_unqualified_alias_fails_before_any_database_or_receiver(self):
        ns = load_functions()
        for alias in ("replica", "default_direct", "unknown"):
            with self.assertRaises(Refused):
                with ns["locked_preflight"]([], using=alias):
                    self.fail("unqualified alias yielded")

    def test_root_snapshot_does_not_expand_with_mutable_queryset(self):
        roots = [Model()]
        original = Mock()
        ns = load_functions(_original_collect=original)
        collector = SimpleNamespace(origin=QuerySet())
        ns["remember_roots"](collector, roots)
        roots.append(Model())
        self.assertEqual(len(collector._hard_erase_roots), 1)
        self.assertEqual(collector._hard_erase_collect_depth, 0)

    def test_unknown_sensitive_collector_fails_closed(self):
        original = Mock()
        ns = load_functions(_original_delete=original)
        c = SimpleNamespace(origin=None, data={Model: [Model()]})
        with self.assertRaises(Refused):
            ns["guarded_delete"](c)
        original.assert_not_called()

    def test_row_lock_is_limited_to_sorted_closure_rows(self):
        a = type("A", (), {"_meta": SimpleNamespace(db_table="a")})
        z = type("Z", (), {"_meta": SimpleNamespace(db_table="z")})
        rows = [
            SimpleNamespace(pk=4, _meta=SimpleNamespace(concrete_model=z)),
            SimpleNamespace(pk=2, _meta=SimpleNamespace(concrete_model=a)),
        ]
        query = Mock()
        query.filter.return_value.order_by.return_value.select_for_update.return_value.values_list.return_value = []
        factory = Mock(return_value=query)
        ns = load_functions(
            models=SimpleNamespace(QuerySet=factory), _rows=lambda c: rows
        )
        ns["_lock_rows"](SimpleNamespace(using="default"))
        self.assertEqual(
            [call.kwargs["model"] for call in factory.call_args_list], [a, z]
        )
        self.assertEqual(
            [call.kwargs["pk__in"] for call in query.filter.call_args_list], [{2}, {4}]
        )
        query.filter.return_value.order_by.assert_called_with("pk")


if __name__ == "__main__":
    unittest.main()
