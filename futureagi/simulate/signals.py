import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from simulate.models import AgentVersion

logger = logging.getLogger(__name__)


@receiver(post_save, sender=AgentVersion, dispatch_uid="harness_agent_version_saved")
def _agent_version_saved(
    sender, instance, created, raw=False, update_fields=None, **kwargs
):
    if raw:
        return
    # Fires on every AgentVersion save platform-wide: only a creation or a save that
    # can have set status ACTIVE goes on.
    if not created and update_fields is not None and "status" not in update_fields:
        return
    status = instance.status
    if not created and status != AgentVersion.StatusChoices.ACTIVE:
        return
    version_id, definition_id = instance.id, instance.agent_definition_id

    def saved():
        from simulate.services.harness_agent_changes import on_agent_version_saved

        try:
            on_agent_version_saved(
                version_id, definition_id=definition_id, status=status, created=created
            )
        except Exception:  # noqa: BLE001 - never raised into the save
            logger.exception(
                "harness_agent_version_saved_failed version=%s", version_id
            )

    transaction.on_commit(saved)
