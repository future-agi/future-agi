"""
Django signals for model_hub app.

Handles cross-app synchronization between prompt playground (model_hub)
and agent playground when PromptVersion changes occur.

Also handles cascade soft-delete when Dataset, PromptTemplate, or
PromptVersion are soft-deleted.
"""

from threading import local

import structlog
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

logger = structlog.get_logger(__name__)

# Thread-local storage for capturing old state before save
_thread_locals = local()


@receiver(pre_save, sender="model_hub.PromptVersion")
def capture_prompt_version_old_state(sender, instance, **kwargs):
    """Capture old state before save for comparison in post_save."""
    if instance.pk:
        # Only capture if this is an update (not a new creation)
        from model_hub.models.run_prompt import PromptVersion

        try:
            old_instance = PromptVersion.objects.get(pk=instance.pk)
            # Store old state in thread-local storage
            _thread_locals.prompt_version_old_state = {
                "variable_names": old_instance.variable_names,
                "prompt_config_snapshot": old_instance.prompt_config_snapshot,
            }
        except PromptVersion.DoesNotExist:
            _thread_locals.prompt_version_old_state = None
    else:
        _thread_locals.prompt_version_old_state = None


@receiver(post_save, sender="model_hub.PromptVersion")
def sync_agent_playground_on_prompt_change(sender, instance, created, **kwargs):
    """
    Auto-sync agent playground nodes when PromptVersion changes.

    Triggers sync when:
    - Variables change ({{var}} additions/removals in messages)
    - response_format changes (string/json/json_schema)

    Does NOT trigger on:
    - Message content changes (not stored at node level)
    - is_draft or is_default flag changes
    - Metadata or evaluation config changes
    """
    # Skip if this is a new creation (no previous state to compare)
    if created:
        _thread_locals.prompt_version_old_state = None
        return

    # Check if relevant fields changed
    if not _has_relevant_changes(instance):
        _thread_locals.prompt_version_old_state = None
        return

    # Import here to avoid circular imports
    from agent_playground.services.prompt_sync import sync_nodes_for_prompt_version

    try:
        sync_nodes_for_prompt_version(instance)
        logger.info(
            "agent_playground_sync_completed",
            prompt_version_id=str(instance.id),
            prompt_template_id=str(instance.original_template_id),
        )
    except Exception as e:
        logger.error(
            "agent_playground_sync_failed",
            prompt_version_id=str(instance.id),
            error=str(e),
            exc_info=True,
        )
        # Don't raise - allow prompt save to succeed even if sync fails
    finally:
        # Clean up thread-local storage
        _thread_locals.prompt_version_old_state = None


def _has_relevant_changes(prompt_version) -> bool:
    """
    Check if PromptVersion has changes requiring agent playground sync.

    Compares current state with previous state captured in pre_save.
    Returns True if variables or response_format changed.
    """
    # Get old state from thread-local storage (captured in pre_save)
    old_state = getattr(_thread_locals, "prompt_version_old_state", None)
    if not old_state:
        return False

    def _variable_keys(value):
        if isinstance(value, dict):
            return set(value.keys())
        if isinstance(value, (list, tuple, set)):
            return set(value)
        return set()

    old_vars = _variable_keys(old_state.get("variable_names"))
    new_vars = _variable_keys(prompt_version.variable_names)

    if old_vars != new_vars:
        logger.info(
            "variables_changed",
            prompt_version_id=str(prompt_version.id),
            added=list(new_vars - old_vars),
            removed=list(old_vars - new_vars),
        )
        return True

    # Check if response_format changed
    def _response_format(config):
        if not isinstance(config, dict):
            return None
        configuration = config.get("configuration")
        if not isinstance(configuration, dict):
            return None
        return configuration.get("response_format")

    old_format = _response_format(old_state.get("prompt_config_snapshot"))
    new_format = _response_format(prompt_version.prompt_config_snapshot)

    if old_format != new_format:
        logger.info(
            "response_format_changed",
            prompt_version_id=str(prompt_version.id),
            old_format=old_format,
            new_format=new_format,
        )
        return True

    return False


@receiver(pre_save, sender="model_hub.Dataset")
def cascade_soft_delete_on_dataset_deletion(sender, instance, **kwargs):
    """
    When a Dataset is soft-deleted, cascade soft-delete all experiments
    that use it as their source dataset (along with their EDTs, EPCs, EACs,
    and snapshot datasets).
    """
    if not instance.pk:
        return

    from model_hub.models.develop_dataset import Dataset

    try:
        old_instance = Dataset.all_objects.get(pk=instance.pk)
    except Dataset.DoesNotExist:
        return

    if not old_instance.deleted and instance.deleted:
        _cascade_soft_delete_dataset_experiments(instance)


def _cascade_soft_delete_dataset_experiments(dataset):
    """Soft-delete experiments and their children when source dataset is deleted."""
    from django.utils import timezone

    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentDatasetTable,
        ExperimentPromptConfig,
        ExperimentsTable,
    )

    now = timezone.now()

    experiments = ExperimentsTable.all_objects.filter(dataset=dataset, deleted=False)

    for exp in experiments:
        # Soft-delete EDTs, EPCs, and EACs
        edts = ExperimentDatasetTable.all_objects.filter(experiment=exp, deleted=False)
        edt_ids = list(edts.values_list("id", flat=True))

        ExperimentPromptConfig.all_objects.filter(
            experiment_dataset_id__in=edt_ids, deleted=False
        ).update(deleted=True, deleted_at=now)

        ExperimentAgentConfig.all_objects.filter(
            experiment_dataset_id__in=edt_ids, deleted=False
        ).update(deleted=True, deleted_at=now)

        edts.update(deleted=True, deleted_at=now)

        # Soft-delete snapshot dataset (triggers this signal recursively
        # to clean up the snapshot's experiments if any)
        if exp.snapshot_dataset_id:
            snapshot = exp.snapshot_dataset
            if snapshot and not snapshot.deleted:
                snapshot.deleted = True
                snapshot.deleted_at = now
                snapshot.save(update_fields=["deleted", "deleted_at"])

    experiments.update(deleted=True, deleted_at=now)

    logger.info(
        "dataset_soft_delete_cascaded",
        dataset_id=str(dataset.id),
        experiments_deleted=experiments.count(),
    )


@receiver(pre_save, sender="model_hub.PromptTemplate")
def cascade_soft_delete_on_prompt_template_deletion(sender, instance, **kwargs):
    """
    When a PromptTemplate is soft-deleted, cascade soft-delete all linked agent playground nodes.
    """
    if not instance.pk:
        return  # New object, not an update

    try:
        old_instance = sender.no_workspace_objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return  # Should not happen, but be defensive

    # Check if this is a soft delete operation
    if not old_instance.deleted and instance.deleted:
        # Soft delete is happening - cascade to nodes
        from agent_playground.models.prompt_template_node import PromptTemplateNode
        from agent_playground.services.node_crud import cascade_soft_delete_node

        # Find all PromptTemplateNodes linked to this template
        ptn_records = PromptTemplateNode.no_workspace_objects.filter(
            prompt_template=instance, deleted=False
        ).select_related("node")

        nodes_cascaded = 0
        for ptn in ptn_records:
            # Cascade soft delete the node (which will also delete the PTN)
            cascade_soft_delete_node(ptn.node)
            nodes_cascaded += 1

        if nodes_cascaded > 0:
            logger.info(
                "prompt_template_soft_delete_cascaded",
                prompt_template_id=str(instance.id),
                nodes_deleted=nodes_cascaded,
            )


@receiver(pre_save, sender="model_hub.PromptVersion")
def cascade_soft_delete_on_prompt_version_deletion(sender, instance, **kwargs):
    """
    When a PromptVersion is soft-deleted, cascade soft-delete all linked agent playground nodes.
    """
    if not instance.pk:
        return  # New object, not an update

    # Import PromptVersion to get the actual model class with managers
    from model_hub.models.run_prompt import PromptVersion

    try:
        # PromptVersion doesn't have a workspace field, so use all_objects
        old_instance = PromptVersion.all_objects.get(pk=instance.pk)
    except PromptVersion.DoesNotExist:
        return

    # Check if this is a soft delete operation
    if not old_instance.deleted and instance.deleted:
        # Soft delete is happening - cascade to nodes
        from agent_playground.models.prompt_template_node import PromptTemplateNode
        from agent_playground.services.node_crud import cascade_soft_delete_node

        # Find all PromptTemplateNodes linked to this version
        ptn_records = PromptTemplateNode.no_workspace_objects.filter(
            prompt_version=instance, deleted=False
        ).select_related("node")

        nodes_cascaded = 0
        for ptn in ptn_records:
            # Cascade soft delete the node (which will also delete the PTN)
            cascade_soft_delete_node(ptn.node)
            nodes_cascaded += 1

        if nodes_cascaded > 0:
            logger.info(
                "prompt_version_soft_delete_cascaded",
                prompt_version_id=str(instance.id),
                nodes_deleted=nodes_cascaded,
            )
