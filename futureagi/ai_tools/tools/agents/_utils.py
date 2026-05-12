from typing import Any

from django.core.exceptions import ValidationError

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_datetime, markdown_table, section, truncate
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


def _agent_qs(context: ToolContext):
    from simulate.models.agent_definition import AgentDefinition

    return AgentDefinition.objects.filter(
        organization=context.organization,
        deleted=False,
    ).order_by("-created_at")


def candidate_agents_result(
    context: ToolContext,
    title: str = "Candidate Agents",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = _agent_qs(context)
    search = clean_ref(search)
    if search:
        qs = qs.filter(agent_name__icontains=search)
    agents = list(qs[:10])

    rows = [
        [
            truncate(agent.agent_name, 40),
            f"`{agent.id}`",
            agent.agent_type or "-",
            format_datetime(agent.created_at),
        ]
        for agent in agents
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "ID", "Type", "Created"],
            rows,
        )
    else:
        body = body or "No agents found in this workspace."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_agent_id": True,
            "agents": [
                {"id": str(agent.id), "name": agent.agent_name}
                for agent in agents
            ],
        },
    )


def resolve_agent(
    agent_ref: Any,
    context: ToolContext,
    title: str = "Candidate Agents",
) -> tuple[Any | None, ToolResult | None]:
    from simulate.models.agent_definition import AgentDefinition

    ref = clean_ref(agent_ref)
    if not ref:
        return None, candidate_agents_result(context, title)

    qs = _agent_qs(context)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None

        exact = qs.filter(agent_name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, candidate_agents_result(
                context,
                "Multiple Agents Matched",
                f"More than one agent matched `{ref}`. Use one of these IDs.",
                search=ref,
            )

        fuzzy = qs.filter(agent_name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (AgentDefinition.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_agents_result(
        context,
        "Agent Not Found",
        f"Agent `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )


def _run_test_qs(context: ToolContext):
    from simulate.models.run_test import RunTest

    return (
        RunTest.objects.select_related("agent_definition", "agent_version")
        .filter(organization=context.organization, deleted=False)
        .order_by("-created_at")
    )


def candidate_run_tests_result(
    context: ToolContext,
    title: str = "Candidate Agent Tests",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = _run_test_qs(context)
    search = clean_ref(search)
    if search:
        qs = qs.filter(name__icontains=search)
    run_tests = list(qs[:10])

    rows = []
    for run_test in run_tests:
        agent_name = (
            run_test.agent_definition.agent_name
            if run_test.agent_definition
            else "-"
        )
        rows.append(
            [
                truncate(run_test.name, 40),
                f"`{run_test.id}`",
                truncate(agent_name, 36),
                str(run_test.scenarios.filter(deleted=False).count()),
            ]
        )

    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Test", "ID", "Agent", "Scenarios"],
            rows,
        )
    else:
        body = body or "No agent tests found in this workspace."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_run_test_id": True,
            "run_tests": [
                {
                    "id": str(run_test.id),
                    "name": run_test.name,
                    "agent_id": (
                        str(run_test.agent_definition_id)
                        if run_test.agent_definition_id
                        else None
                    ),
                }
                for run_test in run_tests
            ],
        },
    )


def resolve_run_test(
    run_test_ref: Any,
    context: ToolContext,
    title: str = "Candidate Agent Tests",
) -> tuple[Any | None, ToolResult | None]:
    from simulate.models.run_test import RunTest

    ref = clean_ref(run_test_ref)
    if not ref:
        return None, candidate_run_tests_result(context, title)

    qs = _run_test_qs(context)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None

        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, candidate_run_tests_result(
                context,
                "Multiple Agent Tests Matched",
                f"More than one agent test matched `{ref}`. Use one of these IDs.",
                search=ref,
            )

        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (RunTest.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_run_tests_result(
        context,
        "Agent Test Not Found",
        f"Agent test `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )
