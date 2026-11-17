"""One saved example through experiment admission and native scoring, without writes."""

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_dataset import Column, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.services.eval_group import filter_eval_templates_for_group_scope
from model_hub.services.experiment_eval_pins import (
    prepare_experiment_eval,
    resolve_eval_entries,
)
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.native_run_binding import (
    native_evaluator_version,
    native_workspace,
)
from model_hub.utils.experiment_reference_scope import validate_eval_references
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace,
    scoped_dataset_queryset,
)
from tfc.utils.general_methods import GeneralMethods


def preview_evaluation(request):
    from model_hub.views.eval_runner import NativeEvaluationRunner

    gm = GeneralMethods()
    data = request.validated_data["experiment_preview"]
    try:
        dataset = scoped_dataset_queryset(request).get(pk=data["dataset_id"])
        organization = request_organization(request)
        workspace = request_workspace(request)
        workspace = native_workspace(
            dataset, workspace.pk if workspace else None, actor=request.user
        )
        item = (
            DatasetVersionItem.objects.select_related("dataset_version")
            .filter(
                pk=data["version_item_id"],
                deleted=False,
                dataset_version_id=data["dataset_version_id"],
                dataset_version__dataset=dataset,
                dataset_version__deleted=False,
                dataset_version__organization_id=dataset.organization_id,
                dataset_version__workspace_id=dataset.workspace_id,
            )
            .first()
        )
        if item is None:
            raise ValueError("Saved version item is unavailable in this dataset.")
        row = Row.no_workspace_objects.filter(
            pk=item.legacy_row_id, dataset=dataset, deleted=False
        ).first()
        values = item.input.get("values") if isinstance(item.input, dict) else None
        if row is None or not isinstance(values, dict):
            raise ValueError("Saved item has no available source row or values.")
        entry = data["evaluator"]
        validate_eval_references([entry], organization, workspace)
        template, pin = resolve_eval_entries(
            [entry],
            organization,
            accessible_templates=filter_eval_templates_for_group_scope(
                EvalTemplate.no_workspace_objects.all(), organization, workspace
            ),
        )[0]
        metric = UserEvalMetric(
            **prepare_experiment_eval(entry, template, pin),
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            user=request.user,
        )
        version = native_evaluator_version(
            metric, dataset, workspace.pk if workspace else None
        )
        runner = NativeEvaluationRunner.for_preview(
            dataset=dataset,
            metric=metric,
            version=version,
            workspace=workspace,
            column=Column(name=data["output_name"], data_type=data["output_type"]),
        )
        columns = [
            {
                "source_id": str(c.id),
                "snapshot_id": str(c.id),
                "name": c.name,
                "data_type": c.data_type,
            }
            for c in Column.no_workspace_objects.filter(dataset=dataset, deleted=False)
        ]
        result = runner.evaluate_native_row(
            row.id,
            values=values,
            columns=columns,
            output=data["output"],
            messages=data["messages"],
        )
        return gm.success_response(result)
    except (ValueError, Http404, ObjectDoesNotExist, RunWriteError) as exc:
        return gm.bad_request(str(exc))
