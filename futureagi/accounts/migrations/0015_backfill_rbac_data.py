"""
Data migration: backfill RBAC data for existing users.

Combines all backfill operations in dependency order:
1. Create OrganizationMembership rows for users with user.organization set.
2. Backfill integer levels from legacy string roles on OrganizationMembership.
3. Backfill integer levels from legacy string roles on WorkspaceMembership.
4. Link WorkspaceMembership → OrganizationMembership where missing.
5. Enable workspaces for all existing organizations.

Idempotent — only updates rows where data is missing.
Runs AFTER 0014 which adds the level/FK columns.
"""

from django.db import migrations


# Mapping from legacy string roles to integer levels
STRING_TO_LEVEL = {
    "Owner": 15,
    "Admin": 8,
    "Member": 3,
    "Viewer": 1,
    "workspace_admin": 8,
    "workspace_member": 3,
    "workspace_viewer": 1,
}


def backfill_org_memberships(apps, schema_editor):
    """Create OrganizationMembership for users that have user.organization set.

    In migration context, apps.get_model() returns a plain Manager (no
    BaseModelManager filtering), so objects.get_or_create sees all rows
    including soft-deleted ones and won't hit unique constraint issues.
    """
    User = apps.get_model("accounts", "User")
    OrganizationMembership = apps.get_model("accounts", "OrganizationMembership")

    users = User.objects.using(schema_editor.connection.alias).filter(
        organization__isnull=False,
        is_active=True,
    ).select_related("organization")

    created = 0
    for user in users.iterator(chunk_size=500):
        _, was_created = OrganizationMembership.objects.using(schema_editor.connection.alias).get_or_create(
            user=user,
            organization=user.organization,
            defaults={
                "role": user.organization_role or "Member",
                "level": STRING_TO_LEVEL.get(user.organization_role, 3),
                "is_active": True,
            },
        )
        if was_created:
            created += 1

    if created:
        print(f"\n  Created {created} OrganizationMembership rows")


def backfill_org_membership_levels(apps, schema_editor):
    """Backfill OrganizationMembership.level from role string."""
    OrganizationMembership = apps.get_model("accounts", "OrganizationMembership")

    for role_str, level_int in STRING_TO_LEVEL.items():
        OrganizationMembership.objects.using(schema_editor.connection.alias).filter(
            role=role_str,
            level__isnull=True,
        ).update(level=level_int)


def backfill_ws_membership_levels(apps, schema_editor):
    """Backfill WorkspaceMembership.level from role string."""
    WorkspaceMembership = apps.get_model("accounts", "WorkspaceMembership")

    for role_str, level_int in STRING_TO_LEVEL.items():
        WorkspaceMembership.objects.using(schema_editor.connection.alias).filter(
            role=role_str,
            level__isnull=True,
        ).update(level=level_int)


def backfill_ws_org_membership_fk(apps, schema_editor):
    """
    Link WorkspaceMembership → OrganizationMembership where the FK is null.
    Uses raw SQL because Django ORM doesn't allow joined field references
    (workspace__organization_id) in Subquery updates.
    """
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE accounts_workspacemembership wm
            SET organization_membership_id = (
                SELECT om.id
                FROM accounts_organization_membership om
                JOIN accounts_workspace w ON w.id = wm.workspace_id
                WHERE om.user_id = wm.user_id
                  AND om.organization_id = w.organization_id
                LIMIT 1
            )
            WHERE wm.organization_membership_id IS NULL
        """)
        updated = cursor.rowcount

    if updated:
        print(f"\n  Linked {updated} WorkspaceMembership rows to OrganizationMembership")


def backfill_ws_enabled(apps, schema_editor):
    """Enable workspaces for all existing organizations."""
    Organization = apps.get_model("accounts", "Organization")
    updated = Organization.objects.using(schema_editor.connection.alias).filter(ws_enabled=False).update(ws_enabled=True)
    if updated:
        print(f"\n  Enabled workspaces for {updated} existing organizations")


def noop(apps, schema_editor):
    """Reverse migration is a no-op (backfilled data stays populated)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_organization_require_2fa_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_org_memberships, noop),
        migrations.RunPython(backfill_org_membership_levels, noop),
        migrations.RunPython(backfill_ws_membership_levels, noop),
        migrations.RunPython(backfill_ws_org_membership_fk, noop),
        migrations.RunPython(backfill_ws_enabled, noop),
    ]
