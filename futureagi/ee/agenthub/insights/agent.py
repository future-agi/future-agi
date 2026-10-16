"""Client for the Insights agent: a separate Omega service that does the
analysis for a report.

When ``INSIGHTS_AGENT_URL`` is set, every model step of a run goes to the
agent: reading each unit (facets plus verbatim evidence quotes), naming and
organising the taxonomy (with its own verifier), writing the report as an
analyst that reads examples through receipt-issuing tools, and answering
follow-up questions. Without it the built-in engine runs the same steps in
process.

The agent's output is checked again here before anything is stored: quotes
must be exact substrings of the unit's text, numbers in findings must be in
the report's statistics, and citations must point at units of the run.
"""

from __future__ import annotations

import os
import re

import httpx
import structlog

from ee.agenthub.insights.facets import lens_for, normalise
from ee.agenthub.insights.llm import InsightModelError, Spend

logger = structlog.get_logger(__name__)

ENGINE_VERSION = "insights-agent/v1"
_TIMEOUTS = {
    "/v1/read-units": 120.0,
    "/v1/name-taxonomy": 240.0,
    "/v1/analyse-report": 420.0,
    "/v1/ask": 180.0,
}
MAX_QUOTE = 300


def _setting(name: str, default: str = "") -> str:
    try:
        from django.conf import settings

        value = getattr(settings, name, None)
    except Exception:  # pragma: no cover - settings always load in the app
        value = None
    return str(value or os.environ.get(name) or default)


def enabled() -> bool:
    return bool(_setting("INSIGHTS_AGENT_URL"))


def engine() -> dict:
    return {"kind": "omega" if enabled() else "builtin", "version": ENGINE_VERSION}


def _post(path: str, payload: dict, spend: Spend | None = None) -> dict:
    url = _setting("INSIGHTS_AGENT_URL").rstrip("/") + path
    headers = {"Authorization": f"Bearer {_setting('INSIGHTS_AGENT_SECRET')}"}
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUTS[path])
    except httpx.HTTPError as exc:
        logger.warning("insight_agent_unreachable", path=path, error=str(exc)[:200])
        raise InsightModelError(
            "The Insights agent is unavailable right now. Try again shortly.",
            unavailable=True,
        ) from exc
    if response.status_code >= 500 or response.status_code == 429:
        logger.warning("insight_agent_error", path=path, status=response.status_code)
        raise InsightModelError(
            "The Insights agent is unavailable right now. Try again shortly.",
            unavailable=True,
        )
    if response.status_code >= 400:
        logger.error("insight_agent_rejected", path=path, status=response.status_code,
                     body=response.text[:300])
        raise InsightModelError("The Insights agent rejected the request.")
    body = response.json()
    usage = body.get("usage") or {}
    if spend is not None:
        spend.add(float(usage.get("cost_usd") or 0.0))
    return body


def run_context(settings: dict, unit: str, run_id: str) -> dict:
    lens = lens_for(settings.get("lens", "usage"))
    return {
        "run_id": str(run_id),
        "unit": unit,
        "lens": {
            "key": lens.key,
            "title": lens.title,
            "instruction": lens.instruction,
            "exclude": list(lens.exclude),
        },
        "question": settings.get("question") or lens.title,
        "agent_description": settings.get("agent_description", ""),
        "instructions": settings.get("instructions", ""),
        "attributes": settings.get("attributes") or [],
    }


# ─── Receipts, checked host-side ────────────────────────────────────────────
def _find(content: str, quote: str) -> tuple[int, int] | None:
    if not quote:
        return None
    at = content.find(quote)
    if at >= 0:
        return at, at + len(quote)
    # Whitespace-normalised match, mapped back to real offsets.
    words = quote.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    match = re.search(pattern, content)
    return (match.start(), match.end()) if match else None


def verified_evidence(evidence, content: str, key: str) -> tuple[list[dict], int]:
    """Keep quotes that occur in the unit's text, with their real offsets."""
    kept, dropped = [], 0
    for item in evidence or []:
        quote = str((item or {}).get("quote", "")).strip()[:MAX_QUOTE]
        span = _find(content, quote)
        if span is None:
            dropped += 1
            continue
        start, end = span
        kept.append({
            "evidence_id": f"{key}:{start}:{end - start}",
            "quote": content[start:end],
            "start": start,
            "end": end,
            "supports": str((item or {}).get("supports", ""))[:60],
        })
    return kept[:3], dropped


# ─── Steps ──────────────────────────────────────────────────────────────────
def read_units(context: dict, units: list[tuple[str, str]], attributes: list[dict],
               spend: Spend) -> dict[str, dict]:
    """``{key: {"facets": ..., "evidence": [...], "error": str|None}}``."""
    body = _post(
        "/v1/read-units",
        {"run": context, "units": [{"key": k, "content": c} for k, c in units]},
        spend,
    )
    contents = dict(units)
    out: dict[str, dict] = {}
    for item in body.get("units") or []:
        key = str(item.get("key", ""))
        if key not in contents:
            continue
        if not item.get("ok", True):
            error = item.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            out[key] = {"error": str(message or "The Insights agent could not read this item.")[:200]}
            continue
        facets = normalise(item.get("facets") or {}, attributes)
        evidence, dropped = verified_evidence(item.get("evidence"), contents[key], key)
        if facets["failure_present"] and not evidence:
            # A failure claim needs a quote that backs it.
            facets["failure_present"] = False
        facets["evidence"] = evidence
        facets["dropped_quotes"] = dropped + int(item.get("dropped_quotes") or 0)
        out[key] = {"facets": facets}
    for key in contents:
        out.setdefault(key, {"error": "The Insights agent returned nothing for this item."})
    return out


def name_taxonomy(context: dict, clusters: list[dict], target_top_count: int,
                  spend: Spend) -> dict:
    return _post(
        "/v1/name-taxonomy",
        {"run": context, "clusters": clusters, "target_top_count": target_top_count},
        spend,
    )


def analyse_report(payload: dict, spend: Spend) -> dict:
    return _post("/v1/analyse-report", payload, spend)


def ask(payload: dict, spend: Spend) -> dict:
    return _post("/v1/ask", payload, spend)
