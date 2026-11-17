import uuid

from django.db import migrations


def forwards(apps, schema_editor):
    """Copy existing assigned_to FK values to QueueItemAssignment through table."""
    QueueItem = apps.get_model("model_hub", "QueueItem")
    QueueItemAssignment = apps.get_model("model_hub", "QueueItemAssignment")

    items_with_assignment = QueueItem.objects.using(schema_editor.connection.alias).filter(
        assigned_to__isnull=False, deleted=False
    ).values_list("id", "assigned_to_id")

    assignments = [
        QueueItemAssignment(
            id=uuid.uuid4(),
            queue_item_id=item_id,
            user_id=user_id,
        )
        for item_id, user_id in items_with_assignment
    ]

    if assignments:
        QueueItemAssignment.objects.using(schema_editor.connection.alias).bulk_create(assignments, ignore_conflicts=True)


def backwards(apps, schema_editor):
    """No-op reverse — the old FK is still populated."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0069_queueitemassignment"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
