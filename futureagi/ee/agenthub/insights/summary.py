"""Executive summary for an Insights report.

The thinking model sees only computed statistics and numbered example units
(each with a quote from its conversation), writes a few findings with
citations, and picks highlighted examples. Every number in a finding
(headline, detail and action, citations aside) is then checked against the
statistics; a finding with a number that isn't there is dropped rather than
shown. A finding and a highlight carry the quotes of the examples they cite,
each found verbatim in its example's text when the example was read; nothing
here checks a finding's own words against them.
"""

from __future__ import annotations

import re

from ee.agenthub.insights.llm import THINKING, Spend, chat_json

MAX_POINTS = 5
MAX_HIGHLIGHTS = 6
EXAMPLES_PER_CATEGORY = 2
MAX_FINDING_QUOTES = 3
EXAMPLE_QUOTE_CHARS = 200

UNIT_WORDS = {"traces": "requests", "sessions": "conversations", "users": "users"}

_SYSTEM = (
    "You write the executive summary of a product analytics report about an AI "
    "agent's production traffic. Be concrete and useful to the team that owns the "
    "agent: what is happening, what is going wrong, what to do. Use only numbers "
    "that appear in the statistics, written exactly as given. Cite examples as #n. "
    "Never include personal data. Reply with JSON only."
)


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def model_list(value) -> list:
    """A list the model wrote, or nothing when it wrote something else."""
    return value if isinstance(value, list) else []


def _notable_text(note: dict) -> str:
    if note["kind"] == "rate":
        return (
            f"{_pct(note['value'])} {note['label']} vs {_pct(note['rest'])} elsewhere"
        )
    if note["metric"] == "latency_ms":
        return f"median latency {note['value'] / 1000:.1f}s vs {note['rest'] / 1000:.1f}s elsewhere"
    return f"{note['label']} {note['value']:.1f} vs {note['rest']:.1f} elsewhere"


def allowed_numbers(result: dict) -> set[str]:
    """Every number the report's statistics hold, as a claim may write it.
    Rates may also be written as percentages; other decimals (a cost of 0.42,
    a sentiment of 3.2) only as themselves, so they never vouch for "42"."""
    numbers: set[str] = set()

    def add(value, *, rate: bool = False):
        if value is None:
            return
        if isinstance(value, float):
            texts = [f"{value:.1f}", f"{value:.2f}", f"{round(value)}"]
            if rate:
                texts += [f"{value * 100:.0f}", f"{value * 100:.1f}"]
            for text in texts:
                numbers.add(text.rstrip("0").rstrip(".") if "." in text else text)
        else:
            numbers.add(str(value))

    def add_stats(stats: dict):
        for key in ("count", "users", "sessions", "turns"):
            add(stats.get(key))
        for key in (
            "share",
            "failure_rate",
            "unresolved_rate",
            "error_rate",
            "top_user_share",
        ):
            if stats.get(key) is not None:
                add(float(stats[key]), rate=True)
        for key in ("sentiment", "cost"):
            if stats.get(key) is not None:
                add(float(stats[key]))
        if stats.get("latency_ms"):
            add(stats["latency_ms"])
            add(stats["latency_ms"] / 1000)
        for bound in stats.get("share_ci") or []:
            add(float(bound), rate=True)
        for count in (stats.get("outcomes") or {}).values():
            add(count)
            if stats.get("count"):
                add(count / stats["count"], rate=True)
        for attribute in (stats.get("attributes") or {}).values():
            if attribute.get("rate") is not None:
                add(float(attribute["rate"]), rate=True)
            if attribute.get("mean") is not None:
                add(float(attribute["mean"]))
            add(attribute.get("n"))
            for top in attribute.get("top") or []:
                add(top.get("count"))

    totals = result["totals"]
    add_stats(totals)
    for key in ("population_size", "sample_size", "excluded", "failed"):
        add(result.get(key))
    for category in result["categories"]:
        add_stats(category["stats"])
        for note in category.get("notable", []):
            is_rate = note.get("kind") == "rate"
            add(float(note["value"]), rate=is_rate)
            add(float(note["rest"]), rate=is_rate)
            add(float(note["lift"]))
            if note["metric"] == "latency_ms":
                add(note["value"] / 1000)
                add(note["rest"] / 1000)
    return numbers


def without_citations(text: str) -> str:
    """Example citations (#3) are not claims, so their digits aren't checked."""
    return re.sub(r"#\d+", "", text)


# A number as a claim writes it: "2,300" is one number, not "2" and "300".
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def verified(text: str, numbers: set[str]) -> bool:
    for found in _NUMBER.findall(text):
        found = found.replace(",", "")
        normal = found.rstrip("0").rstrip(".") if "." in found else found
        if normal not in numbers and found not in numbers:
            return False
    return True


def unit_quote(unit: dict) -> dict | None:
    """The quote that best stands for a unit: the first that shows its
    failure, else its first."""
    evidence = [
        e
        for e in model_list((unit.get("facets") or {}).get("evidence"))
        if isinstance(e, dict) and isinstance(e.get("quote"), str) and e["quote"]
    ]
    showing = [e for e in evidence if str(e.get("supports", "")).startswith("fail")]
    return (showing or evidence or [None])[0]


def _cited_quotes(examples: list[dict], refs: list[int]) -> list[dict]:
    quotes = []
    for ref in refs:
        unit = examples[ref - 1]["unit"]
        quote = unit_quote(unit)
        if quote:
            quotes.append(
                {
                    "unit_id": unit["unit_id"],
                    "quote": quote["quote"],
                    "evidence_id": quote.get("evidence_id"),
                }
            )
    return quotes[:MAX_FINDING_QUOTES]


def pick_examples(
    units_by_category: dict[str, list[dict]], categories: list[dict]
) -> list[dict]:
    """Up to two examples from each category, notable categories first."""
    ordered = sorted(
        (c for c in categories if c["level"] == 2 or not c.get("has_children")),
        key=lambda c: (-len(c.get("notable", [])), -c["stats"]["count"]),
    )
    examples = []
    for category in ordered:
        members = [
            u for u in units_by_category.get(category["id"], []) if u.get("summary")
        ]
        members.sort(key=lambda u: u["facets"].get("sentiment") or 3)
        for unit in members[:EXAMPLES_PER_CATEGORY]:
            examples.append({"unit": unit, "category": category})
        if len(examples) >= 16:
            break
    return examples


def write_summary(
    *,
    question: str,
    unit: str,
    agent_description: str,
    result: dict,
    units_by_category: dict[str, list[dict]],
    spend: Spend,
) -> dict:
    noun = UNIT_WORDS.get(unit, "items")
    categories = result["categories"]
    tops = [c for c in categories if c["level"] == 1]
    lines = []
    for top in sorted(tops, key=lambda c: -c["stats"]["count"]):
        stats = top["stats"]
        line = (
            f"[{top['id']}] {top['name']}: {stats['count']} {noun} ({_pct(stats['share'])}), "
            f"{stats['users']} users. {top['description']}"
        )
        for note in top.get("notable", [])[:3]:
            line += f" Notable: {_notable_text(note)}."
        lines.append(line)
        for sub in sorted(
            (c for c in categories if c.get("parent_id") == top["id"]),
            key=lambda c: -c["stats"]["count"],
        ):
            sstats = sub["stats"]
            sline = (
                f"    [{sub['id']}] {sub['name']}: {sstats['count']} {noun} "
                f"({_pct(sstats['share'])}), {sstats['users']} users."
            )
            for note in sub.get("notable", [])[:2]:
                sline += f" Notable: {_notable_text(note)}."
            lines.append(sline)
    examples = pick_examples(units_by_category, categories)
    example_lines = []
    for i, e in enumerate(examples, start=1):
        line = f"#{i} [{e['category']['name']}] {e['unit']['summary']}"
        quote = unit_quote(e["unit"])
        if quote:
            words = " ".join(quote["quote"].split())[:EXAMPLE_QUOTE_CHARS]
            line += f' Quote: "{words}"'
        example_lines.append(line)
    totals = result["totals"]
    prompt = (
        f"Report question: {question}\nAgent: {agent_description or 'an AI application'}\n"
        f"Analysed {totals['count']} {noun} from {totals['users']} users "
        f"(sampled {result.get('sample_size')} of {result.get('population_size')}).\n\n"
        "Categories (id, name, size, notable differences vs everything else):\n"
        + "\n".join(lines)
        + "\n\nExamples:\n"
        + "\n".join(example_lines)
        + '\n\nReturn {"title": "the report question, phrased crisply", '
        f'"findings": [up to {MAX_POINTS} {{"headline": "at most 140 characters, leads '
        'with the number, names the mechanism", "detail": "one or two sentences on how it '
        'happens, citing examples as #n", "action": "imperative fix, at most 120 '
        'characters", "severity": "high|medium|low", "category_ids": ["..."]}}], '
        f'"highlights": [up to {MAX_HIGHLIGHTS} {{"ref": n, "title": "at most 90 '
        'characters, what happened", "reason": "why this example matters, one line"}}]}}. '
        "Lead with the biggest or most actionable finding. Be specific: name the task, tool, "
        "policy or behaviour, never 'some users had issues'. The quotes are copied from the "
        "conversations: claim only what the examples show."
    )
    raw = chat_json(
        _SYSTEM, prompt, tier=THINKING, max_tokens=2200, temperature=0.3, spend=spend
    )
    numbers = allowed_numbers(result)
    ids = {c["id"] for c in categories}
    points, findings, dropped = [], [], 0
    raw_points = (
        model_list(raw.get("findings") or raw.get("key_points"))
        if isinstance(raw, dict)
        else []
    )
    for point in raw_points:
        if not isinstance(point, dict):
            continue
        headline = " ".join(str(point.get("headline", "")).split())
        detail = " ".join(str(point.get("detail", "")).split())
        action = " ".join(str(point.get("action", "")).split())
        if not headline:  # older shape: one "text" field
            headline = " ".join(str(point.get("text", "")).split())
        text = " ".join(p for p in (headline, detail) if p)
        if not text:
            continue
        if not verified(without_citations(f"{text} {action}"), numbers):
            dropped += 1
            continue
        refs = sorted(
            {
                int(r)
                for r in re.findall(r"#(\d+)", f"{text} {action}")
                if 0 < int(r) <= len(examples)
            }
        )
        ref_items = [
            {"ref": r, "unit_id": examples[r - 1]["unit"]["unit_id"]} for r in refs
        ]
        category_ids = [
            str(c) for c in model_list(point.get("category_ids")) if str(c) in ids
        ]
        points.append({"text": text, "category_ids": category_ids, "refs": ref_items})
        findings.append(
            {
                "headline": re.sub(r"\s*\(?#\d+(?:,\s*#\d+)*\)?", "", headline).strip(),
                "detail": detail,
                "action": action,
                "severity": (
                    point.get("severity")
                    if point.get("severity") in ("high", "medium", "low")
                    else "medium"
                ),
                "category_ids": category_ids,
                "refs": ref_items,
                "evidence": _cited_quotes(examples, refs),
            }
        )
        if len(points) >= MAX_POINTS:
            break
    highlights = []
    for entry in model_list(raw.get("highlights")) if isinstance(raw, dict) else []:
        try:
            ref = int(entry.get("ref"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 0 < ref <= len(examples):
            continue
        example = examples[ref - 1]
        quote = unit_quote(example["unit"])
        highlights.append(
            {
                "ref": ref,
                "unit_id": example["unit"]["unit_id"],
                "category_id": example["category"]["id"],
                "category_name": example["category"]["name"],
                "summary": example["unit"]["summary"],
                "title": " ".join(str(entry.get("title", "")).split())[:120]
                or example["unit"]["summary"][:90],
                "reason": " ".join(str(entry.get("reason", "")).split())[:240],
                "quote": quote["quote"] if quote else "",
            }
        )
        if len(highlights) >= MAX_HIGHLIGHTS:
            break
    return {
        "title": (
            " ".join(str(raw.get("title", "")).split())[:200]
            if isinstance(raw, dict)
            else ""
        ),
        "key_points": points,
        "findings": findings,
        "highlights": highlights,
        "examples": [
            {
                "ref": i,
                "unit_id": e["unit"]["unit_id"],
                "category_id": e["category"]["id"],
            }
            for i, e in enumerate(examples, start=1)
        ],
        "dropped_points": dropped,
    }
