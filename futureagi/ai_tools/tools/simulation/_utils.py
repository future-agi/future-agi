from typing import Any

from django.core.exceptions import ValidationError

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section, truncate
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


def _scenario_qs(context: ToolContext, agent=None):
    from simulate.models.scenarios import Scenarios

    qs = Scenarios.objects.select_related("agent_definition").filter(
        organization=context.organization,
        deleted=False,
    )
    if agent is not None:
        qs = qs.filter(agent_definition=agent)
    return qs.order_by("-created_at")


def candidate_scenarios_result(
    context: ToolContext,
    title: str = "Candidate Scenarios",
    detail: str = "",
    search: str = "",
    agent=None,
) -> ToolResult:
    qs = _scenario_qs(context, agent=agent)
    search = clean_ref(search)
    if search:
        qs = qs.filter(name__icontains=search)
    scenarios = list(qs[:10])

    rows = []
    for scenario in scenarios:
        agent_name = (
            scenario.agent_definition.agent_name
            if scenario.agent_definition
            else "-"
        )
        rows.append(
            [
                truncate(scenario.name, 40),
                f"`{scenario.id}`",
                scenario.scenario_type or "-",
                truncate(agent_name, 36),
                format_datetime(scenario.created_at),
            ]
        )

    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "ID", "Type", "Agent", "Created"],
            rows,
        )
    else:
        body = body or "No scenarios found in this workspace."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_scenario_id": True,
            "scenarios": [
                {
                    "id": str(scenario.id),
                    "name": scenario.name,
                    "type": scenario.scenario_type,
                    "agent_id": (
                        str(scenario.agent_definition_id)
                        if scenario.agent_definition_id
                        else None
                    ),
                }
                for scenario in scenarios
            ],
        },
    )


def resolve_scenario(
    scenario_ref: Any,
    context: ToolContext,
    title: str = "Candidate Scenarios",
    agent=None,
) -> tuple[Any | None, ToolResult | None]:
    from simulate.models.scenarios import Scenarios

    ref = clean_ref(scenario_ref)
    if not ref:
        return None, candidate_scenarios_result(context, title, agent=agent)

    qs = _scenario_qs(context, agent=agent)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None

        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, candidate_scenarios_result(
                context,
                "Multiple Scenarios Matched",
                f"More than one scenario matched `{ref}`. Use one of these IDs.",
                search=ref,
                agent=agent,
            )

        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (Scenarios.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_scenarios_result(
        context,
        "Scenario Not Found",
        f"Scenario `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
        agent=agent,
    )


def resolve_scenarios(
    scenario_refs: Any,
    context: ToolContext,
    title: str = "Candidate Scenarios",
    agent=None,
) -> tuple[list[Any] | None, ToolResult | None]:
    refs = scenario_refs or []
    if isinstance(refs, (str, bytes)):
        refs = [refs]
    refs = list(refs)
    if not refs:
        return None, candidate_scenarios_result(context, title, agent=agent)

    scenarios = []
    seen_ids = set()
    for ref in refs:
        scenario, error = resolve_scenario(ref, context, title=title, agent=agent)
        if error:
            return None, error
        if scenario and scenario.id not in seen_ids:
            scenarios.append(scenario)
            seen_ids.add(scenario.id)
    return scenarios, None
