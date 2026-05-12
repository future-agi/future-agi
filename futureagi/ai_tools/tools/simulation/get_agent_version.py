from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetAgentVersionInput(PydanticBaseModel):
    agent_id: str = Field(
        default="",
        description="Agent name or UUID. If omitted, candidate agents are returned.",
    )
    version_id: str = Field(
        default="",
        description="Version UUID. If omitted, the active or latest version is returned.",
    )


@register_tool
class GetAgentVersionTool(BaseTool):
    name = "get_agent_version"
    description = (
        "Returns detailed information about a specific agent version, "
        "including score, pass rate, test count, status, and configuration snapshot."
    )
    category = "simulation"
    input_model = GetAgentVersionInput

    def _candidate_agents_result(
        self, context: ToolContext, message: str = ""
    ) -> ToolResult:
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion

        agents = list(
            AgentDefinition.objects.filter(organization=context.organization).order_by(
                "-created_at"
            )[:10]
        )
        rows = []
        data = []
        for agent in agents:
            latest = (
                AgentVersion.objects.filter(agent_definition=agent)
                .order_by("-version_number", "-created_at")
                .first()
            )
            rows.append(
                [
                    truncate(agent.agent_name, 40),
                    f"`{agent.id}`",
                    f"`{latest.id}`" if latest else "-",
                    latest.version_name if latest else "-",
                ]
            )
            data.append(
                {
                    "id": str(agent.id),
                    "name": agent.agent_name,
                    "latest_version_id": str(latest.id) if latest else None,
                }
            )
        return ToolResult(
            content=section(
                "Agent Version Candidates",
                (
                    (message + "\n\n" if message else "")
                    + (
                        markdown_table(
                            ["Agent", "Agent ID", "Latest Version ID", "Version"],
                            rows,
                        )
                        if rows
                        else "No agents found."
                    )
                ),
            ),
            data={"requires_agent_id": True, "agents": data},
        )

    def execute(self, params: GetAgentVersionInput, context: ToolContext) -> ToolResult:

        from django.db.models import Q
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion

        from ai_tools.resolvers import is_uuid

        if not params.agent_id:
            return self._candidate_agents_result(context)

        agent_ref = params.agent_id.strip()
        try:
            query = Q(organization=context.organization)
            if is_uuid(agent_ref):
                query &= Q(id=agent_ref)
            else:
                query &= Q(agent_name__iexact=agent_ref)
            agent = AgentDefinition.objects.get(query)
        except AgentDefinition.DoesNotExist:
            return self._candidate_agents_result(
                context, f"Agent `{agent_ref}` was not found in this workspace."
            )
        except (ValueError, TypeError):
            return self._candidate_agents_result(
                context, f"Agent `{agent_ref}` was not found in this workspace."
            )

        if params.version_id:
            version_ref = params.version_id.strip()
            try:
                version = AgentVersion.objects.get(
                    id=version_ref, agent_definition=agent
                )
            except AgentVersion.DoesNotExist:
                return ToolResult.not_found("Agent Version", version_ref)
            except (ValueError, TypeError):
                return ToolResult.not_found("Agent Version", version_ref)
        else:
            version = (
                AgentVersion.objects.filter(
                    agent_definition=agent,
                    status=AgentVersion.StatusChoices.ACTIVE,
                )
                .order_by("-version_number", "-created_at")
                .first()
            )
            if not version:
                version = (
                    AgentVersion.objects.filter(agent_definition=agent)
                    .order_by("-version_number", "-created_at")
                    .first()
                )
            if not version:
                return ToolResult(
                    content=section(
                        f"Agent Versions: {agent.agent_name}",
                        "No versions found for this agent.",
                    ),
                    data={"agent_id": str(agent.id), "versions": []},
                )

        score = format_number(version.score) if version.score is not None else "—"
        pass_rate = f"{version.pass_rate}%" if version.pass_rate is not None else "—"

        info = key_value_block(
            [
                ("Version ID", f"`{version.id}`"),
                ("Agent", agent.agent_name),
                ("Version", version.version_name),
                ("Version Number", str(version.version_number)),
                ("Status", format_status(version.status)),
                ("Score", score),
                ("Test Count", str(version.test_count)),
                ("Pass Rate", pass_rate),
                (
                    "Description",
                    truncate(version.description, 300) if version.description else "—",
                ),
                (
                    "Commit Message",
                    (
                        truncate(version.commit_message, 200)
                        if version.commit_message
                        else "—"
                    ),
                ),
                (
                    "Release Notes",
                    (
                        truncate(version.release_notes, 300)
                        if version.release_notes
                        else "—"
                    ),
                ),
                ("Is Active", "Yes" if version.is_active else "No"),
                ("Is Latest", "Yes" if version.is_latest else "No"),
                ("Created", format_datetime(version.created_at)),
            ]
        )

        content = section(f"Agent Version: {version.version_name}", info)

        # Configuration snapshot
        if version.configuration_snapshot:
            content += "\n\n### Configuration Snapshot\n\n"
            snapshot = version.configuration_snapshot
            snapshot_pairs = []
            for key, value in list(snapshot.items())[:15]:
                snapshot_pairs.append((key, truncate(str(value), 100)))
            content += key_value_block(snapshot_pairs)

        data = {
            "id": str(version.id),
            "agent_id": str(agent.id),
            "version_number": version.version_number,
            "version_name": version.version_name,
            "status": version.status,
            "score": float(version.score) if version.score is not None else None,
            "test_count": version.test_count,
            "pass_rate": (
                float(version.pass_rate) if version.pass_rate is not None else None
            ),
        }

        return ToolResult(content=content, data=data)
