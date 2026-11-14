"""Explicit dataset scope for optimizer workers, which have no request context."""

from django.db.models import CharField, F, Q
from django.db.models.functions import Cast

from model_hub.models.choices import FeedbackSourceChoices, OwnerChoices
from model_hub.models.develop_dataset import Column, Row
from model_hub.models.evals_metric import Feedback, UserEvalMetric


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
        .filter(
            Q(pinned_version__isnull=True)
            | Q(pinned_version__eval_template_id=F("template_id"))
        )
        .select_related("template")
    )


def optimization_feedback_ids(dataset, template_id):
    """Return vector identities backed by feedback in this optimizer's dataset."""
    if dataset.deleted:
        return []
    columns = Column.no_workspace_objects.filter(dataset=dataset, deleted=False)
    rows = Row.no_workspace_objects.filter(dataset=dataset, deleted=False)
    feedback = Feedback.no_workspace_objects.filter(
        _workspace_scope(dataset, "workspace"),
        _workspace_scope(dataset, "user_eval_metric__workspace"),
        organization_id=dataset.organization_id,
        eval_template_id=template_id,
        user_eval_metric__dataset=dataset,
        user_eval_metric__organization_id=dataset.organization_id,
        user_eval_metric__template_id=template_id,
        user_eval_metric__deleted=False,
        source=FeedbackSourceChoices.DATASET.value,
        source_id__in=columns.annotate(text_id=Cast("id", CharField())).values(
            "text_id"
        ),
        row_id__in=rows.annotate(text_id=Cast("id", CharField())).values("text_id"),
        deleted=False,
    )
    return [str(feedback_id) for feedback_id in feedback.values_list("id", flat=True)]
