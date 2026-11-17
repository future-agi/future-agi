"""Metadata-only PUT path; no submission, rerun, or evidence mutation."""

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from model_hub.models.experiments import ExperimentsTable
from model_hub.serializers.experiments import ExperimentDetailV2Serializer
from model_hub.utils.workspace_scope import request_workspace, scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


@primary_required
@transaction.atomic
def update_experiment_metadata(request, experiment_id):
    """Serialize same-dataset renames with V2 creation using dataset-first locks."""
    datasets = scoped_dataset_queryset(request)
    experiments = ExperimentsTable.no_workspace_objects.filter(
        deleted=False, dataset__in=datasets
    )
    dataset_id = experiments.filter(pk=experiment_id).values_list(
        "dataset_id", flat=True
    ).first()
    if dataset_id is None:
        return _gm.not_found("Experiment not found")

    workspace = request_workspace(request)
    if workspace is None or not request.user.can_write_to_workspace(workspace):
        return Response(
            build_error_envelope(
                "You do not have permission to edit experiments in this workspace.",
                status_code=403,
            ),
            status=403,
        )

    # Lock a shared parent even when the requested name has no matching row.
    # This is the same order used by V2 create and execution configuration edits.
    dataset = datasets.select_for_update(of=("self",)).filter(pk=dataset_id).first()
    if dataset is None:
        return _gm.not_found("Experiment not found")
    experiment = experiments.select_for_update(of=("self",)).filter(
        pk=experiment_id, dataset=dataset
    ).first()
    if experiment is None:
        return _gm.not_found("Experiment not found")

    data = request.validated_data
    name = data.get("name", experiment.name)
    # Match the existing create/validate-name contract: case-sensitive, per dataset.
    if name != experiment.name and ExperimentsTable.no_workspace_objects.filter(
        dataset=dataset, deleted=False, name=name
    ).exclude(pk=experiment.pk).exists():
        return _gm.bad_request({
            "name": [
                "An experiment with this name already exists in this dataset. "
                "Choose another name."
            ]
        })

    changes = {
        key: data[key] for key in ("name", "description")
        if key in data and data[key] != getattr(experiment, key)
    }
    if changes:
        changes["updated_at"] = timezone.now()
        # Narrow SQL avoids revalidating legacy prompt configs or saving any
        # execution fields. Runs, variants, results and snapshots stay untouched.
        experiments.filter(pk=experiment.pk).update(**changes)
        for key, value in changes.items():
            setattr(experiment, key, value)
    detail = ExperimentDetailV2Serializer(
        experiment,
        context={"organization_id": str(dataset.organization_id), "request": request},
    )
    return _gm.success_response(detail.data)
