# Generated manually — see issue #1585

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('model_hub', '0101_backfill_composite_v1_config_snapshots'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='status',
            field=models.CharField(
                choices=[('NotStarted', 'Not Started'), ('Queued', 'Queued'), ('Running', 'Running'), ('Completed', 'Completed'), ('Editing', 'Editing'), ('Inactive', 'Inactive'), ('Failed', 'Failed'), ('PartialRun', 'Partial Run'), ('ExperimentEvaluation', 'Experiment Evaluation'), ('Uploading', 'Uploading'), ('PartialExtracted', 'Partial Extracted'), ('Processing', 'Processing'), ('Deleting', 'Deleting'), ('PartialCompleted', 'Partial Completed'), ('OptimizationEvaluation', 'Optimization Evaluation'), ('Error', 'Error'), ('Cancelled', 'Cancelled')],
                default='Completed',
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='dataset',
            name='failure_reason',
            field=models.TextField(blank=True, null=True),
        ),
    ]
