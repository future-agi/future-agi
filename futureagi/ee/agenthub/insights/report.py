"""The written part of a report: findings, highlights and root causes.

With the Insights agent the analyst reads examples through tools and writes
findings with cited evidence; everything it returns is checked again here
(numbers must be in the report's statistics, quotes must occur in the unit's
text, citations must name units of the run). Without the agent the built-in
summary writes findings in the same shape, checked for their numbers, with
the quotes of the examples they cite (each found in its example's text when
it was read).
``summary["verification"]`` records which checks ran: ``numbers``, ``quotes``
(quotes on findings found in the units they cite) and ``audit`` (an
independent verifier passed the findings).

On an ``insights-agent/v2`` run verification fails closed: the agent's
``verification.status`` is kept (a missing one is ``incomplete``), findings
its verifier never judged are kept apart under ``summary["unverified"]``,
never under ``findings``, and ``audit`` is true only for ``verified``. Its
quotes are receipts of what the agent read of a unit's span graph
(``ev2|...``), checked against the units of the run; the units' text never
goes back to the agent, which reads them itself through the evidence broker
from the snapshots stored for the run (built again, as of the same moment,
before the report or a question when the store no longer holds them).
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from ee.agenthub.insights import agent as insight_agent
from ee.agenthub.insights.llm import Spend
from ee.agenthub.insights.summary import (
    _NUMBER,
    allowed_numbers,
    model_list,
    verified,
    write_summary,
)

MAX_FINDINGS = 6
MAX_HIGHLIGHTS = 6
MAX_REPORT_UNITS = 400
UNIT_CONTENT_CHARS = 9000
# Evidence items a unit shows the analyst, and the units of a v2 run its
# investigators may open with unit tools (one capability's worth).
UNIT_EVIDENCE = 6
MAX_INVESTIGATED_UNITS = 64
_STAT_KEYS = (
    "count",
    "share",
    "share_ci",
    "users",
    "sessions",
    "turns",
    "outcomes",
    "sentiment",
    "failure_rate",
    "unresolved_rate",
    "error_rate",
    "latency_ms",
    "cost",
    "attributes",
    "top_user_share",
)


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _objects(value) -> list[dict]:
    """The objects of a list the model wrote. A string or a number in their
    place has no fields to read, so it is skipped rather than a crash (a 500
    in Ask, a paid retry of the whole write in finish)."""
    return [item for item in model_list(value) if isinstance(item, dict)]


def _engine(run) -> dict:
    return insight_agent.pinned(run.settings)


def _investigated(units: list[dict]) -> list[str]:
    """The units a v2 report's investigators may open, at most
    MAX_INVESTIGATED_UNITS: every category in turn, failures first."""
    queues: dict[str, list[str]] = {}
    for unit in sorted(
        units, key=lambda u: (not u["facets"].get("failure_present"), u["time"])
    ):
        category = unit["sub_category_id"] or unit["category_id"]
        queues.setdefault(category, []).append(unit["key"])
    keys: list[str] = []
    while len(keys) < MAX_INVESTIGATED_UNITS and any(queues.values()):
        for queue in queues.values():
            if queue and len(keys) < MAX_INVESTIGATED_UNITS:
                keys.append(queue.pop(0))
    return keys


def _unit_payload(unit: dict, contents: dict[str, str], v2: bool) -> dict:
    item = {
        "key": unit["key"],
        "unit_id": unit["unit_id"],
        # Who and when, so "which users…" questions can be answered.
        "user_id": unit.get("user_id") or "",
        "session_id": unit.get("session_id") or "",
        "time": unit.get("time") or "",
        "category_id": unit["category_id"],
        "sub_category_id": unit["sub_category_id"],
        "summary": unit["summary"],
        "lens_answer": unit["facets"].get("lens_answer", ""),
        "facets": {
            k: v
            for k, v in unit["facets"].items()
            if k not in ("evidence", "evidence_meta")
        },
    }
    evidence = insight_agent.unit_evidence(unit["facets"])
    if v2:
        # Receipts as the reader returned them; no text: the agent reads the
        # unit itself.
        item["evidence"] = evidence[:UNIT_EVIDENCE]
        return item
    item["evidence"] = [
        {"evidence_id": e.get("evidence_id"), "quote": e.get("quote")} for e in evidence
    ]
    item["content"] = contents.get(unit["key"], "")[:UNIT_CONTENT_CHARS]
    return item


def agent_payload(
    run,
    result: dict,
    units: list[dict],
    contents: dict[str, str],
    *,
    stage: str = "report",
) -> dict:
    """What the analyst sees: the categories with their statistics and the
    analysed units with their summaries, evidence and text (on a v2 run,
    their receipts and a capability to read them through the broker)."""
    categories = [
        {
            "id": c["id"],
            "level": c.get("level"),
            "parent_id": c.get("parent_id"),
            "name": c.get("name", ""),
            "description": c.get("description", ""),
            "status": c.get("status", "active"),
            "stats": {k: c["stats"].get(k) for k in _STAT_KEYS if k in c["stats"]},
            "notable": [
                {
                    k: n.get(k)
                    for k in ("metric", "label", "kind", "value", "rest", "lift", "q")
                }
                for n in c.get("notable", [])
            ],
        }
        for c in result["categories"]
    ]
    chosen = sorted(units, key=lambda u: u["time"])[:MAX_REPORT_UNITS]
    totals = dict(result["totals"])
    totals.update(
        {
            "population_size": result.get("population_size"),
            "sample_size": result.get("sample_size"),
            "excluded": result.get("excluded"),
        }
    )
    changes = result.get("changes") or {}
    v2 = insight_agent.is_v2(_engine(run))
    payload = {
        "run": insight_agent.run_context(run.settings, run.unit, run.id),
        "totals": totals,
        "categories": categories,
        "units": [_unit_payload(u, contents, v2) for u in chosen],
        "previous": {"changes": changes.get("categories", [])} if changes else None,
        "limits": {"max_model_calls": 40, "max_seconds": 300},
    }
    investigated = _investigated(chosen) if v2 else []
    if investigated:
        from ee.agenthub.insights import broker

        ttl = insight_agent.TIMEOUTS[
            "/v1/ask" if stage == "ask" else "/v1/analyse-report"
        ]
        # Their snapshots, as the reading built them (rebuilt with the same
        # cutoff where the store no longer has them); a unit not stored is
        # one the investigators can't open.
        by_key = {u["key"]: u for u in chosen}
        ready = broker.prebuild(
            run,
            [_unit_row(by_key[key]) for key in investigated],
            stage=stage,
            ttl_s=ttl,
        ).ready
        if ready:
            payload["evidence_source"] = {
                "source": "broker",
                "capability": broker.mint(run, ready, stage=stage, ttl_s=ttl),
            }
    return payload


def _unit_row(unit: dict) -> SimpleNamespace:
    """What a snapshot is built from: the unit's pk and the id it reads."""
    return SimpleNamespace(pk=int(unit["key"]), unit_id=unit["unit_id"])


def checked_numbers(result: dict, units: list[dict]) -> set[str]:
    """Numbers a written claim may use: the report's statistics plus the
    per-user counts the analyst can look up (``list_users``)."""
    from collections import Counter, defaultdict

    numbers = allowed_numbers(result)
    per_user: dict[str, Counter] = defaultdict(Counter)
    for unit in units:
        user = unit.get("user_id")
        if not user:
            continue
        facets = unit.get("facets") or {}
        per_user[user]["units"] += 1
        per_user[user]["failures"] += 1 if facets.get("failure_present") else 0
        per_user[user][f"outcome:{facets.get('outcome', 'unclear')}"] += 1
        if unit.get("session_id"):
            per_user[user][f"session:{unit['session_id']}"] = 1
    for counts in per_user.values():
        for key, value in counts.items():
            if not key.startswith("session:"):
                numbers.add(str(value))
        numbers.add(str(sum(1 for k in counts if k.startswith("session:"))))
    numbers.add(str(len(per_user)))
    return numbers


def _without_ids(text: str, units: list[dict]) -> str:
    """Ids carry digits that are not claims: user ids like cust_ada_532, and
    the session or trace id of a unit (956ae508-1958-…) an answer names. An
    id of digits alone stays in, so it never vouches for a number."""
    for user in {u.get("user_id") for u in units if u.get("user_id")}:
        text = text.replace(user, "")
    ids = {u.get(k) or "" for u in units for k in ("unit_id", "session_id")}
    for unit_id in sorted(ids, key=len, reverse=True):
        if unit_id in text and re.search(r"[A-Za-z]", unit_id):
            text = re.sub(rf"(?<![\w-]){re.escape(unit_id)}(?![\w-])", "", text)
    return text


def _cited_numbers(keys: list[str], evidence: list[dict]) -> set[str]:
    """The numbers a claim's own citations hold, as ``verified`` reads them
    (the agent's numbers rule): how many units it cites, and what its kept
    quotes show (an amount the agent promised, a tool's retry delay)."""
    numbers = {str(len(set(keys)))} if keys else set()
    for item in evidence:
        for found in _NUMBER.findall(item.get("quote") or ""):
            found = found.replace(",", "")
            numbers.add(found.rstrip("0").rstrip(".") if "." in found else found)
    return numbers


def _inline_citations(
    text: str, units_by_key: dict[str, dict], categories
) -> tuple[str, list, list]:
    """Examples and categories cited in the text as [key] or [id] (unit keys
    are row ids, category ids carry digits) are citations, not claims. They
    leave the text, which the page follows with chips for cited examples, and
    join the paragraph's unit keys and category ids. A bracket holding
    anything else stays and is checked like the rest."""
    keys: list[str] = []
    ids: list[str] = []

    def take(match):
        tokens = [t for t in re.split(r"[\s,]+", match.group(1)) if t]
        if not tokens or not all(t in units_by_key or t in categories for t in tokens):
            return match.group(0)
        keys.extend(t for t in tokens if t in units_by_key)
        ids.extend(t for t in tokens if t not in units_by_key)
        return ""

    return re.sub(r"\s*\[([^\[\]]+)\]", take, text).strip(), keys, ids


class _Refs:
    """Numbers cited units #1, #2… in order of first citation."""

    def __init__(self, units_by_key: dict[str, dict]):
        self.units = units_by_key
        self.order: list[str] = []

    def ref(self, key: str) -> int | None:
        if key not in self.units:
            return None
        if key not in self.order:
            self.order.append(key)
        return self.order.index(key) + 1


def _evidence(items, units_by_key, contents, *, v2: bool = False) -> list[dict]:
    kept = []
    for item in _objects(items):
        key = str(item.get("unit_key", ""))
        if key not in units_by_key:
            continue
        if v2:
            kept.extend(_receipt_evidence(item, key, units_by_key[key]))
            continue
        found, _ = insight_agent.verified_evidence([item], contents.get(key, ""), key)
        for e in found:
            kept.append(
                {
                    "unit_id": units_by_key[key]["unit_id"],
                    "quote": e["quote"],
                    "evidence_id": e["evidence_id"],
                }
            )
    return kept[:3]


def _receipt_evidence(item: dict, key: str, unit: dict) -> list[dict]:
    """A v2 citation of unit ``key``: a receipt the reader stored for that
    unit keeps what was stored; another (a region an investigator read) must
    be a well-formed receipt of that unit whose quote is exactly as long as
    the region. An image has no quote, only what the agent saw in it
    (``observation``)."""
    stored = {
        e.get("evidence_id"): e for e in insight_agent.unit_evidence(unit["facets"])
    }
    known = stored.get(str(item.get("evidence_id") or ""))
    if known is not None:
        found = [known]
    else:
        found, _ = insight_agent.checked_receipts([item], key)
    kept = []
    for e in found:
        cited = {
            "unit_id": unit["unit_id"],
            "quote": e.get("quote") or "",
            "evidence_id": e["evidence_id"],
            "span": e.get("span", ""),
            "field": e.get("field", ""),
            "read_by": "reader" if known is not None else "investigator",
        }
        if e.get("kind") == "media":
            cited["observation"] = e.get("observation", "")
        kept.append(cited)
    return kept


def _findings(
    raws, units, units_by_key, categories, numbers, refs, contents, *, v2
) -> tuple[list[dict], int]:
    """The findings that check out, and how many did not."""
    findings, dropped = [], 0
    for raw in _objects(raws):
        headline = _clean(raw.get("headline"), 200)
        detail = _clean(raw.get("detail"), 600)
        action = _clean(raw.get("action"), 200)
        keys = [str(k) for k in model_list(raw.get("unit_keys"))]
        keys += [str(e.get("unit_key", "")) for e in _objects(raw.get("evidence"))]
        # Checked against what it cites too, as a paragraph is; its example
        # numbers are given only once it checks out.
        evidence = _evidence(raw.get("evidence"), units_by_key, contents, v2=v2)
        grounded = _cited_numbers([k for k in keys if k in units_by_key], evidence)
        if not headline or not verified(
            _without_ids(f"{headline} {detail} {action}", units), numbers | grounded
        ):
            dropped += 1
            continue
        cited = [refs.ref(k) for k in dict.fromkeys(keys)]
        findings.append(
            {
                "headline": headline,
                "detail": detail,
                "action": action,
                "severity": (
                    raw.get("severity")
                    if raw.get("severity") in ("high", "medium", "low")
                    else "medium"
                ),
                "category_ids": [
                    str(c)
                    for c in model_list(raw.get("category_ids"))
                    if str(c) in categories
                ],
                "refs": [
                    {"ref": r, "unit_id": units_by_key[refs.order[r - 1]]["unit_id"]}
                    for r in cited
                    if r
                ],
                "evidence": evidence,
            }
        )
        if len(findings) >= MAX_FINDINGS:
            break
    return findings, dropped


def _category_insights(raws, units_by_key, categories, contents, *, v2) -> dict:
    insights = {}
    for raw in _objects(raws):
        cid = str(raw.get("category_id") or "")
        if cid in categories and _clean(raw.get("root_cause"), 400):
            insights[cid] = {
                "root_cause": _clean(raw.get("root_cause"), 400),
                "fix": _clean(raw.get("fix"), 200),
                "evidence": _evidence(
                    raw.get("evidence"), units_by_key, contents, v2=v2
                ),
            }
    return insights


def from_agent(
    reply: dict,
    result: dict,
    units: list[dict],
    contents: dict[str, str],
    *,
    v2: bool = False,
) -> dict:
    """Map the analyst's reply onto unit ids and keep only what checks out.
    On a v2 run, what its verifier did not pass stays out of ``findings``."""
    units_by_key = {u["key"]: u for u in units}
    categories = {c["id"]: c for c in result["categories"]}
    numbers = checked_numbers(result, units)
    refs = _Refs(units_by_key)
    raw_findings = model_list(reply.get("findings"))
    raw_unverified = model_list(reply.get("unverified_findings"))
    raw_insights = model_list(reply.get("category_insights"))
    raw_unverified_insights = model_list(reply.get("unverified_category_insights"))
    verification = insight_agent.verification(reply.get("verification"), v2=v2)
    if v2 and (
        verification["status"] == "failed"
        or not insight_agent.reported_status(reply.get("verification"))
    ):
        # No verifier verdict stands behind any of it: nothing is verified.
        raw_unverified = raw_findings + raw_unverified
        raw_unverified_insights = raw_insights + raw_unverified_insights
        raw_findings, raw_insights = [], []
    findings, dropped = _findings(
        raw_findings, units, units_by_key, categories, numbers, refs, contents, v2=v2
    )
    dropped += int((reply.get("dropped") or {}).get("findings") or 0)
    highlights = []
    for raw in _objects(reply.get("highlights")):
        key = str(raw.get("unit_key", ""))
        unit = units_by_key.get(key)
        if not unit:
            continue
        category_id = str(raw.get("category_id") or "")
        if category_id not in categories:
            category_id = unit["sub_category_id"] or unit["category_id"]
        quotes = _evidence(
            [
                {
                    "unit_key": key,
                    "quote": raw.get("quote"),
                    "evidence_id": raw.get("evidence_id"),
                }
            ],
            units_by_key,
            contents,
            v2=v2,
        )
        highlights.append(
            {
                "ref": refs.ref(key),
                "unit_id": unit["unit_id"],
                "category_id": category_id,
                "category_name": categories.get(category_id, {}).get("name", ""),
                "title": _clean(raw.get("title"), 120) or _clean(unit["summary"], 90),
                "summary": unit["summary"],
                "reason": _clean(raw.get("reason"), 240),
                "quote": quotes[0]["quote"] if quotes else "",
            }
        )
        if len(highlights) >= MAX_HIGHLIGHTS:
            break
    insights = _category_insights(
        raw_insights, units_by_key, categories, contents, v2=v2
    )
    verification.update(
        {
            "numbers": True,
            "quotes": any(f["evidence"] for f in findings),
            "audit": (
                verification.get("status") == "verified"
                if v2
                else verification.get("model_verifier") == "passed"
            ),
        }
    )
    summary = {
        "title": _clean(reply.get("title"), 200),
        "findings": findings,
        "key_points": _legacy_points(findings),
        "highlights": highlights,
        "examples": [
            {
                "ref": i,
                "unit_id": units_by_key[k]["unit_id"],
                "category_id": units_by_key[k]["sub_category_id"]
                or units_by_key[k]["category_id"],
            }
            for i, k in enumerate(refs.order, start=1)
        ],
        "category_insights": insights,
        "dropped_points": dropped,
        "verification": verification,
    }
    if v2 and (verification["status"] != "verified" or raw_unverified):
        # Numbered on their own: they are not the report's examples.
        apart, _ = _findings(
            raw_unverified,
            units,
            units_by_key,
            categories,
            numbers,
            _Refs(units_by_key),
            contents,
            v2=True,
        )
        summary["unverified"] = {
            "findings": apart,
            "category_insights": _category_insights(
                raw_unverified_insights, units_by_key, categories, contents, v2=True
            ),
            "status": verification["status"],
            "reason": verification.get("reason"),
        }
    return summary


def _legacy_points(findings: list[dict]) -> list[dict]:
    return [
        {
            "text": " ".join(p for p in (f["headline"], f["detail"]) if p),
            "category_ids": f["category_ids"],
            "refs": f["refs"],
        }
        for f in findings
    ]


def write(
    run,
    result: dict,
    units: list[dict],
    contents: dict[str, str],
    spend: Spend,
    *,
    cancelled=None,
) -> dict:
    """The report's summary and findings. On the agent, ``cancelled`` (the
    run was stopped) gives up its call."""
    engine = _engine(run)
    if insight_agent.is_omega(engine):
        reply = insight_agent.analyse_report(
            agent_payload(run, result, units, contents),
            spend,
            engine=engine,
            cancelled=cancelled,
        )
        return from_agent(
            reply, result, units, contents, v2=insight_agent.is_v2(engine)
        )
    by_category: dict[str, list[dict]] = {}
    for unit in units:
        by_category.setdefault(unit["sub_category_id"], []).append(unit)
        by_category.setdefault(unit["category_id"], []).append(unit)
    summary = write_summary(
        question=run.settings.get("question") or "",
        unit=run.unit,
        agent_description=run.settings.get("agent_description", ""),
        result=result,
        units_by_category=by_category,
        spend=spend,
    )
    summary.setdefault(
        "findings",
        [
            {
                "headline": p["text"].split(". ")[0],
                "detail": ". ".join(p["text"].split(". ")[1:]),
                "action": "",
                "severity": "medium",
                "category_ids": p["category_ids"],
                "refs": p["refs"],
                "evidence": [],
            }
            for p in summary.get("key_points", [])
        ],
    )
    summary["verification"] = {
        "numbers": True,
        "quotes": any(f.get("evidence") for f in summary["findings"]),
        "audit": False,
    }
    return summary


# ─── Follow-up questions ────────────────────────────────────────────────────
MAX_ASK_UNITS = 150
_ASK_SYSTEM = (
    "You answer questions about an analytics report on an AI agent's production "
    "traffic. Use only the report's statistics and the listed examples. Use numbers "
    "exactly as they appear in the statistics. Cite examples by their [key]. If the "
    "report cannot answer, say so plainly. Never include personal data. Reply with "
    "JSON only."
)


def _answer_payload(
    reply: dict,
    result: dict,
    units: list[dict],
    contents: dict[str, str],
    *,
    v2: bool = False,
) -> dict:
    """The answer's paragraphs that check out. On a v2 run an answer its
    verifier did not pass is returned under ``unverified_answer``, with
    ``answer`` empty."""
    paragraphs, dropped = _paragraphs(
        reply.get("answer"), result, units, contents, v2=v2
    )
    dropped_reply = reply.get("dropped")
    if isinstance(dropped_reply, dict):
        dropped += int(dropped_reply.get("paragraphs") or 0)
    out = {
        "answer": paragraphs,
        "follow_ups": [
            _clean(q, 160)
            for q in model_list(reply.get("follow_ups"))[:3]
            if isinstance(q, str) and q.strip()
        ],
        "dropped": dropped,
    }
    if not v2:
        return out
    verification = insight_agent.verification(reply.get("verification"), v2=True)
    apart, more = _paragraphs(
        reply.get("unverified_answer"), result, units, contents, v2=True
    )
    if verification["status"] != "verified" or not insight_agent.reported_status(
        reply.get("verification")
    ):
        apart, out["answer"] = paragraphs + apart, []
    out.update(
        {
            "unverified_answer": apart,
            "dropped": out["dropped"] + more,
            "verification": {
                "status": verification["status"],
                "reason": verification.get("reason"),
            },
        }
    )
    return out


def _paragraphs(
    raws, result: dict, units: list[dict], contents: dict[str, str], *, v2: bool
) -> tuple[list[dict], int]:
    units_by_key = {u["key"]: u for u in units}
    categories = {c["id"] for c in result["categories"]}
    numbers = checked_numbers(result, units)
    paragraphs, dropped = [], 0
    for raw in model_list(raws):
        if not isinstance(raw, dict):
            # A model-written item that isn't an object ({"answer": ["text"]})
            # is dropped like any unverifiable paragraph, never a 500.
            dropped += 1
            continue
        text, cited, cited_ids = _inline_citations(
            _clean(raw.get("text"), 1200), units_by_key, categories
        )
        if not text:
            continue
        keys = [
            str(k) for k in model_list(raw.get("unit_keys")) if str(k) in units_by_key
        ] + cited
        # Its citations are checked first: how many units it cites, or a
        # number one of its quotes holds, is what it cites, not a statistic.
        evidence = _evidence(raw.get("evidence"), units_by_key, contents, v2=v2)
        if not verified(
            _without_ids(text, units), numbers | _cited_numbers(keys, evidence)
        ):
            dropped += 1
            continue
        ids = [
            str(c) for c in model_list(raw.get("category_ids")) if str(c) in categories
        ] + cited_ids
        paragraphs.append(
            {
                "text": text,
                "unit_ids": [units_by_key[k]["unit_id"] for k in dict.fromkeys(keys)],
                "category_ids": list(dict.fromkeys(ids)),
                "evidence": evidence,
            }
        )
    return paragraphs, dropped


def answer(
    run,
    result: dict,
    units: list[dict],
    contents: dict[str, str],
    question: str,
    history: list[dict],
    spend: Spend,
) -> dict:
    engine = _engine(run)
    # A report written by the agent is asked about there while the agent is
    # configured. With the agent turned off (a rollback) the built-in engine
    # answers instead, so the report's follow-up questions keep working.
    if insight_agent.is_omega(engine) and insight_agent.enabled():
        payload = agent_payload(run, result, units, contents, stage="ask")
        summary = result.get("summary") or {}
        payload.update(
            {
                "question": question,
                "history": history[-10:],
                # The report's own findings, as context for the answer.
                "report": {
                    "title": summary.get("title", ""),
                    "findings": [
                        {
                            k: f.get(k)
                            for k in ("headline", "detail", "action", "category_ids")
                        }
                        for f in summary.get("findings", [])
                    ],
                },
            }
        )
        return _answer_payload(
            insight_agent.ask(payload, spend, engine=engine),
            result,
            units,
            contents,
            v2=insight_agent.is_v2(engine),
        )
    from ee.agenthub.insights.llm import THINKING, chat_json

    lines = []
    for c in result["categories"]:
        st = c["stats"]
        indent = "  " if c.get("level") == 2 else ""
        lines.append(
            f"{indent}[{c['id']}] {c['name']}: {st['count']} ({round(st['share'] * 100)}%), "
            f"{st.get('users', 0)} users, failure {round((st.get('failure_rate') or 0) * 100)}%, "
            f"unresolved {round((st.get('unresolved_rate') or 0) * 100)}%"
        )
    examples = [
        f"[{u['key']}] ({u['sub_category_id'] or u['category_id']}) {u['summary']}"
        for u in sorted(units, key=lambda u: u["time"])[:MAX_ASK_UNITS]
    ]
    talk = "\n".join(
        f"{m.get('role')}: {_clean(m.get('content'), 600)}" for m in history[-6:]
    )
    prompt = (
        f"Report question: {run.settings.get('question', '')}\n"
        f"Totals: {result['totals'].get('count')} analysed, {result['totals'].get('users')} users.\n"
        "Categories:\n"
        + "\n".join(lines)
        + "\n\nExamples:\n"
        + "\n".join(examples)
        + (f"\n\nConversation so far:\n{talk}" if talk else "")
        + f'\n\nQuestion: {question}\n\nReturn {{"answer": [{{"text": "a paragraph", '
        '"unit_keys": ["..."], "category_ids": ["..."]}}], "follow_ups": '
        '["up to 3 short next questions"]}.'
    )
    reply = chat_json(_ASK_SYSTEM, prompt, tier=THINKING, max_tokens=1600, spend=spend)
    return _answer_payload(
        reply if isinstance(reply, dict) else {}, result, units, contents
    )
