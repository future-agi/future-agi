from django.db import migrations, models

ROLE_PRIORITY = ["manager", "reviewer", "annotator"]
CREATOR_FULL_ACCESS_ROLES = ["manager", "reviewer", "annotator"]


def normalize_roles(value, default="annotator"):
    if value is None or value == "":
        raw_roles = []
    elif isinstance(value, str):
        raw_roles = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_roles = list(value)
    else:
        raw_roles = []

    roles = []
    for role in raw_roles:
        if role in ROLE_PRIORITY and role not in roles:
            roles.append(role)

    if not roles and default:
        roles = [default]

    return [role for role in ROLE_PRIORITY if role in roles]


def merge_roles(*role_groups):
    merged = []
    for group in role_groups:
        for role in normalize_roles(group, default=None):
            if role not in merged:
                merged.append(role)
    return [role for role in ROLE_PRIORITY if role in merged]


def backfill_roles(apps, schema_editor):
    AnnotationQueue = apps.get_model("model_hub", "AnnotationQueue")
    AnnotationQueueAnnotator = apps.get_model(
        "model_hub",
        "AnnotationQueueAnnotator",
    )

    for membership in (
        AnnotationQueueAnnotator.objects.select_related("queue").all().iterator()
    ):
        roles = normalize_roles(membership.roles or membership.role)
        if (
            membership.queue_id
            and membership.user_id
            and membership.queue.created_by_id == membership.user_id
        ):
            roles = merge_roles(roles, CREATOR_FULL_ACCESS_ROLES)
        membership.roles = roles
        membership.role = roles[0] if roles else "annotator"
        membership.save(update_fields=["role", "roles"])

    # Existing queues created before multi-role support may not have a creator
    # membership at all. Add one so creators keep manager/reviewer/annotator
    # access after deploy, matching the new queue creation default.
    for queue in (
        AnnotationQueue.objects.filter(deleted=False, created_by_id__isnull=False)
        .only("id", "created_by_id")
        .iterator()
    ):
        exists = AnnotationQueueAnnotator.objects.filter(
            queue_id=queue.id,
            user_id=queue.created_by_id,
            deleted=False,
        ).exists()
        if exists:
            continue
        AnnotationQueueAnnotator.objects.create(
            queue_id=queue.id,
            user_id=queue.created_by_id,
            role="manager",
            roles=CREATOR_FULL_ACCESS_ROLES,
        )


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
