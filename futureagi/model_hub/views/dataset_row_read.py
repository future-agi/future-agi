"""Read one dataset row through the public API.

``GET /model-hub/datasets/{dataset_id}/rows/{row_id}/`` returns the values
get-row-data returns for the row: its id and order, ``row_meta.source`` when
the row was imported from Observe, and under ``cells`` one entry per visible
column, keyed by the column id. It is read-only and scoped like get-row-data:
the dataset must belong to the caller's organization and workspace, and a
deleted or foreign dataset or row is a 404.
"""

import json

import structlog
from django.db import DatabaseError, connection, transaction
from drf_yasg.app_settings import swagger_settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.choices import DatasetSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubErrorResponseSerializer,
)
from model_hub.serializers.dataset_row_read import DatasetRowReadResponseSerializer
from model_hub.services.bounded_dataset_read import (
    BoundedDatasetReadDeadline,
    BoundedDatasetReadDeadlineExceeded,
    BoundedDatasetReadLimitExceeded,
    assert_bounded_projection,
)
from model_hub.services.dataset_table_snapshot import (
    DATASET_TABLE_EXACT_MAX_COLUMNS,
    DatasetTableExactLimitExceeded,
    assert_dataset_table_cells_within_limits,
    assert_dataset_table_response_within_limits,
)
from model_hub.services.row_source_gate import (
    ensure_row_read_route_enabled,
    gate_append_slash,
    row_source_enabled,
)
from model_hub.services.row_sources import attach_row_sources
from model_hub.views.develop_dataset import (
    GetRowDataView,
    _bounded_dataset_error,
    _request_column_queryset,
    _request_dataset_queryset,
    _request_row_queryset,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_EXPERIMENT_SOURCES = (
    SourceChoices.EXPERIMENT.value,
    SourceChoices.EXPERIMENT_EVALUATION.value,
    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
)


def _cell_metadata(value_infos):
    """``(value_infos, response_time_ms, token_count)`` as get-row-data reads them.

    Like get-row-data, a ``value_infos`` that does not parse, or whose metadata
    does not parse, is returned exactly as stored.
    """

    stored = value_infos
    response_time_ms = None
    token_count = None
    try:
        if isinstance(value_infos, str):
            value_infos = json.loads(value_infos)
        if isinstance(value_infos, dict):
            raw_metadata = value_infos.get("metadata", {})
            if isinstance(raw_metadata, str):
                raw_metadata = json.loads(raw_metadata)
            if isinstance(raw_metadata, dict):
                response_time_ms = raw_metadata.get("response_time")
                usage = raw_metadata.get("usage", {})
                if isinstance(usage, dict):
                    token_count = usage.get("total_tokens")
    except (TypeError, ValueError, json.JSONDecodeError):
        value_infos = stored
    return value_infos, response_time_ms, token_count


def row_values(*, dataset, row, columns, deadline):
    """``row_id``, ``order``, ``row_meta`` and the row's ``cells``."""

    column_ids = [str(column.id) for column in columns]
    if connection.vendor == "postgresql":
        assert_dataset_table_cells_within_limits(
            row_ids=[str(row.id)], column_ids=column_ids, deadline=deadline
        )
    deadline.before_query()
    cells = list(
        Cell.no_workspace_objects.filter(
            dataset=dataset,
            row=row,
            row__dataset=dataset,
            column__dataset=dataset,
            column_id__in=column_ids,
            row__deleted=False,
            column__deleted=False,
            deleted=False,
        )
    )
    annotation_status = GetRowDataView()._get_annotation_status
    values = {"row_id": str(row.id), "order": row.order, "row_meta": {}}
    cell_values = {}
    for cell in cells:
        deadline.checkpoint()
        value_infos, response_time_ms, token_count = _cell_metadata(cell.value_infos)
        cell_values[str(cell.column_id)] = {
            "cell_id": str(cell.id),
            "cell_value": cell.value,
            "metadata": {
                "response_time_ms": response_time_ms,
                "token_count": token_count,
                "annotation": annotation_status(cell.feedback_info, cell.value),
            },
            "status": cell.status,
            "value_infos": value_infos,
            "feedback_info": cell.feedback_info,
        }
    deadline.before_query()
    attach_row_sources([values], dataset=dataset, live=True)
    values["cells"] = cell_values
    return values


class _RowReadSchema:
    """drf-yasg reads swagger_schema on the view class (endpoint enumeration)
    and on the view instance (operation); None omits the route while OFF.
    """

    def __get__(self, instance, owner):
        if row_source_enabled():
            return swagger_settings.DEFAULT_AUTO_SCHEMA_CLASS
        return None


class DatasetRowReadView(APIView):
    """Read-only GET of one row of a dataset the caller can read."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    swagger_schema = _RowReadSchema()

    @classmethod
    def as_view(cls, **initkwargs):
        # OFF, a slashless request is not redirected here either (APPEND_SLASH).
        return gate_append_slash(super().as_view(**initkwargs))

    def dispatch(self, request, *args, **kwargs):
        # Before DRF's dispatch: OFF answers exactly as an unknown route does,
        # with no authentication, negotiation, lookup or query.
        ensure_row_read_route_enabled(request)
        return super().dispatch(request, *args, **kwargs)

    @validated_request(
        responses={
            200: DatasetRowReadResponseSerializer,
            413: ModelHubErrorResponseSerializer,
            503: ModelHubErrorResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    def get(self, request, dataset_id, row_id, *args, **kwargs):
        deadline = BoundedDatasetReadDeadline.start()
        try:
            with transaction.atomic():
                deadline.before_query()
                dataset = (
                    _request_dataset_queryset(request).filter(id=dataset_id).first()
                )
                if dataset is None:
                    return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))

                deadline.before_query()
                row = _request_row_queryset(request, dataset).filter(id=row_id).first()
                if row is None:
                    return self._gm.not_found("Dataset row not found.")

                columns = _request_column_queryset(request).filter(dataset=dataset)
                if dataset.source != DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value:
                    columns = columns.exclude(source__in=_EXPERIMENT_SOURCES)
                deadline.before_query()
                columns = list(
                    columns.order_by("created_at", "id")[
                        : DATASET_TABLE_EXACT_MAX_COLUMNS + 1
                    ]
                )
                assert_bounded_projection(column_count=len(columns), page_row_count=1)

                values = row_values(
                    dataset=dataset, row=row, columns=columns, deadline=deadline
                )
                assert_dataset_table_response_within_limits(values)
                deadline.checkpoint()
                return self._gm.success_response(values)
        except (BoundedDatasetReadLimitExceeded, DatasetTableExactLimitExceeded) as e:
            return _bounded_dataset_error(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code="dataset_read_limit_exceeded",
                detail=str(e),
            )
        except (BoundedDatasetReadDeadlineExceeded, DatabaseError):
            logger.exception("Dataset row read failed or timed out")
            return _bounded_dataset_error(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="dataset_read_unavailable",
                detail="The dataset read could not finish within the server deadline.",
            )
