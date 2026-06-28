"""Selector for checking whether a user may access a prompt template."""

from accounts.models.organization_membership import OrganizationMembership


def get_user_org_ids(user) -> set:
    """Return the set of organisation IDs the user is an active member of.

    Includes the primary FK org so users without explicit membership rows
    (legacy accounts) are still covered.
    """
    org_ids = set(
        OrganizationMembership.objects.filter(
            user=user, is_active=True
        ).values_list("organization_id", flat=True)
    )
    # Only fall back to the FK org when no active membership row exists for
    # it — adding it unconditionally would re-grant access to orgs the user
    # was removed from (inactive membership).
    fk_org_id = getattr(user, "organization_id", None)
    if fk_org_id and fk_org_id not in org_ids:
        has_inactive = OrganizationMembership.objects.filter(
            user=user, organization_id=fk_org_id
        ).exists()
        if not has_inactive:
            org_ids.add(fk_org_id)
    return org_ids


def user_can_access_template(user, template) -> bool:
    """Return True if the user belongs to the org that owns the template."""
    return template.organization_id in get_user_org_ids(user)
