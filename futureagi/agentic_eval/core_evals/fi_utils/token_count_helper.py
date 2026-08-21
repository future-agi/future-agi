from typing import Any, Optional

import tiktoken
import structlog

logger = structlog.get_logger(__name__)

# Default pricing constants for fallback when pricing data is unavailable or invalid
DEFAULT_IMAGE_COST_PER_IMAGE = (
    0.04  # Default cost per image for image generation models
)

OPENAI_MODEL_ENCODINGS = {
    "gpt-3.5-turbo-0613": "cl100k_base",
    "gpt-3.5-turbo-16k-0613": "cl100k_base",
    "gpt-4-0613": "cl100k_base",
    "gpt-4-32k-0613": "cl100k_base",
    # chat
    "gpt-4o": "o200k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-3.5": "cl100k_base",  # Common shorthand
    "gpt-35-turbo": "cl100k_base",  # Azure deployment name
    # base
    "davinci-002": "cl100k_base",
    "babbage-002": "cl100k_base",
    # embeddings
    "text-embedding-ada-002": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
    # DEPRECATED MODELS
    # text (DEPRECATED)
    "text-davinci-003": "p50k_base",
    "text-davinci-002": "p50k_base",
    "text-davinci-001": "r50k_base",
    "text-curie-001": "r50k_base",
    "text-babbage-001": "r50k_base",
    "text-ada-001": "r50k_base",
    "davinci": "r50k_base",
    "curie": "r50k_base",
    "babbage": "r50k_base",
    "ada": "r50k_base",
    # code (DEPRECATED)
    "code-davinci-002": "p50k_base",
    "code-davinci-001": "p50k_base",
    "code-cushman-002": "p50k_base",
    "code-cushman-001": "p50k_base",
    "davinci-codex": "p50k_base",
    "cushman-codex": "p50k_base",
    # edit (DEPRECATED)
    "text-davinci-edit-001": "p50k_edit",
    "code-davinci-edit-001": "p50k_edit",
    # old embeddings (DEPRECATED)
    "text-similarity-davinci-001": "r50k_base",
    "text-similarity-curie-001": "r50k_base",
    "text-similarity-babbage-001": "r50k_base",
    "text-similarity-ada-001": "r50k_base",
    "text-search-davinci-doc-001": "r50k_base",
    "text-search-curie-doc-001": "r50k_base",
    "text-search-babbage-doc-001": "r50k_base",
    "text-search-ada-doc-001": "r50k_base",
    "code-search-babbage-code-001": "r50k_base",
    "code-search-ada-code-001": "r50k_base",
    # open source
    "gpt2": "gpt2",
    "gpt-2": "gpt2",  # Maintains consistency with gpt-4
}




def get_prompt_tokens_openai_chat_completion(
    prompt: list[dict[str, Any]], language_model_id: str
):
    """
    gets the prompt tokens given the prompt for the openai chat model completion
    """
    if prompt is None:
        raise ValueError("prompt is None")

    if language_model_id in OPENAI_MODEL_ENCODINGS:
        tokens_per_message = 3
        tokens_per_name = 1
    elif "gpt-3.5-turbo" in language_model_id:
        return get_prompt_tokens_openai_chat_completion(
            prompt=prompt, language_model_id="gpt-3.5-turbo-0613"
        )
    elif "gpt-4" in language_model_id:
        return get_prompt_tokens_openai_chat_completion(
            prompt=prompt, language_model_id="gpt-4-0613"
        )
    else:
        raise ValueError(f"Language model {language_model_id} is not supported")

    try:
        encoding = tiktoken.get_encoding(OPENAI_MODEL_ENCODINGS[language_model_id])
    except KeyError:
        return None

    try:
        num_tokens = 0
        for message in prompt:
            num_tokens += tokens_per_message
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":
                    num_tokens += tokens_per_name
        num_tokens += 3
        return num_tokens
    except Exception as e:
        raise e


def get_completion_tokens_openai_chat_completion(response: str, language_model_id: str):
    """
    gets the completion tokens given the prompt response from the openai chat model completion
    """
    if response is None:
        raise ValueError("response is None")
    try:
        if language_model_id in {
            "gpt-3.5-turbo-0613",
            "gpt-3.5-turbo-16k-0613",
            "gpt-4-0613",
            "gpt-4-32k-0613",
        }:
            encoding = tiktoken.get_encoding(OPENAI_MODEL_ENCODINGS[language_model_id])
        elif "gpt-3.5-turbo" in language_model_id:
            return get_completion_tokens_openai_chat_completion(
                response=response, language_model_id="gpt-3.5-turbo-0613"
            )
        elif "gpt-4" in language_model_id:
            return get_completion_tokens_openai_chat_completion(
                response=response, language_model_id="gpt-4-0613"
            )
        else:
            raise ValueError(f"Language model {language_model_id} is not supported")
    except KeyError:
        return None

    try:
        tokens = len(encoding.encode(response))
        return tokens
    except Exception as e:
        raise e


def get_token_usage_openai_completion(text: str, language_model_id: str):
    """
    gets the token usage given the text and language_model_id for openai completion
    """
    if text is None:
        raise ValueError("text is None")
    try:
        encoding = tiktoken.get_encoding(OPENAI_MODEL_ENCODINGS[language_model_id])
    except KeyError:
        return None

    try:
        tokens = len(encoding.encode(text))
        return tokens
    except Exception as e:
        raise e


# Default fallback pricing for different model types when model is not found
# in AVAILABLE_MODELS. These are conservative estimates based on common pricing.

# LLM/Chat models - token-based pricing (default)
DEFAULT_FALLBACK_PRICING = {"input_per_1M_tokens": 0.15, "output_per_1M_tokens": 0.60}

# Image generation models - per-image pricing
# Conservative default estimate (~$0.04 per image)
DEFAULT_IMAGE_FALLBACK_PRICING = {"per_image": 0.04}

# TTS (Text-to-Speech) models - character-based pricing
# Conservative default estimate (~$15 per 1M characters)
DEFAULT_TTS_FALLBACK_PRICING = {"input_per_1M_characters": 15.0}

# STT (Speech-to-Text) models - minute-based pricing
# Conservative default estimate (~$0.006 per minute)
DEFAULT_STT_FALLBACK_PRICING = {"input_per_minute": 0.006}


def calculate_total_cost(
    model_name: str, token_usage: dict, fallback_pricing: Optional[dict] = None
) -> dict:
    """
    Calculate total cost for a model inference using pricing from AVAILABLE_MODELS.

    Supports multiple pricing models:
    - LLM/Chat: Token-based pricing (input/output tokens)
    - TTS: Character-based pricing (input characters)
    - STT: Time-based pricing (audio seconds/minutes)

    Args:
        model_name: The model identifier (e.g., "gpt-4o", "tts-1", "whisper-1")
        token_usage: Dict with keys depending on model type:
            For LLM: {"prompt_tokens": int, "completion_tokens": int}
            For multimodal embeddings (e.g. titan-embed-image): additionally
                {"num_images": int} to bill the per-image input component
            For TTS: {"input_characters": int, "prompt_tokens": int, "completion_tokens": int}
            For STT: {"audio_seconds": float}
        fallback_pricing: Optional custom fallback pricing dict

    Returns:
        Dict with keys:
            - total_cost (float): Total cost in USD (rounded to 6 decimals)
            - prompt_cost (float): Input cost in USD (rounded to 6 decimals)
            - completion_cost (float): Output cost in USD (rounded to 6 decimals)
            - pricing_source (str): "available_models", "fallback", or "default"
    """
    from agentic_eval.core_evals.run_prompt.model_pricing import get_model_pricing

    # Try to get pricing from available_models.py
    pricing = get_model_pricing(model_name)
    pricing_source = "available_models"

    # Fallback chain
    if pricing is None:
        # Determine appropriate fallback based on token_usage fields
        if fallback_pricing:
            pricing = fallback_pricing
            fallback_type = "custom"
        elif "images_generated" in token_usage:
            # Image generation model
            pricing = DEFAULT_IMAGE_FALLBACK_PRICING
            fallback_type = "default_image"
        elif "input_characters" in token_usage:
            # TTS model
            pricing = DEFAULT_TTS_FALLBACK_PRICING
            fallback_type = "default_tts"
        elif "audio_seconds" in token_usage:
            # STT model
            pricing = DEFAULT_STT_FALLBACK_PRICING
            fallback_type = "default_stt"
        else:
            # Default to LLM token-based pricing
            pricing = DEFAULT_FALLBACK_PRICING
            fallback_type = "default"

        logger.warning(
            f"Model '{model_name}' not found in AVAILABLE_MODELS, using fallback pricing",
            model=model_name,
            fallback_type=fallback_type,
        )
        pricing_source = "fallback" if fallback_pricing else "default"

    # Detect pricing type and calculate accordingly
    prompt_cost = 0.0
    completion_cost = 0.0

    # Token-based pricing (LLM/Chat/embedding models). Embedding models are priced
    # with only `input_per_1M_tokens` (they produce no output tokens), so accept
    # either key and treat a missing side as zero. Requiring both keys made every
    # embedding model fall through to the "unknown pricing structure" branch and be
    # costed at $0. Multimodal embedding models (e.g. amazon.titan-embed-image-v1)
    # additionally carry `input_per_image`, so enter this branch for that key too
    # and add the per-image component below.
    if (
        "input_per_1M_tokens" in pricing
        or "output_per_1M_tokens" in pricing
        or "input_per_image" in pricing
    ):
        input_cost_per_1M = pricing.get("input_per_1M_tokens") or 0
        output_cost_per_1M = pricing.get("output_per_1M_tokens") or 0

        # Use `or 0` to handle both missing keys AND explicit None values
        prompt_tokens = token_usage.get("prompt_tokens") or 0
        completion_tokens = token_usage.get("completion_tokens") or 0

        prompt_cost = round((prompt_tokens / 1_000_000) * input_cost_per_1M, 6)
        completion_cost = round((completion_tokens / 1_000_000) * output_cost_per_1M, 6)

        # Per-image input pricing for multimodal embeddings. Titan multimodal
        # embeddings charge `input_per_image` per input image on top of any token
        # cost, so image-only requests are non-zero and mixed (text + image)
        # requests include both components. Text-only requests supply no image
        # count and are unaffected. `num_images` is the canonical key; fall back to
        # `images_generated` for callers that already report image counts that way.
        if "input_per_image" in pricing:
            cost_per_image = pricing.get("input_per_image") or 0
            num_images = (
                token_usage.get("num_images")
                or token_usage.get("images_generated")
                or 0
            )
            image_cost = round(num_images * cost_per_image, 6)
            prompt_cost = round(prompt_cost + image_cost, 6)

    # Character-based pricing (TTS models)
    elif "input_per_1M_characters" in pricing:
        input_cost_per_1M_chars = pricing["input_per_1M_characters"]
        input_characters = token_usage.get("input_characters") or 0

        prompt_cost = round((input_characters / 1_000_000) * input_cost_per_1M_chars, 6)
        completion_cost = 0.0  # TTS doesn't have completion cost

    # Minute-based pricing (STT models)
    elif "input_per_minute" in pricing:
        cost_per_minute = pricing["input_per_minute"]
        audio_seconds = token_usage.get("audio_seconds") or 0.0

        # Convert seconds to minutes
        audio_minutes = audio_seconds / 60.0
        prompt_cost = round(audio_minutes * cost_per_minute, 6)
        completion_cost = 0.0  # STT doesn't have completion cost

    # Image-based pricing (Image generation models)
    elif "per_image" in pricing or "low_quality_per_image" in pricing:
        images_generated = token_usage.get("images_generated") or 1
        quality = token_usage.get("quality") or "standard"

        # Handle quality-based pricing (gpt-image models)
        if "low_quality_per_image" in pricing:
            # Map quality names to pricing keys
            quality_map = {
                "low": "low_quality_per_image",
                "medium": "medium_quality_per_image",
                "high": "high_quality_per_image",
                # Default mappings for standard/hd
                "standard": "medium_quality_per_image",
                "hd": "high_quality_per_image",
            }
            pricing_key = quality_map.get(quality.lower(), "medium_quality_per_image")
            cost_per_image = pricing.get(
                pricing_key,
                pricing.get("medium_quality_per_image", DEFAULT_IMAGE_COST_PER_IMAGE),
            )
        else:
            # Simple per-image pricing (dall-e, imagen, flux, etc.)
            cost_per_image = pricing.get("per_image", DEFAULT_IMAGE_COST_PER_IMAGE)
            # Handle "N/A" or invalid pricing
            if not isinstance(cost_per_image, (int, float)):
                cost_per_image = DEFAULT_IMAGE_COST_PER_IMAGE

        total_image_cost = cost_per_image * images_generated
        prompt_cost = 0.0  # Image generation doesn't have prompt cost
        completion_cost = round(total_image_cost, 6)

    else:
        # Unknown pricing structure - log warning and return zero costs
        logger.warning(
            f"Unknown pricing structure for model '{model_name}': {pricing}",
            model=model_name,
            pricing=pricing,
        )
    return {
        "total_cost": round(prompt_cost + completion_cost, 6),
        "prompt_cost": prompt_cost,
        "completion_cost": completion_cost,
        "pricing_source": pricing_source,
    }
