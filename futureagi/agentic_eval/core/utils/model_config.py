from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Final, Optional


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model_name: str
    temperature: float
    max_tokens: int
    # Modality support flags
    supports_audio: bool = False
    supports_pdf: bool = False


class LiteLlmProvider(str, Enum):
    VERTEX_AI = "vertex_ai"
    OPENAI = "openai"
    PERPLEXITY = "perplexity"
    ANTHROPIC = "anthropic"
    AWS_BEDROCK_ANTHROPIC = "aws_bedrock_anthropic"
    GROQ = "groq"
    VLLM = "vllm"
    TURING = "turing"
    PROTECT = "protect"
    PROTECT_FLASH = "protect_flash"


class ModelConfigs:
    """
    Single source of truth for model + runtime parameters.

    This is intentionally isolated from `agentic_eval/core/utils/constant.py` so the
    codebase can be refactored without depending on chained constants.
    """

    TURING_LARGE: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.TURING.value,
        model_name="turing_large",
        temperature=0.2,
        max_tokens=50000,
    )
    TURING_LARGE_XL: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.TURING.value,
        model_name="turing_large_xl",
        temperature=0.2,
        max_tokens=50000,
        supports_audio=True,
        supports_pdf=True,
    )
    TURING_SMALL: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.TURING.value,
        model_name="turing_small",
        temperature=0.2,
        max_tokens=50000,
    )
    TURING_FLASH: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.TURING.value,
        model_name="turing_flash",
        temperature=0.2,
        max_tokens=50000,
    )
    INTERNET_SEARCH: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.PERPLEXITY.value,
        model_name="perplexity/llama-3.1-sonar-large-128k-online",
        temperature=0.2,
        max_tokens=16000,
    )
    OPENAI_GPT_5_1: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.OPENAI.value,
        model_name="gpt-5.1",
        temperature=0.2,
        max_tokens=16000,
    )

    VERTEX_GEMINI_2_5_PRO: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.VERTEX_AI.value,
        model_name="vertex_ai/gemini-2.5-pro",
        temperature=0.2,
        max_tokens=50000,
        supports_audio=True,
        supports_pdf=True,
    )

    VERTEX_GEMINI_2_5_FLASH: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.VERTEX_AI.value,
        model_name="vertex_ai/gemini-2.5-flash",
        temperature=0.2,
        max_tokens=50000,
    )

    # Claude Sonnet (used by EvalTextLLM defaults in constant.py today).
    CLAUDE_3_5_SONNET: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.ANTHROPIC.value,
        model_name="anthropic.claude-3-5-sonnet-20240620-v1:0",
        temperature=0.2,
        max_tokens=8100,
    )
    CLAUDE_SONNET_4_6: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.ANTHROPIC.value,
        model_name="claude-sonnet-4-6",
        temperature=0.2,
        max_tokens=16000,
    )

    VERTEX_GEMINI_3_FLASH: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.VERTEX_AI.value,
        model_name="vertex_ai/gemini-3-flash-preview",
        temperature=0.2,
        max_tokens=50000,
        supports_audio=True,
        supports_pdf=True,
    )

    VERTEX_GEMINI_3_PRO: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.VERTEX_AI.value,
        model_name="vertex_ai/gemini-3-pro-preview",
        temperature=0.2,
        max_tokens=50000,
        supports_audio=True,
        supports_pdf=True,
    )

    CLAUDE_4_5_SONNET_BEDROCK_ARN: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value,
        model_name=os.environ.get("BEDROCK_SONNET_ARN", ""),
        temperature=0.2,
        max_tokens=8100,
    )

    GROQ_LLAMA_3_3_70B: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.GROQ.value,
        model_name="groq/llama-3.3-70b-versatile",
        temperature=0.2,
        max_tokens=16000,
    )

    PROTECT_FLASH: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.PROTECT_FLASH.value,
        model_name="protect_flash",
        temperature=0.0,
        max_tokens=128,
    )

    PROTECT: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.PROTECT.value,
        model_name="protect",
        temperature=0.0,
        max_tokens=150,
    )

    OPUS_4_5_BEDROCK_ARN: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value,
        model_name=os.environ.get("BEDROCK_OPUS_ARN", ""),
        temperature=0.2,
        max_tokens=50000,
    )

    SONNET_4_5_BEDROCK_ARN: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value,
        model_name=os.environ.get("BEDROCK_SONNET_ARN", ""),
        temperature=0.2,
        max_tokens=50000,
    )

    HAIKU_4_5_BEDROCK_ARN: Final[ModelConfig] = ModelConfig(
        provider=LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value,
        model_name=os.environ.get("BEDROCK_HAIKU_ARN", ""),
        temperature=0.2,
        max_tokens=50000,
    )

    @classmethod
    def get_config(cls, model_name: str) -> Optional[ModelConfig]:
        """
        Resolves a ModelConfig given a model name string.
        Attempts exact match first, then checks without provider prefix.
        """
        if not model_name:
            return None

        # 1. Exact match across all configs
        for attr in dir(cls):
            cfg = getattr(cls, attr)
            if isinstance(cfg, ModelConfig):
                if cfg.model_name == model_name:
                    return cfg

        # 2. Match without provider prefix (e.g. "gpt-4o" matches "gpt-4o")
        # and handle common shorthands
        clean_name = model_name.split("/")[-1] if "/" in model_name else model_name
        for attr in dir(cls):
            cfg = getattr(cls, attr)
            if isinstance(cfg, ModelConfig):
                if cfg.model_name.split("/")[-1] == clean_name:
                    return cfg

        return None

    @classmethod
    def get_max_tokens(cls, model_name: str) -> Optional[int]:
        """Resolves max tokens for a model name."""
        cfg = cls.get_config(model_name)
        return cfg.max_tokens if cfg else None

    @classmethod
    def get_temperature(cls, model_name: str) -> Optional[float]:
        """Resolves default temperature for a model name."""
        cfg = cls.get_config(model_name)
        return cfg.temperature if cfg else None

    @classmethod
    def get_provider(cls, model_name: str) -> Optional[str]:
        """Resolves provider for a model name."""
        cfg = cls.get_config(model_name)
        return cfg.provider if cfg else None

    @classmethod
    def is_turing(cls, model_name: str) -> bool:
        """Check if the model is a turing model."""
        cfg = cls.get_config(model_name)
        return bool(cfg and cfg.provider == LiteLlmProvider.TURING.value)

    @classmethod
    def supports_audio(cls, model_name: str) -> bool:
        """Check if the model supports audio inputs."""
        cfg = cls.get_config(model_name)
        return bool(cfg and cfg.supports_audio)

    @classmethod
    def supports_pdf(cls, model_name: str) -> bool:
        """Check if the model supports PDF inputs."""
        cfg = cls.get_config(model_name)
        return bool(cfg and cfg.supports_pdf)
