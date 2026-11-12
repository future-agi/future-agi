"""Dependency-free ownership regressions; also collected by the database venue."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "services/hard_erase_graph.py"
SPEC = importlib.util.spec_from_file_location("erase_graph_under_test", SOURCE)
GRAPH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GRAPH)


def scalar(name, *, primary_key=False):
    return SimpleNamespace(
        name=name, attname=name, primary_key=primary_key, is_relation=False
    )


def relation(name, target, *, enforced=True):
    return SimpleNamespace(
        name=name,
        attname=name + "_id",
        primary_key=False,
        is_relation=True,
        many_to_one=True,
        one_to_one=False,
        remote_field=SimpleNamespace(model=target),
        target_field=SimpleNamespace(attname="id"),
        db_constraint=enforced,
    )


def model(name, *fields):
    primary_key = scalar("id", primary_key=True)
    return type(
        name.rsplit(".", 1)[-1],
        (),
        {"_meta": SimpleNamespace(
            label_lower=name,
            pk=primary_key,
            concrete_fields=[primary_key, *fields],
        )},
    )


def row(kind, pk, **values):
    obj = kind()
    obj.pk = obj.id = pk
    for field in kind._meta.concrete_fields[1:]:
        setattr(obj, field.attname, values.get(field.attname))
    return obj


class UnconstrainedOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.org = model("accounts.organization")
        self.source = model("example.source")
        self.calls = []
        self.available = []

    def read(self, target, field, values):
        self.calls.append((target, field, set(values)))
        target_label = target if isinstance(target, str) else target._meta.label_lower
        return [
            obj for obj in self.available
            if obj._meta.label_lower == target_label
            and getattr(obj, field) in values
        ]

    def resolve(self, rows):
        try:
            return GRAPH.OwnershipGraph(rows, self.read)
        except GRAPH.OwnershipUnresolved as exc:
            self.fail(f"A resolved ownership path must survive an absent soft link: {exc}")

    def assert_unresolved(self, rows):
        with self.assertRaisesRegex(
            GRAPH.OwnershipUnresolved, "^an ownership reference cannot be resolved$"
        ):
            GRAPH.OwnershipGraph(rows, self.read)

    def test_missing_soft_link_with_enforced_chain(self):
        queue = model("example.queue", relation("organization", self.org))
        item = model(
            "example.item", relation("queue", queue),
            relation("source", self.source, enforced=False),
        )
        obj = row(item, "item", queue_id="queue", source_id="absent")
        self.available = [row(queue, "queue", organization_id="one"), row(self.org, "one")]
        graph = self.resolve([obj])
        self.assertEqual(graph.scopes(obj), {"one"})

    def test_missing_soft_link_with_direct_organization(self):
        kind = model(
            "example.item", scalar("org_id"),
            relation("source", self.source, enforced=False),
        )
        obj = row(kind, "item", org_id="one", source_id="absent")
        self.assertEqual(self.resolve([obj]).scopes(obj), {"one"})

    def test_missing_soft_link_on_scoped_ancestor(self):
        parent = model(
            "example.parent", relation("organization", self.org),
            relation("source", self.source, enforced=False),
        )
        child = model("example.child", relation("parent", parent))
        obj = row(child, "child", parent_id="parent")
        self.available = [
            row(parent, "parent", organization_id="one", source_id="absent"),
            row(self.org, "one"),
        ]
        self.assertEqual(self.resolve([obj]).scopes(obj), {"one"})

    def test_unscoped_ancestor_does_not_hide_child_organization(self):
        parent = model("example.parent", relation("source", self.source, enforced=False))
        child = model("example.child", relation("parent", parent), scalar("organization"))
        obj = row(child, "child", parent_id="parent", organization="one")
        ancestor = row(parent, "parent", source_id="absent")
        self.available = [ancestor]
        graph = self.resolve(iter([obj]))
        self.assertEqual(graph.scopes(obj), {"one"})
        self.assertEqual(graph.scopes(ancestor), set())

    def test_missing_soft_link_as_only_path_refuses(self):
        kind = model("example.note", relation("source", self.source, enforced=False))
        self.assert_unresolved([row(kind, "note", source_id="absent")])

    def test_missing_soft_link_on_only_ancestor_path_refuses(self):
        parent = model("example.parent", relation("source", self.source, enforced=False))
        child = model("example.child", relation("parent", parent))
        self.available = [row(parent, "parent", source_id="absent")]
        self.assert_unresolved([row(child, "child", parent_id="parent")])

    def test_supplied_unscoped_ancestor_still_refuses(self):
        parent = model("example.parent", relation("source", self.source, enforced=False))
        child = model("example.child", relation("parent", parent), scalar("org"))
        ancestor = row(parent, "parent", source_id="absent")
        obj = row(child, "child", parent_id="parent", org="one")
        self.assert_unresolved([obj, ancestor])

    def test_unrelated_scoped_root_cannot_cover_unscoped_row(self):
        kind = model("example.note", relation("source", self.source, enforced=False))
        self.assert_unresolved([
            row(self.org, "one"), row(kind, "note", source_id="absent"),
        ])

    def test_absent_enforced_link_refuses_even_with_scope(self):
        kind = model("example.item", relation("source", self.source), scalar("org"))
        self.assert_unresolved([row(kind, "item", source_id="absent", org="one")])

    def test_legacy_field_without_constraint_attribute_is_enforced(self):
        field = relation("source", self.source)
        del field.db_constraint
        kind = model("example.item", field, scalar("org"))
        self.assert_unresolved([row(kind, "item", source_id="absent", org="one")])

    def test_same_missing_binding_enforced_and_soft_still_refuses(self):
        kind = model(
            "example.item", scalar("org"),
            relation("optional", self.source, enforced=False),
            relation("required", self.source),
        )
        self.assert_unresolved([
            row(kind, "item", org="one", optional_id="absent", required_id="absent"),
        ])

    def test_scalar_workspace_remains_enforced(self):
        kind = model("example.grant", scalar("org"), scalar("workspace"))
        self.assert_unresolved([row(kind, "grant", org="one", workspace="absent")])

    def test_present_soft_link_preserves_foreign_organization_scope(self):
        source = model("example.source", relation("organization", self.org))
        kind = model(
            "example.item", scalar("org"), relation("source", source, enforced=False)
        )
        obj = row(kind, "item", org="one", source_id="source")
        self.available = [row(source, "source", organization_id="two"), row(self.org, "two")]
        self.assertEqual(self.resolve([obj]).scopes(obj), {"one", "two"})

    def test_foreign_scope_survives_another_missing_soft_link(self):
        source = model(
            "example.source", scalar("org"),
            relation("ancestor", self.source, enforced=False),
        )
        kind = model(
            "example.item", scalar("org"), relation("source", source, enforced=False)
        )
        obj = row(kind, "item", org="one", source_id="source")
        self.available = [row(source, "source", org="two", ancestor_id="absent")]
        self.assertEqual(self.resolve([obj]).scopes(obj), {"one", "two"})

    def test_no_links_still_has_empty_scope(self):
        obj = row(model("example.unowned"), "unowned")
        self.assertEqual(self.resolve([obj]).scopes(obj), set())
        self.assertEqual(self.calls, [])

    def test_user_organization_and_user_edges_remain_excluded(self):
        user = model("accounts.user", relation("organization", self.org))
        kind = model("example.item", relation("created_by", user))
        person = row(user, "user", organization_id="absent")
        obj = row(kind, "item", created_by_id="absent")
        graph = self.resolve([person, obj])
        self.assertEqual(graph.scopes(person), set())
        self.assertEqual(graph.scopes(obj), set())
        self.assertEqual(self.calls, [])

    def test_null_soft_link_is_not_an_unresolved_reference(self):
        kind = model("example.item", relation("source", self.source, enforced=False))
        obj = row(kind, "item")
        self.assertEqual(self.resolve([obj]).scopes(obj), set())
        self.assertEqual(self.calls, [])

    def cycle(self, scope):
        kind = model(
            "example.cycle", scalar("org"),
            relation("source", self.source, enforced=False),
        )
        kind._meta.concrete_fields.append(relation("next", kind))
        first = row(kind, "a", next_id="b")
        second = row(kind, "b", next_id="a", source_id="absent", org=scope)
        self.available = [first, second]
        return first, second

    def test_missing_soft_link_in_unscoped_cycle_refuses(self):
        first, _ = self.cycle(None)
        self.assert_unresolved([first])

    def test_missing_soft_link_in_scoped_cycle_propagates(self):
        first, second = self.cycle("one")
        graph = self.resolve([first])
        self.assertEqual(graph.scopes(first), {"one"})
        self.assertEqual(graph.scopes(second), {"one"})

    def test_enforced_links_still_batch_without_constraint_attribute(self):
        field = relation("organization", self.org)
        del field.db_constraint
        kind = model("example.item", field)
        rows = [row(kind, i, organization_id="one") for i in range(100)]
        self.available = [row(self.org, "one")]
        graph = self.resolve(rows)
        self.assertEqual([graph.scopes(obj) for obj in rows], [{"one"}] * 100)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(graph.nodes_read, 101)

    def test_node_budget_still_refuses(self):
        with patch.object(GRAPH, "MAX_NODES", 1):
            with self.assertRaisesRegex(GRAPH.OwnershipUnresolved, "ownership node budget exceeded"):
                GRAPH.OwnershipGraph([row(self.org, "one"), row(self.org, "two")], self.read)

    def test_read_budget_still_refuses_for_soft_links(self):
        kind = model(
            "example.item", scalar("org"),
            relation("source", self.source, enforced=False),
        )
        with patch.object(GRAPH, "MAX_READS", 0):
            with self.assertRaisesRegex(GRAPH.OwnershipUnresolved, "ownership read budget exceeded"):
                GRAPH.OwnershipGraph([row(kind, "item", org="one", source_id="absent")], self.read)

    def test_depth_budget_still_refuses(self):
        kind = model("example.item", relation("organization", self.org))
        self.available = [row(self.org, "one")]
        with patch.object(GRAPH, "MAX_ROUNDS", 1):
            with self.assertRaisesRegex(GRAPH.OwnershipUnresolved, "ownership depth budget exceeded"):
                GRAPH.OwnershipGraph([row(kind, "item", organization_id="one")], self.read)


if __name__ == "__main__":
    unittest.main()
