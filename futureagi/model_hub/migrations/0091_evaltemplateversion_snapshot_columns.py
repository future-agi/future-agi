# Generated for TH-4787

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds the EvalTemplate column-only fields (output_type_normalized,
    pass_threshold, choice_scores, error_localizer_enabled, eval_tags) onto
    EvalTemplateVersion so version snapshots are complete and lossless.

    Before this change, only ``config_snapshot`` / ``criteria`` / ``model``
    were captured per-version. Restoring an older version silently kept the
    template's current threshold / choice_scores / tags / error-localizer
    flag / output type, which is a correctness bug — the user's intent when
    they say "restore V2" is "restore everything that defined V2".

    All new columns are nullable (no default backfill) so that existing
    versions remain distinguishable as "pre-snapshot" rows. The restore +
    set-default views skip restoring any field whose snapshot value is NULL
    on the source version, falling back to the template's current value.
    """

    dependencies = [
        ("model_hub", "0090_merge_20260423_1541"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaltemplateversion",
            name="output_type_normalized",
            field=models.CharField(
                blank=True,
                help_text="Normalized output type at this version: pass_fail, percentage, deterministic. NULL on pre-snapshot versions.",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="evaltemplateversion",
            name="pass_threshold",
            field=models.FloatField(
                blank=True,
                help_text="Pass threshold at this version (0.0-1.0). NULL on pre-snapshot versions.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="evaltemplateversion",
            name="choice_scores",
            field=models.JSONField(
                blank=True,
                help_text='Choice→score mapping at this version (e.g. {"Yes": 1.0, "No": 0.0}). NULL on pre-snapshot versions.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="evaltemplateversion",
            name="error_localizer_enabled",
            field=models.BooleanField(
                blank=True,
                help_text="Whether error localization was enabled at this version. NULL on pre-snapshot versions.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="evaltemplateversion",
            name="eval_tags",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=100),
                blank=True,
                help_text="Eval tags at this version. NULL on pre-snapshot versions.",
                null=True,
                size=None,
            ),
        ),
    ]
