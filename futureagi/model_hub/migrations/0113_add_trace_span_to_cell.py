# Generated manually for issue #2665 - dataset execution tracing

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('model_hub', '0112_eval_ground_truth_tenant_scope'),
    ]

    operations = [
        migrations.AddField(
            model_name='cell',
            name='trace_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Root trace ID for this dataset/experiment run',
                max_length=255,
                null=True
            ),
        ),
        migrations.AddField(
            model_name='cell',
            name='span_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Span ID for this specific row execution',
                max_length=255,
                null=True
            ),
        ),
    ]
