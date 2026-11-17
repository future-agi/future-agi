from django.db import migrations


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_demotracepublication",
        "postgres:tracer_observation_span",
        "postgres:tracer_project",
        "postgres:tracer_trace",
    ],
    "mitigation": (
        "Read existing live session-linked demo traces through the project join and "
        "a correlated span-existence query, streaming IDs in chunks of 500. Insert "
        "publication intents in batches of at most 500 with ignore_conflicts=True. "
        "atomic=False allows committed batches; reruns tolerate existing intents and "
        "the reverse is a noop. Qualification must measure the trace/span reference "
        "workload, normal migration bootstrap, storage/WAL/temp growth and restore. "
        "This migration does not directly publish to ClickHouse or dispatch workflows."
    ),
    "reviewer": "Tanmaycode1",
    "review": "https://github.com/future-agi/future-agi/pull/3623#discussion_r4460578890",
    "large_volume_rehearsal": True,
}


def queue_existing_demo_traces(apps, schema_editor):
    # Includes demo traces repaired by 0020, before this outbox existed.
    from django.db.models import Exists, OuterRef

    Trace = apps.get_model("tracer", "Trace")
    Span = apps.get_model("tracer", "ObservationSpan")
    Publication = apps.get_model("accounts", "DemoTracePublication")
    alias = schema_editor.connection.alias
    trace_ids = (
        Trace.objects.using(alias)
        .filter(
            project__source="demo",
            project__deleted=False,
            deleted=False,
            session_id__isnull=False,
        )
        .filter(Exists(Span.objects.using(alias).filter(trace_id=OuterRef("pk"))))
    )
    batch = []
    for trace_id in trace_ids.values_list("pk", flat=True).iterator(chunk_size=500):
        batch.append(Publication(trace_id=trace_id))
        if len(batch) == 500:
            Publication.objects.using(alias).bulk_create(batch, ignore_conflicts=True)
            batch = []
    Publication.objects.using(alias).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("accounts", "0029_demotracepublication_demoseedrequest")]
    operations = [
        migrations.RunPython(queue_existing_demo_traces, migrations.RunPython.noop)
    ]
