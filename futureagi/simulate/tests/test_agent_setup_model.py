import importlib

from django.db import migrations

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
