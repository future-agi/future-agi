"""One experiment run's traces, joined by run id.

An SDK run's traces are stored in an Observe project (the collector creates it
by name), so the run page cannot find them by project or project version. The
SDK writes the link twice: every stored run item carries its trial's
``trace_id`` / ``root_span_id``, and every span of the trial carries the run id
as ``fi.experiment.run_id``. This read joins on both: it takes a page of the
run's items, reads the page's root spans from the span store inside the
caller's project scope (one bounded read for the whole page:
``tracer.services.clickhouse.v2.trace_root_reads``, two queries whatever the
page size), and lists a trace only when its root span names THIS run.

A trace whose root names another run, or names none, is never listed as this
run's; it is reported in ``not_joined`` with the reason, as is a trace the
span store does not hold (yet). Reads never write.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem

if TYPE_CHECKING:
    from tracer.services.clickhouse.v2.trace_root_reads import TraceRoot

RUN_ID_ATTRIBUTE = "fi.experiment.run_id"
EXAMPLE_ATTRIBUTE = "fi.experiment.example_id"
TRIAL_ATTRIBUTE = "fi.experiment.trial"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100

# reasons a recorded trace is not listed as the run's
NOT_FOUND = "not_found"  # the span store has no such trace in the caller's projects
OTHER_RUN = "other_run"  # its root span names another run id
NO_RUN_ID = "no_run_id"  # its root span names no run id


_OTEL_TRACE_ID = re.compile(r"[0-9a-fA-F]{32}")


def store_trace_id(recorded: str) -> str:
    """The span store's form of a recorded trace id. The SDK records the
    OpenTelemetry id (32 hex digits); the span store (fi-collector) keeps it
    as a dashed UUID string, the form the trace view and its links use."""
    recorded = str(recorded).strip()
    if _OTEL_TRACE_ID.fullmatch(recorded):
        return str(uuid.UUID(hex=recorded))
    return recorded


@dataclass(frozen=True)
class RunTracePage:
    traces: list[dict[str, Any]]
    not_joined: list[dict[str, Any]]
    items_with_trace: int
    limit: int
    offset: int
    has_more: bool


# (trace id, recorded root span id or None) of each page item -> what the span
# store holds of each: its served project, live span count and root span row
ReadTraces = Callable[[list[tuple[str, str | None]]], dict[str, "TraceRoot"]]


def _attributes(row: dict[str, Any]) -> dict[str, Any]:
    from tracer.services.clickhouse.v2.span_reader import merge_span_attributes

    return merge_span_attributes(
        row.get("attrs_string"),
        row.get("attrs_number"),
        row.get("attrs_bool"),
        row.get("span_attributes"),
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _default_reader(project_ids: list[str]) -> ReadTraces:
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
    from tracer.services.clickhouse.v2.trace_root_reads import read_trace_roots

    analytics = V2AnalyticsQueryService()

    def read(wanted: list[tuple[str, str | None]]) -> dict[str, TraceRoot]:
        return read_trace_roots(
            analytics=analytics, project_ids=project_ids, wanted=wanted
        )

    return read


def list_run_traces(
    run: ExperimentRun,
    *,
    project_ids: Iterable[str],
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    read_traces: ReadTraces | None = None,
) -> RunTracePage:
    """A page of the run's items that recorded a trace (plan order), read
    from the span store in one bounded read for the page; each listed only
    when its root span carries this run's id. ``read_traces`` (tests) maps
    the page's (trace id, recorded root span id) pairs to what is stored."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    project_ids = [str(p) for p in project_ids]
    items = (
        ExperimentRunItem.no_workspace_objects.filter(run_id=run.id, deleted=False)
        .exclude(trace_id__isnull=True)
        .exclude(trace_id="")
        .order_by("example_id", "trial_number", "id")
    )
    total = items.count()
    page = list(items[offset : offset + limit])
    if read_traces is not None:
        reader = read_traces
    elif project_ids:
        reader = _default_reader(project_ids)
    else:  # no project in scope: nothing can be read, so nothing is listed
        reader = lambda _wanted: {}  # noqa: E731
    wanted = [
        (store_trace_id(item.trace_id), item.root_span_id or None) for item in page
    ]
    stored = reader(wanted) if wanted else {}
    run_id = str(run.id)
    traces: list[dict[str, Any]] = []
    not_joined: list[dict[str, Any]] = []
    for item, (trace_id, _) in zip(page, wanted, strict=True):
        base = {
            "trace_id": trace_id,
            "root_span_id": item.root_span_id,
            "example_id": str(item.example_id),
            "trial_number": item.trial_number,
            "item_status": item.status,
        }
        found = stored.get(trace_id)
        root = found.root if found is not None else None
        if root is None:
            not_joined.append({**base, "reason": NOT_FOUND, "trace_run_id": None})
            continue
        attrs = _attributes(root)
        trace_run_id = attrs.get(RUN_ID_ATTRIBUTE)
        trace_run_id = str(trace_run_id) if trace_run_id not in (None, "") else None
        if trace_run_id != run_id:
            not_joined.append(
                {
                    **base,
                    "reason": OTHER_RUN if trace_run_id else NO_RUN_ID,
                    "trace_run_id": trace_run_id,
                }
            )
            continue
        traces.append(
            {
                **base,
                "trace_run_id": trace_run_id,
                "project_id": found.project_id or None,
                "root_span_name": root.get("name"),
                "start_time": _iso(root.get("start_time")),
                "span_count": found.span_count,
                "trace_example_id": (
                    str(attrs[EXAMPLE_ATTRIBUTE])
                    if attrs.get(EXAMPLE_ATTRIBUTE)
                    else None
                ),
                "trace_trial": attrs.get(TRIAL_ATTRIBUTE),
            }
        )
    return RunTracePage(
        traces=traces,
        not_joined=not_joined,
        items_with_trace=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(page) < total,
    )
