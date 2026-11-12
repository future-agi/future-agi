"""Explicit background-only schemas; no coercion, truncation or recovery.

Optional trace attributes are not failed analysis. Dropped spans, truncated
content, invalid model reports and requested-but-missing result identities are.
Foreground tools never call this module. Refusal marks the generation before
raising, so a dispatcher catching the error cannot authorize publication.
"""

import json

from ee.agenthub.traceerroragent.background_tools import reject_chauffeur


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(value, *, nonempty=True):
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(_text(item) for item in value)
    )


def parse_chauffeur_report(raw, guard):
    from ee.agenthub.traceerroragent.background_payload_reports import chauffeur_report

    return chauffeur_report(raw, guard, decode=True)


def validate_chauffeur_report(report, guard, span_ids=None):
    from ee.agenthub.traceerroragent.background_payload_reports import chauffeur_report

    return chauffeur_report(report, guard, span_ids=span_ids)


def validate_trace_spans(spans, guard):
    """Prevent empty/partial tree and summary formatting fallbacks before send."""
    if not spans:
        reject_chauffeur(guard)
    ids = [str(span.id) for span in spans]
    if len(set(ids)) != len(ids) or any(not _text(sid) for sid in ids):
        reject_chauffeur(guard)
    parents = {
        str(span.id): str(span.parent_span_id)
        if getattr(span, "parent_span_id", None)
        else None
        for span in spans
    }
    for sid in ids:
        visited = set()
        current = sid
        while current is not None:
            if current in visited or current not in parents:
                reject_chauffeur(guard)
            visited.add(current)
            current = parents[current]
    for span in spans:
        for field in ("input", "output"):
            value = getattr(span, field, None)
            if value:
                try:
                    # _build_execution_summary serializes the initial input;
                    # elsewhere it stringifies. Neither may silently degrade.
                    if not isinstance(value, str):
                        json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if len(str(value)) > 2000:
                        reject_chauffeur(guard)
                except (TypeError, ValueError):
                    reject_chauffeur(guard)


def validate_submission(params, guard, *, scores=False):
    """Pydantic typing alone permits empty evidence and explanation strings."""
    if scores:
        reasons = (
            "factual_grounding",
            "privacy_and_safety",
            "instruction_adherence",
            "optimal_plan_execution",
        )
        if not _text(params.insights) or any(
            not _text(getattr(params, f"{dimension}_reason")) for dimension in reasons
        ):
            reject_chauffeur(guard)
        for field in (
            "overall_score",
            *(f"{dimension}_score" for dimension in reasons),
        ):
            value = getattr(params, field)
            if type(value) not in (int, float) or not 1 <= value <= 5:
                reject_chauffeur(guard)
    else:
        if any(
            not _text(getattr(params, field))
            for field in ("category", "description", "recommendation")
        ) or any(
            not _strings(getattr(params, field))
            for field in ("location_spans", "evidence_snippets", "root_causes")
        ):
            reject_chauffeur(guard)


def validate_span_content(span, max_len, guard):
    """A read must contain real content, with no head/tail omission."""
    values = [
        (getattr(span, "input", None), max_len),
        (getattr(span, "output", None), max_len),
        (getattr(span, "metadata", None), min(max_len, 1000)),
        (getattr(span, "span_events", None), min(max_len, 1000)),
    ]
    useful = False
    for value, limit in values:
        if value and str(value).strip() not in ("", "{}", "[]"):
            useful = True
            if len(str(value)) > limit:
                reject_chauffeur(guard)
    if not useful:
        reject_chauffeur(guard)
