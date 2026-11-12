"""Project drained workflow state onto existing experiment status fields."""

from django.db import transaction

from model_hub.models.choices import StatusType
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable


def persist_run_state(data):
    with transaction.atomic():
        experiment = ExperimentsTable.objects.select_for_update().get(
            id=data["experiment_id"], deleted=False
        )
        binding = ExperimentRunControl.objects.get(
            workflow_run_id=data["workflow_run_id"],
            config_id=data["config_id"],
            experiment_dataset__experiment=experiment,
        )
        latest = binding.experiment_dataset.control_executions.first()
        # A stale execution cannot change the status of a newer execution.
        if latest.pk != binding.pk:
            return
        phase = data["phase"]
        binding.phase = phase
        binding.save(update_fields=["phase"])
        ExperimentDatasetTable.objects.filter(id=binding.experiment_dataset_id).update(
            status=StatusType.CANCELLED.value
            if phase == "STOPPED"
            else StatusType[phase].value
        )
        if phase == "RUNNING":
            ExperimentsTable.objects.filter(id=experiment.id).update(
                status=StatusType.RUNNING.value
            )
            return
        runs = list(experiment.experiment_datasets.filter(deleted=False))
        phases = []
        for run in runs:
            current = run.control_executions.first()
            phases.append(current.phase if current is not None else run.status.upper())
        if "STOPPED" in phases and all(
            p in ("STOPPED", "COMPLETED", "FAILED", "CANCELLED") for p in phases
        ):
            ExperimentsTable.objects.filter(id=experiment.id).update(
                status=StatusType.CANCELLED.value
            )
