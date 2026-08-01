"""ClickHouse-backed source vehicles for eval execution.

The eval-task engine forces ``eval_read_source("clickhouse")``: span, trace,
and session telemetry is loaded only from ClickHouse, and a miss/error never
falls back to PostgreSQL. Legacy non-task callers can still explicitly select
the PostgreSQL mode while that path remains supported.

ClickHouse rows are adapted into unsaved Django model instances so the existing
evaluation core can keep using attribute access without implying that a source
row exists in PostgreSQL. ``save()`` is a no-op on those vehicles; task state
and results are persisted on the materialized EvalLogger entry instead.
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog
from django.conf import settings

if TYPE_CHECKING:
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.project import Project
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

logger = structlog.get_logger(__name__)

# Per-execution override of the read source. The new eval engine sets this to
# "clickhouse" for the duration of one entry's run (see run_entry). Production
# defaults to ClickHouse because telemetry is no longer written to PostgreSQL.
_forced_source: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "eval_read_source_override", default=None
)


@contextmanager
def eval_read_source(source: str):
    """Force ``_read_source()`` to ``source`` within the block."""
    token = _forced_source.set(source.lower())
    try:
        yield
    finally:
        _forced_source.reset(token)


def _read_source() -> str:
    """Resolve the active read source: per-execution override first, else
    settings, env, or the ClickHouse-only production default.

    PostgreSQL telemetry reads remain available only under Django's test
    settings so legacy unit fixtures can create model rows without seeding CH.
    A production override back to PostgreSQL fails closed: direct ingestion no
    longer writes those rows, so a fallback would silently evaluate stale or
    missing telemetry.
    """
    src = (
        _forced_source.get()
        or getattr(settings, "EVAL_SPAN_READ_SOURCE", None)
        or os.environ.get("EVAL_SPAN_READ_SOURCE")
        or "clickhouse"
    ).lower()
    if src == "clickhouse":
        return src
    if src == "postgres" and getattr(settings, "TESTING", False):
        return src
    raise RuntimeError(
        "Eval telemetry must be read from ClickHouse; PostgreSQL telemetry "
        f"source {src!r} is not supported in production"
    )


def get_observation_span(
    span_id: str, *, select_related: tuple[str, ...] = (), project_id: str | None = None
) -> ObservationSpan:
    """Return an ObservationSpan instance for the given id.

    Mirrors the surface area of `ObservationSpan.objects.select_related(*).get(id=...)`
    so the eval runner can swap call sites mechanically.

    ``project_id`` (the eval config's project) scopes the CH read to one tenant.
    ``spans`` is sorted by ``project_id`` first, so passing it lets ClickHouse
    prune by the primary index instead of scanning every project's parts for the
    span id — the difference between a whole-table read and a pruned one.

    Raises `ObservationSpan.DoesNotExist` (same as the Django path) when no
    such span — keeps downstream `except ObservationSpan.DoesNotExist` blocks
    in the eval runner working unchanged.
    """
    from tracer.models.observation_span import ObservationSpan

    src = _read_source()

    if src == "postgres":
        # The original path — preserved as the default during rollout.
        qs = ObservationSpan.objects
        if select_related:
            qs = qs.select_related(*select_related)
        return qs.get(id=span_id)

    # ── v2 path: read span data from CH, construct partial Django model,
    # let FK descriptors lazy-load from PG on attribute access.
    return _hybrid_load_from_ch(span_id, select_related, project_id=project_id)


def _hybrid_load_from_ch(
    span_id: str,
    select_related: tuple[str, ...],
    *,
    project_id: str | None = None,
) -> ObservationSpan:
    """Reads the span row from CH and returns a partially-hydrated Django
    ObservationSpan whose FK descriptors will lazy-load from PG on access.

    If the CH read fails or the row isn't there, fail closed. PostgreSQL has no
    authoritative telemetry row after the direct-to-CH cutover.
    """
    from tracer.models.observation_span import ObservationSpan
    from tracer.services.clickhouse.v2 import get_reader

    try:
        # One eval-task entry opens one point reader. Close it immediately after
        # the read; historical tasks can contain millions of entries and leaking
        # one clickhouse-connect HTTP client per entry eventually exhausts the
        # worker's sockets/file descriptors.
        with get_reader() as reader:
            ch_row = reader.get(span_id, project_id=project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "eval_span_ch_read_failed", span_id=span_id, err=repr(e)[:200]
        )
        raise

    if ch_row is None:
        raise ObservationSpan.DoesNotExist(
            f"Span {span_id} not in ClickHouse (CH-direct; PG fallback disabled)"
        )

    return _construct_from_chspan(ch_row)


def filter_observation_spans_by_trace(
    trace_id: str,
    deleted: bool = False,
    *,
    project_id: str | None = None,
    heavy_span_ids: set[str] | None = None,
):
    """v2 equivalent of `ObservationSpan.objects.filter(trace=trace, deleted=False)`.

    Returns a list of ObservationSpan instances (NOT a QuerySet). Eval-runner
    aggregate sites that iterate the result work unchanged; sites that chain
    additional `.filter()` calls need explicit porting.

    ``project_id`` scopes the CH read so ClickHouse can prune ``spans`` by the
    ``project_id`` sort-key prefix instead of scanning all projects for the
    trace's spans.

    ``heavy_span_ids`` (optional): ids whose heavy columns (attributes_extra /
    span_events / resource_attrs) are required. A lean pass loads all spans
    first; a second heavy pass fetches only those spans in ``heavy_span_ids``
    that actually appeared in the trace, merging them back in. Spans not in
    the set stay lean. Pass ``None`` (default) for a fully lean load.
    """
    from tracer.models.observation_span import ObservationSpan

    src = _read_source()
    if src != "clickhouse":
        return list(ObservationSpan.objects.filter(trace_id=trace_id, deleted=deleted))

    try:
        from tracer.services.clickhouse.v2 import get_reader

        with get_reader() as reader:
            ch_rows = reader.list_by_trace(
                trace_id, include_heavy=False, project_id=project_id
            )

            # Second pass: replace lean rows for the requested heavy span ids.
            wanted = set(heavy_span_ids or ()) & {r.id for r in ch_rows}
            if wanted:
                heavy_map = {
                    r.id: r
                    for r in reader.list_by_ids(
                        list(wanted), include_heavy=True, project_id=project_id
                    )
                }
                ch_rows = [heavy_map.get(r.id, r) for r in ch_rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "eval_span_filter_ch_failed", trace_id=trace_id, err=repr(e)[:200]
        )
        raise

    out = []
    for ch_row in ch_rows:
        obj = _construct_from_chspan(ch_row)
        out.append(obj)
    return out


def _construct_from_chspan(ch_row) -> ObservationSpan:
    """Shared body of the hybrid-construct logic."""
    import json as _j

    from tracer.models.observation_span import ObservationSpan
    from tracer.services.clickhouse.v2.span_reader import _ch_span_attributes

    obj = ObservationSpan(
        id=ch_row.id,
        project_id=ch_row.project_id,
        project_version_id=ch_row.project_version_id,
        trace_id=ch_row.trace_id,
        parent_span_id=ch_row.parent_span_id or None,
        name=ch_row.name,
        observation_type=ch_row.observation_type,
        operation_name=ch_row.operation_name or None,
        start_time=ch_row.start_time,
        end_time=ch_row.end_time,
        model=ch_row.model or None,
        provider=ch_row.provider or None,
        prompt_tokens=ch_row.prompt_tokens,
        completion_tokens=ch_row.completion_tokens,
        total_tokens=ch_row.total_tokens,
        cost=ch_row.cost,
        status=ch_row.status or None,
        status_message=ch_row.status_message or None,
        eval_status=ch_row.eval_status or "INACTIVE",
        org_id=ch_row.org_id,
        end_user_id=ch_row.end_user_id,
        prompt_version_id=ch_row.prompt_version_id,
        prompt_label_id=ch_row.prompt_label_id,
        custom_eval_config_id=ch_row.custom_eval_config_id,
        semconv_source=ch_row.semconv_source,
    )
    try:
        obj.input = _j.loads(ch_row.input) if ch_row.input else None
        obj.output = _j.loads(ch_row.output) if ch_row.output else None
    except Exception:  # noqa: BLE001
        obj.input = ch_row.input or None
        obj.output = ch_row.output or None

    obj.span_attributes = _ch_span_attributes(ch_row)

    try:
        obj.span_events = _j.loads(ch_row.span_events) if ch_row.span_events else []
        obj.resource_attributes = (
            _j.loads(ch_row.resource_attrs) if ch_row.resource_attrs else {}
        )
    except Exception:  # noqa: BLE001
        obj.span_events = []
        obj.resource_attributes = {}

    obj._state.adding = False
    obj._state.db = "default"

    # A CH-hydrated span has no PG row, so a real save() (UPDATE→0 rows→INSERT)
    # would create a phantom span. The new engine records the terminal result on
    # the EvalLogger entry, not the span — so eval_status writeback is a no-op
    # against PG here.
    # Flag-gated behavior change: if EVAL_SPAN_READ_SOURCE=clickhouse is ever set
    # *globally* (not just per-run by the engine), the legacy
    # eval_observation_span_runner's observation_span.save() eval_status
    # writeback to PG silently no-ops through here too. Safe on the default
    # postgres path, where the legacy cron actually runs.
    obj.save = lambda *args, **kwargs: None

    # Resolve span.trace without a PG hit: the span eval path reads
    # span.trace.id and passes the Trace into the EvalLogger FK
    # (db_constraint=False), so an id-only unsaved Trace suffices.
    if ch_row.trace_id:
        from tracer.models.trace import Trace

        trace = Trace(id=ch_row.trace_id, project_id=ch_row.project_id)
        trace._state.adding = False
        trace._state.db = "default"
        obj.trace = trace
    return obj


def get_trace(
    trace_id: str,
    *,
    select_related: tuple[str, ...] = (),
    reader: CHSpanReader | None = None,
    project_id: str | None = None,
) -> Trace:
    """Return a Trace instance for the id. CH mode hydrates it from the CH
    ``traces`` table (the same store the trace list endpoints read), so
    trace-level fields (input/output/tags/metadata/error) match the UI; PG mode
    keeps the Django path. Raises Trace.DoesNotExist when forced-CH and the
    trace isn't in ClickHouse. Pass ``reader`` to reuse an open CHSpanReader
    across a loop of traces instead of opening (and leaking) one per call.

    ``project_id`` scopes the CH read; ``traces`` is sorted ``(project_id, id)``
    and has no bloom on ``id``, so without it a lone-id lookup scans every
    project's parts — passing it enables the sort-key prefix prune."""
    import json as _json

    from tracer.models.trace import Trace
    from tracer.services.clickhouse.v2 import get_reader

    if _read_source() != "clickhouse":
        qs = Trace.objects
        if select_related:
            qs = qs.select_related(*select_related)
        return qs.get(id=trace_id)

    try:
        if reader is not None:
            row = reader.get_trace_row(str(trace_id), project_id=project_id)
        else:
            with get_reader() as _reader:
                row = _reader.get_trace_row(str(trace_id), project_id=project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "eval_trace_ch_read_failed", trace_id=str(trace_id), err=repr(e)[:200]
        )
        raise
    if row is None:
        raise Trace.DoesNotExist(
            f"Trace {trace_id} not in ClickHouse (CH-direct; PG fallback disabled)"
        )

    def _decode(v):
        if not v:
            return None
        try:
            return _json.loads(v)
        except Exception:  # noqa: BLE001 — opaque non-JSON blob
            return v

    obj = Trace(
        id=row["id"],
        project_id=row["project_id"],
        project_version_id=row.get("project_version_id") or None,
        name=row.get("name") or "",
        input=_decode(row.get("input")),
        output=_decode(row.get("output")),
        metadata=_decode(row.get("metadata")),
        error=_decode(row.get("error")),
        tags=_decode(row.get("tags")) or [],
        external_id=row.get("external_id") or None,
        session_id=row.get("session_id") or None,
        error_analysis_status=row.get("error_analysis_status") or "PENDING",
        created_at=row.get("created_at"),
    )
    obj._state.adding = False
    obj._state.db = "default"
    obj.save = lambda *args, **kwargs: None
    return obj


def get_trace_session(session_id: str, *, project: Project) -> TraceSession:
    """Return a TraceSession for the id. CH mode builds an unsaved vehicle from
    the curated CH session fields (the same source the session list endpoint
    uses); PG mode keeps the Django path. Raises TraceSession.DoesNotExist when
    forced-CH and the session isn't in ClickHouse."""
    from tracer.models.trace_session import TraceSession

    if _read_source() != "clickhouse":
        return TraceSession.objects.get(id=session_id)

    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    fields = resolve_session_fields([session_id], project_id=str(project.id)).get(
        str(session_id)
    )
    if not fields:
        raise TraceSession.DoesNotExist(
            f"TraceSession {session_id} not in ClickHouse "
            "(CH-direct; PG fallback disabled)"
        )

    obj = TraceSession(
        id=session_id,
        name=fields.get("display_name") or fields.get("external_session_id") or "",
        bookmarked=bool(fields.get("bookmarked")),
        created_at=fields.get("first_seen"),
        project=project,
    )
    obj._state.adding = False
    obj._state.db = "default"
    obj.save = lambda *args, **kwargs: None
    return obj
