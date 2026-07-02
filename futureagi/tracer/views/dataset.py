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
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
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
            # prevents cross-org leakage, and workspace scoping prevents
            # same-org workspace bleed. `select_all` still requires project.
            org = _request_organization(request)
            workspace = _request_workspace(request)
            if not project and not select_all:
                if trace_ids:
                    project = (
                        Trace.no_workspace_objects.filter(
                            Q(project__organization=org),
                            _workspace_scope_q("project__workspace", workspace, org),
                            id__in=trace_ids,
                        )
                        .values_list("project_id", flat=True)
                        .first()
                    )
                elif span_ids:
                    # KEEP-PG: tenancy-anchored project derivation. The
                    # PG variant is one query (single JOIN through
                    # ``project__organization=org``); replacing with
                    # CHSpanReader.get() would cost a CH round-trip
                    # PLUS a PG ``Project.objects.filter(id=..,
                    # organization=).exists()`` round-trip — same blast
                    # radius, more code, no perf win. Spans table is
                    # only used here to resolve the project FK.
                    project = (
                        ObservationSpan.no_workspace_objects.filter(
                            Q(project__organization=org),
                            _workspace_scope_q("project__workspace", workspace, org),
                            id__in=span_ids,
                        )
                        .values_list("project_id", flat=True)
                        .first()
                    )

            if not project:
                raise ValueError("Project id cannot be null")

            # Tenant scope: validate the resolved project belongs to the
            # caller's org BEFORE issuing any CH read. Spans in CH carry
            # only project_id (no org_id JOIN), so the PG ``project__
            # organization=org`` predicate the legacy ORM filters used
            # MUST be replicated as an explicit pre-flight check here —
            # otherwise a malicious caller can pass a foreign project
            # UUID and reader.list_by_project would happily return that
            # other org's spans. This matches the P1 fix landed in
            # commit e80a7176d for separate_evals.py.
            if not Project.no_workspace_objects.filter(
                _workspace_scope_q("workspace", workspace, org),
                id=project,
                organization=org,
            ).exists():
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

            # Build span_ids list for create_new_cells. Each branch
            # below is either a pure CH read (project_id-scoped) or a
            # CH25-TODO deferral when the reader lacks exclusion API.
            span_id_list: list[str] = []
            if select_all:
                # CH25-TODO(list_by_project_exclude): both select_all
                # branches use ``.exclude(trace_id__in=…)`` /
                # ``.exclude(id__in=…)``. CHSpanReader has no exclusion
                # API; adding ad-hoc CH SQL inline would violate Rule 1.
                # Propose: ``list_by_project(..., exclude_trace_ids=None,
                # exclude_ids=None) -> list[CHSpan]`` (or a sibling
                # ``ids_by_project_exclude``). Until that lands, keep
                # the PG queryset so the select_all-with-exclusion
                # semantics stay exact and don't risk silent row loss
                # from a Python-side filter on a partially-fetched set.
                if trace_ids is not None:
                    observation_spans = ObservationSpan.no_workspace_objects.filter(
                        _project_scope_q(request),
                        parent_span_id__isnull=True,
                        project_id=project,
                    ).exclude(
                        trace_id__in=trace_ids,
                    )
                elif span_ids is not None:
                    observation_spans = ObservationSpan.no_workspace_objects.filter(
                        _project_scope_q(request),
                        project_id=project,
                    ).exclude(
                        id__in=span_ids,
                    )
                else:
                    observation_spans = ObservationSpan.objects.none()
                span_id_list = [
                    str(sid) for sid in observation_spans.values_list("id", flat=True)
                ]

            elif trace_ids and len(trace_ids) > 0:
                # Pre-validate trace_ids via PG so foreign-org / foreign-
                # project ids are dropped BEFORE the CH read — without
                # this, ``reader.list_by_trace_ids`` would happily fetch
                # foreign-org spans into process memory and we'd drop
                # them only at the Python ``project_id`` filter (defense
                # in depth still works, but loading foreign rows even
                # transiently is the codex P1 concern). The PG lookup
                # is one indexed SELECT.
                #
                # CH: legacy ORM additionally constrained
                # ``parent_span_id__isnull=True`` (root spans only). CH
                # stores parent_span_id as non-nullable String — root
                # spans have empty string, so a Python ``not span
                # .parent_span_id`` filter is the equivalent test.
                validated_trace_ids = list(
                    Trace.no_workspace_objects.filter(
                        _workspace_scope_q("project__workspace", workspace, org),
                        id__in=trace_ids,
                        project_id=project,
                        project__organization=org,
                    ).values_list("id", flat=True)
                )
                if not validated_trace_ids:
                    span_id_list = []
                else:
                    with get_reader() as reader:
                        ch_spans = reader.list_by_trace_ids(
                            [str(t) for t in validated_trace_ids]
                        )
                    # Codex final-review P2 (2026-05-26): every PG-validated
                    # trace must produce at least one root span in CH. A
                    # silently empty subset would have the dataset task
                    # report "creation started" while quietly dropping
                    # selected rows, because the downstream missing-span
                    # check only sees the already-filtered list.
                    seen_root_trace_ids = {
                        str(s.trace_id) for s in ch_spans if not s.parent_span_id
                    }
                    missing_root = [
                        str(t)
                        for t in validated_trace_ids
                        if str(t) not in seen_root_trace_ids
                    ]
                    if missing_root:
                        pg_root_span_ids = _pg_root_span_ids_for_trace_ids(
                            request, validated_trace_ids, project_id=project
                        )
                        if pg_root_span_ids:
                            span_id_list = pg_root_span_ids
                        else:
                            logger.error(
                                "dataset_add_to_new_ch_traces_missing_root_spans",
                                new_dataset_name=new_dataset_name,
                                missing_count=len(missing_root),
                                missing_sample=missing_root[:10],
                            )
                            raise RuntimeError(
                                f"CH missing root spans for {len(missing_root)} of "
                                f"{len(validated_trace_ids)} requested traces; "
                                "refusing to silently drop rows from the new dataset."
                            )
                    else:
                        span_id_list = [s.id for s in ch_spans if not s.parent_span_id]
            elif span_ids and len(span_ids) > 0:
                # Pre-validate span_ids via PG (project + org JOIN) so
                # only same-project / same-org ids reach the CH reader.
                # Codex P1 (mid-views-chunk review).
                validated_span_ids = list(
                    ObservationSpan.no_workspace_objects.filter(
                        _workspace_scope_q("project__workspace", workspace, org),
                        id__in=span_ids,
                        project_id=project,
                        project__organization=org,
                    ).values_list("id", flat=True)
                )
                if not validated_span_ids:
                    span_id_list = []
                else:
                    with get_reader() as reader:
                        ch_spans = reader.list_by_ids(
                            [str(s) for s in validated_span_ids]
                        )
                    ch_span_ids = {str(s.id) for s in ch_spans}
                    if len(ch_span_ids) < len(validated_span_ids):
                        span_id_list = [str(sid) for sid in validated_span_ids]
                    else:
                        span_id_list = [s.id for s in ch_spans]
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
            create_new_cells(span_id_list, dataset, column_span_mapping)

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

            # Tenant scope: if project is client-supplied, validate it
            # against the caller's org before any CH read. ``select_all``
            # paths use project explicitly; trace_ids / span_ids paths
            # below pre-validate via PG ``Trace`` / ``ObservationSpan``
            # filters that JOIN through ``project__organization=org``.
            if (
                project
                and not Project.no_workspace_objects.filter(
                    _workspace_scope_q("workspace", workspace, org),
                    id=project,
                    organization=org,
                ).exists()
            ):
                return self._gm.bad_request(get_error_message("PROJECT_NOT_FOUND"))

            span_id_list: list[str] = []
            if select_all:
                # CH25-TODO(list_by_project_exclude): same exclusion-API
                # gap as add_to_new_dataset. The reader has no
                # ``.exclude()`` equivalent and Rule 1 forbids ad-hoc
                # CH SQL. Defer until ``list_by_project(...,
                # exclude_trace_ids=, exclude_ids=)`` (or
                # ``ids_by_project_exclude``) lands.
                if trace_ids is not None:
                    observation_spans = ObservationSpan.no_workspace_objects.filter(
                        _project_scope_q(request),
                        parent_span_id__isnull=True,
                        project_id=project,
                    ).exclude(
                        trace_id__in=trace_ids,
                    )
                elif span_ids is not None:
                    observation_spans = ObservationSpan.no_workspace_objects.filter(
                        _project_scope_q(request),
                        project_id=project,
                    ).exclude(
                        id__in=span_ids,
                    )
                else:
                    observation_spans = ObservationSpan.objects.none()
                span_id_list = [
                    str(sid) for sid in observation_spans.values_list("id", flat=True)
                ]

            elif trace_ids and len(trace_ids) > 0:
                # Org-scope pre-validate via PG (no project_id supplied
                # in this branch — the trace_id list itself is the
                # tenancy anchor). PG ``Trace`` JOINs through
                # ``project__organization=org``; only the surviving
                # trace_ids hit CH.
                validated_trace_ids = list(
                    Trace.no_workspace_objects.filter(
                        Q(project__organization=org),
                        _workspace_scope_q("project__workspace", workspace, org),
                        id__in=trace_ids,
                    ).values_list("id", flat=True)
                )
                if not validated_trace_ids:
                    span_id_list = []
                else:
                    with get_reader() as reader:
                        ch_spans = reader.list_by_trace_ids(
                            [str(t) for t in validated_trace_ids]
                        )
                    # parent_span_id__isnull=True in PG; CH stores it as
                    # non-nullable String so root spans have an empty
                    # value. ``not s.parent_span_id`` is the equivalent
                    # truthy test.
                    span_id_list = [s.id for s in ch_spans if not s.parent_span_id]
                    if not span_id_list:
                        span_id_list = _pg_root_span_ids_for_trace_ids(
                            request, validated_trace_ids
                        )
            elif span_ids and len(span_ids) > 0:
                # KEEP-PG: this branch was a single ORM lookup that
                # served BOTH as the row accessor and as the tenancy
                # gate (``project__organization=org`` JOIN). Migrating
                # in isolation would either (a) replace the row read
                # with CH + bolt a PG ``ObservationSpan.objects.filter
                # (id__in=, project__organization=org)`` round-trip back
                # on for tenancy — net negative, no perf win, more code
                # paths — or (b) use ``reader.list_by_ids`` with a
                # Python ``s.org_id == str(org.id)`` filter, trusting
                # CH's denormalized ``org_id`` column.
                # CH25-TODO(span_id_org_validate_pure_ch): option (b)
                # is the right end state; add a benchmark + a
                # ``reader.list_by_ids(..., org_id=)`` overload so the
                # org filter happens in the CH WHERE clause instead of
                # in Python over the full result set.
                observation_spans = ObservationSpan.no_workspace_objects.filter(
                    Q(project__organization=org),
                    _workspace_scope_q("project__workspace", workspace, org),
                    id__in=span_ids,
                )
                span_id_list = [
                    str(sid) for sid in observation_spans.values_list("id", flat=True)
                ]
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
            create_new_cells(span_id_list, dataset, columns_to_span_fields)

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


def _project_scope_q(request):
    org = _request_organization(request)
    workspace = _request_workspace(request)
    return Q(project__organization=org) & _workspace_scope_q(
        "project__workspace", workspace, org
    )


def _pg_root_span_ids_for_trace_ids(request, trace_ids, project_id=None):
    root_filter = Q(parent_span_id__isnull=True) | Q(parent_span_id="")
    qs = ObservationSpan.no_workspace_objects.filter(
        _project_scope_q(request),
        root_filter,
        trace_id__in=trace_ids,
    )
    if project_id:
        qs = qs.filter(project_id=project_id)
    return [str(sid) for sid in qs.values_list("id", flat=True)]


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


def create_new_cells(span_ids, dataset, column_span_mapping):
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
            _submit_or_run_sync(batch, str(dataset.id), column_span_mapping_data)
            batch = []

    # Process remaining
    if batch:
        _submit_or_run_sync(batch, str(dataset.id), column_span_mapping_data)

    logger.info(
        f"dataset_creation_tasks_submitted: dataset_id={dataset.id}, "
        f"total_spans={len(span_ids_list)}, total_batches={total_batches}, "
        f"batch_size={batch_size}"
    )

    # Returns nothing - tasks run independently in background
