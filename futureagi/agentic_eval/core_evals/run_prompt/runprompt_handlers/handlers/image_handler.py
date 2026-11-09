"""
Image Handler for image generation models.

This handler implements sync and async execution for image generation:
- DALL-E 2, DALL-E 3, GPT-Image models (OpenAI)
- Imagen 3.0, Imagen 4.0 (Vertex AI)
- Stable Diffusion, Amazon Nova Canvas, Titan (Bedrock)
- FLUX models (Together AI, Replicate)

Features:
- Model name parsing for embedded size/quality/steps parameters
- S3 upload for generated images
- Cost calculation based on model and quality
- Retry on timeout with exponential backoff
"""

# Pre-existing lint debt, exempted file-wide so a security fix can touch this
# module behaviour-neutrally (import pruning/reordering, an unused local and
# typing rewrites).
# ruff: noqa: F401, F841, I001, UP006, UP035

import re
import time
from typing import Any, Dict, Optional, Tuple

import litellm
import structlog

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from model_hub.utils.model_endpoints import (
    guarded_image_generation,
    is_endpoint_error,
)
from tfc.utils.storage import upload_image_to_s3

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    HandlerResponse,
    ModelHandlerContext,
)

logger = structlog.get_logger(__name__)


class ImageHandler(BaseModelHandler):
    """
    Handler for image generation models.

    Supports all LiteLLM-compatible image generation providers including
    OpenAI DALL-E, Azure, Vertex AI Imagen, Bedrock Stable Diffusion,
    Together AI FLUX, and Replicate models.
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize image handler.

        Args:
            context: ModelHandlerContext with model configuration
        """
        super().__init__(context)
        self._validate_context()

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute image generation synchronously.

        Note: Image generation does not support streaming, so the streaming
        parameter is ignored.

        Args:
            streaming: Ignored for image generation

        Returns:
            HandlerResponse with generated image URL and metadata
        """
        start_time = time.time()

        return self._retry_on_timeout(
            self._image_generation_response,
            start_time,
        )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute image generation asynchronously.

        Note: Image generation does not support streaming, so the streaming
        parameter is ignored.

        Args:
            streaming: Ignored for image generation

        Returns:
            HandlerResponse with generated image URL and metadata
        """
        start_time = time.time()

        return await self._retry_on_timeout_async(
            self._image_generation_response,
            start_time,
        )

    # -------------------------------------------------------------------------
    # Model Name Parsing
    # -------------------------------------------------------------------------

    def _parse_image_model_name(self, model_name: str) -> Tuple[str, Dict[str, Any]]:
        """
        Parse image model name to extract actual model and embedded parameters.

        Handles various formats:
        - OpenAI with prefixes: "256-x-256/dall-e-2", "hd/1024-x-1792/dall-e-3"
        - OpenAI without prefixes: "gpt-image-1", "dall-e-3"
        - Azure: "azure/standard/1024-x-1024/dall-e-3", "azure/gpt-image-1"
        - Vertex AI: "vertex_ai/imagen-3.0-generate-001"
        - Bedrock: "512-x-512/50-steps/stability.stable-diffusion-xl-v0",
                   "bedrock/amazon.nova-canvas-v1:0"
        - Together AI: "together_ai/black-forest-labs/FLUX.1-schnell"
        - Replicate: "replicate/black-forest-labs/flux-schnell"

        Args:
            model_name: The model name potentially with embedded parameters

        Returns:
            Tuple of (actual_model_name, extracted_params_dict)
        """
        # Known provider prefixes that should be preserved
        provider_prefixes = (
            "azure/",
            "vertex_ai/",
            "bedrock/",
            "together_ai/",
            "replicate/",
        )

        # Patterns that indicate our custom prefixes (not part of actual model name)
        size_pattern = re.compile(
            r"^\d+-x-\d+$|^max-x-max$"
        )  # e.g., "256-x-256", "1024-x-1792", "max-x-max"
        quality_pattern = re.compile(r"^(hd|standard)$", re.IGNORECASE)
        steps_pattern = re.compile(
            r"^\d+-steps$|^max-steps$"
        )  # e.g., "50-steps", "max-steps"

        extracted_params: Dict[str, Any] = {}
        parts = model_name.split("/")
        actual_parts = []

        for part in parts:
            # Check if this part is a size prefix
            if size_pattern.match(part):
                # Convert "1024-x-1024" to "1024x1024"
                extracted_params["size"] = part.replace("-x-", "x")
                continue

            # Check if this part is a quality prefix
            if quality_pattern.match(part):
                extracted_params["quality"] = part.lower()
                continue

            # Check if this part is a steps prefix (for Bedrock Stable Diffusion)
            if steps_pattern.match(part):
                # Extract number of steps if specified
                if part != "max-steps":
                    extracted_params["steps"] = int(part.replace("-steps", ""))
                continue

            # This is part of the actual model name
            actual_parts.append(part)

        # Reconstruct the actual model name
        actual_model = "/".join(actual_parts) if actual_parts else model_name

        return actual_model, extracted_params

    # -------------------------------------------------------------------------
    # Image Generation
    # -------------------------------------------------------------------------

    def _image_generation_response(self, start_time: float) -> HandlerResponse:
        """
        Handle image generation using litellm.image_generation.

        Extracts prompt from messages and calls the image generation API.
        Uploads generated images to S3 and returns the URL with metadata.

        Args:
            start_time: Request start time for timing calculations

        Returns:
            HandlerResponse with image URL and metadata
        """
        # Extract prompt from messages
        prompt = self._extract_text_from_messages()

        # Parse model name to extract actual model and embedded parameters
        actual_model, model_params = self._parse_image_model_name(self.context.model)

        # Build image generation parameters
        image_params: Dict[str, Any] = {
            "model": actual_model,
            "prompt": prompt,
        }

        # Add API key based on provider
        api_key = self.context.api_key
        if isinstance(api_key, dict):
            if "api_key" in api_key:
                image_params["api_key"] = api_key["api_key"]
            if "api_base" in api_key:
                image_params["api_base"] = api_key["api_base"]
        elif api_key:
            image_params["api_key"] = api_key

        # Get configuration from run_prompt_config
        config = self.context.run_prompt_config or {}

        # Handle size parameter - priority: config > model_params (from model name) > default
        if "size" in config:
            image_params["size"] = config["size"]
        elif "size" in model_params:
            image_params["size"] = model_params["size"]
        else:
            # Default size for most models
            image_params["size"] = "1024x1024"

        # Handle quality parameter - priority: config > model_params > none
        if "quality" in config:
            image_params["quality"] = config["quality"]
        elif "quality" in model_params:
            image_params["quality"] = model_params["quality"]

        # Handle steps parameter (for Bedrock Stable Diffusion models)
        if "steps" in config:
            image_params["steps"] = config["steps"]
        elif "steps" in model_params:
            image_params["steps"] = model_params["steps"]

        # Handle style parameter (DALL-E 3)
        if "style" in config:
            image_params["style"] = config["style"]

        # Handle n parameter (number of images)
        if "n" in config:
            image_params["n"] = config["n"]
        else:
            image_params["n"] = 1

        # Log image generation request
        self.logger.info(
            "Image generation request",
            original_model=self.context.model,
            parsed_model=actual_model,
            extracted_params=model_params,
            size=image_params.get("size"),
            quality=image_params.get("quality", "standard"),
        )

        # Call litellm image_generation
        try:
            # A tenant api_base is called through a guarded client.
            response = guarded_image_generation(**image_params, drop_params=True)
        except Exception as e:
            if is_endpoint_error(e):
                # A tenant endpoint's error: generic already; logged without
                # text or traceback.
                self.logger.warning(
                    "Image generation failed",
                    error_type=type(e).__name__,
                    category=getattr(e, "category", None),
                )
            else:
                self.logger.exception("Image generation failed", error=str(e))
            raise

        # Validate response is not None
        self._validate_response_not_empty(response, response_type="Image generation")

        # Validate response has data
        if (
            not hasattr(response, "data")
            or not response.data
            or len(response.data) == 0
        ):
            self.logger.warning(
                "Image generation returned no images",
                model=self.context.model,
            )
            raise Exception(
                "Image generation returned no images. This may be due to content filtering or API issues."
            )

        end_time = time.time()
        completion_time = (end_time - start_time) * 1000

        # Extract image URL from response and upload to S3
        image_url = None
        revised_prompt = None
        if hasattr(response, "data") and len(response.data) > 0:
            image_data = response.data[0]
            if hasattr(image_data, "url") and image_data.url:
                # Upload provider URL to our S3 for consistency
                try:
                    image_url = upload_image_to_s3(image_data.url)
                except Exception as e:
                    self.logger.warning(
                        "Failed to upload image URL to S3, using original URL",
                        error=str(e),
                    )
                    image_url = image_data.url
            elif hasattr(image_data, "b64_json") and image_data.b64_json:
                # Upload base64 image to S3
                try:
                    image_url = upload_image_to_s3(image_data.b64_json)
                except Exception as e:
                    self.logger.warning(
                        "Failed to upload base64 image to S3, using data URL",
                        error=str(e),
                    )
                    image_url = f"data:image/png;base64,{image_data.b64_json}"

            if hasattr(image_data, "revised_prompt"):
                revised_prompt = image_data.revised_prompt

        # Validate that we successfully extracted an image URL
        self._validate_response_not_empty(
            image_url,
            response_type="Image generation",
        )

        # Build usage/cost payload
        # Image generation typically doesn't have token counts, but has per-image cost
        # Determine quality for pricing - check model name and params
        quality = image_params.get("quality", "standard")
        if "hd" in self.context.model.lower():
            quality = "hd"
        elif "standard" in self.context.model.lower():
            quality = "standard"

        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": image_params.get("n", 1),
            "quality": quality,
        }

        # Calculate cost based on model and quality
        # Use actual_model (parsed/stripped model name) for pricing lookup
        # since self.context.model may have prefixes like "hd/1024-x-1792/dall-e-3"
        try:
            cost_payload = calculate_total_cost(actual_model, usage_payload)
        except Exception as e:
            self.logger.warning("Failed to calculate image cost", error=str(e))
            # Fallback cost estimation for image models
            cost_per_image = 0.04  # Default DALL-E 3 standard cost
            actual_model_lower = actual_model.lower()
            if "hd" in actual_model_lower or quality == "hd":
                cost_per_image = 0.08
            elif "dall-e-2" in actual_model_lower:
                cost_per_image = 0.02
            elif "gpt-image" in actual_model_lower:
                cost_per_image = 0.04

            total_cost = cost_per_image * image_params.get("n", 1)
            cost_payload = {
                "total_cost": total_cost,
                "prompt_cost": 0.0,
                "completion_cost": total_cost,
            }

        metadata = {
            "usage": {
                **usage_payload,
                "total_tokens": 0,
            },
            "cost": cost_payload,
            "response_time": completion_time,
            "revised_prompt": revised_prompt,
            "size": image_params.get("size"),
            "quality": image_params.get("quality"),
            "style": image_params.get("style"),
        }

        return HandlerResponse(
            response=image_url,
            start_time=start_time,
            end_time=end_time,
            model=self.context.model,
            metadata=metadata,
        )
