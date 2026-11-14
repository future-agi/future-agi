"""Close relay sockets when a membership or account stops granting access.

Covers membership saves (deactivation and soft delete), hard deletes
(including cascades from a deleted user) and account deactivation. Bulk
``QuerySet.update`` calls send no signal; their callers notify directly, and
delivery revalidation covers anything else.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from sockets.membership import close_sockets_for_membership


def _touches(update_fields, *names):
    return update_fields is None or any(name in update_fields for name in names)


@receiver(
    post_save,
    sender="accounts.OrganizationMembership",
    dispatch_uid="sockets_close_on_membership_saved",
)
def close_sockets_when_membership_ends(sender, instance, created, **kwargs):
    if instance.is_active and not instance.deleted:
        return
    if created or not _touches(kwargs.get("update_fields"), "is_active", "deleted"):
        return
    close_sockets_for_membership(instance.user_id, instance.organization_id)


@receiver(
    post_delete,
    sender="accounts.OrganizationMembership",
    dispatch_uid="sockets_close_on_membership_deleted",
)
def close_sockets_when_membership_deleted(sender, instance, **kwargs):
    close_sockets_for_membership(instance.user_id, instance.organization_id)


@receiver(
    post_save,
    sender="accounts.User",
    dispatch_uid="sockets_close_on_user_deactivated",
)
def close_sockets_when_user_deactivated(sender, instance, created, **kwargs):
    if instance.is_active or created:
        return
    if not _touches(kwargs.get("update_fields"), "is_active"):
        return
    close_sockets_for_membership(instance.pk)
