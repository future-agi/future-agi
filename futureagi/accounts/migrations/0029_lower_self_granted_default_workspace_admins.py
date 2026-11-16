from django.db import migrations
from django.db.models import F

ADMIN = 8
MEMBER = 3
VIEWER = 1
LEGACY_LEVELS = {"Owner": 15, "Admin": ADMIN, "Member": MEMBER, "Viewer": VIEWER}


def lower_self_granted_admins(apps, schema_editor):
    WorkspaceMembership = apps.get_model("accounts", "WorkspaceMembership")
    OrganizationMembership = apps.get_model("accounts", "OrganizationMembership")
    rows = WorkspaceMembership._base_manager.filter(
        workspace__is_default=True,
        invited_by_id=F("user_id"),
        role="workspace_admin",
    ).select_related("workspace")
    for row in rows.iterator():
        membership = OrganizationMembership._base_manager.filter(
            user_id=row.user_id,
            organization_id=row.workspace.organization_id,
            is_active=True,
        ).first()
        level = None
        if membership is not None:
            level = membership.level
            if level is None:
                level = LEGACY_LEVELS.get(membership.role)
        if level is not None and level >= ADMIN:
            continue
        member = level is not None and level >= MEMBER
        row.role = "workspace_member" if member else "workspace_viewer"
        row.level = MEMBER if member else VIEWER
        row.save(update_fields=["role", "level"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0028_merge_authtoken_scope_and_simulation_agent_key"),
    ]

    operations = [
        migrations.RunPython(lower_self_granted_admins, migrations.RunPython.noop),
    ]
