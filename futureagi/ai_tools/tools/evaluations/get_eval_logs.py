
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class GetEvalLogsInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    eval_template_id: str = Field(
        default="",
        description="Name or UUID of the eval template to get logs for. If omitted, returns recent eval logs.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    search: str | None = Field(
        default=None,
        description="Search/filter logs by status or source",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["eval_template_id"] = (
            normalized.get("eval_template_id")
            or normalized.get("template_id")
            or normalized.get("eval_id")
            or normalized.get("evaluation_id")
            or normalized.get("source_id")
            or normalized.get("id")
            or normalized.get("name")
            or ""
        )
        return normalized


@register_tool
class GetEvalLogsTool(BaseTool):
    name = "get_eval_logs"
    description = (
        "Returns evaluation execution logs/history for a specific eval template. "
        "Shows API call logs including cost, status, timestamps, and token counts."
    )
    category = "evaluations"
    input_model = GetEvalLogsInput

    def execute(self, params: GetEvalLogsInput, context: ToolContext) -> ToolResult:
        from tfc.ee_gating import EEFeature, is_oss

        if is_oss():
            return ToolResult.feature_unavailable(EEFeature.AUDIT_LOGS.value)

        from django.db.models import Q

        from ai_tools.resolvers import is_uuid, resolve_eval_template
        from ee.usage.models.usage import APICallLog

        eval_ref = str(params.eval_template_id or "").strip()
        template = None
        source_label = "recent eval logs"

        if eval_ref:
            template, err = resolve_eval_template(eval_ref, context.organization)
            if err:
                if is_uuid(eval_ref):
                    fallback_qs = APICallLog.objects.filter(
                        Q(source_id=eval_ref)
                        | Q(reference_id=eval_ref)
                        | Q(log_id=eval_ref),
                        organization=context.organization,
                    ).order_by("-created_at")
                    if fallback_qs.exists():
                        qs = fallback_qs
                        source_label = f"source `{eval_ref}`"
                    else:
                        return _eval_template_candidates(context, err)
                else:
                    return _eval_template_candidates(context, err)
            else:
                source_label = template.name
                qs = APICallLog.objects.filter(
                    organization=context.organization,
                    source_id=str(template.id),
                ).order_by("-created_at")
        else:
            qs = (
                APICallLog.objects.filter(organization=context.organization)
                .filter(Q(source__icontains="eval") | Q(source_id__isnull=False))
                .order_by("-created_at")
            )

        if params.search:
            qs = qs.filter(
                Q(status__icontains=params.search) | Q(source__icontains=params.search)
            )

        total = qs.count()
        logs = qs[params.offset : params.offset + params.limit]

        if not logs:
            return ToolResult(
                content=section(
                    f"Eval Logs: {source_label}",
                    "_No execution logs found for this eval template._",
                ),
                data={"logs": [], "total": 0},
            )

        rows = []
        data_list = []
        for log in logs:
            rows.append(
                [
                    f"`{str(log.log_id)}`",
                    format_status(log.status),
                    format_number(log.cost, 6),
                    str(log.input_token_count or 0),
                    log.source or "—",
                    format_datetime(log.created_at),
                ]
            )
            data_list.append(
                {
                    "log_id": str(log.log_id),
                    "status": log.status,
                    "cost": str(log.cost),
                    "input_token_count": log.input_token_count,
                    "source": log.source,
                    "created_at": (
                        log.created_at.isoformat() if log.created_at else None
                    ),
                }
            )

        table = markdown_table(
            ["Log ID", "Status", "Cost", "Tokens", "Source", "Created"],
            rows,
        )

        showing = f"Showing {len(rows)} of {total}"
        content = section(
            f"Eval Logs: {source_label} ({total})", f"{showing}\n\n{table}"
        )

        if total > params.offset + params.limit:
            content += f"\n\n_Use offset={params.offset + params.limit} to see more._"

        return ToolResult(content=content, data={"logs": data_list, "total": total})


def _eval_template_candidates(context: ToolContext, detail: str) -> ToolResult:
    from django.db.models import Q
    from model_hub.models.evals_metric import EvalTemplate

    templates = list(
        EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True),
            deleted=False,
        ).order_by("-created_at")[:10]
    )
    rows = [
        [f"`{template.id}`", truncate(template.name, 48), template.owner or "—"]
        for template in templates
    ]
    body = detail
    body += "\n\n"
    body += (
        markdown_table(["ID", "Name", "Owner"], rows)
        if rows
        else "No eval templates found."
    )
    return ToolResult(
        content=section("Eval Template Candidates", body),
        data={
            "requires_eval_template_id": True,
            "templates": [
                {"id": str(template.id), "name": template.name}
                for template in templates
            ],
        },
    )
