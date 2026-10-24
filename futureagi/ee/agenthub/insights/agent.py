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

import json
import os
import re

import httpx
import structlog

from ee.agenthub.insights.facets import lens_for, normalise
from ee.agenthub.insights.llm import InsightModelError, Spend, call_cost

logger = structlog.get_logger(__name__)

ENGINE_VERSION = "insights-agent/v1"
# The in-process engine that runs every step when no agent is configured.
BUILTIN_ENGINE_VERSION = "builtin/v1"
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
    if enabled():
        return {"kind": "omega", "version": ENGINE_VERSION}
    return {"kind": "builtin", "version": BUILTIN_ENGINE_VERSION}


# An agent reply is a report or an answer: a few hundred KB at most. Anything
# far larger is a broken agent, not a reply to read.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _Oversized(Exception):
    pass


def _bounded_body(response) -> bytes:
    """The response body, read no further than MAX_RESPONSE_BYTES."""
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise _Oversized
        chunks.append(chunk)
    return b"".join(chunks)


def _unavailable() -> InsightModelError:
    return InsightModelError(
        "The Insights agent is unavailable right now. Try again shortly.",
        unavailable=True,
    )


def _post(path: str, payload: dict, spend: Spend | None = None) -> dict:
    url = _setting("INSIGHTS_AGENT_URL").rstrip("/") + path
    headers = {"Authorization": f"Bearer {_setting('INSIGHTS_AGENT_SECRET')}"}
    try:
        with httpx.stream(
            "POST", url, json=payload, headers=headers, timeout=_TIMEOUTS[path]
        ) as response:
            status_code = response.status_code
            content = _bounded_body(response)
    except httpx.HTTPError as exc:
        logger.warning("insight_agent_unreachable", path=path, error=str(exc)[:200])
        raise _unavailable() from exc
    except _Oversized:
        logger.warning("insight_agent_oversized", path=path)
        raise _unavailable() from None
    if status_code >= 500 or status_code == 429:
        logger.warning("insight_agent_error", path=path, status=status_code)
        raise _unavailable()
    if status_code >= 400:
        logger.error(
            "insight_agent_rejected",
            path=path,
            status=status_code,
            body=content[:300].decode("utf-8", errors="replace"),
        )
        raise InsightModelError("The Insights agent rejected the request.")
    try:
        body = json.loads(content)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        logger.warning("insight_agent_not_json", path=path, status=status_code)
        raise _unavailable()
    if spend is not None:
        _add_usage(spend, body.get("usage") or {})
    return body


def _tokens(entry: dict) -> tuple[int, int]:
    return (
        int(entry.get("input_tokens") or entry.get("prompt_tokens") or 0),
        int(entry.get("output_tokens") or entry.get("completion_tokens") or 0),
    )


def _add_usage(spend: Spend, usage: dict) -> None:
    """Add what an agent request cost. The agent reports tokens per model
    (``by_model``) and a ``cost_usd`` that is 0, or null, for models the
    gateway doesn't price, so each model is priced on its own: its gateway
    cost when it reports one, else its tokens at the model's price."""
    prompt_tokens, completion_tokens = _tokens(usage)
    by_model = usage.get("by_model") or {}
    if by_model:
        cost = sum(
            call_cost(model, float(entry.get("cost_usd") or 0.0), *_tokens(entry))
            for model, entry in by_model.items()
        )
    else:
        cost = call_cost(
            usage.get("model") or "",
            float(usage.get("cost_usd") or 0.0),
            prompt_tokens,
            completion_tokens,
        )
    spend.add(cost, prompt_tokens, completion_tokens)


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
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            # A model-written quote that isn't an object backs nothing.
            dropped += 1
            continue
        quote = str(item.get("quote", "")).strip()[:MAX_QUOTE]
        span = _find(content, quote)
        if span is None:
            dropped += 1
            continue
        start, end = span
        kept.append(
            {
                "evidence_id": f"{key}:{start}:{end - start}",
                "quote": content[start:end],
                "start": start,
                "end": end,
                "supports": str(item.get("supports", ""))[:60],
            }
        )
    return kept[:3], dropped


# ─── Steps ──────────────────────────────────────────────────────────────────
def read_units(
    context: dict, units: list[tuple[str, str]], attributes: list[dict], spend: Spend
) -> dict[str, dict]:
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
            out[key] = {
                "error": str(message or "The Insights agent could not read this item.")[
                    :200
                ]
            }
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
        out.setdefault(
            key, {"error": "The Insights agent returned nothing for this item."}
        )
    return out


def name_taxonomy(
    context: dict, clusters: list[dict], target_top_count: int, spend: Spend
) -> dict:
    return _post(
        "/v1/name-taxonomy",
        {"run": context, "clusters": clusters, "target_top_count": target_top_count},
        spend,
    )


def analyse_report(payload: dict, spend: Spend) -> dict:
    return _post("/v1/analyse-report", payload, spend)


def ask(payload: dict, spend: Spend) -> dict:
    return _post("/v1/ask", payload, spend)
