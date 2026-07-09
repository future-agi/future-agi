import uuid

import structlog
from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from model_hub.models.choices import DatasetSourceChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.project import Project
from tracer.serializers.dataset import (
    AddToExistingDatasetObserveSerializer,
    AddToNewDatasetObserveSerializer,
    ObserveDatasetSerializer,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.tasks import CHUNK_SIZE, process_spans_chunk_task

logger = structlog.get_logger(__name__)

try:
    from ee.usage.utils.usage_entries import check_if_dataset_creation_is_allowed
except ImportError:
    check_if_dataset_creation_is_allowed = None


class DatasetView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = ObserveDatasetSerializer
    # TH-4667 (ai_tools bridge): get_queryset honors a `name` query param
    # (name__icontains below) — the bridged list_datasets tool advertises
    # `search` and remaps it onto `name`.
    # See ai_tools.drf_bridge._detect_collection_params.
    mcp_list_params = {"search": "name"}

    def get_queryset(self):
        dataset_id = self.kwargs.get("pk")
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        if dataset_id:
            queryset = queryset.filter(id=dataset_id)

        # Filter by name if provided
        name = self.request.query_params.get("name")
        if name:
            queryset = queryset.filter(name__icontains=name)

        return queryset

    @action(detail=False, methods=["post"])
    def add_to_new_dataset(self, request, *args, **kwargs):
        try:
            serializer = AddToNewDatasetObserveSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            span_ids = serializer.validated_data.get("span_ids")
            trace_ids = serializer.validated_data.get("trace_ids")
            mapping_config = serializer.validated_data.get("mapping_config")
            new_dataset_name = serializer.validated_data.get("new_dataset_name")
            select_all = serializer.validated_data.get("select_all", False)
            project = serializer.validated_data.get("project")

            org = _request_organization(request)
            workspace = _request_workspace(request)
            if not project and not select_all:
                project = _project_id_from_selected_ids(
                    trace_ids, span_ids, org, workspace
                )

            if not project:
                raise ValueError("Project id cannot be null")

            if not Project.no_workspace_objects.filter(
                _workspace_scope_q("workspace", workspace, org),
                id=project,
                organization=org,
            ).exists():
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

            span_id_list: list[str] = []
            if select_all:
                span_id_list = _span_ids_for_select_all(
                    request, project, trace_ids, span_ids
                )

            elif trace_ids and len(trace_ids) > 0:
                span_id_list = _root_span_ids_for_trace_ids(
                    request, trace_ids, project_id=project
                )
            elif span_ids and len(span_ids) > 0:
                span_id_list = _span_ids_for_span_ids(
                    request, span_ids, project_id=project
                )
            else:
                raise ValueError("No trace or span ids provided")

            # Creating Dataset
            dataset = create_new_dataset(
                new_dataset_name,
                org,
                workspace,
                str(request.user.id),
            )
            column_span_mapping = create_new_columns(dataset, mapping_config)

            # Submit batch tasks asynchronously - returns nothing
            create_new_cells(
                span_id_list,
                dataset,
                column_span_mapping,
                project_id=project,
                org_id=org.id,
            )

            return self._gm.success_response(
                {
                    "dataset_id": str(dataset.id),
                    "dataset_name": dataset.name,
                    "status": "processing",
                    "message": "Dataset creation started. Data is being processed in background.",
                }
            )

        except (ValidationError, ValueError) as e:
            logger.exception(f"Error in creating dataset observe:  {str(e)}")
            return self._gm.bad_request(f"Error creating the dataset observe {str(e)}")

        except Exception as e:
            logger.exception(f"Error in creating dataset observe:  {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error creating the dataset observe {str(e)}"
            )

    @action(detail=False, methods=["post"])
    def add_to_existing_dataset(self, request, *args, **kwargs):
        try:
            serializer = AddToExistingDatasetObserveSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            span_ids = serializer.validated_data.get("span_ids")
            trace_ids = serializer.validated_data.get("trace_ids")
            mapping_config = serializer.validated_data.get("mapping_config") or []
            new_mapping_config = (
                serializer.validated_data.get("new_mapping_config") or []
            )
            dataset_id = serializer.validated_data.get("dataset_id")
            select_all = serializer.validated_data.get("select_all", False)
            project = serializer.validated_data.get("project")
            org = _request_organization(request)
            workspace = _request_workspace(request)
            try:
                dataset = Dataset.no_workspace_objects.get(
                    _workspace_scope_q("workspace", workspace, org),
                    id=dataset_id,
                    organization=org,
                    deleted=False,
                )
            except Dataset.DoesNotExist:
                logger.exception(f"Dataset with id {dataset_id} does not exist.")
                return self._gm.bad_request(get_error_message("DATASET_NOT_FOUND"))

            if (
                project
                and not Project.no_workspace_objects.filter(
                    _workspace_scope_q("workspace", workspace, org),
                    id=project,
                    organization=org,
                ).exists()
            ):
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

            if not project and not select_all:
                project = _project_id_from_selected_ids(
                    trace_ids, span_ids, org, workspace
                )
                if not project:
                    raise ValueError("Project id cannot be null")

            span_id_list: list[str] = []
            if select_all:
                span_id_list = _span_ids_for_select_all(
                    request, project, trace_ids, span_ids
                )

            elif trace_ids and len(trace_ids) > 0:
                span_id_list = _root_span_ids_for_trace_ids(
                    request, trace_ids, project_id=project
                )
            elif span_ids and len(span_ids) > 0:
                span_id_list = _span_ids_for_span_ids(
                    request, span_ids, project_id=project
                )
            else:
                raise ValueError("No trace or span ids provided")

            columns_to_span_fields = []
            column_to_span_dict = {}

            for obj in mapping_config:
                try:
                    column_name = obj.get("col_name")
                    span_field = obj.get("span_field") or column_name
                    column = Column.objects.get(
                        name=column_name, dataset=dataset, deleted=False
                    )
                    columns_to_span_fields.append(
                        {"column": column, "span_field": span_field}
                    )
                    column_to_span_dict[column_name] = span_field
                except Column.DoesNotExist as e:
                    logger.exception(f"Column with name {column_name} does not exist.")
                    raise ValueError(
                        f"Column with name {column_name} does not exist."
                    ) from e

            if new_mapping_config and len(new_mapping_config) > 0:
                column_span_mapping = create_new_columns(dataset, new_mapping_config)
                if column_span_mapping and len(column_span_mapping) > 0:
                    columns_to_span_fields.extend(column_span_mapping)
                    for item in column_span_mapping:
                        column_to_span_dict[item.get("column").name] = item.get(
                            "span_field"
                        )

            columns = Column.no_workspace_objects.filter(dataset=dataset, deleted=False)

            for column in columns:
                if column.name not in column_to_span_dict:
                    columns_to_span_fields.append(
                        {"column": column, "span_field": None}
                    )

            # Submit batch tasks asynchronously - returns nothing
            create_new_cells(
                span_id_list,
                dataset,
                columns_to_span_fields,
                project_id=project,
                org_id=org.id,
            )

            return self._gm.success_response(
                {
                    "dataset_id": str(dataset.id),
                    "status": "processing",
                    "message": "Data is being added to existing dataset in background.",
                }
            )

        except (ValidationError, ValueError) as e:
            logger.exception(f"Error in adding to existing dataset:  {str(e)}")
            return self._gm.bad_request(f"Error adding to existing dataset {str(e)}")

        except Exception as e:
            logger.exception(f"Error in adding to existing dataset: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error adding to existing dataset {str(e)}"
            )


def _request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _request_workspace(request):
    return getattr(request, "workspace", None)


def _workspace_scope_q(field_name, workspace, organization):
    if not workspace:
        return Q()

    if getattr(workspace, "is_default", False):
        return (
            Q(**{field_name: workspace})
            | Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization": organization,
                }
            )
            | Q(**{f"{field_name}__isnull": True})
        )

    return Q(**{field_name: workspace})


def _project_id_from_selected_ids(trace_ids, span_ids, organization, workspace):
    if trace_ids:
        with get_reader() as reader:
            spans = reader.roots_by_trace_ids(
                [str(trace_id) for trace_id in trace_ids],
                org_id=str(organization.id),
            )
        return _first_allowed_project_id(
            [span.project_id for span in spans], organization, workspace
        )

    if span_ids:
        with get_reader() as reader:
            spans = reader.list_by_ids(
                [str(span_id) for span_id in span_ids],
                org_id=str(organization.id),
            )
        return _first_allowed_project_id(
            [span.project_id for span in spans], organization, workspace
        )

    return None


def _first_allowed_project_id(project_ids, organization, workspace):
    ids = [str(project_id) for project_id in project_ids if project_id]
    if not ids:
        return None
    return (
        Project.no_workspace_objects.filter(
            _workspace_scope_q("workspace", workspace, organization),
            id__in=ids,
            organization=organization,
        )
        .values_list("id", flat=True)
        .first()
    )


def _allowed_project_id_strings(project_ids, organization, workspace):
    ids = [str(project_id) for project_id in project_ids if project_id]
    if not ids:
        return set()
    return {
        str(project_id)
        for project_id in Project.no_workspace_objects.filter(
            _workspace_scope_q("workspace", workspace, organization),
            id__in=ids,
            organization=organization,
        ).values_list("id", flat=True)
    }


def _root_span_ids_for_trace_ids(request, trace_ids, project_id=None):
    trace_id_list = [str(trace_id) for trace_id in trace_ids or []]
    if not trace_id_list:
        return []

    org = _request_organization(request)
    workspace = _request_workspace(request)
    with get_reader() as reader:
        ch_spans = reader.roots_by_trace_ids(
            trace_id_list,
            project_id=str(project_id) if project_id else None,
            org_id=None if project_id else str(org.id),
        )

    if not project_id:
        allowed_project_ids = _allowed_project_id_strings(
            [span.project_id for span in ch_spans], org, workspace
        )
        ch_spans = [
            span for span in ch_spans if str(span.project_id) in allowed_project_ids
        ]

    if ch_spans:
        return [str(span.id) for span in ch_spans]

    return []


def _span_ids_for_span_ids(request, span_ids, project_id=None):
    span_id_list = [str(span_id) for span_id in span_ids or []]
    if not span_id_list:
        return []

    org = _request_organization(request)
    workspace = _request_workspace(request)
    with get_reader() as reader:
        ch_spans = reader.list_by_ids(
            span_id_list,
            project_id=str(project_id) if project_id else None,
            org_id=None if project_id else str(org.id),
        )

    if not project_id:
        allowed_project_ids = _allowed_project_id_strings(
            [span.project_id for span in ch_spans], org, workspace
        )
        ch_spans = [
            span for span in ch_spans if str(span.project_id) in allowed_project_ids
        ]

    ch_span_ids = {str(span.id) for span in ch_spans}
    if ch_span_ids:
        return [span_id for span_id in span_id_list if span_id in ch_span_ids]

    return []


def _span_ids_for_select_all(request, project_id, trace_ids, span_ids):
    if not project_id:
        return []

    org = _request_organization(request)
    with get_reader() as reader:
        if trace_ids is not None:
            ch_span_ids = reader.root_ids_by_project(
                str(project_id),
                org_id=str(org.id),
                exclude_trace_ids=[str(trace_id) for trace_id in trace_ids],
            )
        elif span_ids is not None:
            ch_span_ids = reader.ids_by_project(
                str(project_id),
                org_id=str(org.id),
                exclude_ids=[str(span_id) for span_id in span_ids],
            )
        else:
            ch_span_ids = []

    return ch_span_ids


def create_new_dataset(new_dataset_name, organization, workspace, user_id):
    if Dataset.no_workspace_objects.filter(
        _workspace_scope_q("workspace", workspace, organization),
        name=new_dataset_name,
        organization=organization,
        deleted=False,
    ).exists():
        raise ValueError(get_error_message("DATASET_EXIST_IN_ORG"))

    if (
        check_if_dataset_creation_is_allowed is not None
        and not check_if_dataset_creation_is_allowed(organization)
    ):
        raise ValueError(get_error_message("DATASET_CREATE_LIMIT_REACHED"))

    return Dataset.no_workspace_objects.create(
        id=uuid.uuid4(),
        name=new_dataset_name,
        organization=organization,
        workspace=workspace,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        source=DatasetSourceChoices.OBSERVE.value,
        user_id=user_id,
    )


def create_new_columns(dataset, mapping_config):
    if not isinstance(mapping_config, list):
        raise ValueError("Mapping config must be a list")

    columns_to_create = []
    column_order = []
    column_config = {}
    new_columns = []
    column_span_mapping = []
    column_span_mapping_dict = {}

    for obj in mapping_config:
        new_col_name = obj.get("col_name")
        span_col_name = obj.get("span_field") or new_col_name
        new_col_data_type = obj.get("data_type")

        try:
            column = Column.objects.get(
                name=new_col_name, dataset=dataset, deleted=False
            )
            column_span_mapping.append({"column": column, "span_field": span_col_name})
        except Column.DoesNotExist:
            column = Column(
                id=uuid.uuid4(),
                name=new_col_name,
                data_type=new_col_data_type,
                source=SourceChoices.OTHERS.value,
                dataset=dataset,
            )
            columns_to_create.append(column)
            column_order.append(str(column.id))
            column_config[str(column.id)] = {"is_visible": True, "is_frozen": None}
            column_span_mapping_dict[new_col_name] = span_col_name

    if len(columns_to_create) > 0:
        new_columns = Column.objects.bulk_create(columns_to_create)

    for column in new_columns:
        column_span_mapping.append(
            {
                "column": column,
                "span_field": column_span_mapping_dict.get(column.name, None),
            }
        )

    existing_column_order = dataset.column_order or []
    existing_column_config = dataset.column_config or {}

    if len(column_order) > 0:
        existing_column_order.extend(column_order)

    if len(column_config) > 0:
        existing_column_config.update(column_config)

    dataset.column_order = existing_column_order
    dataset.column_config = existing_column_config
    dataset.save(update_fields=["column_order", "column_config"])

    return column_span_mapping


def _submit_or_run_sync(
    batch,
    dataset_id,
    column_span_mapping_data,
    *,
    project_id=None,
    org_id=None,
):
    """Submit task via Temporal, fall back to synchronous execution."""
    try:
        process_spans_chunk_task.delay(
            batch,
            dataset_id,
            column_span_mapping_data,
            project_id,
            org_id,
        )
    except Exception:
        logger.warning(
            "temporal_submit_failed_running_sync",
            dataset_id=dataset_id,
            batch_size=len(batch),
        )
        # Fall back to synchronous execution
        process_spans_chunk_task(
            batch,
            dataset_id,
            column_span_mapping_data,
            project_id,
            org_id,
        )


def create_new_cells(
    span_ids,
    dataset,
    column_span_mapping,
    *,
    project_id=None,
    org_id=None,
):
    """Submit Celery/Temporal batches of span ids for cell processing.

    CH25 migration (D-027): callers used to pass an
    ``ObservationSpan.objects.filter(...)`` queryset; the body relied on
    ``.count()`` + ``.values_list("id", flat=True).iterator()``. Spans
    now live in ClickHouse — the caller materializes ids via the CH
    reader (or via a PG pre-validation step for the select_all + exclude
    paths that need exclusion semantics) and hands a plain
    ``list[str]`` to this function.

    Args:
        span_ids: iterable of span id strings (uuid). The caller is
            responsible for tenant scoping (we no longer apply
            ``project__organization=org`` here — the function trusts the
            list it received).
        dataset: ``Dataset`` model instance.
        column_span_mapping: list of ``{column, span_field}`` dicts.
    """
    span_ids_list = [str(sid) for sid in span_ids or []]
    if len(span_ids_list) == 0:
        raise ValueError("No observation spans provided")

    if len(column_span_mapping) == 0:
        raise ValueError("No column span mapping provided")

    # Prepare serializable column mapping
    # Send both column_id and column_name for fallback
    column_span_mapping_data = [
        {
            "column_id": str(item["column"].id),
            "column_name": item["column"].name,
            "span_field": item["span_field"],
        }
        for item in column_span_mapping
    ]

    # Split into batches
    batch_size = CHUNK_SIZE
    total_batches = (len(span_ids_list) + batch_size - 1) // batch_size
    batch: list[str] = []

    for span_id in span_ids_list:
        batch.append(span_id)

        if len(batch) >= CHUNK_SIZE:
            _submit_or_run_sync(
                batch,
                str(dataset.id),
                column_span_mapping_data,
                project_id=project_id,
                org_id=org_id,
            )
            batch = []

    # Process remaining
    if batch:
        _submit_or_run_sync(
            batch,
            str(dataset.id),
            column_span_mapping_data,
            project_id=project_id,
            org_id=org_id,
        )

    logger.info(
        f"dataset_creation_tasks_submitted: dataset_id={dataset.id}, "
        f"total_spans={len(span_ids_list)}, total_batches={total_batches}, "
        f"batch_size={batch_size}"
    )

    # Returns nothing - tasks run independently in background
