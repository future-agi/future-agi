import structlog
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_workspace,
)
from tfc.utils.base_model import _can_assign_workspace, _is_insert_save

logger = structlog.get_logger(__name__)


@receiver(pre_save)
def auto_assign_workspace(sender, instance, **kwargs):
    """
    Signal to automatically assign workspace and organization from thread-local context.
    Works for all models that have a workspace / organization field.
    """
    insert_save = _is_insert_save(
        instance,
        force_update=kwargs.get("force_update", False),
        update_fields=kwargs.get("update_fields"),
    )
    current_workspace = get_current_workspace()
    current_organization = get_current_organization()

    # Check if the model has a workspace field (not just a property)
    if hasattr(instance, "workspace") and hasattr(instance, "workspace_id"):
        if (
            insert_save
            and not instance.workspace_id
            and _can_assign_workspace(instance, current_workspace)
        ):
            instance.workspace = current_workspace
            logger.debug(
                f"Auto-assigned workspace {getattr(current_workspace, 'id', None)} to new {instance.__class__.__name__}"
            )

    if hasattr(instance, "organization") and hasattr(instance, "organization_id"):
        if insert_save and not instance.organization_id and current_organization:
            instance.organization = current_organization
            logger.debug(
                f"Auto-assigned organization {getattr(current_organization, 'id', None)} to new {instance.__class__.__name__}"
            )


@receiver(post_save)
def ensure_workspace_after_save(sender, instance, created, **kwargs):
    """
    Signal to ensure workspace and organization are set after saving.
    Handles cases where pre_save didn't work or for updates.
    """
    if kwargs.get("update_fields"):
        return

    needs_workspace = (
        hasattr(instance, "workspace")
        and hasattr(instance, "workspace_id")
        and not instance.workspace_id
    )

    needs_organization = (
        hasattr(instance, "organization")
        and hasattr(instance, "organization_id")
        and not instance.organization_id
    )

    if not (needs_workspace or needs_organization):
        return

    current_workspace = get_current_workspace()
    current_organization = get_current_organization()

    update_fields = []

    if needs_workspace and _can_assign_workspace(instance, current_workspace):
        instance.workspace = current_workspace
        update_fields.append("workspace")
        logger.info(
            f"Assigned workspace {getattr(current_workspace, 'id', None)} to {instance.__class__.__name__} {instance.pk}"
        )

    if needs_organization and current_organization:
        instance.organization = current_organization
        update_fields.append("organization")
        logger.info(
            f"Assigned organization {getattr(current_organization, 'id', None)} to {instance.__class__.__name__} {instance.pk}"
        )

    if update_fields:
        instance.save(update_fields=update_fields)
