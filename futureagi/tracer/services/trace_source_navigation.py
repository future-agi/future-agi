"""Shared source proof for evaluation readiness and the destination snapshot."""

import re
from dataclasses import dataclass
from typing import Literal

# ObservationSpan.id is a case-sensitive CharField(255), not a UUID. The
# collector preserves the 16-character OTel SpanID; older writers use other
# strings. Reject malformed tokens without trimming or rewriting identity.
_SPAN_ID = re.compile(r"\A[^\s\x00-\x1f\x7f]{1,255}\Z")


def is_span_id(value) -> bool:
    return isinstance(value, str) and _SPAN_ID.fullmatch(value) is not None


Status = Literal[
    "ready",
    "no_reference",
    "unsupported_source",
    "unsupported_target",
    "incomplete_reference",
    "invalid_reference",
    "ambiguous_reference",
    "unavailable",
    # An older log that never recorded the project it ran on: no navigation.
    "source_not_recorded",
    "temporarily_unavailable",
]
Kind = Literal["trace", "voice_call"]


@dataclass(frozen=True)
class SourceNavigation:
    status: Status
    kind: Kind | None = None
    project_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    @property
    def retryable(self) -> bool:
        return self.status == "temporarily_unavailable"

    def as_dict(self) -> dict:
        ready = self.status == "ready"
        return {
            "status": self.status,
            "kind": self.kind if ready else None,
            "project_id": self.project_id if ready else None,
            "trace_id": self.trace_id if ready else None,
            "span_id": self.span_id if ready else None,
            "retryable": self.retryable,
        }


def classify_trace_source(
    detail, *, project_id: str, trace_id: str, span_id: str
) -> SourceNavigation:
    """Prove identity and unique conversation ancestry in one bounded snapshot."""
    if str(detail.project_id) != project_id:
        return SourceNavigation("invalid_reference")
    spans = list(detail.spans)
    by_id: dict[str, list[dict]] = {}
    for row in spans:
        if (
            str(row.get("trace_id") or "") != trace_id
            or str(row.get("project_id") or "") != project_id
        ):
            return SourceNavigation("invalid_reference")
        by_id.setdefault(str(row.get("id") or ""), []).append(row)
    matches = by_id.get(span_id, [])
    if not matches:
        return SourceNavigation("unavailable")
    if len(matches) > 1:
        return SourceNavigation("ambiguous_reference")
    roots = [
        row
        for row in spans
        if row.get("parent_span_id") in (None, "")
        and str(row.get("observation_type") or "").lower() == "conversation"
    ]
    if not roots:
        return SourceNavigation("ready", "trace", project_id, trace_id, span_id)
    if len(roots) > 1:
        return SourceNavigation("ambiguous_reference")
    root_id = str(roots[0].get("id"))
    seen, current = set(), span_id
    while True:
        if current == root_id:
            return SourceNavigation(
                "ready", "voice_call", project_id, trace_id, span_id
            )
        if current in seen:
            return SourceNavigation("ambiguous_reference")
        seen.add(current)
        rows = by_id.get(current, [])
        if len(rows) != 1:
            return SourceNavigation("ambiguous_reference")
        parent = rows[0].get("parent_span_id")
        if parent in (None, ""):
            return SourceNavigation("ambiguous_reference")
        current = str(parent)
