from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section, truncate
from ai_tools.registry import register_tool


class GetEvalCodeSnippetInput(PydanticBaseModel):
    eval_template_id: str = Field(
        default="",
        description="Eval template name or UUID. If omitted, candidate templates are returned.",
    )
    language: str = Field(
        default="python",
        description="Programming language: 'python', 'javascript', or 'curl'",
    )


@register_tool
class GetEvalCodeSnippetTool(BaseTool):
    name = "get_eval_code_snippet"
    description = (
        "Returns a ready-to-use code snippet for running an evaluation template. "
        "Supports Python (futureagi SDK), JavaScript (fetch), and cURL. "
        "The snippet includes the template's required input keys."
    )
    category = "evaluations"
    input_model = GetEvalCodeSnippetInput

    def execute(
        self, params: GetEvalCodeSnippetInput, context: ToolContext
    ) -> ToolResult:

        from django.db.models import Q

        from ai_tools.resolvers import is_uuid
        from model_hub.models.evals_metric import EvalTemplate

        if not params.eval_template_id:
            templates = list(
                EvalTemplate.no_workspace_objects.filter(
                    Q(organization=context.organization) | Q(organization__isnull=True),
                    deleted=False,
                ).order_by("-created_at")[:10]
            )
            rows = [
                [
                    f"`{template.id}`",
                    truncate(template.name, 40),
                    template.owner or "-",
                ]
                for template in templates
            ]
            return ToolResult(
                content=section(
                    "Eval Template Candidates",
                    markdown_table(["ID", "Name", "Owner"], rows)
                    if rows
                    else "No eval templates found.",
                ),
                data={
                    "requires_eval_template_id": True,
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ],
                },
            )

        template_ref = params.eval_template_id.strip()
        try:
            query = Q(deleted=False) & (
                Q(organization=context.organization) | Q(organization__isnull=True)
            )
            if is_uuid(template_ref):
                query &= Q(id=template_ref)
            else:
                query &= Q(name__iexact=template_ref)
            template = EvalTemplate.no_workspace_objects.get(query)
        except EvalTemplate.DoesNotExist:
            return ToolResult.not_found("Eval Template", template_ref)
        except (ValueError, TypeError):
            return ToolResult.not_found("Eval Template", template_ref)

        config = template.config or {}
        required_keys = (
            config.get("required_keys", []) if isinstance(config, dict) else []
        )
        optional_keys = (
            config.get("optional_keys", []) if isinstance(config, dict) else []
        )

        # Build input placeholder
        all_keys = required_keys + optional_keys
        input_dict_str = (
            ", ".join(f'"{k}": "<your_{k}_here>"' for k in all_keys)
            if all_keys
            else '"response": "<your_response_here>"'
        )

        template_name = template.name
        model_str = f'model="{template.model}"' if template.model else ""
        language = params.language.lower()

        if language == "python":
            snippet = f"""# pip install futureagi ai-evaluation
from fi.evals import Evaluator

evaluator = Evaluator(
    fi_api_key="<YOUR_API_KEY>",
    fi_secret_key="<YOUR_SECRET_KEY>"
)

result = evaluator.evaluate(
    eval_templates="{template_name}",
    inputs={{{input_dict_str}}},
    {model_str}
)

print(result.eval_results[0].output)
print(result.eval_results[0].reason)"""

        elif language in ("javascript", "js"):
            body_dict = f'{{"eval_templates": ["{template_name}"], "inputs": {{{input_dict_str}}}}}'
            snippet = f"""const response = await fetch(
  "https://api.futureagi.com/model-hub/eval-playground/",
  {{
    method: "POST",
    headers: {{
      "X-Api-Key": "<YOUR_API_KEY>",
      "X-Secret-Key": "<YOUR_SECRET_KEY>",
      "Content-Type": "application/json"
    }},
    body: JSON.stringify({{
      eval_templates: ["{template_name}"],
      inputs: {{{input_dict_str}}}
    }})
  }}
);

const result = await response.json();
console.log(result.eval_results[0].output);
console.log(result.eval_results[0].reason);"""

        elif language == "curl":
            snippet = f"""curl 'https://api.futureagi.com/model-hub/eval-playground/' \\
  -H 'X-Api-Key: <YOUR_API_KEY>' \\
  -H 'X-Secret-Key: <YOUR_SECRET_KEY>' \\
  -H 'Content-Type: application/json' \\
  --data-raw '{{"eval_templates": ["{template_name}"], "inputs": {{{input_dict_str}}}}}\'"""

        else:
            return ToolResult.error(
                f"Unsupported language '{language}'. Use 'python', 'javascript', or 'curl'.",
                error_code="VALIDATION_ERROR",
            )

        info = key_value_block(
            [
                ("Template", template.name),
                ("Language", language),
                (
                    "Required Keys",
                    (
                        ", ".join(f"`{k}`" for k in required_keys)
                        if required_keys
                        else "—"
                    ),
                ),
            ]
        )

        content = section(f"Code Snippet: {template.name}", info)
        content += f"\n\n```{language}\n{snippet}\n```"
        content += "\n\n_Replace `<YOUR_API_KEY>` and `<YOUR_SECRET_KEY>` with your actual credentials._"

        return ToolResult(
            content=content,
            data={
                "template_id": str(template.id),
                "template_name": template.name,
                "language": language,
                "snippet": snippet,
                "required_keys": required_keys,
            },
        )
