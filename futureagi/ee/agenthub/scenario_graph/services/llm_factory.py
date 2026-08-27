"""Centralized LLM instance creation for graph scenario services.

Each service call creates a fresh LLM instance (no shared mutable state).
"""

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs


def create_llm(temperature: float = 0.3, max_tokens: int = 400) -> LLM:
    """Create a fresh LLM instance (thread-safe — no shared state).

    Uses Gemini 2.5 Flash for fast classification/categorization tasks.
    """
    config = ModelConfigs.VERTEX_GEMINI_2_5_FLASH
    return LLM(
        model_name=config.model_name,
        provider=config.provider,
        temperature=temperature,
        max_tokens=max_tokens,
    )
