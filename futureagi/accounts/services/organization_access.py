from accounts.models import OrgApiKey
from accounts.models.organization import Organization
from accounts.models.user import User
from tfc.constants.levels import Level


def membership_level(user: User, organization: Organization) -> int:
    return user.get_membership_level(organization) or 0


def is_organization_admin(user: User, organization: Organization) -> bool:
    return membership_level(user, organization) >= Level.ADMIN


def can_switch_key(user: User, api_key: OrgApiKey, is_admin: bool) -> bool:
    creator_id = {"user": api_key.user_id, "coding_agent": api_key.minted_by_id}.get(
        api_key.type
    )
    return is_admin or (creator_id is not None and creator_id == user.id)
