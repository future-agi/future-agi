# Generated manually -- see issue #1667

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('model_hub', '0101_backfill_composite_v1_config_snapshots'),
    ]

    operations = [
        migrations.AddField(
            model_name='annotationqueue',
            name='custom_eval_config',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name='annotation_queues',
                to='tracer.customevalconfig',
            ),
        ),
    ]
