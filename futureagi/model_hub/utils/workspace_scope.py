import uuid

from django.db.models import F, Q

from model_hub.models.ai_model import AIModel
from model_hub.models.api_key import SecretModel
from model_hub.models.column_config import ColumnConfig
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Cell, Column, Dataset, KnowledgeBaseFile
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.optimize_dataset import OptimizeDataset
from tfc.middleware.workspace_context import workspace_scope_is_empty
from tfc.utils.workspace_scope import empty_workspace_filter
from tfc.utils.tenant_scope import (
    caller_organization,
    caller_workspace,
    workspace_scope_q,
)


def request_organization(request=None):
    """The caller's organization (``tfc.utils.tenant_scope.caller_organization``)."""
    return caller_organization(request)


def request_workspace(request=None):
    """The caller's workspace (``tfc.utils.tenant_scope.caller_workspace``)."""
    return caller_workspace(request)


def request_workspace_filter(request=None, field_name="workspace"):
    """Keep central tenancy semantics behind the authenticated-empty guard."""
    return explicit_workspace_filter(request_workspace(request), field_name)


def eval_picker_workspace_filter(request=None, field_name="workspace"):
    """User eval templates the eval picker offers in the caller's workspace.

    The picker (``model_hub.utils.eval_list.build_eval_list_queryset``) offers
    the workspace's own user templates and those with no workspace, in every
    workspace. A lookup of a template picked there uses the same rule; the
    caller still adds the organization. An authenticated empty scope matches
    nothing, and no workspace narrows nothing, as in
    :func:`request_workspace_filter`.
    """
    if workspace_scope_is_empty():
        return empty_workspace_filter()
    workspace = request_workspace(request)
    if workspace is None:
        return Q()
    return Q(**{field_name: workspace}) | Q(**{f"{field_name}__isnull": True})


def _without_organization(request, organization):
    """A request whose caller resolves to no organization owns no rows."""
    return request is not None and organization is None


def scoped_dataset_queryset(request=None):
    queryset = Dataset.no_workspace_objects.filter(
        request_workspace_filter(request),
        deleted=False,
    )
    organization = request_organization(request)
    if _without_organization(request, organization):
        return queryset.none()
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset


def scoped_dataset_version_queryset(request=None):
    """Return dataset versions visible in the current org/workspace context."""

    if request_organization(request) is None:
        return DatasetVersion.no_workspace_objects.none()
    queryset = DatasetVersion.no_workspace_objects.filter(
        dataset__in=scoped_dataset_queryset(request),
        deleted=False,
    )
    return queryset


def scoped_column_queryset(request=None):
    queryset = Column.no_workspace_objects.filter(
        request_workspace_filter(request, field_name="dataset__workspace"),
        dataset__deleted=False,
        deleted=False,
    )
    organization = request_organization(request)
    if _without_organization(request, organization):
        return queryset.none()
    if organization is not None:
        queryset = queryset.filter(dataset__organization=organization)
    return queryset


def scoped_cell_queryset(request):
    """Cells inherit tenancy from their row; nullable duplicate links grant nothing."""
    organization = request_organization(request)
    if organization is None:
        return Cell.objects.none()
    datasets = scoped_dataset_queryset(request).filter(organization=organization)
    return Cell.objects.filter(
        Q(dataset__isnull=True) | Q(dataset_id=F("row__dataset_id")),
        row__dataset_id__in=datasets.values("id"),
        column__dataset_id=F("row__dataset_id"),
        row__deleted=False,
        column__deleted=False,
        deleted=False,
    )


def explicit_workspace_filter(workspace, field_name="workspace"):
    """Central workspace rule; authenticated empty scope is never a wildcard."""
    if workspace_scope_is_empty():
        return empty_workspace_filter()
    return workspace_scope_q(workspace, field_name)


def legacy_dataset_filter(workspace, field_name="workspace"):
    """Datasets in ``workspace``; the default workspace also gets its
    organization's legacy datasets that have no workspace (seeded demo data,
    datasets older than workspaces, and snapshots of them).

    A dataset with a workspace matches only that exact workspace, and a
    non-default workspace never matches a dataset with no workspace. The caller
    adds the organization filter. No workspace, or an authenticated empty
    scope, matches nothing.
    """
    if workspace is None or workspace_scope_is_empty():
        return Q(pk__in=[])
    scope = Q(**{field_name: workspace})
    if getattr(workspace, "is_default", False):
        scope |= Q(**{f"{field_name}__isnull": True})
    return scope


def scoped_knowledge_base_queryset(request=None):
    queryset = KnowledgeBaseFile.no_workspace_objects.filter(
        request_workspace_filter(request),
        deleted=False,
    )
    organization = request_organization(request)
    if _without_organization(request, organization):
        return queryset.none()
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset


def knowledge_base_queryset_for(organization, workspace=None):
    """Knowledge bases of ``organization`` visible from ``workspace``, by the
    managers' workspace rule. No organization means none."""
    if organization is None:
        return KnowledgeBaseFile.no_workspace_objects.none()
    return KnowledgeBaseFile.no_workspace_objects.filter(
        explicit_workspace_filter(workspace), organization=organization, deleted=False
    )


def knowledge_base_id_for(organization, workspace, kb_id):
    """``kb_id`` as a UUID when it names one of ``organization``'s knowledge
    bases visible from ``workspace``, else ``None`` (also for a non-UUID)."""
    try:
        kb_uuid = uuid.UUID(str(kb_id))
    except (TypeError, ValueError, AttributeError):
        return None
    if knowledge_base_queryset_for(organization, workspace).filter(id=kb_uuid).exists():
        return kb_uuid
    return None


def knowledge_base_in_scope(request, kb_id):
    """Whether ``kb_id`` names a knowledge base in the caller's scope.

    An id that is not a UUID fails like an id that does not exist.
    """
    try:
        kb_uuid = uuid.UUID(str(kb_id))
    except (TypeError, ValueError, AttributeError):
        return False
    return scoped_knowledge_base_queryset(request).filter(id=kb_uuid).exists()


def organization_secret_key(secret_id, organization_id):
    """The key of ``organization_id``'s secret ``secret_id``.

    Another organization's secret fails like an unknown id
    (``SecretModel.DoesNotExist``).
    """
    return SecretModel.objects.get(
        id=secret_id, organization_id=organization_id
    ).actual_key


def scoped_user_eval_metric_queryset(request=None):
    queryset = UserEvalMetric.no_workspace_objects.filter(
        request_workspace_filter(request, field_name="workspace"),
        request_workspace_filter(request, field_name="dataset__workspace"),
        dataset__deleted=False,
        deleted=False,
    )
    organization = request_organization(request)
    if _without_organization(request, organization):
        return queryset.none()
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
    if _without_organization(request, organization):
        return queryset.none()
    if organization is not None:
        queryset = queryset.filter(dataset__organization=organization)
    return queryset


def scoped_ai_model_queryset(request=None):
    queryset = AIModel.objects.filter(request_workspace_filter(request))
    organization = request_organization(request)
    if _without_organization(request, organization):
        return queryset.none()
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
    # The direct-org OR branch below must not bypass an empty landing.
    if workspace_scope_is_empty():
        return queryset.none()

    organization = request_organization(request)
    if _without_organization(request, organization):
        return queryset.none()
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
    if _without_organization(request, organization):
        return None
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    return queryset.order_by("-updated_at").first()
