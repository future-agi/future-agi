"""An organization with no workspace gets its default workspace.

An organization with no workspace of any state gets the default workspace the
product makes for a new organization (``accounts.services.default_workspace``):
the same columns, and a workspace-admin membership for its earliest active
Owner, else its earliest active Admin. Nothing else is written. Every other
organization is left as it is; the output names each one whose default workspace
is inactive or removed, that has workspaces but no default, or that has no
workspace and no active Owner or Admin.

A batch with an organization to serve takes the organization and person advisory
locks the product's issuance takes, in one transaction, and re-reads its
organizations under them.
"""

from django.db import migrations
from django.db.models import Q

from accounts.services.default_workspace import (
    DEFAULT_WORKSPACE_DESCRIPTION,
    DEFAULT_WORKSPACE_NAME,
)
from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_organization",
        "postgres:accounts_organization_membership",
        "postgres:accounts_user",
        "postgres:accounts_workspace",
        "postgres:accounts_workspacemembership",
    ],
    "mitigation": (
        "Reads organizations in primary-key order in batches of 100 (keyset on "
        "the primary key, never a whole-table statement). For each organization "
        "with no workspace row of any state and an active Owner or Admin, "
        "unless its workspaces or that administrator change during its batch, "
        "it inserts one default workspace "
        "(the columns accounts.services.default_workspace writes) and one "
        "workspace-admin membership for its earliest active Owner, else its "
        "earliest active Admin. It writes nothing else: no organization "
        "membership, no key, no account change, no update or delete of any row. "
        "atomic=False; for a batch with an organization to serve, the re-read "
        "of its organizations and the inserts run in one short transaction "
        "holding the per-organization and per-person advisory transaction "
        "locks the product's issuance takes and the row and key locks of its "
        "inserts; the batch's first reads run before that transaction. "
        "Idempotent and resumable: an organization it served has an active "
        "default workspace and is skipped, so a re-run, or a run after a stop "
        "part-way, writes only what is still missing. The reverse is a noop; "
        "it never deletes a workspace."
    ),
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}

BATCH_SIZE = 100
HELD_DEFAULT = "its default workspace is inactive or removed"
NO_DEFAULT = "it has workspaces and none is its default"
NO_ADMINISTRATOR = "it has no active Owner or Admin"
CHANGED = "it changed during this run; it is left as it is now"


def _at_level(level):
    """Memberships at ``level`` as ``level_or_legacy`` reads them."""
    roles = [role for role, value in Level.STRING_TO_LEVEL.items() if value == level]
    return Q(level=level) | Q(level__isnull=True, role__in=roles)


def _administrators(apps, alias, organization_ids):
    """Map each organization to (user id, membership id) of its earliest active
    Owner, else its earliest active Admin."""
    Membership = apps.get_model("accounts", "OrganizationMembership")
    active = Membership._base_manager.using(alias).filter(
        organization_id__in=organization_ids,
        deleted=False,
        is_active=True,
        removed_at__isnull=True,
        user__is_active=True,
    )
    chosen = {}
    for level in (Level.OWNER, Level.ADMIN):
        rows = (
            active.filter(_at_level(level))
            .order_by("created_at", "pk")
            .values_list("organization_id", "user_id", "pk")
        )
        for organization_id, user_id, membership_id in rows:
            chosen.setdefault(organization_id, (user_id, membership_id))
    return chosen


def _plan(apps, alias, organization_ids, *, report):
    """Map each organization to serve to its administrator; when report is set,
    name those left with a held or missing default or no administrator."""
    Organization = apps.get_model("accounts", "Organization")
    Workspace = apps.get_model("accounts", "Workspace")
    existing = list(
        Organization._base_manager.using(alias)
        .filter(pk__in=organization_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    served, held, undefaulted = set(), set(), set()
    workspaces = Workspace._base_manager.using(alias).filter(
        organization_id__in=existing
    )
    for organization_id, is_default, is_active, deleted in workspaces.values_list(
        "organization_id", "is_default", "is_active", "deleted"
    ):
        if not is_default:
            undefaulted.add(organization_id)
        elif is_active and not deleted:
            served.add(organization_id)
        else:
            held.add(organization_id)
    administrators = _administrators(apps, alias, existing)
    plan = {}
    for organization_id in existing:
        if organization_id in served:
            continue
        if organization_id in held:
            reason = HELD_DEFAULT
        elif organization_id in undefaulted:
            reason = NO_DEFAULT
        elif organization_id not in administrators:
            reason = NO_ADMINISTRATOR
        else:
            plan[organization_id] = administrators[organization_id]
            continue
        if report:
            _name(organization_id, reason)
    return plan


def _name(organization_id, reason):
    print(f"  accounts: organization {organization_id} left as it is: {reason}")


def _write(apps, alias, organization_id, user_id, membership_id):
    Workspace = apps.get_model("accounts", "Workspace")
    WorkspaceMembership = apps.get_model("accounts", "WorkspaceMembership")
    workspace = Workspace._base_manager.using(alias).create(
        name=DEFAULT_WORKSPACE_NAME,
        display_name=DEFAULT_WORKSPACE_NAME,
        description=DEFAULT_WORKSPACE_DESCRIPTION,
        organization_id=organization_id,
        is_default=True,
        is_active=True,
        created_by_id=user_id,
    )
    WorkspaceMembership._base_manager.using(alias).create(
        workspace=workspace,
        user_id=user_id,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership_id=membership_id,
    )


def _provision(apps, alias, organization_ids):
    plan = _plan(apps, alias, organization_ids, report=True)
    if not plan:
        return
    keys = identity_keys(
        organizations=list(plan), people=[user_id for user_id, _ in plan.values()]
    )
    with lifecycle_lock(alias, keys=keys):
        current = _plan(apps, alias, list(plan), report=False)
        for organization_id, administrator in plan.items():
            if current.get(organization_id) != administrator:
                _name(organization_id, CHANGED)
                continue
            _write(apps, alias, organization_id, *administrator)
            print(
                f"  accounts: organization {organization_id} has its default workspace"
            )


def give_every_organization_its_default_workspace(
    apps, schema_editor, *, batch_size=BATCH_SIZE, batches=None
):
    """Serve every organization in primary-key order, ``batch_size`` at a time;
    ``batches`` stops after that many batches."""
    with executor_alias_scope(schema_editor):
        Organization = apps.get_model("accounts", "Organization")
        alias = schema_editor.connection.alias
        organizations = Organization._base_manager.using(alias).order_by("pk")
        after = None
        done = 0
        while batches is None or done < batches:
            page = (
                organizations if after is None else organizations.filter(pk__gt=after)
            )
            organization_ids = list(page.values_list("pk", flat=True)[:batch_size])
            if not organization_ids:
                return
            _provision(apps, alias, organization_ids)
            after = organization_ids[-1]
            done += 1


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("accounts", "0040_alter_user_invited_by")]
    operations = [
        migrations.RunPython(
            give_every_organization_its_default_workspace,
            migrations.RunPython.noop,
        )
    ]
