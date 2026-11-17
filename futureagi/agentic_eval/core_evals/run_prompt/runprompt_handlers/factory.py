"""
Factory for creating the appropriate model handler based on context.

This implements the Strategy Pattern by routing to the correct handler
based on model type, output format, and other context attributes.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Type

import structlog

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    ModelHandlerContext,
)

logger = structlog.get_logger(__name__)

# The handlers that call a provider through litellm with only an api_key and
# api_base taken from the model's configuration.
_PROVIDER_MODES = ("stt", "image_generation")


def _off_the_event_loop(read):
    """``read()``; on a running event loop (the async Run Prompt path, where
    Django refuses the ORM) ``read()`` in a worker thread instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return read()

    def in_a_worker_thread():
        from django.db import connections

        try:
            return read()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(in_a_worker_thread).result()


class ModelHandlerFactory:
    """
    Factory for creating model handlers.

    This class determines the appropriate handler based on:
    1. Model mode (STT, TTS, etc.)
    2. Output format (audio, text, etc.)
    3. Custom model registration

    Adding new model types is simple:
    1. Create a new handler class extending BaseModelHandler
    2. Register it using register_handler() or add detection logic here
    """

    # Handler registry for custom registration
    _handler_registry: dict[str, Type[BaseModelHandler]] = {}

    @classmethod
    def register_handler(cls, mode: str, handler_class: Type[BaseModelHandler]):
        """
        Register a custom handler for a specific mode.

        Args:
            mode: Model mode identifier (e.g., "image", "video", "embedding")
            handler_class: Handler class to use for this mode
        """
        cls._handler_registry[mode] = handler_class
        logger.info(
            f"Registered handler for mode: {mode}", handler_class=handler_class.__name__
        )

    @classmethod
    def create_handler(cls, context: ModelHandlerContext) -> BaseModelHandler:
        """
        Create the appropriate handler for the given context.

        Priority order:
        0. Custom endpoint models (provider "custom") in the STT, image or
           audio-output modes below, on the sync and the async path
        1. STT models (model_mode == "stt")
        2. Image generation models (model_mode == "image_generation")
        3. TTS models (output_format == "audio")
        4. Custom models (registered in CustomAIModel)
        5. Registered custom handlers (via register_handler)
        6. Default: LLM (chat completion)

        Args:
            context: ModelHandlerContext with model and configuration

        Returns:
            Instantiated handler ready for execution

        Raises:
            ValueError: If context is invalid or handler cannot be determined
        """
        # Import here to avoid circular dependencies
        from model_hub.utils.utils import get_model_mode

        # Validate context
        if not context.model:
            raise ValueError("Model name is required in context")

        # Get model mode
        try:
            model_mode = get_model_mode(context.model)
        except Exception as e:
            logger.warning(f"Failed to get model mode, defaulting to LLM: {e}")
            model_mode = None

        logger.debug(
            "Determining handler",
            model=context.model,
            model_mode=model_mode,
            output_format=context.output_format,
        )

        # 0. A custom endpoint model named like a catalog speech-to-text, image
        # or audio-output model is served by its own endpoint: those handlers
        # call a provider with only an api_key and api_base from the
        # configuration, so a row with no key of its own would run on another
        # key. Read off the event loop, so the async path cannot skip it.
        provider_mode = (
            model_mode in _PROVIDER_MODES or context.output_format == "audio"
        )
        if provider_mode and _off_the_event_loop(
            lambda: cls._is_custom_model(
                context.model, context.organization_id, refuse_on_error=True
            )
        ):
            from .handlers.custom_model_handler import CustomModelHandler

            logger.debug("Using CustomModelHandler")
            return _off_the_event_loop(lambda: CustomModelHandler(context))

        # 1. STT models
        if model_mode == "stt":
            from .handlers.stt_handler import STTHandler

            logger.debug("Using STTHandler")
            return STTHandler(context)

        # 2. Image generation models
        if model_mode == "image_generation":
            from .handlers.image_handler import ImageHandler

            logger.debug("Using ImageHandler")
            return ImageHandler(context)

        # 3. TTS models (output_format == "audio")
        if context.output_format == "audio":
            from .handlers.tts_handler import TTSHandler

            logger.debug("Using TTSHandler")
            return TTSHandler(context)

        # 4. Custom models (check if registered as CustomAIModel)
        if cls._is_custom_model(context.model, context.organization_id):
            from .handlers.custom_model_handler import CustomModelHandler

            logger.debug("Using CustomModelHandler")
            return CustomModelHandler(context)

        # 5. Check registry for custom handlers
        if model_mode and model_mode in cls._handler_registry:
            handler_class = cls._handler_registry[model_mode]
            logger.debug(f"Using registered handler: {handler_class.__name__}")
            return handler_class(context)

        # 6. Default: LLM (chat completion)
        from .handlers.llm_handler import LLMHandler

        logger.debug("Using LLMHandler (default)")
        return LLMHandler(context)

    @staticmethod
    def _is_custom_model(
        model: str, organization_id: str, *, refuse_on_error: bool = False
    ) -> bool:
        """
        Check if model is a truly custom HTTP-endpoint model (provider="custom").

        Models stored in CustomAIModel with known LiteLLM providers (bedrock,
        vertex_ai, azure, etc.) should be handled by LLMHandler via LiteLLM,
        not CustomModelHandler (which requires an endpoint_url).

        Args:
            model: Model name (user_model_id in database)
            organization_id: Organization ID
            refuse_on_error: Refuse unexpected lookup errors in provider modes.

        Returns:
            True only if model is a custom HTTP-endpoint model (provider="custom")
        """
        try:
            from model_hub.models.custom_models import CustomAIModel

            try:
                custom_model = CustomAIModel.objects.get(
                    user_model_id=model,
                    organization_id=organization_id,
                    deleted=False,
                )
            except CustomAIModel.DoesNotExist:
                return False
            return custom_model.provider == "custom"
        except Exception:
            if refuse_on_error:
                # A failed lookup is not proof that catalog routing is safe.
                # Keep database details out of the caller's refusal.
                raise ValueError(
                    "Could not check the model configuration. Try again."
                ) from None
            # Preserve the legacy chat lookup's behavior outside step 0.
            return False
