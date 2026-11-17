"""
Django signals for membership lifecycle events.

- OrganizationMembership deletion: clears Redis cache, logs audit.
- WorkspaceMembership deletion: logs audit.

Neither writes an audit row while its organization itself is being deleted: the organization's
audit log goes with it, and a row written mid-cascade names an organization that no longer
exists, so the whole deletion failed at commit (READINESS 3.22).

Registered in accounts/apps.py ready().
"""

import threading

import structlog
from django.db import connection
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from tfc.utils.audit import get_current_actor, is_audit_muted, log_audit

logger = structlog.get_logger(__name__)

_deleting = threading.local()


@receiver(post_save, sender="accounts.OrganizationMembership")
def revoke_departing_member_keys(sender, instance, using, **kwargs):
    if not instance.is_active or instance.deleted:
        from accounts.services.offboarding import revoke_organization_credentials

        revoke_organization_credentials(
            instance.user_id, instance.organization_id, using=using
        )


@receiver(pre_delete, sender="accounts.OrganizationMembership")
def revoke_deleted_member_keys(sender, instance, using, **kwargs):
    from accounts.services.offboarding import revoke_organization_credentials

    revoke_organization_credentials(
        instance.user_id, instance.organization_id, using=using
    )


@receiver(pre_save, sender="accounts.User")
def remember_person_deactivation(sender, instance, using, update_fields=None, **kwargs):
    # An inactive invitee can be saved by tenant profile/workspace operations.
    # Only an actual active -> inactive transition is platform deactivation.
    instance._deactivating_identity = False
    if instance.is_active or instance._state.adding:
        return
    if update_fields is not None and "is_active" not in update_fields:
        return
    instance._deactivating_identity = (
        sender._base_manager.using(using)
        .filter(pk=instance.pk, is_active=True)
        .exists()
    )


@receiver(post_save, sender="accounts.User")
def revoke_deactivated_person_keys(sender, instance, using, **kwargs):
    if not getattr(instance, "_deactivating_identity", False):
        return
    instance._deactivating_identity = False
    from accounts.services.offboarding import revoke_organization_credentials
    from accounts.services.personal_key_lifecycle import revoke_personal_keys
    from tfc.middleware.workspace_context import get_current_organization

    organization = get_current_organization()
    if organization is not None:
        # Tenant context never authorizes a global revoke, even for old callers.
        revoke_organization_credentials(instance.pk, organization.pk, using=using)
    else:
        # Explicit platform/admin lifecycle, not tenant offboarding.
        revoke_personal_keys(instance.pk, using=using)


@receiver(pre_delete, sender="accounts.User")
def revoke_deleted_person_keys(sender, instance, using, **kwargs):
    from accounts.services.hard_erase_effects import revoke_erased_identity_sessions
    from accounts.services.personal_key_lifecycle import revoke_personal_keys

    # The common Collector boundary has already refused protected closures,
    # independently of this receiver's registration order.
    revoke_personal_keys(instance.pk, using=using)
    revoke_erased_identity_sessions(instance.pk, using=using)


@receiver(post_save, sender="accounts.WorkspaceMembership")
def revoke_departing_workspace_grants(sender, instance, using, **kwargs):
    if not instance.is_active or instance.deleted:
        from accounts.services.service_grants import revoke_person_grants

        revoke_person_grants(
            instance.user_id, workspace_id=instance.workspace_id, using=using
        )


@receiver(pre_delete, sender="accounts.WorkspaceMembership")
def revoke_deleted_workspace_grants(sender, instance, using, **kwargs):
    from accounts.services.service_grants import revoke_person_grants

    revoke_person_grants(
        instance.user_id, workspace_id=instance.workspace_id, using=using
    )


def _organizations_being_deleted():
    """Organization id -> the database transaction deleting it, for this thread."""
    if not hasattr(_deleting, "ids"):
        _deleting.ids = {}
    return _deleting.ids


def _transaction_id():
    # A rolled-back organization deletion never reaches post_delete, so its entry is stale;
    # comparing transactions keeps it from muting a later, unrelated membership removal.
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT txid_current()")
        return cursor.fetchone()[0]


def _organization_being_deleted(organization_id):
    deleting = _organizations_being_deleted()
    if organization_id not in deleting:
        return False
    if deleting[organization_id] == _transaction_id():
        return True
    deleting.pop(organization_id, None)
    return False


@receiver(pre_delete, sender="accounts.Organization")
def organization_pre_delete(sender, instance, **kwargs):
    # Django sends every pre_delete of a cascade before it deletes anything.
    _organizations_being_deleted()[instance.pk] = _transaction_id()


@receiver(post_delete, sender="accounts.Organization")
def organization_post_delete(sender, instance, **kwargs):
    _organizations_being_deleted().pop(instance.pk, None)


@receiver(pre_delete, sender="accounts.OrganizationMembership")
def org_membership_pre_delete(sender, instance, **kwargs):
    """
    Before deleting an org membership, reset the user's current workspace
    if it belongs to this organization — prevents 404s after removal.
    """
    try:
        user = instance.user
        if (
            hasattr(user, "current_workspace")
            and user.current_workspace
            and user.current_workspace.organization_id == instance.organization_id
        ):
            user.current_workspace = None
            user.save(update_fields=["current_workspace"])
    except Exception:
        logger.exception("Error in org_membership_pre_delete signal")


@receiver(post_delete, sender="accounts.OrganizationMembership")
def org_membership_post_delete(sender, instance, **kwargs):
    """
    After deleting an org membership:
    1. Revoke this organization's credentials and scoped session cache only.
    2. Create audit log entry.
    """
    # 1. Clear Redis cache
    try:
        from accounts.services.offboarding import revoke_organization_credentials

        revoke_organization_credentials(
            instance.user_id,
            instance.organization_id,
            using=kwargs.get("using", "default"),
        )
    except Exception:
        logger.exception("Error clearing Redis cache in org_membership_post_delete")

    # 2. Audit log
    if not is_audit_muted() and not _organization_being_deleted(
        instance.organization_id
    ):
        try:
            log_audit(
                organization=instance.organization,
                action="member.removed",
                scope="organization",
                target_id=instance.user_id,
                changes={
                    "role": instance.role,
                    "level": instance.level,
                },
                actor=get_current_actor(),
            )
        except Exception:
            logger.exception("Error creating audit log in org_membership_post_delete")


@receiver(post_delete, sender="accounts.WorkspaceMembership")
def ws_membership_post_delete(sender, instance, **kwargs):
    """
    After deleting a workspace membership, create audit log entry.
    Redis cache clearing is handled by the org membership signal
    (since ws memberships CASCADE from org memberships).
    """
    if not is_audit_muted() and not _workspace_org_being_deleted(instance):
        try:
            log_audit(
                organization=instance.workspace.organization,
                action="member.removed",
                scope="workspace",
                target_id=instance.user_id,
                changes={
                    "workspace_id": str(instance.workspace_id),
                    "role": instance.role,
                    "level": instance.level,
                },
                actor=get_current_actor(),
            )
        except Exception:
            logger.exception("Error creating audit log in ws_membership_post_delete")


def _workspace_org_being_deleted(membership):
    if not _organizations_being_deleted():
        return False
    try:
        organization_id = membership.workspace.organization_id
    except Exception:
        # The workspace row is already gone in this cascade: only an org deletion does that.
        return True
    return _organization_being_deleted(organization_id)
