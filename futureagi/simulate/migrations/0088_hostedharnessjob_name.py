from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:simulate_hosted_harness_job"],
    "mitigation": (
        "forward adds a name column (constant default '') to "
        "simulate_hosted_harness_job, then derives a name for each row whose name is "
        "'' from its payload (metadata agent_name or name, else the last segment of "
        "source.repository, else simulation- and the first 8 characters of the id), at "
        "most 255 characters, reading with iterator(chunk_size=500) and writing with "
        "bulk_update 500 rows at a time on the migration's own connection. A database "
        "that applied 1.58.0 already has this migration recorded and does not run it "
        "again. On a fresh install simulate_hosted_harness_job has no rows when it "
        "runs, so nothing is written. Safe to run again: only rows whose name is still "
        "'' are selected, and the migration is atomic, so a failed run rolls back "
        "whole. The reverse sets name to '' on every row before the column is dropped. "
        "Row scale: one row per hosted harness job; not a registered high-volume "
        "table."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


_CHUNK = 500


def _derived_name(job) -> str:
    """The name the read model would have derived for this row.

    Kept in step with ``services.harness_environment.environment_name`` rather
    than imported from it: a data migration has to keep working when that
    function changes, and a row written here is what the surface will show from
    now on.
    """
    payload = job.payload or {}
    metadata = payload.get("metadata") or {}
    for key in ("agent_name", "name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value[:255]
    source = payload.get("source") or {}
    repository = str(source.get("repository") or "").strip()
    if repository:
        return (repository.rsplit("/", 1)[-1] or repository)[:255]
    return f"simulation-{str(job.id)[:8]}"


def backfill_names(apps, schema_editor):
    """Freeze each existing environment's displayed name into the new column.

    Without this an old row keeps deriving its name on every read, so the first
    rename of any other row would make the list look inconsistent: some names
    stored, some inferred. Writing them once means every row is named the same
    way from here on.
    """
    HostedHarnessJob = apps.get_model("simulate", "HostedHarnessJob")
    pending = []
    for job in HostedHarnessJob.objects.using(schema_editor.connection.alias).filter(name="").iterator(chunk_size=_CHUNK):
        try:
            job.name = _derived_name(job)
        except (AttributeError, TypeError):
            job.name = f"simulation-{str(job.id)[:8]}"
        pending.append(job)
        if len(pending) >= _CHUNK:
            HostedHarnessJob.objects.using(schema_editor.connection.alias).bulk_update(pending, ["name"])
            pending = []
    if pending:
        HostedHarnessJob.objects.using(schema_editor.connection.alias).bulk_update(pending, ["name"])


def clear_names(apps, schema_editor):
    """Reverse to the pre-migration state: the column goes away, so empty it."""
    HostedHarnessJob = apps.get_model("simulate", "HostedHarnessJob")
    HostedHarnessJob.objects.update(name="")


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0087_hostedharnessjob_content_updated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(backfill_names, clear_names),
    ]
