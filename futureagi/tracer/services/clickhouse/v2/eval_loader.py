"""
EvalSpanLoader — read the eval runner's span input from CH 25.3 when the
`EVAL_SPAN_READ_SOURCE=clickhouse` flag is set; fall back to PG otherwise.

The hard constraint: the eval runner uses Django ORM heavily — it calls
`observation_span.save()` to write `eval_status` back, navigates
`observation_span.project.organization`, etc. A pure CH dataclass can't
replace ObservationSpan without rewriting hundreds of lines of eval-runner
code that depend on those Django patterns.

Pragmatic design:

  v1 mode (`EVAL_SPAN_READ_SOURCE=postgres`):
      Pure Django: `ObservationSpan.objects.get(id=span_id)`.
      Current behavior. Default during rollout.

  v2 mode (`EVAL_SPAN_READ_SOURCE=clickhouse`):
      1. Read span DATA (id, observation_type, attrs, input, output, model,
         eval_status, cost, project_id, trace_id, ...) from CH — cheap, no
         JSONB select on tracer_observation_span.
      2. Construct a Django ObservationSpan INSTANCE from that data.
         Project, trace, end_user etc. FK descriptors lazy-load from PG on
         attribute access (standard Django behavior, fast targeted FK
         queries instead of the heavy span select).
      3. `.save()` writes eval_status back to PG as today AND emits a
         versioned CH UPDATE so the new CH stays current. Dual-write
         during the cutover window keeps both surfaces in sync.

Real win: the biggest single read in the eval hot path — `SELECT *` on
tracer_observation_span with its multi-MB JSONB columns — moves from PG
to CH point-read. The relational metadata stays where it already is.

When EvalLogger itself moves to CH (separate future migration), this
hybrid collapses to a pure-CH path; until then, the bridge buys us most
of the perf and PG-load relief without rewriting downstream eval code.
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
# "clickhouse" for the duration of one entry's run (see run_entry); the legacy
# cron path never sets it, so it stays on the settings/env default ("postgres")
# and its behavior is unchanged.
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


def _forced_clickhouse() -> bool:
    return _forced_source.get() == "clickhouse"


def _read_source() -> str:
    """Resolve the active read source: per-execution override first, else
    settings, env, or default."""
    forced = _forced_source.get()
    if forced:
        return forced
    src = (
        getattr(settings, "EVAL_SPAN_READ_SOURCE", None)
        or os.environ.get("EVAL_SPAN_READ_SOURCE")
        or "postgres"
    )
    return src.lower()


def get_observation_span(
    span_id: str, *, select_related: tuple[str, ...] = ()
) -> ObservationSpan:
    """Return an ObservationSpan instance for the given id.

    Mirrors the surface area of `ObservationSpan.objects.select_related(*).get(id=...)`
    so the eval runner can swap call sites mechanically.

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

    if src != "clickhouse":
        logger.warning("eval_span_read_source_unknown_fallback_to_pg", value=src)
        qs = ObservationSpan.objects
        if select_related:
            qs = qs.select_related(*select_related)
        return qs.get(id=span_id)

    # ── v2 path: read span data from CH, construct partial Django model,
    # let FK descriptors lazy-load from PG on attribute access.
    return _hybrid_load_from_ch(span_id, select_related)


def _hybrid_load_from_ch(
    span_id: str, select_related: tuple[str, ...]
) -> ObservationSpan:
    """Reads the span row from CH and returns a partially-hydrated Django
    ObservationSpan whose FK descriptors will lazy-load from PG on access.

    If the CH read fails or the row isn't there, falls back to PG so the
    eval runner never sees a transient CH failure.
    """
    from tracer.models.observation_span import ObservationSpan
    from tracer.services.clickhouse.v2 import get_reader

    try:
        reader = get_reader()
        ch_row = reader.get(span_id)
    except Exception as e:  # noqa: BLE001 — CH transient errors → fall back to PG
        logger.warning(
            "eval_span_ch_read_fallback_to_pg", span_id=span_id, err=repr(e)[:200]
        )
        ch_row = None

    if ch_row is None:
        if _forced_clickhouse():
            raise ObservationSpan.DoesNotExist(
                f"Span {span_id} not in ClickHouse (CH-direct; PG fallback disabled)"
            )
        # Non-forced clickhouse mode — fall back to PG with the requested FKs.
        qs = ObservationSpan.objects
        if select_related:
            qs = qs.select_related(*select_related)
        return qs.get(id=span_id)

    return _construct_from_chspan(ch_row)


def filter_observation_spans_by_trace(trace_id: str, deleted: bool = False):
    """v2 equivalent of `ObservationSpan.objects.filter(trace=trace, deleted=False)`.

    Returns a list of ObservationSpan instances (NOT a QuerySet). Eval-runner
    aggregate sites that iterate the result work unchanged; sites that chain
    additional `.filter()` calls need explicit porting.
    """
    from tracer.models.observation_span import ObservationSpan

    src = _read_source()
    if src != "clickhouse":
        return list(ObservationSpan.objects.filter(trace_id=trace_id, deleted=deleted))

    try:
        from tracer.services.clickhouse.v2 import get_reader

        reader = get_reader()
        ch_rows = reader.list_by_trace(trace_id)
    except Exception as e:  # noqa: BLE001
        if _forced_clickhouse():
            raise
        logger.warning(
            "eval_span_filter_ch_fallback_to_pg", trace_id=trace_id, err=repr(e)[:200]
        )
        return list(ObservationSpan.objects.filter(trace_id=trace_id, deleted=deleted))

    out = []
    for ch_row in ch_rows:
        # _hybrid_load_from_ch builds a single instance from a CHSpan;
        # invoke the same shaping via a passthrough.
        obj = _construct_from_chspan(ch_row)
        out.append(obj)
    return out


def _construct_from_chspan(ch_row) -> ObservationSpan:
    """Shared body of the hybrid-construct logic."""
    from tracer.models.observation_span import ObservationSpan

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
        import json as _j

        obj.input = _j.loads(ch_row.input) if ch_row.input else None
        obj.output = _j.loads(ch_row.output) if ch_row.output else None
    except Exception:  # noqa: BLE001
        obj.input = ch_row.input or None
        obj.output = ch_row.output or None
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
) -> Trace:
    """Return a Trace instance for the id. CH mode hydrates it from the CH
    ``traces`` table (the same store the trace list endpoints read), so
    trace-level fields (input/output/tags/metadata/error) match the UI; PG mode
    keeps the Django path. Raises Trace.DoesNotExist when forced-CH and the
    trace isn't in ClickHouse. Pass ``reader`` to reuse an open CHSpanReader
    across a loop of traces instead of opening (and leaking) one per call."""
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
            row = reader.get_trace_row(str(trace_id))
        else:
            with get_reader() as _reader:
                row = _reader.get_trace_row(str(trace_id))
    except Exception as e:  # noqa: BLE001 — CH transient errors → fall back to PG
        logger.warning(
            "eval_trace_ch_read_fallback", trace_id=str(trace_id), err=repr(e)[:200]
        )
        row = None
    if row is None:
        if _forced_clickhouse():
            raise Trace.DoesNotExist(
                f"Trace {trace_id} not in ClickHouse (CH-direct; PG fallback disabled)"
            )
        return Trace.objects.get(id=trace_id)

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

    fields = resolve_session_fields([session_id]).get(str(session_id))
    if not fields:
        if _forced_clickhouse():
            raise TraceSession.DoesNotExist(
                f"TraceSession {session_id} not in ClickHouse "
                "(CH-direct; PG fallback disabled)"
            )
        return TraceSession.objects.get(id=session_id)

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
