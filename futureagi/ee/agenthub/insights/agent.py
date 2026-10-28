"""Client for the Insights agent: a separate Omega service that does the
analysis for a report.

When ``INSIGHTS_AGENT_URL`` is set, every model step of a run goes to the
agent: reading each unit (facets plus verbatim evidence quotes), naming and
organising the taxonomy (with its own verifier), writing the report as an
analyst that reads examples through receipt-issuing tools, and answering
follow-up questions. Without it the built-in engine runs the same steps in
process.

A run is pinned at creation to the engine it starts on (``resolve_engine``,
kept in ``run.settings["engine"]``) and every step dispatches on that pin,
never on the setting of the moment. An ``insights-agent/v2`` pin carries the
agent's ``engine_pin``, sent on every call as ``X-Insights-Engine``: an agent
running another engine (an old pod mid-deploy) answers 409, retried briefly,
then the run fails; it never falls back to another engine. Under v2 the agent
reads each unit's full span graph through the evidence broker
(``broker.py``) instead of a clipped copy of its text (PROTOCOL_V2).

The agent's output is checked again here before anything is stored: quotes
must be exact substrings of the unit's text (v1) or receipts of the unit
asked about (v2), numbers in findings must be in the report's statistics,
and citations must point at units of the run.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import re
import time

import httpx
import structlog

from ee.agenthub.insights.facets import lens_for, normalise
from ee.agenthub.insights.llm import InsightModelError, Spend, usage_cost
from ee.agenthub.insights.units import own_words

logger = structlog.get_logger(__name__)

ENGINE_VERSION = "insights-agent/v1"
CONTRACT_V1 = ENGINE_VERSION
CONTRACT_V2 = "insights-agent/v2"
# The in-process engine that runs every step when no agent is configured.
BUILTIN_ENGINE_VERSION = "builtin/v1"
# Seconds each agent call may take; a capability for a call outlives it.
TIMEOUTS = {
    "/healthz": 10.0,
    "/v1/read-units": 120.0,
    "/v1/name-taxonomy": 240.0,
    "/v1/analyse-report": 420.0,
    "/v1/ask": 180.0,
}
MAX_QUOTE = 300
ENGINE_HEADER = "X-Insights-Engine"
# Waits before asking again an agent that runs another engine than the run's
# pin: a rolling deploy can route a call to a pod of either build.
MISMATCH_RETRY_DELAYS = (2.0, 5.0)
_ENGINE_PIN = re.compile(r"[0-9a-f]{64}")


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
    """The engine of a run started before runs were pinned, by the setting."""
    if enabled():
        return {"kind": "omega", "version": ENGINE_VERSION}
    return {"kind": "builtin", "version": BUILTIN_ENGINE_VERSION}


class InsightEngineMismatch(InsightModelError):
    """The agent runs another engine than the run is pinned to, after the
    retries: the run fails rather than mix engines."""

    def __init__(self):
        super().__init__(
            "The Insights agent was updated while this report ran. Run it again."
        )


def resolve_engine() -> dict:
    """The engine a new run is pinned to, asked of the agent itself: its
    contract, build, models, modalities and ``engine_pin``, copied verbatim
    (never hashed here). ``insights-agent/v2`` needs an agent that serves it,
    an evidence secret to sign capabilities with and an evidence store that
    starts, its bucket asked too; otherwise v1. Without an agent, the
    built-in engine. Raises when the agent can't be asked."""
    if not enabled():
        return {"kind": "builtin", "version": BUILTIN_ENGINE_VERSION}
    from ee.agenthub.insights import broker

    health = _health()
    contracts = health.get("contracts")
    contracts = contracts if isinstance(contracts, list) else [health.get("contract")]
    pin = health.get("engine_pin")
    pin = pin if isinstance(pin, str) and _ENGINE_PIN.fullmatch(pin) else None
    if CONTRACT_V2 in contracts and pin and broker.configured(probe=True):
        return {
            "kind": "omega",
            "contract": CONTRACT_V2,
            "version": str(health.get("version") or ""),
            "runtime_build": str(health.get("runtime_build") or ""),
            "models": health.get("models") or {},
            "modalities": health.get("modalities") or {},
            "engine_pin": pin,
        }
    if CONTRACT_V2 in contracts:
        logger.warning("insight_agent_v2_unused", engine_pin=bool(pin))
    # A v1 run is held to the agent's build too, when the agent names it.
    return {
        "kind": "omega",
        "contract": CONTRACT_V1,
        "version": ENGINE_VERSION,
        **({"engine_pin": pin} if pin else {}),
    }


def pinned(settings: dict | None) -> dict:
    """The engine a run is pinned to. A run created before pins (one still in
    flight across the deploy, or an old report asked a question) keeps what
    the setting says, as it always has."""
    stored = (settings or {}).get("engine")
    if isinstance(stored, dict) and stored.get("kind") in ("omega", "builtin"):
        return stored
    return engine()


def is_omega(engine: dict) -> bool:
    return engine.get("kind") == "omega"


def is_v2(engine: dict) -> bool:
    return is_omega(engine) and engine.get("contract") == CONTRACT_V2


def expected_contract() -> str | None:
    """The contract a run started now would likely use, for estimates (which
    don't ask the agent): v2 when an agent and an evidence secret are set."""
    if not enabled():
        return None
    from ee.agenthub.insights import broker

    return CONTRACT_V2 if broker.configured() else CONTRACT_V1


# How long estimates reuse the models the agent said it runs on.
MODELS_CACHE_SECONDS = 300


def expected_models() -> dict | None:
    """The models the agent runs on (``{"fast": ..., "thinking": ...}``), for
    estimates: what its health reports, kept a few minutes. A run is charged
    what those models cost, not this deployment's tier models. None without
    an agent, or when it can't be asked (estimates then use the tier
    models)."""
    if not enabled():
        return None
    from django.core.cache import cache

    key = "insights:agent-models"
    models = cache.get(key)
    if models is None:
        try:
            models = _health().get("models")
        except InsightModelError:
            models = None
        if not (isinstance(models, dict) and models.get("fast")):
            models = {}
        cache.set(key, models, MODELS_CACHE_SECONDS)
    return models or None


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


def _health() -> dict:
    """The agent's ``/healthz``: what it serves and its ``engine_pin``."""
    url = _setting("INSIGHTS_AGENT_URL").rstrip("/") + "/healthz"
    try:
        with httpx.stream("GET", url, timeout=TIMEOUTS["/healthz"]) as response:
            status_code = response.status_code
            content = _bounded_body(response)
    except (httpx.HTTPError, _Oversized) as exc:
        logger.warning("insight_agent_health_unreachable", error=str(exc)[:200])
        raise _unavailable() from None
    try:
        body = json.loads(content) if status_code == 200 else None
    except ValueError:
        body = None
    if not isinstance(body, dict):
        logger.warning("insight_agent_health_unreadable", status=status_code)
        raise _unavailable()
    return body


# Failures after the agent had the request: it may have spent on it.
_LOST_REPLY = (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError)
# What a lost reply is charged: one model call reading the request, writing
# this many tokens, at the ceiling price (as the agent estimates an aborted
# call).
LOST_REPLY_OUTPUT_TOKENS = 4_096


def _send(
    path: str, payload: dict, headers: dict, spend: Spend | None = None
) -> tuple[int, bytes]:
    url = _setting("INSIGHTS_AGENT_URL").rstrip("/") + path
    try:
        with httpx.stream(
            "POST", url, json=payload, headers=headers, timeout=TIMEOUTS[path]
        ) as response:
            return response.status_code, _bounded_body(response)
    except httpx.HTTPError as exc:
        logger.warning("insight_agent_unreachable", path=path, error=str(exc)[:200])
        if isinstance(exc, _LOST_REPLY):
            _add_lost_reply(spend, payload)
        raise _unavailable() from exc
    except _Oversized:
        logger.warning("insight_agent_oversized", path=path)
        _add_lost_reply(spend, payload)
        raise _unavailable() from None


def _add_lost_reply(spend: Spend | None, payload: dict) -> None:
    """Spend for a call whose reply never arrived: unknown, so an estimate
    at the ceiling price, flagged ``unpriced``."""
    if spend is None:
        return
    tokens_in = math.ceil(len(json.dumps(payload, default=str)) / 3)
    cost, _ = usage_cost("", None, tokens_in, LOST_REPLY_OUTPUT_TOKENS)
    spend.add(cost, tokens_in, LOST_REPLY_OUTPUT_TOKENS, unpriced=True)


def _post(path: str, payload: dict, spend: Spend | None = None, *, engine=None) -> dict:
    """One agent call. What it cost is added to ``spend`` from every reply
    that says, errors included. A pinned run's call names its engine; an
    agent running another one is asked again after each of
    ``MISMATCH_RETRY_DELAYS``, then the run fails (InsightEngineMismatch)."""
    headers = {"Authorization": f"Bearer {_setting('INSIGHTS_AGENT_SECRET')}"}
    pin = (engine or {}).get("engine_pin")
    if pin:
        headers[ENGINE_HEADER] = pin
    for attempt in range(len(MISMATCH_RETRY_DELAYS) + 1):
        status_code, content = _send(path, payload, headers, spend)
        try:
            body = json.loads(content)
        except ValueError:
            body = None
        if spend is not None and isinstance(body, dict):
            if status_code < 400 or isinstance(body.get("usage"), dict):
                _add_usage(spend, body.get("usage") or {})
        if not (pin and status_code == 409 and _error_code(body) == "engine_mismatch"):
            break
        if attempt == len(MISMATCH_RETRY_DELAYS):
            logger.error("insight_agent_engine_mismatch", path=path)
            raise InsightEngineMismatch()
        logger.warning("insight_agent_engine_retry", path=path, attempt=attempt + 1)
        time.sleep(MISMATCH_RETRY_DELAYS[attempt])
    if status_code >= 500 or status_code == 429:
        logger.warning("insight_agent_error", path=path, status=status_code)
        raise _unavailable()
    if status_code >= 400:
        logger.error(
            "insight_agent_rejected",
            path=path,
            status=status_code,
            code=_error_code(body),
        )
        raise InsightModelError("The Insights agent rejected the request.")
    if not isinstance(body, dict):
        logger.warning("insight_agent_not_json", path=path, status=status_code)
        raise _unavailable()
    return body


def _error_code(body) -> str | None:
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code)[:80] if code else None


def _count(value) -> int:
    """A non-negative whole count from a reply, 0 for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value) if math.isfinite(value) and value > 0 else 0


def _seconds(value) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value) if math.isfinite(value) and value > 0 else 0.0


def _tokens(entry: dict) -> tuple[int, int]:
    return (
        _count(entry.get("input_tokens") or entry.get("prompt_tokens")),
        _count(entry.get("output_tokens") or entry.get("completion_tokens")),
    )


def _add_usage(spend: Spend, usage: dict) -> None:
    """Add what an agent request cost. The agent reports tokens per model
    (``by_model``) and a ``cost_usd`` that is null (or 0) for models the
    gateway doesn't price, so each model is priced on its own: its gateway
    cost when it reports one, else its tokens at the model's price, else at
    the ceiling price, which flags the spend ``unpriced`` (never $0). So does
    a cost the agent itself says it does not know (``cost_known: false``, or
    a call it cut off and estimated)."""
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens, completion_tokens = _tokens(usage)
    by_model = usage.get("by_model")
    if isinstance(by_model, dict) and by_model:
        entries = list(by_model.items())
    else:
        entries = [(usage.get("model") or "", usage)]
    cost, priced = 0.0, True
    for model, entry in entries:
        if not isinstance(entry, dict):
            continue
        part, known = usage_cost(
            str(model),
            entry.get("cost_usd"),
            *_tokens(entry),
            image_parts=_count(entry.get("image_parts")),
        )
        cost += part
        priced = priced and known
    calls = usage.get("calls")
    if usage.get("cost_known") is False or any(
        isinstance(call, dict)
        and (call.get("estimated") is True or call.get("status") == "aborted")
        for call in (calls if isinstance(calls, list) else ())
    ):
        priced = False
    spend.add(
        cost,
        prompt_tokens,
        completion_tokens,
        image_parts=_count(usage.get("image_parts")),
        audio_seconds=_seconds(usage.get("audio_seconds")),
        unpriced=not priced,
    )


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
    """Where ``quote`` first occurs in the unit's text, exactly, else with
    its whitespace normalised: the unit's own words there (``own_words``),
    without the engine's around them, such as "Assistant: " or a failed
    call's "-> ERROR "."""
    words = quote.split()
    if not words:
        return None
    exact = re.finditer(re.escape(quote), content)
    loose = re.finditer(r"\s+".join(re.escape(w) for w in words), content)
    for match in itertools.chain(exact, loose):
        span = own_words(content, *match.span())
        if span:
            return span
    return None


def verified_evidence(evidence, content: str, key: str) -> tuple[list[dict], int]:
    """Keep quotes that occur in the unit's text, as its own words there,
    with their real offsets."""
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


# ─── v2 receipts and verification, checked host-side ────────────────────────
# ``ev2|<unit>|<span>|<field>|<start>|<length>`` and ``ev2m|<unit>|<media>|
# <segment>``, read from the right: the agent anchored each one in what its
# tools actually read of that span's field (PROTOCOL_V2 section 2).
_TEXT_RECEIPT = re.compile(
    r"ev2\|(?P<unit>.{1,256})\|(?P<span>s\d{1,6})\|(?P<field>[a-z][a-z_]{0,31})"
    r"\|(?P<start>\d{1,10})\|(?P<length>\d{1,10})"
)
_MEDIA_RECEIPT = re.compile(
    r"ev2m\|(?P<unit>.{1,256})\|(?P<media>m\d{1,6})\|(?P<segment>[\w:.\-]{1,64})"
)
MAX_V2_EVIDENCE = 4
VERIFICATION_STATUSES = ("verified", "failed", "incomplete")


def parse_receipt(evidence_id) -> dict | None:
    """A v2 receipt's parts, or None for anything else."""
    text = str(evidence_id or "")
    match = _TEXT_RECEIPT.fullmatch(text) or _MEDIA_RECEIPT.fullmatch(text)
    return match.groupdict() if match else None


def _plain(value, depth: int = 0):
    """Counts, flags and short codes from a reply, for storing: what a
    coverage or source record holds, and never trace text."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:120]
    if depth >= 2:
        return None
    if isinstance(value, dict):
        return {str(k)[:64]: _plain(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_plain(v, depth + 1) for v in value[:50]]
    return None


def _utf16_len(text: str) -> int:
    """A string's length as the agent counts it (JavaScript, UTF-16)."""
    return len(text.encode("utf-16-le")) // 2


def checked_receipts(evidence, key: str) -> tuple[list[dict], int]:
    """The v2 evidence items that cite unit ``key`` by a well-formed receipt
    (a text quote no longer than MAX_QUOTE, exactly as long as its receipt,
    on the span and field its receipt names), with only the fields kept for
    provenance."""
    kept, dropped = [], 0
    for item in evidence if isinstance(evidence, list) else []:
        receipt = (
            parse_receipt(item.get("evidence_id")) if isinstance(item, dict) else None
        )
        if receipt is None or receipt["unit"] != key:
            dropped += 1
            continue
        common = {
            "evidence_id": str(item["evidence_id"]),
            "supports": str(item.get("supports") or "")[:60],
            "span": str(item.get("span") or receipt.get("span") or "")[:16],
            "span_id": str(item.get("span_id") or "")[:128],
            "trace_id": str(item.get("trace_id") or "")[:128],
        }
        if "media" in receipt:
            observation = " ".join(str(item.get("observation") or "").split())
            kept.append(
                {
                    "kind": "media",
                    **common,
                    "media": receipt["media"],
                    "field": str(item.get("field") or "")[:32],
                    "observation": observation[:MAX_QUOTE],
                }
            )
            continue
        quote = item.get("quote")
        if (
            not isinstance(quote, str)
            or not quote.strip()
            or len(quote) > MAX_QUOTE
            or _utf16_len(quote) != int(receipt["length"])
            or common["span"] != receipt["span"]
            or str(item.get("field") or receipt["field"]) != receipt["field"]
        ):
            dropped += 1
            continue
        kept.append(
            {
                "kind": "text",
                **common,
                "field": receipt["field"],
                "quote": quote,
                "field_sha256": str(item.get("field_sha256") or "")[:80],
            }
        )
    return kept[:MAX_V2_EVIDENCE], dropped + max(0, len(kept) - MAX_V2_EVIDENCE)


def unit_evidence(facets: dict) -> list[dict]:
    """A unit's evidence items (v1 quotes or v2 receipts)."""
    evidence = (facets or {}).get("evidence")
    return (
        [e for e in evidence if isinstance(e, dict)]
        if isinstance(evidence, list)
        else []
    )


def reported_status(raw) -> bool:
    """Whether the agent gave a verification status this side knows."""
    return isinstance(raw, dict) and raw.get("status") in VERIFICATION_STATUSES


def verification(raw, *, v2: bool) -> dict:
    """The agent's verification verdict. Under v2 a missing or unknown status
    is ``incomplete``: nothing is presented as verified that the agent did not
    say was."""
    raw = dict(raw) if isinstance(raw, dict) else {}
    status = raw.get("status")
    if status not in VERIFICATION_STATUSES:
        if not v2:
            return raw
        raw["reason"] = "verification_missing" if status is None else "unknown_status"
        status = "incomplete"
    raw["status"] = status
    raw["reason"] = str(raw.get("reason") or "")[:80] or None
    return raw


# ─── Steps ──────────────────────────────────────────────────────────────────
def read_units(
    context: dict,
    units: list[tuple[str, str]],
    attributes: list[dict],
    spend: Spend,
    *,
    engine: dict | None = None,
) -> dict[str, dict]:
    """``{key: {"facets": ..., "evidence": [...], "error": str|None}}``."""
    body = _post(
        "/v1/read-units",
        {"run": context, "units": [{"key": k, "content": c} for k, c in units]},
        spend,
        engine=engine,
    )
    contents = dict(units)
    out: dict[str, dict] = {}
    for item in body.get("units") or []:
        key = str(item.get("key", ""))
        if key not in contents:
            continue
        if not item.get("ok", True):
            out[key] = {"error": _unit_error(item)}
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


def _unit_error(item: dict) -> str:
    error = item.get("error")
    if isinstance(error, dict) and error.get("code") == "model_rejected":
        from ee.agenthub.insights.llm import MODEL_REFUSED_MESSAGE

        return MODEL_REFUSED_MESSAGE
    message = error.get("message") if isinstance(error, dict) else error
    return str(message or "The Insights agent could not read this item.")[:200]


def read_units_v2(
    run,
    context: dict,
    keys: list[str],
    attributes: list[dict],
    spend: Spend,
    *,
    engine: dict,
) -> dict[str, dict]:
    """Read up to eight units of a v2 run through the evidence broker:
    ``{key: {"facets": ..., "coverage": {...}} | {"error": str}}``. Keys are
    the units' InsightUnit pks, which the capability names. ``facets`` holds
    the receipts under ``evidence`` and the reader's notes on its reading
    (dropped, forced, coverage note, source) under ``evidence_meta``."""
    from ee.agenthub.insights import broker

    capability = broker.mint(run, keys, stage="read", ttl_s=TIMEOUTS["/v1/read-units"])
    body = _post(
        "/v1/read-units",
        {
            "contract": CONTRACT_V2,
            "run": context,
            "units": [
                {"key": key, "evidence": {"source": "broker", "ref": key}}
                for key in keys
            ],
            "capability": capability,
        },
        spend,
        engine=engine,
    )
    out: dict[str, dict] = {}
    for item in body.get("units") or []:
        key = str(item.get("key", "")) if isinstance(item, dict) else ""
        if key not in keys or key in out:
            continue
        if item.get("ok") is not True:
            out[key] = {"error": _unit_error(item)}
            continue
        facets = normalise(item.get("facets") or {}, attributes)
        items, dropped = checked_receipts(item.get("evidence"), key)
        if facets["failure_present"] and not any(
            e["supports"] == "failure" for e in items
        ):
            # A failure claim needs a receipt that backs it.
            facets["failure_present"] = False
        reported = item.get("dropped_evidence")
        dropped += len(reported) if isinstance(reported, list) else _count(reported)
        # A list, as v1 stores it and the report page reads it; what the
        # reader said of its reading goes beside it.
        facets["evidence"] = items
        facets["evidence_meta"] = {
            "dropped": dropped,
            "dropped_reasons": _plain(item.get("dropped_reasons")) or {},
            "forced": _plain(item.get("forced")) or {},
            "verification": _plain(item.get("verification")) or {},
            "coverage_note": " ".join(str(item.get("coverage_note") or "").split())[
                :MAX_QUOTE
            ],
            "decisive_unread": bool(item.get("decisive_unread")),
            "source": _plain(item.get("source")) or {},
        }
        facets["dropped_quotes"] = dropped
        out[key] = {"facets": facets, "coverage": _plain(item.get("coverage")) or {}}
    for key in keys:
        out.setdefault(
            key, {"error": "The Insights agent returned nothing for this item."}
        )
    return out


def name_taxonomy(
    context: dict,
    clusters: list[dict],
    target_top_count: int,
    spend: Spend,
    *,
    engine: dict | None = None,
) -> dict:
    return _post(
        "/v1/name-taxonomy",
        {"run": context, "clusters": clusters, "target_top_count": target_top_count},
        spend,
        engine=engine,
    )


def naming_audit(reply: dict, engine: dict) -> dict:
    """The namer's audit of the taxonomy as a whole; under v2 one it didn't
    report is ``incomplete``, never taken as passed."""
    audit = reply.get("audit") if isinstance(reply, dict) else None
    if isinstance(audit, dict) and audit.get("status") in VERIFICATION_STATUSES:
        return {
            "status": audit["status"],
            "reason": str(audit.get("reason") or "")[:80] or None,
        }
    if is_v2(engine):
        return {"status": "incomplete", "reason": "audit_missing"}
    return {}


def analyse_report(payload: dict, spend: Spend, *, engine: dict | None = None) -> dict:
    return _post("/v1/analyse-report", payload, spend, engine=engine)


def ask(payload: dict, spend: Spend, *, engine: dict | None = None) -> dict:
    return _post("/v1/ask", payload, spend, engine=engine)
