from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and avoids the
    # write-blocking lock a plain CREATE INDEX would take on model_hub_score
    # (10M+ rows).
    atomic = False

    dependencies = [
        ('model_hub', '0118_score_tracer_project_id_and_more'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='score',
            index=models.Index(
                fields=['tracer_project_id', 'label'],
                name='idx_score_tracer_project_label',
            ),
        ),
    ]
