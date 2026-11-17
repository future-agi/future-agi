import uuid
from copy import copy

import structlog
from django.db import transaction
from django.db.models import Max, Q
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from model_hub.models.choices import DatasetSourceChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services.row_source_gate import row_source_enabled
from model_hub.services.row_sources import SOURCE_CONTEXT_VERSION
from model_hub.views.utils.dataset_limit import (
    DatasetLimitCheckFailed,
    DatasetLimitReached,
    dataset_limit_check_failed_response,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import ApiErrorCode
from tfc.utils.api_serializers import DatasetLimitCheckFailedErrorSerializer
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
from tracer.services.dataset_sources import (
    DatasetImportResponseSerializer,
    DatasetSourcePreviewSerializer,
    DatasetSourceResponseSerializer,
    preview_sources,
    validate_import_mapping,
)
from tracer.tasks import CHUNK_SIZE, process_spans_chunk_task
from tracer.tasks.dataset import (
    CH_EXPORT_READ_BATCH_SIZE,
    _export_span_field_value,
    _export_span_rows,
    _serialize_cell_value,
)

logger = structlog.get_logger(__name__)

# Synchronous whole-session exports must fit within one bounded request.
SESSION_EXPORT_MAX_SESSIONS = 100
SESSION_EXPORT_MAX_TURNS = 1000
SESSION_EXPORT_MAX_BYTES = 10 * 1024 * 1024
SESSION_EXPORT_MAX_PAGES = 10


def _check_session_export_limit(value, maximum, unit):
    if value > maximum:
        raise ValueError(
            f"Session export limit: {maximum} {unit}; use narrower filters."
        )

try:
    from ee.usage.utils.usage_entries import check_if_dataset_creation_is_allowed
except ImportError:
    check_if_dataset_creation_is_allowed = None


class DatasetView(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = ObserveDatasetSerializer

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

    @validated_request(
        request_serializer=DatasetSourcePreviewSerializer,
        responses={200: DatasetSourceResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def preview_sources(self, request, *args, **kwargs):
        serializer = request.validated_serializer
        org = _request_organization(request)
        project = Project.no_workspace_objects.filter(
            _workspace_scope_q("workspace", _request_workspace(request), org),
            id=serializer.validated_data["project"],
            organization=org,
        ).first()
        if project is None:
            return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))
        return self._gm.success_response(
            preview_sources(project, serializer.validated_data, request=request)
        )

    @validated_request(
        request_serializer=AddToNewDatasetObserveSerializer,
        responses={
            200: DatasetImportResponseSerializer,
            503: DatasetLimitCheckFailedErrorSerializer,
        },
    )
    @action(detail=False, methods=["post"])
    def add_to_new_dataset(self, request, *args, **kwargs):
        try:
            serializer = request.validated_serializer

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

            session_rows = None
            span_id_list: list[str] = []
            if (
                "session_ids" in serializer.validated_data
                and "span_ids" not in serializer.validated_data
                and "trace_ids" not in serializer.validated_data
            ):
                session_rows = _session_rows_for_request(
                    request, serializer.validated_data
                )
            elif select_all:
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

            if session_rows is None and not span_id_list:
                raise ValueError("No accessible observation spans selected")
            validate_import_mapping(mapping_config, session_fields=session_rows is not None)
            if session_rows is not None:
                with transaction.atomic():
                    dataset = create_new_dataset(
                        new_dataset_name, org, workspace, str(request.user.id)
                    )
                    column_span_mapping = create_new_columns(dataset, mapping_config)
                    create_new_session_cells(session_rows, dataset, column_span_mapping)
                return self._gm.success_response(
                    {
                        "dataset_id": str(dataset.id),
                        "dataset_name": dataset.name,
                        "status": "completed",
                        "rows_created": len(session_rows),
                    }
                )

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
                source_context=_row_source_context(serializer, project),
            )

            return self._gm.success_response(
                {
                    "dataset_id": str(dataset.id),
                    "dataset_name": dataset.name,
                    "status": "processing",
                    "message": "Dataset creation started. Data is being processed in background.",
                }
            )

        except DatasetLimitCheckFailed:
            return dataset_limit_check_failed_response()

        except DatasetLimitReached:
            # 400, not the sibling routes' 429: the frontend shows a 429 only
            # through the usage entry's upgrade alert, which this route does
            # not send, so a 429 here would refuse without telling the user.
            return self._gm.bad_request(
                get_error_message("DATASET_CREATE_LIMIT_REACHED")
            )

        except (ValidationError, ValueError) as e:
            logger.exception(f"Error in creating dataset observe:  {str(e)}")
            return self._gm.bad_request(f"Error creating the dataset observe {str(e)}")

        except Exception as e:
            logger.exception(f"Error in creating dataset observe:  {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error creating the dataset observe {str(e)}"
            )

    @validated_request(
        request_serializer=AddToExistingDatasetObserveSerializer,
        responses={200: DatasetImportResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def add_to_existing_dataset(self, request, *args, **kwargs):
        try:
            serializer = request.validated_serializer

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

            session_rows = None
            span_id_list: list[str] = []
            if (
                "session_ids" in serializer.validated_data
                and "span_ids" not in serializer.validated_data
                and "trace_ids" not in serializer.validated_data
            ):
                session_rows = _session_rows_for_request(
                    request, serializer.validated_data
                )
            elif select_all:
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

            if session_rows is None and not span_id_list:
                raise ValueError("No accessible observation spans selected")
            if not mapping_config and not new_mapping_config:
                raise ValueError("No column mapping provided")
            validate_import_mapping(
                mapping_config, new_columns=False, session_fields=session_rows is not None
            )
            validate_import_mapping(new_mapping_config, session_fields=session_rows is not None)
            names = [
                item.get("col_name") for item in mapping_config + new_mapping_config
            ]
            if len(names) != len(set(names)):
                raise ValueError("Column names must be unique")

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

            if session_rows is not None:
                create_new_session_cells(session_rows, dataset, columns_to_span_fields)
                return self._gm.success_response(
                    {
                        "dataset_id": str(dataset.id),
                        "status": "completed",
                        "rows_created": len(session_rows),
                    }
                )

            # Submit batch tasks asynchronously - returns nothing
            create_new_cells(
                span_id_list,
                dataset,
                columns_to_span_fields,
                project_id=project,
                org_id=org.id,
                source_context=_row_source_context(serializer, project),
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


def _row_source_context(serializer, project):
    """The row source context (v1) an import's worker records, or None.

    The request names only the kind; each row's trace, span and session come
    from its own span in the worker. Without ``source_kind`` nothing is
    recorded, exactly as before, and nothing is recorded while row sources are
    turned off (the span export then leaves out each span's session, so a
    session would be recorded as missing). The context never changes which
    rows import.
    """

    source_kind = serializer.validated_data.get("source_kind")
    if source_kind is None or not row_source_enabled():
        return None
    return {
        "v": SOURCE_CONTEXT_VERSION,
        "kind": source_kind,
        "project_id": str(project),
    }


def _session_ids_for_select_all(request, project_id, filters, excluded_ids):
    """Use the Sessions list's filter semantics and cursor, not span filters."""
    from tracer.views.trace_session import TraceSessionView

    selected = []
    seen = set()
    seen_cursors = set()
    body = {
        "project_id": str(project_id),
        "filters": filters,
        "page_size": 500,
        "cursor_mode": True,
        "allow_sampled": False,
    }
    pages = 0
    while True:
        pages += 1
        _check_session_export_limit(pages, SESSION_EXPORT_MAX_PAGES, "selection pages")
        # Keep the authenticated tenant/workspace context; validate only the
        # list request, not the enclosing dataset mapping payload.
        selection_request = copy(request)
        selection_request._full_data = body.copy()
        view = TraceSessionView(request=selection_request, kwargs={}, action="list_sessions")
        response = view.list_sessions(selection_request)
        if response.status_code != 200:
            raise ValueError("Unable to resolve the session selection with these filters.")
        result = response.data.get("result", {})
        metadata = result.get("metadata", {})
        if metadata.get("query_complete") is False:
            raise ValueError("Session selection is incomplete; no rows were added.")
        for row in result.get("table", []):
            session_id = str(row["session_id"])
            if session_id not in excluded_ids and session_id not in seen:
                seen.add(session_id)
                selected.append(session_id)
                _check_session_export_limit(
                    len(selected), SESSION_EXPORT_MAX_SESSIONS, "sessions"
                )
        if not metadata.get("has_more"):
            return selected
        cursor = metadata.get("next_cursor")
        if not cursor or cursor in seen_cursors:
            raise ValueError("Session selection could not be completed; no rows were added.")
        seen_cursors.add(cursor)
        body["cursor"] = cursor


def _session_rows_for_request(request, data):
    """Preflight the entire selection before hydration or any dataset mutation."""
    from tracer.services.clickhouse.v2.end_user_dict_reader import resolve_user_ids

    project_id = str(data["project"])
    org_id = str(_request_organization(request).id)
    session_ids = list(dict.fromkeys(str(sid) for sid in data["session_ids"]))
    if data.get("select_all"):
        session_ids = _session_ids_for_select_all(
            request, project_id, data.get("session_filters", []), set(session_ids)
        )
    else:
        _check_session_export_limit(len(session_ids), SESSION_EXPORT_MAX_SESSIONS, "sessions")
    if not session_ids:
        raise ValueError("No sessions selected.")
    mappings = (data.get("mapping_config") or []) + (data.get("new_mapping_config") or [])
    mapped_fields = [mapping.get("span_field") or mapping.get("col_name") for mapping in mappings]
    include_turns = "turns" in mapped_fields

    rows = []
    grouped = {sid: [] for sid in session_ids}
    turn_count = raw_bytes = hydrated_bytes = serialized_bytes = 0
    with get_reader() as reader:
        for start in range(0, len(session_ids), CH_EXPORT_READ_BATCH_SIZE):
            batch = session_ids[start : start + CH_EXPORT_READ_BATCH_SIZE]
            try:
                roots = reader.session_export_roots(
                    batch, project_id=project_id,
                    max_turns=SESSION_EXPORT_MAX_TURNS - turn_count,
                    max_payload_bytes=SESSION_EXPORT_MAX_BYTES - raw_bytes,
                    include_turns=include_turns,
                )
            except ValueError as exc:
                # The reader receives the remaining budget; report the request's
                # configured totals, not a confusing per-batch remainder.
                raise ValueError(
                    f"Session export limits: {SESSION_EXPORT_MAX_TURNS} turns, "
                    f"{SESSION_EXPORT_MAX_BYTES} payload bytes; use narrower filters."
                ) from exc
            turn_count += len(roots)
            raw_bytes += sum(root.payload_bytes for root in roots) if include_turns else 0
            _check_session_export_limit(turn_count, SESSION_EXPORT_MAX_TURNS, "turns")
            _check_session_export_limit(raw_bytes, SESSION_EXPORT_MAX_BYTES, "payload bytes")
            for root in roots:
                sid = str(root.trace_session_id)
                if str(root.project_id) == project_id and sid in batch:
                    grouped[sid].append(root)
        if any(not roots for roots in grouped.values()):
            raise ValueError("One or more sessions were not found in this project.")

        for start in range(0, len(session_ids), CH_EXPORT_READ_BATCH_SIZE):
            batch = session_ids[start : start + CH_EXPORT_READ_BATCH_SIZE]
            user_ids = {
                str(root.end_user_id) for sid in batch for root in grouped[sid] if root.end_user_id
            }
            labels = resolve_user_ids(user_ids) if "user_id" in mapped_fields else {}
            aggregates = (
                reader.aggregate_by_session_ids(batch, project_id=project_id)
                if "metrics" in mapped_fields else {}
            )
            for sid in batch:
                roots = sorted(
                    grouped[sid], key=lambda root: (root.start_time, str(root.trace_id), str(root.id))
                )
                internal_user_id = next(
                    (str(root.end_user_id) for root in roots if root.end_user_id), None
                )
                values = {
                    "session_id": sid,
                    "user_id": labels.get(internal_user_id),
                    "metrics": aggregates.get(sid, {}),
                    "turns": [],
                }
                if include_turns:
                    for offset in range(0, len(roots), CH_EXPORT_READ_BATCH_SIZE):
                        root_ids = [str(root.id) for root in roots[offset : offset + CH_EXPORT_READ_BATCH_SIZE]]
                        try:
                            exported = reader.export_fields_by_ids(
                                root_ids, {"input", "output"},
                                project_id=project_id, org_id=org_id,
                                max_payload_bytes=SESSION_EXPORT_MAX_BYTES - hydrated_bytes,
                            )
                        except ValueError as exc:
                            raise ValueError(
                                f"Session export limit: {SESSION_EXPORT_MAX_BYTES} payload bytes; "
                                "use narrower filters."
                            ) from exc
                        hydrated_bytes += sum(
                            len((span.get(field) or "").encode("utf-8"))
                            for span in exported.values() for field in ("input", "output")
                        )
                        _check_session_export_limit(
                            hydrated_bytes, SESSION_EXPORT_MAX_BYTES, "payload bytes"
                        )
                        if any(root_id not in exported for root_id in root_ids):
                            raise ValueError("Session turns are unavailable; no rows were added.")
                        for root_id in root_ids:
                            span = exported[root_id]
                            values["turns"].append({
                                "input": _export_span_field_value(span, "input"),
                                "output": _export_span_field_value(span, "output"),
                            })
                # Count actual UTF-8 cell bytes too: JSON escaping and duplicate
                # column mappings can be larger than the stored input/output.
                serialized_bytes += sum(
                    len((_serialize_cell_value(values.get(field)) or "").encode("utf-8"))
                    for field in mapped_fields
                )
                _check_session_export_limit(serialized_bytes, SESSION_EXPORT_MAX_BYTES, "payload bytes")
                rows.append(values)
    return rows


@transaction.atomic
def create_new_session_cells(session_rows, dataset, column_mapping):
    """One atomic row per session, using the existing dataset cell encoding."""
    if not column_mapping:
        raise ValueError("No column mapping provided.")
    # Serialize session appends to this dataset and preserve order after gaps.
    Dataset.no_workspace_objects.select_for_update().get(id=dataset.id)
    last_order = Row.objects.filter(dataset=dataset).aggregate(last=Max("order"))["last"]
    first_order = 0 if last_order is None else last_order + 1
    data_size = 0
    for start in range(0, len(session_rows), CHUNK_SIZE):
        batch = session_rows[start : start + CHUNK_SIZE]
        rows = Row.objects.bulk_create([
            Row(id=uuid.uuid4(), dataset=dataset, order=first_order + start + index)
            for index in range(len(batch))
        ], batch_size=CHUNK_SIZE)
        cells = []
        for row, values in zip(rows, batch):
            for mapping in column_mapping:
                cells.append(Cell(
                    id=uuid.uuid4(), dataset=dataset, row=row, column=mapping["column"],
                    value=_serialize_cell_value(values.get(mapping["span_field"])),
                ))
        Cell.objects.bulk_create(cells, batch_size=1000)
        data_size += sum(len((cell.value or "").encode()) for cell in cells)

    def emit_storage_usage():
        # Same storage event as span exports; emit only after rows commit.
        try:
            from ee.usage.schemas.events import UsageEvent
            from ee.usage.services.emitter import emit

            emit(UsageEvent(
                org_id=str(dataset.organization_id),
                event_type="dataset_row_from_spans",
                amount=data_size,
                properties={
                    "source": "dataset_from_sessions",
                    "source_id": str(dataset.id),
                    "rows_created": len(session_rows),
                },
            ))
        except Exception:
            logger.debug("emit_dataset_storage_event_failed", dataset_id=str(dataset.id))

    transaction.on_commit(emit_storage_usage)


def _request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _request_workspace(request):
    return getattr(request, "workspace", None)


def _workspace_scope_q(field_name, workspace, organization):
    if not workspace:
        from tfc.utils.workspace_scope import empty_workspace_filter

        return empty_workspace_filter()

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

    if {str(span.trace_id) for span in ch_spans} != set(trace_id_list):
        raise ValueError("One or more selected traces are unavailable in this project")
    return list(dict.fromkeys(str(span.id) for span in ch_spans))


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
    if ch_span_ids != set(span_id_list):
        raise ValueError("One or more selected spans are unavailable in this project")
    return list(dict.fromkeys(span_id_list))


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

    if check_if_dataset_creation_is_allowed is not None:
        allowed, detail = check_if_dataset_creation_is_allowed(organization)
        if detail.get("error_code") == ApiErrorCode.DATASET_LIMIT_CHECK_FAILED:
            raise DatasetLimitCheckFailed
        if not allowed:
            raise DatasetLimitReached

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
    source_context=None,
):
    """Submit task via Temporal, fall back to synchronous execution."""
    try:
        process_spans_chunk_task.delay(
            batch,
            dataset_id,
            column_span_mapping_data,
            project_id,
            org_id,
            source_context,
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
            source_context,
        )


def create_new_cells(
    span_ids,
    dataset,
    column_span_mapping,
    *,
    project_id=None,
    org_id=None,
    source_context=None,
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
                source_context=source_context,
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
            source_context=source_context,
        )

    logger.info(
        f"dataset_creation_tasks_submitted: dataset_id={dataset.id}, "
        f"total_spans={len(span_ids_list)}, total_batches={total_batches}, "
        f"batch_size={batch_size}"
    )

    # Returns nothing - tasks run independently in background
