"""Explicit dataset scope for optimizer workers, which have no request context."""

from django.db.models import Q

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import UserEvalMetric


def _workspace_scope(dataset, field):
    # Match the API's legacy NULL/default-workspace equivalence, but never a
    # different private workspace. Organization is checked separately below.
    if dataset.workspace_id is None or dataset.workspace.is_default:
        return Q(**{f"{field}__isnull": True}) | Q(
            **{
                f"{field}__is_default": True,
                f"{field}__organization_id": dataset.organization_id,
            }
        )
    return Q(**{f"{field}_id": dataset.workspace_id})


def optimization_metrics(optimization):
    """Do not trust historical M2M links as tenant authorization."""
    dataset = optimization.dataset
    return (
        UserEvalMetric.no_workspace_objects.filter(
            _workspace_scope(dataset, "workspace"),
            id__in=optimization.user_eval_template_ids.values_list("id", flat=True),
            dataset=dataset,
            dataset__deleted=False,
            organization_id=dataset.organization_id,
            template__deleted=False,
        )
        .filter(
            (
                Q(template__organization_id=dataset.organization_id)
                & _workspace_scope(dataset, "template__workspace")
            )
            | Q(
                template__owner=OwnerChoices.SYSTEM.value,
                template__organization__isnull=True,
                template__workspace__isnull=True,
            )
        )
        .select_related("template")
    )
