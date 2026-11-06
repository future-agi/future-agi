from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simulate", "0102_merge_sub_actors_and_agent_fix_candidate_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="calltranscript",
            name="actor_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.RenameField(
            model_name="hostedharnessscenario",
            old_name="sub_actors",
            new_name="actors",
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
                    ("actor", "Actor"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                help_text="Role of the speaker (user or assistant)",
                max_length=20,
            ),
        ),
    ]
