"""Project drained workflow state onto existing experiment status fields."""

from django.db import transaction

from model_hub.models.choices import StatusType
from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable


def _selected_siblings(experiment):
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.services.experiment_run_control import (
        _selected_scoring_control,
        _selected_scoring_workflow_id,
    )

    owned, active = set(), {}
    links = NativeRunBinding.objects.select_related("run").filter(
        snapshot__experiment=experiment,
        variant__experiment=experiment,
        variant__deleted=False,
        run__deleted=False,
        run__metadata__native_identity__selected_parent_run_id__isnull=False,
    )
    for link in links:
        run = link.run
        if _selected_scoring_workflow_id(run, link) is None:
            continue
        control = _selected_scoring_control(run, link)
        if control is not None:
            owned.add(control.pk)
        if run.completed_at is None and run.status in {"queued", "running"}:
            # A queued sibling may not have reached its binding activity yet.
            active.setdefault(link.variant_id, []).append(control)
    return owned, active


def _variant_phase(variant, owned, active):
    current = variant.control_executions.first()
    siblings = active.get(variant.pk, [])
    if not siblings:
        return current.phase if current is not None else variant.status.upper()
    phases = [control.phase if control else "RUNNING" for control in siblings]
    if current is not None and current.pk not in owned:
        phases.append(current.phase)
    for phase in ("RUNNING", "STOPPED", "FAILED", "CANCELLED"):
        if phase in phases:
            return phase
    return "COMPLETED"


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
        owned, active = _selected_siblings(experiment)
        latest = binding.experiment_dataset.control_executions.first()
        # Legacy executions retain latest-only ownership. Selected children own
        # separate sealed executions, even when another sibling binds later.
        if latest.pk != binding.pk and binding.pk not in owned:
            return
        phase = data["phase"]
        binding.phase = phase
        binding.save(update_fields=["phase"])
        for siblings in active.values():
            for control in siblings:
                if control is not None and control.pk == binding.pk:
                    control.phase = phase
        variant_phase = _variant_phase(binding.experiment_dataset, owned, active)
        ExperimentDatasetTable.objects.filter(id=binding.experiment_dataset_id).update(
            status=StatusType.CANCELLED.value
            if variant_phase == "STOPPED"
            else StatusType[variant_phase].value
        )
        runs = list(experiment.experiment_datasets.filter(deleted=False))
        phases = [_variant_phase(run, owned, active) for run in runs]
        if "RUNNING" in phases:
            ExperimentsTable.objects.filter(id=experiment.id).update(
                status=StatusType.RUNNING.value
            )
        elif "STOPPED" in phases and all(
            p in ("STOPPED", "COMPLETED", "FAILED", "CANCELLED") for p in phases
        ):
            ExperimentsTable.objects.filter(id=experiment.id).update(
                status=StatusType.CANCELLED.value
            )
