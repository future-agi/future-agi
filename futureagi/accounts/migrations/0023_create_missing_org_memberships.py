"""
Heals membership drift: active workspace members with *no* OrganizationMembership.

0022 only *linked* WorkspaceMembership → an existing active OrganizationMembership;
it never created the org membership when one was missing entirely. Those users
(workspace access but no org-membership row) hit
``UserViewSet._resolve_org_access`` and got
``"No users found for the specified organization."`` when opening the annotation
queue annotator picker (TH-6156), and were invisible to other
OrganizationMembership-dependent surfaces.

This migration creates the minimal ``Viewer`` org membership for every active
workspace member that lacks one, then re-runs the FK backfill so the new rows are
linked.

Fail-closed: only ``(user, organization)`` pairs with **no non-deleted** org
membership are created. A pair whose only row is *inactive* (the user was
deliberately removed from the org) is left untouched — we never resurrect a
removed membership.

Safety: non-atomic + id-batched; idempotent — re-running creates nothing because
the pairs now have a non-deleted row.
"""

import uuid

from django.db import migrations
from django.utils import timezone

_VIEWER_LEVEL = 1  # tfc.constants.levels.Level.VIEWER
_VIEWER_ROLE = "Viewer"  # OrganizationRoles.MEMBER_VIEW_ONLY value
_BATCH_SIZE = 2000

# (user, org) pairs that have an active membership in a *live* workspace but no
# non-deleted org membership of any kind. The workspace must itself be active and
# not soft-deleted — a stale membership on a dead workspace must not mint a fresh
# org Viewer membership (that would be an access expansion from stale data).
_MISSING_PAIRS_SQL = """
    SELECT DISTINCT wm.user_id, w.organization_id
    FROM accounts_workspacemembership wm
    JOIN accounts_workspace w ON w.id = wm.workspace_id
    WHERE wm.is_active = true
      AND wm.deleted = false
      AND w.is_active = true
      AND w.deleted = false
      AND NOT EXISTS (
          SELECT 1 FROM accounts_organization_membership om
          WHERE om.user_id = wm.user_id
            AND om.organization_id = w.organization_id
            AND om.deleted = false
      )
"""

# Mirrors 0022: link any NULL-FK active workspace membership to its active org
# membership (now including the rows created above). psycopg3 binds the batch as
# a Postgres array and matches with ``= ANY(...)``.
_LINK_SQL = """
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


def create_missing_org_memberships(apps, schema_editor):
    OrganizationMembership = apps.get_model("accounts", "OrganizationMembership")
    WorkspaceMembership = apps.get_model("accounts", "WorkspaceMembership")
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        cursor.execute(_MISSING_PAIRS_SQL)
        pairs = cursor.fetchall()

    now = timezone.now()
    to_create = [
        OrganizationMembership(
            id=uuid.uuid4(),
            user_id=user_id,
            organization_id=org_id,
            role=_VIEWER_ROLE,
            level=_VIEWER_LEVEL,
            is_active=True,
            deleted=False,
            joined_at=now,
            created_at=now,
            updated_at=now,
        )
        for (user_id, org_id) in pairs
    ]

    created = 0
    for start in range(0, len(to_create), _BATCH_SIZE):
        batch = to_create[start : start + _BATCH_SIZE]
        OrganizationMembership.objects.bulk_create(batch)
        created += len(batch)

    # Link freshly-created (and any other NULL-FK) active workspace memberships.
    null_ids = list(
        WorkspaceMembership.objects.filter(
            organization_membership__isnull=True
        ).values_list("id", flat=True)
    )
    linked = 0
    for start in range(0, len(null_ids), _BATCH_SIZE):
        batch = [str(pk) for pk in null_ids[start : start + _BATCH_SIZE]]
        with connection.cursor() as cursor:
            cursor.execute(_LINK_SQL, [batch])
            linked += cursor.rowcount

    if created or linked:
        print(
            f"\n  Created {created} Viewer OrganizationMembership rows; "
            f"linked {linked} WorkspaceMembership FK rows"
        )


def noop(apps, schema_editor):
    # Irreversible by design: auto-created rows are indistinguishable from
    # legitimate Viewer memberships, so we never delete on reverse.
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0022_backfill_ws_org_membership_fk"),
    ]

    operations = [
        migrations.RunPython(create_missing_org_memberships, noop),
    ]
