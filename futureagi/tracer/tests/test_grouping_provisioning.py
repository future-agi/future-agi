from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class GroupingProvisioningTests(SimpleTestCase):
    def test_default_prints_ddl_without_connecting(self):
        output = StringIO()
        with patch(
            "tracer.management.commands.provision_grouping_features.ClickHouseClient"
        ) as client:
            call_command("provision_grouping_features", stdout=output)
        client.assert_not_called()
        self.assertIn("f6_grouping_features", output.getvalue())
        self.assertIn("f6_grouping_buckets", output.getvalue())

    def test_explicit_apply_uses_only_two_non_destructive_create_statements(self):
        with patch(
            "tracer.management.commands.provision_grouping_features.ClickHouseClient"
        ) as client:
            call_command("provision_grouping_features", "--apply", stdout=StringIO())
        self.assertEqual(client.return_value.execute.call_count, 2)
        for call in client.return_value.execute.call_args_list:
            self.assertTrue(call.args[0].startswith("CREATE TABLE IF NOT EXISTS"))
            self.assertNotIn("DROP", call.args[0])
