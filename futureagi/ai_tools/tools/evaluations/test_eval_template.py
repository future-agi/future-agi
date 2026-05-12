import json
import os
from typing import Any
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section, truncate
from ai_tools.registry import register_tool


class TestEvalTemplateInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    eval_template_id: str = Field(
        default="",
        description=(
            "Eval template name or UUID. If omitted, recent eval template "
            "candidates are returned."
        ),
    )
    mapping: dict | str = Field(
        default_factory=dict,
        description=(
            "Mapping of template variable names to test values. "
            'Example: {"input": "What is the capital of France?", '
            '"output": "Paris is the capital."}'
        ),
    )
    model: str | None = Field(
        default=None,
        description="Override model for the test run (optional).",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if not normalized.get("eval_template_id"):
            normalized["eval_template_id"] = (
                normalized.get("template_id")
                or normalized.get("id")
                or normalized.get("name")
                or ""
            )
        if not normalized.get("mapping"):
            extra_mapping = {
                key: value
                for key, value in normalized.items()
                if key
                not in {
                    "eval_template_id",
                    "template_id",
                    "id",
                    "name",
                    "mapping",
                    "model",
                }
            }
            normalized["mapping"] = (
                normalized.get("input_data")
                or normalized.get("test_data")
                or normalized.get("sample_data")
                or normalized.get("inputs")
                or extra_mapping
                or {}
            )
        return normalized


@register_tool
class TestEvalTemplateTool(BaseTool):
    name = "test_eval_template"
    description = (
        "Runs a dry-run test of an evaluation template with provided input data. "
        "Returns the evaluation result without persisting anything. Works with "
        "LLM, code, and agent eval types. Use this to validate template "
        "configuration before applying to datasets."
    )
    category = "evaluations"
    input_model = TestEvalTemplateInput

    def execute(
        self, params: TestEvalTemplateInput, context: ToolContext
    ) -> ToolResult:
        from django.db.models import Q
        from model_hub.models.evals_metric import EvalTemplate

        template_ref = self._clean_template_ref(params.eval_template_id or "")
        scope = Q(organization=context.organization) | Q(organization__isnull=True)

        if not template_ref:
            templates = list(
                EvalTemplate.no_workspace_objects.filter(scope).order_by(
                    "-created_at"
                )[:10]
            )
            return self._candidate_result(
                templates,
                "Provide `eval_template_id` and optional `mapping` to dry-run a template.",
            )

        template = self._resolve_template(EvalTemplate, scope, template_ref)
        if not template:
            candidates = list(
                EvalTemplate.no_workspace_objects.filter(
                    scope, name__icontains=template_ref
                ).order_by("-created_at")[:10]
            )
            if not candidates:
                candidates = list(
                    EvalTemplate.no_workspace_objects.filter(scope).order_by(
                        "-created_at"
                    )[:10]
                )
            return self._candidate_result(
                candidates,
                (
                    f"No exact eval template matched `{template_ref}`. Use one of "
                    "these IDs to dry-run a template."
                ),
            )

        config = template.config or {}
        required_keys = config.get("required_keys", []) if isinstance(config, dict) else []
        mapping = self._normalize_mapping(params.mapping, required_keys)
        for key in required_keys:
            mapping.setdefault(key, self._sample_value_for_key(key))

        rule_prompt = (
            config.get("rule_prompt")
            or config.get("criteria")
            or template.criteria
            or template.description
            or "Evaluate whether the output satisfies the requested task."
        )
        if "rule_prompt" in required_keys:
            mapping.setdefault("rule_prompt", rule_prompt)

        if getattr(template, "template_type", "") == "composite":
            return self._test_composite_template(
                template=template,
                mapping=mapping,
                params=params,
                context=context,
            )

        model = (
            params.model
            or os.environ.get("FALCON_AI_MODEL")
            or os.environ.get("TURING_SMALL_MODEL")
            or template.model
        )
        if model:
            # In-memory only. The eval runner reads the model from the saved
            # template in some branches, so override without persisting.
            template.model = model

        try:
            from model_hub.utils.function_eval_params import (
                has_function_params_schema,
                normalize_eval_runtime_config,
            )
            from model_hub.views.utils.evals import run_eval_func

            extra = getattr(params, "model_extra", {}) or {}
            runtime_config = self._normalize_runtime_config(extra)
            input_data_types = self._normalize_extra_dict(
                extra.get("input_data_types")
            )

            if (
                isinstance(config, dict)
                and config.get("eval_type_id") == "CustomCodeEval"
                and has_function_params_schema(config)
            ):
                runtime_config = normalize_eval_runtime_config(
                    config, runtime_config
                )

            response = run_eval_func(
                runtime_config,
                mapping,
                template,
                context.organization,
                model=model,
                error_localizer=bool(extra.get("error_localizer", False)),
                source="eval_playground",
                kb_id=extra.get("kb_id"),
                workspace=context.workspace,
                input_data_types=input_data_types,
                row_context=extra.get("row_context"),
                span_context=extra.get("span_context"),
                trace_context=extra.get("trace_context"),
                session_context=extra.get("session_context"),
                call_context=extra.get("call_context"),
            )

            result_info = []
            if isinstance(response, dict):
                for key, value in response.items():
                    result_info.append((key, truncate(str(value), 200)))
            else:
                result_info.append(("Result", truncate(str(response), 500)))

            eval_type_labels = {"llm": "LLM", "code": "Code", "agent": "Agent"}
            info = key_value_block(
                [
                    ("Template", f"{template.name} (`{str(template.id)}`)"),
                    (
                        "Type",
                        eval_type_labels.get(
                            template.eval_type or "llm",
                            template.eval_type or "-",
                        ),
                    ),
                    ("Model", model or "default"),
                ]
                + result_info
            )

            return ToolResult(
                content=section("Eval Template Test Result", info),
                data={
                    "template_id": str(template.id),
                    "eval_type": template.eval_type,
                    "result": response,
                    "test_completed": True,
                },
            )

        except Exception as exc:
            from ai_tools.error_codes import code_from_exception

            error_code = code_from_exception(exc)
            info = key_value_block(
                [
                    ("Template", f"{template.name} (`{str(template.id)}`)"),
                    ("Model", model or "template default"),
                    ("Status", "test did not complete"),
                    ("Reason", truncate(str(exc), 500)),
                    ("Error Code", error_code),
                ]
            )
            return ToolResult(
                content=section("Eval Template Test Did Not Complete", info),
                data={
                    "template_id": str(template.id),
                    "test_completed": False,
                    "error": str(exc),
                    "error_code": error_code,
                    "blocked_reason": "eval_runtime_unavailable",
                },
                status="blocked",
            )

    @staticmethod
    def _resolve_template(EvalTemplate, scope, template_ref: str):
        if TestEvalTemplateTool._looks_like_uuid(template_ref):
            try:
                return EvalTemplate.no_workspace_objects.get(scope, id=template_ref)
            except EvalTemplate.DoesNotExist:
                return None
        return EvalTemplate.no_workspace_objects.filter(
            scope,
            name__iexact=template_ref,
        ).first()

    @staticmethod
    def _test_composite_template(
        *,
        template,
        mapping: dict,
        params: TestEvalTemplateInput,
        context: ToolContext,
    ) -> ToolResult:
        from ai_tools.tools.evaluations.execute_composite_eval import (
            ExecuteCompositeEvalInput,
            ExecuteCompositeEvalTool,
        )

        extra = getattr(params, "model_extra", {}) or {}
        child_required_keys = TestEvalTemplateTool._composite_required_keys(template)
        mapping = dict(mapping)
        for key in child_required_keys:
            mapping.setdefault(key, TestEvalTemplateTool._sample_value_for_key(key))

        composite_params = ExecuteCompositeEvalInput.model_validate(
            {
                "composite_eval_id": str(template.id),
                "mapping": mapping,
                "model": params.model,
                "row_context": extra.get("row_context"),
                "span_context": extra.get("span_context"),
                "trace_context": extra.get("trace_context"),
            }
        )
        result = ExecuteCompositeEvalTool().execute(composite_params, context)
        result.data = result.data or {}
        result.data.update(
            {
                "template_id": str(template.id),
                "template_type": "composite",
                "test_completed": not result.is_error,
            }
        )
        result.content = section(
            "Composite Eval Template Test",
            (
                f"`{template.name}` is a Composite Eval, so Falcon used "
                "`execute_composite_eval` instead of the single-eval runner."
            ),
        ) + f"\n\n{result.content}"
        return result

    @staticmethod
    def _composite_required_keys(template) -> list[str]:
        from model_hub.models.evals_metric import CompositeEvalChild

        required_keys: list[str] = []
        links = CompositeEvalChild.objects.filter(
            parent=template,
            deleted=False,
        ).select_related("child")
        for link in links:
            config = link.child.config or {}
            child_required = (
                config.get("required_keys", []) if isinstance(config, dict) else []
            )
            if not isinstance(child_required, list):
                continue
            for key in child_required:
                key = str(key)
                if key and key not in required_keys:
                    required_keys.append(key)
        return required_keys

    @staticmethod
    def _sample_value_for_key(key: str) -> str:
        key_lower = key.lower()
        if key_lower in {"input", "query", "question", "prompt"}:
            return "What is the refund policy for an enterprise customer?"
        if key_lower in {"output", "response", "answer", "generated"}:
            return (
                "Enterprise customers can request a refund through support within "
                "the policy window."
            )
        if key_lower in {"expected", "expected_output", "reference", "ground_truth"}:
            return (
                "Refunds are handled by support according to the customer's "
                "contract and policy window."
            )
        if key_lower in {"context", "document", "source"}:
            return (
                "Enterprise refund requests should be reviewed against the "
                "customer contract and policy window."
            )
        return f"Sample value for {key}"

    @staticmethod
    def _normalize_mapping(raw_mapping: dict | str, required_keys: list[str]) -> dict:
        if isinstance(raw_mapping, dict):
            mapping = dict(raw_mapping)
            nested_input = mapping.get("input_data")
            if nested_input and not any(key in mapping for key in required_keys):
                nested = TestEvalTemplateTool._normalize_mapping(
                    nested_input, required_keys
                )
                if nested:
                    return nested
            return mapping

        if isinstance(raw_mapping, str):
            stripped = raw_mapping.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                if len(required_keys) == 1:
                    return {required_keys[0]: stripped}
                return {}
            return TestEvalTemplateTool._normalize_mapping(parsed, required_keys)

        return {}

    @staticmethod
    def _normalize_extra_dict(value: Any) -> dict:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _normalize_runtime_config(extra: dict) -> dict:
        runtime_config = TestEvalTemplateTool._normalize_extra_dict(
            extra.get("config")
        )
        top_level_params = TestEvalTemplateTool._normalize_extra_dict(
            extra.get("params")
        )
        config_params = runtime_config.get("params")
        if (
            (not isinstance(config_params, dict) or not config_params)
            and top_level_params
        ):
            runtime_config["params"] = top_level_params
        runtime_config.pop("mapping", None)
        runtime_config.pop("input_data_types", None)
        return runtime_config

    @staticmethod
    def _clean_template_ref(value: str) -> str:
        return (value or "").strip().strip("`'\"")

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        try:
            UUID(str(value))
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _candidate_result(templates, lead: str) -> ToolResult:
        rows = []
        data = []
        for template in templates:
            config = template.config or {}
            required_keys = (
                config.get("required_keys", []) if isinstance(config, dict) else []
            )
            rows.append(
                [
                    f"`{template.id}`",
                    truncate(template.name, 40),
                    template.owner or "-",
                    ", ".join(required_keys[:4]) if required_keys else "-",
                ]
            )
            data.append(
                {
                    "id": str(template.id),
                    "name": template.name,
                    "required_keys": required_keys,
                }
            )
        return ToolResult(
            content=section(
                "Eval Template Candidates",
                (
                    lead
                    + "\n\n"
                    + markdown_table(["ID", "Name", "Owner", "Required Keys"], rows)
                )
                if rows
                else "No eval templates found.",
            ),
            data={"templates": data, "requires_eval_template_id": True},
        )
