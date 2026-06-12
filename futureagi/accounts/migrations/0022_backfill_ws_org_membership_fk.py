"""
Re-runs the WorkspaceMembership → OrganizationMembership FK backfill.

0015 ran this once at the RBAC upgrade. Several creation paths added since
have inserted rows without setting the FK, leaving the column with accumulated
NULLs. The view at ``rbac_views.WorkspaceMemberListAPIView`` reads this FK
and surfaces ``org_role: null`` for those rows.

Same UPDATE as ``0015_backfill_rbac_data.backfill_ws_org_membership_fk``.
Idempotent — only touches rows where the FK is still NULL.
"""

from django.db import migrations


def backfill_ws_org_membership_fk(apps, schema_editor):
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


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0021_merge_20260526_0921"),
    ]

    operations = [
        migrations.RunPython(backfill_ws_org_membership_fk, noop),
    ]
