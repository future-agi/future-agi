from django.db import migrations, transaction
from django.db.models import F, Q

ADMIN = 8
MEMBER = 3
VIEWER = 1
BATCH = 500
LEGACY_LEVELS = {"Owner": 15, "Admin": ADMIN, "Member": MEMBER, "Viewer": VIEWER}

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_organization_membership",
        "postgres:accounts_workspace",
        "postgres:accounts_workspacemembership",
    ],
    "mitigation": (
        "Walks self-invited workspace_admin memberships of default workspaces in "
        "id order, 500 at a time, each batch committed on its own so an interrupted "
        "run keeps its progress. Skips soft-deleted rows and any seat someone else "
        "granted (granted_by set to another user). Reads only active, non-deleted "
        "organization memberships, one query per batch, and lowers each seat whose "
        "organization level is below Admin to workspace_member or workspace_viewer, "
        "writing only role and level. Rows already lowered no longer match, so a "
        "re-run is a no-op; the reverse is a noop. Nothing is written to ClickHouse."
    ),
    "reviewer": "khushalsonawat",
    "review": "https://github.com/future-agi/future-agi/pull/3700#discussion_r4596026037",
    "large_volume_rehearsal": True,
}


def _level_of(membership):
    if membership.level is not None:
        return membership.level
    return LEGACY_LEVELS.get(membership.role)


def lower_self_granted_admins(apps, schema_editor):
    WorkspaceMembership = apps.get_model("accounts", "WorkspaceMembership")
    OrganizationMembership = apps.get_model("accounts", "OrganizationMembership")
    candidates = (
        WorkspaceMembership._base_manager.filter(
            workspace__is_default=True,
            invited_by_id=F("user_id"),
            role="workspace_admin",
            deleted=False,
        )
        .filter(Q(granted_by_id__isnull=True) | Q(granted_by_id=F("user_id")))
        .order_by("id")
    )
    after = None
    while True:
        page = candidates if after is None else candidates.filter(id__gt=after)
        batch = list(
            page.values_list("id", "user_id", "workspace__organization_id")[:BATCH]
        )
        if not batch:
            return
        after = batch[-1][0]
        levels = {}
        for membership in OrganizationMembership._base_manager.filter(
            user_id__in={user_id for _id, user_id, _org in batch},
            organization_id__in={org for _id, _user, org in batch},
            is_active=True,
            deleted=False,
        ):
            key = (membership.user_id, membership.organization_id)
            level = _level_of(membership)
            if level is not None:
                levels[key] = max(levels.get(key, level), level)
        to_member, to_viewer = [], []
        for row_id, user_id, organization_id in batch:
            level = levels.get((user_id, organization_id))
            if level is not None and level >= ADMIN:
                continue
            if level is not None and level >= MEMBER:
                to_member.append(row_id)
            else:
                to_viewer.append(row_id)
        with transaction.atomic():
            still_admin = WorkspaceMembership._base_manager.filter(
                role="workspace_admin", deleted=False
            )
            still_admin.filter(id__in=to_member).update(
                role="workspace_member", level=MEMBER
            )
            still_admin.filter(id__in=to_viewer).update(
                role="workspace_viewer", level=VIEWER
            )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0028_merge_authtoken_scope_and_simulation_agent_key"),
    ]

    operations = [
        migrations.RunPython(lower_self_granted_admins, migrations.RunPython.noop),
    ]
