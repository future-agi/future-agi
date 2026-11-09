"""Run bindings are written by the worker, never supplied by the API caller."""

from asgiref.sync import sync_to_async
from temporalio import activity


@activity.defn
async def bind_experiment_run_activity(data: dict) -> None:
    await sync_to_async(_bind, thread_sensitive=True)(data)


@activity.defn
async def persist_experiment_run_state_activity(data: dict) -> None:
    from model_hub.services.experiment_run_state import persist_run_state

    await sync_to_async(persist_run_state, thread_sensitive=True)(data)


def _bind(data):
    from django.db import close_old_connections

    from model_hub.models.experiment_run_control import ExperimentRunControl
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentPromptConfig,
    )

    close_old_connections()
    try:
        model = (
            ExperimentPromptConfig
            if data["kind"] == "prompt"
            else ExperimentAgentConfig
        )
        config = model.objects.get(
            id=data["config_id"],
            deleted=False,
            experiment_dataset__deleted=False,
            experiment_dataset__experiment_id=data["experiment_id"],
            experiment_dataset__experiment__deleted=False,
        )
        ExperimentRunControl.objects.get_or_create(
            workflow_run_id=data["workflow_run_id"],
            defaults={
                "workflow_id": data["workflow_id"],
                "experiment_dataset": config.experiment_dataset,
                "config_id": config.id,
                "kind": data["kind"],
            },
        )
    finally:
        close_old_connections()
