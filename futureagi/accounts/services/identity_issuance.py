"""Explicit identity/membership issuance locks; never hooks arbitrary ORM writes."""

from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock


def _pk(value):
    return getattr(value, "pk", value)


def _exists(model, value, using):
    from django.db import models

    from accounts.services.hard_erase import _refuse

    if (
        value is not None
        and not models.QuerySet(model=model, using=using).filter(pk=value).exists()
    ):
        _refuse("issuance refers to an erased identity or organization")


def save_new_person(user, *, using="default"):
    """A new person has its UUID before acquisition; insertion never upserts."""
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.services.hard_erase_issuance_entry import planned_person

    user = planned_person(user)
    keys = identity_keys(
        organizations=[user.organization_id], people=[user.pk, user.invited_by_id]
    )
    with lifecycle_lock(using, keys=keys):
        _exists(Organization, user.organization_id, using)
        _exists(User, user.invited_by_id, using)
        user.save(using=using, force_insert=True)
    return user


def create_person(**values):
    """Equivalent to User.objects.create, routed explicitly by its callers."""
    from accounts.models.user import User

    return save_new_person(User(**values))


def issue_membership(manager, method, **values):
    """Preserve a caller's create/get_or_create/update_or_create return contract.

    Only membership issuance calls this service. Offboarding/reactivation, audit,
    logging and general ORM updates remain untouched. Identity lookups are fixed;
    defaults may set roles, but cannot silently rebind a membership to another key.
    """
    from django.db import models, router

    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from accounts.services.hard_erase import _refuse

    if manager.model not in {
        OrganizationMembership,
        WorkspaceMembership,
    } or method not in {"create", "get_or_create", "update_or_create"}:
        _refuse("unqualified membership issuance method")
    using = manager._db or router.db_for_write(manager.model)
    manager = manager.db_manager(using)
    bindings = dict(values)
    for defaults in (values.get("defaults", {}), values.get("create_defaults", {})):
        for name in (
            "user",
            "user_id",
            "organization",
            "organization_id",
            "workspace",
            "workspace_id",
        ):
            if name in defaults:
                _refuse("membership defaults cannot rebind identity")
        bindings.update(defaults)
    user = _pk(bindings.get("user", bindings.get("user_id")))
    org = _pk(bindings.get("organization", bindings.get("organization_id")))
    workspace = _pk(bindings.get("workspace", bindings.get("workspace_id")))
    if manager.model is WorkspaceMembership:
        org = (
            models.QuerySet(model=Workspace, using=using)
            .filter(pk=workspace)
            .values_list("organization_id", flat=True)
            .first()
        )
    if user is None or org is None:
        _refuse("membership issuance requires live person and organization")
    people = [user] + [
        _pk(bindings.get(name, bindings.get(name + "_id")))
        for name in ("invited_by", "granted_by")
    ]
    keys = identity_keys(organizations=[org], people=people)
    with lifecycle_lock(using, keys=keys):
        for person in people:
            _exists(User, person, using)
        _exists(Organization, org, using)
        if (
            workspace is not None
            and not models.QuerySet(model=Workspace, using=using)
            .filter(pk=workspace, organization_id=org)
            .exists()
        ):
            _refuse("workspace binding changed during issuance")
        return getattr(manager, method)(**values)
