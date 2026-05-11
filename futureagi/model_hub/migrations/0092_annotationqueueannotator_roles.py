from django.db import migrations, models


def backfill_roles(apps, schema_editor):
    AnnotationQueueAnnotator = apps.get_model(
        "model_hub",
        "AnnotationQueueAnnotator",
    )
    for membership in AnnotationQueueAnnotator.objects.all().iterator():
        role = membership.role or "annotator"
        membership.roles = [role]
        membership.save(update_fields=["roles"])


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0091_automationrule_trigger_frequency"),
    ]

    operations = [
        migrations.AddField(
            model_name="annotationqueueannotator",
            name="roles",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(backfill_roles, migrations.RunPython.noop),
    ]
