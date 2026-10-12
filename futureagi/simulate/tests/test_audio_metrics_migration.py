"""AC16: additive migration forward/backward on populated tables."""

import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_migration_forward_backward_populated_db():
    before = ("simulate", "0097_merge_selected_runs_environment_v3")
    after = ("simulate", "0098_callexecution_audio_metrics")
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        org = apps.get_model("accounts", "Organization").objects.create(
            name="Migration audio"
        )
        scenario = apps.get_model("simulate", "Scenarios").objects.create(
            name="Before", source="synthetic", organization_id=org.pk
        )
        run = apps.get_model("simulate", "RunTest").objects.create(
            name="Before", organization_id=org.pk
        )
        execution = apps.get_model("simulate", "TestExecution").objects.create(
            run_test_id=run.pk
        )
        call_id = uuid.uuid4()
        apps.get_model("simulate", "CallExecution").objects.create(
            id=call_id,
            test_execution_id=execution.pk,
            scenario_id=scenario.pk,
            call_summary="retained",
        )
        snapshot_id = uuid.uuid4()
        apps.get_model("simulate", "CallExecutionSnapshot").objects.create(
            id=snapshot_id, call_execution_id=call_id, rerun_type="call_and_eval"
        )
        executor = MigrationExecutor(connection)
        executor.migrate([after])
        apps = executor.loader.project_state([after]).apps
        Call = apps.get_model("simulate", "CallExecution")
        call = Call.objects.get(pk=call_id)
        assert (
            call.audio_metrics,
            call.audio_provenance,
            call.audio_analysis_generation,
        ) == (None, None, 0)
        assert (
            apps.get_model("simulate", "CallExecutionSnapshot")
            .objects.get(pk=snapshot_id)
            .audio_metrics
            is None
        )
        Call.objects.filter(pk=call_id).update(
            audio_metrics={"schema_version": 1}, audio_analysis_generation=3
        )
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        assert (
            apps.get_model("simulate", "CallExecution")
            .objects.get(pk=call_id)
            .call_summary
            == "retained"
        )
        with connection.cursor() as cursor:
            columns = {
                c.name
                for c in connection.introspection.get_table_description(
                    cursor, "simulate_call_execution"
                )
            }
        assert (
            not {"audio_metrics", "audio_provenance", "audio_analysis_generation"}
            & columns
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)
