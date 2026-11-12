"""Authoritative, key-independent eligibility for personal gateway access."""

from accounts.models.organization_membership import OrganizationMembership


def live_gateway_memberships():
    """Every live person, including those with no keys and in other workspaces.

    Organization is a plain Django model: it has neither ``deleted`` nor
    ``is_active``. Its existence is enforced by the membership's cascading FK.
    Membership has both flags; User has only ``is_active``. Do not infer access
    from a user's home org, a key row, or a context-filtered default manager.
    Role-derived gateway scopes are deliberately not part of 733.
    """
    return OrganizationMembership._base_manager.using("default").filter(
        is_active=True,
        deleted=False,
        user__is_active=True,
    )
