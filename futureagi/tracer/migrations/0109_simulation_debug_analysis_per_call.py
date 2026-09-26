import django.db.models.deletion
from django.db import migrations, models


def drop_execution_level_jobs(apps, schema_editor):
    # A simulation job now investigates one call; jobs that covered a whole run
    # have no call to point at. Their reports cascade with them.
    Job = apps.get_model("tracer", "TraceInvestigationJob")
    Job.objects.filter(workload_type="simulation_test_execution").delete()


class Migration(migrations.Migration):
    # The delete's deferred FK checks must commit before the table is altered.
    atomic = False

    dependencies = [
        ("tracer", "0108_simulation_debug_analysis"),
        ("simulate", "0092_merge_parallelism_environment_v3"),
    ]

    operations = [
        migrations.RunPython(drop_execution_level_jobs, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="traceinvestigationjob",
            name="unique_simulation_investigation_job",
        ),
        migrations.RemoveConstraint(
            model_name="traceinvestigationjob",
            name="valid_trace_investigation_workload",
        ),
        migrations.AddField(
            model_name="traceinvestigationjob",
            name="call_execution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debug_analysis_jobs",
                to="simulate.callexecution",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("call_execution__isnull", False)),
                fields=("project", "call_execution"),
                name="unique_simulation_call_investigation_job",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("workload_type", "trace"),
                        ("trace_id__isnull", False),
                        ("test_execution__isnull", True),
                        ("call_execution__isnull", True),
                    )
                    | models.Q(
                        ("workload_type", "simulation_test_execution"),
                        ("trace_id__isnull", True),
                        ("test_execution__isnull", False),
                        ("call_execution__isnull", False),
                    )
                ),
                name="valid_trace_investigation_workload",
            ),
        ),
    ]
