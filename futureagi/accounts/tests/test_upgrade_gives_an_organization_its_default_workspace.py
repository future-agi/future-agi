"""An organization with no workspace gets its default workspace.

An organization with no workspace of any state gets the default workspace the
product makes for a new organization, administered by its earliest active
Owner (else its earliest active Admin), so its Owner and Admins land in it and
can write. Nothing else is written: no row for a Member or Viewer, no key, no
change to an account or an organization membership. Every other organization
keeps its rows as they are; the output names each one whose default workspace
is inactive or removed, that has workspaces but no default, or that has no
workspace and no active Owner or Admin.

An upgrade here steps the accounts app back to the migration before this one,
then forward to its leaf.
"""

import importlib
import uuid
from datetime import timedelta
from functools import cache
from unittest.mock import patch

import pytest
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.workspace_landing import resolve_user_workspace
from accounts.tests import workspace_write_support as write_support
from accounts.utils import first_signup
from model_hub.models.develop_dataset import Dataset
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.api_errors import first_error_code
from tfc.utils.tests.production_path import (
    PASSWORD,
    sign_in,
    use_production_request_settings,
)

pytestmark = pytest.mark.django_db(transaction=True)

PREVIOUS = ("accounts", "0040_alter_user_invited_by")
STEP = ("accounts", "0041_default_workspace_for_every_organization")
CREATE_DATASET = "/model-hub/develops/create-dataset-manually/"
IDENTIFIERS = {"id"}
TIMESTAMPS = {"created_at", "updated_at", "granted_at", "deleted_at"}
ADMIN_ROW = ("workspace_id", "role", "level", "is_active", "organization_membership_id")
ADMIN_ROLE = OrganizationRoles.WORKSPACE_ADMIN
ADMIN_LEVEL = Level.WORKSPACE_ADMIN
HELD_DEFAULT = "its default workspace is inactive or removed"
NO_DEFAULT = "it has workspaces and none is its default"
NO_ADMINISTRATOR = "it has no active Owner or Admin"

scope_no_egress = write_support.scope_no_egress
write_effects = write_support.write_effects


def upgrade():
    """Migrate the accounts app to the migration before this one, then to its leaf."""
    clear_workspace_context()
    connection = connections["default"]
    try:
        MigrationExecutor(connection).migrate([PREVIOUS])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("accounts"))
        clear_workspace_context()


def forward_step():
    module = importlib.import_module(f"accounts.migrations.{STEP[1]}")
    return module.give_every_organization_its_default_workspace


@cache
def historical_apps():
    return MigrationLoader(None, ignore_no_migrations=True).project_state(STEP).apps


def organization(label, **fields):
    clear_workspace_context()
    return Organization.objects.create(name=f"{label} Org", **fields)


def person(organization, label, role, level, *, active=True):
    """An account and its organization membership at ``level``."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        password=PASSWORD,
        name=f"{label.title()} Person",
        organization=organization,
        organization_role=role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=role,
        level=level,
        is_active=True,
    )
    if not active:
        OrganizationMembership._base_manager.filter(pk=membership.pk).update(
            is_active=False, removed_at=timezone.now()
        )
    return user, membership


def owner(organization, label="owner"):
    return person(organization, label, OrganizationRoles.OWNER, Level.OWNER)


def workspace(organization, created_by, *, name, is_default, is_active=True):
    clear_workspace_context()
    return Workspace.no_workspace_objects.create(
        name=name,
        organization=organization,
        is_default=is_default,
        is_active=is_active,
        created_by=created_by,
    )


def remove(model, row):
    model._base_manager.filter(pk=row.pk).update(
        deleted=True, deleted_at=timezone.now()
    )


def sign_up(monkeypatch, label):
    """A new account through the product's own sign-up write."""
    monkeypatch.setenv("ALLOW_ANY_EMAIL", "true")
    with (
        patch("accounts.utils.is_cloud_env", return_value=False),
        patch("accounts.utils.process_post_registration"),
        patch("accounts.utils.track_mixpanel_event"),
        patch("accounts.utils.mixpanel_tracker"),
        patch("accounts.utils._fire_deployment_telemetry_registration"),
    ):
        account = first_signup(
            {
                "email": f"{label}-{uuid.uuid4().hex[:8]}@example.test",
                "full_name": f"{label.title()} Person",
                "company_name": "",
                "password": PASSWORD,
            }
        )
    clear_workspace_context()
    return account


def organization_of(account):
    memberships = OrganizationMembership.no_workspace_objects
    return memberships.get(user=account, is_active=True).organization


def state(organization):
    """Every row the upgrade could write or change for ``organization``."""
    clear_workspace_context()
    memberships = OrganizationMembership._base_manager.filter(organization=organization)
    people = list(memberships.values_list("user_id", flat=True))
    return {
        "workspaces": list(
            Workspace._base_manager.filter(organization=organization)
            .order_by("pk")
            .values()
        ),
        "workspace_memberships": list(
            WorkspaceMembership._base_manager.filter(user_id__in=people)
            .order_by("pk")
            .values()
        ),
        "memberships": list(
            OrganizationMembership._base_manager.filter(user_id__in=people)
            .order_by("pk")
            .values()
        ),
        "accounts": list(
            User.objects.filter(pk__in=people)
            .order_by("pk")
            .values("pk", "organization_id", "organization_role", "is_active", "config")
        ),
        "keys": OrgApiKey._base_manager.filter(organization=organization).count(),
    }


def every_row():
    clear_workspace_context()
    return {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (Workspace, WorkspaceMembership, OrganizationMembership)
    }


def column_value(column, value, names):
    if column in TIMESTAMPS:
        return value is not None
    return names.get(value, value)


def provisioned_rows(organization):
    """The organization's workspace and workspace-membership rows, column by
    column, with identifiers replaced by what they name and each timestamp
    replaced by whether it is set."""
    clear_workspace_context()
    workspace = Workspace._base_manager.get(organization=organization)
    membership = WorkspaceMembership._base_manager.get(workspace=workspace)
    administrator = OrganizationMembership._base_manager.get(
        pk=membership.organization_membership_id
    )
    names = {
        organization.pk: "organization",
        workspace.pk: "workspace",
        administrator.pk: "administrator's organization membership",
        administrator.user_id: "administrator",
    }

    def columns(model, pk):
        values = model._base_manager.filter(pk=pk).values().get()
        return {
            column: column_value(column, value, names)
            for column, value in values.items()
            if column not in IDENTIFIERS
        }

    return {
        "workspace": columns(Workspace, workspace.pk),
        "workspace_membership": columns(WorkspaceMembership, membership.pk),
    }


def joined_in_order(*memberships):
    """Give the memberships ``created_at`` values one minute apart, in this order."""
    start = timezone.now() - timedelta(days=1)
    for minutes, membership in enumerate(memberships):
        OrganizationMembership._base_manager.filter(pk=membership.pk).update(
            created_at=start + timedelta(minutes=minutes)
        )


def administration(organization):
    """Who made the organization's workspace, and its workspace-membership rows."""
    clear_workspace_context()
    default = Workspace._base_manager.get(organization=organization)
    rows = WorkspaceMembership._base_manager.filter(workspace=default)
    return default.created_by_id, list(rows.values_list("user_id", *ADMIN_ROW[1:]))


def administered_by(user, membership):
    """``administration`` when ``user`` administers through ``membership``."""
    return user.pk, [(user.pk, ADMIN_ROLE, ADMIN_LEVEL, True, membership.pk)]


def people_rows(organization):
    """Every column of the organization's memberships and of its people's accounts,
    keyed by model and row id."""
    clear_workspace_context()
    memberships = OrganizationMembership._base_manager.filter(organization=organization)
    accounts = User._base_manager.filter(pk__in=memberships.values("user_id"))
    return {
        (model.__name__, str(row[model._meta.pk.attname])): row
        for model, rows in ((OrganizationMembership, memberships), (User, accounts))
        for row in rows.values()
    }


def create_dataset(client, name):
    clear_workspace_context()
    response = client.post(
        CREATE_DATASET,
        {"dataset_name": name, "number_of_rows": 1, "number_of_columns": 1},
        format="json",
    )
    clear_workspace_context()
    return response


def test_an_owner_without_a_workspace_lands_and_writes_after_the_upgrade(
    write_effects, settings
):
    use_production_request_settings(settings)
    solo = organization("Solo")
    account, membership = owner(solo)
    client = sign_in(account.email)

    refused = create_dataset(client, "refused-dataset")
    assert refused.status_code == 403, refused.status_code
    assert first_error_code(refused.data) == "workspace_required"

    upgrade()

    first = create_dataset(client, "first-dataset")
    assert first.status_code == 200, first_error_code(first.data)
    second = create_dataset(client, "second-dataset")
    assert second.status_code == 200, first_error_code(second.data)

    clear_workspace_context()
    default = Workspace._base_manager.get(organization=solo)
    assert default.name == "Default Workspace"
    assert default.is_default and default.is_active and not default.deleted
    rows = WorkspaceMembership._base_manager.filter(user=account)
    assert list(rows.values_list(*ADMIN_ROW)) == [
        (default.pk, ADMIN_ROLE, ADMIN_LEVEL, True, membership.pk)
    ]
    assert resolve_user_workspace(account, solo) == default
    datasets = Dataset._base_manager.filter(organization=solo)
    assert sorted(datasets.values_list("name", "workspace_id")) == [
        ("first-dataset", default.pk),
        ("second-dataset", default.pk),
    ]
    assert not OrgApiKey._base_manager.filter(organization=solo).exists()


def test_the_upgrade_writes_the_rows_sign_up_writes(monkeypatch):
    signed_up = organization_of(sign_up(monkeypatch, "signup-owner"))
    upgraded = organization("Upgraded")
    owner(upgraded)

    upgrade()

    assert provisioned_rows(upgraded) == provisioned_rows(signed_up)


def test_a_second_upgrade_writes_nothing():
    twice = organization("Twice")
    owner(twice)
    upgrade()
    assert Workspace._base_manager.filter(organization=twice, is_default=True).count()
    before = every_row()

    upgrade()

    assert every_row() == before


def test_a_run_stopped_after_one_batch_is_finished_by_the_next_run():
    forward = forward_step()
    apps = historical_apps()
    connection = connections["default"]
    first = organization("First", id=uuid.UUID(int=1))
    second = organization("Second", id=uuid.UUID(int=2))
    owner(first)
    owner(second)

    with connection.schema_editor(atomic=False) as editor:
        forward(apps, editor, batch_size=1, batches=1)
    clear_workspace_context()
    assert Workspace._base_manager.filter(organization=first, is_default=True).count()
    assert not Workspace._base_manager.filter(organization=second).exists()
    assert not WorkspaceMembership._base_manager.filter(
        workspace__organization=second
    ).exists()
    first_rows = state(first)

    with connection.schema_editor(atomic=False) as editor:
        forward(apps, editor, batch_size=1)
    assert state(first) == first_rows
    assert provisioned_rows(second) == provisioned_rows(first)


def test_an_organization_without_an_active_owner_is_given_to_its_admin():
    led = organization("Admin Led")
    person(led, "former-owner", OrganizationRoles.OWNER, Level.OWNER, active=False)
    admin, admin_membership = person(led, "admin", OrganizationRoles.ADMIN, Level.ADMIN)
    member, _ = person(led, "member", OrganizationRoles.MEMBER, Level.MEMBER)

    upgrade()

    clear_workspace_context()
    default = Workspace._base_manager.get(organization=led)
    assert default.created_by_id == admin.pk
    rows = WorkspaceMembership._base_manager.filter(workspace=default)
    assert list(rows.values_list("user_id", *ADMIN_ROW[1:])) == [
        (admin.pk, ADMIN_ROLE, ADMIN_LEVEL, True, admin_membership.pk)
    ]
    assert not WorkspaceMembership._base_manager.filter(user=member).exists()
    assert resolve_user_workspace(admin, led) == default


def test_the_earliest_active_owner_administers_before_an_earlier_admin():
    owners = organization("Two Owners")
    admin, admin_membership = person(
        owners, "early-admin", OrganizationRoles.ADMIN, Level.ADMIN
    )
    first, first_membership = owner(owners, "first-owner")
    second, second_membership = owner(owners, "second-owner")
    joined_in_order(admin_membership, first_membership, second_membership)

    upgrade()

    assert administration(owners) == administered_by(first, first_membership)
    for passed_over in (admin, second):
        assert not WorkspaceMembership._base_manager.filter(user=passed_over).exists()


def test_an_owner_whose_account_is_inactive_is_passed_over():
    next_owner = organization("Next Owner")
    inactive, inactive_membership = owner(next_owner, "inactive-owner")
    chosen, chosen_membership = owner(next_owner, "next-owner")
    joined_in_order(inactive_membership, chosen_membership)
    admin_led = organization("Admin After Owner")
    inactive_alone, inactive_alone_membership = owner(admin_led, "inactive-owner")
    admin, admin_membership = person(
        admin_led, "admin", OrganizationRoles.ADMIN, Level.ADMIN
    )
    joined_in_order(inactive_alone_membership, admin_membership)
    User._base_manager.filter(pk__in=[inactive.pk, inactive_alone.pk]).update(
        is_active=False
    )

    upgrade()

    assert administration(next_owner) == administered_by(chosen, chosen_membership)
    assert administration(admin_led) == administered_by(admin, admin_membership)
    for passed_over in (inactive, inactive_alone):
        assert not WorkspaceMembership._base_manager.filter(user=passed_over).exists()


def test_an_organization_it_serves_keeps_its_memberships_and_accounts():
    served = organization("Whole Team")
    owner(served)
    person(served, "admin", OrganizationRoles.ADMIN, Level.ADMIN)
    person(served, "member", OrganizationRoles.MEMBER, Level.MEMBER)
    person(served, "viewer", OrganizationRoles.MEMBER_VIEW_ONLY, Level.VIEWER)
    before = people_rows(served)

    upgrade()

    clear_workspace_context()
    assert Workspace._base_manager.filter(organization=served, is_default=True).exists()
    after = people_rows(served)
    assert sorted(after) == sorted(before)
    changed = [
        (row, column)
        for row, values in before.items()
        for column, value in values.items()
        if after[row][column] != value
    ]
    assert changed == []


def held_default_organizations():
    """Organizations whose default is inactive or removed, and one with
    workspaces but no default."""
    inactive = organization("Inactive Default")
    account, _ = owner(inactive)
    workspace(inactive, account, name="Held", is_default=True, is_active=False)

    removed = organization("Removed Default")
    account, _ = owner(removed)
    remove(Workspace, workspace(removed, account, name="Held", is_default=True))

    undefaulted = organization("No Default")
    account, _ = owner(undefaulted)
    workspace(undefaulted, account, name="Team", is_default=False)
    return inactive, removed, undefaulted


def unowned_organization():
    """An active Member only: its Owner is deactivated and its Admin removed."""
    unowned = organization("Unowned")
    person(unowned, "former-owner", OrganizationRoles.OWNER, Level.OWNER, active=False)
    _, admin_membership = person(
        unowned, "removed-admin", OrganizationRoles.ADMIN, Level.ADMIN
    )
    remove(OrganizationMembership, admin_membership)
    person(unowned, "member", OrganizationRoles.MEMBER, Level.MEMBER)
    return unowned


def test_the_organizations_it_does_not_serve_are_named_in_its_output(capsys):
    inactive, removed, undefaulted = held_default_organizations()
    unowned = unowned_organization()
    capsys.readouterr()

    upgrade()

    output = capsys.readouterr().out
    for organization_left, reason in (
        (inactive, HELD_DEFAULT),
        (removed, HELD_DEFAULT),
        (undefaulted, NO_DEFAULT),
        (unowned, NO_ADMINISTRATOR),
    ):
        assert str(organization_left.pk) in output, organization_left.name
        named = f"organization {organization_left.pk} left as it is: {reason}"
        assert named in output, organization_left.name


def test_an_organization_with_its_default_workspace_is_not_touched():
    served = organization("Served")
    account, _ = owner(served)
    workspace(served, account, name="Default Workspace", is_default=True)
    member, _ = person(served, "member", OrganizationRoles.MEMBER, Level.MEMBER)
    before = state(served)

    upgrade()

    assert state(served) == before
    assert not WorkspaceMembership._base_manager.filter(user=member).exists()


def test_a_held_default_or_a_missing_default_is_not_touched():
    held = held_default_organizations()
    before = [state(row) for row in held]

    upgrade()

    assert [state(row) for row in held] == before


def test_an_organization_without_an_active_owner_or_admin_is_not_touched():
    unowned = unowned_organization()
    before = state(unowned)

    upgrade()

    assert state(unowned) == before
    assert not Workspace._base_manager.filter(organization=unowned).exists()


def test_an_organization_created_by_sign_up_is_not_touched(monkeypatch):
    signed_up = organization_of(sign_up(monkeypatch, "fresh-owner"))
    before = state(signed_up)

    upgrade()

    assert state(signed_up) == before
