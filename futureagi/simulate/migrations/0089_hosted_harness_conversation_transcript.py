from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0088_hostedconversation_control_only"),
    ]

    operations = [
        migrations.CreateModel(
            name="HostedHarnessConversationTranscript",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("project_key", models.CharField(max_length=255)),
                ("provider_session_id", models.CharField(max_length=255)),
                ("subpath", models.CharField(default="", max_length=512)),
                ("entries", models.JSONField(default=list)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="provider_transcripts",
                        to="simulate.hostedharnessconversation",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_hosted_harness_conversation_transcript",
            },
        ),
        migrations.AddConstraint(
            model_name="hostedharnessconversationtranscript",
            constraint=models.UniqueConstraint(
                fields=(
                    "conversation",
                    "project_key",
                    "provider_session_id",
                    "subpath",
                ),
                name="uniq_hconv_transcript_key",
            ),
        ),
        migrations.AddIndex(
            model_name="hostedharnessconversationtranscript",
            index=models.Index(
                fields=("conversation", "provider_session_id"),
                name="idx_hconv_transcript_session",
            ),
        ),
    ]
