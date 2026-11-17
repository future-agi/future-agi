"""The platform's own models: which model each platform feature runs, and
the call for the features that need only a prompt answered.

Five platform features run a model of the platform's own choosing, on the
platform's account. Each is a ``PlatformFeature`` member; its model, the
model's provider and location, and the temperature are its entry in
``PLATFORM_MODELS``, the platform's own fixed values.

The chat simulator's first message and the voice simulator's background
choice call ``call_platform_model``, which takes which of them is calling
and the prompt, nothing else. Customer text may be in the prompt; it is
data. The call goes to the platform gateway first, as before; without one,
it goes to the model library with that model and location and no key,
configuration, project or address.

The smart filter, the criteria helper and the AI eval writer build their
model with ``model_hub.services.model_route.platform_llm``, which takes
which of them is calling, not a model, and runs that feature's entry.

``LLM.call_llm`` refuses a call to a Vertex model given no service-account
key of the customer's own
(``refuse_vertex_call_without_a_service_account_key``).
"""

import enum
from types import MappingProxyType

import structlog

from agentic_eval.core.llm.llm import (
    LITELLM_NUM_RETRIES,
    LITELLM_RETRY_STRATEGY,
    LLM,
    Messages,
)
from agentic_eval.core.utils.model_config import ModelConfigs
from model_hub.utils.model_endpoints import (
    ModelConfigurationRefused,
    guarded_completion,
)

logger = structlog.get_logger(__name__)


class PlatformFeature(enum.Enum):
    """A platform feature that calls a model of the platform's own choosing."""

    CHAT_SIMULATOR_FIRST_MESSAGE = "chat_simulator_first_message"
    VOICE_BACKGROUND_CHOICE = "voice_background_choice"
    SMART_FILTER = "smart_filter"
    CRITERIA_HELPER = "criteria_helper"
    AI_EVAL_WRITER = "ai_eval_writer"


# feature -> (the platform's own model for it, the temperature it uses)
PLATFORM_MODELS = MappingProxyType(
    {
        PlatformFeature.CHAT_SIMULATOR_FIRST_MESSAGE: (
            ModelConfigs.VERTEX_GEMINI_3_7_FLASH,
            0.7,
        ),
        PlatformFeature.VOICE_BACKGROUND_CHOICE: (
            ModelConfigs.VERTEX_GEMINI_3_5_FLASH_LITE,
            0.2,
        ),
        PlatformFeature.SMART_FILTER: (
            ModelConfigs.VERTEX_GEMINI_3_5_FLASH_LITE,
            0.0,
        ),
        PlatformFeature.CRITERIA_HELPER: (
            ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN,
            0.2,
        ),
        PlatformFeature.AI_EVAL_WRITER: (
            ModelConfigs.HAIKU_4_5_BEDROCK_ARN,
            0.3,
        ),
    }
)


def call_platform_model(feature: PlatformFeature, messages: Messages) -> str:
    """The text the platform's own model for ``feature`` answers to
    ``messages``. ``feature`` must be a ``PlatformFeature`` member, or
    ModelConfigurationRefused before any call; the model, its location and the
    temperature are ``PLATFORM_MODELS``'s. Raises what the call raises."""
    if not isinstance(feature, PlatformFeature):
        raise ModelConfigurationRefused()
    config, temperature = PLATFORM_MODELS[feature]
    llm = LLM(
        provider=config.provider,
        model_name=config.model_name,
        temperature=temperature,
        api_key=None,
    )
    logger.info("platform_model_call", feature=feature.value, model=config.model_name)
    content = llm._call_llm_through_gateway(
        messages,
        managed_transport=llm._requires_managed_transport(config.model_name),
    )
    if content is not None:
        return content
    response = guarded_completion(
        model=config.model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=llm.max_tokens,
        custom_llm_provider=config.provider,
        vertex_location=config.vertex_location,
        num_retries=LITELLM_NUM_RETRIES,
        retry_strategy=LITELLM_RETRY_STRATEGY,
        drop_params=True,
    )
    llm._update_token_usage(response)
    llm._update_cost(response)
    content = response.choices[0].message.content
    return content if content is not None else ""
