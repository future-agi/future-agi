from typing import Literal, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, section
from ai_tools.registry import register_tool


class UpdateEvalTemplateInput(PydanticBaseModel):
    eval_template_id: str = Field(
        description="Name or UUID of the eval template to update"
    )

    # ── Core fields ──
    name: Optional[str] = Field(default=None, description="Name for the template")
    description: Optional[str] = Field(default=None, description="Description")
    eval_type: Optional[Literal["llm", "code", "agent"]] = Field(
        default=None,
        description="Change eval type: 'llm', 'code', or 'agent'.",
    )
    instructions: Optional[str] = Field(
        default=None,
        description=(
            "Evaluation instructions/criteria. Must include {{variable}} "
            "placeholders for non-code evals."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "Model for evaluation. Prefer turing models (no extra setup): "
            "'turing_large' (recommended), 'turing_small', 'turing_flash'. "
            "External models require configured API key: "
            "'gpt-4o', 'claude-sonnet-4-6', 'gemini-2.5-pro', etc."
        ),
    )
    output_type: Optional[Literal["pass_fail", "percentage", "deterministic"]] = Field(
        default=None,
        description=(
            "Output type: 'pass_fail' (binary Pass/Fail), "
            "'percentage' (0-1 numeric score), "
            "'deterministic' (custom choices with scores — requires choice_scores)."
        ),
    )
    pass_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Score threshold for pass/fail (0.0-1.0, default 0.5). "
            "For percentage output: score >= threshold = Pass. "
            "For deterministic: choice_score >= threshold = Pass."
        ),
    )
    choice_scores: Optional[dict] = Field(
        default=None,
        description=(
            "Choice scores. Required when output_type='deterministic'. "
            "Keys are choice labels, values are float scores."
        ),
    )
    tags: Optional[list[str]] = Field(
        default=None, description="tags for the template"
    )
    check_internet: Optional[bool] = Field(
        default=None,
        description="Whether the eval can access the internet.",
    )
    template_format: Optional[Literal["mustache", "jinja"]] = Field(
        default=None,
        description="Template variable format: 'mustache' or 'jinja'.",
    )
    error_localizer_enabled: Optional[bool] = Field(
        default=None,
        description="Enable/disable error localizer for this eval.",
    )
    publish: Optional[bool] = Field(
        default=None,
        description="Set to true to publish a draft eval (make it visible in listings).",
    )

    # ── Code eval fields ──
    code: Optional[str] = Field(
        default=None,
        description="evaluation code (for code-type evals).",
    )
    code_language: Optional[Literal["python", "javascript"]] = Field(
        default=None,
        description="Code language: 'python' or 'javascript'.",
    )

    # ── LLM eval fields ──
    messages: Optional[list[dict]] = Field(
        default=None,
        description="message chain for LLM evals: [{role, content}].",
    )
    few_shot_examples: Optional[list[dict]] = Field(
        default=None,
        description="few-shot examples: [{input, output, score}].",
    )

    # ── Agent eval fields ──
    mode: Optional[Literal["auto", "agent", "quick"]] = Field(
        default=None,
        description=(
            "Agent evaluation mode: "
            "'agent' (multi-turn reasoning, up to 15 iterations, full tool access), "
            "'quick' (single-turn, fast), "
            "'auto' (auto-selects based on data complexity)."
        ),
    )
    tools: Optional[dict] = Field(
        default=None,
        description=(
            "Tool configuration for agent evals: "
            "{'internet': bool, 'connectors': ['connector-uuid']}."
        ),
    )
    knowledge_bases: Optional[list[str]] = Field(
        default=None,
        description="Knowledge base UUIDs for agent evals to search during evaluation.",
    )
    data_injection: Optional[dict] = Field(
        default=None,
        description=(
            "Context injection for agent evals. Flags: "
            "'variables_only' (default true), 'full_row', 'span_context', "
            "'trace_context', 'session_context', 'call_context'."
        ),
    )
    summary: Optional[dict] = Field(
        default=None,
        description=(
            "Explanation style for agent evals: "
            "{'type': 'concise'} (default), 'short', 'long', or "
            "{'type': 'custom', 'custom_instructions': '...'}."
        ),
    )

    # Aliases for alternate field names (excluded from schema sent to LLM).
    criteria: Optional[str] = Field(default=None, exclude=True)
    eval_tags: Optional[list[str]] = Field(default=None, exclude=True)
    choices_map: Optional[dict] = Field(default=None, exclude=True)


@register_tool
class UpdateEvalTemplateTool(BaseTool):
    name = "update_eval_template"
    description = (
        "Updates a user-owned evaluation template. "
        "Only USER-owned templates can be updated (system templates are read-only). "
        "Provide only the fields you want to change. "
        "Can update instructions, model, output type, scoring thresholds, "
        "code (for code evals), agent config (mode, tools, knowledge bases), "
        "LLM config (messages, few-shot examples), and template format."
    )
    category = "evaluations"
    input_model = UpdateEvalTemplateInput

    def execute(
        self, params: UpdateEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        import re

        from django.utils import timezone

        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        # Normalize alternate field names
        if params.criteria and not params.instructions:
            params.instructions = params.criteria
        if params.eval_tags and not params.tags:
            params.tags = params.eval_tags

        from ai_tools.resolvers import resolve_eval_template

        template, err = resolve_eval_template(
            params.eval_template_id, context.organization
        )
        if err:
            return ToolResult.error(err, error_code="NOT_FOUND")
        if template.owner != OwnerChoices.USER.value:
            return ToolResult.error(
                "Only user-owned templates can be updated.",
                error_code="VALIDATION_ERROR",
            )

        changed_fields = []

        # ── Name ──
        if params.name is not None:
            clean_name = params.name.strip()
            if not re.match(r"^[a-z0-9_-]+$", clean_name):
                return ToolResult.error(
                    "Name can only contain lowercase letters, numbers, hyphens, or underscores.",
                    error_code="VALIDATION_ERROR",
                )
            if clean_name[0] in "-_" or clean_name[-1] in "-_":
                return ToolResult.error(
                    "Name cannot start or end with hyphens or underscores.",
                    error_code="VALIDATION_ERROR",
                )
            if "_-" in clean_name or "-_" in clean_name:
                return ToolResult.error(
                    "Name cannot contain consecutive mixed separators.",
                    error_code="VALIDATION_ERROR",
                )
            if (
                EvalTemplate.objects.filter(
                    name=clean_name,
                    organization=context.organization,
                    deleted=False,
                )
                .exclude(id=params.eval_template_id)
                .exists()
            ):
                return ToolResult.error(
                    f"An eval template named '{clean_name}' already exists.",
                    error_code="VALIDATION_ERROR",
                )
            template.name = clean_name
            changed_fields.append("name")

        # ── Description ──
        if params.description is not None:
            template.description = params.description
            changed_fields.append("description")

        # ── Tags ──
        if params.tags is not None:
            template.eval_tags = params.tags
            changed_fields.append("tags")

        # ── Template format ──
        template_format = params.template_format or (template.config or {}).get(
            "template_format", "mustache"
        )

        # ── Instructions ──
        if params.instructions is not None:
            if template.config is None:
                template.config = {}

            # Don't overwrite code with instructions for code evals
            if template.config.get("eval_type_id") != "CustomCodeEval":
                template.criteria = params.instructions

            # Extract variables and auto-context roots
            _AUTO_CTX_ROOTS = {"row", "span", "trace", "session", "call"}
            _AUTO_CTX_ROOT_TO_FLAG = {
                "row": "full_row",
                "span": "span_context",
                "trace": "trace_context",
                "session": "session_context",
                "call": "call_context",
            }

            all_text = [params.instructions or ""]
            msgs = (
                params.messages
                if params.messages
                else (template.config or {}).get("messages", [])
            )
            if msgs:
                for msg in msgs:
                    all_text.append(msg.get("content", "") if isinstance(msg, dict) else "")
            combined = "\n".join(t for t in all_text if t)

            if template_format == "jinja":
                try:
                    from model_hub.utils.jinja_variables import extract_jinja_variables

                    raw_vars = []
                    for t in all_text:
                        if t.strip():
                            raw_vars.extend(extract_jinja_variables(t))
                    raw_vars = list(set(raw_vars))
                except Exception:
                    raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", combined)
                    raw_vars = [v.strip() for v in raw_vars]
            else:
                raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", combined)
                raw_vars = [v.strip() for v in raw_vars]

            auto_flags = {}
            filtered = []
            for v in raw_vars:
                head = v.split(".", 1)[0].strip()
                if head in _AUTO_CTX_ROOTS:
                    auto_flags[_AUTO_CTX_ROOT_TO_FLAG[head]] = True
                else:
                    filtered.append(v)

            template.config["required_keys"] = list(set(filtered))
            template.config["rule_prompt"] = params.instructions
            if auto_flags:
                di = template.config.get("data_injection") or {}
                di.update(auto_flags)
                di.pop("variables_only", None)
                di.pop("variablesOnly", None)
                template.config["data_injection"] = di

            changed_fields.append("instructions")

        # ── Model ──
        if params.model is not None:
            template.model = params.model
            changed_fields.append("model")

        # ── Output type ──
        if params.output_type is not None:
            template.output_type_normalized = params.output_type
            output_map = {
                "pass_fail": "Pass/Fail",
                "percentage": "score",
                "deterministic": "choices",
            }
            if template.config is None:
                template.config = {}
            template.config["output"] = output_map.get(params.output_type, "Pass/Fail")
            changed_fields.append("output_type")

        # ── Pass threshold ──
        if params.pass_threshold is not None:
            template.pass_threshold = params.pass_threshold
            changed_fields.append("pass_threshold")

        # ── Choice scores ──
        if params.choice_scores is not None:
            template.choice_scores = params.choice_scores
            template.choices = list(params.choice_scores.keys())
            if template.config is None:
                template.config = {}
            template.config["choices"] = list(params.choice_scores.keys())
            template.config["choices_map"] = {
                k: "pass" if v >= 0.7 else ("neutral" if v >= 0.3 else "fail")
                for k, v in params.choice_scores.items()
            }
            changed_fields.append("choice_scores")

        # ── Check internet ──
        if params.check_internet is not None:
            if template.config is None:
                template.config = {}
            template.config["check_internet"] = params.check_internet
            changed_fields.append("check_internet")

        # ── Code eval fields ──
        if params.code is not None:
            if template.config is None:
                template.config = {}
            template.config["code"] = params.code
            template.config["eval_type_id"] = "CustomCodeEval"
            template.eval_type = "code"
            template.criteria = params.code
            changed_fields.append("code")

        if params.code_language is not None:
            if template.config is None:
                template.config = {}
            template.config["language"] = params.code_language
            changed_fields.append("code_language")

        # ── LLM eval fields ──
        if params.messages is not None:
            if template.config is None:
                template.config = {}
            template.config["messages"] = params.messages
            changed_fields.append("messages")

        if params.few_shot_examples is not None:
            if template.config is None:
                template.config = {}
            template.config["few_shot_examples"] = params.few_shot_examples
            changed_fields.append("few_shot_examples")

        # ── Agent eval fields ──
        if params.mode is not None:
            if template.config is None:
                template.config = {}
            template.config["agent_mode"] = params.mode
            changed_fields.append("mode")

        if params.tools is not None:
            if template.config is None:
                template.config = {}
            template.config["tools"] = params.tools
            changed_fields.append("tools")

        if params.knowledge_bases is not None:
            if template.config is None:
                template.config = {}
            template.config["knowledge_bases"] = params.knowledge_bases
            changed_fields.append("knowledge_bases")

        if params.data_injection is not None:
            if template.config is None:
                template.config = {}
            template.config["data_injection"] = params.data_injection
            changed_fields.append("data_injection")

        if params.summary is not None:
            if template.config is None:
                template.config = {}
            template.config["summary"] = params.summary
            changed_fields.append("summary")

        # ── Eval type change ──
        if params.eval_type is not None:
            _EVAL_TYPE_ID_MAP = {
                "agent": "AgentEvaluator",
                "llm": "CustomPromptEvaluator",
                "code": "CustomCodeEval",
            }
            template.eval_type = params.eval_type
            if template.config is None:
                template.config = {}
            template.config["eval_type_id"] = _EVAL_TYPE_ID_MAP[params.eval_type]
            changed_fields.append("eval_type")

        # ── Error localizer ──
        if params.error_localizer_enabled is not None:
            template.error_localizer_enabled = params.error_localizer_enabled
            changed_fields.append("error_localizer_enabled")

        # ── Template format ──
        if template.config is None:
            template.config = {}
        template.config["template_format"] = template_format

        # ── Publish draft ──
        if params.publish:
            template.visible_ui = True
            changed_fields.append("published")

        if not changed_fields:
            return ToolResult.error(
                "No fields to update. Provide at least one field to change.",
                error_code="VALIDATION_ERROR",
            )

        # ── Save ──
        template.updated_at = timezone.now()
        template.save()

        # ── Create new version on update ──
        try:
            EvalTemplateVersion.objects.create_version(
                eval_template=template,
                prompt_messages=[],
                config_snapshot=template.config,
                criteria=template.criteria,
                model=template.model,
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
            )
        except Exception:
            pass  # Non-fatal

        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Updated Fields", ", ".join(changed_fields)),
            ]
        )

        return ToolResult(
            content=section("Eval Template Updated", info),
            data={"id": str(template.id), "name": template.name, "updated_fields": changed_fields},
        )
