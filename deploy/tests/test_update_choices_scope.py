"""AST-only producer-scope regressions; not native SQL or release qualification.

Django 5.1's BaseDatabaseSchemaEditor.alter_field returns immediately when
_field_should_be_altered is false; choices is in Field.non_db_attrs. The producer
recognizes only the narrower exact, non-index/key CharField case, never an
arbitrary AlterField, a migration name, or a general repeated-table waiver.
"""

import ast
import copy
import unittest
from types import SimpleNamespace

from deploy.tests.test_update_usage_pending_scope import (
    CharField,
    State,
    migration_path,
    pending_plan,
    scope_environment,
)

SOURCE = ("tracer", "0026_alter_observationspan_observation_type")
TARGET = ("tracer", "0130_observationspan_internal_type")
TABLE = ("postgres", "public.tracer_observation_span")


def fixture():
    state = State()
    state.models[("tracer", "observationspan")] = SimpleNamespace(
        options={"db_table": "tracer_observation_span"}, fields={}
    )
    env = scope_environment(state)
    source = pending_plan([SOURCE], env)[0]
    source.operations[0].state_forwards("tracer", state)
    target = pending_plan([TARGET], env)[0]
    return state, env, target, target.operations[0]


class ChoicesScopeTests(unittest.TestCase):
    def test_real_choices_change_keeps_state_and_plan_without_physical_scope(self):
        state, env, migration, op = fixture()
        before = copy.deepcopy(state.models[("tracer", "observationspan")].fields)
        old = before["observation_type"].deconstruct()
        new = op.field.deconstruct()
        self.assertNotEqual(old[3]["choices"], new[3]["choices"])
        self.assertEqual(
            {k: v for k, v in old[3].items() if k != "choices"},
            {k: v for k, v in new[3].items() if k != "choices"},
        )
        self.assertEqual(env["touched"](op, migration, state), set())
        self.assertEqual(state.models[("tracer", "observationspan")].fields, before)
        op.state_forwards("tracer", state)
        self.assertEqual(
            state.models[("tracer", "observationspan")]
            .fields["observation_type"]
            .deconstruct(),
            new,
        )
        self.assertEqual((migration.app_label, migration.name), TARGET)
        self.assertEqual(len(migration.operations), 1)

    def test_any_other_constructor_change_retains_table(self):
        for key, value in (
            ("max_length", 21),
            ("null", True),
            ("default", "internal"),
            ("db_column", "new_column"),
            ("db_collation", "C"),
            ("db_comment", "changed"),
            ("help_text", "changed"),
        ):
            with self.subTest(key=key):
                state, env, migration, op = fixture()
                op.field.options[key] = ast.dump(ast.Constant(value))
                self.assertEqual(env["touched"](op, migration, state), {TABLE})
        state, env, migration, op = fixture()
        op.field.arguments.append(ast.dump(ast.Constant("verbose name")))
        self.assertEqual(env["touched"](op, migration, state), {TABLE})

    def test_index_and_key_fields_remain_conservative_on_either_side(self):
        for side in ("before", "after"):
            for flag in ("unique", "primary_key", "db_index"):
                with self.subTest(side=side, flag=flag):
                    state, env, migration, op = fixture()
                    field = (
                        op.field
                        if side == "after"
                        else state.models[("tracer", "observationspan")].fields[
                            "observation_type"
                        ]
                    )
                    setattr(field, flag, True)
                    self.assertEqual(env["touched"](op, migration, state), {TABLE})

    def test_custom_field_with_identical_deconstruction_is_not_exempt(self):
        class CustomCharField(CharField):
            pass

        for side in ("before", "after"):
            with self.subTest(side=side):
                state, env, migration, op = fixture()
                if side == "after":
                    op.field = CustomCharField(**vars(op.field))
                else:
                    fields = state.models[("tracer", "observationspan")].fields
                    fields["observation_type"] = CustomCharField(
                        **vars(fields["observation_type"])
                    )
                self.assertEqual(env["touched"](op, migration, state), {TABLE})

    def test_custom_operation_is_still_refused(self):
        state, env, migration, op = fixture()
        custom = type("AlterField", (type(op),), {"__module__": "custom.migrations"})
        with self.assertRaisesRegex(
            AssertionError, "custom_operation_table_scope_unavailable"
        ):
            env["touched"](custom(op.node), migration, state)

    def test_unknown_field_semantics_do_not_get_choices_exemption(self):
        state, env, migration, op = fixture()
        op.field.non_db_attrs = ()
        self.assertEqual(env["touched"](op, migration, state), {TABLE})

    def test_new_or_removed_field_is_not_a_choices_only_alteration(self):
        state, env, migration, op = fixture()
        # AddField with the same field bytes must retain its physical scope.
        add = env["migrations"].AddField(op.node)
        self.assertEqual(env["touched"](add, migration, state), {TABLE})
        del state.models[("tracer", "observationspan")].fields["observation_type"]
        self.assertEqual(env["touched"](op, migration, state), {TABLE})

    def test_the_real_target_still_declares_only_the_choices_delta(self):
        # Read only AST data: never import/execute application migration code.
        tree = ast.parse(migration_path(*TARGET).read_text())
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Migration"
        )
        operations = next(
            n.value
            for n in cls.body
            if isinstance(n, ast.Assign) and n.targets[0].id == "operations"
        )
        self.assertEqual(len(operations.elts), 1)
        self.assertEqual(operations.elts[0].func.attr, "AlterField")
        state, env, migration, op = fixture()
        self.assertEqual(env["touched"](op, migration, state), set())
