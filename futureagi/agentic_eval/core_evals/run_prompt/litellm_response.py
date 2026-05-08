import ast
import json
import os
import re
import time

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from model_hub.queries.tts_voices import resolve_voice_id

from agentic_eval.core.utils.json_utils import extract_dict_from_string

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.custom_models import CustomAIModel
from model_hub.queries.tts_voices import resolve_voice_id
from model_hub.utils import call_websocket
from django.core.cache import cache
from model_hub.utils.azure_endpoints import normalize_azure_custom_model_config
from model_hub.utils.utils import MyCustomLLM, convert_messages_to_text_only
from model_hub.utils.websocket_manager import get_websocket_manager
from channels.db import sync_to_async

import litellm
import av
import requests
import base64
import copy
import io

import structlog

logger = structlog.get_logger(__name__)

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.custom_models import CustomAIModel

from model_hub.utils import call_websocket
from django.core.cache import cache
from model_hub.utils.utils import convert_messages_to_text_only

from model_hub.utils.utils import convert_messages_to_text_only

from model_hub.utils.websocket_manager import get_websocket_manager
from tfc.utils.error_codes import get_error_message
try:
    from ee.usage.utils.usage_entries import count_tiktoken_tokens
except ImportError:
    from tfc.oss_stubs.usage import count_tiktoken_tokens
from tfc.utils.storage import upload_audio_to_s3, upload_image_to_s3
from agentic_eval.core_evals.run_prompt.other_services.manager import (
    OtherServicesManager,
)
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
# (available_models always available)
from tfc.utils.storage import (
    detect_audio_format,
    convert_to_mp3,
    get_audio_duration,
    audio_bytes_from_url_or_base64,
)
from agentic_eval.core_evals.run_prompt.other_services.elevenlabs_response import (
    elevenlabs_transcription_response,
)
from model_hub.utils.utils import get_model_mode
from agentic_eval.core_evals.run_prompt.error_handler import (
    handle_api_error,
    litellm_try_except,
)

# New handler imports for refactored implementation
from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
    ModelHandlerFactory,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
    LLMHandler,
)

# Var for quick hotfix if something breaks, will remove in future if everything works
USE_NEW_RUNPROMPT_HANDLERS = True


async def create_model_manager_async(model_name, organization_id):
    """
    Asynchronously creates and initializes a LiteLLMModelManager.
    """
    manager = LiteLLMModelManager(model_name)
    await sync_to_async(manager._add_custom_models)(organization_id)
    return manager


class RunPrompt:
    def __init__(
        self,
        model,
        messages,
        organization_id,
        output_format,
        temperature,
        frequency_penalty,
        presence_penalty,
        max_tokens,
        top_p,
        response_format,
        tool_choice,
        tools,
        ws_manager=None,
        run_prompt_config=None,
        workspace_id=None,
    ):
        self.model = model
        self.messages = messages
        self.run_prompt_config = run_prompt_config or {}
        # For all LLM params: use the direct value if provided, fall back to
        # run_prompt_config, and ultimately allow None (let litellm use its own defaults).
        self.temperature = (
            temperature
            if temperature is not None
            else self.run_prompt_config.get("temperature")
        )
        self.frequency_penalty = (
            frequency_penalty
            if frequency_penalty is not None
            else self.run_prompt_config.get("frequency_penalty")
        )
        self.presence_penalty = (
            presence_penalty
            if presence_penalty is not None
            else self.run_prompt_config.get("presence_penalty")
        )
        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else self.run_prompt_config.get("max_tokens")
        )
        self.top_p = top_p if top_p is not None else self.run_prompt_config.get("top_p")
        self.response_format = (
            response_format
            if response_format is not None
            else self.run_prompt_config.get("response_format")
        )
        self.tool_choice = tool_choice
        self.tools = tools
        self.output_format = output_format or "string"
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.ws_manager = ws_manager

        # Extract reasoning parameters from run_prompt_config
        # Note: Config can have either camelCase (from frontend/DB) or snake_case (from converted requests)
        reasoning_config = self.run_prompt_config.get("reasoning", {})

        # Handle both snake_case and camelCase for dropdowns
        dropdowns = reasoning_config.get("dropdowns", {})
        self.reasoning_effort = dropdowns.get("reasoning_effort") or dropdowns.get(
            "reasoningEffort"
        )

        # Handle both snake_case and camelCase for sliders
        sliders = reasoning_config.get("sliders", {})
        self.thinking_budget = sliders.get("thinking_budget") or sliders.get(
            "thinkingBudget"
        )

        # Handle both snake_case and camelCase for show_reasoning_process
        self.show_reasoning_process = reasoning_config.get(
            "show_reasoning_process"
        ) or reasoning_config.get("showReasoningProcess")

        logger.info(
            f"[RunPrompt Init] Reasoning config: reasoning_effort={self.reasoning_effort}, thinking_budget={self.thinking_budget}, show_reasoning_process={self.show_reasoning_process}"
        )

    def _retry_on_timeout(
        self, func, *args, max_retries=10, initial_delay=1, max_delay=10, **kwargs
    ):
        """
        Retry wrapper for API calls that may timeout.

        Args:
            func: The function to call
            *args: Positional arguments for the function
            max_retries: Maximum number of retry attempts (default: 3)
            initial_delay: Initial delay in seconds before first retry (default: 1)
            max_delay: Maximum delay in seconds between retries (default: 10)
            **kwargs: Keyword arguments for the function

        Returns:
            The result of the function call

        Raises:
            Exception: If all retries are exhausted
        """
        last_exception = None

        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (TimeoutError, requests.exceptions.Timeout) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    # Exponential backoff: delay = min(initial_delay * (2 ** attempt), max_delay)
                    delay = min(initial_delay * (2**attempt), max_delay)
                    logger.warning(
                        f"API timeout on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying in {delay} seconds. Error: {str(e)}"
                    )
                    time.sleep(delay)
                else:
                    # Last attempt - re-raise
                    logger.error(
                        f"All {max_retries} retry attempts exhausted for timeout"
                    )
                    raise
            except Exception as e:
                error_str = str(e).lower()
                is_timeout = (
                    "timeout" in error_str
                    or "timed out" in error_str
                    or "connection timeout" in error_str
                    or "read timeout" in error_str
                    or "too many requests" in error_str
                    or "429" in error_str
                    or "rate limit" in error_str
                )

                if is_timeout and attempt < max_retries - 1:
                    last_exception = e
                    delay = min(initial_delay * (2**attempt), max_delay)
                    logger.warning(
                        f"API timeout on attempt {attempt + 1}/{max_retries}. "
                        f"Retrying in {delay} seconds. Error: {str(e)}"
                    )
                    time.sleep(delay)
                else:
                    # Not a timeout or last attempt - re-raise immediately
                    raise

        # If we exhausted all retries, raise the last exception
        if last_exception:
            logger.error(f"All {max_retries} retry attempts exhausted for timeout")
            raise last_exception

    def _get_input_text_from_messages(self):
        """
        Extracts and concatenates text content from messages for TTS input.
        Assumes standard message format:
        [
            {"role": "user", "content": "Hello world"},
            {"role": "user", "content": [{"type": "text", "text": "Another message"}]}
        ]
        """
        text_parts = []
        for msg in self.messages:
            content = msg.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
        input_text = " ".join(text_parts)

        if not input_text.strip():
            raise ValueError("No text found in messages to generate speech.")

        # Validate length - OpenAI TTS has 4096 char limit, use conservative 4000
        max_length = 4000
        if len(input_text) > max_length:
            raise ValueError(
                f"Text too long for TTS: {len(input_text)} characters (max: {max_length})"
            )

        return input_text

    def _format_audio_output(self, audio_bytes, start_time, input_text):
        """
        Uploads the audio output to S3 and returns the URL.
        Handles both bytes and existing base64 strings.
        """

        # The upload_audio_to_s3 function can handle raw bytes directly.
        # If it's a string, we assume it's base64 and decode it first.
        if isinstance(audio_bytes, str):
            audio_data_to_upload = base64.b64decode(audio_bytes)
        else:
            audio_data_to_upload = audio_bytes

        # Estimate audio duration in seconds for token accounting (32 tokens/sec)
        duration_seconds = None
        try:
            with io.BytesIO(audio_data_to_upload) as _buf:
                container = av.open(_buf)
                if container.duration:
                    # container.duration is in microseconds
                    duration_seconds = container.duration / 1_000_000.0
                else:
                    audio_stream = container.streams.audio[0]
                    if audio_stream.duration and audio_stream.time_base:
                        duration_seconds = float(
                            audio_stream.duration * audio_stream.time_base
                        )
                    else:
                        # Fallback: decode frames and compute
                        total_frames = 0
                        for packet in container.demux(audio_stream):
                            for frame in packet.decode():
                                total_frames += frame.samples
                        duration_seconds = total_frames / float(audio_stream.rate)
                container.close()
        except Exception as e:
            logger.warning(f"Unable to estimate audio duration: {str(e)}")

        s3_url = upload_audio_to_s3(audio_data_to_upload)

        formatted_output = s3_url

        end_time = time.time()
        completion_time = end_time - start_time

        # Token usage: prompt (text) via tiktoken helper, completion (audio) via 32 tokens/sec
        try:
            prompt_tokens = count_tiktoken_tokens(input_text)
        except Exception:
            prompt_tokens = None
        completion_tokens = int(duration_seconds * 32) if duration_seconds else None
        total_tokens = (
            ((prompt_tokens or 0) + (completion_tokens or 0))
            if (prompt_tokens is not None or completion_tokens is not None)
            else None
        )

        # Build usage/cost
        usage_payload = {
            "prompt_tokens": (prompt_tokens or 0),
            "completion_tokens": (completion_tokens or 0),
            "input_characters": len(input_text),
        }
        try:
            cost_payload = (
                calculate_total_cost(self.model, usage_payload)
                if (prompt_tokens is not None and completion_tokens is not None)
                else {"total_cost": 0.0, "prompt_cost": 0.0, "completion_cost": 0.0}
            )
        except Exception:
            cost_payload = {
                "total_cost": 0.0,
                "prompt_cost": 0.0,
                "completion_cost": 0.0,
            }

        metadata = {
            "usage": {
                **usage_payload,
                "total_tokens": total_tokens,
                "input_characters": len(input_text),
            },
            "cost": cost_payload,
            "response_time": completion_time,
        }

        value_info = {
            "name": None,
            "data": {"response": formatted_output},
            "failure": None,
            "runtime": completion_time * 1000,  # Convert to milliseconds for runtime
            "model": self.model,
            "metrics": [],
            "metadata": metadata,
            "output": None,
        }
        return formatted_output, value_info

    def _speech_response(self, start_time, api_key):
        input_text = self._get_input_text_from_messages()

        speech_params = {
            "model": self.model,
            "input": input_text,
            "api_key": api_key,
        }

        # Provider-specific handling
        # Frontend sends: { "voice": "My Custom Voice", "voice_id": "resolved-provider-id" }
        # or { "voice": "alloy" } for system voices (no voice_id)
        if self.model.startswith("vertex_ai/"):
            # Vertex AI requires voice as a dict for TTS API. If a dict is provided, pass it; otherwise skip.
            cfg_voice = self.run_prompt_config.get("voice")
            if isinstance(cfg_voice, dict):
                speech_params["voice"] = cfg_voice
            # Support audioConfig if provided
            audio_config = self.run_prompt_config.get("audioConfig")
            if isinstance(audio_config, dict):
                speech_params["audioConfig"] = audio_config
        elif self.model.startswith("gemini/"):
            # Google AI Studio Gemini via speech bridge - allow simple string overrides
            # Use voice_id if present (resolved provider ID), otherwise use voice
            cfg_voice = self.run_prompt_config.get(
                "voice_id"
            ) or self.run_prompt_config.get("voice")
            if isinstance(cfg_voice, str):
                speech_params["voice"] = cfg_voice
            cfg_format = self.run_prompt_config.get("format")
            if isinstance(cfg_format, str):
                speech_params["format"] = cfg_format
        else:
            # OpenAI / others accept simple string voice/format
            # Use voice_id if present (resolved provider ID), otherwise use voice
            cfg_voice = self.run_prompt_config.get(
                "voice_id"
            ) or self.run_prompt_config.get("voice")
            if isinstance(cfg_voice, str):
                speech_params["voice"] = cfg_voice
            cfg_format = self.run_prompt_config.get("format")
            if isinstance(cfg_format, str):
                speech_params["format"] = cfg_format

        # Log TTS request for monitoring
        logger.info(
            f"TTS request initiated - model: {self.model}, input_length: {len(input_text)}, voice: {speech_params.get('voice', 'default')}"
        )

        audio_response = litellm.speech(**speech_params, drop_params=True)

        return self._format_audio_output(audio_response.content, start_time, input_text)

    def _parse_image_model_name(self, model_name: str) -> tuple:
        """
        Parse image model name to extract actual model and embedded parameters.

        Handles various formats:
        - OpenAI with prefixes: "256-x-256/dall-e-2", "hd/1024-x-1792/dall-e-3"
        - OpenAI without prefixes: "gpt-image-1", "dall-e-3"
        - Azure: "azure/standard/1024-x-1024/dall-e-3", "azure/gpt-image-1"
        - Vertex AI: "vertex_ai/imagen-3.0-generate-001"
        - Bedrock: "512-x-512/50-steps/stability.stable-diffusion-xl-v0", "bedrock/amazon.nova-canvas-v1:0"
        - Together AI: "together_ai/black-forest-labs/FLUX.1-schnell"
        - Replicate: "replicate/black-forest-labs/flux-schnell"

        Returns:
            tuple: (actual_model_name, extracted_params_dict)
        """
        import re

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

        extracted_params = {}
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

    def _image_generation_response(self, start_time, api_key, provider):
        """
        Handle image generation using litellm.image_generation.

        Extracts prompt from messages and calls the image generation API.
        Returns the generated image URL and metadata.
        """
        # Extract prompt from messages
        prompt = self._get_input_text_from_messages()

        # Parse model name to extract actual model and embedded parameters
        actual_model, model_params = self._parse_image_model_name(self.model)

        # Build image generation parameters
        image_params = {
            "model": actual_model,
            "prompt": prompt,
        }

        # Add API key based on provider
        if isinstance(api_key, dict):
            if "api_key" in api_key:
                image_params["api_key"] = api_key["api_key"]
            if "api_base" in api_key:
                image_params["api_base"] = api_key["api_base"]
        else:
            image_params["api_key"] = api_key

        # Get configuration from run_prompt_config
        config = self.run_prompt_config or {}

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
        logger.info(
            f"Image generation request - original: {self.model}, parsed: {actual_model}, "
            f"extracted_params: {model_params}, size: {image_params.get('size')}, "
            f"quality: {image_params.get('quality', 'standard')}, provider: {provider}"
        )

        # Call litellm image_generation
        try:
            response = litellm.image_generation(**image_params, drop_params=True)
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
            raise

        end_time = time.time()
        completion_time = end_time - start_time

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
                    logger.warning(
                        f"Failed to upload image URL to S3, using original URL: {e}"
                    )
                    image_url = image_data.url
            elif hasattr(image_data, "b64_json") and image_data.b64_json:
                # Upload base64 image to S3
                try:
                    image_url = upload_image_to_s3(image_data.b64_json)
                except Exception as e:
                    logger.warning(
                        f"Failed to upload base64 image to S3, using data URL: {e}"
                    )
                    image_url = f"data:image/png;base64,{image_data.b64_json}"

            if hasattr(image_data, "revised_prompt"):
                revised_prompt = image_data.revised_prompt

        formatted_output = image_url

        # Build usage/cost payload
        # Image generation typically doesn't have token counts, but has per-image cost
        # Determine quality for pricing - check model name and params
        quality = image_params.get("quality", "standard")
        if "hd" in self.model.lower():
            quality = "hd"
        elif "standard" in self.model.lower():
            quality = "standard"

        usage_payload = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "images_generated": image_params.get("n", 1),
            "quality": quality,
        }

        # Calculate cost based on model and quality
        # Use actual_model (parsed/stripped model name) for pricing lookup
        # since self.model may have prefixes like "hd/1024-x-1792/dall-e-3"
        try:
            cost_payload = calculate_total_cost(actual_model, usage_payload)
        except Exception as e:
            logger.warning(f"Failed to calculate image cost: {e}")
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

        value_info = {
            "name": None,
            "data": {"response": formatted_output},
            "failure": None,
            "runtime": completion_time * 1000,  # Convert to milliseconds for runtime
            "model": self.model,
            "metrics": [],
            "metadata": metadata,
            "output": None,
        }

        return formatted_output, value_info

    def _get_input_audio_from_messages(self):
        """
        Extracts audio data (URL or base64 string) from messages, handling
        the specific formats generated by the placeholder system or direct uploads.

        Standard Payloads:
        1. From placeholder replacement (e.g., in prompt templates):
           {"type": "input_audio", "input_audio": {"data": "<base64_string>"}}
        2. From direct file upload:
           {"type": "audio_url", "audioUrl": {"url": "<s3_or_public_url>"}}
        """
        logger.info(f"[STT] Searching for audio in messages")
        for msg in self.messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            for part in content:
                if not isinstance(part, dict):
                    continue

                part_type = part.get("type")

                # Case 1: From placeholder replacement -> {'type': 'input_audio', 'input_audio': {'data': '...'}}
                if part_type == "input_audio":
                    input_audio_payload = part.get("input_audio")
                    if (
                        isinstance(input_audio_payload, dict)
                        and "data" in input_audio_payload
                    ):
                        logger.info("[STT] Found audio data in 'input_audio' part.")
                        return input_audio_payload["data"]

                # Case 2: From direct upload -> {'type': 'audio_url', 'audioUrl': {'url': '...'}}
                if part_type == "audio_url":
                    audio_url_payload = part.get("audioUrl") or part.get("audio_url")
                    if (
                        isinstance(audio_url_payload, dict)
                        and "url" in audio_url_payload
                    ):
                        logger.info("[STT] Found audio URL in 'audio_url' part.")
                        return audio_url_payload["url"]

        logger.error(
            f"[STT] No audio input found in messages. Sample: {str(self.messages)[:500]}"
        )
        raise ValueError("No audio input found in messages for STT.")

    def _transcription_response(self, start_time, api_key):
        """Handles Speech-to-Text using litellm.transcription."""
        audio_input = self._get_input_audio_from_messages()
        try:
            if isinstance(audio_input, dict):
                logger.info(
                    f"[STT] Raw input: dict keys={list(audio_input.keys())}, has_data={'data' in audio_input and bool(audio_input.get('data'))}, has_url={'url' in audio_input}"
                )
            else:
                logger.info(
                    f"[STT] Raw input: type={type(audio_input).__name__}, preview={str(audio_input)[:200]}"
                )
        except Exception:
            pass

        # Normalize audio input using shared utility
        audio_bytes = audio_bytes_from_url_or_base64(audio_input)

        try:
            logger.info(f"[STT] Normalized bytes length={len(audio_bytes)}")
        except Exception:
            pass

        # Ensure OpenAI Whisper gets a recognized file extension; convert if needed
        try:
            detected = (detect_audio_format(audio_bytes) or "").lower()
        except Exception:
            detected = ""
        allowed = {
            "flac",
            "m4a",
            "mp3",
            "mp4",
            "mpeg",
            "mpga",
            "oga",
            "ogg",
            "wav",
            "webm",
        }
        ext_map = {"mpeg": "mp3", "mpga": "mp3"}

        file_ext = detected if detected in allowed else None
        if not file_ext:
            try:
                audio_bytes, _ = convert_to_mp3(audio_bytes)
                file_ext = "mp3"
            except Exception:
                # Fallback: still try with wav name
                file_ext = "wav"

        buf = io.BytesIO(audio_bytes)
        # Name attribute is used by openai multipart to infer content type
        buf.name = f"audio.{ext_map.get(file_ext, file_ext)}"
        buf.seek(0)

        response = litellm.transcription(
            model=self.model, file=buf, api_key=api_key, drop_params=True
        )

        end_time = time.time()
        completion_time = end_time - start_time

        # Get audio duration using shared utility
        duration_seconds = get_audio_duration(audio_bytes)

        metadata = {
            "usage": {
                "audio_seconds": duration_seconds,
            },
            "cost": calculate_total_cost(
                model_name=self.model, token_usage={"audio_seconds": duration_seconds}
            ),
            "response_time": completion_time,
        }

        value_info = {
            "name": None,
            "data": {"response": response.text},
            "failure": None,
            "runtime": completion_time * 1000,  # Convert to milliseconds for runtime
            "model": self.model,
            "metrics": [],
            "metadata": metadata,
            "output": None,
        }
        return response.text, value_info

    def get_formatted_output(self, response):
        content = response.choices[0].message.content

        if self.output_format == "array":
            try:
                # Try to parse the content as JSON array
                parsed_content = json.loads(content)
                if isinstance(parsed_content, list):
                    return parsed_content
            except json.JSONDecodeError:
                try:
                    # If JSON parsing fails, try evaluating as Python literal
                    import ast

                    parsed_content = ast.literal_eval(content)
                    if isinstance(parsed_content, list):
                        return parsed_content
                except (ValueError, SyntaxError):
                    # If both methods fail, try to convert it
                    return self.convert_to_format(content)

        elif self.output_format == "string":
            return content  # It's already a string

        elif self.output_format == "object":
            try:
                # Try to parse the content as JSON object
                parsed_content = extract_dict_from_string(content)
                if isinstance(parsed_content, dict):
                    return parsed_content
            except json.JSONDecodeError:
                # If it's not a JSON object, try to convert it
                return self.convert_to_format(content)

        elif self.output_format == "number":
            try:
                return float(content)
            except ValueError:
                # If it's not a number, try to convert it
                return self.convert_to_format(content)

        else:
            raise ValueError("Invalid output format specified")

    def convert_to_format(self, content):
        prompt = f"Convert the following content into a valid {self.output_format} format:\n\n{content}\n\nConverted {self.output_format}:"

        conversion_response = None
        with litellm_try_except():
            conversion_response = litellm.completion(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                api_key=self.api_key,
                drop_params=True,
            )

        converted_content = conversion_response.choices[0].message.content

        if self.output_format == "array":
            return json.loads(converted_content)
        elif self.output_format == "object":
            return json.loads(converted_content)
        elif self.output_format == "number":
            return float(converted_content)
        else:
            return converted_content

    def to_float(self, value, default=0.0):
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    def to_int(self, value, default=0):
        try:
            return int(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    def _extract_message_content_for_token_counting(self, messages):
        """
        Extract text content and image URLs from messages for token counting, handling different content structures
        including text, images, and other media types.
        Returns tuple of (text_content, image_urls) similar to eval_runner.
        """
        text_parts = []
        image_urls = []

        for msg in messages:
            content = msg.get("content", "")

            if isinstance(content, list):
                # Handle list content (e.g., [{"text": "hi", "type": "text"}])
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text" and "text" in part:
                            # Text content
                            text_parts.append(str(part["text"]))
                        elif part.get("type") == "image_url" and "image_url" in part:
                            # Image content - extract URL for proper token counting
                            image_url = part["image_url"]
                            if isinstance(image_url, dict) and "url" in image_url:
                                image_urls.append(image_url["url"])
                            else:
                                image_urls.append(str(image_url))
                        elif (
                            part.get("type") == "input_audio" and "input_audio" in part
                        ):
                            # Audio content - add placeholder for token counting
                            text_parts.append("[AUDIO_PLACEHOLDER]")
                        else:
                            # Other content types
                            text_parts.append(str(part))
                    else:
                        text_parts.append(str(part))

            elif isinstance(content, dict):
                # Handle dict content
                if content.get("type") == "text" and "text" in content:
                    text_parts.append(str(content["text"]))
                elif content.get("type") == "image_url" and "image_url" in content:
                    # Image content - extract URL for proper token counting
                    image_url = content["image_url"]
                    if isinstance(image_url, dict) and "url" in image_url:
                        image_urls.append(image_url["url"])
                    else:
                        image_urls.append(str(image_url))
                elif content.get("type") == "input_audio" and "input_audio" in content:
                    text_parts.append("[AUDIO_PLACEHOLDER]")
                else:
                    text_parts.append(str(content))
            else:
                # Handle string or other content types
                text_parts.append(str(content))

        return " ".join(text_parts), image_urls

    def _custom_model_response(self, payload, start_time):
        try:
            payload.pop("custom_llm_provider")
            api_key = payload.pop("api_key")
            url = api_key.pop("api_base")
            headers = api_key.pop("headers")

            response = requests.post(url, headers=headers, json=payload)
            response_content = response.text
            end_time = time.time()

            # Get custom model to calculate cost based on stored token costs

            try:
                custom_model = CustomAIModel.objects.get(
                    organization_id=self.organization_id,
                    user_model_id=self.model,
                    deleted=False,
                )

                # Extract message content for token counting
                prompt_text, image_urls = (
                    self._extract_message_content_for_token_counting(
                        payload.get("messages", [])
                    )
                )
                completion_text = response_content

                # Use tiktoken for accurate token counting (handles both text and images)
                estimated_prompt_tokens = count_tiktoken_tokens(prompt_text, image_urls)
                estimated_completion_tokens = count_tiktoken_tokens(completion_text)
                total_tokens = estimated_prompt_tokens + estimated_completion_tokens

                # Use calculate_total_cost with custom model pricing as fallback
                token_usage = {
                    "prompt_tokens": estimated_prompt_tokens,
                    "completion_tokens": estimated_completion_tokens,
                }

                fallback_pricing = {
                    "input_per_1M_tokens": custom_model.input_token_cost,
                    "output_per_1M_tokens": custom_model.output_token_cost,
                }

                cost_dict = calculate_total_cost(
                    model_name=self.model,
                    token_usage=token_usage,
                    fallback_pricing=fallback_pricing,
                )

                metadata = {
                    "usage": {
                        "completion_tokens": estimated_completion_tokens,
                        "prompt_tokens": estimated_prompt_tokens,
                        "total_tokens": total_tokens,
                    },
                    "cost": cost_dict,
                    "response_time": (end_time - start_time),
                }
            except CustomAIModel.DoesNotExist:
                # Fallback if custom model not found
                metadata = {
                    "usage": {
                        "completion_tokens": 0,
                        "prompt_tokens": 0,
                        "total_tokens": 0,
                    },
                    "cost": {
                        "total_cost": 0,
                        "prompt_cost": 0,
                        "completion_cost": 0,
                    },
                    "response_time": (end_time - start_time),
                }

            value_info = {
                "name": None,
                "data": {"response": response_content},
                "failure": None,
                "runtime": (end_time - start_time) * 1000,  # Convert to milliseconds
                "model": self.model,
                "metrics": [],
                "metadata": metadata,  # Insert metadata as constructed above
                "output": None,  # Adjust to actual evaluation if available
            }
            return response_content, value_info, "completed"
        except Exception as e:
            logger.exception(f"An error occurred: {str(e)}")
            return str(e), {}, "error"

    def _streaming_response(
        self, payload, template_id, version, index, max_index, run_type
    ):
        response_content = ""
        start_time = time.time()

        # Initialize WebSocket manager
        ws_manager = (
            self.ws_manager
            if self.ws_manager
            else get_websocket_manager(self.organization_id)
        )

        # Send started message
        ws_manager.send_started_message(
            template_id=str(template_id),
            version=version,
            result_index=index,
            num_results=max_index,
            output_format=self.output_format,
        )

        if (
            "custom_llm_provider" in payload
            and payload["custom_llm_provider"] == "custom"
        ):
            response_content, value_info, streaming_status = (
                self._custom_model_response(payload, start_time)
            )
            if streaming_status == "completed":
                ws_manager.send_running_message(
                    template_id=str(template_id),
                    version=version,
                    chunk=response_content,
                    chunk_pos=len(response_content),
                    result_index=index,
                    num_results=max_index,
                )
                ws_manager.send_completed_message(
                    template_id=str(template_id),
                    version=version,
                    result_index=index,
                    num_results=max_index,
                    metadata=value_info.get("metadata"),
                    output_format=self.output_format,
                )
            else:
                try:
                    ws_manager.send_error_message(
                        template_id=str(template_id),
                        version=version,
                        error=response_content,
                        result_index=index,
                        num_results=max_index,
                        output_format=self.output_format,
                    )
                except Exception as ws_error:
                    logger.error(
                        f"Failed to send WebSocket error message: {str(ws_error)}"
                    )

            return response_content, value_info

        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        # Buffer for accumulating chunks before sending
        chunk_buffer = ""
        try:
            response = None
            with litellm_try_except():
                response = litellm.completion(**payload, drop_params=True)

            tool_calls = []
            tool_calls_str = ""

            buffer_size = 0
            max_buffer_size = 60  # Maximum characters to buffer before sending
            last_sent_chunk_pos = -1
            thinking_started = False  # Track if we've sent <thinking> tag
            thinking_finished = False  # Track if we've sent </thinking> tag

            for i, chunk in enumerate(response):
                # Check for stop streaming BEFORE processing the chunk
                if ws_manager.is_streaming_stopped(str(template_id), version):
                    logger.info(
                        f"Streaming stopped for template {template_id}, version {version}"
                    )

                    # Send any remaining buffered chunks before stopping
                    if chunk_buffer:
                        ws_manager.send_running_message(
                            template_id=str(template_id),
                            version=version,
                            chunk=chunk_buffer,
                            chunk_pos=i,
                            result_index=index,
                            num_results=max_index,
                        )

                    # Send stopped message with partial response
                    ws_manager.send_stopped_message(
                        template_id=str(template_id),
                        version=version,
                        partial_response=response_content,
                        result_index=index,
                        num_results=max_index,
                        output_format=self.output_format,
                    )

                    # Clean up streaming data
                    ws_manager.cleanup_streaming_data(str(template_id), version)

                    # Return partial response
                    value_info = {
                        "name": None,
                        "data": {"response": response_content},
                        "failure": None,
                        "runtime": time.time() - start_time,
                        "model": chunk.model if hasattr(chunk, "model") else None,
                        "metrics": [],
                        "metadata": {},
                        "output": None,
                    }
                    return response_content, value_info

                if chunk.choices and chunk.choices[0].delta:
                    # Check for reasoning content in delta (for reasoning models like o1/o3)
                    delta = chunk.choices[0].delta

                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        reasoning_chunk = delta.reasoning_content

                        # Stream the thinking content if show_reasoning_process is True
                        if self.show_reasoning_process:
                            # Send <thinking> tag before first thinking chunk
                            if not thinking_started:
                                thinking_tag = "<thinking>\n"
                                response_content += thinking_tag
                                ws_manager.send_running_message(
                                    template_id=str(template_id),
                                    version=version,
                                    chunk=thinking_tag,
                                    chunk_pos=i,
                                    result_index=index,
                                    num_results=max_index,
                                )
                                thinking_started = True

                            # Stream the reasoning chunk
                            response_content += reasoning_chunk
                            ws_manager.send_running_message(
                                template_id=str(template_id),
                                version=version,
                                chunk=reasoning_chunk,
                                chunk_pos=i,
                                result_index=index,
                                num_results=max_index,
                            )

                    if chunk.choices[0].delta.content:
                        chunk_message = chunk.choices[0].delta.content
                        if chunk_message:
                            # If we were streaming thinking and now getting content, close the thinking tag
                            if (
                                thinking_started
                                and not thinking_finished
                                and self.show_reasoning_process
                            ):
                                closing_tag = "\n</thinking>\n\n"
                                response_content += closing_tag
                                # Send closing tag immediately (don't add to buffer to avoid double-send)
                                ws_manager.send_running_message(
                                    template_id=str(template_id),
                                    version=version,
                                    chunk=closing_tag,
                                    chunk_pos=i,
                                    result_index=index,
                                    num_results=max_index,
                                )
                                thinking_finished = True

                            response_content += chunk_message

                            # Add to buffer
                            chunk_buffer += chunk_message
                            buffer_size += len(chunk_message)

                            # Send buffered chunks when buffer is full or at end of stream
                            if buffer_size >= max_buffer_size:
                                # Send running message with buffered chunks
                                ws_manager.send_running_message(
                                    template_id=str(template_id),
                                    version=version,
                                    chunk=chunk_buffer,
                                    chunk_pos=i,
                                    result_index=index,
                                    num_results=max_index,
                                )

                                # Cache the current response
                                ws_manager.set_cached_response(
                                    template_id=str(template_id),
                                    version=version,
                                    index=index,
                                    response_data={
                                        "response": response_content,
                                        "error": None,
                                        "last_chunk_pos": i,
                                    },
                                )

                                # Reset buffer
                                chunk_buffer = ""
                                buffer_size = 0
                                last_sent_chunk_pos = i

                    elif self.tools and chunk.choices[0].delta.tool_calls:
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            if len(tool_calls) <= tool_call.index:
                                tool_calls.append(
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                )
                            tc = tool_calls[tool_call.index]

                            if tool_call.id:
                                tc["id"] += tool_call.id
                            if tool_call.function.name:
                                tc["function"]["name"] += tool_call.function.name
                            if tool_call.function.arguments:
                                tc["function"]["arguments"] += (
                                    tool_call.function.arguments
                                )

        except Exception as e:
            if "value must be a string" in str(e):
                raise Exception(str(e))

            # Build context for error logging
            context = {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "message_count": len(self.messages) if self.messages else 0,
                "output_format": self.output_format,
                "organization_id": self.organization_id,
                "workspace_id": self.workspace_id,
                "template_id": template_id,
            }

            # Use error handler for concise message and verbose logging
            concise_error = handle_api_error(e, logger, context)

            # Send any remaining buffered chunks before sending error
            if chunk_buffer:
                try:
                    ws_manager.send_running_message(
                        template_id=str(template_id),
                        version=version,
                        chunk=chunk_buffer,
                        chunk_pos=last_sent_chunk_pos + 1,
                        result_index=index,
                        num_results=max_index,
                    )
                except Exception as buffer_error:
                    logger.error(
                        f"Failed to send remaining buffered chunks: {str(buffer_error)}"
                    )

            # Send error message with concise format
            try:
                ws_manager.send_error_message(
                    template_id=str(template_id),
                    version=version,
                    error=concise_error,
                    result_index=index,
                    num_results=max_index,
                    output_format=self.output_format,
                )
            except Exception as ws_error:
                logger.error(f"Failed to send WebSocket error message: {str(ws_error)}")

            # Cache error
            try:
                ws_manager.set_cached_response(
                    template_id=str(template_id),
                    version=version,
                    index=index,
                    response_data={
                        "response": response_content,
                        "error": concise_error,
                    },
                )
            except Exception as cache_error:
                logger.error(f"Failed to cache error response: {str(cache_error)}")

            raise Exception(concise_error)

        # Send any remaining buffered chunks
        if chunk_buffer:
            ws_manager.send_running_message(
                template_id=str(template_id),
                version=version,
                chunk=chunk_buffer,
                chunk_pos=last_sent_chunk_pos + 1,
                result_index=index,
                num_results=max_index,
            )

        # Handle tool calls if any
        if tool_calls:
            tool_calls_str = json.dumps(tool_calls)
            response_content += tool_calls_str

            # Send final tool calls as a chunk
            ws_manager.send_running_message(
                template_id=str(template_id),
                version=version,
                chunk=tool_calls_str,
                chunk_pos=last_sent_chunk_pos + 2,
                result_index=index,
                num_results=max_index,
            )

        # Check for stop streaming before sending completed message
        if ws_manager.is_streaming_stopped(str(template_id), version):
            logger.info(
                f"Not sending completed/all_completed for stopped session {template_id}, version {version}"
            )
            return response_content, {
                "name": None,
                "data": {"response": response_content},
                "failure": None,
                "runtime": time.time() - start_time,
                "model": chunk.model
                if "chunk" in locals() and hasattr(chunk, "model")
                else None,
                "metrics": [],
                "metadata": {},
                "output": None,
            }

        end_time = time.time()
        metadata = {
            "usage": {
                "completion_tokens": chunk.usage.completion_tokens,
                "prompt_tokens": chunk.usage.prompt_tokens,
                "total_tokens": chunk.usage.total_tokens,
            },
            "cost": calculate_total_cost(chunk.model, dict(chunk.usage)),
            "response_time": end_time - start_time,
        }

        # Note: Thinking content is now streamed during the loop above, not appended here

        # Send completed message
        ws_manager.send_completed_message(
            template_id=str(template_id),
            version=version,
            result_index=index,
            num_results=max_index,
            metadata=metadata,
            output_format=self.output_format,
        )

        value_info = {
            "name": None,
            "data": {"response": response_content},
            "failure": None,
            "runtime": end_time - start_time,
            "model": chunk.model,
            "metrics": [],
            "metadata": metadata,
            "output": None,
        }

        return response_content, value_info

    def _regular_response(self, payload, start_time):
        if (
            "custom_llm_provider" in payload
            and payload["custom_llm_provider"] == "custom"
        ):
            response_content, value_info, _ = self._custom_model_response(
                payload, start_time
            )
            return response_content, value_info

        response = None
        with litellm_try_except():
            response = litellm.completion(**payload, drop_params=True)

        # Validate that response is not None
        if response is None:
            logger.warning(f"LLM returned None response for model {self.model}")
            raise Exception(
                "LLM returned None response. This may be due to API issues."
            )

        response_content = response.choices[0].message.content

        # Validate that response content is not empty (unless there are tool calls)
        has_tool_calls = self.tools and response.choices[0].message.tool_calls
        if not has_tool_calls:
            if response_content is None or (
                isinstance(response_content, str) and not response_content.strip()
            ):
                logger.warning(
                    f"LLM returned empty response content for model {self.model}"
                )
                raise Exception(
                    "LLM returned empty response content. This may be due to content filtering or API issues."
                )

        response_model = response.model
        completion_tokens = response.usage.completion_tokens
        prompt_tokens = response.usage.prompt_tokens
        total_tokens = response.usage.total_tokens
        end_time = time.time()
        completion_time = (end_time - start_time) * 1000

        # Extract and prepend reasoning/thinking content if present
        if self.show_reasoning_process:
            reasoning_text = None

            # Extract reasoning_content (available for all reasoning models)

            if hasattr(response.choices[0].message, "reasoning_content"):
                reasoning_text = response.choices[0].message.reasoning_content

            # Extract thinking_blocks (Anthropic-specific) and concatenate
            elif hasattr(response.choices[0].message, "thinking_blocks"):
                thinking_blocks = response.choices[0].message.thinking_blocks
                if thinking_blocks:
                    reasoning_text = "\n\n".join(
                        block.get("thinking", "") for block in thinking_blocks
                    )

            # Prepend reasoning content to response_content (for value_info only)
            if reasoning_text:
                response_content = (
                    f"<thinking>\n{reasoning_text}\n</thinking>\n\n{response_content}"
                )
            else:
                logger.warning(
                    "[Regular Thinking] No reasoning_text extracted despite show_reasoning_process=True"
                )

        # Step 2: Construct Metadata
        metadata = {
            "usage": {
                "completion_tokens": completion_tokens,
                "prompt_tokens": prompt_tokens,
                "total_tokens": total_tokens,
            },
            "cost": calculate_total_cost(response.model, dict(response.usage)),
            "response_time": completion_time,  # Assuming a static time or calculated elsewhere
        }

        if self.tools and response.choices[0].message.tool_calls:
            # Convert tool calls to a list of dictionaries
            tool_calls_list = [
                {
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in response.choices[0].message.tool_calls
            ]

            # Convert to JSON string
            tool_calls_str = json.dumps(tool_calls_list)
            response = tool_calls_str

        elif self.output_format:
            response = self.get_formatted_output(response)
        else:
            response = response.choices[0].message.content

        # Step 3: Construct Final Data Format
        value_info = {
            "name": None,
            "data": {"response": response_content},
            "failure": None,
            "runtime": completion_time,
            "model": response_model,
            "metrics": [],
            "metadata": metadata,  # Insert metadata as constructed above
            "output": None,  # Adjust to actual evaluation if available
        }

        return response, value_info

    def _create_payload(self, provider, api_key):
        """
        Creates the payload for litellm.completion, handling various parameters
        and provider-specific adjustments.

        Standard Tool/Function Payload (`tools` parameter):
        [
            {
                "type": "function",
                "function": {
                    "name": "function_name",
                    "description": "Function description",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "param1": {"type": "string", "description": "Param 1 description"},
                        },
                        "required": ["param1"]
                    }
                }
            }
        ]
        Example Payload (messages) with Image Input:
        When handling image inputs, the messages structure includes image_url content:

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Image Input_0 is given below:"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAACSQAAAOmCAYAAAD43QlT..."
                        }
                    },
                    {"type": "text", "text": " describe this"}
                ]
            }
        ]
        """
        response_format = None

        # Skip response_format processing for audio/image generation
        # These outputs don't need JSON formatting and adding JSON instructions would interfere
        # Check both output_format (user config) and model_mode (actual model type) for safety
        model_mode = get_model_mode(self.model)
        is_non_text_output = self.output_format in ("audio", "image") or model_mode in (
            "tts",
            "stt",
            "audio",
            "audio_transcription",
            "image_generation",
        )
        if self.response_format and not is_non_text_output:
            #'json_object', 'json_schema', and 'text'

            # Handle json_schema format (structured outputs)
            if isinstance(self.response_format, dict) and self.response_format.get(
                "schema"
            ):
                schema = self.response_format.get("schema", {})
                if schema and "additional_properties" in schema:
                    schema["additionalProperties"] = schema.pop("additional_properties")
                json_schema_obj = {
                    "name": self.response_format.get("name"),
                    "strict": True,
                    "schema": self.response_format.get("schema"),
                }
                response_format = {
                    "type": "json_schema",
                    "json_schema": json_schema_obj,
                }
            # Handle dict with explicit type
            elif (
                isinstance(self.response_format, dict)
                and "type" in self.response_format
            ):
                format_type = self.response_format["type"]
                if format_type in ("text", "json_object", "json_schema"):
                    response_format = {"type": format_type}
                else:
                    logger.error(
                        "invalid_response_format_type",
                        response_format=self.response_format,
                        format_type=format_type,
                        message="Invalid response_format type",
                    )
                    raise ValueError(f"Invalid response_format type '{format_type}'")
            # Handle string values (DEPRECATED - backward compatibility)
            elif isinstance(self.response_format, str):
                logger.warning(
                    "deprecated_response_format_string",
                    response_format=self.response_format,
                    message="String response_format is deprecated. Use dict format instead.",
                )
                response_format = {
                    "type": "text"
                    if self.response_format.lower() == "text"
                    else "json_object"
                }
            else:
                logger.error(
                    "invalid_response_format",
                    response_format=self.response_format,
                    message="Invalid response_format. Must be dict with 'type' or 'schema' key",
                )
                raise ValueError("Invalid response_format format")

        # OpenAI requires the word "json" in messages when using json_object response_format
        if response_format and response_format.get("type") == "json_object":
            # Extract text from messages - handle both string content and multimodal list content
            def extract_message_text(msg):
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    # Handle multimodal content like [{"type": "text", "text": "..."}]
                    return " ".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
                return ""

            messages_text = " ".join(
                extract_message_text(msg) for msg in self.messages
            ).lower()
            if "json" not in messages_text:
                # Prepend a system message with JSON instruction
                json_instruction = "You must respond with valid JSON."
                self.messages.insert(0, {"role": "system", "content": json_instruction})

        payload = {
            "messages": self.messages,
            "model": self.model,
            "temperature": float(self.temperature)
            if self.temperature is not None
            else None,
            "frequency_penalty": float(self.frequency_penalty)
            if self.frequency_penalty is not None
            else None,
            "presence_penalty": float(self.presence_penalty)
            if self.presence_penalty is not None
            else None,
            "max_tokens": int(self.max_tokens) if self.max_tokens is not None else None,
            "top_p": float(self.top_p) if self.top_p is not None else None,
            "response_format": response_format,
            "tools": self.tools or [],
            "tool_choice": self.tool_choice if self.tools else None,
        }
        payload = {k: v for k, v in payload.items() if v not in [None, [], {}, ""]}

        # Add reasoning parameters if present
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        # Add thinking parameter for models that support it (primarily Anthropic)
        # Note: litellm auto-maps reasoning_effort to thinking.budget_tokens for Anthropic
        # But we can also provide thinking.budget_tokens directly for finer control
        if self.thinking_budget:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": int(self.thinking_budget),
            }

        # Clamp max_tokens to model's maximum output tokens
        if "max_tokens" in payload:
            try:
                model_max = litellm.get_max_tokens(self.model)
                if model_max and payload["max_tokens"] > model_max:
                    logger.warning(
                        f"max_tokens ({payload['max_tokens']}) exceeds model limit "
                        f"({model_max}) for {self.model}, clamping to {model_max}"
                    )
                    payload["max_tokens"] = model_max
            except Exception:
                # Unknown model (e.g. custom) — skip validation, pass through
                pass

        # Handle models that require max_completion_tokens instead of max_tokens
        # OpenAI reasoning models have special parameter requirements:
        # - Must use max_completion_tokens instead of max_tokens
        # - Don't support: temperature, top_p, presence_penalty, frequency_penalty, logprobs, logit_bias
        reasoning_models = [
            # o1 series
            "o1",
            "o1-2024-12-17",
            "o1-mini",
            "o1-mini-2024-09-12",
            "o1-preview",
            "o1-preview-2024-09-12",
            "o1-pro",
            "o1-pro-2025-03-19",
            # o3 series
            "o3",
            "o3-2025-04-16",
            "o3-mini",
            "o3-mini-2025-01-31",
            "o3-pro",
            "o3-pro-2025-06-10",
            # o4 series
            "o4-mini",
            "o4-mini-2025-04-16",
            # GPT-5 series (all are reasoning models)
            "gpt-5",
            "gpt-5-2025-08-07",
            "gpt-5-mini",
            "gpt-5-mini-2025-08-07",
            "gpt-5-nano",
            "gpt-5-nano-2025-08-07",
            "gpt-5-chat-latest",
            "gpt-5-pro",
            "gpt-5-pro-2025-10-06",
            "gpt-5-codex",
            "gpt-5-codex-2025-09-01",
            # GPT-5.1 series
            "gpt-5.1",
            "gpt-5.1-2025-11-13",
            "gpt-5.1-chat-latest",
            "gpt-5.1-codex",
            "gpt-5.1-codex-mini",
            "gpt-5.1-codex-max",
            # GPT-5.2 series
            "gpt-5.2",
            "gpt-5.2-2025-12-11",
            "gpt-5.2-chat-latest",
            "gpt-5.2-pro",
            "gpt-5.2-pro-2025-12-11",
        ]

        # Strip provider prefix (azure/, bedrock/, openai/, etc.) for model matching
        # This allows Azure/Bedrock versions of reasoning models to be handled correctly
        model_name_for_check = self.model
        if "/" in self.model:
            # Handle prefixes like "azure/o3-mini", "bedrock/o1", "openai/gpt-5"
            model_name_for_check = self.model.split("/")[-1]

        if model_name_for_check in reasoning_models:
            if self.max_tokens is not None:
                payload["max_completion_tokens"] = int(self.max_tokens)
            payload.pop("max_tokens", None)
            payload.pop("frequency_penalty", None)
            payload.pop("presence_penalty", None)

            # OpenAI reasoning models don't support temperature/top_p
            # They only work with temperature=1 (the default)
            if payload.get("temperature") != 1.0:
                payload.pop("temperature", None)
                payload.pop("top_p", None)
                logger.info(
                    f"Temperature parameter removed for reasoning model {self.model} - only default value (1.0) is supported"
                )

        # Handle temperature restriction for Claude models (don't need max_completion_tokens change)
        claude_temperature_restricted = [
            "claude-haiku-4-5-20251001",
            "claude-opus-4-1-20250805",
            "claude-opus-4-5-20251101",
            "claude-sonnet-4-5-20250929",
        ]
        if model_name_for_check in claude_temperature_restricted:
            if payload.get("temperature") != 1.0:
                payload.pop("temperature", None)
                payload.pop("top_p", None)
                logger.info(
                    f"Temperature parameter removed for model {self.model} - only default value (1.0) is supported"
                )

        if isinstance(api_key, dict):
            normalized = None
            provider_for_payload = provider
            if provider == "azure":
                normalized = normalize_azure_custom_model_config(api_key)
                if normalized.get("azure_endpoint_type") == "foundry":
                    provider_for_payload = "azure_ai"

            if not provider_for_payload == "openai":
                payload["custom_llm_provider"] = provider_for_payload
            if provider_for_payload in ["bedrock", "azure", "openai", "azure_ai"]:
                if provider_for_payload == "azure":
                    # Normalize and filter Azure config - only pass litellm-supported fields
                    if normalized is None:
                        normalized = normalize_azure_custom_model_config(api_key)
                    payload.update(
                        {
                            "api_base": normalized["api_base"],
                            "api_version": normalized["api_version"],
                            "api_key": normalized["api_key"],
                        }
                    )
                elif provider_for_payload == "azure_ai":
                    if normalized is None:
                        normalized = normalize_azure_custom_model_config(api_key)
                    payload.update(
                        {
                            "api_base": normalized["api_base"],
                            "api_key": normalized["api_key"],
                        }
                    )
                    if isinstance(payload.get("model"), str) and not payload[
                        "model"
                    ].startswith("azure_ai/"):
                        payload["model"] = f"azure_ai/{payload['model']}"
                else:
                    payload.update(api_key)
                if provider_for_payload == "openai":
                    payload["model"] = "openai/" + payload["model"]
            elif provider_for_payload.startswith("vertex_ai"):
                vertex_location = api_key.get("location") if isinstance(api_key, dict) else None
                creds = {k: v for k, v in api_key.items() if k != "location"} if isinstance(api_key, dict) else api_key
                payload["vertex_credentials"] = json.dumps(creds)
                if vertex_location:
                    payload["vertex_location"] = vertex_location
            else:
                payload["api_key"] = api_key
        else:
            payload["api_key"] = api_key

        return payload

    def _litellm_response_old(
        self,
        streaming=False,
        template_id=None,
        version=None,
        index=None,
        max_index=None,
        run_type=None,
    ):
        """
        Original implementation of litellm_response.
        This is the OLD implementation that will be deprecated once new handlers are verified.
        """
        try:
            # Resolve voice_id if present in config (keep voice as display name)
            # Frontend sends: { "voice": "My Custom Voice", "voice_id": "uuid-here" }
            # or { "voice": "Clyde" } for system voices (no voice_id)
            if self.run_prompt_config and "voice_id" in self.run_prompt_config:
                # Resolve voice_id (UUID) to provider ID
                resolved_id = resolve_voice_id(self.run_prompt_config["voice_id"])
                self.run_prompt_config["voice_id"] = resolved_id
                # voice remains as the display name

            payload = {}
            model_manager = LiteLLMModelManager(
                self.model, organization_id=self.organization_id
            )
            provider = model_manager.get_provider(
                model_name=self.model, organization_id=self.organization_id
            )
            api_key = model_manager.get_api_key(
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
                provider=provider,
            )
            if isinstance(api_key, dict) and "key" in api_key:
                if "custom_provider" in api_key:
                    api_key.pop("custom_provider")

                elif "api_base" in api_key:
                    api_key["api_key"] = api_key.pop("key")
                else:
                    api_key = api_key.get("key")
            self.api_key = api_key

            # Get model mode using shared utility
            model_mode = get_model_mode(self.model)
            other_services_manager = OtherServicesManager()

            start_time = time.time()

            if model_mode == "stt":
                transcription_handler = (
                    other_services_manager.get_transcription_handler(provider)
                )
                if transcription_handler:
                    return self._retry_on_timeout(
                        transcription_handler, self, start_time, self.api_key
                    )
                return self._retry_on_timeout(
                    self._transcription_response, start_time, self.api_key
                )

            # Handle image generation models based on model mode
            if model_mode == "image_generation":
                logger.info(f"Image generation model detected: {self.model}")
                return self._retry_on_timeout(
                    self._image_generation_response, start_time, api_key, provider
                )

            payload = self._create_payload(provider, api_key)
            start_time = time.time()

            if self.output_format == "audio":
                speech_handler = other_services_manager.get_speech_handler(provider)
                if speech_handler:
                    return self._retry_on_timeout(
                        speech_handler, self, time.time(), api_key
                    )

                # Use completion for OpenAI audio-preview; prefer speech() for Gemini/Vertex when possible
                use_completion_for_tts = "gpt-4o-audio-preview" in self.model

                if use_completion_for_tts:
                    logger.info(
                        f"Using litellm.completion() for audio generation with model {self.model}"
                    )
                    try:
                        import copy
                        import base64

                        fallback_payload = copy.deepcopy(payload)
                        config = self.run_prompt_config or {}

                        if self.model.startswith("gemini/"):
                            # Gemini completion path: enforce modalities/audio, allow standard params, plain string messages
                            fallback_payload["modalities"] = ["audio"]
                            # Build audio object
                            # Use voice_id if present (resolved provider ID), otherwise use voice
                            voice_val = config.get("voice_id") or config.get(
                                "voice", "Kore"
                            )
                            fallback_payload["audio"] = {
                                "voice": voice_val
                                if isinstance(voice_val, str)
                                else "Kore",
                                "format": "pcm16",
                            }
                            # Strip OpenAI-only params
                            for k in (
                                "response_format",
                                "tools",
                                "tool_choice",
                                "functions",
                                "max_tokens",
                                "max_completion_tokens",
                                "frequency_penalty",
                                "presence_penalty",
                                "top_p",
                            ):
                                fallback_payload.pop(k, None)
                            # De-normalize messages to plain strings
                            for m in fallback_payload.get("messages", []):
                                content = m.get("content")
                                if isinstance(content, list):
                                    parts = [
                                        p.get("text", "")
                                        for p in content
                                        if isinstance(p, dict)
                                        and p.get("type") == "text"
                                    ]
                                    if parts:
                                        m["content"] = " ".join(parts)
                            # Explicitly allow params and drop unknowns
                            fallback_payload["allowed_openai_params"] = [
                                "audio",
                                "modalities",
                            ]
                        else:
                            # For OpenAI gpt-4o-audio-preview and similar
                            fallback_payload["modalities"] = config.get(
                                "modalities", ["text", "audio"]
                            )

                            # Check if audio config is nested or at top level
                            if "audio" in config and isinstance(config["audio"], dict):
                                fallback_payload["audio"] = config["audio"]
                            else:
                                # Use voice_id if present (resolved provider ID), otherwise use voice
                                voice_val = config.get("voice_id") or config.get(
                                    "voice", "alloy"
                                )
                                fallback_payload["audio"] = {
                                    "voice": voice_val,
                                    "format": config.get("format", "mp3"),
                                }

                            # Normalize messages for OpenAI multimodal models that expect content as a list
                            for message in fallback_payload.get("messages", []):
                                content = message.get("content")
                                if isinstance(content, str):
                                    message["content"] = [
                                        {"type": "text", "text": content}
                                    ]

                        completion_response = None
                        with litellm_try_except():
                            completion_response = litellm.completion(
                                **fallback_payload, drop_params=True
                            )

                        audio_data = completion_response.choices[0].message.audio.data
                        return self._format_audio_output(
                            audio_data, start_time, self._get_input_text_from_messages()
                        )
                    except Exception as completion_e:
                        logger.error(
                            f"TTS via litellm.completion() failed: {str(completion_e)}"
                        )
                        raise completion_e

                # For other models (OpenAI TTS, Azure TTS, Vertex TTS), try speech() first
                try:
                    logger.info(
                        f"Attempting audio generation via litellm.speech() for model {self.model}"
                    )
                    return self._speech_response(start_time, api_key)
                except Exception as e:
                    error_str = str(e).lower()
                    # Fallback only for OpenAI audio-preview models
                    if ("404" in error_str or "invalid url" in error_str) and (
                        "gpt-4o-audio-preview" in self.model
                    ):
                        logger.warning(
                            f"litellm.speech() failed for model {self.model}. "
                            f"Attempting fallback via litellm.completion(). Error: {str(e)}"
                        )
                        try:
                            import copy
                            import base64

                            fallback_payload = copy.deepcopy(payload)
                            config = self.run_prompt_config or {}

                            # For OpenAI gpt-4o-audio-preview and similar
                            fallback_payload["modalities"] = config.get(
                                "modalities", ["text", "audio"]
                            )

                            # Check if audio config is nested or at top level
                            if "audio" in config and isinstance(config["audio"], dict):
                                # Audio config is nested
                                fallback_payload["audio"] = config["audio"]
                            else:
                                # Audio config is at top level
                                # Use voice_id if present (resolved provider ID), otherwise use voice
                                voice_val = config.get("voice_id") or config.get(
                                    "voice", "alloy"
                                )
                                fallback_payload["audio"] = {
                                    "voice": voice_val,
                                    "format": config.get("format", "mp3"),
                                }

                            # Ensure multimodal format for OpenAI
                            for message in fallback_payload.get("messages", []):
                                if isinstance(message.get("content"), str):
                                    message["content"] = [
                                        {"type": "text", "text": message["content"]}
                                    ]

                            # Call completion endpoint
                            completion_response = None
                            with litellm_try_except():
                                completion_response = litellm.completion(
                                    **fallback_payload, drop_params=True
                                )

                            # Extract audio data from the correct location in the response (OpenAI)
                            audio_data = completion_response.choices[
                                0
                            ].message.audio.data

                            return self._format_audio_output(
                                audio_data,
                                start_time,
                                self._get_input_text_from_messages(),
                            )

                        except Exception as completion_e:
                            logger.error(
                                f"TTS fallback via litellm.completion() also failed: {str(completion_e)}"
                            )
                            raise completion_e
                    else:
                        # Re-raise original error if it's not a 404
                        raise e

            # Handle image generation
            if self.output_format == "image":
                logger.info(f"Image generation requested with model {self.model}")
                return self._retry_on_timeout(
                    self._image_generation_response, start_time, api_key, provider
                )

            if streaming:
                return self._streaming_response(
                    payload, template_id, version, index, max_index, run_type
                )
            else:
                return self._regular_response(payload, start_time)

        except Exception as e:
            # Build context for error logging
            context = {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "message_count": len(self.messages) if self.messages else 0,
                "output_format": self.output_format,
                "organization_id": self.organization_id,
                "workspace_id": self.workspace_id,
            }

            logger.info(f"Original messages: {payload.get('messages', [])}")

            # Try message conversion for "value must be a string" errors
            try:
                if "value must be a string" in str(e):
                    payload["messages"] = convert_messages_to_text_only(
                        payload["messages"]
                    )
                    logger.info(f"Converted messages: {payload['messages']}")
                    if streaming:
                        return self._streaming_response(
                            payload, template_id, version, index, max_index, run_type
                        )
                    else:
                        return self._regular_response(payload, start_time)
            except Exception as retry_error:
                # If retry fails, use error handler on the retry error
                concise_error = handle_api_error(retry_error, logger, context)
                raise Exception(concise_error)

            # Use error handler for concise message and verbose logging
            concise_error = handle_api_error(e, logger, context)
            raise Exception(concise_error)

    async def _litellm_response_async_old(
        self,
        streaming=False,
        template_id=None,
        version=None,
        index=None,
        max_index=None,
        type=None,
    ):
        """
        Original async implementation of litellm_response.
        This is the OLD implementation that will be deprecated once new handlers are verified.
        NOTE: Uses 'type' parameter (NOT 'run_type') - must preserve this for backward compatibility.
        """
        try:
            if self.run_prompt_config and "voice_id" in self.run_prompt_config:
                # Resolve voice_id (UUID) to provider ID
                resolved_id = await sync_to_async(resolve_voice_id)(
                    self.run_prompt_config["voice_id"]
                )
                self.run_prompt_config["voice_id"] = resolved_id

            # model_manager = await create_model_manager_async(self.model, self.organization_id)

            payload = {}
            model_manager = await sync_to_async(LiteLLMModelManager)(
                self.model, organization_id=self.organization_id
            )

            provider = await sync_to_async(model_manager.get_provider)(
                model_name=self.model, organization_id=self.organization_id
            )
            api_key = await sync_to_async(model_manager.get_api_key)(
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
                provider=provider,
            )
            if isinstance(api_key, dict) and "key" in api_key:
                if "custom_provider" in api_key:
                    api_key.pop("custom_provider")
                elif "api_base" in api_key:
                    api_key["api_key"] = api_key.pop("key")
                else:
                    api_key = api_key.get("key")
            self.api_key = api_key

            # Get model mode using shared utility
            model_mode = await sync_to_async(get_model_mode)(self.model)
            other_services_manager = OtherServicesManager()

            start_time = time.time()

            if model_mode == "stt" or model_mode == "audio_transcription":
                ws_manager = self.ws_manager
                if ws_manager:
                    await ws_manager.send_started_message(
                        template_id=str(template_id),
                        version=version,
                        result_index=index,
                        num_results=max_index,
                        output_format=self.output_format,
                    )

                try:
                    transcription_handler = await sync_to_async(
                        other_services_manager.get_transcription_handler
                    )(provider)

                    if transcription_handler:
                        response, value_info = await sync_to_async(
                            transcription_handler
                        )(self, start_time, self.api_key)
                    else:
                        response, value_info = await sync_to_async(
                            self._transcription_response
                        )(start_time, self.api_key)

                    # Stream the transcript in chunks (like LLM streaming)
                    if ws_manager and response:
                        transcript_text = response  # This is the transcript string
                        chunk_size = 60  # Characters per chunk
                        chunk_pos = 0

                        for i in range(0, len(transcript_text), chunk_size):
                            chunk = transcript_text[i : i + chunk_size]
                            await ws_manager.send_running_message(
                                template_id=str(template_id),
                                version=version,
                                chunk=chunk,
                                chunk_pos=chunk_pos,
                                result_index=index,
                                num_results=max_index,
                            )
                            chunk_pos += 1

                        # Send completed with metadata
                        await ws_manager.send_completed_message(
                            template_id=str(template_id),
                            version=version,
                            result_index=index,
                            num_results=max_index,
                            metadata=value_info.get("metadata", {}),
                            output_format=self.output_format,
                        )

                    return response, value_info

                except Exception as e:
                    if ws_manager:
                        await ws_manager.send_error_message(
                            template_id=str(template_id),
                            version=version,
                            error=str(e),
                            result_index=index,
                            num_results=max_index,
                            output_format=self.output_format,
                        )
                    raise

            # Handle image generation models based on model mode
            if model_mode == "image_generation":
                logger.info(f"Image generation model detected (async): {self.model}")

                ws_manager = self.ws_manager
                if ws_manager:
                    await ws_manager.send_started_message(
                        template_id=str(template_id),
                        version=version,
                        result_index=index,
                        num_results=max_index,
                        output_format="image",
                    )

                try:
                    response, value_info = await sync_to_async(self._retry_on_timeout)(
                        self._image_generation_response, start_time, api_key, provider
                    )

                    if ws_manager:
                        # Send the actual output (image URL) as a running message before completed
                        # This allows the frontend to receive the generated content
                        await ws_manager.send_running_message(
                            template_id=str(template_id),
                            version=version,
                            chunk=response,
                            chunk_pos=0,
                            result_index=index,
                            num_results=max_index,
                        )
                        await ws_manager.send_completed_message(
                            template_id=str(template_id),
                            version=version,
                            result_index=index,
                            num_results=max_index,
                            metadata=value_info.get("metadata", {}),
                            output_format="image",
                        )

                    return response, value_info

                except Exception as e:
                    if ws_manager:
                        await ws_manager.send_error_message(
                            template_id=str(template_id),
                            version=version,
                            error=str(e),
                            result_index=index,
                            num_results=max_index,
                            output_format="image",
                        )
                    raise

            payload = self._create_payload(provider, api_key)

            if self.output_format == "audio":
                ws_manager = self.ws_manager
                if ws_manager:
                    await ws_manager.send_started_message(
                        template_id=str(template_id),
                        version=version,
                        result_index=index,
                        num_results=max_index,
                        output_format="audio",
                    )

                speech_handler = other_services_manager.get_speech_handler(provider)
                if speech_handler:
                    try:
                        response, value_info = await sync_to_async(
                            self._retry_on_timeout
                        )(speech_handler, self, time.time(), api_key)
                        if ws_manager:
                            # Send the actual output (audio URL) as a running message before completed
                            # This allows the frontend to receive the generated content
                            await ws_manager.send_running_message(
                                template_id=str(template_id),
                                version=version,
                                chunk=response,
                                chunk_pos=0,
                                result_index=index,
                                num_results=max_index,
                            )
                            await ws_manager.send_completed_message(
                                template_id=str(template_id),
                                version=version,
                                result_index=index,
                                num_results=max_index,
                                metadata=value_info.get("metadata", {}),
                                output_format="audio",
                            )
                        return response, value_info
                    except Exception as e:
                        if ws_manager:
                            await ws_manager.send_error_message(
                                template_id=str(template_id),
                                version=version,
                                error=str(e),
                                result_index=index,
                                num_results=max_index,
                                output_format="audio",
                            )
                        raise

                # Use completion for OpenAI audio-preview; prefer speech() for Gemini/Vertex when possible
                use_completion_for_tts = "gpt-4o-audio-preview" in self.model

                if use_completion_for_tts:
                    logger.info(
                        f"Using litellm.completion() for audio generation with model {self.model}"
                    )
                    try:
                        import copy
                        import base64

                        fallback_payload = copy.deepcopy(payload)
                        config = self.run_prompt_config or {}

                        if self.model.startswith("gemini/"):
                            # Gemini completion path: enforce modalities/audio, allow standard params, plain string messages
                            fallback_payload["modalities"] = ["audio"]
                            # Build audio object
                            voice_val = config.get("voice_id") or config.get(
                                "voice", "Kore"
                            )
                            fallback_payload["audio"] = {
                                "voice": voice_val
                                if isinstance(voice_val, str)
                                else "Kore",
                                "format": "pcm16",
                            }
                            # Strip OpenAI-only params
                            for k in (
                                "response_format",
                                "tools",
                                "tool_choice",
                                "functions",
                                "max_tokens",
                                "max_completion_tokens",
                                "frequency_penalty",
                                "presence_penalty",
                                "top_p",
                            ):
                                fallback_payload.pop(k, None)
                            # De-normalize messages to plain strings
                            for m in fallback_payload.get("messages", []):
                                content = m.get("content")
                                if isinstance(content, list):
                                    parts = [
                                        p.get("text", "")
                                        for p in content
                                        if isinstance(p, dict)
                                        and p.get("type") == "text"
                                    ]
                                    if parts:
                                        m["content"] = " ".join(parts)
                            # Explicitly allow params and drop unknowns
                            fallback_payload["allowed_openai_params"] = [
                                "audio",
                                "modalities",
                            ]
                        else:
                            # For OpenAI gpt-4o-audio-preview and similar
                            fallback_payload["modalities"] = config.get(
                                "modalities", ["text", "audio"]
                            )

                            # Check if audio config is nested or at top level
                            if "audio" in config and isinstance(config["audio"], dict):
                                fallback_payload["audio"] = config["audio"]
                            else:
                                voice_val = config.get("voice_id") or config.get(
                                    "voice", "alloy"
                                )
                                fallback_payload["audio"] = {
                                    "voice": voice_val,
                                    "format": config.get("format", "mp3"),
                                }

                            # Normalize messages for OpenAI multimodal models that expect content as a list
                            for message in fallback_payload.get("messages", []):
                                content = message.get("content")
                                if isinstance(content, str):
                                    message["content"] = [
                                        {"type": "text", "text": content}
                                    ]

                        completion_response = await litellm.acompletion(
                            **fallback_payload, drop_params=True
                        )

                        audio_data = completion_response.choices[0].message.audio.data
                        response, value_info = await sync_to_async(
                            self._format_audio_output
                        )(audio_data, start_time, self._get_input_text_from_messages())
                        if ws_manager:
                            # Send the actual output (audio URL) as a running message before completed
                            await ws_manager.send_running_message(
                                template_id=str(template_id),
                                version=version,
                                chunk=response,
                                chunk_pos=0,
                                result_index=index,
                                num_results=max_index,
                            )
                            await ws_manager.send_completed_message(
                                template_id=str(template_id),
                                version=version,
                                result_index=index,
                                num_results=max_index,
                                metadata=value_info.get("metadata", {}),
                                output_format="audio",
                            )
                        return response, value_info
                    except Exception as completion_e:
                        logger.error(
                            f"TTS via litellm.completion() failed: {str(completion_e)}"
                        )
                        if ws_manager:
                            await ws_manager.send_error_message(
                                template_id=str(template_id),
                                version=version,
                                error=str(completion_e),
                                result_index=index,
                                num_results=max_index,
                                output_format="audio",
                            )
                        raise completion_e

                # For other models (OpenAI TTS, Azure TTS, Vertex TTS), try speech() first
                try:
                    logger.info(
                        f"Attempting audio generation via litellm.speech() for model {self.model}"
                    )
                    response, value_info = await sync_to_async(self._speech_response)(
                        start_time, api_key
                    )
                    if ws_manager:
                        # Send the actual output (audio URL) as a running message before completed
                        await ws_manager.send_running_message(
                            template_id=str(template_id),
                            version=version,
                            chunk=response,
                            chunk_pos=0,
                            result_index=index,
                            num_results=max_index,
                        )
                        await ws_manager.send_completed_message(
                            template_id=str(template_id),
                            version=version,
                            result_index=index,
                            num_results=max_index,
                            metadata=value_info.get("metadata", {}),
                            output_format="audio",
                        )
                    return response, value_info
                except Exception as e:
                    error_str = str(e).lower()
                    # Fallback only for OpenAI audio-preview models
                    if ("404" in error_str or "invalid url" in error_str) and (
                        "gpt-4o-audio-preview" in self.model
                    ):
                        logger.warning(
                            f"litellm.speech() failed for model {self.model}. "
                            f"Attempting fallback via litellm.completion(). Error: {str(e)}"
                        )
                        try:
                            import copy
                            import base64

                            fallback_payload = copy.deepcopy(payload)
                            config = self.run_prompt_config or {}

                            # For OpenAI gpt-4o-audio-preview and similar
                            fallback_payload["modalities"] = config.get(
                                "modalities", ["text", "audio"]
                            )

                            # Check if audio config is nested or at top level
                            if "audio" in config and isinstance(config["audio"], dict):
                                # Audio config is nested
                                fallback_payload["audio"] = config["audio"]
                            else:
                                # Audio config is at top level
                                voice_val = config.get("voice_id") or config.get(
                                    "voice", "alloy"
                                )
                                fallback_payload["audio"] = {
                                    "voice": voice_val,
                                    "format": config.get("format", "mp3"),
                                }

                            # Ensure multimodal format for OpenAI
                            for message in fallback_payload.get("messages", []):
                                if isinstance(message.get("content"), str):
                                    message["content"] = [
                                        {"type": "text", "text": message["content"]}
                                    ]

                            # Call completion endpoint
                            completion_response = await litellm.acompletion(
                                **fallback_payload, drop_params=True
                            )

                            # Extract audio data from the correct location in the response (OpenAI)
                            audio_data = completion_response.choices[
                                0
                            ].message.audio.data

                            response, value_info = await sync_to_async(
                                self._format_audio_output
                            )(
                                audio_data,
                                start_time,
                                self._get_input_text_from_messages(),
                            )
                            if ws_manager:
                                # Send the actual output (audio URL) as a running message before completed
                                await ws_manager.send_running_message(
                                    template_id=str(template_id),
                                    version=version,
                                    chunk=response,
                                    chunk_pos=0,
                                    result_index=index,
                                    num_results=max_index,
                                )
                                await ws_manager.send_completed_message(
                                    template_id=str(template_id),
                                    version=version,
                                    result_index=index,
                                    num_results=max_index,
                                    metadata=value_info.get("metadata", {}),
                                    output_format="audio",
                                )
                            return response, value_info

                        except Exception as completion_e:
                            logger.error(
                                f"TTS fallback via litellm.completion() also failed: {str(completion_e)}"
                            )
                            if ws_manager:
                                await ws_manager.send_error_message(
                                    template_id=str(template_id),
                                    version=version,
                                    error=str(completion_e),
                                    result_index=index,
                                    num_results=max_index,
                                    output_format="audio",
                                )
                            raise completion_e
                    else:
                        # Re-raise original error if it's not a 404
                        if ws_manager:
                            await ws_manager.send_error_message(
                                template_id=str(template_id),
                                version=version,
                                error=str(e),
                                result_index=index,
                                num_results=max_index,
                                output_format="audio",
                            )
                        raise e

            # Handle image generation
            if self.output_format == "image":
                logger.info(f"Image generation requested with model {self.model}")

                ws_manager = self.ws_manager
                if ws_manager:
                    await ws_manager.send_started_message(
                        template_id=str(template_id),
                        version=version,
                        result_index=index,
                        num_results=max_index,
                        output_format="image",
                    )

                try:
                    response, value_info = await sync_to_async(self._retry_on_timeout)(
                        self._image_generation_response, start_time, api_key, provider
                    )

                    if ws_manager:
                        # Send the actual output (image URL) as a running message before completed
                        # This allows the frontend to receive the generated content
                        await ws_manager.send_running_message(
                            template_id=str(template_id),
                            version=version,
                            chunk=response,
                            chunk_pos=0,
                            result_index=index,
                            num_results=max_index,
                        )
                        await ws_manager.send_completed_message(
                            template_id=str(template_id),
                            version=version,
                            result_index=index,
                            num_results=max_index,
                            metadata=value_info.get("metadata", {}),
                            output_format="image",
                        )

                    return response, value_info

                except Exception as e:
                    if ws_manager:
                        await ws_manager.send_error_message(
                            template_id=str(template_id),
                            version=version,
                            error=str(e),
                            result_index=index,
                            num_results=max_index,
                            output_format="image",
                        )
                    raise

            if streaming:
                return await self._streaming_response_async(
                    payload, template_id, version, index, max_index, type
                )
            else:
                return await sync_to_async(self._regular_response)(payload, start_time)
        except Exception as e:
            # Build context for error logging
            context = {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "message_count": len(self.messages) if self.messages else 0,
                "output_format": self.output_format,
                "organization_id": self.organization_id,
                "workspace_id": self.workspace_id,
            }

            logger.info(f"Original messages: {payload.get('messages', [])}")

            try:
                if "value must be a string" in str(e):
                    payload["messages"] = convert_messages_to_text_only(
                        payload["messages"]
                    )
                    logger.info(f"Converted messages: {payload['messages']}")
                    if streaming:
                        return await self._streaming_response_async(
                            payload, template_id, version, index, max_index, type
                        )
                    else:
                        return await sync_to_async(self._regular_response)(
                            payload, start_time
                        )
            except Exception as retry_error:
                concise_error = handle_api_error(retry_error, logger, context)
                raise Exception(concise_error)

            # Use error handler for concise message and verbose logging
            concise_error = handle_api_error(e, logger, context)
            raise Exception(concise_error)

    async def _streaming_response_async(
        self, payload, template_id, version, index, max_index, type
    ):
        response_content = ""
        start_time = time.time()

        ws_manager = self.ws_manager

        await ws_manager.send_started_message(
            template_id=str(template_id),
            version=version,
            result_index=index,
            num_results=max_index,
            output_format=self.output_format,
        )

        if (
            "custom_llm_provider" in payload
            and payload["custom_llm_provider"] == "custom"
        ):
            response_content, value_info, streaming_status = await sync_to_async(
                self._custom_model_response
            )(payload, start_time)
            if streaming_status == "completed":
                ws_manager.send_running_message(
                    template_id=str(template_id),
                    version=version,
                    chunk=response_content,
                    chunk_pos=len(response_content),
                    result_index=index,
                    num_results=max_index,
                )
                ws_manager.send_completed_message(
                    template_id=str(template_id),
                    version=version,
                    result_index=index,
                    num_results=max_index,
                    metadata=value_info.get("metadata"),
                    output_format=self.output_format,
                )
            else:
                try:
                    ws_manager.send_error_message(
                        template_id=str(template_id),
                        version=version,
                        error=response_content,
                        result_index=index,
                        num_results=max_index,
                        output_format=self.output_format,
                    )
                except Exception as ws_error:
                    logger.error(
                        f"Failed to send WebSocket error message: {str(ws_error)}"
                    )

            return response_content, value_info

        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        chunk_buffer = ""
        try:
            response = await litellm.acompletion(**payload, drop_params=True)
            tool_calls = []
            tool_calls_str = ""

            buffer_size = 0
            max_buffer_size = 60  # Maximum characters to buffer before sending
            last_sent_chunk_pos = -1

            i = 0
            thinking_started = False  # Track if we've sent <thinking> tag
            thinking_finished = False  # Track if we've sent </thinking> tag
            async for chunk in response:
                i += 1

                if await ws_manager.is_streaming_stopped(str(template_id), version):
                    logger.info(
                        f"Streaming stopped for template {template_id}, version {version}"
                    )

                    if chunk_buffer:
                        await ws_manager.send_running_message(
                            template_id=str(template_id),
                            version=version,
                            chunk=chunk_buffer,
                            chunk_pos=i,
                            result_index=index,
                            num_results=max_index,
                        )

                    await ws_manager.send_stopped_message(
                        template_id=str(template_id),
                        version=version,
                        partial_response=response_content,
                        result_index=index,
                        num_results=max_index,
                        output_format=self.output_format,
                    )

                    # Clean up streaming data
                    await ws_manager.cleanup_streaming_data(str(template_id), version)

                    # Return partial response
                    value_info = {
                        "name": None,
                        "data": {"response": response_content},
                        "failure": None,
                        "runtime": time.time() - start_time,
                        "model": chunk.model if hasattr(chunk, "model") else None,
                        "metrics": [],
                        "metadata": {},
                        "output": None,
                    }

                    return response_content, value_info

                if chunk.choices and chunk.choices[0].delta:
                    # Check for reasoning content in delta (for reasoning models like o1/o3)
                    delta = chunk.choices[0].delta

                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        reasoning_chunk = delta.reasoning_content

                        # Stream the thinking content if show_reasoning_process is True
                        if self.show_reasoning_process:
                            # Send <thinking> tag before first thinking chunk
                            if not thinking_started:
                                thinking_tag = "<thinking>\n"
                                response_content += thinking_tag
                                await ws_manager.send_running_message(
                                    template_id=str(template_id),
                                    version=version,
                                    chunk=thinking_tag,
                                    chunk_pos=i,
                                    result_index=index,
                                    num_results=max_index,
                                )
                                thinking_started = True

                            # Stream the reasoning chunk
                            response_content += reasoning_chunk
                            await ws_manager.send_running_message(
                                template_id=str(template_id),
                                version=version,
                                chunk=reasoning_chunk,
                                chunk_pos=i,
                                result_index=index,
                                num_results=max_index,
                            )

                    if chunk.choices[0].delta.content:
                        chunk_message = chunk.choices[0].delta.content
                        if chunk_message:
                            # If we were streaming thinking and now getting content, close the thinking tag
                            if (
                                thinking_started
                                and not thinking_finished
                                and self.show_reasoning_process
                            ):
                                closing_tag = "\n</thinking>\n\n"
                                response_content += closing_tag
                                # Send closing tag immediately (don't add to buffer to avoid double-send)
                                await ws_manager.send_running_message(
                                    template_id=str(template_id),
                                    version=version,
                                    chunk=closing_tag,
                                    chunk_pos=i,
                                    result_index=index,
                                    num_results=max_index,
                                )
                                thinking_finished = True

                            response_content += chunk_message

                        chunk_buffer += chunk_message
                        buffer_size += len(chunk_message)

                        if buffer_size >= max_buffer_size:
                            # Send running message with buffered chunks
                            await ws_manager.send_running_message(
                                template_id=str(template_id),
                                version=version,
                                chunk=chunk_buffer,
                                chunk_pos=i,
                                result_index=index,
                                num_results=max_index,
                            )

                            # Cache the current response
                            await ws_manager.set_cached_response(
                                template_id=str(template_id),
                                version=version,
                                index=index,
                                response_data={
                                    "response": response_content,
                                    "error": None,
                                    "last_chunk_pos": i,
                                },
                            )

                            # Reset buffer
                            chunk_buffer = ""
                            buffer_size = 0
                            last_sent_chunk_pos = i

                    elif self.tools and chunk.choices[0].delta.tool_calls:
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            if len(tool_calls) <= tool_call.index:
                                tool_calls.append(
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                )
                            tc = tool_calls[tool_call.index]

                            if tool_call.id:
                                tc["id"] += tool_call.id
                            if tool_call.function.name:
                                tc["function"]["name"] += tool_call.function.name
                            if tool_call.function.arguments:
                                tc["function"]["arguments"] += (
                                    tool_call.function.arguments
                                )

        except Exception as e:
            if "value must be a string" in str(e):
                raise Exception(str(e))

            # Build context for error logging
            context = {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "message_count": len(self.messages) if self.messages else 0,
                "output_format": self.output_format,
                "organization_id": self.organization_id,
                "workspace_id": self.workspace_id,
                "template_id": template_id,
            }

            # Use error handler for concise message and verbose logging
            concise_error = handle_api_error(e, logger, context)

            if chunk_buffer:
                try:
                    await ws_manager.send_running_message(
                        template_id=str(template_id),
                        version=version,
                        chunk=chunk_buffer,
                        chunk_pos=last_sent_chunk_pos + 1,
                        result_index=index,
                        num_results=max_index,
                    )
                except Exception as buffer_error:
                    logger.error(
                        f"Failed to send remaining buffered chunks: {str(buffer_error)}"
                    )

            try:
                await ws_manager.send_error_message(
                    template_id=str(template_id),
                    version=version,
                    error=concise_error,
                    result_index=index,
                    num_results=max_index,
                    output_format=self.output_format,
                )
            except Exception as error_error:
                logger.error(
                    f"Failed to send WebSocket error message: {str(error_error)}"
                )

            # Cache error
            try:
                await ws_manager.set_cached_response(
                    template_id=str(template_id),
                    version=version,
                    index=index,
                    response_data={
                        "response": response_content,
                        "error": concise_error,
                    },
                )
            except Exception as cache_error:
                logger.error(f"Failed to cache error response: {str(cache_error)}")

            raise Exception(concise_error)

        if chunk_buffer:
            await ws_manager.send_running_message(
                template_id=str(template_id),
                version=version,
                chunk=chunk_buffer,
                chunk_pos=last_sent_chunk_pos + 1,
                result_index=index,
                num_results=max_index,
            )

        # Handle tool calls if any
        if tool_calls:
            tool_calls_str = json.dumps(tool_calls)
            response_content += tool_calls_str

            # Send final tool calls as a chunk
            await ws_manager.send_running_message(
                template_id=str(template_id),
                version=version,
                chunk=tool_calls_str,
                chunk_pos=last_sent_chunk_pos + 2,
                result_index=index,
                num_results=max_index,
            )

        # Check for stop streaming before sending completed message
        if await ws_manager.is_streaming_stopped(str(template_id), version):
            logger.info(
                f"Not sending completed/all_completed for stopped session {template_id}, version {version}"
            )
            return response_content, {
                "name": None,
                "data": {"response": response_content},
                "failure": None,
                "runtime": time.time() - start_time,
                "model": chunk.model
                if "chunk" in locals() and hasattr(chunk, "model")
                else None,
                "metrics": [],
                "metadata": {},
                "output": None,
            }

        end_time = time.time()
        metadata = {
            "usage": {
                "completion_tokens": chunk.usage.completion_tokens,
                "prompt_tokens": chunk.usage.prompt_tokens,
                "total_tokens": chunk.usage.total_tokens,
            },
            "cost": calculate_total_cost(chunk.model, dict(chunk.usage)),
            "response_time": end_time - start_time,
        }

        # Note: Thinking content is now streamed during the loop above, not appended here

        await ws_manager.send_completed_message(
            template_id=str(template_id),
            version=version,
            result_index=index,
            num_results=max_index,
            metadata=metadata,
            output_format=self.output_format,
        )

        value_info = {
            "data": {"response": response_content},
            "metadata": metadata,
            "name": None,
            "failure": None,
            "runtime": end_time - start_time,
            "model": chunk.model,
            "metrics": [],
            "output": None,
        }

        return response_content, value_info

    # =========================================================================
    # NEW IMPLEMENTATION USING REFACTORED HANDLERS
    # =========================================================================

    def _litellm_response_new(
        self,
        streaming=False,
        template_id=None,
        version=None,
        index=None,
        max_index=None,
        run_type=None,
    ):
        """
        NEW implementation using refactored ModelHandler architecture.
        Uses Strategy Pattern with dedicated handlers for each model type.

        Enable via environment variable: USE_NEW_RUNPROMPT_HANDLERS=true
        """
        try:
            # Resolve voice_id if present in config (keep voice as display name)
            if self.run_prompt_config and "voice_id" in self.run_prompt_config:
                resolved_id = resolve_voice_id(self.run_prompt_config["voice_id"])
                self.run_prompt_config["voice_id"] = resolved_id

            # Setup model manager and get provider/API key
            model_manager = LiteLLMModelManager(
                self.model, organization_id=self.organization_id
            )
            provider = model_manager.get_provider(
                model_name=self.model, organization_id=self.organization_id
            )
            api_key = model_manager.get_api_key(
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
                provider=provider,
            )

            # Normalize API key format
            if isinstance(api_key, dict) and "key" in api_key:
                if "custom_provider" in api_key:
                    api_key.pop("custom_provider")
                elif "api_base" in api_key:
                    api_key["api_key"] = api_key.pop("key")
                else:
                    api_key = api_key.get("key")
            self.api_key = api_key

            # Create handler context
            context = ModelHandlerContext.from_run_prompt(
                run_prompt_instance=self,
                template_id=template_id,
                version=version,
                result_index=index,
                num_results=max_index,
                run_type=run_type,
                provider=provider,
                api_key=api_key,
            )

            # Get the appropriate handler for this model type
            handler = ModelHandlerFactory.create_handler(context)

            # Execute with handler
            # LLM handlers handle their own WebSocket streaming
            # Non-LLM handlers use WS lifecycle wrapper for streaming
            if streaming and isinstance(handler, LLMHandler):
                handler_response = handler.execute_sync(streaming=True)
            elif streaming:
                handler_response = handler.execute_with_ws_lifecycle_sync()
            else:
                handler_response = handler.execute_sync(streaming=False)

            # Raise if handler returned a failure so it propagates to frontend
            if handler_response.failure:
                raise Exception(handler_response.failure)

            # Convert to legacy format (response, value_info)
            return handler_response.to_value_info()

        except Exception as e:
            logger.error(f"[NEW] An error occurred: {str(e)}")
            raise Exception(str(e))

    async def _litellm_response_async_new(
        self,
        streaming=False,
        template_id=None,
        version=None,
        index=None,
        max_index=None,
        type=None,
    ):
        """
        NEW async implementation using refactored ModelHandler architecture.
        Uses Strategy Pattern with dedicated handlers for each model type.

        Enable via environment variable: USE_NEW_RUNPROMPT_HANDLERS=true

        NOTE: Uses 'type' parameter (NOT 'run_type') - must preserve this for backward compatibility.
        """
        try:
            # Resolve voice_id if present in config (keep voice as display name)
            if self.run_prompt_config and "voice_id" in self.run_prompt_config:
                resolved_id = await sync_to_async(resolve_voice_id)(
                    self.run_prompt_config["voice_id"]
                )
                self.run_prompt_config["voice_id"] = resolved_id

            # Setup model manager and get provider/API key (async)
            model_manager = await sync_to_async(LiteLLMModelManager)(
                self.model, organization_id=self.organization_id
            )
            provider = await sync_to_async(model_manager.get_provider)(
                model_name=self.model, organization_id=self.organization_id
            )
            api_key = await sync_to_async(model_manager.get_api_key)(
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
                provider=provider,
            )

            # Normalize API key format
            if isinstance(api_key, dict) and "key" in api_key:
                if "custom_provider" in api_key:
                    api_key.pop("custom_provider")
                elif "api_base" in api_key:
                    api_key["api_key"] = api_key.pop("key")
                else:
                    api_key = api_key.get("key")
            self.api_key = api_key

            # Create handler context
            # Note: Pass "type" as "run_type" for consistency in handler
            context = ModelHandlerContext.from_run_prompt(
                run_prompt_instance=self,
                template_id=template_id,
                version=version,
                result_index=index,
                num_results=max_index,
                run_type=type,  # Map "type" param to "run_type" for handler
                provider=provider,
                api_key=api_key,
            )

            # Get the appropriate handler for this model type
            handler = ModelHandlerFactory.create_handler(context)

            # Execute with handler (async)
            # LLM handlers handle their own WebSocket streaming
            # Non-LLM handlers use WS lifecycle wrapper for streaming
            if streaming and isinstance(handler, LLMHandler):
                handler_response = await handler.execute_async(streaming=True)
            elif streaming:
                handler_response = await handler.execute_with_ws_lifecycle_async()
            else:
                handler_response = await handler.execute_async(streaming=False)

            # Raise if handler returned a failure so it propagates to frontend
            if handler_response.failure:
                raise Exception(handler_response.failure)

            # Convert to legacy format (response, value_info)
            return handler_response.to_value_info()

        except Exception as e:
            logger.error(f"[NEW] An error occurred: {str(e)}")
            raise Exception(str(e))

    # =========================================================================
    # PUBLIC API - WRAPPER METHODS THAT TOGGLE BETWEEN OLD AND NEW
    # =========================================================================

    def litellm_response(
        self,
        streaming=False,
        template_id=None,
        version=None,
        index=None,
        max_index=None,
        run_type=None,
    ):
        """
        Execute LLM/TTS/STT request and return response.

        This is the PUBLIC API entry point. It toggles between the old and new
        implementation based on the USE_NEW_RUNPROMPT_HANDLERS environment variable.

        Default: Uses OLD implementation for safe rollout.
        Set USE_NEW_RUNPROMPT_HANDLERS=true to enable new handlers.

        Args:
            streaming: Whether to stream the response
            template_id: Template ID for WebSocket messages
            version: Version for WebSocket messages
            index: Result index for WebSocket messages
            max_index: Total number of results for WebSocket messages
            run_type: Type of run (for tracking)

        Returns:
            Tuple of (response, value_info)
        """
        logger.info(
            f"[RunPrompt] litellm_response called, USE_NEW_RUNPROMPT_HANDLERS={USE_NEW_RUNPROMPT_HANDLERS}",
            model=self.model,
            streaming=streaming,
        )

        if USE_NEW_RUNPROMPT_HANDLERS:
            logger.info(
                "[RunPrompt] Using NEW handler implementation",
                model=self.model,
                streaming=streaming,
                template_id=template_id,
            )
            return self._litellm_response_new(
                streaming=streaming,
                template_id=template_id,
                version=version,
                index=index,
                max_index=max_index,
                run_type=run_type,
            )
        else:
            return self._litellm_response_old(
                streaming=streaming,
                template_id=template_id,
                version=version,
                index=index,
                max_index=max_index,
                run_type=run_type,
            )

    async def litellm_response_async(
        self,
        streaming=False,
        template_id=None,
        version=None,
        index=None,
        max_index=None,
        type=None,
    ):
        """
        Execute LLM/TTS/STT request asynchronously and return response.

        This is the PUBLIC API entry point. It toggles between the old and new
        implementation based on the USE_NEW_RUNPROMPT_HANDLERS environment variable.

        Default: Uses OLD implementation for safe rollout.
        Set USE_NEW_RUNPROMPT_HANDLERS=true to enable new handlers.

        IMPORTANT: This method uses 'type' parameter (NOT 'run_type') for backward
        compatibility. Do not change this parameter name.

        Args:
            streaming: Whether to stream the response
            template_id: Template ID for WebSocket messages
            version: Version for WebSocket messages
            index: Result index for WebSocket messages
            max_index: Total number of results for WebSocket messages
            type: Type of run (for tracking) - NOTE: named 'type' not 'run_type'

        Returns:
            Tuple of (response, value_info)
        """
        logger.info(
            f"[RunPrompt] litellm_response_async called, USE_NEW_RUNPROMPT_HANDLERS={USE_NEW_RUNPROMPT_HANDLERS}",
            model=self.model,
            streaming=streaming,
        )

        if USE_NEW_RUNPROMPT_HANDLERS:
            logger.info(
                "[RunPrompt] Using NEW handler implementation (async)",
                model=self.model,
                streaming=streaming,
                template_id=template_id,
            )
            return await self._litellm_response_async_new(
                streaming=streaming,
                template_id=template_id,
                version=version,
                index=index,
                max_index=max_index,
                type=type,
            )
        else:
            return await self._litellm_response_async_old(
                streaming=streaming,
                template_id=template_id,
                version=version,
                index=index,
                max_index=max_index,
                type=type,
            )
