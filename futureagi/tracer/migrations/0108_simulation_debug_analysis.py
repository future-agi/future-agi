import django.db.models.deletion
import django.db.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0107_investigation_attribution_explanation"),
        ("simulate", "0092_merge_parallelism_environment_v3"),
    ]

    operations = [
        migrations.AddField(
            model_name="traceerrorgroup",
            name="target_type",
            field=models.CharField(
                choices=[("error_feed", "Error Feed"), ("simulation", "Simulation")],
                db_index=True,
                default="error_feed",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="traceerrorgroup",
            name="test_execution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="error_groups",
                to="simulate.testexecution",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceerrorgroup",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("target_type", "error_feed"),
                        ("test_execution__isnull", True),
                    )
                    | models.Q(
                        ("target_type", "simulation"),
                        ("test_execution__isnull", False),
                    )
                ),
                name="valid_error_group_target_scope",
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationjob",
            name="workload_type",
            field=models.CharField(
                choices=[("trace", "Trace"), ("simulation_test_execution", "Simulation test execution")],
                default="trace",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationjob",
            name="test_execution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debug_analysis_jobs",
                to="simulate.testexecution",
            ),
        ),
        migrations.AlterField(
            model_name="traceinvestigationjob",
            name="trace_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="traceinvestigationjob",
            name="root_span_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name="traceinvestigationjob",
            name="root_end_time",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name="traceinvestigationjob",
            name="unique_trace_investigation_job",
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("trace_id__isnull", False)),
                fields=("project", "trace_id"),
                name="unique_trace_investigation_job",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("test_execution__isnull", False)),
                fields=("project", "test_execution"),
                name="unique_simulation_investigation_job",
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
                    )
                    | models.Q(
                        ("workload_type", "simulation_test_execution"),
                        ("trace_id__isnull", True),
                        ("test_execution__isnull", False),
                    )
                ),
                name="valid_trace_investigation_workload",
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationreport",
            name="test_execution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debug_analysis_reports",
                to="simulate.testexecution",
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationreport",
            name="workload_type",
            field=models.CharField(
                choices=[("trace", "Trace"), ("simulation_test_execution", "Simulation test execution")],
                default="trace",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationreport",
            name="observed_call_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="traceinvestigationreport",
            name="trace_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name="traceinvestigationreport",
            name="valid_inv_report_source_fields",
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationreport",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("source", "legacy_scan"),
                        ("source_record_id__isnull", False),
                        ("job__isnull", True),
                        ("attempt__isnull", True),
                    )
                    | (
                        models.Q(
                            ("source", "omega"),
                            ("source_record_id__isnull", True),
                            ("job__isnull", False),
                            ("attempt__isnull", False),
                            ("idempotency_key__isnull", False),
                            ("result_digest__isnull", False),
                            ("contract_version__isnull", False),
                            ("evidence_digest__isnull", False),
                            ("outcome__isnull", False),
                            ("coverage_scope__isnull", False),
                            ("read_complete__isnull", False),
                            ("model_calls__isnull", False),
                            ("input_tokens__isnull", False),
                            ("output_tokens__isnull", False),
                            ("cost_status__isnull", False),
                        )
                        & (
                            models.Q(
                                ("workload_type", "trace"),
                                ("test_execution__isnull", True),
                                ("trace_id__isnull", False),
                                ("observed_span_count__isnull", False),
                                ("observed_call_count__isnull", True),
                                ("future_arrivals_known__isnull", False),
                            )
                            | models.Q(
                                ("workload_type", "simulation_test_execution"),
                                ("test_execution__isnull", False),
                                ("trace_id__isnull", True),
                                ("observed_span_count__isnull", True),
                                ("observed_call_count__isnull", False),
                                ("future_arrivals_known__isnull", True),
                            )
                        )
                    )
                ),
                name="valid_inv_report_source_fields",
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationevidencereceipt",
            name="call_execution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debug_analysis_evidence",
                to="simulate.callexecution",
            ),
        ),
        migrations.AlterField(
            model_name="traceinvestigationevidencereceipt",
            name="span_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationevidencereceipt",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("call_execution__isnull", False), ("span_id__isnull", True))
                    | models.Q(("call_execution__isnull", True), ("span_id__isnull", False))
                ),
                name="valid_inv_evidence_locator",
            ),
        ),
        migrations.AddField(
            model_name="traceinvestigationattribution",
            name="call_execution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="debug_analysis_attributions",
                to="simulate.callexecution",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationreport",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("is_current", True),
                    ("deleted", False),
                    ("test_execution__isnull", False),
                ),
                fields=("project", "test_execution"),
                name="unique_current_simulation_investigation",
            ),
        ),
    ]
