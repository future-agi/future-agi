"""Effective tag predicates and discovery that scale with telemetry volume.

SDK/source tags are machine-scale: every span of an agent trace can carry them.
They are evaluated inside ClickHouse from the projected tag sources only (the
``tags`` column, ``attrs_string['tag.tags']`` and the ``tag.tags`` paths of
``attributes_extra``), never by shipping span rows or whole attribute maps to
Python. Curated ``traces.tags`` (the previous editor's tags, and tags some
importers write on every trace) are machine-scale too and are also evaluated
in ClickHouse. Only UI assignments are human-scale: they are read from
PostgreSQL and bound as compact key parameters, under a count and a rendered
byte budget so a statement never approaches ``max_query_size``. Liveness, the
selected time window and the trace/span domain are always decided by the same
ClickHouse latest-state replay. Overflow of the assignment overlay is
unavailable, never a truncated membership set.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from clickhouse_driver.errors import Error as ClickHouseError
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection, transaction
from django.db.models import Q

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.entity_tags import (
    TagInputError,
    TagReadUnavailable,
    normalize_manual_tags,
)
from tracer.services.postgres_read_policy import application_postgres_reads

# UI assignments are human-authored, so a statement binds at most this many
# keys, and at most this many rendered bytes of keys and candidate ids, which
# keeps it well below ClickHouse's 262,144-byte max_query_size.
MAX_TAG_OVERLAY_KEYS = 2048
MAX_TAG_OVERLAY_BYTES = 128 * 1024
MAX_TAG_CANDIDATE_TRACES = 512
# Curated matches a window-scan leaf reads before it asks PostgreSQL which of
# those traces have an assignment; above it the assignments are paged instead.
MAX_CURATED_SUPPRESSION_CANDIDATES = MAX_TAG_OVERLAY_KEYS
_ASSIGNMENT_PAGE_SIZE = 1000
# At most 24,000 examined assignment rows per window graph leaf: well beyond
# the 8,100 dead rows of a busy project, and within the graph's 5 s overlay
# deadline (a page plus its ClickHouse liveness reads took ~165 ms on the e2e
# stack, so 64 pages could never finish before the deadline).
MAX_GRAPH_OVERLAY_SCAN_PAGES = 24
# Only probe history after a substantial walk; time spent on reads does not
# itself prove that the project exceeds the history cap.
MIN_SCANNED_PAGES_FOR_TOO_BROAD = 8
# Dense curated suppression fallbacks budget pages with window-live hits only.
MAX_WINDOW_OVERLAY_PAGES = 8
TAG_OVERLAY_SETTINGS = {
    "max_threads": 2,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
# A valid UUID that is also a valid span trace id string: an unscoped leaf's
# trace list must type-check against both ``spans.trace_id`` and ``traces.id``.
_UNSCOPED_TRACE = "00000000-0000-0000-0000-000000000000"
_TRACE_SENTINEL = ("", "")
_SPAN_SENTINEL = ("", "", "", 0, "", "")
_IDENTITY_FIELDS = (
    "project_id",
    "entity_kind",
    "trace_id",
    "span_id",
    "start_hour",
    "observation_type",
    "service_name",
)


@dataclass(frozen=True)
class EffectiveTagRelation:
    """Unbound compiler parameter; must be prepared before executing SQL.

    ``values`` are the exact names of a membership leaf; ``None`` selects the
    non-empty test of ``is_null``/``is_not_null``.
    """

    project_ids: tuple[str, ...]
    values: tuple[str, ...] | None = None


def _unwrap_sql(array: str) -> str:
    # One JSON string layer per call, element-wise, so the SQL stays linear.
    return (
        "arrayMap(etag_u -> if(JSONType(etag_u) = 'String', "
        f"JSONExtractString(etag_u), etag_u), {array})"
    )


def source_names_sql(raw_values: Sequence[str], json_values: Sequence[str]) -> str:
    """Array(String) of source tag names, mirroring ``entity_tags._source_items``.

    ``raw_values`` are attribute/column strings that Python decodes with up to
    three ``json.loads``. ``json_values`` are ``JSONExtractRaw`` outputs, i.e.
    one extra JSON layer around the attribute value. Items are strings or
    ``{name}`` objects; blank, non-string and malformed items are skipped.
    """
    values = f"[{', '.join(raw_values)}]"
    if json_values:
        values = (
            f"arrayConcat({values}, {_unwrap_sql('[' + ', '.join(json_values) + ']')})"
        )
    decoded = _unwrap_sql(_unwrap_sql(values))
    item = (
        "multiIf(JSONType(etag_i) = 'String', JSONExtractString(etag_i), "
        "JSONType(etag_i) = 'Object' AND JSONType(etag_i, 'name') = 'String', "
        "JSONExtractString(etag_i, 'name'), '')"
    )
    names = (
        "arrayFlatten(arrayMap(etag_v -> if(JSONType(etag_v) = 'Array', "
        f"arrayMap(etag_i -> {item}, JSONExtractArrayRaw(etag_v)), "
        f"CAST([], 'Array(String)')), {decoded}))"
    )
    return f"arrayDistinct(arrayFilter(etag_n -> match(etag_n, '[^\\\\s]'), {names}))"


def live_tag_source_sql(prefix: str) -> str:
    """Latest live span identities in the bound window with source names.

    The physical hours are replayed completely before the mutable liveness and
    event-time predicates, exactly like the list/detail readers. Only the tag
    sources enter the aggregate state; whole attribute maps never do.
    """
    p = prefix
    names = source_names_sql(
        ("etag_w.5", "etag_w.6"),
        ("etag_w.7", "etag_w.8"),
    )
    return f"""(
        SELECT project_id, trace_id, id, observation_type, service_name,
            start_hour, etag_w.1 AS start_time, etag_w.2 = '' AS is_root,
            etag_w.4 AS project_version_id, {names} AS src
        FROM (
            SELECT project_id, trace_id, id, observation_type, service_name,
                toStartOfHour(start_time) AS start_hour,
                argMax(tuple(start_time, ifNull(parent_span_id, ''), is_deleted,
                             project_version_id, tags, attrs_string['tag.tags'],
                             JSONExtractRaw(attributes_extra, 'tag.tags'),
                             JSONExtractRaw(attributes_extra, 'tag', 'tags')),
                       _version) AS etag_w
            FROM spans
            PREWHERE project_id IN %({p}_projects)s
              AND start_time >= %({p}_hour_start)s
              AND start_time < %({p}_hour_end)s
              AND (%({p}_scoped)s = 0 OR trace_id IN %({p}_traces)s)
            GROUP BY project_id, observation_type, service_name,
                     toStartOfHour(start_time), trace_id, id
            HAVING etag_w.3 = 0
               AND etag_w.1 >= %({p}_start)s
               AND etag_w.1 < %({p}_end)s
        )
    )"""


def live_tag_roots_sql(prefix: str) -> str:
    """Window-live roots without replaying source-tag columns or JSON."""
    p = prefix
    return f"""(
        SELECT project_id, trace_id
        FROM (
            SELECT project_id, trace_id, id,
                argMax(tuple(start_time, ifNull(parent_span_id, ''), is_deleted),
                       _version) AS etag_root
            FROM spans
            PREWHERE project_id IN %({p}_projects)s
              AND start_time >= %({p}_hour_start)s
              AND start_time < %({p}_hour_end)s
              AND (%({p}_scoped)s = 0 OR trace_id IN %({p}_traces)s)
            GROUP BY project_id, observation_type, service_name,
                     toStartOfHour(start_time), trace_id, id
            HAVING etag_root.3 = 0
               AND etag_root.1 >= %({p}_start)s
               AND etag_root.1 < %({p}_end)s
               AND etag_root.2 = ''
        )
    )"""


def legacy_trace_tags_sql(prefix: str, *, live_roots: bool = False) -> str:
    """Unsuppressed latest curated ``traces.tags`` names in the leaf's scope.

    Only traces that ever carried curated tags enter the replay; all of their
    versions do, so a later clear or tombstone still wins. Any saved trace
    assignment, even an empty one, suppresses this fallback. Picker reads
    additionally restrict both history passes to window-live roots.
    """
    p = prefix
    scope = (
        f"project_id IN %({p}_projects)s "
        f"AND (%({p}_scoped)s = 0 OR id IN %({p}_traces)s)"
    )
    if live_roots:
        scope += f" AND (project_id, id) IN {live_tag_roots_sql(p)}"
    names = source_names_sql(("etag_l.1",), ())
    return f"""(
        SELECT toString(project_id) AS legacy_project, toString(id) AS legacy_trace,
            {names} AS legacy_names
        FROM (
            SELECT project_id, id, argMax(tuple(tags, is_deleted), _version) AS etag_l
            FROM traces
            WHERE {scope}
              AND id IN (
                  SELECT id FROM traces WHERE {scope} AND tags != '' AND tags != '[]'
              )
            GROUP BY project_id, id
        )
        WHERE etag_l.2 = 0
          AND (toString(project_id), toString(id)) NOT IN %({p}_assigned_keys)s
    )"""


def relation_parameter_owner(name: str, params: dict[str, Any]) -> str | None:
    """The unprepared relation whose bound scope will define placeholder ``name``.

    Builders that namespace a residual's placeholders must carry that relation
    under the same namespace, so preparation binds the namespaced names.
    """
    for key, value in params.items():
        if isinstance(value, EffectiveTagRelation) and name.startswith(f"{key}_"):
            return key
    return None


def span_key_sql(*, project: str = "toString(project_id)") -> str:
    return (
        f"({project}, trace_id, id, toInt64(toUnixTimestamp(start_hour)), "
        "toString(observation_type), toString(service_name))"
    )


def trace_overlay_sql(
    prefix: str, names_predicate: str, *, live_roots: bool = False
) -> str:
    """A trace's own non-source names: its assignment, else curated tags.

    ``names_predicate`` is a template whose ``{names}`` is an Array(String).
    """
    p = prefix
    legacy = names_predicate.format(names="legacy_names")
    return (
        f"((toString(project_id), trace_id) IN %({p}_trace_keys)s "
        f"OR (toString(project_id), trace_id) IN (SELECT legacy_project, "
        f"legacy_trace FROM {legacy_trace_tags_sql(p, live_roots=live_roots)} "
        f"WHERE {legacy}))"
    )


def effective_match_sql(prefix: str, names_predicate: str) -> str:
    """One span's effective membership: source OR its own UI/legacy overlay.

    Roots carry the trace assignment (or, absent one, the curated legacy tags);
    children carry only their own physical assignment.
    """
    p = prefix
    source = names_predicate.format(names="src")
    return (
        f"({source} "
        f"OR (is_root AND {trace_overlay_sql(p, names_predicate, live_roots=True)}) "
        f"OR (NOT is_root AND {span_key_sql()} IN %({p}_span_keys)s))"
    )


def row_source_names_sql() -> str:
    """Source names of the current physical ``spans`` row.

    The same projected tag sources as :func:`live_tag_source_sql`, read from
    the row itself rather than from a replayed aggregate state.
    """
    return source_names_sql(
        ("tags", "attrs_string['tag.tags']"),
        (
            "JSONExtractRaw(attributes_extra, 'tag.tags')",
            "JSONExtractRaw(attributes_extra, 'tag', 'tags')",
        ),
    )


def graph_row_tag_sql(prefix: str, names_predicate: str, *, root_only: bool) -> str:
    """One physical span row's effective tag membership, with no spans read.

    Exact graphs evaluate this on every physical version and keep the latest
    version's flag, so liveness and the trace-level any-sibling semantics come
    from the graph's own replay. As in :func:`effective_match_sql`, roots carry
    the trace assignment (or the unsuppressed curated tags) and children carry
    only their own physical assignment. ``root_only`` is the trace-mode
    ``tags`` leaf: only the root's own names count.
    """
    p = prefix
    root = "(parent_span_id IS NULL OR parent_span_id = '')"
    source = names_predicate.format(names=row_source_names_sql())
    trace = trace_overlay_sql(p, names_predicate)
    if root_only:
        return f"({root} AND ({source} OR {trace}))"
    span = (
        "(toString(project_id), trace_id, id, "
        "toInt64(toUnixTimestamp(toStartOfHour(start_time))), "
        "toString(observation_type), toString(service_name)) "
        f"IN %({p}_span_keys)s"
    )
    return f"({source} OR ({root} AND {trace}) OR (NOT {root} AND {span}))"


def graph_overlay_executor() -> Callable[..., Any]:
    """The CH25 reader of a graph leaf's curated-tag suppressions.

    Graph statements are compiled where no executor is at hand; they read the
    same authoritative CH25 ``traces`` the statement itself reads.
    """
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    try:
        return V2AnalyticsQueryService().execute_ch_query
    except (
        ClickHouseError,
        ImproperlyConfigured,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        # An unreachable server or invalid connection settings: unavailable.
        raise TagReadUnavailable("tag_source_unavailable") from exc


def graph_tag_overlay_params(
    prefix: str,
    *,
    project_ids: tuple[str, ...],
    values: tuple[str, ...] | None,
    deadline: ReadDeadline,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    execute: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """Bind one graph leaf's UI overlay now, with its rendered byte size.

    Graph statements also run on executors without a preparation hook (the
    background exact-aggregation worker), so they carry concrete parameters,
    never an :class:`EffectiveTagRelation`. Window-scoped callers retain only
    live matching assignment keys before binding them; the graph's replay
    still decides final liveness. ``execute`` reads these candidates and any
    curated tags whose trace assignments suppress them, defaulting to CH25.
    """
    if not project_ids or (window_start is None) != (window_end is None):
        raise TagReadUnavailable("tag_scope_required")
    reader = execute or (graph_overlay_executor() if window_start is not None else None)
    phase: int | None = 0  # only paged walks justify a history probe

    def suppression_walk_started() -> None:
        nonlocal phase
        phase = 1

    try:
        overlay = read_tag_overlay(
            project_ids=project_ids,
            trace_ids=None,
            values=values,
            deadline=deadline,
            window_start=window_start,
            window_end=window_end,
            execute=reader,
            classify_deadline=False,
        )
        if overlay.unmatched_trace_assignments:
            phase = None
            # Only window-live traces can be in the graph: older curated tags
            # their assignments hide must not spend the suppression budget.
            overlay.assigned |= legacy_suppressions(
                reader or graph_overlay_executor(),
                project_ids=project_ids,
                values=values,
                deadline=deadline,
                window_start=window_start,
                window_end=window_end,
                classify_deadline=False,
                on_walk_start=suppression_walk_started,
            )
        trace_keys, span_keys, assigned = overlay.keys(values)
        params = {
            f"{prefix}_projects": project_ids,
            f"{prefix}_scoped": 0,
            f"{prefix}_traces": (_UNSCOPED_TRACE,),
            f"{prefix}_trace_keys": trace_keys,
            f"{prefix}_span_keys": span_keys,
            f"{prefix}_assigned_keys": assigned,
        }
        rendered = (
            _literal_bytes(trace_keys)
            + _literal_bytes(span_keys)
            + _literal_bytes(assigned)
            + _literal_bytes(project_ids)
        )
        return params, rendered
    except TagReadUnavailable as error:
        # Only a started paged walk can make a spent deadline over budget;
        # curated reads and sparse suppression lookups do not walk that history.
        if (
            error.code != "tag_overlay_budget_exceeded"
            and window_start is not None
            and phase is not None
        ):
            try:
                deadline.remaining_ms()
            except ReadDeadlineExceeded:
                assert window_end is not None
                grace = ReadDeadline.start(500)
                # An unwalked suppression history cannot make a stalled
                # matching walk terminal (and vice versa).
                predicate = _graph_history_predicates(
                    project_ids, values, window_start, window_end
                )[phase]
                if _history_exceeds_graph_cap(predicate, grace=grace):
                    raise TagReadUnavailable("tag_overlay_budget_exceeded") from error
        raise


def canonical_root_sql(prefix: str) -> str:
    """The list's canonical root per trace (latest start, then identity)."""
    p = prefix
    return f"""(
        SELECT project_id, trace_id,
            argMax(src, tuple(start_time, id, toString(observation_type),
                              toString(service_name), start_hour)) AS src
        FROM {live_tag_source_sql(p)}
        WHERE is_root
          AND (%({p}_rpv_any)s = 1 OR toString(project_version_id) = %({p}_rpv)s)
        GROUP BY project_id, trace_id
    )"""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _hours(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    hour_start = start.replace(minute=0, second=0, microsecond=0)
    hour_end = end.replace(minute=0, second=0, microsecond=0)
    if hour_end < end:
        hour_end += timedelta(hours=1)
    return hour_start, hour_end


def _scope_params(
    prefix: str,
    *,
    project_ids: tuple[str, ...],
    window_start: datetime,
    window_end: datetime,
    trace_ids: tuple[str, ...] | None,
    root_project_version: str | None = None,
) -> dict[str, Any]:
    start, end = _utc(window_start), _utc(window_end)
    if start >= end or not project_ids:
        raise TagReadUnavailable("tag_scope_required")
    if trace_ids is not None and (
        not trace_ids or len(trace_ids) > MAX_TAG_CANDIDATE_TRACES
    ):
        raise TagReadUnavailable("tag_candidate_budget_exceeded")
    hour_start, hour_end = _hours(start, end)
    return {
        f"{prefix}_projects": project_ids,
        f"{prefix}_hour_start": hour_start,
        f"{prefix}_hour_end": hour_end,
        f"{prefix}_start": start,
        f"{prefix}_end": end,
        f"{prefix}_scoped": 1 if trace_ids is not None else 0,
        f"{prefix}_traces": tuple(trace_ids)
        if trace_ids is not None
        else (_UNSCOPED_TRACE,),
        f"{prefix}_rpv_any": 0 if root_project_version is not None else 1,
        f"{prefix}_rpv": str(root_project_version or ""),
        f"{prefix}_assigned_keys": (_TRACE_SENTINEL,),
    }


def _literal_bytes(values: Iterable[Any]) -> int:
    """Upper bound of the rendered size of a bound tuple/scalar collection."""
    total = 2
    for value in values:
        parts = value if isinstance(value, tuple) else (value,)
        total += 4 + sum(len(str(part).encode("utf-8")) + 4 for part in parts)
    return total


def _manual_names(manual: object) -> tuple[str, ...]:
    try:
        return tuple(tag["name"] for tag in normalize_manual_tags(manual))
    except TagInputError:
        # Stored invalid data is unavailable, never permission to omit labels.
        raise TagReadUnavailable("tag_assignment_invalid") from None


def _assignment_key(record: dict[str, Any]) -> tuple:
    project = str(record["project_id"])
    if record["entity_kind"] == "trace":
        return (project, str(record["trace_id"]))
    hour = _utc(record["start_hour"])
    return (
        project,
        str(record["trace_id"]),
        str(record["span_id"]),
        int(hour.timestamp()),
        str(record["observation_type"]),
        str(record["service_name"]),
    )


def _query_assignments(
    predicate: Q, *, deadline: ReadDeadline, limit: int
) -> list[dict[str, Any]]:
    """Primary-only, deadline-bound read of at most ``limit + 1`` rows."""
    try:
        with application_postgres_reads(
            connection=connection,
            atomic=partial(transaction.atomic, using="default"),
            check_request=deadline.remaining_ms,
            statement_timeout_ms=deadline.remaining_ms,
        ):
            records = list(
                EntityTagAssignment.objects.using("default")
                .filter(predicate)
                .order_by("pk")
                .values("pk", *_IDENTITY_FIELDS, "manual_tags")[: limit + 1]
            )
            deadline.remaining_ms()
    except (DatabaseError, ReadDeadlineExceeded):
        raise TagReadUnavailable("tag_assignment_unavailable") from None
    return records


def _read_assignments(predicate: Q, *, deadline: ReadDeadline) -> list[dict[str, Any]]:
    """Every matching assignment, or unavailable above the overlay budget."""
    records = _query_assignments(
        predicate, deadline=deadline, limit=MAX_TAG_OVERLAY_KEYS
    )
    if len(records) > MAX_TAG_OVERLAY_KEYS:
        raise TagReadUnavailable("tag_overlay_budget_exceeded")
    return records


def _history_exceeds_graph_cap(
    predicate: Q, *, grace: ReadDeadline | None = None
) -> bool:
    """Prove that a leaf has more history than the bounded graph walk admits.

    The request deadline is already spent. Use a fresh, short PostgreSQL wall
    for the one-row existence probe; a timeout or DB outage proves nothing.
    """
    grace = grace or ReadDeadline.start(500)
    try:
        with application_postgres_reads(
            connection=connection,
            atomic=partial(transaction.atomic, using="default"),
            check_request=grace.remaining_ms,
            statement_timeout_ms=grace.remaining_ms,
        ):
            cap = MAX_GRAPH_OVERLAY_SCAN_PAGES * _ASSIGNMENT_PAGE_SIZE
            found = bool(
                list(
                    EntityTagAssignment.objects.using("default")
                    .filter(predicate)
                    .order_by("pk")
                    .values_list("pk", flat=True)[cap : cap + 1]
                )
            )
            grace.remaining_ms()
            return found
    except Exception:  # noqa: BLE001 - an advisory probe cannot justify a 400
        # Includes DB outages, timeout, configuration errors and a broken
        # connection wrapper. Only a successful existence read is proof.
        return False


def _graph_history_predicates(
    project_ids: tuple[str, ...],
    values: tuple[str, ...] | None,
    window_start: datetime,
    window_end: datetime,
) -> tuple[Q, Q]:
    """Walked names and curated suppressions both have independent histories."""
    names = _names_predicate(values)
    hour_start, hour_end = _hours(_utc(window_start), _utc(window_end))
    matching = (
        Q(project_id__in=project_ids, entity_kind__in=("trace", "span"))
        & names
        & (
            Q(entity_kind="trace")
            | Q(entity_kind="span", start_hour__gte=hour_start, start_hour__lt=hour_end)
        )
    )
    return matching, _unmatched_trace_predicate(project_ids, names)


def _too_broad_after_deadline(
    error: TagReadUnavailable,
    *,
    scanned_pages: int,
    deadline: ReadDeadline,
    predicate: Q,
) -> TagReadUnavailable:
    """Only a proven over-cap history turns a spent deadline into a 400."""
    if (
        error.code == "tag_overlay_budget_exceeded"
        or scanned_pages < MIN_SCANNED_PAGES_FOR_TOO_BROAD
    ):
        return error
    try:
        deadline.remaining_ms()
    except ReadDeadlineExceeded:
        if _history_exceeds_graph_cap(predicate):
            return TagReadUnavailable("tag_overlay_budget_exceeded")
    return error


def _assignment_pages(
    predicate: Q, *, deadline: ReadDeadline
) -> Iterable[list[dict[str, Any]]]:
    """Keyset pages of matching assignments; the deadline bounds the walk."""
    after = None
    while True:
        page = _query_assignments(
            predicate if after is None else predicate & Q(pk__gt=after),
            deadline=deadline,
            limit=_ASSIGNMENT_PAGE_SIZE - 1,
        )
        if page:
            yield page
        if len(page) < _ASSIGNMENT_PAGE_SIZE:
            return
        after = page[-1]["pk"]


@dataclass
class TagOverlay:
    """Human-scale UI assignment names keyed by the identity they attach to.

    ``assigned`` holds the traces whose saved assignment, even an empty one,
    suppresses their curated ``traces.tags``. A candidate batch reads every
    assignment of its traces. A window scan reads only the assignments that
    carry a requested name; ``unmatched_trace_assignments`` then says whether
    other trace assignments exist, whose suppressions the caller must add
    with :func:`legacy_suppressions`.
    """

    trace_names: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    span_names: dict[tuple, tuple[str, ...]] = field(default_factory=dict)
    assigned: set[tuple[str, str]] = field(default_factory=set)
    unmatched_trace_assignments: bool = False

    def add(self, record: dict[str, Any]) -> None:
        key = _assignment_key(record)
        names = _manual_names(record["manual_tags"])
        if record["entity_kind"] == "trace":
            self.assigned.add(key)
            self.trace_names[key] = names
        else:
            self.span_names[key] = names

    def keys(self, values: tuple[str, ...] | None) -> tuple[tuple, tuple, tuple]:
        def matches(names: tuple[str, ...]) -> bool:
            return bool(names) if values is None else bool(set(names) & set(values))

        traces = sorted(
            key for key, names in self.trace_names.items() if matches(names)
        )
        spans = sorted(key for key, names in self.span_names.items() if matches(names))
        if len(traces) + len(spans) > MAX_TAG_OVERLAY_KEYS:
            raise TagReadUnavailable("tag_overlay_budget_exceeded")
        # A trace in ``traces`` matches through its own assignment, so whether
        # it also suppresses curated tags cannot change the result: bind only
        # the suppressions that can.
        suppressing = sorted(self.assigned - set(traces))
        # A sentinel keeps a one-key set a set of tuples when rendered.
        return (
            (_TRACE_SENTINEL, *traces),
            (_SPAN_SENTINEL, *spans),
            (_TRACE_SENTINEL, *suppressing),
        )


def read_tag_overlay(
    *,
    project_ids: tuple[str, ...],
    trace_ids: tuple[str, ...] | None,
    values: tuple[str, ...] | None,
    deadline: ReadDeadline,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    execute: Callable[..., Any] | None = None,
    classify_deadline: bool = True,
) -> TagOverlay:
    """Resolve UI assignments for one leaf scope.

    Candidate batches read every assignment of their (at most 512) traces.
    Window scans read only assignments carrying a requested name (or any name
    for the non-empty test). Every other trace assignment can only suppress a
    matching curated tag, and only a few of them actually do; the window scan
    records whether any exist, and :func:`legacy_suppressions` finds the ones
    that matter. Window-scoped graph calls page matching assignments, then
    check liveness in ClickHouse before counting keys against the bind budget.
    The graph's own SQL remains the final authority on latest-state membership.
    """
    scope = Q(project_id__in=project_ids, entity_kind__in=("trace", "span"))
    overlay = TagOverlay()
    if trace_ids is not None:
        predicate = scope & Q(trace_id__in=trace_ids)
    else:
        names = _names_predicate(values)
        predicate = scope & names
        overlay.unmatched_trace_assignments = bool(
            _query_assignments(
                _unmatched_trace_predicate(project_ids, names),
                deadline=deadline,
                limit=0,
            )
        )
    if window_start is not None and window_end is not None and trace_ids is None:
        if execute is None:
            raise TagReadUnavailable("tag_source_unavailable")
        start, end = _utc(window_start), _utc(window_end)
        hour_start, hour_end = _hours(start, end)
        predicate &= Q(entity_kind="trace") | Q(
            entity_kind="span", start_hour__gte=hour_start, start_hour__lt=hour_end
        )
        # A graph's trace witness needs a live root in the window. Span
        # assignments additionally need their exact latest physical identity.
        # Page PostgreSQL first; only live keys count against SQL bind limits.
        # Scanned rows have their own work bound. A deadline alone does not
        # prove a history is over that bound.
        scanned = 0
        try:
            for page_number, page in enumerate(
                _assignment_pages(predicate, deadline=deadline), start=1
            ):
                if page_number > MAX_GRAPH_OVERLAY_SCAN_PAGES:
                    raise TagReadUnavailable("tag_overlay_budget_exceeded")
                candidates = TagOverlay()
                for record in page:
                    candidates.add(record)
                live: set[tuple] = set()
                for batch, batch_overlay in _liveness_batches(
                    candidates.trace_names, candidates.span_names
                ):
                    prefix = "tag_graph_live"
                    params = _scope_params(
                        prefix,
                        project_ids=project_ids,
                        window_start=start,
                        window_end=end,
                        trace_ids=batch,
                    )
                    params[f"{prefix}_trace_keys"], params[f"{prefix}_span_keys"], _ = (
                        batch_overlay.keys(None)
                    )
                    query = f"""
                        SELECT DISTINCT toString(project_id) AS project_id, trace_id,
                            if(is_root, '', id) AS span_id,
                            if(is_root, 0, toInt64(toUnixTimestamp(start_hour))) AS hour,
                            if(is_root, '', toString(observation_type)) AS observation_type,
                            if(is_root, '', toString(service_name)) AS service_name
                        FROM {live_tag_source_sql(prefix)}
                        WHERE (is_root AND (toString(project_id), trace_id)
                               IN %({prefix}_trace_keys)s)
                           OR (NOT is_root AND {span_key_sql()} IN %({prefix}_span_keys)s)
                        LIMIT %(tag_graph_live_limit)s
                    """
                    params["tag_graph_live_limit"] = MAX_TAG_OVERLAY_KEYS + 1
                    try:
                        rows = _execute_overlay_read(
                            execute,
                            query,
                            params,
                            deadline=deadline,
                            settings={},
                            row_limit=MAX_TAG_OVERLAY_KEYS + 1,
                        )
                    except (ReadDeadlineExceeded, ClickHouseError, OSError, EOFError):
                        raise TagReadUnavailable("tag_source_unavailable") from None
                    if len(rows) > MAX_TAG_OVERLAY_KEYS:
                        raise TagReadUnavailable("tag_overlay_budget_exceeded")
                    for row in rows:
                        key = (
                            (str(row["project_id"]), str(row["trace_id"]))
                            if row["span_id"] == ""
                            else (
                                str(row["project_id"]),
                                str(row["trace_id"]),
                                str(row["span_id"]),
                                int(row["hour"]),
                                str(row["observation_type"]),
                                str(row["service_name"]),
                            )
                        )
                        live.add(key)
                for record in page:
                    if _assignment_key(record) in live:
                        overlay.add(record)
                overlay.keys(values)
                scanned = page_number
        except TagReadUnavailable as error:
            if not classify_deadline:
                raise
            raise _too_broad_after_deadline(
                error, scanned_pages=scanned, deadline=deadline, predicate=predicate
            ) from None
        return overlay
    for record in _read_assignments(predicate, deadline=deadline):
        overlay.add(record)
    return overlay


def _names_predicate(values: tuple[str, ...] | None) -> Q:
    """Assignments carrying a requested name, or any name for ``None``."""
    if values is None:
        return ~Q(manual_tags=[])
    names = Q()
    for value in values:
        names |= Q(manual_tags__contains=[{"name": value}])
    return names


def _unmatched_trace_predicate(project_ids: tuple[str, ...], names: Q) -> Q:
    """Trace assignments that do not carry a requested name."""
    return Q(project_id__in=project_ids, entity_kind="trace") & ~names


def _legacy_match_query(
    prefix: str, names_predicate: str, *, live_roots: bool = False
) -> str:
    """Latest live curated ``traces.tags`` rows of the scope that match."""
    legacy = names_predicate.format(names="legacy_names")
    return f"""
        SELECT legacy_project, legacy_trace
        FROM {legacy_trace_tags_sql(prefix, live_roots=live_roots)}
        WHERE {legacy}
        LIMIT %({prefix}_limit)s
    """


def legacy_suppressions(
    execute: Callable[..., Any],
    *,
    project_ids: tuple[str, ...],
    values: tuple[str, ...] | None,
    deadline: ReadDeadline,
    settings: dict[str, Any] | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    classify_deadline: bool = True,
    on_walk_start: Callable[[], None] | None = None,
) -> set[tuple[str, str]]:
    """Trace assignments that hide a curated tag the leaf would otherwise match.

    A trace assignment that carries none of the leaf's names changes the
    leaf's result only when the trace's latest live curated ``traces.tags``
    match; then it suppresses them. That set is bounded by real edits, not by
    how many traces were ever tagged. Curated matches are read from
    ClickHouse first; when they are few, PostgreSQL is asked which of those
    traces have such an assignment. Otherwise (an importer that writes
    curated tags on every trace) the unmatched assignments are paged from
    PostgreSQL and ClickHouse is asked, a bounded batch at a time, which of
    them carry a matching curated tag. Above the overlay budget the leaf is
    over budget, never a partial suppression set.
    """
    if (
        getattr(
            getattr(execute, "__self__", None), "supports_per_query_read_settings", True
        )
        is False
    ):
        raise TagReadUnavailable("tag_source_unavailable")
    prefix = "tag_suppress"
    if values is None:
        names_predicate = "notEmpty({names})"
    else:
        names_predicate = "hasAny({names}, %(" + prefix + "_names)s)"
    unmatched = _unmatched_trace_predicate(project_ids, _names_predicate(values))
    if (window_start is None) != (window_end is None):
        raise TagReadUnavailable("tag_scope_required")
    live_roots = window_start is not None and window_end is not None
    query = _legacy_match_query(prefix, names_predicate, live_roots=live_roots)
    over_budget = "tag_overlay_budget_exceeded"

    def curated_matches(traces: tuple[str, ...] | None) -> set[tuple[str, str]]:
        """Matching curated traces of the project, or of ``traces`` only."""
        limit = (
            MAX_CURATED_SUPPRESSION_CANDIDATES
            if traces is None
            else MAX_TAG_OVERLAY_KEYS
        )
        params: dict[str, Any] = {
            f"{prefix}_projects": project_ids,
            f"{prefix}_scoped": 0 if traces is None else 1,
            f"{prefix}_traces": traces or (_UNSCOPED_TRACE,),
            f"{prefix}_assigned_keys": (_TRACE_SENTINEL,),
            f"{prefix}_limit": limit + 1,
        }
        if live_roots:
            assert window_start is not None and window_end is not None
            params.update(
                _scope_params(
                    prefix,
                    project_ids=project_ids,
                    window_start=window_start,
                    window_end=window_end,
                    trace_ids=traces,
                )
            )
        if values is not None:
            params[f"{prefix}_names"] = list(values)
        try:
            rows = _execute_overlay_read(
                execute,
                query,
                params,
                deadline=deadline,
                settings=settings or {},
                row_limit=limit + 1,
            )
        except (ReadDeadlineExceeded, ClickHouseError, OSError, EOFError):
            # Unavailable, never "nothing is suppressed".
            raise TagReadUnavailable("tag_source_unavailable") from None
        return {(str(row["legacy_project"]), str(row["legacy_trace"])) for row in rows}

    curated = curated_matches(None)
    suppressed: set[tuple[str, str]] = set()
    if len(curated) <= MAX_CURATED_SUPPRESSION_CANDIDATES:
        if curated:
            records = _query_assignments(
                unmatched & Q(trace_id__in=sorted({trace for _, trace in curated})),
                deadline=deadline,
                limit=MAX_TAG_OVERLAY_KEYS,
            )
            if len(records) > MAX_TAG_OVERLAY_KEYS:
                raise TagReadUnavailable(over_budget)
            suppressed = {_assignment_key(record) for record in records} & curated
        return suppressed
    live_hit_pages = 0
    scanned = 0
    try:
        if on_walk_start is not None:
            on_walk_start()
        for page_number, page in enumerate(
            _assignment_pages(unmatched, deadline=deadline), start=1
        ):
            # Hitless pages cost no live budget, but still count as scanned work,
            # so a lifetime walk ends as "too broad" and never as a deadline 503.
            if live_roots and page_number > MAX_GRAPH_OVERLAY_SCAN_PAGES:
                raise TagReadUnavailable(over_budget)
            keys = {_assignment_key(record) for record in page}
            traces = sorted({trace for _, trace in keys})
            page_hits: set[tuple[str, str]] = set()
            for start in range(0, len(traces), MAX_TAG_CANDIDATE_TRACES):
                batch = tuple(traces[start : start + MAX_TAG_CANDIDATE_TRACES])
                page_hits |= curated_matches(batch) & keys
            if live_roots and page_hits:
                live_hit_pages += 1
                if live_hit_pages > MAX_WINDOW_OVERLAY_PAGES:
                    raise TagReadUnavailable(over_budget)
            suppressed |= page_hits
            if len(suppressed) > MAX_TAG_OVERLAY_KEYS:
                raise TagReadUnavailable(over_budget)
            scanned = page_number
    except TagReadUnavailable as error:
        if not live_roots or not classify_deadline:
            raise
        raise _too_broad_after_deadline(
            error, scanned_pages=scanned, deadline=deadline, predicate=unmatched
        ) from None
    return suppressed


def prepare_effective_tag_params(
    execute: Callable[..., Any],
    params: dict[str, Any],
    *,
    deadline: ReadDeadline,
    allow_window_scan: bool = False,
    window_overlay: bool = False,
) -> dict[str, Any]:
    """Bind each tag leaf's scope and human-scale overlay before execution.

    Non-tag queries return the original dictionary and perform no reads.
    Source and curated tags need no preparation: the compiled SQL evaluates
    them in place. ``execute`` reads curated suppressions and, with
    ``window_overlay``, the liveness of a window scan's assignments: then only
    assignments live in the window count against the overlay budget, as for
    graph leaves, instead of every assignment the project ever saved.
    """
    pending = {
        key: value
        for key, value in params.items()
        if isinstance(value, EffectiveTagRelation)
    }
    if not pending:
        return params
    start = params.get("candidate_start_date", params.get("start_date"))
    end = params.get("candidate_end_date", params.get("end_date"))
    ids = params.get("candidate_trace_ids", params.get("candidate_span_trace_ids"))
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or (not ids and not allow_window_scan)
    ):
        raise TagReadUnavailable("tag_scope_required")
    trace_ids = tuple(str(value) for value in ids) if ids else None
    bound = dict(params)
    cache: dict[tuple, TagOverlay] = {}
    rendered = 0
    for name, request in pending.items():
        bound.pop(name)
        scope = _scope_params(
            name,
            project_ids=request.project_ids,
            window_start=start,
            window_end=end,
            trace_ids=trace_ids,
            root_project_version=params.get("project_version_id"),
        )
        bound.update(scope)
        cache_key = (request.project_ids, request.values)
        if cache_key not in cache:
            windowed = window_overlay and trace_ids is None
            overlay = read_tag_overlay(
                project_ids=request.project_ids,
                trace_ids=trace_ids,
                values=request.values,
                deadline=deadline,
                window_start=start if windowed else None,
                window_end=end if windowed else None,
                execute=execute if windowed else None,
            )
            if overlay.unmatched_trace_assignments:
                # A window scan: bind only the suppressions that matter.
                overlay.assigned |= legacy_suppressions(
                    execute,
                    project_ids=request.project_ids,
                    values=request.values,
                    deadline=deadline,
                    window_start=start,
                    window_end=end,
                )
            cache[cache_key] = overlay
        trace_keys, span_keys, assigned = cache[cache_key].keys(request.values)
        bound[f"{name}_trace_keys"] = trace_keys
        bound[f"{name}_span_keys"] = span_keys
        bound[f"{name}_assigned_keys"] = assigned
        # The candidate ids are rendered once per spans/traces scope clause.
        rendered += (
            _literal_bytes(trace_keys)
            + _literal_bytes(span_keys)
            + _literal_bytes(assigned)
            + 3 * _literal_bytes(scope[f"{name}_traces"])
        )
        if rendered > MAX_TAG_OVERLAY_BYTES:
            raise TagReadUnavailable("tag_overlay_budget_exceeded")
    deadline.remaining_ms()
    return bound


def _liveness_batches(
    trace_names: dict[tuple[str, str], tuple[str, ...]],
    span_names: dict[tuple, tuple[str, ...]],
) -> Iterable[tuple[tuple[str, ...], TagOverlay]]:
    """Group keys by trace into batches that fit the candidate/byte budgets."""
    by_trace: dict[str, list[tuple[bool, tuple]]] = {}
    for key in trace_names:
        by_trace.setdefault(key[1], []).append((True, key))
    for key in span_names:
        by_trace.setdefault(key[1], []).append((False, key))
    batch: list[str] = []
    overlay = TagOverlay()
    rendered = 0
    for trace in sorted(by_trace):
        keys = by_trace[trace]
        size = _literal_bytes([trace]) * 3 + sum(
            _literal_bytes([key]) for _, key in keys
        )
        if batch and (
            len(batch) >= MAX_TAG_CANDIDATE_TRACES
            or rendered + size > MAX_TAG_OVERLAY_BYTES // 2
        ):
            yield tuple(batch), overlay
            batch, overlay, rendered = [], TagOverlay(), 0
        batch.append(trace)
        rendered += size
        for is_trace, key in keys:
            if is_trace:
                overlay.trace_names[key] = trace_names[key]
            else:
                overlay.span_names[key] = span_names[key]
    if batch:
        yield tuple(batch), overlay


def _execute_overlay_read(
    execute: Callable[..., Any],
    query: str,
    params: dict[str, Any],
    *,
    deadline: ReadDeadline,
    settings: dict[str, Any],
    row_limit: int,
) -> list[dict[str, Any]]:
    result = execute(
        query,
        params,
        timeout_ms=deadline.remaining_ms(),
        server_execution_cap_ms=deadline.remaining_ms(),
        settings={**settings, **TAG_OVERLAY_SETTINGS, "max_result_rows": row_limit},
    )
    return list(result.data or [])


@dataclass
class TagVocabularyCache:
    """Request-local assignment snapshot; large vocabularies retain the paged walk."""

    window_start: datetime
    window_end: datetime
    pages: tuple[list[dict[str, Any]], ...] | None = None
    too_large: bool = False


def _assignment_vocabulary(
    execute: Callable[..., Any],
    *,
    project_ids: tuple[str, ...],
    metric_name: str,
    window_start: datetime,
    window_end: datetime,
    deadline: ReadDeadline,
    settings: dict[str, Any],
    assignment_cache: TagVocabularyCache | None = None,
) -> tuple[set[str], set[tuple[str, str]] | None]:
    """UI names on entities live in the window, and the trace assignments.

    The walk pages through assignments; a record whose names are all already
    found needs no liveness check. The second value is every assigned trace
    when that set fits the overlay budget, else ``None``.
    """
    start, end = _utc(window_start), _utc(window_end)
    hour_start, hour_end = _hours(start, end)
    scope = Q(project_id__in=project_ids)

    def predicate_for(hours: tuple[datetime, datetime]) -> Q:
        predicate = scope & Q(entity_kind="trace")
        if metric_name == "tag":
            predicate |= (
                scope
                & Q(
                    entity_kind="span",
                    start_hour__gte=hours[0],
                    start_hour__lt=hours[1],
                )
                & ~Q(manual_tags=[])
            )
        return predicate

    pages: Iterable[list[dict[str, Any]]]
    if assignment_cache is not None and not assignment_cache.too_large:
        if assignment_cache.pages is None:
            snapshot: list[list[dict[str, Any]]] = []
            count = 0
            full_hours = _hours(
                _utc(assignment_cache.window_start), _utc(assignment_cache.window_end)
            )
            for page in _assignment_pages(predicate_for(full_hours), deadline=deadline):
                count += len(page)
                if count > MAX_TAG_OVERLAY_KEYS:
                    assignment_cache.too_large = True
                    snapshot.clear()
                    break
                snapshot.append(page)
            if not assignment_cache.too_large:
                assignment_cache.pages = tuple(snapshot)
        pages = (
            assignment_cache.pages
            if assignment_cache.pages is not None
            else _assignment_pages(
                predicate_for((hour_start, hour_end)), deadline=deadline
            )
        )
    else:
        pages = _assignment_pages(
            predicate_for((hour_start, hour_end)), deadline=deadline
        )
    found: set[str] = set()
    assigned: set[tuple[str, str]] | None = set()
    prefix = "tag_vocab"
    for page in pages:
        overlay = TagOverlay()
        for record in page:
            if record["entity_kind"] == "span" and not (
                hour_start <= _utc(record["start_hour"]) < hour_end
            ):
                continue
            overlay.add(record)
        if assigned is not None:
            assigned |= overlay.assigned
            if len(assigned) > MAX_TAG_OVERLAY_KEYS:
                assigned = None
        trace_names = {
            key: names
            for key, names in overlay.trace_names.items()
            if not set(names) <= found
        }
        span_names = {
            key: names
            for key, names in overlay.span_names.items()
            if not set(names) <= found
        }
        for batch, batch_overlay in _liveness_batches(trace_names, span_names):
            params = _scope_params(
                prefix,
                project_ids=project_ids,
                window_start=start,
                window_end=end,
                trace_ids=batch,
            )
            params[f"{prefix}_trace_keys"], params[f"{prefix}_span_keys"], _ = (
                batch_overlay.keys(None)
            )
            params["tag_row_limit"] = MAX_TAG_OVERLAY_KEYS + 1
            query = f"""
                SELECT DISTINCT toString(project_id) AS project_id, trace_id,
                    if(is_root, '', id) AS span_id,
                    if(is_root, 0, toInt64(toUnixTimestamp(start_hour))) AS hour,
                    if(is_root, '', toString(observation_type)) AS observation_type,
                    if(is_root, '', toString(service_name)) AS service_name
                FROM {live_tag_source_sql(prefix)}
                WHERE (is_root AND (toString(project_id), trace_id)
                       IN %({prefix}_trace_keys)s)
                   OR (NOT is_root AND {span_key_sql()} IN %({prefix}_span_keys)s)
                LIMIT %(tag_row_limit)s
            """
            for row in _execute_overlay_read(
                execute,
                query,
                params,
                deadline=deadline,
                settings=settings,
                row_limit=MAX_TAG_OVERLAY_KEYS + 1,
            ):
                if row["span_id"] == "":
                    trace_key = (row["project_id"], row["trace_id"])
                    found.update(trace_names.get(trace_key, ()))
                else:
                    span_key = (
                        row["project_id"],
                        row["trace_id"],
                        row["span_id"],
                        int(row["hour"]),
                        row["observation_type"],
                        row["service_name"],
                    )
                    found.update(span_names.get(span_key, ()))
    return found, assigned


def _legacy_vocabulary(
    execute: Callable[..., Any],
    *,
    project_ids: tuple[str, ...],
    window_start: datetime,
    window_end: datetime,
    assigned: set[tuple[str, str]] | None,
    deadline: ReadDeadline,
    settings: dict[str, Any],
    after: str | None,
    search: str,
    limit: int,
) -> set[str]:
    """Curated trace names of traces with a live root in the window.

    Suppression by saved assignments is applied exactly while the assigned set
    fits the overlay budget; above it the vocabulary may include a curated name
    every carrier has since cleared, never omit a live one.
    """
    prefix = "tag_legacy_vocab"
    params = _scope_params(
        prefix,
        project_ids=project_ids,
        window_start=window_start,
        window_end=window_end,
        trace_ids=None,
    )
    if assigned and _literal_bytes(assigned) <= MAX_TAG_OVERLAY_BYTES:
        params[f"{prefix}_assigned_keys"] = (_TRACE_SENTINEL, *sorted(assigned))
    params.update(
        {
            "tag_after_any": 1 if after is None else 0,
            "tag_after": after or "",
            "tag_search": search,
            "tag_value_limit": int(limit),
        }
    )
    query = f"""
        SELECT DISTINCT name
        FROM (
            SELECT legacy_names
            FROM {legacy_trace_tags_sql(prefix, live_roots=True)}
        ) ARRAY JOIN legacy_names AS name
        WHERE (%(tag_after_any)s = 1 OR name > %(tag_after)s)
          AND (%(tag_search)s = '' OR positionCaseInsensitiveUTF8(name, %(tag_search)s) > 0)
        ORDER BY name
        LIMIT %(tag_value_limit)s
    """
    rows = _execute_overlay_read(
        execute,
        query,
        params,
        deadline=deadline,
        settings=settings,
        row_limit=int(limit),
    )
    return {str(row["name"]) for row in rows}


def effective_tag_values(
    execute: Callable[..., Any],
    *,
    project_ids: tuple[str, ...],
    metric_name: str,
    window_start: datetime,
    window_end: datetime,
    deadline: ReadDeadline,
    search: str = "",
    after: str | None = None,
    limit: int,
    settings: dict[str, Any] | None = None,
    assignment_cache: TagVocabularyCache | None = None,
) -> tuple[str, ...]:
    """Sorted effective names in the window: ``tag`` any span, ``tags`` root.

    Source and curated names are aggregated in ClickHouse (DISTINCT over the
    projected tag sources, LIMITed after the keyset/search predicates); only
    UI assignment names are resolved in Python, with a liveness check.
    """
    if (
        getattr(
            getattr(execute, "__self__", None), "supports_per_query_read_settings", True
        )
        is False
    ):
        raise TagReadUnavailable("tag_source_unavailable")
    prefix = "tag_values"
    params = _scope_params(
        prefix,
        project_ids=project_ids,
        window_start=window_start,
        window_end=window_end,
        trace_ids=None,
    )
    params.update(
        {
            "tag_after_any": 1 if after is None else 0,
            "tag_after": after or "",
            "tag_search": search,
            "tag_value_limit": int(limit),
        }
    )
    source = (
        f"SELECT src FROM {live_tag_source_sql(prefix)}"
        if metric_name == "tag"
        else f"SELECT src FROM {canonical_root_sql(prefix)}"
    )
    query = f"""
        SELECT DISTINCT name
        FROM ({source}) ARRAY JOIN src AS name
        WHERE (%(tag_after_any)s = 1 OR name > %(tag_after)s)
          AND (%(tag_search)s = '' OR positionCaseInsensitiveUTF8(name, %(tag_search)s) > 0)
        ORDER BY name
        LIMIT %(tag_value_limit)s
    """
    read_settings = {**(settings or {}), "max_result_rows": int(limit)}
    result = execute(
        query,
        params,
        timeout_ms=deadline.remaining_ms(),
        server_execution_cap_ms=deadline.remaining_ms(),
        settings=read_settings,
    )
    values = {str(row["name"]) for row in result.data or []}
    manual, assigned = _assignment_vocabulary(
        execute,
        project_ids=project_ids,
        metric_name=metric_name,
        window_start=window_start,
        window_end=window_end,
        deadline=deadline,
        settings=settings or {},
        assignment_cache=assignment_cache,
    )
    folded = search.casefold()
    values.update(
        name
        for name in manual
        if (after is None or name > after) and (not search or folded in name.casefold())
    )
    values.update(
        _legacy_vocabulary(
            execute,
            project_ids=project_ids,
            window_start=window_start,
            window_end=window_end,
            assigned=assigned,
            deadline=deadline,
            settings=settings or {},
            after=after,
            search=search,
            limit=limit,
        )
    )
    deadline.remaining_ms()
    return tuple(sorted(values)[:limit])
