"""Columns a previous build does not know keep a database default.

Migrations run while pods of the previous release still serve, and a Helm
rollback never reverts them. Those pods insert rows without the columns that
simulate 0103, 0106 and 0115 and tracer 0123 added, so each column needs a
database default, not only Django's Python one.
"""

import pytest
from django.db import connection

from simulate.models.agent_setup import AgentVersionSetup
from simulate.models.hosted_harness import HostedHarnessJob
from tracer.models.monitor import UserAlertMonitor

CASES = [
    (AgentVersionSetup, "contract_object_key", ""),
    (AgentVersionSetup, "origin", "build"),
    (HostedHarnessJob, "cancel_confirmed_by", ""),
    (UserAlertMonitor, "alert_when", "point"),
]


def _column(table, column):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s "
            "AND column_name = %s",
            [table, column],
        )
        return cursor.fetchone()


@pytest.mark.django_db
@pytest.mark.parametrize(("model", "name", "value"), CASES)
def test_column_keeps_a_database_default_equal_to_its_default(model, name, value):
    field = model._meta.get_field(name)
    # The database default must match what the current code would write.
    assert (field.default, field.db_default) == (value, value)

    default, nullable = _column(model._meta.db_table, field.column)
    assert nullable == "NO"
    # PostgreSQL reports a text literal default as e.g. 'point'::character varying.
    assert default is not None
    assert default.split("::")[0] == f"'{value}'"
