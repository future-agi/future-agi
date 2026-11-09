"""
Data migration: re-seed demo datasets broken by the
ExperimentDatasetTable.prompt_config → legacy_prompt_config rename.

The bug crashed upload_demo_dataset at ExperimentDatasetTable.objects.create()
before rows/cells were created and before create_image_dataset() was reached.
Affected orgs have a Demo-dataset with 0 rows and no Image-Demo-dataset.

Idempotent: only targets demo datasets with 0 rows. Successfully reseeded
datasets have 50 rows and won't be matched on re-run.
"""

from django.db import migrations, transaction
from django.db.models import Count, Q


def reseed_broken_demo_data(apps, schema_editor):
    alias = schema_editor.connection.alias
    Dataset = apps.get_model("model_hub", "Dataset")
    Row = apps.get_model("model_hub", "Row")
    Cell = apps.get_model("model_hub", "Cell")
    Column = apps.get_model("model_hub", "Column")
    Project = apps.get_model("tracer", "Project")
    OrganizationMembership = apps.get_model("accounts", "OrganizationMembership")

    from accounts.user_onboard import (
        create_demo_traces_and_spans,
        upload_demo_dataset,
    )

    # Find broken demo datasets (0 rows) in one query
    broken_datasets = (
        Dataset.objects.using(alias).filter(source="demo", deleted=False)
        .annotate(row_count=Count("row", filter=Q(row__deleted=False)))
        .filter(row_count=0)
    )

    broken_orgs = {}
    for ds in broken_datasets:
        broken_orgs.setdefault(str(ds.organization_id), []).append(ds)

    if not broken_orgs:
        print("  No broken demo datasets found — nothing to do.")
        return

    print(
        f"  Found {sum(len(v) for v in broken_orgs.values())} broken demo "
        f"dataset(s) across {len(broken_orgs)} organisation(s)"
    )

    ok = 0
    failed = 0
    for org_id, datasets in broken_orgs.items():
        membership = (
            OrganizationMembership.objects.using(alias).filter(
                organization_id=org_id, is_active=True
            )
            .order_by("created_at")
            .first()
        )
        if not membership:
            print(f"  SKIP org={org_id} — no active members")
            continue

        user_id = str(membership.user_id)
        ds_ids = [ds.pk for ds in datasets]

        # Use a savepoint so we can roll back just this org on failure
        # without aborting the entire migration.
        #
        # NOTE: upload_demo_dataset / create_demo_traces_and_spans swallow
        # their own exceptions, so we can't rely on them raising. Instead
        # we verify the result after calling them and manually roll back
        # the savepoint if the reseed didn't produce rows.
        try:
            sid = transaction.savepoint(using=alias)

            # Delete broken datasets and children
            Cell.objects.using(alias).filter(dataset_id__in=ds_ids).delete()
            Row.objects.using(alias).filter(dataset_id__in=ds_ids).delete()
            Column.objects.using(alias).filter(dataset_id__in=ds_ids).delete()
            Dataset.objects.using(alias).filter(pk__in=ds_ids).delete()

            # Hard-delete demo projects so re-seed can recreate
            Project.objects.using(alias).filter(
                organization_id=org_id, source="demo", deleted=False
            ).delete()

            # Re-seed
            upload_demo_dataset(org_id, user_id, using=alias)
            create_demo_traces_and_spans(str(org_id), using=alias)

            # Verify reseed actually worked — the functions swallow errors
            # so we must check the result ourselves.
            new_demo = (
                Dataset.objects.using(alias).filter(
                    organization_id=org_id,
                    source="demo",
                    name="Demo-dataset",
                    deleted=False,
                )
                .annotate(row_count=Count("row", filter=Q(row__deleted=False)))
                .first()
            )

            if not new_demo or new_demo.row_count == 0:
                # Reseed silently failed — roll back to keep old broken data
                # (broken data is better than no data)
                transaction.savepoint_rollback(sid, using=alias)
                print(f"  FAIL org={org_id} — reseed produced no rows, rolled back")
                failed += 1
            else:
                transaction.savepoint_commit(sid, using=alias)
                print(f"  OK org={org_id} — re-seeded ({new_demo.row_count} rows)")
                ok += 1

        except Exception as e:
            transaction.savepoint_rollback(sid, using=alias)
            print(f"  FAIL org={org_id}: {e}")
            failed += 1

    print(f"  Done. OK: {ok}, Failed: {failed}")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_merge_20260407_1927"),
        # This data migration reads model_hub (Dataset/Row/Cell/Column) and
        # tracer (Project) via apps.get_model(), so those apps must be migrated
        # first. Without these deps a fresh database can apply this migration
        # before them, raising "No installed app with label 'model_hub'" and
        # crash-looping the backend on first boot.
        ("model_hub", "0104_merge_20260526_0921"),
        ("tracer", "0078_canonicalize_persisted_filter_contracts"),
    ]

    operations = [
        migrations.RunPython(reseed_broken_demo_data, migrations.RunPython.noop),
    ]
