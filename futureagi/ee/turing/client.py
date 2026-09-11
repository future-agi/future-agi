"""
TuringClient — client for turing model completions.

Provides OpenAI-compatible chat completion interface for turing models
(turing_large, turing_small, turing_flash).

All turing-specific business logic (modality validation, model upgrades)
is centralized here.
"""

from typing import Any, Dict, List, Optional

import structlog

from agentic_eval.core.utils.model_config import ModelConfigs

logger = structlog.get_logger(__name__)

# Model to upgrade to when audio/pdf is needed
_MULTIMODAL_UPGRADE_MODEL = "turing_large_xl"


class ModalityNotSupportedError(ValueError):
    """Raised when a turing model does not support the input modality (audio, PDF).

    User-actionable: tells them which model to use instead.
    Safe to surface directly to the end user.
    """


class TuringClient:
    """
    Client for turing model completions.

    Provides chat_completion and chat_completion_with_tools methods
    with token usage and cost tracking. Handles modality validation
    and model upgrades for audio/pdf inputs.
    """

    def __init__(self, **kwargs):
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._cost = {"prompt_cost": 0.0, "completion_cost": 0.0, "total_cost": 0.0}

    def _call_managed_gateway(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from ee.licensing.managed_ai import chat_completion

        return chat_completion(payload)

    @staticmethod
    def resolve_model(
        model: str,
        detected_media_types: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Resolve the effective model based on detected media types.

        Upgrades turing_large to turing_large_xl when audio/pdf is detected.
        Raises ValueError if the model does not support the detected modalities.

        Args:
            model: Requested turing model name.
            detected_media_types: {key: modality} map from prior detection.
                Values are "image", "images", "audio", "pdf", etc.

        Returns:
            The effective model name (possibly upgraded).

        Raises:
            ValueError: If the model does not support the detected modalities.
        """
        if not detected_media_types:
            return model

        detected_set = set(detected_media_types.values())
        has_audio = "audio" in detected_set
        has_pdf = "pdf" in detected_set

        if not has_audio and not has_pdf:
            return model

        # Step 1: upgrade turing_large → turing_large_xl for audio/pdf
        effective = model
        if effective == "turing_large":
            effective = _MULTIMODAL_UPGRADE_MODEL
            logger.info(
                "turing_client_model_upgrade",
                original_model=model,
                upgraded_model=effective,
                has_audio=has_audio,
                has_pdf=has_pdf,
            )

        # Step 2: validate the effective model supports the modality
        config = ModelConfigs.get_config(effective)
        supports = (
            (not has_audio or (config and config.supports_audio))
            and (not has_pdf or (config and config.supports_pdf))
        )
        if supports:
            return effective

        modality = "audio" if has_audio else "PDF"
        raise ModalityNotSupportedError(
            f"Model {model} does not support {modality} inputs. "
            f"Please use turing_large for {modality} evaluations."
        )

    @property
    def token_usage(self) -> Dict[str, int]:
        """Get accumulated token usage across all calls."""
        return self._usage

    @property
    def cost(self) -> Dict[str, float]:
        """Get accumulated cost across all calls."""
        return self._cost

    @staticmethod
    def _truncate_text(value: Any, limit: int = 500) -> Any:
        if not isinstance(value, str):
            return value
        if len(value) <= limit:
            return value
        return f"{value[:limit]}..."

    @classmethod
    def sanitize_for_logs(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: cls.sanitize_for_logs(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls.sanitize_for_logs(v) for v in value]
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("data:"):
                prefix = text.split(",", 1)[0]
                return cls._truncate_text(f"{prefix},<truncated>", 220)
            return cls._truncate_text(text, 220)
        return value

    def _accumulate_usage(self, llm) -> None:
        """Accumulate token usage and cost from the LLM instance."""
        usage = llm.token_usage
        self._usage = {
            "prompt_tokens": self._usage["prompt_tokens"] + usage.get("prompt_tokens", 0),
            "completion_tokens": self._usage["completion_tokens"] + usage.get("completion_tokens", 0),
            "total_tokens": self._usage["total_tokens"] + usage.get("total_tokens", 0),
        }
        cost = llm.cost
        self._cost = {
            "prompt_cost": self._cost["prompt_cost"] + cost.get("prompt_cost", 0.0),
            "completion_cost": self._cost["completion_cost"] + cost.get("completion_cost", 0.0),
            "total_cost": self._cost["total_cost"] + cost.get("total_cost", 0.0),
        }

    def _accumulate_response_usage(self, response: Dict[str, Any]) -> None:
        usage = response.get("usage") or {}
        self._usage = {
            "prompt_tokens": self._usage["prompt_tokens"] + usage.get("prompt_tokens", 0),
            "completion_tokens": self._usage["completion_tokens"] + usage.get("completion_tokens", 0),
            "total_tokens": self._usage["total_tokens"] + usage.get("total_tokens", 0),
        }

    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 50000,
        response_format: Optional[Dict[str, Any]] = None,
        knowledge_base_id: Optional[str] = None,
        check_internet: bool = False,
        detected_media_types: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Call turing model chat completions.

        Automatically upgrades the model if audio/pdf inputs are detected
        and the requested model supports multimodal upgrade.

        Args:
            model: Turing model name (turing_large/small/flash)
            messages: Chat messages
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            response_format: Optional response format spec
            knowledge_base_id: Reserved for future use
            check_internet: Reserved for future use
            detected_media_types: {key: modality} from prior detection.
                Pass this to avoid redundant detect_input_type calls.

        Returns:
            Completion content string

        Raises:
            ValueError: On modality validation errors or invalid response
        """
        effective_model = self.resolve_model(model, detected_media_types)

        logger.debug(
            "turing_client_request",
            model=effective_model,
            original_model=model if effective_model != model else None,
            message_count=len(messages),
            has_response_format=response_format is not None,
            has_knowledge_base=bool(knowledge_base_id),
            check_internet=check_internet,
        )

        response = self._call_managed_gateway(
            {
                "model": effective_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
            }
        )

        self._accumulate_response_usage(response)
        from ee.licensing.managed_ai import response_content

        content = response_content(response)

        logger.debug(
            "turing_client_response",
            model=effective_model,
            prompt_tokens=self._usage.get("prompt_tokens", 0),
            completion_tokens=self._usage.get("completion_tokens", 0),
            total_tokens=self._usage.get("total_tokens", 0),
            total_cost=self._cost.get("total_cost", 0.0),
            content_length=len(content) if content else 0,
        )

        return content if content is not None else ""

    def chat_completion_with_tools(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 16000,
        detected_media_types: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Call turing model with tool support for agentic loops.

        Returns a dict matching the OpenAI response format so callers
        can inspect tool_calls, finish_reason, etc.

        Args:
            model: Turing model name
            messages: Chat messages
            tools: Optional list of tool definitions (OpenAI function-calling format)
            tool_choice: Optional tool choice strategy
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            detected_media_types: {key: modality} from prior detection.

        Returns:
            OpenAI-compatible response dict

        Raises:
            ValueError: On modality or network errors
        """
        effective_model = self.resolve_model(model, detected_media_types)

        logger.debug(
            "turing_client_tool_request",
            model=effective_model,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
        )

        data = self._call_managed_gateway(
            {
                "model": effective_model,
                "messages": messages,
                "tools": tools or [],
                "tool_choice": tool_choice,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        self._accumulate_response_usage(data)

        logger.debug(
            "turing_client_tool_response",
            model=effective_model,
            finish_reason=data["choices"][0].get("finish_reason") if data["choices"] else None,
            has_tool_calls=bool(
                data["choices"][0].get("message", {}).get("tool_calls") if data["choices"] else False
            ),
        )

        return data
