"""Metric filter identity, active metadata validation and committed membership SQL.

No mutable-head reads happen here. A planner supplies a request-time pin from
B02R's capture_pin, active metadata for that pin, and canonical candidates from
its native list classifier, BEFORE paging/count/sampling. Missing planner input
fails closed. B07 owns replica visibility checks and rechecking pin usability at
execution/completion; constructing SQL is not a publication/visibility receipt.

Users filters are validated here (numeric per-user projections of conversation
metrics, and user-level metrics behind their capability flag), but the per-user
value is computed only by the shared user rules (B20) inside the Users list
planner (B16). That planner compares its per-user relation with
metric_value_comparison; build_metric_membership never aggregates users itself.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from tracer.services.derived_metrics import list_filter_capabilities
from tracer.utils.filter_operators import (
    FILTER_TYPE_ALLOWED_OPS,
    METRIC_FIELD_COLUMN_TYPE,
    METRIC_FIELD_ID_PREFIX,
    NO_VALUE_FILTER_OPS,
    RANGE_FILTER_OPS,
    normalize_filter_type,
)

METRIC_FIELD = METRIC_FIELD_COLUMN_TYPE
METRIC_FIELD_PREFIX = METRIC_FIELD_ID_PREFIX
# metric_field_values.status, as written by the unit writer: a settled unit
# with a value, a settled unit with No value, or a unit still open. A unit that
# has never been calculated has no row at all.
STATUS_OK = "ok"
STATUS_NO_VALUE = "no_value"
STATUS_OPEN = "open"
METRIC_FIELD_STATUSES = frozenset({STATUS_OK, STATUS_NO_VALUE, STATUS_OPEN})
_METRIC_FILTER_TYPES = ("boolean", "number", "text")
_CONTEXT_LEVELS = {
    "sessions": "conversation",
    "traces": "trace",
    "spans": "span",
    "task_sessions": "conversation",
    "task_traces": "trace",
    "task_spans": "span",
}


class MetricFilterError(ValueError):
    def __init__(self, message: str, code: str = "invalid_metric_filter"):
        super().__init__(message)
        self.code = code


def metric_field_id(metric_id) -> str:
    return f"{METRIC_FIELD_PREFIX}{UUID(str(metric_id))}"


def decode_metric_field_id(identity: object) -> str:
    try:
        if not isinstance(identity, str) or not identity.startswith(
            METRIC_FIELD_PREFIX
        ):
            raise ValueError
        metric_id = str(UUID(identity[len(METRIC_FIELD_PREFIX) :]))
        if identity != metric_field_id(metric_id):
            raise ValueError
        return metric_id
    except (ValueError, TypeError, AttributeError):
        raise MetricFilterError(
            "Expected a canonical metric_field:<uuid> identity."
        ) from None


@dataclass(frozen=True)
class MetricFieldMetadata:
    metric_id: str
    project_id: str
    organization_id: str
    workspace_id: str
    version_id: str
    version: int
    level: str
    value_type: str
    kind: str
    user_rule: str = ""
    value_labels: dict[str, str] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return metric_field_id(self.metric_id)

    @classmethod
    def from_metric(cls, metric):
        version = metric.active_version
        if metric.deleted or version is None or version.metric_id != metric.id:
            raise MetricFilterError("Metric has no available active field.")
        return cls(
            metric_id=str(metric.id),
            project_id=str(metric.project_id),
            organization_id=str(metric.organization_id),
            workspace_id=str(metric.workspace_id),
            version_id=str(version.id),
            version=version.version,
            level=version.level,
            value_type=version.value_type,
            kind=version.kind,
            user_rule=version.user_rule,
            value_labels=dict(version.value_labels),
        )


def resolve_metric_field(
    identity: str, *, project_id, organization_id=None, workspace_id=None
):
    """Resolve within an already-authorized project, never by unscoped UUID."""
    from tracer.models.derived_metric import DerivedMetric

    metric_id = decode_metric_field_id(identity)
    if not project_id:
        raise MetricFilterError("Metric filters require an authorized project scope.")
    scope = {"pk": metric_id, "project_id": project_id, "deleted": False}
    if organization_id is not None:
        scope["organization_id"] = organization_id
    if workspace_id is not None:
        scope["workspace_id"] = workspace_id
    metric = (
        DerivedMetric.objects.select_related("active_version").filter(**scope).first()
    )
    if metric is None:
        raise MetricFilterError("Metric field is unavailable in this project.")
    return MetricFieldMetadata.from_metric(metric)


def metric_filter_type(metadata: MetricFieldMetadata, context: str) -> str:
    if metadata.level == "user":
        if context != "users":
            message = (
                "User metrics can't filter tasks yet."
                if str(context).startswith("task")
                else "User metrics can only filter the Users list."
            )
            raise MetricFilterError(message, "metric_filter_wrong_grain")
        if not list_filter_capabilities.USER_LEVEL_METRIC_FILTER_ON_USERS:
            raise MetricFilterError(
                "User metric filters are not available on the Users list yet.",
                "user_metric_filter_unavailable",
            )
        if metadata.kind in {"share", "count"} and metadata.user_rule in {
            "any",
            "every",
        }:
            return "boolean"
        if metadata.kind == "average" and metadata.user_rule in {"average", "total"}:
            return "number"
        raise MetricFilterError("Unsupported user metric rule.")
    if context == "users":
        if metadata.level != "conversation":
            raise MetricFilterError(
                "Users projections require a conversation metric.",
                "metric_filter_wrong_grain",
            )
        return "number"
    if _CONTEXT_LEVELS.get(context) != metadata.level:
        raise MetricFilterError(
            "Metric field grain does not match the filter context.",
            "metric_filter_wrong_grain",
        )
    if metadata.level == "trace":
        raise MetricFilterError(
            "Trace metric filters are not enabled.", "trace_metric_unavailable"
        )
    if metadata.value_type not in _METRIC_FILTER_TYPES:
        raise MetricFilterError("Unsupported metric field value type.")
    return metadata.value_type


def validate_metric_filter(
    item: dict, metadata: MetricFieldMetadata, *, context: str
) -> dict:
    """Validate without services; retain stored identity, labels and wire values."""
    identity = item.get("column_id")
    if decode_metric_field_id(identity) != str(metadata.metric_id):
        raise MetricFilterError("Metric field identity does not match its metadata.")
    if item.get("property_id", identity) != identity:
        raise MetricFilterError("Metric property_id and column_id must be identical.")
    config = item["filter_config"]
    if config.get("col_type") != METRIC_FIELD:
        raise MetricFilterError("Metric field identity requires col_type METRIC_FIELD.")
    expected_type = metric_filter_type(metadata, context)
    if normalize_filter_type(config.get("filter_type")) != expected_type:
        raise MetricFilterError(
            "Metric filter type does not match the active field in this context."
        )
    op = config.get("filter_op")
    if op not in FILTER_TYPE_ALLOWED_OPS[expected_type]:
        raise MetricFilterError("Unsupported operator for the metric field type.")
    if config.get("attribute_value_types") is not None:
        raise MetricFilterError(
            "Metric filters do not accept attribute storage provenance."
        )
    labels = None
    if expected_type == "boolean":
        labels = {
            metadata.value_labels.get("match", "Counts"),
            metadata.value_labels.get("other", "Doesn't count"),
        }
    _validate_metric_values(config, op, expected_type, labels)
    return item


def _validate_metric_values(config: dict, op: str, expected_type: str, labels):
    """Value shape for one operator. labels=None accepts any non-empty label."""
    if op in NO_VALUE_FILTER_OPS:
        return
    value = config.get("filter_value")
    if op in RANGE_FILTER_OPS:
        if not isinstance(value, list) or len(value) != 2:
            raise MetricFilterError("Metric range filters require two values.")
        values = value
    elif op in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise MetricFilterError(
                "Metric membership filters require a non-empty list."
            )
        values = value
    else:
        values = [value]
    if expected_type == "number":
        try:
            valid = all(
                not isinstance(v, bool)
                and isinstance(v, int | float | str)
                and v != ""
                and math.isfinite(float(v))
                for v in values
            )
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            raise MetricFilterError("Metric number values must be finite numbers.")
        if op in RANGE_FILTER_OPS and float(values[0]) > float(values[1]):
            raise MetricFilterError("Metric range bounds must be ordered.")
    elif expected_type == "boolean":
        if any(
            not isinstance(v, bool)
            and (
                not isinstance(v, str)
                or not v.strip()
                or (labels is not None and v not in labels)
            )
            for v in values
        ):
            raise MetricFilterError(
                "Metric yes/no values must be booleans or the active field's labels."
            )
        if (
            labels is not None
            and len(labels) != 2
            and any(isinstance(v, str) for v in values)
        ):
            raise MetricFilterError("Ambiguous metric labels require a boolean value.")
    elif any(not isinstance(v, str) for v in values):
        raise MetricFilterError("Metric text values must be strings.")


def validate_metric_filter_structure(item: dict) -> dict:
    """Context-free check for stored filters (saved views), opted into explicitly.

    Checks identity, family, operator and value shape only. The active
    definition, grain, labels and scope are checked again at read time by
    build_metric_membership, because a stored metric can later be deleted or
    re-versioned. Never use this for a list request.
    """
    identity = item.get("column_id")
    decode_metric_field_id(identity)
    if item.get("property_id", identity) != identity:
        raise MetricFilterError("Metric property_id and column_id must be identical.")
    config = item["filter_config"]
    if config.get("col_type") != METRIC_FIELD:
        raise MetricFilterError("Metric field identity requires col_type METRIC_FIELD.")
    filter_type = normalize_filter_type(config.get("filter_type"))
    if filter_type not in _METRIC_FILTER_TYPES:
        raise MetricFilterError("Unsupported metric field value type.")
    op = config.get("filter_op")
    if op not in FILTER_TYPE_ALLOWED_OPS[filter_type]:
        raise MetricFilterError("Unsupported operator for the metric field type.")
    if config.get("attribute_value_types") is not None:
        raise MetricFilterError(
            "Metric filters do not accept attribute storage provenance."
        )
    _validate_metric_values(config, op, filter_type, None)
    return item


def validate_scoped_metric_filter(item: dict, *, context: dict) -> dict:
    """Serializer adapter. Context and scope come from the caller, not the chip."""
    project_id = context.get("project_id")
    if not project_id:
        raise MetricFilterError("Metric filters require an authorized project scope.")
    resolver = context.get("metric_field_resolver", resolve_metric_field)
    metadata = resolver(
        item["column_id"],
        project_id=project_id,
        organization_id=context.get("organization_id"),
        workspace_id=context.get("workspace_id"),
    )
    if str(metadata.project_id) != str(project_id) or any(
        context.get(key) is not None
        and str(context[key]) != str(getattr(metadata, key))
        for key in ("organization_id", "workspace_id")
    ):
        raise MetricFilterError("Metric field is unavailable in this project.")
    return validate_metric_filter(
        item, metadata, context=context.get("metric_filter_context", "")
    )


_INTERNAL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class MetricFilterCandidates:
    """Code-owned canonical candidates, not a SQL string supplied by HTTP.

    A relation must expose a String id column named unit_id holding canonical
    (remap-resolved) conversation ids. For finite-retention ``is_null`` filters,
    it must also expose the canonical conversation start in a code-owned column;
    this lets an expired cell remain No value after its field rows are purged.
    """

    ids_param: str | None = None
    relation: str | None = None
    unit_start_column: str | None = None

    def __post_init__(self):
        names = [v for v in (self.ids_param, self.relation) if v is not None]
        if len(names) != 1 or not _INTERNAL_IDENTIFIER.fullmatch(names[0]):
            raise MetricFilterError(
                "Metric filters require one internal candidate set."
            )
        if self.unit_start_column is not None and (
            self.relation is None
            or not _INTERNAL_IDENTIFIER.fullmatch(self.unit_start_column)
        ):
            raise MetricFilterError(
                "Retention-aware No value filters require a safe candidate relation."
            )

    def condition(self, column: str) -> str:
        if self.ids_param is not None:
            return f"{column} IN %({self.ids_param})s"
        return f"{column} IN (SELECT {column} FROM {self.relation})"

    def expired_unit_ids(self, retention_start: datetime, bind: Callable) -> str:
        if self.relation is None or self.unit_start_column is None:
            raise MetricFilterError(
                "Finite-retention No value filters require canonical candidate starts.",
                "metric_filter_planner_unavailable",
            )
        return (
            f"SELECT unit_id FROM {self.relation} WHERE "
            f"{self.unit_start_column} < {bind(retention_start)}"
        )


@dataclass(frozen=True)
class MetricReadScope:
    """One pinned definition/manifest. No acquisition from a mutable head here."""

    metadata: MetricFieldMetadata
    pin: Any
    # Explicit None means the caller verified unlimited retention. There is
    # deliberately no default that could silently skip the current read guard.
    retention_start: datetime | None

    def ensure_usable(self):
        if not self.pin.is_usable():
            raise MetricFilterError(
                "Metric filter pin expired.", "metric_filter_expired"
            )
        if (
            str(self.pin.project_id) != self.metadata.project_id
            or str(self.pin.definition_version_id) != self.metadata.version_id
            or str(self.pin.manifest.project_id) != self.metadata.project_id
        ):
            raise MetricFilterError("Metric filter pin does not match the field scope.")


def metric_value_comparison(
    item: dict, metadata: MetricFieldMetadata, value_type: str, bind: Callable
) -> str:
    """WHERE/HAVING expression over a relation with status and value_* columns.

    Apply it only AFTER latest-state collapse (or, for Users, over the shared
    per-user relation). Open and never-calculated units are not No value, and a
    negated operator never admits a unit without a value.
    """
    op = item["filter_config"]["filter_op"]
    if op == "is_null":
        return f"status = '{STATUS_NO_VALUE}'"
    column = {"number": "value_number", "boolean": "value_bool", "text": "value_text"}[
        value_type
    ]
    present = f"status = '{STATUS_OK}' AND isNotNull({column})"
    if op == "is_not_null":
        return present
    value = item["filter_config"]["filter_value"]

    def convert(v):
        if value_type == "number":
            return float(v)
        if value_type == "boolean":
            return int(
                v
                if isinstance(v, bool)
                else v == metadata.value_labels.get("match", "Counts")
            )
        return v

    if op in RANGE_FILTER_OPS:
        expression = f"{column} {'NOT BETWEEN' if op == 'not_between' else 'BETWEEN'} {bind(convert(value[0]))} AND {bind(convert(value[1]))}"
    elif op in {"in", "not_in"}:
        expression = f"{column} {'NOT IN' if op == 'not_in' else 'IN'} {bind(tuple(convert(v) for v in value))}"
    elif op in {"contains", "not_contains", "starts_with", "ends_with"}:
        function = {
            "contains": "positionUTF8",
            "not_contains": "positionUTF8",
            "starts_with": "startsWith",
            "ends_with": "endsWith",
        }[op]
        expression = f"{function}({column}, {bind(value)})"
        if op in {"contains", "not_contains"}:
            expression += " = 0" if op == "not_contains" else " > 0"
    else:
        symbol = {
            "equals": "=",
            "not_equals": "!=",
            "greater_than": ">",
            "greater_than_or_equal": ">=",
            "less_than": "<",
            "less_than_or_equal": "<=",
        }[op]
        expression = f"{column} {symbol} {bind(convert(value))}"
    return f"({present}) AND ({expression})"


def build_metric_membership(
    item: dict,
    *,
    scope: MetricReadScope,
    candidates: MetricFilterCandidates,
    context: str,
    bind: Callable,
) -> str:
    """Return a unit_id subquery for the native planner's pre-page WHERE clause.

    Conversation fields only. No list time bound reaches the field's global
    unit_start: list date/root membership stays with the native classifier.
    The canonical candidates are pushed into each attempt-qualified source
    before the latest-state collapse; deletion, retention and the comparison
    apply after it. With finite retention, ``is_null`` also unions expired ids
    from the native candidate relation, so expiry stays No value after the
    per-unit field rows are physically purged.
    """
    metadata = scope.metadata
    validate_metric_filter(item, metadata, context=context)
    if context == "users":
        raise MetricFilterError(
            "Users metric filters need the shared per-user planner.",
            "metric_filter_planner_unavailable",
        )
    scope.ensure_usable()
    if metadata.level != "conversation":
        raise MetricFilterError(
            "This metric grain needs its native list planner.",
            "metric_filter_planner_unavailable",
        )
    value_type = metric_filter_type(metadata, context)
    common = " AND ".join(
        [
            f"org_id = {bind(UUID(metadata.organization_id))}",
            f"workspace_id = {bind(UUID(metadata.workspace_id))}",
            f"project_id = {bind(UUID(metadata.project_id))}",
            f"field_id = {bind(UUID(metadata.metric_id))}",
            f"field_version = {bind(metadata.version)}",
            "level = 'conversation'",
            candidates.condition("unit_id"),
        ]
    )
    manifest = scope.pin.manifest
    # The manifest's own reader contract decides which attempts are visible.
    pairs = [(int(g), UUID(str(a))) for g, a in manifest.allowed_attempts()]
    sources = []
    if manifest.checkpoint_attempt_id is not None:
        checkpoint, pairs = pairs[0], pairs[1:]
        sources.append(
            "SELECT * FROM metric_field_values_checkpoint WHERE "
            + common
            + f" AND (generation, attempt_id) = {bind(checkpoint)}"
        )
    if pairs:
        sources.append(
            "SELECT * FROM metric_field_values WHERE "
            + common
            + f" AND (generation, attempt_id) IN {bind(tuple(pairs))}"
        )
    if not sources:
        raise MetricFilterError(
            "Metric filter has no committed state.", "metric_filter_unavailable"
        )
    # One tuple preserves explicit NULLs (argMax(nullable_column) skips them).
    columns = (
        "is_deleted",
        "status",
        "unit_start",
        "user_id",
        "value_bool",
        "value_number",
        "value_text",
        "part_n",
        "part_d",
        "settled_at",
    )
    latest = (
        "SELECT unit_id, "
        + ", ".join(
            f"tupleElement(payload, {i}) AS {c}" for i, c in enumerate(columns, 1)
        )
        + " FROM (SELECT unit_id, argMax(tuple("
        + ", ".join(columns)
        + "), tuple(generation, fencing_epoch, row_sequence)) AS payload FROM ("
        + " UNION ALL ".join(sources)
        + ") GROUP BY unit_id)"
    )
    # Field rows past the horizon never participate in value comparisons. An
    # expired cell is nevertheless No value: for is_null, use the native list's
    # canonical candidate starts so the result is stable before and after the
    # retention worker physically purges those field rows.
    live = "is_deleted = 0"
    expired_ids = None
    if scope.retention_start is not None:
        live += f" AND unit_start >= {bind(scope.retention_start)}"
        if item["filter_config"]["filter_op"] == "is_null":
            expired_ids = candidates.expired_unit_ids(scope.retention_start, bind)
    comparison = metric_value_comparison(item, metadata, value_type, bind)
    membership = f"SELECT unit_id FROM ({latest}) WHERE {live} AND ({comparison})"
    if expired_ids is not None:
        membership += f" UNION DISTINCT {expired_ids}"
    return membership
