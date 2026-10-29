import importlib
import re
from pathlib import Path

import pytest
from django.db import migrations

from simulate.models.agent_setup import (
    AgentBehaviourUnit,
    AgentTelephony,
    AgentTool,
    AgentVersionSetup,
)
from tracer.services.clickhouse.oss_cdc_bootstrap import LANDING, landing_tables

MIGRATION = importlib.import_module("simulate.migrations.0098_agent_version_setup")


def test_migration_only_creates_the_four_tables():
    operations = MIGRATION.Migration.operations

    assert all(isinstance(operation, migrations.CreateModel) for operation in operations)
    assert sorted(operation.name for operation in operations) == [
        "AgentBehaviourUnit",
        "AgentTelephony",
        "AgentTool",
        "AgentVersionSetup",
    ]
    assert MIGRATION.Migration.dependencies == [
        ("simulate", "0097_merge_selected_runs_environment_v3")
    ]


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.mark.parametrize("script", ["setup_pg_replication.sql", "setup_peerdb_mirrors.sql"])
def test_setup_tables_are_in_no_mirror_list(script):
    tables = [
        model._meta.db_table
        for model in (AgentVersionSetup, AgentBehaviourUnit, AgentTool, AgentTelephony)
    ]
    text = (SCRIPTS / script).read_text()

    assert [table for table in tables if re.search(rf"\b{table}\b", text)] == []
    assert [table for table in tables if table in LANDING] == []
    assert [table for table in tables if table in landing_tables(include_usage_schema=True)] == []
    assert [table for table in tables if table in landing_tables(include_usage_schema=False)] == []
