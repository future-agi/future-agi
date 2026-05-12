from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_agent
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


def _candidate_versions_result(agent, title: str, detail: str = "") -> ToolResult:
    from simulate.models.agent_version import AgentVersion

    versions = list(
        AgentVersion.objects.filter(agent_definition=agent).order_by("-version_number")[
            :10
        ]
    )
    rows = [
        [
            f"`{version.id}`",
            version.version_name or f"v{version.version_number}",
            format_status(version.status),
            format_datetime(version.created_at),
        ]
        for version in versions
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["ID", "Version", "Status", "Created"],
            rows,
        )
    else:
        body = body or f"No versions found for `{agent.agent_name}`."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_version_ids": True,
            "agent_id": str(agent.id),
            "versions": [
                {
                    "id": str(version.id),
                    "version_name": version.version_name,
                    "version_number": version.version_number,
                }
                for version in versions
            ],
        },
    )


def _resolve_version(agent, version_ref):
    from simulate.models.agent_version import AgentVersion

    ref = clean_ref(version_ref)
    if not ref:
        return None
    qs = AgentVersion.objects.filter(agent_definition=agent)
    ref_uuid = uuid_text(ref)
    if ref_uuid:
        return qs.filter(id=ref_uuid).first()
    exact = qs.filter(version_name__iexact=ref)
    if exact.count() == 1:
        return exact.first()
    if ref.lower().startswith("v") and ref[1:].isdigit():
        return qs.filter(version_number=int(ref[1:])).first()
    if ref.isdigit():
        return qs.filter(version_number=int(ref)).first()
    return None


class CompareAgentVersionsInput(PydanticBaseModel):
    agent_id: str = Field(default="", description="Agent definition name or UUID")
    version_id_a: str = Field(
        default="", description="First version UUID, version name, or number"
    )
    version_id_b: str = Field(
        default="", description="Second version UUID, version name, or number"
    )


@register_tool
class CompareAgentVersionsTool(BaseTool):
    name = "compare_agent_versions"
    description = (
        "Compares two agent versions side by side, showing differences in "
        "metrics (score, pass rate, test count) and configuration."
    )
    category = "simulation"
    input_model = CompareAgentVersionsInput

    def execute(
        self, params: CompareAgentVersionsInput, context: ToolContext
    ) -> ToolResult:

        agent, unresolved = resolve_agent(
            params.agent_id,
            context,
            title="Agent Required For Version Comparison",
        )
        if unresolved:
            return unresolved

        version_a = _resolve_version(agent, params.version_id_a)
        version_b = _resolve_version(agent, params.version_id_b)
        if not version_a or not version_b:
            return _candidate_versions_result(
                agent,
                "Agent Versions Required",
                "Provide `version_id_a` and `version_id_b` from this agent.",
            )

        # Metrics comparison table
        rows = [
            [
                "Version Name",
                version_a.version_name or f"v{version_a.version_number}",
                version_b.version_name or f"v{version_b.version_number}",
            ],
            [
                "Status",
                format_status(version_a.status),
                format_status(version_b.status),
            ],
            [
                "Score",
                format_number(version_a.score) if version_a.score is not None else "—",
                format_number(version_b.score) if version_b.score is not None else "—",
            ],
            [
                "Test Count",
                str(version_a.test_count),
                str(version_b.test_count),
            ],
            [
                "Pass Rate",
                f"{version_a.pass_rate}%" if version_a.pass_rate is not None else "—",
                f"{version_b.pass_rate}%" if version_b.pass_rate is not None else "—",
            ],
        ]

        table = markdown_table(["Metric", "Version A", "Version B"], rows)

        content = section(
            f"Version Comparison: {agent.agent_name}",
            f"Comparing `{str(version_a.id)}` vs `{str(version_b.id)}`\n\n{table}",
        )

        # Configuration diff
        snap_a = version_a.configuration_snapshot or {}
        snap_b = version_b.configuration_snapshot or {}
        all_keys = sorted(set(list(snap_a.keys()) + list(snap_b.keys())))

        diff_rows = []
        for key in all_keys:
            val_a = snap_a.get(key)
            val_b = snap_b.get(key)
            if val_a != val_b:
                diff_rows.append(
                    [
                        key,
                        str(val_a) if val_a is not None else "—",
                        str(val_b) if val_b is not None else "—",
                    ]
                )

        if diff_rows:
            diff_table = markdown_table(["Field", "Version A", "Version B"], diff_rows)
            content += f"\n\n### Configuration Differences\n\n{diff_table}"
        else:
            content += "\n\n### Configuration Differences\n\n_No configuration differences found._"

        data = {
            "agent_id": str(agent.id),
            "version_a": {
                "id": str(version_a.id),
                "version_number": version_a.version_number,
                "score": (
                    float(version_a.score) if version_a.score is not None else None
                ),
                "test_count": version_a.test_count,
                "pass_rate": (
                    float(version_a.pass_rate)
                    if version_a.pass_rate is not None
                    else None
                ),
            },
            "version_b": {
                "id": str(version_b.id),
                "version_number": version_b.version_number,
                "score": (
                    float(version_b.score) if version_b.score is not None else None
                ),
                "test_count": version_b.test_count,
                "pass_rate": (
                    float(version_b.pass_rate)
                    if version_b.pass_rate is not None
                    else None
                ),
            },
        }

        return ToolResult(content=content, data=data)
