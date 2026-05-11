from typing import Literal, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, key_value_block, section
from ai_tools.registry import register_tool


class CreateEvalTemplateInput(PydanticBaseModel):
    name: str = Field(
        description=(
            "Name for the evaluation template. Must be lowercase alphanumeric "
            "with hyphens or underscores only (e.g. 'toxicity-check', 'is_indian_name')."
        ),
        min_length=1,
        max_length=255,
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        from model_hub.utils.eval_validators import validate_eval_name

        return validate_eval_name(v)

    description: Optional[str] = Field(
        default=None, description="Description of what this evaluation measures"
    )
    eval_type: Literal["llm", "code", "agent"] = Field(
        default="agent",
        description=(
            "Type of evaluation. Default 'agent' (recommended for this platform — "
            "uses an agent loop with tools like web search, knowledge bases, MCP "
            "connectors, and trace exploration). Use 'llm' (LLM-as-a-judge) only "
            "if the user explicitly requests a lightweight single-call judge. Use "
            "'code' only when the user explicitly wants custom Python/JavaScript "
            "evaluation logic."
        ),
    )
    instructions: Optional[str] = Field(
        default=None,
        description=(
            "Evaluation prompt / criteria. MUST include template variables "
            "using double curly braces (e.g. '{{input}}', '{{output}}'). "
            "The variables map to dataset columns at runtime. "
            "For agent evals, can also use auto-context variables like "
            "{{row}}, {{span}}, {{trace}} which are resolved automatically."
        ),
    )
    model: Optional[str] = Field(
        default="turing_large",
        description=(
            "Model for evaluation (not used for code evals). "
            "Built-in: 'turing_large' (default), 'turing_small', 'turing_flash'. "
            "External (needs configured API key): 'gpt-4o', 'claude-sonnet-4-6', 'gemini-2.5-pro', etc."
        ),
    )
    output_type: Literal["pass_fail", "percentage", "deterministic"] = Field(
        default="pass_fail",
        description=(
            "Output type: 'pass_fail' (binary Pass/Fail), 'percentage' (0-100 score), "
            "'deterministic' (custom choices with scores — requires choice_scores)."
        ),
    )
    pass_threshold: Optional[float] = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Score threshold for pass/fail determination (0.0-1.0, default 0.5).",
    )
    choice_scores: Optional[dict] = Field(
        default=None,
        description=(
            "Score per choice option. Required when output_type='deterministic'. "
            "Keys are choice labels, values are float scores (e.g. "
            "{'Excellent': 1.0, 'Good': 0.7, 'Poor': 0.3, 'Bad': 0.0})."
        ),
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Tags to categorize this evaluation template.",
    )
    check_internet: bool = Field(
        default=False,
        description="Whether the eval can access the internet during execution.",
    )
    template_format: Literal["mustache", "jinja"] = Field(
        default="mustache",
        description=(
            "Template variable format: 'mustache' (default, {{var}}) or "
            "'jinja' (supports {% for %}, {% if %}, filters)."
        ),
    )

    # ── Code eval fields ──
    code: Optional[str] = Field(
        default=None,
        description=(
            "Custom evaluation code. Required when eval_type='code'. "
            "Python or JavaScript code that receives inputs and returns a score."
        ),
    )
    code_language: Optional[Literal["python", "javascript"]] = Field(
        default=None,
        description="Code language: 'python' or 'javascript'. Required when eval_type='code'.",
    )

    # ── LLM eval fields ──
    messages: Optional[list[dict]] = Field(
        default=None,
        description=(
            "Message chain for LLM evals. List of {role, content} dicts. "
            "Example: [{'role': 'system', 'content': '...'}, {'role': 'user', 'content': '...'}]"
        ),
    )
    few_shot_examples: Optional[list[dict]] = Field(
        default=None,
        description=(
            "Reference datasets for few-shot calibration (LLM and agent evals). "
            "Pass dataset references: [{'id': 'uuid', 'name': 'name'}]. "
            "Use list_datasets to find IDs."
        ),
    )

    # ── Agent eval fields ──
    mode: Optional[Literal["auto", "agent", "quick"]] = Field(
        default=None,
        description=(
            "Agent eval mode: 'agent' (default, multi-turn with tools), "
            "'quick' (single-turn, fast), 'auto' (adapts to data complexity)."
        ),
    )
    tools: Optional[dict] = Field(
        default=None,
        description=(
            "Tool configuration for agent evals. "
            "Keys: 'internet' (bool, enable web search), "
            "'connectors' (list of MCP connector IDs for external tools). "
            "Example: {'internet': true, 'connectors': ['connector-uuid']}"
        ),
    )
    knowledge_bases: Optional[list[str]] = Field(
        default=None,
        description=(
            "Knowledge base IDs for agent evals. The agent can search these KBs "
            "during evaluation for fact-grounding. Pass a list of KB UUIDs."
        ),
    )
    data_injection: Optional[dict] = Field(
        default=None,
        description=(
            "Context injection for agent evals. Flags: 'variables_only' (default), "
            "'full_row', 'span_context', 'trace_context', 'session_context', 'call_context'. "
            "Auto-detected from {{row}}/{{span}}/{{trace}} in instructions."
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
    template_type: Optional[str] = Field(default=None, exclude=True)
    required_keys: Optional[list[str]] = Field(default=None, exclude=True)
    choices: Optional[dict] = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        # Normalize alternate field names
        _type_map = {"Futureagi": "agent", "Llm": "llm", "Function": "code"}
        if self.template_type and not self.eval_type:
            mapped = _type_map.get(self.template_type)
            if mapped:
                self.eval_type = mapped
        if self.criteria and not self.instructions:
            self.instructions = self.criteria
        if self.choices and not self.choice_scores and self.output_type == "deterministic":
            keys = list(self.choices.keys())
            n = len(keys)
            self.choice_scores = {k: round(1.0 - i / max(n - 1, 1), 2) for i, k in enumerate(keys)}

        # Validate
        if self.eval_type == "code" and not self.code:
            raise ValueError("'code' field is required when eval_type='code'.")

        # Validate: non-code evals need instructions (unless data_injection)
        if self.eval_type != "code" and not self.instructions:
            has_injection = (
                self.data_injection
                and (
                    self.data_injection.get("full_row")
                    or not self.data_injection.get("variables_only", True)
                )
            ) if self.data_injection else False
            if not has_injection:
                raise ValueError(
                    "Instructions are required. Include template variables like {{input}}, {{output}}."
                )

        # Validate: deterministic needs choice_scores
        if self.output_type == "deterministic" and not self.choice_scores:
            raise ValueError("choice_scores is required when output_type='deterministic'.")

        return self


@register_tool
class CreateEvalTemplateTool(BaseTool):
    name = "create_eval_template"
    description = (
        "Creates an evaluation template that can be run on datasets, prompts, or traces. "
        "Three eval types are supported:\n"
        "- **llm** (LLM-as-a-Judge): Provide instructions with {{variable}} placeholders. "
        "An LLM evaluates each row by substituting variables with column values. "
        "Best for subjective quality, relevance, safety, and semantic checks. "
        "Supports few_shot_examples and messages for multi-turn prompting.\n"
        "- **code**: Provide Python or JavaScript code with an evaluate() function that "
        "receives kwargs and returns True/False, a 0-1 score, or {result, reason}. "
        "Runs in a sandbox. Best for deterministic, rule-based checks (regex, format, exact match).\n"
        "- **agent**: Uses an AI agent that can call tools, search knowledge bases, and "
        "reason over multiple turns. Best for complex evaluations needing internet, "
        "knowledge base grounding, or multi-step analysis of traces/spans.\n\n"
        "Use list_eval_templates to see existing templates first."
    )
    category = "evaluations"
    input_model = CreateEvalTemplateInput

    def execute(
        self, params: CreateEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        import re
        import traceback

        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        # ── 1. Check name uniqueness ──
        if EvalTemplate.objects.filter(
            name=params.name,
            organization=context.organization,
            deleted=False,
        ).exists():
            return ToolResult.error(
                f"An eval template named '{params.name}' already exists in this organization.",
                error_code="VALIDATION_ERROR",
            )
        if EvalTemplate.no_workspace_objects.filter(
            name=params.name,
            owner=OwnerChoices.SYSTEM.value,
            deleted=False,
        ).exists():
            return ToolResult.error(
                f"A system eval template named '{params.name}' already exists. "
                "Choose a different name.",
                error_code="VALIDATION_ERROR",
            )

        # ── 2. Extract variables from instructions ──
        instructions = params.instructions or ""
        template_format = params.template_format or "mustache"

        _AUTO_CTX_ROOTS = {"row", "span", "trace", "session", "call"}
        _AUTO_CTX_ROOT_TO_FLAG = {
            "row": "full_row",
            "span": "span_context",
            "trace": "trace_context",
            "session": "session_context",
            "call": "call_context",
        }

        # Collect text from instructions + messages
        all_text = [instructions]
        if params.messages:
            for msg in params.messages:
                all_text.append(msg.get("content", ""))
        combined_text = "\n".join(t for t in all_text if t)

        if template_format == "jinja":
            try:
                from model_hub.utils.jinja_variables import extract_jinja_variables

                variables = []
                for t in all_text:
                    if t.strip():
                        variables.extend(extract_jinja_variables(t))
                variables = list(set(variables))
            except Exception:
                variables = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", combined_text)
                variables = [v.strip() for v in variables]
        else:
            variables = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", combined_text)
            variables = [v.strip() for v in variables]

        # Filter auto-context roots and detect data injection flags
        auto_flags: dict = {}
        filtered_vars = []
        for v in variables:
            head = v.split(".", 1)[0].strip()
            if head in _AUTO_CTX_ROOTS:
                auto_flags[_AUTO_CTX_ROOT_TO_FLAG[head]] = True
            else:
                filtered_vars.append(v)
        required_keys = list(set(filtered_vars))

        # ── 3. Build output values ──
        output_map = {
            "pass_fail": "Pass/Fail",
            "percentage": "score",
            "deterministic": "choices",
        }
        output_value = output_map.get(params.output_type, "Pass/Fail")

        # Build choices list
        if params.choice_scores:
            choices_list = list(params.choice_scores.keys())
            choices_map = {
                k: "pass" if v >= 0.7 else ("neutral" if v >= 0.3 else "fail")
                for k, v in params.choice_scores.items()
            }
        elif params.output_type == "pass_fail":
            choices_list = ["Passed", "Failed"]
            choices_map = {}
        else:
            choices_list = []
            choices_map = {}

        # ── 4. Build config per eval_type ──
        if params.eval_type == "code":
            config = {
                "output": output_value,
                "eval_type_id": "CustomCodeEval",
                "code": params.code,
                "language": params.code_language or "python",
                "required_keys": [],
                "custom_eval": True,
            }
            criteria = params.code or ""

        elif params.eval_type == "agent":
            # Merge auto-detected context flags with explicit data_injection
            merged_injection = dict(
                params.data_injection or {"variables_only": True}
            )
            if auto_flags:
                merged_injection.update(auto_flags)
                merged_injection.pop("variables_only", None)
                merged_injection.pop("variablesOnly", None)

            config = {
                "output": output_value,
                "eval_type_id": "AgentEvaluator",
                "required_keys": required_keys,
                "rule_prompt": instructions,
                "custom_eval": True,
                "check_internet": params.check_internet,
                "agent_mode": params.mode or "agent",
                "model": params.model,
                "tools": params.tools or {},
                "knowledge_bases": params.knowledge_bases or [],
                "data_injection": merged_injection,
                "summary": params.summary or {"type": "concise"},
                "instructions": instructions,
            }
            if choices_map:
                config["choices"] = choices_list
                config["choices_map"] = choices_map
                config["multi_choice"] = False
            if params.few_shot_examples:
                config["few_shot_examples"] = params.few_shot_examples
            criteria = instructions

        else:
            # LLM-as-a-judge (default)
            system_prompt = None
            if params.messages:
                sys_msgs = [m for m in params.messages if m.get("role") == "system"]
                if sys_msgs:
                    system_prompt = sys_msgs[0].get("content", "")

            config = {
                "output": output_value,
                "eval_type_id": "CustomPromptEvaluator",
                "required_keys": required_keys,
                "rule_prompt": instructions,
                "system_prompt": system_prompt,
                "custom_eval": True,
                "check_internet": params.check_internet,
            }
            if params.messages and len(params.messages) > 1:
                config["messages"] = params.messages
            if params.few_shot_examples:
                config["few_shot_examples"] = params.few_shot_examples
            if choices_map:
                config["choices"] = choices_list
                config["choices_map"] = choices_map
                config["multi_choice"] = False
            criteria = instructions

        # Store template_format in config
        config["template_format"] = template_format

        eval_tags = list(params.tags) if params.tags else []

        # ── 5. Create EvalTemplate ──
        try:
            template = EvalTemplate.objects.create(
                name=params.name,
                organization=context.organization,
                workspace=context.workspace,
                owner=OwnerChoices.USER.value,
                eval_type=params.eval_type,
                eval_tags=eval_tags,
                config=config,
                choices=choices_list,
                description=params.description or "",
                criteria=criteria,
                multi_choice=False,
                proxy_agi=True,
                visible_ui=True,
                model=params.model,
                output_type_normalized=params.output_type,
                pass_threshold=params.pass_threshold,
                choice_scores=params.choice_scores,
            )
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to create eval template: {str(e)}",
                error_code=code_from_exception(e),
            )

        # ── 6. Create initial version (V1) ──
        try:
            EvalTemplateVersion.objects.create_version(
                eval_template=template,
                prompt_messages=[],
                config_snapshot=config,
                criteria=criteria,
                model=params.model,
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
            )
        except Exception:
            pass  # Non-fatal — version creation is best-effort

        # ── 7. Build response ──
        eval_type_labels = {"llm": "LLM-as-a-Judge", "code": "Code", "agent": "Agent"}

        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Type", eval_type_labels.get(params.eval_type, params.eval_type)),
                ("Model", template.model or "—"),
                ("Output Type", params.output_type),
                ("Pass Threshold", str(params.pass_threshold)),
                ("Tags", ", ".join(eval_tags) if eval_tags else "—"),
                ("Created", format_datetime(template.created_at)),
            ]
        )

        content = section("Eval Template Created", info)

        if criteria:
            preview = criteria[:500] + ("..." if len(criteria) > 500 else "")
            label = "Code" if params.eval_type == "code" else "Instructions"
            content += f"\n\n### {label}\n\n{preview}"

        if required_keys:
            content += f"\n\n**Variables:** {', '.join(f'`{k}`' for k in required_keys)}"

        content += "\n\n_Use `test_eval_template` to validate the template before running evaluations._"

        return ToolResult(
            content=content,
            data={
                "id": str(template.id),
                "name": template.name,
                "eval_type": params.eval_type,
                "owner": template.owner,
                "config": template.config,
                "model": template.model,
                "output_type": params.output_type,
                "criteria": criteria,
                "eval_tags": eval_tags,
                "required_keys": required_keys,
            },
        )
