import json
import os
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, key_value_block, section
from ai_tools.registry import register_tool


DEFAULT_INSTRUCTIONS = (
    "Check whether {{output}} correctly and concisely answers {{input}}."
)
AUTO_CONTEXT_ROOTS = {"row", "span", "trace", "session", "call"}
AUTO_CONTEXT_ROOT_TO_FLAG = {
    "row": "full_row",
    "span": "span_context",
    "trace": "trace_context",
    "session": "session_context",
    "call": "call_context",
}


def _canonical_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", (value or "").strip().lower()).strip("_-")


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return value


def _normalize_eval_type(value: Any) -> Any:
    aliases = {
        "futureagi": "agent",
        "future_agi": "agent",
        "agent": "agent",
        "llm": "llm",
        "llm-as-a-judge": "llm",
        "function": "code",
        "code": "code",
    }
    if isinstance(value, str):
        return aliases.get(value.strip().lower(), value)
    return value


def _normalize_output_type(value: Any) -> Any:
    aliases = {
        "pass/fail": "pass_fail",
        "pass_fail": "pass_fail",
        "pass-fail": "pass_fail",
        "binary": "pass_fail",
        "score": "percentage",
        "percentage": "percentage",
        "percent": "percentage",
        "choices": "deterministic",
        "choice": "deterministic",
        "deterministic": "deterministic",
    }
    if isinstance(value, str):
        return aliases.get(value.strip().lower(), value)
    return value


def _coerce_tags(value: Any) -> list[str] | None:
    parsed = _parse_jsonish(value)
    if parsed is None:
        return None
    if isinstance(parsed, list):
        return [str(tag).strip() for tag in parsed if str(tag).strip()]
    if isinstance(parsed, str):
        stripped = parsed.strip()
        return [stripped] if stripped else None
    return None


def _coerce_choices(value: Any) -> dict | None:
    parsed = _parse_jsonish(value)
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        return {str(k): v for k, v in parsed.items()}
    if isinstance(parsed, list):
        return {str(choice): str(choice) for choice in parsed}
    if isinstance(parsed, str) and parsed.strip():
        return {parsed.strip(): parsed.strip()}
    return None


def _extract_template_variables(texts: list[str], template_format: str) -> list[str]:
    if template_format == "jinja":
        try:
            from model_hub.utils.jinja_variables import extract_jinja_variables

            variables = []
            for text in texts:
                if text.strip():
                    variables.extend(extract_jinja_variables(text))
            return list(dict.fromkeys(str(v).strip() for v in variables if str(v).strip()))
        except Exception:
            pass

    combined_text = "\n".join(text for text in texts if text)
    variables = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", combined_text)
    return list(dict.fromkeys(v.strip() for v in variables if v.strip()))


class CreateEvalTemplateInput(PydanticBaseModel):
    name: str = Field(
        default="falcon_correctness_eval",
        description=(
            "Name for the evaluation template. Must be lowercase alphanumeric "
            "with hyphens or underscores only (e.g. 'toxicity-check', 'is_indian_name')."
        ),
        min_length=1,
        max_length=255,
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, value: str) -> str:
        from model_hub.utils.eval_validators import validate_eval_name

        normalized = _canonical_name(value) or "falcon_correctness_eval"
        return validate_eval_name(normalized)

    description: Optional[str] = Field(
        default=None, description="Description of what this evaluation measures"
    )
    eval_type: Literal["llm", "code", "agent"] = Field(
        default="llm",
        description=(
            "Type of evaluation: 'llm' (LLM-as-a-judge), 'code' "
            "(custom Python/JavaScript code), or 'agent' (Falcon AI powered)."
        ),
    )
    instructions: Optional[str] = Field(
        default=None,
        description=(
            "Evaluation prompt / criteria. Include template variables using double "
            "curly braces, e.g. '{{input}}' and '{{output}}'."
        ),
    )
    model: Optional[str] = Field(
        default="turing_large",
        description=(
            "Model for evaluation. Built-in: 'turing_large', 'turing_small', "
            "'turing_flash'. External models require configured credentials."
        ),
    )
    output_type: Literal["pass_fail", "percentage", "deterministic"] = Field(
        default="pass_fail",
        description=(
            "Output type: 'pass_fail', 'percentage', or 'deterministic' "
            "(custom choices with scores)."
        ),
    )
    pass_threshold: Optional[float] = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Score threshold for pass/fail determination.",
    )
    choice_scores: Optional[dict] = Field(
        default=None,
        description=(
            "Score per choice option. Required when output_type='deterministic'."
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
        description="Template variable format: 'mustache' or 'jinja'.",
    )

    code: Optional[str] = Field(
        default=None,
        description="Custom evaluation code. Required when eval_type='code'.",
    )
    code_language: Optional[Literal["python", "javascript"]] = Field(
        default=None,
        description="Code language: 'python' or 'javascript'.",
    )
    messages: Optional[list[dict]] = Field(
        default=None,
        description="Message chain for LLM evals. List of {role, content} dicts.",
    )
    few_shot_examples: Optional[list[dict]] = Field(
        default=None,
        description="Reference datasets for few-shot calibration.",
    )
    mode: Optional[Literal["auto", "agent", "quick"]] = Field(
        default=None,
        description="Agent eval mode: 'agent', 'quick', or 'auto'.",
    )
    tools: Optional[dict] = Field(
        default=None,
        description="Tool configuration for agent evals.",
    )
    knowledge_bases: Optional[list[str]] = Field(
        default=None,
        description="Knowledge base IDs for agent evals.",
    )
    data_injection: Optional[dict] = Field(
        default=None,
        description="Context injection flags for agent evals.",
    )
    summary: Optional[dict] = Field(
        default=None,
        description="Explanation style for agent evals.",
    )

    # Legacy/alternate names accepted by Falcon recovery flows.
    criteria: Optional[str] = Field(default=None, exclude=True)
    template_type: Optional[str] = Field(default=None, exclude=True)
    required_keys: Optional[list[str]] = Field(default=None, exclude=True)
    choices: Optional[dict] = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        normalized = dict(values)

        if "criteria" in normalized and not normalized.get("instructions"):
            normalized["instructions"] = normalized.get("criteria")
        if "eval_tags" in normalized and not normalized.get("tags"):
            normalized["tags"] = normalized.get("eval_tags")

        if "template_type" in normalized and "eval_type" not in normalized:
            normalized["eval_type"] = _normalize_eval_type(normalized.get("template_type"))
        elif "eval_type" in normalized:
            normalized["eval_type"] = _normalize_eval_type(normalized.get("eval_type"))

        if "output_type" in normalized:
            normalized["output_type"] = _normalize_output_type(
                normalized.get("output_type")
            )
        elif "choices" in normalized or "choice_scores" in normalized:
            normalized["output_type"] = "deterministic"

        if "tags" in normalized:
            normalized["tags"] = _coerce_tags(normalized.get("tags"))
        if "choices" in normalized:
            normalized["choices"] = _coerce_choices(normalized.get("choices"))

        for key in ("choice_scores", "tools", "data_injection", "summary"):
            if key in normalized:
                normalized[key] = _parse_jsonish(normalized.get(key))

        return normalized

    @model_validator(mode="after")
    def normalize_and_validate(self):
        if self.criteria and not self.instructions:
            self.instructions = self.criteria

        if self.eval_type == "code":
            if not self.code:
                raise ValueError("'code' field is required when eval_type='code'.")
        else:
            if not self.instructions:
                self.instructions = DEFAULT_INSTRUCTIONS
            elif not re.search(r"\{\{\s*[^{}]+?\s*\}\}", self.instructions):
                self.instructions = (
                    self.instructions.rstrip()
                    + " Use {{input}} and {{output}}."
                )

        if self.choices and not self.choice_scores:
            keys = list(self.choices.keys())
            if keys:
                if self.output_type != "deterministic":
                    self.output_type = "deterministic"
                max_index = max(len(keys) - 1, 1)
                self.choice_scores = {
                    key: round(1.0 - index / max_index, 2)
                    for index, key in enumerate(keys)
                }

        if self.output_type == "deterministic" and not self.choice_scores:
            raise ValueError("choice_scores is required when output_type='deterministic'.")

        if self.choice_scores:
            normalized_scores = {}
            for key, value in self.choice_scores.items():
                try:
                    score = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"choice_scores['{key}'] must be a number."
                    ) from exc
                if score < 0 or score > 1:
                    raise ValueError(
                        f"choice_scores['{key}'] must be between 0 and 1."
                    )
                normalized_scores[str(key)] = score
            self.choice_scores = normalized_scores

        if self.pass_threshold is None:
            self.pass_threshold = 0.5

        if not self.required_keys and self.instructions:
            variables = _extract_template_variables(
                [self.instructions], self.template_format or "mustache"
            )
            self.required_keys = [
                var
                for var in variables
                if var.split(".", 1)[0].strip() not in AUTO_CONTEXT_ROOTS
            ]

        return self


@register_tool
class CreateEvalTemplateTool(BaseTool):
    name = "create_eval_template"
    description = (
        "Creates an evaluation template that can be run on datasets, prompts, or traces. "
        "Supports llm, code, and agent evals. Use list_eval_templates to see existing "
        "templates first."
    )
    category = "evaluations"
    input_model = CreateEvalTemplateInput

    def execute(
        self, params: CreateEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        existing_template = EvalTemplate.objects.filter(
            name=params.name,
            organization=context.organization,
            deleted=False,
        ).first()
        if existing_template:
            return ToolResult(
                content=section(
                    "Eval Template Already Exists",
                    key_value_block(
                        [
                            ("ID", f"`{existing_template.id}`"),
                            ("Name", existing_template.name),
                            ("Owner", existing_template.owner),
                        ]
                    ),
                ),
                data={
                    "id": str(existing_template.id),
                    "name": existing_template.name,
                    "eval_type": existing_template.eval_type,
                    "already_exists": True,
                },
            )

        system_templates = EvalTemplate.no_workspace_objects.filter(
            owner=OwnerChoices.SYSTEM.value,
            deleted=False,
        )
        canonical_name = _canonical_name(params.name)
        canonical_system_match = any(
            _canonical_name(template.name) == canonical_name
            for template in system_templates.only("name")[:500]
        )
        if system_templates.filter(name=params.name).exists() or canonical_system_match:
            return ToolResult.error(
                f"A system eval template named '{params.name}' already exists. "
                "Choose a different name.",
                error_code="VALIDATION_ERROR",
            )

        instructions = params.instructions or ""
        template_format = params.template_format or "mustache"

        all_text = [instructions]
        if params.messages:
            for message in params.messages:
                all_text.append(str(message.get("content", "")))

        variables = _extract_template_variables(all_text, template_format)
        auto_flags: dict[str, bool] = {}
        filtered_vars = []
        for variable in variables:
            head = variable.split(".", 1)[0].strip()
            if head in AUTO_CONTEXT_ROOTS:
                auto_flags[AUTO_CONTEXT_ROOT_TO_FLAG[head]] = True
            else:
                filtered_vars.append(variable)

        required_keys = list(
            dict.fromkeys(list(params.required_keys or []) + filtered_vars)
        )

        output_map = {
            "pass_fail": "Pass/Fail",
            "percentage": "score",
            "deterministic": "choices",
        }
        output_value = output_map.get(params.output_type, "Pass/Fail")

        choices_list = []
        choices_map = {}
        if params.choice_scores:
            choices_list = list(params.choice_scores.keys())
            choices_map = {
                key: "pass" if score >= 0.7 else ("neutral" if score >= 0.3 else "fail")
                for key, score in params.choice_scores.items()
            }
        elif params.output_type == "pass_fail":
            choices_list = ["Passed", "Failed"]

        model = params.model or os.environ.get("FALCON_AI_MODEL") or "turing_large"

        if params.eval_type == "code":
            config = {
                "output": output_value,
                "eval_type_id": "CustomCodeEval",
                "code": params.code,
                "language": params.code_language or "python",
                "required_keys": required_keys,
                "custom_eval": True,
            }
            criteria = params.code or ""
        elif params.eval_type == "agent":
            merged_injection = dict(params.data_injection or {"variables_only": True})
            if auto_flags:
                merged_injection.update(auto_flags)
                merged_injection.pop("variables_only", None)
                merged_injection.pop("variablesOnly", None)

            tools_config = dict(params.tools or {})
            if params.check_internet and "internet" not in tools_config:
                tools_config["internet"] = True

            config = {
                "output": output_value,
                "eval_type_id": "AgentEvaluator",
                "required_keys": required_keys,
                "rule_prompt": instructions,
                "custom_eval": True,
                "check_internet": params.check_internet,
                "agent_mode": params.mode or "agent",
                "model": model,
                "tools": tools_config,
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
            system_prompt = None
            if params.messages:
                system_messages = [
                    msg for msg in params.messages if msg.get("role") == "system"
                ]
                if system_messages:
                    system_prompt = system_messages[0].get("content", "")

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

        config["template_format"] = template_format
        eval_tags = list(params.tags) if params.tags else []

        try:
            template = EvalTemplate.objects.create(
                name=params.name,
                description=params.description or "",
                organization=context.organization,
                workspace=context.workspace,
                owner=OwnerChoices.USER.value,
                eval_type=params.eval_type,
                eval_tags=eval_tags,
                config=config,
                choices=choices_list,
                criteria=criteria,
                multi_choice=False,
                proxy_agi=True,
                visible_ui=True,
                model=model if params.eval_type != "code" else params.model,
                output_type_normalized=params.output_type,
                pass_threshold=params.pass_threshold,
                choice_scores=params.choice_scores,
            )
        except Exception as exc:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to create eval template: {str(exc)}",
                error_code=code_from_exception(exc),
            )

        try:
            EvalTemplateVersion.objects.create_version(
                eval_template=template,
                prompt_messages=params.messages or [],
                config_snapshot=config,
                criteria=criteria,
                model=model if params.eval_type != "code" else params.model,
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
            )
        except Exception:
            pass

        eval_type_labels = {"llm": "LLM-as-a-Judge", "code": "Code", "agent": "Agent"}
        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Type", eval_type_labels.get(params.eval_type, params.eval_type)),
                ("Model", template.model or "-"),
                ("Output Type", params.output_type),
                ("Pass Threshold", str(params.pass_threshold)),
                ("Tags", ", ".join(eval_tags) if eval_tags else "-"),
                ("Created", format_datetime(template.created_at)),
            ]
        )

        content = section("Eval Template Created", info)
        if criteria:
            preview = criteria[:500] + ("..." if len(criteria) > 500 else "")
            label = "Code" if params.eval_type == "code" else "Instructions"
            content += f"\n\n### {label}\n\n{preview}"
        if required_keys:
            content += (
                "\n\n**Variables:** "
                + ", ".join(f"`{key}`" for key in required_keys)
            )
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
