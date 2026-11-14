from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0110_expand_investigation_requirement_id")]
    operations = [
        migrations.AddField(
            model_name="tracegroupingwork",
            name="budget_work",
            field=models.ForeignKey(
                to="tracer.tracegroupingwork",
                on_delete=models.RESTRICT,
                null=True,
                blank=True,
                related_name="budget_peers",
            ),
        ),
        migrations.AddField(
            model_name="tracegroupingattempt",
            name="pending_occurrence_ids",
            field=models.JSONField(default=list),
        ),
        migrations.AlterField(
            model_name="tracegroupingwork",
            name="state",
            field=models.CharField(
                max_length=20,
                default="pending",
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("waiting_budget", "Waiting Budget"),
                    ("superseded", "Superseded"),
                ],
            ),
        ),
    ]
