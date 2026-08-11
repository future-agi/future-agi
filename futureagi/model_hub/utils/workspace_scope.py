from django.db.models import Q

from model_hub.models.ai_model import AIModel
from model_hub.models.column_config import ColumnConfig
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.optimize_dataset import OptimizeDataset
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_workspace,
)


def request_organization(request=None):
    if request is not None:
        organization = getattr(request, "organization", None)
        if organization is not None:
            return organization
        user = getattr(request, "user", None)
        organization = getattr(user, "organization", None)
        if organization is not None:
            return organization
    return get_current_organization()


def request_workspace(request=None):
    if request is not None:
        workspace = getattr(request, "workspace", None)
        if workspace is not None:
            return workspace
    return get_current_workspace()


def request_workspace_filter(request=None, field_name="workspace"):
    workspace = request_workspace(request)
    if workspace is None:
        return Q()

    if getattr(workspace, "is_default", False):
        scope = Q(**{field_name: workspace})
        organization_id = getattr(workspace, "organization_id", None)
        if organization_id is not None:
            scope |= Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization_id": organization_id,
                }
            )
        scope |= Q(**{f"{field_name}__isnull": True})
        return scope

    return Q(**{field_name: workspace})


def scoped_dataset_queryset(request=None):
    queryset = Dataset.no_workspace_objects.filter(
        request_workspace_filter(request),
        deleted=False,
    )
    organization = request_organization(request)
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset


def scoped_column_queryset(request=None):
    queryset = Column.no_workspace_objects.filter(
        request_workspace_filter(request, field_name="dataset__workspace"),
        dataset__deleted=False,
        deleted=False,
    )
    organization = request_organization(request)
    if organization is not None:
        queryset = queryset.filter(dataset__organization=organization)
    return queryset


def scoped_user_eval_metric_queryset(request=None):
    queryset = UserEvalMetric.no_workspace_objects.filter(
        request_workspace_filter(request, field_name="workspace"),
        request_workspace_filter(request, field_name="dataset__workspace"),
        dataset__deleted=False,
        deleted=False,
    )
    organization = request_organization(request)
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset


def scoped_optimization_queryset(request=None):
    queryset = OptimizationDataset.no_workspace_objects.select_related(
        "dataset",
        "column",
    ).filter(
        request_workspace_filter(request, field_name="dataset__workspace"),
        dataset__deleted=False,
        deleted=False,
    )
    organization = request_organization(request)
    if organization is not None:
        queryset = queryset.filter(dataset__organization=organization)
    return queryset


def scoped_ai_model_queryset(request=None):
    queryset = AIModel.objects.filter(request_workspace_filter(request))
    organization = request_organization(request)
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset


def scoped_optimize_dataset_queryset(request=None):
    queryset = OptimizeDataset.no_workspace_objects.select_related(
        "model",
        "optimizer_model",
        "column__dataset",
        "develop",
        "knowledge_base",
    ).prefetch_related("metrics")

    organization = request_organization(request)
    workspace = request_workspace(request)
    scope = Q()

    if organization is not None:
        direct_scope = Q(organization=organization)
        if workspace is not None:
            direct_scope &= request_workspace_filter(request)
        scope |= direct_scope

        model_scope = Q(model__organization=organization, model__deleted=False)
        model_scope &= request_workspace_filter(request, field_name="model__workspace")
        scope |= model_scope

        optimizer_model_scope = Q(
            optimizer_model__organization=organization,
            optimizer_model__deleted=False,
        )
        optimizer_model_scope &= request_workspace_filter(
            request, field_name="optimizer_model__workspace"
        )
        scope |= optimizer_model_scope

        column_scope = Q(
            column__dataset__organization=organization,
            column__dataset__deleted=False,
            column__deleted=False,
        )
        column_scope &= request_workspace_filter(
            request, field_name="column__dataset__workspace"
        )
        scope |= column_scope

        develop_scope = Q(develop__organization=organization, develop__deleted=False)
        develop_scope &= request_workspace_filter(
            request, field_name="develop__workspace"
        )
        scope |= develop_scope

        knowledge_base_scope = Q(
            knowledge_base__organization=organization,
            knowledge_base__deleted=False,
        )
        knowledge_base_scope &= request_workspace_filter(
            request, field_name="knowledge_base__workspace"
        )
        scope |= knowledge_base_scope
    elif workspace is not None:
        scope |= request_workspace_filter(request)
        scope |= request_workspace_filter(request, field_name="model__workspace")
        scope |= request_workspace_filter(
            request, field_name="optimizer_model__workspace"
        )
        scope |= request_workspace_filter(
            request, field_name="column__dataset__workspace"
        )
        scope |= request_workspace_filter(request, field_name="develop__workspace")
        scope |= request_workspace_filter(
            request, field_name="knowledge_base__workspace"
        )

    if scope:
        queryset = queryset.filter(scope).distinct()

    return queryset


def scoped_column_config_for_identifier(request, table_name, identifier):
    queryset = ColumnConfig.objects.filter(
        table_name=table_name,
        identifier=identifier,
    ).filter(request_workspace_filter(request))
    organization = request_organization(request)
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset.order_by("-updated_at").first()
