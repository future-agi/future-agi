import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0147_pairwise_preference")]

    operations = [
        migrations.AlterField(
            model_name="evaltemplate", name="eval_type",
            field=models.CharField(
                max_length=10, default="llm",
                choices=[("agent", "Agent"), ("llm", "LLM"), ("code", "Code"),
                         ("external", "External"), ("dag", "Decision tree"), ("remote", "Remote scorer")],
                help_text="Evaluator type: agent (Falcon AI powered), llm (LLM-as-a-judge), "
                          "code (custom code), dag (decision tree), external (scored by the "
                          "caller, e.g. the SDK; never executed by the server)",
            ),
        ),
        migrations.CreateModel(
            name="RemoteEvalConfig",
            fields=[
                ("template", models.OneToOneField(primary_key=True, serialize=False,
                    on_delete=django.db.models.deletion.CASCADE, related_name="remote_credentials", to="model_hub.evaltemplate")),
                ("encrypted_credentials", models.BinaryField()),
            ],
        ),
        migrations.CreateModel(
            name="RemoteEvalJob",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("definition", models.JSONField(default=dict)),
                ("encrypted_credentials", models.BinaryField()),
                ("row_ids", models.JSONField(default=list)),
                ("status", models.CharField(max_length=16, default="queued")),
                ("sent", models.PositiveIntegerField(default=0)),
                ("total", models.PositiveIntegerField(default=0)),
                ("failed", models.PositiveIntegerField(default=0)),
                ("result", models.JSONField(default=dict)),
                ("error", models.CharField(max_length=200, blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("template", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="model_hub.evaltemplate")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="model_hub.experimentrun")),
            ],
        ),
    ]
