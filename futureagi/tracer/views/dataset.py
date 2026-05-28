import uuid

import structlog
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

logger = structlog.get_logger(__name__)
from model_hub.models.choices import DatasetSourceChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.develop_dataset import DatasetSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.serializers.dataset import (
    AddToExistingDatasetObserveSerializer,
    AddToNewDatasetObserveSerializer,
)
from tracer.tasks import CHUNK_SIZE, process_spans_chunk_task
try:
    from ee.usage.utils.usage_entries import check_if_dataset_creation_is_allowed
except ImportError:
    check_if_dataset_creation_is_allowed = None


class DatasetView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = DatasetSerializer

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

            # Derive project from the target trace(s)/span(s) when the client
            # didn't pass one — avoids forcing the frontend to thread a
            # project id through every call site. Org scoping on the lookup
            # prevents cross-org leakage. `select_all` still requires project
            # explicitly since there's no bound otherwise.
            org = (
                getattr(request, "organization", None) or request.user.organization
            )
            if not project and not select_all:
                if trace_ids:
                    project = (
                        Trace.objects.filter(
                            id__in=trace_ids, project__organization=org
                        )
                        .values_list("project_id", flat=True)
                        .first()
                    )
                elif span_ids:
                    project = (
                        ObservationSpan.objects.filter(
                            id__in=span_ids, project__organization=org
                        )
                        .values_list("project_id", flat=True)
                        .first()
                    )

            if not project:
                raise ValueError("Project id cannot be null")

            if select_all:
                if trace_ids is not None:
                    observation_spans = ObservationSpan.objects.filter(
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                        parent_span_id__isnull=True,
                        project_id=project,
                    ).exclude(
                        trace_id__in=trace_ids,
                    )
                elif span_ids is not None:
                    observation_spans = ObservationSpan.objects.filter(
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                        project_id=project,
                    ).exclude(
                        id__in=span_ids,
                    )

            elif trace_ids and len(trace_ids) > 0:
                observation_spans = ObservationSpan.objects.filter(
                    project_id=project,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                    trace_id__in=trace_ids,
                    parent_span_id__isnull=True,
                )
            elif span_ids and len(span_ids) > 0:
                observation_spans = ObservationSpan.objects.filter(
                    project_id=project,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                    id__in=span_ids,
                )
            else:
                raise ValueError("No trace or span ids provided")

            # Creating Dataset
            dataset = create_new_dataset(
                new_dataset_name,
                getattr(request, "organization", None) or request.user.organization,
                str(request.user.id),
            )
            column_span_mapping = create_new_columns(dataset, mapping_config)

            # Submit batch tasks asynchronously - returns nothing
            create_new_cells(observation_spans, dataset, column_span_mapping)

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
            mapping_config = serializer.validated_data.get("mapping_config")
            new_mapping_config = serializer.validated_data.get("new_mapping_config")
            dataset_id = serializer.validated_data.get("dataset_id")
            select_all = serializer.validated_data.get("select_all", False)
            project = serializer.validated_data.get("project")

            try:
                dataset = Dataset.objects.get(
                    id=dataset_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    deleted=False,
                )
            except Dataset.DoesNotExist:
                logger.exception(f"Dataset with id {dataset_id} does not exist.")
                return self._gm.bad_request(get_error_message("DATASET_NOT_FOUND"))

            if select_all:
                if trace_ids is not None:
                    observation_spans = ObservationSpan.objects.filter(
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                        parent_span_id__isnull=True,
                        project_id=project,
                    ).exclude(
                        trace_id__in=trace_ids,
                    )
                elif span_ids is not None:
                    observation_spans = ObservationSpan.objects.filter(
                        project__organization=getattr(request, "organization", None)
                        or request.user.organization,
                        project_id=project,
                    ).exclude(
                        id__in=span_ids,
                    )
            elif trace_ids and len(trace_ids) > 0:
                observation_spans = ObservationSpan.objects.filter(
                    trace_id__in=trace_ids,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                    parent_span_id__isnull=True,
                )
            elif span_ids and len(span_ids) > 0:
                observation_spans = ObservationSpan.objects.filter(
                    id__in=span_ids,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
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

            columns = Column.objects.filter(dataset=dataset, deleted=False)

            for column in columns:
                if column.name not in column_to_span_dict:
                    columns_to_span_fields.append(
                        {"column": column, "span_field": None}
                    )

            # Submit batch tasks asynchronously - returns nothing
            create_new_cells(observation_spans, dataset, columns_to_span_fields)

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


def create_new_dataset(new_dataset_name, organization, user_id):
    if Dataset.objects.filter(
        name=new_dataset_name, organization=organization, deleted=False
    ).exists():
        raise ValueError(get_error_message("DATASET_EXIST_IN_ORG"))

    if check_if_dataset_creation_is_allowed is not None and not check_if_dataset_creation_is_allowed(organization):
        raise ValueError(get_error_message("DATASET_CREATE_LIMIT_REACHED"))

    dataset_id = uuid.uuid4()
    dataset_serializer = DatasetSerializer(
        data={
            "id": dataset_id,
            "name": new_dataset_name,
            "organization": organization.id,
            "model_type": ModelTypes.GENERATIVE_LLM.value,
            "source": DatasetSourceChoices.OBSERVE.value,
            "user": user_id,
        }
    )

    if dataset_serializer.is_valid():
        dataset = dataset_serializer.save()
        return dataset
    else:
        raise ValueError(parse_serialized_errors(dataset_serializer))


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


def _submit_or_run_sync(batch, dataset_id, column_span_mapping_data):
    """Submit task via Temporal, fall back to synchronous execution."""
    try:
        process_spans_chunk_task.delay(
            batch,
            dataset_id,
            column_span_mapping_data,
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
        )


def create_new_cells(observation_spans, dataset, column_span_mapping):

    # Use count() to avoid loading the QuerySet
    spans_count = observation_spans.count()
    if spans_count == 0:
        raise ValueError("No observation spans provided")

    if len(column_span_mapping) == 0:
        raise ValueError("No column span mapping provided")

    # Fetch only IDs without loading full objects
    span_ids = list(observation_spans.values_list("id", flat=True))

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
    total_batches = (len(span_ids) + batch_size - 1) // batch_size
    batch = []

    for span_id in observation_spans.values_list("id", flat=True).iterator():
        batch.append(str(span_id))

        if len(batch) >= CHUNK_SIZE:
            _submit_or_run_sync(batch, str(dataset.id), column_span_mapping_data)
            batch = []

    # Process remaining
    if batch:
        _submit_or_run_sync(batch, str(dataset.id), column_span_mapping_data)

    logger.info(
        f"dataset_creation_tasks_submitted: dataset_id={dataset.id}, total_spans={len(span_ids)}, total_batches={total_batches}, batch_size={batch_size}"
    )

    # Returns nothing - tasks run independently in background
