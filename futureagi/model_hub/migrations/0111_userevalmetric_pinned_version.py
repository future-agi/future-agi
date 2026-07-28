"""Add pinned_version FK to UserEvalMetric.

Allows each dataset/module eval binding to pin to a specific
EvalTemplateVersion for runtime resolution. NULL means "use
default version or live template" (backward compatible).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0110_merge_20260609_1253"),
    ]

    operations = [
        migrations.AddField(
            model_name="userevalmetric",
            name="pinned_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_user_metrics",
                to="model_hub.evaltemplateversion",
                help_text="Pin to a specific template version for runtime.",
            ),
        ),
    ]
