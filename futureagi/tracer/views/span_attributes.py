"""
Span Attribute Discovery APIs for ClickHouse.

Endpoints:
1. GET /api/traces/span-attribute-keys/ - Discover all attribute keys for a project
2. GET /api/traces/span-attribute-values/ - Get top values for an attribute key
3. GET /api/traces/span-attribute-detail/<key>/ - Full detail for a specific attribute key
"""

import re
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import structlog
from clickhouse_connect.driver.exceptions import (
    DatabaseError as ClickHouseConnectDatabaseError,
)
from clickhouse_driver.errors import Error as ClickHouseError
from django.db import DatabaseError, connection, transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.span_attributes import (
    SpanAttributeDetailQuerySerializer,
    SpanAttributeDetailResponseSerializer,
    SpanAttributeKeysResponseSerializer,
    SpanAttributeProjectQuerySerializer,
    SpanAttributeValuesQuerySerializer,
    SpanAttributeValuesResponseSerializer,
)
from tracer.services.clickhouse.attribute_cursor_state import (
    AttributeCursorStateError,
    load_attribute_cursor_seen_state,
    persist_attribute_cursor_seen_state,
)
from tracer.services.clickhouse.attribute_reads import (
    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
    ATTRIBUTE_READ_EXPLICIT_SEGMENT,
    ATTRIBUTE_READ_MAX_PROJECTS,
    AttributeKeyCursorPageRead,
    AttributeReadMetadata,
    AttributeReadSelector,
)
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
)
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_clickhouse_api_read_unavailable_error,
)
from tracer.services.exact_aggregation_cache import read_or_schedule_exact_snapshot
from tracer.utils.workspace_scope import project_queryset_for_request

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiTextErrorResponseSerializer,
    404: ApiTextErrorResponseSerializer,
    500: ApiTextErrorResponseSerializer,
    503: ApiTextErrorResponseSerializer,
}

# Attribute-name discovery is project metadata, not a preview of the task or
# dashboard time window.  Freeze cursor walks at the earliest timestamp the
# spans contract accepts so pagination can reach every retained project row.
# Per-request candidate/query ceilings in ``AttributeReadSelector`` keep each
# continuation bounded; retention jobs decide which rows still exist.
# The table is partitioned by ``toDate(start_time)``. Although DateTime64 can
# represent pre-1970 instants, Date cannot; a 1900 predicate can be folded into
# a wrapped partition bound and incorrectly prune current data. No telemetry
# predates Unix time, so epoch is the earliest lossless retained-data bound.
SPAN_ATTRIBUTE_RETAINED_DATA_START = datetime(1970, 1, 1, tzinfo=UTC)
_CLICKHOUSE_ERROR_CODE_RE = re.compile(r"\bcode:\s*(\d+)\b", re.IGNORECASE)
_ATTRIBUTE_READ_PERMISSION_ERROR_CODES = frozenset({497})


def _project_is_in_request_scope(request, project_id: str) -> bool:
    """Run the only PostgreSQL query allowed by these telemetry endpoints."""

    return project_queryset_for_request(request).filter(id=project_id).exists()


def _workspace_project_batch(
    request,
    *,
    after_project_id: str | None = None,
) -> tuple[tuple[str, ...], bool]:
    """Return one bounded, authorized project batch for a workspace walk."""

    projects = project_queryset_for_request(request).order_by("id")
    if after_project_id:
        projects = projects.filter(id__gt=after_project_id)
    project_ids = list(
        projects.values_list("id", flat=True)[: ATTRIBUTE_READ_MAX_PROJECTS + 1]
    )
    return (
        tuple(
            str(project_id) for project_id in project_ids[:ATTRIBUTE_READ_MAX_PROJECTS]
        ),
        len(project_ids) > ATTRIBUTE_READ_MAX_PROJECTS,
    )


def _workspace_projects_are_in_request_scope(
    request,
    project_ids: tuple[str, ...],
) -> bool:
    """Re-authorize the bounded project batch embedded in a signed cursor."""

    authorized_ids = {
        str(project_id)
        for project_id in project_queryset_for_request(request)
        .filter(id__in=project_ids)
        .values_list("id", flat=True)
    }
    return authorized_ids == set(project_ids)


def _run_span_attribute_pg_read(deadline: ReadDeadline, read):
    """Run one attribute-scope ORM read inside the request-owned wall."""

    timeout_ms = deadline.remaining_ms(ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS)
    if connection.vendor != "postgresql":
        result = read()
        deadline.remaining_ms(floor_ms=1)
        return result
    already_in_atomic_block = connection.in_atomic_block
    try:
        transaction_context = (
            nullcontext() if already_in_atomic_block else transaction.atomic()
        )
        with transaction_context:
            with connection.cursor() as cursor:
                if not already_in_atomic_block:
                    cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SET LOCAL statement_timeout = %s",
                    [f"{timeout_ms}ms"],
                )
            result = read()
    except DatabaseError as exc:
        raise ReadDeadlineExceeded(
            "Span-attribute PostgreSQL read exceeded its request deadline"
        ) from exc
    deadline.remaining_ms(floor_ms=1)
    return result


def _clickhouse_error_code(exc: Exception) -> int | None:
    """Extract a typed ClickHouse error code without exposing its message."""

    if isinstance(exc, ClickHouseError):
        code = getattr(exc, "code", None)
        return code if isinstance(code, int) else None
    if isinstance(exc, ClickHouseConnectDatabaseError):
        match = _CLICKHOUSE_ERROR_CODE_RE.search(str(exc))
        return int(match.group(1)) if match else None
    return None


def is_attribute_api_read_unavailable_error(exc: Exception) -> bool:
    """Classify retryable attribute-read failures at the HTTP boundary.

    Attribute discovery now reads retained-window metadata before its bounded
    cursor walk.  A read-only ClickHouse user can lack access to that metadata
    during a rolling credential/configuration change (code 497).  That is an
    unavailable telemetry read, not proof that a tenant has no attributes and
    not a programming defect.  Keep the shared timeout/resource/transport
    classifier narrow and add only this attribute-specific permission case.
    """

    return (
        is_clickhouse_api_read_unavailable_error(exc)
        or _clickhouse_error_code(exc) in _ATTRIBUTE_READ_PERMISSION_ERROR_CODES
    )


def _attribute_read_metadata_is_unavailable(metadata) -> bool:
    """Reject degraded selector output while retaining labelled samples."""

    return not metadata.query_complete and metadata.query_status != "sampled"


def retained_attribute_window_start(
    retained_start: datetime | None,
    *,
    window_end: datetime,
) -> datetime:
    """Normalize the exact retained-data lower bound for cursor APIs.

    ``AttributeReadSelector`` has a strict ``datetime | None`` contract.  Keep
    that invariant at the HTTP boundary instead of comparing arbitrary objects
    (including an accidentally unconfigured test double) with a timestamp.  A
    genuine ``None`` means ClickHouse has no active part before ``window_end``;
    the one-microsecond empty interval lets the cursor terminate immediately.
    Any other type is a programming defect and must fail closed rather than
    masquerading as an empty tenant vocabulary.
    """

    if retained_start is None:
        return window_end - timedelta(microseconds=1)
    if not isinstance(retained_start, datetime):
        raise TypeError("retained attribute window start must be a datetime or None")
    normalized_start = (
        retained_start.replace(tzinfo=UTC)
        if retained_start.tzinfo is None
        else retained_start.astimezone(UTC)
    )
    return max(SPAN_ATTRIBUTE_RETAINED_DATA_START, normalized_start)


def _attribute_key_payload(row) -> dict:
    payload = asdict(row)
    if not payload.get("types"):
        payload.pop("types", None)
    # Discovery is deliberately bounded. ``count`` is useful for ordering but
    # is not an exact tenant-wide total unless a future exact endpoint says so.
    payload["count_exact"] = False
    return payload


class SpanAttributeKeysView(APIView):
    """
    Discover span attribute keys for a project.

    Cursor mode walks retained project data newest-first in bounded pages;
    exact ``q`` lookup remains available for direct key discovery.
    ``discovery_mode=eval_mapping`` includes JSON-only keys that eval mapping
    can resolve but attribute filters cannot query. The default ``filter``
    mode retains the narrower filterable-key contract.
    The no-page-size form is retained for older clients.

    GET /api/traces/span-attribute-keys/?project_id=<uuid>&page_size=10
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeProjectQuerySerializer,
        responses={200: SpanAttributeKeysResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        project_id = ""
        selector: AttributeReadSelector | None = None
        request_deadline = ReadDeadline.start(ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS)
        try:
            query_params = request.validated_query_data
            workspace_scope = bool(query_params.get("workspace_scope", False))
            project_id = str(query_params.get("project_id") or "")
            discovery_mode = query_params["discovery_mode"]
            exact_key = query_params.get("q")
            page_size = query_params.get("page_size")
            cursor_token = query_params.get("cursor")
            if not workspace_scope and not _project_is_in_request_scope(
                request, project_id
            ):
                return self._gm.not_found("Project not found")

            if page_size is not None:
                page_size = int(page_size)
                project_ids: tuple[str, ...] | list[str]
                batch_end_project_id = ""
                has_later_projects = False
                project_ids = () if workspace_scope else [project_id]
                cursor_scope = cursor_scope_for_request(
                    request,
                    # Workspace cursors are tenant-bound here and carry only
                    # one bounded, re-authorized project batch in their signed
                    # order. Never materialize an unbounded workspace id list.
                    project_ids=[] if workspace_scope else project_ids,
                )
                cursor_query = (
                    {
                        "workspace_scope": True,
                        "mode": "recent_attribute_keys",
                    }
                    if workspace_scope
                    else {
                        "project_id": project_id,
                        "mode": "recent_attribute_keys",
                    }
                )
                # Keep the default cursor query byte-for-byte compatible with
                # cursors emitted by older pods. Eval mapping is a distinct
                # key contract and is explicitly signed so its cursor cannot
                # be replayed against the narrower filter inventory.
                if discovery_mode != "filter":
                    cursor_query["discovery_mode"] = discovery_mode
                if exact_key is not None:
                    # Signed cursor and server-side seen state are scoped to
                    # the normalized exact key. A continuation for one search
                    # can therefore never be replayed under another key.
                    cursor_query["q"] = exact_key
                if cursor_token:
                    cursor_state = decode_list_cursor(
                        cursor_token,
                        resource="span_attribute_keys",
                        scope=cursor_scope,
                        query=cursor_query,
                        page_size=page_size,
                    )
                    expected_order_lengths = {8, 9} if workspace_scope else {5, 6}
                    if len(cursor_state.order) not in expected_order_lengths:
                        raise ListCursorError(
                            "invalid_cursor",
                            "The continuation cursor is invalid.",
                        )
                    physical_order = cursor_state.order
                    if workspace_scope:
                        (
                            batch_end_project_id,
                            raw_project_ids,
                            has_later_projects,
                        ) = cursor_state.order[:3]
                        physical_order = cursor_state.order[3:]
                        if (
                            not isinstance(batch_end_project_id, str)
                            or not isinstance(raw_project_ids, tuple)
                            or len(raw_project_ids) > ATTRIBUTE_READ_MAX_PROJECTS
                            or not isinstance(has_later_projects, bool)
                            or any(
                                not isinstance(value, str) or not value
                                for value in raw_project_ids
                            )
                            or tuple(sorted(set(raw_project_ids))) != raw_project_ids
                            or (
                                raw_project_ids
                                and batch_end_project_id != raw_project_ids[-1]
                            )
                            or (
                                not raw_project_ids
                                and (not batch_end_project_id or not has_later_projects)
                            )
                        ):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )
                        if raw_project_ids:
                            project_ids = raw_project_ids
                            if not _run_span_attribute_pg_read(
                                request_deadline,
                                lambda: _workspace_projects_are_in_request_scope(
                                    request, project_ids
                                ),
                            ):
                                raise ListCursorError(
                                    "cursor_mismatch",
                                    "The continuation cursor no longer matches this workspace.",
                                )
                        else:
                            project_ids, has_later_projects = (
                                _run_span_attribute_pg_read(
                                    request_deadline,
                                    lambda: _workspace_project_batch(
                                        request,
                                        after_project_id=batch_end_project_id,
                                    ),
                                )
                            )
                            if project_ids:
                                batch_end_project_id = project_ids[-1]
                    (
                        segment_end,
                        raw_before_identity,
                        raw_resume_identity,
                        resume_key_offset,
                        seen_reference,
                    ) = physical_order[:5]
                    raw_segment_start = (
                        physical_order[5] if len(physical_order) == 6 else None
                    )
                    if (
                        not isinstance(segment_end, datetime)
                        or not isinstance(raw_before_identity, tuple)
                        or len(raw_before_identity) not in {0, 4}
                        or not isinstance(raw_resume_identity, tuple)
                        or len(raw_resume_identity) not in {0, 4}
                        or (raw_before_identity and raw_resume_identity)
                        or not isinstance(resume_key_offset, int)
                        or resume_key_offset < 0
                        or (
                            raw_segment_start is not None
                            and not isinstance(raw_segment_start, datetime)
                        )
                        or (
                            raw_segment_start is not None
                            and not (
                                raw_before_identity
                                or raw_resume_identity
                                # Exact-key absence pages advance through a
                                # proven empty temporal slice without a row
                                # checkpoint.  The selector explicitly accepts
                                # this adaptive-width continuation; rejecting it
                                # here made every page-two exact lookup a 400.
                                or exact_key is not None
                            )
                        )
                    ):
                        raise ListCursorError(
                            "invalid_cursor",
                            "The continuation cursor is invalid.",
                        )

                    def restore_identity(raw_identity):
                        if not raw_identity:
                            return None
                        if not all(
                            isinstance(value, str) for value in raw_identity[:3]
                        ) or not isinstance(raw_identity[3], datetime):
                            raise ListCursorError(
                                "invalid_cursor",
                                "The continuation cursor is invalid.",
                            )
                        return raw_identity

                    before_identity = restore_identity(raw_before_identity)
                    resume_identity = restore_identity(raw_resume_identity)
                    segment_start = raw_segment_start
                    window_start = cursor_state.window_start
                    window_end = cursor_state.window_end
                else:
                    if workspace_scope:
                        project_ids, has_later_projects = _run_span_attribute_pg_read(
                            request_deadline,
                            lambda: _workspace_project_batch(request),
                        )
                        if project_ids:
                            batch_end_project_id = project_ids[-1]
                    window_end = datetime.now(UTC)
                    # The system.parts lower-bound read was only a pagination
                    # accelerator, but it added a third ClickHouse round trip
                    # to the latency-sensitive first property page. Epoch is
                    # the selector's already-established conservative bound:
                    # it cannot skip retained spans, and geometric empty-slice
                    # growth still proves exhaustion in a bounded number of
                    # reads. Existing signed cursors keep their frozen window.
                    window_start = SPAN_ATTRIBUTE_RETAINED_DATA_START
                    segment_end = window_end
                    segment_start = None
                    before_identity = None
                    resume_identity = None
                    resume_key_offset = 0
                    seen_reference = ()

                state_binding = {
                    "scope": cursor_scope,
                    "query": cursor_query,
                    "page_size": page_size,
                    "window_start": window_start,
                    "window_end": window_end,
                }
                seen_state = load_attribute_cursor_seen_state(
                    seen_reference,
                    resource="span_attribute_keys",
                    binding=state_binding,
                    validate_digest=lambda value: (
                        len(value) == 32
                        and all(char in "0123456789abcdef" for char in value)
                    ),
                )
                if cursor_token and cursor_state.seen_rows != seen_state.seen_count:
                    raise ListCursorError(
                        "invalid_cursor",
                        "The continuation cursor is invalid.",
                    )

                if project_ids:
                    selector = AttributeReadSelector(
                        typed_only=True,
                        json_attribute_mode=(
                            "all" if discovery_mode == "eval_mapping" else "structured"
                        ),
                        wall_timeout_ms=request_deadline.remaining_ms(
                            ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                        ),
                    )
                    page_read = selector.read_key_cursor_page(
                        project_ids,
                        page_size=page_size,
                        window_start=window_start,
                        window_end=window_end,
                        segment_end=segment_end,
                        segment_start=segment_start,
                        before_identity=before_identity,
                        resume_identity=resume_identity,
                        resume_key_offset=resume_key_offset,
                        seen_key_digests=seen_state.digests,
                        seen_key_contains=seen_state.contains,
                        seen_key_count=seen_state.seen_count,
                        exact_key=exact_key,
                        # A workspace key can migrate storage families between
                        # projects. Remember key+type identities so a later
                        # authorized batch can publish a newly observed family
                        # without re-emitting the same family forever.
                        dedupe_by_type=workspace_scope,
                        # A positive workspace exact lookup proves only this
                        # batch/type. Continue the bounded physical walk and
                        # then every later project batch to cover all observed
                        # retained type families for the requested key.
                        exhaustive_exact_types=(
                            workspace_scope and exact_key is not None
                        ),
                    )
                else:
                    request_deadline.remaining_ms(floor_ms=1)
                    page_read = AttributeKeyCursorPageRead(
                        (),
                        AttributeReadMetadata(
                            query_complete=True,
                            query_status="complete",
                            query_error_code=None,
                            query_window_start=window_start,
                            query_window_end=window_end,
                            query_count=0,
                        ),
                        False,
                        "exhausted",
                        window_start,
                        None,
                        None,
                        0,
                        seen_state.digests,
                        seen_key_count=seen_state.seen_count,
                    )
                if not page_read.metadata.query_complete:
                    logger.warning(
                        "span_attribute_key_cursor_incomplete",
                        project_id=project_id,
                        error_code=page_read.metadata.query_error_code,
                    )
                    return self._gm.custom_error_response(
                        503,
                        "Span attribute keys are temporarily unavailable. Please retry.",
                        code="service_unavailable",
                    )
                exact_match = exact_key is not None and (
                    seen_state.seen_count > 0
                    or any(row.key == exact_key for row in page_read.rows)
                )
                advance_project_batch = (
                    workspace_scope and not page_read.has_more and has_later_projects
                )
                next_cursor = None
                published_has_more = page_read.has_more or advance_project_batch
                published_browse_status = (
                    "continuation" if advance_project_batch else page_read.browse_status
                )
                if published_has_more:
                    appended_digests = (
                        page_read.appended_key_digests
                        or (page_read.seen_key_digests[len(seen_state.digests) :])
                    )
                    seen_reference = persist_attribute_cursor_seen_state(
                        seen_state,
                        appended_digests,
                        resource="span_attribute_keys",
                        binding=state_binding,
                        validate_digest=lambda value: (
                            len(value) == 32
                            and all(char in "0123456789abcdef" for char in value)
                        ),
                    )
                    if advance_project_batch:
                        # The next request resolves and reads exactly one next
                        # authorized project batch. The physical checkpoint is
                        # reset because each batch gets the same frozen window.
                        next_order = (window_end, (), (), 0, seen_reference)
                    else:
                        next_order = (
                            page_read.next_segment_end,
                            page_read.next_before_identity or (),
                            page_read.next_resume_identity or (),
                            page_read.next_resume_key_offset,
                            seen_reference,
                        )
                        if page_read.next_segment_start is not None:
                            next_order = (*next_order, page_read.next_segment_start)
                    if workspace_scope:
                        next_order = (
                            batch_end_project_id,
                            () if advance_project_batch else tuple(project_ids),
                            has_later_projects,
                            *next_order,
                        )
                    next_cursor = encode_list_cursor(
                        resource="span_attribute_keys",
                        scope=cursor_scope,
                        query=cursor_query,
                        page_size=page_size,
                        window_start=window_start,
                        window_end=window_end,
                        order=next_order,
                        seen_rows=seen_state.seen_count + len(appended_digests),
                    )
                return Response(
                    {
                        # Cursor browse counts only describe occurrences inside
                        # the bounded physical prefix used to discover this
                        # suggestion.  Never present them as exact tenant-wide
                        # span totals.
                        "result": [
                            _attribute_key_payload(row) for row in page_read.rows
                        ],
                        **page_read.metadata.public_payload(),
                        "has_more": published_has_more,
                        "next_cursor": next_cursor,
                        # Preserve the rolling-deploy response enum. Despite
                        # this legacy label, the frozen cursor window now spans
                        # all retained project data, not a UI date range.
                        "browse_mode": "recent_suggestions",
                        "browse_status": published_browse_status,
                        **(
                            {
                                "lookup_mode": "exact",
                                "exact_match": exact_match,
                            }
                            if exact_key is not None
                            else {}
                        ),
                    },
                    status=200,
                )

            # The retained-data cursor above is the exhaustive path. Keep this
            # compatibility exact-q endpoint on its production-qualified
            # adaptive windows: one unsegmented 1970-to-now Map probe scanned
            # hundreds of millions of rows on the incident tenant and could
            # recreate the original 503. UI consumers paginate the retained
            # catalog and filter those verified typed names locally.
            selector = AttributeReadSelector(
                typed_only=True,
                json_attribute_mode=(
                    "all" if discovery_mode == "eval_mapping" else "structured"
                ),
                wall_timeout_ms=request_deadline.remaining_ms(
                    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
                ),
            )
            read = selector.discover_keys([project_id], exact_key=exact_key)
            if _attribute_read_metadata_is_unavailable(read.metadata):
                logger.warning(
                    "span_attribute_keys_incomplete",
                    project_id=project_id,
                    error_code=read.metadata.query_error_code,
                )
                return self._gm.custom_error_response(
                    503,
                    "Span attribute keys are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            return Response(
                {
                    "result": [_attribute_key_payload(row) for row in read.rows],
                    **read.metadata.public_payload(),
                    **(
                        {
                            "lookup_mode": "exact",
                            "exact_match": any(
                                row.key == exact_key for row in read.rows
                            ),
                        }
                        if exact_key is not None
                        else {}
                    ),
                },
                status=200,
            )
        except AttributeCursorStateError as exc:
            if exc.code == "cursor_state_unavailable":
                return self._gm.custom_error_response(
                    503,
                    str(exc),
                    code="service_unavailable",
                )
            return self._gm.custom_error_response(400, str(exc), code=exc.code)
        except ListCursorError as exc:
            return self._gm.custom_error_response(
                400,
                str(exc),
                code=exc.code,
            )
        except Exception as exc:
            if is_attribute_api_read_unavailable_error(exc):
                logger.warning(
                    "span_attribute_keys_unavailable",
                    project_id=project_id,
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    503,
                    "Span attribute keys are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "span_attribute_keys_programming_error",
                project_id=project_id,
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "Span attribute keys could not be loaded"
            )


class SpanAttributeValuesView(APIView):
    """
    Get top values for a specific span attribute key.

    Returns the most frequent values for the given string attribute key,
    with optional prefix search filtering.

    GET /api/traces/span-attribute-values/?project_id=<uuid>&key=<attr_key>[&q=<search>][&limit=50]
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeValuesQuerySerializer,
        responses={200: SpanAttributeValuesResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        project_id = ""
        key = ""
        selector: AttributeReadSelector | None = None
        try:
            query_params = request.validated_query_data
            project_id = str(query_params["project_id"])
            key = query_params["key"]
            q = query_params.get("q")
            limit = query_params.get("limit", 50)
            selector = AttributeReadSelector(
                typed_only=True,
                json_attribute_mode="arrays",
            )
            if not _project_is_in_request_scope(request, project_id):
                return self._gm.not_found("Project not found")
            # This endpoint predates signed retained-data pagination. Keep it
            # as a responsive compatibility sample: a narrow exact latest-state
            # slice is useful to old clients and cannot turn a dense 365-day
            # scan into a ten-second 503. Exhaustive filter pickers use the
            # cursor-backed dashboard filter-values endpoint.
            window_end = selector.query_window_end
            read = selector.read_values(
                [project_id],
                key,
                search=q,
                max_values=limit,
                window_start=window_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
                window_end=window_end,
            )
            if (
                read.metadata.query_complete
                or read.metadata.query_error_code == "sample_limit"
            ):
                read = replace(
                    read,
                    metadata=replace(
                        read.metadata,
                        query_complete=False,
                        query_status="sampled",
                        query_error_code="sample_limit",
                    ),
                )
            if _attribute_read_metadata_is_unavailable(read.metadata):
                logger.warning(
                    "span_attribute_values_incomplete",
                    project_id=project_id,
                    key=key,
                    error_code=read.metadata.query_error_code,
                )
                return self._gm.custom_error_response(
                    503,
                    "Span attribute values are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            return Response(
                {
                    "result": [asdict(row) for row in read.rows],
                    **read.metadata.public_payload(),
                },
                status=200,
            )
        except Exception as exc:
            if is_attribute_api_read_unavailable_error(exc):
                logger.warning(
                    "span_attribute_values_unavailable",
                    project_id=project_id,
                    key=key,
                    error_type=type(exc).__name__,
                )
                return self._gm.custom_error_response(
                    503,
                    "Span attribute values are temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )
            logger.exception(
                "span_attribute_values_programming_error",
                project_id=project_id,
                key=key,
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "Span attribute values could not be loaded"
            )


class SpanAttributeDetailView(APIView):
    """
    Serve the last complete exact attribute snapshot and refresh out of band.

    GET /api/traces/span-attribute-detail/?project_id=<uuid>&key=<attr_key>
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SpanAttributeDetailQuerySerializer,
        responses={200: SpanAttributeDetailResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        project_id = ""
        key = ""
        try:
            query_params = request.validated_query_data
            project_id = str(query_params["project_id"])
            key = query_params["key"]
            organization = getattr(request, "organization", None) or getattr(
                request.user, "organization", None
            )
            workspace = getattr(request, "workspace", None) or getattr(
                request.user, "workspace", None
            )
            # Exact workers must carry both tenant dimensions and re-authorize
            # them before touching ClickHouse.  Missing middleware context is
            # therefore an authorization failure, never an organization-less
            # cache identity or a generic programming-error response.
            if organization is None or workspace is None:
                logger.warning(
                    "span_attribute_detail_tenant_context_missing",
                    project_id=project_id,
                    has_organization=organization is not None,
                    has_workspace=workspace is not None,
                )
                return self._gm.not_found("Project not found")
            if not _project_is_in_request_scope(request, project_id):
                return self._gm.not_found("Project not found")

            identity = {
                "organization_id": str(organization.id),
                "workspace_id": str(workspace.id),
                "project_id": project_id,
                "attribute_key": key,
                "horizon_days": 365,
            }
            payload = read_or_schedule_exact_snapshot(
                "attribute-detail",
                identity,
                refresh=bool(query_params.get("refresh", False)),
                pending_payload={
                    "key": key,
                    "type": None,
                    "count": 0,
                    "unique_values": 0,
                    "top_values": [],
                    "query_complete": False,
                    "query_status": "pending",
                    "query_sampled": False,
                },
            )
            return Response(payload, status=200)
        except Exception as exc:
            logger.exception(
                "span_attribute_detail_programming_error",
                project_id=project_id,
                key=key,
                error_type=type(exc).__name__,
            )
            return self._gm.internal_server_error_response(
                "Span attribute details could not be loaded"
            )
