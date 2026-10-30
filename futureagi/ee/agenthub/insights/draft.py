"""Guided report setup: read a few real units and draft the report config
(name, question, what the agent does, extra guidance and attributes), and
preview what the extractor produces on a few units before a full run."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ee.agenthub.insights import agent as insight_agent
from ee.agenthub.insights.facets import clean_attributes, extract, included, lens_for
from ee.agenthub.insights.llm import THINKING, Spend, chat_json
from ee.agenthub.insights.units import load_units

_SYSTEM = (
    "You help a product team set up an analytics report over their AI agent's "
    "production traffic. From a few real examples and their goal, you infer what "
    "the agent does and propose a precise report setup. Reply with JSON only."
)


def draft_config(
    *,
    project_id: str,
    unit: str,
    lens_key: str,
    goal: str,
    agent_description: str,
    sample_ids: list[str],
) -> dict:
    lens = lens_for(lens_key)
    loaded = load_units(unit, project_id, sample_ids[:5])
    examples = "\n\n---\n\n".join(t.content[:2500] for t in list(loaded.values())[:4])
    spend = Spend()
    prompt = (
        f"Unit of analysis: {unit}.\nLens: {lens.key} ({lens.title or 'custom'}).\n"
        f"The team's goal: {goal or lens.title}\n"
        f"What the team says the agent does: {agent_description or '(not given)'}\n\n"
        f"Examples:\n{examples or '(no readable examples)'}\n\n"
        'Return {"name": "short report name", "question": "the report question in '
        'plain words", "agent_description": "one sentence on what this agent does", '
        '"instructions": "one or two sentences of extra guidance for reading each '
        'unit (which signals matter, what to ignore)", "attributes": [2 to 4 '
        '{"key": "snake_case", "type": "boolean|number|string", "description": '
        '"what to extract", "filter_by": false}]}. Attributes must be measurable '
        "from a single unit and useful for comparing categories."
    )
    raw = chat_json(_SYSTEM, prompt, tier=THINKING, max_tokens=900, spend=spend)
    raw = raw if isinstance(raw, dict) else {}
    return {
        "name": " ".join(str(raw.get("name", "")).split())[:120]
        or lens.title
        or "Report",
        "question": " ".join(str(raw.get("question", "")).split())[:300]
        or goal
        or lens.title,
        "agent_description": " ".join(
            str(raw.get("agent_description") or agent_description).split()
        )[:400],
        "instructions": " ".join(str(raw.get("instructions", "")).split())[:600],
        # A filter decides which units the report reads, so it is the user's
        # choice: a drafted "only include when true" on a failures report
        # (resolved_by_agent) would leave out the failures it is meant to find.
        "attributes": [
            {**attribute, "filter_by": False}
            for attribute in clean_attributes(raw.get("attributes"))
        ],
        "examples_read": len(loaded),
        "cost_usd": spend.cost_usd,
    }


def preview(
    *,
    project_id: str,
    unit: str,
    lens_key: str,
    question: str,
    agent_description: str,
    instructions: str,
    attributes: list[dict],
    unit_ids: list[str],
    spend: Spend | None = None,
) -> list[dict]:
    loaded = load_units(unit, project_id, unit_ids[:3])
    attributes = clean_attributes(attributes)
    spend = spend if spend is not None else Spend()
    if insight_agent.enabled():
        context = insight_agent.run_context(
            {
                "lens": lens_key,
                "question": question,
                "agent_description": agent_description,
                "instructions": instructions,
                "attributes": attributes,
            },
            unit,
            "preview",
        )
        read = insight_agent.read_units(
            context,
            [(t.unit_id, t.content) for t in loaded.values()],
            attributes,
            spend,
        )
        out = []
        for text in loaded.values():
            facets = read.get(text.unit_id, {}).get("facets")
            if facets is None:
                continue
            out.append(
                {
                    "unit_id": text.unit_id,
                    "label": text.label,
                    "excerpt": text.excerpt,
                    "user_id": text.user_id,
                    "facets": facets,
                    "included": included(facets, lens_key, attributes),
                }
            )
        return out

    def one(text):
        facets = extract(
            unit,
            text.content,
            lens_key=lens_key,
            question=question,
            agent_description=agent_description,
            instructions=instructions,
            attributes=attributes,
            spend=spend,
            key=text.unit_id,
        )
        return {
            "unit_id": text.unit_id,
            "label": text.label,
            "excerpt": text.excerpt,
            "user_id": text.user_id,
            "facets": facets,
            "included": included(facets, lens_key, attributes),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(one, loaded.values()))
