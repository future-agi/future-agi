"""Cheap-LLM metadata for eval-source error clusters.

An eval-source cluster needs a Sentry-style title (not the raw judge
sentence), a fix layer, and a severity — none of which the eval pipeline
provides (title was first-sentence text, fix_layer was None, severity
defaulted to medium for everything). One cheap LLM call over the
cluster's seed reasoning yields all three. Mirrors the trace scanner's
gateway-LLM + cost-accounting + OSS fallback path.

The public caller (tracer.queries.eval_clustering) import-guards this
module and falls back to deterministic title + null fix_layer + default
severity when this EE code is absent (OSS) or the call fails.
"""

import json
import re
from typing import Optional

import structlog

from agentic_eval.core.utils.model_config import ModelConfigs

from tracer.types.eval_cluster_types import EvalClusterMeta

try:
    from ee.usage.services.gateway_llm_client import (
        call_llm as gateway_call_llm,
        call_llm_raw,
        get_gateway_client,
    )
except ImportError:  # pragma: no cover - ee is always present in this module
    gateway_call_llm = None
    call_llm_raw = None
    get_gateway_client = None

logger = structlog.get_logger(__name__)

# Shared cheap model tier (same config Chauffeur uses); resolves its
# model id from environment, so it works wherever the gateway is wired.
_MODEL = ModelConfigs.TURING_FLASH
_MAX_TOKENS = 160
_TEMPERATURE = 0.0

_FIX_LAYERS = {"Tools", "Prompt", "Orchestration", "Guardrails"}
_SEVERITIES = {"critical", "high", "medium", "low"}

_SYSTEM = (
    "You triage AI-agent failure clusters for an error feed engineers use "
    "like Sentry. Given an evaluation name and the evaluator's reasoning "
    "for why traces in this cluster failed, return strict JSON for the "
    "whole cluster:\n"
    '{"title": str, "fix_layer": str, "severity": str}\n\n'
    "title: name the agent's failing BEHAVIOR and effect in concrete "
    "product terms (not that an evaluation failed). The RECURRING pattern, "
    "not one trace — never trace-specific ids/quotes/varying numbers. "
    "5-11 words, no quotes, no trailing punctuation, no meta-language.\n"
    "fix_layer: where the fix most likely belongs — one of:\n"
    "  Prompt        (agent instructions / persona / wording)\n"
    "  Tools         (tool availability, definitions, or usage)\n"
    "  Orchestration (flow, routing, turn-taking, escalation logic)\n"
    "  Guardrails    (safety, validation, policy enforcement)\n"
    "severity: one of critical | high | medium | low —\n"
    "  critical: unsafe/harmful output, data leak, or total task failure\n"
    "  high:     core task failed or user clearly misled/harmed\n"
    "  medium:   degraded but task largely achieved\n"
    "  low:      minor or cosmetic\n"
    "Output ONLY the JSON object.\n\n"
    "Examples:\n"
    "eval=detect_hallucination | reasoning=This evaluation is given "
    "because the agent stated the refund policy is 90 days when the "
    "provided context says 30 days.\n"
    '{"title": "Agent states refund-policy duration unsupported by '
    'context", "fix_layer": "Prompt", "severity": "high"}\n'
    "eval=pii_leak | reasoning=The verdict is Fail because the agent read "
    "the customer's full card number back in plain text.\n"
    '{"title": "Agent echoes full card number in plain text", '
    '"fix_layer": "Guardrails", "severity": "critical"}\n'
    "eval=turn_taking_and_flow | reasoning=The agent repeatedly "
    "interrupted the customer before they finished speaking.\n"
    '{"title": "Agent interrupts customer mid-utterance, breaking flow", '
    '"fix_layer": "Orchestration", "severity": "medium"}'
)


def _clean_title(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = str(text).strip().strip('"').strip().rstrip(".").strip()
    return cleaned[:200] or None


def _parse_meta(raw: Optional[str]) -> EvalClusterMeta:
    """Parse the JSON object, tolerating markdown fences; validate each
    field against its allowed set. Unparseable/invalid -> None per field
    so the caller can fall back independently."""
    meta = EvalClusterMeta()
    if not raw:
        return meta
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return meta
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return meta

    meta.title = _clean_title(data.get("title"))
    fl = str(data.get("fix_layer", "")).strip().capitalize()
    if fl in _FIX_LAYERS:
        meta.fix_layer = fl
    sev = str(data.get("severity", "")).strip().lower()
    if sev in _SEVERITIES:
        meta.severity = sev
    return meta


def _invoke(
    messages: list, eval_name: str, max_tokens: int = _MAX_TOKENS
) -> Optional[str]:
    client = get_gateway_client() if get_gateway_client else None
    if client is None:
        # OSS / no gateway — content-only helper, no cost data.
        if gateway_call_llm is None:
            return None
        return gateway_call_llm(
            model=_MODEL.model_name,
            messages=messages,
            temperature=_TEMPERATURE,
            max_tokens=max_tokens,
        )

    _result = call_llm_raw(
        client,
        model=_MODEL.model_name,
        messages=messages,
        temperature=_TEMPERATURE,
        max_tokens=max_tokens,
    )
    if _result.cost_usd:
        logger.info(
            "eval_cluster_meta_cost",
            cost_usd=_result.cost_usd,
            eval_name=eval_name,
        )
    response = _result.response
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError):
        return None


def generate_eval_cluster_meta(
    eval_name: str, reasoning: str
) -> Optional[EvalClusterMeta]:
    """Return an EvalClusterMeta (any field may be None to let the caller
    fall back), or None if there's nothing to work with.
    """
    if not reasoning or not reasoning.strip():
        return None

    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"eval={eval_name} | reasoning={reasoning.strip()[:1500]}"
            ),
        },
    ]
    return _parse_meta(_invoke(messages, eval_name))


# ---------------------------------------------------------------------------
# Distill-then-embed: canonical failure phrases for eval clustering
# ---------------------------------------------------------------------------
#
# Raw evaluator explanations carry trace-specific noise (names, numbers,
# quoted utterances), so embedding them fragments clusters: two traces
# failing the same way land far apart in embedding space. Distilling each
# explanation to a canonical phrase BEFORE embedding collapses that noise;
# the tight cosine threshold then does its job.

_DISTILL_BATCH_SIZE = 20
_DISTILL_MAX_TOKENS = 800

_CLUSTER_TITLE_SYSTEM = (
    "You name a group of AI-agent failures that share ONE underlying bug.\n"
    "You are given the individual failure descriptions from that group.\n"
    "Write ONE title, 6-14 words, that describes the shared failure so it fits "
    "EVERY member.\n"
    "Name NO ticker, client, person, amount, date or id — those differ between "
    "members and a title naming one of them describes a single member rather "
    "than the group. Say what the agent did wrong and what it should have done "
    "instead.\n"
    "If the descriptions do NOT share one underlying failure, reply exactly: "
    "MIXED\n"
    "Output ONLY the title text, no quotes, no preamble."
)

_CLUSTER_TITLE_MAX_TOKENS = 60
# A title is only useful if it generalises; these are the giveaways that the
# model ignored the instruction and described one member.
_ENTITY_GIVEAWAY = re.compile(
    r"\b(?:[A-Z]{2,5}\b(?![a-z])|\$[\d,]+|£[\d,]+|\d{4}-\d{2}-\d{2}|CLI-\w+)"
)


def _spread(items: list[str], k: int) -> list[str]:
    """Evenly spaced sample, in order.

    The title has to fit EVERY member, so it must be written from a sample of
    the group rather than the front of one. On this project's largest cluster
    the first 25 members happened to be the fabricated-number variants and the
    title came back "Fabricated quantitative data instead of calling tools",
    which over-claims against the members that merely answered in prose; an
    evenly spread 25 gave "provided text answers instead of executing required
    data retrieval tools", which fits all of them. Deterministic rather than
    random so a cluster does not get a different title on every recompute.
    """
    if len(items) <= k:
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def generate_scan_cluster_title(briefs: list[str]) -> Optional[str]:
    """One entity-free title describing what a cluster's members share.

    Returns None when the model declines (MIXED), when the result still names a
    ticker/amount/id, or on any failure — the caller then keeps its deterministic
    medoid title. A wrong title is worse than a merely unrepresentative one.
    """
    if not briefs or len(briefs) < 2:
        return None
    sample = _spread(briefs, 25)
    lines = "\n".join(f"- {b.strip()[:200]}" for b in sample)
    try:
        raw = _invoke(
            [
                {"role": "system", "content": _CLUSTER_TITLE_SYSTEM},
                {"role": "user", "content": lines},
            ],
            "cluster_title",
            max_tokens=_CLUSTER_TITLE_MAX_TOKENS,
        )
    except Exception:
        logger.warning("scan_cluster_title_failed", exc_info=True)
        return None

    title = (raw or "").strip().strip('"').strip()
    if not title or title.upper().startswith("MIXED"):
        return None
    if len(title.split()) > 20 or len(title) > 160:
        return None
    if _ENTITY_GIVEAWAY.search(title):
        logger.info("scan_cluster_title_rejected_entity", title=title[:80])
        return None
    return title


_DISTILL_SYSTEM = (
    "You normalize AI-agent evaluation failures for clustering. For EACH "
    "numbered item (eval name + the evaluator's reasoning), produce one "
    "canonical failure phrase: 5-12 words naming the failing behavior and "
    "its effect. Strip everything trace-specific — names, numbers, ids, "
    "quotes, dates, amounts — so two traces failing the SAME way produce "
    "the SAME phrase. Return strict JSON: an array of strings, one per "
    "item, in input order. Output ONLY the JSON array.\n\n"
    "Example input:\n"
    "1. eval=pii_leak | reasoning=The verdict is Fail because the agent "
    "read Maria Lopez's full card number 4111-1111-1111-1111 back to her "
    "in plain text.\n"
    "2. eval=pii_leak | reasoning=Fail: while confirming payment the agent "
    "repeated the customer's complete card digits aloud.\n"
    "3. eval=goal_completion | reasoning=The session ended without the "
    "flight to Denver on May 3 ever being booked despite 11 turns.\n"
    "Example output:\n"
    '["agent reads full card number back in plain text", '
    '"agent reads full card number back in plain text", '
    '"session ends without completing the user\'s booking goal"]'
)


def _parse_phrases(raw: Optional[str], expected: int) -> Optional[list[Optional[str]]]:
    """Parse the JSON array; None unless it is exactly ``expected`` strings
    (a misaligned array would assign phrases to the wrong rows)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    phrases = []
    for item in data:
        phrase = str(item).strip() if item else ""
        phrases.append(phrase[:300] or None)
    return phrases


def distill_failure_phrases(
    items: list[tuple[str, str]],
) -> list[Optional[str]]:
    """[(eval_name, explanation)] → list of canonical phrases (same length).

    Batched gateway calls; a failed or misparsed batch yields None entries
    so the caller falls back to embedding those rows' raw explanations.
    """
    out: list[Optional[str]] = []
    for start in range(0, len(items), _DISTILL_BATCH_SIZE):
        chunk = items[start : start + _DISTILL_BATCH_SIZE]
        lines = [
            f"{i + 1}. eval={name} | reasoning={(explanation or '').strip()[:400]}"
            for i, (name, explanation) in enumerate(chunk)
        ]
        phrases = None
        try:
            phrases = _parse_phrases(
                _invoke(
                    [
                        {"role": "system", "content": _DISTILL_SYSTEM},
                        {"role": "user", "content": "\n".join(lines)},
                    ],
                    "distill",
                    max_tokens=_DISTILL_MAX_TOKENS,
                ),
                len(chunk),
            )
        except Exception:
            logger.warning(
                "eval_distill_batch_failed",
                chunk_start=start,
                chunk_size=len(chunk),
                exc_info=True,
            )
        out.extend(phrases if phrases else [None] * len(chunk))
    return out


# ---------------------------------------------------------------------------
# Scanner cluster severity (single seed issue → user-impact severity)
# ---------------------------------------------------------------------------

_SCAN_SEVERITY_SYSTEM = (
    "You triage AI-agent failure clusters for an error feed engineers use "
    "like Sentry. Given a scanner issue's category and one-line description, "
    "rate the user-IMPACT severity for the whole cluster. Return strict JSON:\n"
    '{"severity": str}\n\n'
    "severity: one of critical | high | medium | low —\n"
    "  critical: unsafe/harmful output, data leak, or total task failure\n"
    "  high:     core task failed or the user was clearly misled/harmed\n"
    "  medium:   degraded but the task was largely achieved\n"
    "  low:      minor or cosmetic\n"
    "Output ONLY the JSON object.\n\n"
    "Examples:\n"
    "category=Service Errors | issue=orders-api connection refused\n"
    '{"severity": "high"}\n'
    "category=Authentication Errors | issue=billing-api 401 token expired\n"
    '{"severity": "high"}\n'
    "category=Instruction Non-compliance | issue=answered in prose not JSON\n"
    '{"severity": "low"}\n'
    "category=Timeout Issues | issue=billing-api timed out after 30000ms\n"
    '{"severity": "high"}'
)


def _parse_severity(raw: Optional[str]) -> Optional[str]:
    """Pull a validated severity out of the model's JSON; None if absent."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    sev = str(data.get("severity", "")).strip().lower()
    return sev if sev in _SEVERITIES else None


def generate_scan_cluster_severity(
    category: str, brief: str
) -> Optional[str]:
    """User-impact severity (critical|high|medium|low) for a scanner cluster's
    seed issue. None on OSS/no-gateway/LLM-failure so the caller defaults."""
    if not (category or brief):
        return None
    messages = [
        {"role": "system", "content": _SCAN_SEVERITY_SYSTEM},
        {
            "role": "user",
            "content": f"category={category} | issue={(brief or '').strip()[:500]}",
        },
    ]
    return _parse_severity(_invoke(messages, category or "scan"))
