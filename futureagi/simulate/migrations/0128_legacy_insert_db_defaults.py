from django.db import migrations, models


class Migration(migrations.Migration):
    """Database defaults for three NOT NULL columns added without one.

    A build from before 0103, 0106 and 0115 inserts agent version setups and
    hosted harness jobs without these columns, during a rollout and after a
    rollback, since migrations are never reverted. Each default equals the
    field's existing default, and setting one is a catalog-only change.
    """

    dependencies = [
        ("simulate", "0125_merge_eval_config_keys_and_agent_fix_outside_attempts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agentversionsetup",
            name="contract_object_key",
            field=models.CharField(
                blank=True, db_default="", default="", max_length=255
            ),
        ),
        migrations.AlterField(
            model_name="agentversionsetup",
            name="origin",
            field=models.CharField(
                choices=[("build", "Build"), ("reading", "Reading")],
                db_default="build",
                default="build",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="hostedharnessjob",
            name="cancel_confirmed_by",
            field=models.CharField(
                blank=True,
                choices=[
                    ("sandbox_absent", "Sandbox absent"),
                    ("provider_call_ended", "Provider call ended"),
                    ("cleanup_failed", "Cleanup failed"),
                    ("provider_call_open", "Provider call still open"),
                    ("provider_unchecked", "Provider calls not checked"),
                    ("unchecked_no_key", "Not checked: no provider key"),
                    (
                        "unchecked_key_rejected",
                        "Not checked: provider rejected the key",
                    ),
                    ("unchecked_no_target", "Not checked: no provider agent ID"),
                    ("unchecked_no_calls", "Not checked: no calls listed"),
                    (
                        "unchecked_too_many_calls",
                        "Not checked: more calls than one page",
                    ),
                ],
                db_default="",
                default="",
                max_length=32,
            ),
        ),
    ]
