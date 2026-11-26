from unittest import TestCase

from django.db import migrations, models
from django.db.migrations.loader import MigrationLoader


class PollStateMigrationTests(TestCase):
    def test_tracer_migrations_have_one_leaf(self) -> None:
        loader = MigrationLoader(None)

        self.assertNotIn("tracer", loader.detect_conflicts())

    def test_poll_state_is_added_once_after_eval_logger_indexes(self) -> None:
        loader = MigrationLoader(None)
        target = ("tracer", "0098_observabilityprovider_poll_state")
        plan = loader.graph.forwards_plan(target)
        additions = [
            operation
            for key in plan
            if key[0] == "tracer"
            for operation in loader.disk_migrations[key].operations
            if isinstance(operation, migrations.AddField)
            and operation.model_name == "observabilityprovider"
            and operation.name == "poll_state"
        ]

        self.assertEqual(len(additions), 1)
        self.assertIsInstance(additions[0].field, models.JSONField)
        self.assertIs(additions[0].field.default, dict)
        self.assertLess(
            plan.index(("tracer", "0097_eval_logger_task_created_idx")),
            plan.index(target),
        )
