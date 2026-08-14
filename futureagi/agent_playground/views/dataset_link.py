import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet

from agent_playground.models.choices import GraphVersionStatus
from agent_playground.models.graph_dataset import GraphDataset
from agent_playground.models.graph_version import GraphVersion
from agent_playground.serializers.contracts import AGENT_PLAYGROUND_ERROR_RESPONSES
from agent_playground.serializers.dataset_link import (
    CellUpdateSerializer,
    ColumnSerializer,
    DeleteRowsSerializer,
    ExecuteRequestSerializer,
)
from agent_playground.services.dataset_bridge import (
    _get_exposed_input_display_names,
    execute_rows,
)
from common.utils.pagination import paginate_queryset
from model_hub.models.develop_dataset import Cell, Column, Row
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

agent_playground_errors = swagger_auto_schema(
    responses=AGENT_PLAYGROUND_ERROR_RESPONSES
)


@method_decorator(name="retrieve", decorator=agent_playground_errors)
@method_decorator(name="create_row", decorator=agent_playground_errors)
@method_decorator(name="delete_rows", decorator=agent_playground_errors)
@method_decorator(name="update_cell", decorator=agent_playground_errors)
@method_decorator(name="execute", decorator=agent_playground_errors)
class GraphDatasetViewSet(GenericViewSet):
    """
    ViewSet for Graph-linked Dataset operations.

    Provides dataset detail, row CRUD, cell update, and batch execution.
    All endpoints are nested under /graphs/{graph_id}/dataset/.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = CellUpdateSerializer  # default; overridden per action
    lookup_url_kwarg = "graph_id"
    _gm = GeneralMethods()

    def get_queryset(self):
        """GraphDataset filtered by user's organization and workspace."""
        organization = self.request.organization
        workspace = self.request.workspace

        queryset = GraphDataset.no_workspace_objects.select_related(
            "graph", "dataset"
        ).filter(
            graph__organization=organization,
            graph__is_template=False,
        )

        if workspace:
            queryset = queryset.filter(graph__workspace=workspace)

        return queryset

    def get_object(self):
        """Get the GraphDataset for the graph_id in the URL."""
        queryset = self.get_queryset()
        return queryset.get(graph_id=self.kwargs["graph_id"])

    def get_serializer_class(self):
        if self.action == "delete_rows":
            return DeleteRowsSerializer
        if self.action == "update_cell":
            return CellUpdateSerializer
        if self.action == "execute":
            return ExecuteRequestSerializer
        return CellUpdateSerializer

    def retrieve(self, request, graph_id=None):
        """
        Get full dataset detail: info, columns, and all rows with cells.

        Accepts an optional ``version_id`` query parameter.  When provided,
        only columns whose names match the exposed input ports of that
        version are returned (and cells are filtered accordingly).  When
        omitted the latest version (by version_number) is used.
        """
        try:
            graph_dataset = self.get_object()
            dataset = graph_dataset.dataset
            graph = graph_dataset.graph

            # Determine which version to use for column filtering
            version_id = request.query_params.get("version_id")
            if version_id:
                version = GraphVersion.no_workspace_objects.get(
                    id=version_id, graph=graph
                )
            else:
                version = (
                    GraphVersion.no_workspace_objects.filter(graph=graph)
                    .order_by("-version_number")
                    .first()
                )

            # Compute visible column names from the version's exposed input ports
            visible_names: set[str] | None = None
            if version:
                visible_names = _get_exposed_input_display_names(version.id)

            all_columns = Column.no_workspace_objects.filter(dataset=dataset).order_by(
                "created_at"
            )

            if visible_names is not None:
                columns = all_columns.filter(name__in=visible_names)
            else:
                columns = all_columns

            visible_column_ids = set(columns.values_list("id", flat=True))
            columns_data = ColumnSerializer(columns, many=True).data

            rows_qs = Row.no_workspace_objects.filter(dataset=dataset).order_by("order")
            page, metadata = paginate_queryset(rows_qs, request)

            rows_data = []
            for row in page:
                cells = Cell.no_workspace_objects.filter(row=row).select_related(
                    "column"
                )
                cells_data = [
                    {
                        "id": cell.id,
                        "column_id": cell.column_id,
                        "value": cell.value,
                    }
                    for cell in cells
                    if cell.column_id in visible_column_ids
                ]
                rows_data.append(
                    {
                        "id": row.id,
                        "order": row.order,
                        "cells": cells_data,
                    }
                )

            return self._gm.success_response(
                {
                    "dataset_id": dataset.id,
                    "dataset_name": dataset.name,
                    "columns": columns_data,
                    "rows": rows_data,
                    "metadata": metadata,
                }
            )
        except GraphDataset.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_DATASET_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("VERSION_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error getting dataset detail", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_GRAPH_DATASET")
            )

    def create_row(self, request, graph_id=None):
        """
        Create a single row with empty cells pre-created for every column.
        """
        try:
            graph_dataset = self.get_object()
            dataset = graph_dataset.dataset

            columns = Column.no_workspace_objects.filter(dataset=dataset)
            if not columns.exists():
                return self._gm.bad_request(get_error_message("NO_COLUMNS_EXIST"))

            max_order = (
                Row.no_workspace_objects.filter(dataset=dataset)
                .order_by("-order")
                .values_list("order", flat=True)
                .first()
            ) or 0

            with transaction.atomic():
                row = Row.no_workspace_objects.create(
                    dataset=dataset,
                    order=max_order + 1,
                )

                cells_data = []
                for column in columns:
                    cell = Cell.no_workspace_objects.create(
                        dataset=dataset,
                        column=column,
                        row=row,
                        value=None,
                    )
                    cells_data.append(
                        {
                            "id": cell.id,
                            "column_id": column.id,
                            "value": cell.value,
                        }
                    )

            return self._gm.create_response(
                {
                    "id": row.id,
                    "order": row.order,
                    "cells": cells_data,
                }
            )
        except GraphDataset.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_DATASET_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error creating dataset row", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_DATASET_ROW")
            )

    def delete_rows(self, request, graph_id=None):
        """
        Bulk delete rows by IDs.
        """
        try:
            graph_dataset = self.get_object()
            dataset = graph_dataset.dataset

            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            select_all = serializer.validated_data.get("select_all", False)
            exclude_ids = serializer.validated_data.get("exclude_ids", [])
            row_ids = serializer.validated_data.get("row_ids", [])

            if select_all:
                rows = Row.no_workspace_objects.filter(dataset=dataset)
                if exclude_ids:
                    rows = rows.exclude(id__in=exclude_ids)
            else:
                rows = Row.no_workspace_objects.filter(id__in=row_ids, dataset=dataset)
                found_ids = set(rows.values_list("id", flat=True))
                missing_ids = [str(rid) for rid in row_ids if rid not in found_ids]
                if missing_ids:
                    return self._gm.not_found(
                        {
                            "message": get_error_message("DATASET_ROWS_NOT_FOUND"),
                            "missing_ids": missing_ids,
                        }
                    )

            total_rows = Row.no_workspace_objects.filter(dataset=dataset).count()
            deleting_count = rows.count()

            if deleting_count >= total_rows:
                return self._gm.bad_request(get_error_message("CANNOT_DELETE_ALL_ROWS"))

            with transaction.atomic():
                now = timezone.now()
                Cell.no_workspace_objects.filter(row__in=rows).update(
                    deleted=True, deleted_at=now
                )
                rows.update(deleted=True, deleted_at=now)

            return self._gm.success_response({"message": "Rows deleted successfully"})
        except GraphDataset.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_DATASET_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error deleting dataset rows", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_DELETE_DATASET_ROWS")
            )

    def update_cell(self, request, graph_id=None, cell_id=None):
        """
        Update a single cell value.
        """
        try:
            graph_dataset = self.get_object()
            dataset = graph_dataset.dataset

            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            cell = Cell.no_workspace_objects.select_related("column").get(
                id=cell_id, dataset=dataset
            )
            cell.value = serializer.validated_data["value"]
            cell.save()

            return self._gm.success_response(
                {
                    "column_id": cell.column_id,
                    "column_name": cell.column.name,
                    "value": cell.value,
                }
            )
        except GraphDataset.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_DATASET_NOT_FOUND"))
        except Cell.DoesNotExist:
            return self._gm.not_found(get_error_message("CELL_NOT_FOUND"))
        except Exception as e:
            logger.exception("Error updating cell", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_DATASET_CELL")
            )

    def execute(self, request, graph_id=None):
        """
        Trigger graph execution for dataset rows using the active graph version.
        """
        try:
            graph_dataset = self.get_object()

            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            row_ids = serializer.validated_data.get("row_ids")
            task_queue = serializer.validated_data.get("task_queue")
            dataset = graph_dataset.dataset

            if row_ids is not None:
                rows = Row.no_workspace_objects.filter(id__in=row_ids, dataset=dataset)
                found_ids = set(rows.values_list("id", flat=True))
                missing_ids = [str(rid) for rid in row_ids if rid not in found_ids]
                if missing_ids:
                    return self._gm.not_found(
                        {
                            "message": get_error_message("DATASET_ROWS_NOT_FOUND"),
                            "missing_ids": missing_ids,
                        }
                    )

            graph_version = GraphVersion.no_workspace_objects.get(
                graph=graph_dataset.graph,
                status=GraphVersionStatus.ACTIVE,
            )

            execution_ids = execute_rows(
                graph_version=graph_version,
                dataset=dataset,
                row_ids=row_ids,
                task_queue=task_queue,
            )

            return self._gm.create_response({"execution_ids": execution_ids})

        except GraphDataset.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_DATASET_NOT_FOUND"))
        except GraphVersion.DoesNotExist:
            return self._gm.not_found(get_error_message("GRAPH_VERSION_NOT_ACTIVE"))
        except ValueError as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error executing dataset rows", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_EXECUTE_DATASET_ROWS")
            )
