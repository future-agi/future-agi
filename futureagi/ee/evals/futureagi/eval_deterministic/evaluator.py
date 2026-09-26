import gc
import json
import time
from typing import Any, Dict, List, Optional

import structlog

from ee import _ee_stub

try:
    from ee.agenthub.deterministic_agent.deterministic_agent import (
        DeterministicAgent,
    )
except ImportError:
    DeterministicAgent = _ee_stub("DeterministicAgent")
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import detect_input_type
from agentic_eval.core.utils.model_config import ModelConfig, ModelConfigs
from agentic_eval.core_evals.fi_evals.eval_type import FutureAgiEvalTypeId
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult
from agentic_eval.core_evals.fi_utils.fi_model import Model

logger = structlog.get_logger(__name__)


class DeterministicEvaluator(LLM):
    """Evaluate mapped inputs against a deterministic rule prompt."""

    _PROTECT_MODES = {"protect", "protect_flash"}
    _INPUT_KEYS = (
        "input",
        "system_prompt",
        "context",
        "output",
        "input_image",
        "conversation",
        "image",
        "caption",
        "expected",
        "text",
        "generated_value",
        "expected_value",
        "generated_audio",
        "prompt",
        "audio",
        "input_audio",
        "generated_transcript",
        "transcription",
        "input_pdf",
        "json_content",
        "instruction",
        "generated_image",
    )

    def __init__(
        self,
        model: str = ModelConfigs.TURING_LARGE.model_name,
        api_key: str = "api_key",
        multi_choice: bool = False,
        choices: Optional[List[str]] = None,
        rule_prompt: str = "",
        input: Optional[List[str]] = None,
        input_type: Optional[List[str]] = None,
        **kwargs,
    ) -> None:
        requested_provider = kwargs.get("provider")
        # Protect providers use fixed provider configs and ignore model lookups.
        if requested_provider in {
            ModelConfigs.PROTECT.provider,
            ModelConfigs.PROTECT_FLASH.provider,
        }:
            if requested_provider == ModelConfigs.PROTECT.provider:
                cfg = ModelConfigs.PROTECT
            else:
                cfg = ModelConfigs.PROTECT_FLASH
            self.config_obj = ModelConfig(
                provider=requested_provider,
                model_name=model or cfg.model_name,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
        else:
            # Resolve model config and fall back to TURING_LARGE if unknown.
            requested_config = ModelConfigs.get_config(model)
            if requested_config is None:
                if model is not None:
                    logger.warning(
                        "deterministic_evaluator_model_config_fallback",
                        requested_model=model,
                        fallback_model=ModelConfigs.TURING_LARGE.model_name,
                    )
                self.config_obj = ModelConfigs.TURING_LARGE
            else:
                self.config_obj = requested_config

        super().__init__(
            model_name=self.config_obj.model_name,
            provider=self.config_obj.provider,
            api_key=api_key,
            temperature=self.config_obj.temperature,
            max_tokens=self.config_obj.max_tokens,
        )
        self.multi_choice = multi_choice
        self.choices = list(choices or [])
        self.rule_prompt = rule_prompt
        self.input = list(input or [])
        self.input_type = list(input_type or [])
        self.kwargs = kwargs
        self.scorer = False
        self.knowledge_base_id = kwargs.get("knowledge_base_id", None)
        self.custom_eval = kwargs.get("custom_eval", False)
        self.check_internet = kwargs.get("check_internet", False)
        self.fewshots = kwargs.get("few_shots", None)

    @property
    def name(self):
        return FutureAgiEvalTypeId.DETERMINISTIC_EVAL.value

    @property
    def display_name(self):
        return "Deterministic Evaluation"

    @property
    def default_model(self):
        return Model.GPT4.value

    @property
    def required_args(self):
        return ["rule_prompt", "choices"]

    def _ensure_input_types(self) -> None:
        """Populate input types from current inputs when missing."""
        if self.input_type:
            return

        self.input_type = []
        for input_item in self.input:
            detected = detect_input_type(input_item)
            self.input_type.extend(detected.values())

    def _format_chat_history(self) -> Dict[str, Any]:
        """Build evaluator payload consumed by deterministic agents and protect paths."""
        self._ensure_input_types()

        payload = {
            "inputs": self.input,
            "rule_prompt": self.rule_prompt,
            "choices": self.choices,
            "multi_choice": self.multi_choice,
            "input_type": self.input_type,
        }

        return payload

    def _build_metadata(
        self, eval_runtime_ms: int, score_results: dict, chat_history: dict, llm=None
    ) -> str:
        """Centralized metadata construction for evaluation results."""
        # Use dedicated protect-mode LLM for token/cost accounting.
        target_llm = llm if llm else self
        return json.dumps(
            {
                "usage": {
                    "completion_tokens": target_llm.token_usage["completion_tokens"],
                    "prompt_tokens": target_llm.token_usage["prompt_tokens"],
                    "total_tokens": target_llm.token_usage["total_tokens"],
                },
                "cost": {
                    "total_cost": target_llm.cost["total_cost"],
                    "prompt_cost": target_llm.cost["prompt_cost"],
                    "completion_cost": target_llm.cost["completion_cost"],
                },
                "response_time": eval_runtime_ms,
                "explanation": score_results.get("explanation", ""),
                "data": chat_history,
            },
            default=str,
        )

    def _validate_param_modalities(
        self,
        *,
        required_keys: List[str],
        param_modalities: Dict[str, List[Any]],
        param_values: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Enforce backend mapping contracts before expensive model calls.
        if not param_modalities or not required_keys:
            return

        for idx, param_name in enumerate(required_keys):
            if param_name not in param_modalities:
                continue
            column_type = None
            if param_values is not None:
                if param_name not in param_values:
                    continue

                param_value = param_values.get(param_name)
                if param_value is None:
                    continue

                # Prefer already-computed input_type (from _ensure_input_types)
                # to avoid redundant downloads of large media files and
                # transient network failures causing false type mismatches.
                if idx < len(self.input_type) and self.input_type[idx]:
                    column_type = self.input_type[idx]
                else:
                    detected_type = detect_input_type(param_value)
                    if detected_type:
                        column_type = next(iter(detected_type.values()), None)
            else:
                if idx >= len(self.input_type):
                    continue
                column_type = self.input_type[idx]

            supported_modalities = param_modalities[param_name]
            logger.debug(
                "deterministic_eval_modality_check",
                param_name=param_name,
                detected_modality=column_type,
                allowed_modalities=supported_modalities,
                required_key_index=idx,
            )

            supported_lower = [
                m.lower() if isinstance(m, str) else str(m).lower()
                for m in supported_modalities
            ]
            column_type_lower = (
                column_type.lower()
                if isinstance(column_type, str)
                else str(column_type).lower()
            )

            if column_type_lower not in supported_lower:
                allowed_list = [str(m).title() for m in supported_modalities]
                if len(allowed_list) > 10:
                    allowed_str = ", ".join(allowed_list[:10]) + "..."
                else:
                    allowed_str = ", ".join(allowed_list)
                received_type = (
                    column_type.title()
                    if isinstance(column_type, str)
                    else str(column_type).title()
                )

                error_msg = (
                    f"Input type mismatch for parameter '{param_name}': "
                    f"Expected {allowed_str}, but received {received_type}. "
                    f"Please check your evaluation mapping configuration and ensure "
                    f"the correct input type is mapped to '{param_name}'."
                )
                raise ValueError(error_msg)

    def validate_args(self, **kwargs):
        """Validate deterministic-only arguments.

        Protect modes bypass this because they do not require `rule_prompt`/`choices`.
        """

        if self.provider in {
            ModelConfigs.PROTECT.provider,
            ModelConfigs.PROTECT_FLASH.provider,
        }:
            # Skip deterministic rubric requirements for protect flows.
            return

        # Validate deterministic rubric inputs.
        if not self.rule_prompt:
            raise ValueError("Required arguments are missing rule_prompt")
        if not self.choices:
            self.choices = ["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]
            self.rule_prompt = f"{self.rule_prompt} "
            self.scorer = True

    @staticmethod
    def _is_protect_mode(call_type: str) -> bool:
        return call_type in DeterministicEvaluator._PROTECT_MODES

    def _resolve_call_type(self, default_call_type: str) -> str:
        # Prefer provider-driven routing over external call_type hints.
        if self.provider == ModelConfigs.PROTECT.provider:
            return "protect"
        if self.provider == ModelConfigs.PROTECT_FLASH.provider:
            return "protect_flash"
        return default_call_type

    def _validate_protect_input_types(self) -> None:
        # Allow only text, single image, and audio for protect endpoints.
        normalized_input_types = {
            str(input_type).strip().lower()
            for input_type in self.input_type
            if input_type
        }
        unsupported_types = normalized_input_types - {"text", "image", "audio"}
        if "images" in unsupported_types:
            raise ValueError(
                "Protect does not support image sets (`images`). Please provide a single image input."
            )
        if "pdf" in unsupported_types or "knowledge_base" in unsupported_types:
            raise ValueError("Protect does not support PDF or knowledge base inputs.")
        if unsupported_types:
            raise ValueError(
                "Protect does not support input type(s): "
                + ", ".join(sorted(unsupported_types))
            )

    def _resolve_protect_metric(self, eval_name: Optional[str], call_type: str) -> str:
        if call_type == "protect_flash":
            # Use model_name directly as endpoint model for protect_flash.
            return self.model_name

        # Normalize UI eval names into provider metric ids.
        ui_to_metric = {
            "toxicity": "toxicity",
            "bias": "bias",
            "sexist": "bias",
            "bias detection": "bias",
            "prompt_injection": "prompt_injection",
            "prompt injection": "prompt_injection",
            "data_privacy_compliance": "privacy",
            "data privacy compliance": "privacy",
            "bias_detection": "bias",  # Keep SDK alias mapping for bias.
            "pii": "privacy",
        }
        key = (eval_name or "").strip().lower()
        if key not in ui_to_metric:
            raise ValueError(
                "Protect model supports only toxicity, bias_detection, pii, and prompt_injection evals."
            )
        metric = ui_to_metric[key]
        logger.info(
            "deterministic_eval_protect_request_started",
            metric=metric,
            eval_name=eval_name,
        )
        return metric

    def _run_protect_mode(
        self, chat_history: dict, eval_name: Optional[str], call_type: str
    ):
        # Apply the same modality checks for protect and protect_flash.
        self._validate_protect_input_types()

        metric = self._resolve_protect_metric(eval_name, call_type)
        self.llm = LLM(
            model_name=metric,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            provider=call_type,
        )
        protect_result = self.llm._get_completion_content(chat_history)
        if isinstance(protect_result, dict):
            return protect_result
        # Raise provider-processing error when protect response is not structured.
        raise ValueError("FAILED_TO_PROCESS_PROTECT_EVALUATION")

    def _hydrate_inputs_from_kwargs(self, kwargs: Dict[str, Any]) -> None:
        # Trust required_keys contract for custom eval payloads.
        if self.custom_eval:
            required_keys = kwargs.get("required_keys")
            if required_keys:
                self.input = [kwargs[key] for key in required_keys if key in kwargs]
            else:
                self.input = []
            return

        if self.input:
            return

        required_keys = kwargs.get("required_keys") or []
        config_params_desc = kwargs.get("config_params_desc") or {}

        # Build self.input using required_keys order so that variable_N maps
        # to the correct key by name, not by position in _INPUT_KEYS.
        if required_keys:
            active_keys = [k for k in required_keys if kwargs.get(k) is not None]
            self.input = [kwargs[k] for k in active_keys]
        else:
            # Fallback for evals without required_keys (backward compat).
            active_keys = [k for k in self._INPUT_KEYS if kwargs.get(k)]
            self.input = [kwargs[k] for k in active_keys]

        if not self.input:
            return

        # Skip appending if rule_prompt already contains variable placeholders
        # (e.g. from a version override that stored them inline).
        if "{{variable_" in self.rule_prompt:
            return

        # Build variable references and a mapping block so the LLM knows
        # what each <variable_N> tag represents by its actual key name and description.
        # The {{variable_N}} placeholders are preserved because _get_prompt()
        # substitutes them with $variable_N$ markers via .replace().
        variable_parts = [
            f"{{{{variable_{i + 1}}}}}" for i in range(len(self.input))
        ]

        # Build mapping lines using <variable_N> tags (matching the actual
        # content block tags the LLM sees). Include description only when available.
        has_descriptions = isinstance(config_params_desc, dict) and config_params_desc
        variable_mapping_lines = []
        for i, key_name in enumerate(active_keys):
            var_tag = f"<variable_{i + 1}>"
            key_desc = config_params_desc.get(key_name) if has_descriptions else None
            if key_desc:
                variable_mapping_lines.append(
                    f"- {var_tag} contains the input field \"{key_name}\" - {key_desc}"
                )
            else:
                variable_mapping_lines.append(
                    f"- {var_tag} contains the input field \"{key_name}\""
                )

        # Only append the variable context block when we have key names to map.
        # The {{variable_N}} placeholders inside the user's original rule text
        # are substituted by _get_prompt() — no need to append them again here.
        if variable_mapping_lines:
            variable_mapping_block = "\n".join(variable_mapping_lines)
            self.rule_prompt = (
                f"{self.rule_prompt}\n\n"
                f"Variable Reference:\n{variable_mapping_block}\n"
                f"Refer to inputs by their field name, not as \"variable_1\" etc.\n\n"
            )
        else:
            self.rule_prompt = (
                f"{self.rule_prompt} -- " + " -- ".join(variable_parts)
            )

    def _build_protect_flash_result(
        self, score_results: dict, eval_runtime_ms: int, kwargs: dict
    ) -> dict:
        # Preserve protect_flash result schema consumed by backend/UI.
        is_harmful = bool(score_results.get("is_harmful", False))
        choice_result = "Failed" if is_harmful else "Passed"
        metadata = json.dumps(
            {
                "usage": {
                    "completion_tokens": self.llm.token_usage.get(
                        "completion_tokens", 0
                    ),
                    "prompt_tokens": self.llm.token_usage.get("prompt_tokens", 0),
                    "total_tokens": self.llm.token_usage.get("total_tokens", 0),
                },
                "cost": {
                    "total_cost": self.llm.cost.get("total_cost", 0),
                    "prompt_cost": self.llm.cost.get("prompt_cost", 0),
                    "completion_cost": self.llm.cost.get("completion_cost", 0),
                },
                "response_time": eval_runtime_ms,
                "explanation": score_results.get("explanation", ""),
                "data": {
                    "inputs": kwargs.get("input", self.input),
                    "is_harmful": is_harmful,
                },
            }
        )
        llm_eval_result = {
            "name": self.name,
            "display_name": self.display_name,
            "data": {
                "result": choice_result,
                "input": kwargs.get("input", self.input),
            },
            "failure": is_harmful,
            "reason": score_results.get("explanation", ""),
            "metadata": metadata,
            "runtime": eval_runtime_ms,
            "model": self.llm.model_name,
            "metrics": [
                # Keep score semantics aligned
                {
                    "id": "protect_flash_score",
                    "value": 0.0 if is_harmful else 1.0,
                }
            ],
            "datapoint_field_annotations": {},
        }
        return {k: v for k, v in llm_eval_result.items() if v is not None}

    def _evaluate(self, **kwargs) -> Dict[str, Any]:
        """Run deterministic, protect, or protect-flash evaluation for one row."""
        # Normalize dynamic inputs before validation and routing.
        self._hydrate_inputs_from_kwargs(kwargs)
        start_time = time.time()
        self.validate_args(**kwargs)
        eval_name = kwargs.get("eval_name")
        call_type = self._resolve_call_type(kwargs.get("call_type", "default"))

        # Keep modality metadata synced with resolved input values.
        self._ensure_input_types()

        # Validate mapping modalities before calling any model endpoint.
        if not self.custom_eval:
            self._validate_param_modalities(
                required_keys=kwargs.get("required_keys", []),
                param_modalities=kwargs.get("param_modalities", {}),
                param_values=kwargs,
            )

        # Override constructor defaults with runtime kwargs when provided.
        if "few_shots" in kwargs:
            self.fewshots = kwargs.get("few_shots")
        if "check_internet" in kwargs:
            self.check_internet = kwargs.get("check_internet", False)

        chat_history = self._format_chat_history()

        if self._is_protect_mode(call_type):
            # Bypass DeterministicAgent and call LLM directly for protect providers.
            score_results = self._run_protect_mode(chat_history, eval_name, call_type)
        else:
            # Delegate planning/analysis/validation orchestration to DeterministicAgent.
            agent = DeterministicAgent(
                model_name=self.model_name,
                model_type=self.kwargs.get("model_type"),
                llm=self,
                check_internet=self.check_internet,
                fewshots=self.fewshots,
                knowledge_base_id=self.knowledge_base_id,
            )
            score_results = agent.evaluate(chat_history)
            # For Turing path, tokens accumulate on agent.turing_client but
            # _build_metadata reads from self (the evaluator). Copy them over.
            # For litellm path, tokens already accumulate on self (the evaluator
            # IS the LLM instance), so this is a no-op (agent.token_usage points
            # to self.token_usage, adding zeros).
            if agent.is_turing_model:
                agent_usage = agent.token_usage
                self.token_usage["prompt_tokens"] += agent_usage.get("prompt_tokens", 0)
                self.token_usage["completion_tokens"] += agent_usage.get("completion_tokens", 0)
                self.token_usage["total_tokens"] += agent_usage.get("total_tokens", 0)
                agent_cost = agent.cost
                self.cost["prompt_cost"] += agent_cost.get("prompt_cost", 0.0)
                self.cost["completion_cost"] += agent_cost.get("completion_cost", 0.0)
                self.cost["total_cost"] += agent_cost.get("total_cost", 0.0)
        gc.collect()

        end_time = time.time()
        eval_runtime_ms = int((end_time - start_time) * 1000)

        logger.info(
            "deterministic_eval_final_usage",
            eval_name=eval_name,
            model=self.model_name,
            runtime_ms=eval_runtime_ms,
            prompt_tokens=self.token_usage.get("prompt_tokens", 0),
            completion_tokens=self.token_usage.get("completion_tokens", 0),
            total_tokens=self.token_usage.get("total_tokens", 0),
            total_cost=self.cost.get("total_cost", 0.0),
        )

        if call_type == "protect_flash":
            # Preserve output shape expected by downstream UI/SDK.
            return self._build_protect_flash_result(
                score_results, eval_runtime_ms, kwargs
            )

        # Return a normalized eval payload for deterministic and protect paths.
        metadata = self._build_metadata(
            eval_runtime_ms,
            score_results,
            chat_history,
            llm=self.llm if self._is_protect_mode(call_type) else None,
        )

        llm_eval_result: Dict = {
            "name": self.name,
            "display_name": self.display_name,
            "data": score_results.get("choices", ""),
            "failure": False,
            "reason": score_results.get("explanation", ""),
            "metadata": metadata,
            "runtime": eval_runtime_ms,
            "model": (
                self.model_name
                if not self._is_protect_mode(call_type)
                else self.llm.model_name
            ),
            "metrics": [
                # Preserve deterministic metric id contract (`id=1`).
                {
                    "id": 1,
                    "value": (
                        score_results.get("choices", "")
                        if not self.scorer
                        else float(score_results["choices"][0])
                    ),
                }
            ],
            "datapoint_field_annotations": {},  # Keep required response field.
        }
        result = {k: v for k, v in llm_eval_result.items() if v is not None}
        return result
