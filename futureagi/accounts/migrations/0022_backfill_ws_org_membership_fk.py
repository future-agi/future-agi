"""
Re-runs the WorkspaceMembership → OrganizationMembership FK backfill.

0015 ran this once at the RBAC upgrade. Several creation paths added since
have inserted rows without setting the FK, leaving the column with accumulated
NULLs. The view at ``rbac_views.WorkspaceMemberListAPIView`` reads this FK
and surfaces ``org_role: null`` for those rows. The write-side cause is fixed
by routing every create through ``services.workspace_membership`` in the same
PR; this heals the already-drifted rows.

Correctness vs the 0015 version:
  * Only links to an ``is_active`` org membership — never a soft-deleted /
    cancelled one (the list excludes ``organization_membership__is_active=False``,
    so an inactive FK would *hide* the member, worse than the NULL).
  * Deterministic ``ORDER BY`` so the chosen row is stable across re-runs.

Safety: non-atomic + id-batched so it can't hold one long lock / transaction
over the whole table. Idempotent — only touches rows where the FK is NULL.
"""

from django.db import migrations

_BATCH_SIZE = 2000

_UPDATE_SQL = """
    UPDATE accounts_workspacemembership wm
    SET organization_membership_id = (
        SELECT om.id
        FROM accounts_organization_membership om
        JOIN accounts_workspace w ON w.id = wm.workspace_id
        WHERE om.user_id = wm.user_id
          AND om.organization_id = w.organization_id
          AND om.is_active = true
        ORDER BY om.created_at DESC, om.id DESC
        LIMIT 1
    )
    WHERE wm.id = ANY(%s::uuid[])
      AND wm.organization_membership_id IS NULL
"""


def backfill_ws_org_membership_fk(apps, schema_editor):
    WorkspaceMembership = apps.get_model("accounts", "WorkspaceMembership")
    connection = schema_editor.connection

    null_ids = list(
        WorkspaceMembership.objects.filter(
            organization_membership__isnull=True
        ).values_list("id", flat=True)
    )

    updated = 0
    for start in range(0, len(null_ids), _BATCH_SIZE):
        # psycopg3 has no ``IN %s`` tuple-expansion (that was psycopg2); bind the
        # batch as a Postgres array and match with ``= ANY(...)`` instead.
        batch = [str(pk) for pk in null_ids[start : start + _BATCH_SIZE]]
        with connection.cursor() as cursor:
            cursor.execute(_UPDATE_SQL, [batch])
            updated += cursor.rowcount

    if updated:
        print(
            f"\n  Linked {updated} WorkspaceMembership rows to OrganizationMembership"
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    # Non-atomic: each batch commits on its own so a large table never sits
    # under one long-running UPDATE transaction.
    atomic = False

    dependencies = [
        ("accounts", "0021_merge_20260526_0921"),
    ]

    operations = [
        migrations.RunPython(backfill_ws_org_membership_fk, noop),
    ]
