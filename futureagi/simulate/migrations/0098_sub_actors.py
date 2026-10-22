from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0097_merge_selected_runs_environment_v3")]

    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="sub_actors",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="calltranscript",
            name="speaker_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="calltranscript",
            name="speaker_role",
            field=models.CharField(
                choices=[
                    ("user", "User"),
                    ("assistant", "Assistant"),
                    ("system", "System"),
                    ("tool_calls", "Tool Calls"),
                    ("tool_call_result", "Tool Call Result"),
                    ("sub_actor", "Sub-actor"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                help_text="Role of the speaker (user or assistant)",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="chatmessagemodel",
            name="role",
            field=models.CharField(
                choices=[
                    ("user", "User"),
                    ("assistant", "Assistant"),
                    ("tool", "Tool"),
                    ("sub_actor", "Sub-actor"),
                ],
                help_text="Role of the message sender (user or assistant)",
                max_length=20,
            ),
        ),
    ]
