"""
Constants for Chat Simulation services.

These constants control chat provider behavior, default models, and LLM parameters.
"""

import os

from agentic_eval.core.utils.model_config import ModelConfigs

# =============================================================================
# Chat Provider Configuration
# =============================================================================

# Default chat simulation provider
# Options: "futureagi" (default), "vapi"
CHAT_SIMULATION_PROVIDER = os.getenv("CHAT_SIMULATION_PROVIDER", "futureagi")


# =============================================================================
# Future AGI Chat Configuration (sourced from ModelConfigs.CHAT_SIMULATOR)
# =============================================================================

_chat_sim_config = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN

# The Bedrock config resolves model_name from BEDROCK_SONNET_ARN, which is
# only set on cloud deployments. Without a fallback, self-hosted/local stacks
# created chat simulator assistants with model="" and every chat simulation
# died inside litellm ("LLM Provider NOT provided", surfaced as a Logging
# JSON-serialization crash by the retry wrapper).
if not _chat_sim_config.model_name:
    _chat_sim_config = ModelConfigs.OPENAI_GPT_5_1

FUTUREAGI_CHAT_MODEL = _chat_sim_config.model_name
FUTUREAGI_CHAT_TEMPERATURE = _chat_sim_config.temperature
FUTUREAGI_CHAT_MAX_TOKENS = _chat_sim_config.max_tokens


# =============================================================================
# Chat Session Configuration
# =============================================================================

# Maximum conversation turns before auto-ending (safety limit)
MAX_CONVERSATION_TURNS = int(os.getenv("MAX_CONVERSATION_TURNS", "500"))

# Session timeout in minutes (for stale session cleanup)
CHAT_SESSION_TIMEOUT_MINUTES = int(os.getenv("CHAT_SESSION_TIMEOUT_MINUTES", "30"))
