"""
Data migration to clear non-UUID session_ids from Row.metadata.

Generated scenarios incorrectly stored intent_id (UC-XX) as session_id in Row.metadata.
This migration removes session_id from metadata when it's not a valid UUID,
preserving actual trace session IDs (UUIDs) for replay scenarios.
"""

import uuid
from django.db import migrations, transaction


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def clear_non_uuid_session_ids(apps, schema_editor):
    """
    Clear session_id from Row.metadata where it's not a valid UUID.
    Preserves actual session IDs (UUIDs) for replay scenarios.
    """
    Row = apps.get_model("model_hub", "Row")

    # Find all rows with non-null metadata, only load id and metadata fields
    rows_with_metadata = (
        Row.objects.using(schema_editor.connection.alias).only("id", "metadata", "dataset_id")
        .exclude(metadata__isnull=True)
        .exclude(metadata={})
    )

    updated_count = 0
    batch_size = 1000
    rows_to_update = []
    affected_datasets = set()

    with transaction.atomic(using=schema_editor.connection.alias):
        for row in rows_with_metadata.iterator(chunk_size=batch_size):
            metadata = row.metadata
            if not isinstance(metadata, dict):
                continue

            session_id = metadata.get("session_id")
            if not session_id or not isinstance(session_id, str):
                continue

            # Only keep session_id if it's a valid UUID, otherwise set to null
            if not is_valid_uuid(session_id):
                # Set session_id to null in metadata
                new_metadata = {**metadata, "session_id": None}
                row.metadata = new_metadata
                rows_to_update.append(row)
                updated_count += 1
                affected_datasets.add(str(row.dataset_id))

                # Bulk update in batches
                if len(rows_to_update) >= batch_size:
                    Row.objects.using(schema_editor.connection.alias).bulk_update(rows_to_update, ["metadata"])
                    rows_to_update.clear()
                    print(f"  Progress: {updated_count} rows processed...")

        # Update remaining rows
        if rows_to_update:
            Row.objects.using(schema_editor.connection.alias).bulk_update(rows_to_update, ["metadata"])

    if updated_count > 0:
        print(f"\n  Cleared non-UUID session_ids from {updated_count} rows")
        print(f"  Affected datasets: {len(affected_datasets)}")


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0058_merge_20260302_0732"),
    ]

    operations = [
        # Note: Using noop for reverse as original non-UUID session_id values are not preserved
        migrations.RunPython(clear_non_uuid_session_ids, migrations.RunPython.noop),
    ]
