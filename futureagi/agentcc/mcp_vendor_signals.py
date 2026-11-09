"""Membership deletion/deactivation hooks for partner cleanup.

Bulk queryset mutations bypass Django signals; callers performing those must
call offboard_member explicitly. Credential issuance always rechecks membership.
"""

from functools import partial

from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver


@receiver(
    post_save,
    sender="accounts.OrganizationMembership",
    dispatch_uid="agentcc_vendor_member_inactive",
)
def member_inactive(sender, instance, **kwargs):
    if not instance.is_active or instance.deleted:
        from agentcc.services.mcp_vendor_connections import offboard_member

        transaction.on_commit(
            partial(offboard_member, instance.organization_id, instance.user_id),
            using=kwargs.get("using"),
        )


@receiver(
    pre_delete,
    sender="accounts.OrganizationMembership",
    dispatch_uid="agentcc_vendor_member_deleted",
)
def member_deleted(sender, instance, **kwargs):
    from agentcc.services.mcp_vendor_connections import offboard_member

    transaction.on_commit(
        partial(offboard_member, instance.organization_id, instance.user_id),
        using=kwargs.get("using"),
    )
