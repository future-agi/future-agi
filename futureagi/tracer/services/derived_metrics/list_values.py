"""Deferred, page-keyed conversation cells from one pinned committed state.

This service is called AFTER the native list has returned its rows. It does not
fetch, filter or mutate list rows. Users projections belong to B16 and transient
span fields to B15; neither may be read as stored conversation cells here.

Page lifecycle hints must be canonical GLOBAL unit facts, never the list's root
start/end timestamps. Unknown anchors fail closed pending source admission.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from math import isfinite
from uuid import UUID

from django.utils import timezone

from tracer.models.derived_metric import MetricProjectRefresh, MetricStateConflict
from tracer.services.derived_metrics.publication import pinned_read
from tracer.services.derived_metrics.retention import retention_cutoff
from tracer.services.derived_metrics.spec import require_computable
from tracer.services.derived_metrics.storage import UNIT_STATE
from tracer.services.derived_metrics.unit_reads import committed_unit_values_select

MAX_PAGE_UNITS = 100
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, repr=False)
class MetricPageUnit:
    """Explicit canonical page identity, with optional authoritative unit facts.

    Supply unit_start/last_arrival_ns only if the list knows the global earliest
    live member / latest arrival. A root's end time is NOT an arrival. The pinned
    field supplies the anchor; lifecycle rows supply only the arrival floor. A
    queue's first_seen is discovery time, never authoritative anchor knowledge.
    """

    unit_id: str
    unit_start: datetime | None = None
    last_arrival_ns: int | None = None

    def __post_init__(self):
        canonical = str(UUID(str(self.unit_id)))
        if UUID(canonical).int == 0:
            raise ValueError("A canonical conversation id is required.")
        object.__setattr__(self, "unit_id", canonical)
        if self.unit_start is not None:
            _utc(self.unit_start)
        if self.last_arrival_ns is not None and (
            type(self.last_arrival_ns) is not int
            or not 0 < self.last_arrival_ns < 2**64
        ):
            raise ValueError("A positive UInt64 arrival timestamp is required.")


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Metric unit timestamps must be timezone-aware.")
    return value.astimezone(UTC)


def _rows(store, sql, params):
    rows, columns = store.execute(sql, params, with_column_types=True)
    names = [column[0] for column in columns]
    return [dict(zip(names, row, strict=True)) for row in rows]


def page_lifecycle_select(scope, unit_ids, manifest):
    """DCM3 for the lifecycle too: exact attempts + page keys before collapse.

    Queue tombstones mean settled, not deleted conversations. Do not filter them
    out, or a reopening can expose a stale field value. Queue due_at is not used
    to decide openness: last_arrival and the current settle interval are.
    """
    if str(manifest.project_id) != str(scope.project):
        raise MetricStateConflict("manifest project mismatch")
    params = {
        **scope.parameters(),
        "list_page_ids": tuple(unit_ids),
        "list_attempts": tuple(
            (generation, UUID(str(attempt)))
            for generation, attempt in manifest.allowed_attempts()
        ),
    }
    allowed = (
        "(generation, attempt_id) IN %(list_attempts)s"
        if params["list_attempts"]
        else "0"
    )
    return (
        "SELECT unit_id, last_arrival_ns FROM ("
        f"SELECT unit_id, last_arrival_ns FROM {UNIT_STATE.name} "
        "PREWHERE org_id = %(org_id)s AND project_id = %(project_id)s "
        "AND level = 'conversation' AND unit_id IN %(list_page_ids)s "
        f"WHERE {allowed} ORDER BY generation DESC, _version DESC "
        "LIMIT 1 BY org_id, project_id, level, unit_id)",
        params,
    )


def _field_cells_select(scope, definition, ids, manifest, cutoff):
    sql, params = committed_unit_values_select(
        scope, definition.metric_id, definition.version, ids, manifest
    )
    # Only small cell payloads leave CH; evidence maps are not list data. Expired
    # payloads are masked AFTER collapse even when an old pin still reaches them.
    expired = (
        "0"
        if cutoff is None
        else "(unit_start > toDateTime64(0, 6, 'UTC') AND unit_start < %(list_retention_start)s)"
    )
    values = ", ".join(
        f"if({expired}, NULL, {column}) AS {column}"
        for column in ("value_bool", "value_number", "value_text")
    )
    return (
        "SELECT unit_id, unit_start, last_arrival_ns, settled_at, is_deleted, "
        f"if({expired}, 'no_value', status) AS status, "
        f"if({expired}, 'retention_expired', no_value_reason) AS no_value_reason, {values} "
        f"FROM ({sql})",
        {**params, "list_retention_start": cutoff},
    )


def _cell(definition, manifest, page, field, lifecycle, *, now, cutoff, settle_minutes):
    metadata = {
        "value_type": definition.value_type,
        "format": definition.value_format,
        "projection": "",
        "version": definition.version,
        "manifest": str(manifest.id),
    }

    def result(state, reason="", value=None):
        valid = state == "value"
        eligible = valid or (
            state == "no_value"
            and reason not in {"retention_expired", "unknown_anchor", "deleted"}
        )
        labels = definition.value_labels or {}
        return {
            **metadata,
            "state": state,
            "value": value,
            "reason": reason,
            "label": (
                labels.get("match", "Counts")
                if value
                else labels.get("other", "Doesn't count")
            )
            if valid and definition.value_type == "boolean"
            else None,
            "counts": {
                "eligible": int(eligible),
                "evaluated": int(valid),
                "missing": int(eligible and not valid),
            },
        }

    # Queue first_seen is the first refresh's clock and survives membership
    # changes. Only a committed field or authoritative page hint knows the anchor.
    starts = [
        _utc(value)
        for value in (page.unit_start, (field or {}).get("unit_start"))
        if value is not None and _utc(value) > _EPOCH
    ]
    if (field or {}).get("no_value_reason") == "retention_expired" or (
        cutoff is not None and any(start < cutoff for start in starts)
    ):
        return result("no_value", "retention_expired")
    arrivals = [
        value
        for value in (
            page.last_arrival_ns,
            (field or {}).get("last_arrival_ns"),
            (lifecycle or {}).get("last_arrival_ns"),
        )
        if value is not None and value > 0
    ]
    if not starts or not arrivals:
        return result("no_value", "unknown_anchor")
    last = max(arrivals)
    # Integer microseconds avoid converting nanoseconds through a float.
    due = _EPOCH + timedelta(microseconds=last // 1000, minutes=settle_minutes)
    if now < due:
        return result("in_progress")
    if field is None or last > field["last_arrival_ns"] or field["status"] == "open":
        return result("not_calculated")
    if field["is_deleted"]:
        return result("no_value", "deleted")
    if field["status"] == "no_value":
        return result("no_value", field["no_value_reason"] or "attribute_absent")
    if field["status"] != "ok" or field["settled_at"] is None:
        return result("not_calculated")
    value = field[
        {"boolean": "value_bool", "number": "value_number", "text": "value_text"}[
            definition.value_type
        ]
    ]
    if value is None:
        return result("no_value", "attribute_absent")
    if definition.value_type == "number" and not isfinite(value):
        return result("no_value", "non_finite")
    if definition.value_type == "boolean":
        if value not in (0, 1):
            return result("no_value", "non_finite")
        value = bool(value)
    return result("value", value=value)


def hydrate_metric_list_values(
    store, scope, metric_id, page_units, *, retention_days, pin=None, now=None
):
    """Return one cell per explicit page id; callers fetch these after list rows.

    retention_days is a callable reading the CURRENT organization policy, not a
    horizon cached with the pin. An existing request pin can keep a pre-activation
    definition, but must identify this same metric/project. The caller authorizes
    scope before entering; parent and tenant consistency are checked again here.
    Users projections and span extraction must use their own value paths.
    """
    pages = tuple(islice(page_units, MAX_PAGE_UNITS + 1))
    if len(pages) > MAX_PAGE_UNITS or any(
        not isinstance(p, MetricPageUnit) for p in pages
    ):
        raise ValueError("Use at most 100 explicit canonical page units.")
    if len({p.unit_id for p in pages}) != len(pages):
        raise ValueError("Page units must be distinct.")
    if not callable(retention_days):
        raise ValueError("A live retention policy reader is required.")
    if not pages:
        return {}
    with pinned_read(store, scope.project, metric_id=metric_id, pin=pin, now=now) as (
        manifest,
        definition,
    ):
        if definition is None or str(definition.metric_id) != str(metric_id):
            raise MetricStateConflict("pin definition mismatch")
        metric = definition.metric
        project = metric.project
        if (
            metric.deleted
            or project.deleted
            or str(metric.project_id) != str(scope.project)
            or str(metric.organization_id) != str(scope.organization)
            or str(metric.workspace_id) != str(scope.workspace)
            or str(project.organization_id) != str(scope.organization)
            or str(project.workspace_id) != str(scope.workspace)
        ):
            raise MetricStateConflict("metric scope mismatch")
        if definition.level != "conversation" or definition.value_type not in {
            "boolean",
            "number",
            "text",
        }:
            raise ValueError(
                "Stored list cells require a conversation field; use the grain-specific reader."
            )
        require_computable(definition)
        clock = _utc(now or timezone.now())
        cutoff = retention_cutoff(retention_days(), now=clock)
        ids = tuple(page.unit_id for page in pages)
        sql, params = _field_cells_select(scope, definition, ids, manifest, cutoff)
        fields = {row["unit_id"]: row for row in _rows(store, sql, params)}
        sql, params = page_lifecycle_select(scope, ids, manifest)
        states = {row["unit_id"]: row for row in _rows(store, sql, params)}
        # Policy can shorten, or a unit can age out, during the read before a
        # purge publishes. Lengthening must not unmask CH-redacted data. A supplied
        # test clock stays frozen; otherwise the final horizon uses completion time.
        final = retention_cutoff(retention_days(), now=now)
        cutoff = (
            max(c for c in (cutoff, final) if c is not None)
            if cutoff or final
            else None
        )
        settle_minutes = MetricProjectRefresh.objects.get(
            project_id=scope.project
        ).settle_minutes
        return {
            page.unit_id: _cell(
                definition,
                manifest,
                page,
                fields.get(page.unit_id),
                states.get(page.unit_id),
                now=clock,
                cutoff=cutoff,
                settle_minutes=settle_minutes,
            )
            for page in pages
        }
