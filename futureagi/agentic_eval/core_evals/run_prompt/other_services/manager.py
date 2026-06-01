from .elevenlabs_response import (
    elevenlabs_speech_response,
    elevenlabs_transcription_response,
    get_elevenlabs_tts_parameters,
    get_elevenlabs_stt_parameters,
    validate_elevenlabs_voice,
)
from .deepgram_response import (
    deepgram_speech_response,
    get_deepgram_tts_parameters,
    deepgram_transcription_response,
    get_deepgram_flux_parameters,
)
from .inworld_response import inworld_speech_response, get_inworld_tts_parameters
from .rime_response import rime_speech_response, get_rime_tts_parameters
from .neuphonic_response import neuphonic_speech_response, get_neuphonic_tts_parameters
from .hume_response import hume_speech_response, get_hume_tts_parameters
from .cartesia_response import (
    cartesia_speech_response,
    get_cartesia_tts_parameters,
    validate_cartesia_voice,
)
from .lmnt_response import lmnt_speech_response, get_lmnt_tts_parameters
from .smallest_ai_response import (
    smallest_ai_speech_response,
    smallest_ai_transcription_response,
    get_smallest_ai_tts_parameters,
    get_smallest_ai_stt_parameters,
)
from agentic_eval.core_evals.run_prompt.error_handler import (
    ErrorContext,
    handle_api_error,
)
import structlog
from functools import wraps
import litellm

logger = structlog.get_logger(__name__)


def wrap_handler_with_error_handling(handler, provider_name):
    """
    Wraps a handler function with error handling.

    This ensures all service handlers (speech, transcription) return
    concise error messages while logging verbose details.
    """

    @wraps(handler)
    def wrapped_handler(run_prompt_instance, start_time, api_key, *args, **kwargs):
        try:
            return handler(run_prompt_instance, start_time, api_key, *args, **kwargs)
        except Exception as e:
            # Build context for error logging
            # Use getattr for compatibility with both RunPrompt and RunPromptAdapter
            context = ErrorContext(
                model=getattr(run_prompt_instance, "model", None),
                temperature=getattr(run_prompt_instance, "temperature", None),
                max_tokens=getattr(run_prompt_instance, "max_tokens", None),
                output_format=getattr(run_prompt_instance, "output_format", None),
                organization_id=getattr(run_prompt_instance, "organization_id", None),
                workspace_id=getattr(run_prompt_instance, "workspace_id", None),
                provider=provider_name,
            )

            # Use error handler for concise message and verbose logging
            concise_error = handle_api_error(e, logger, context)
            raise Exception(concise_error)

    return wrapped_handler


class OtherServicesManager:
    def __init__(self):
        # Wrap all handlers with error handling for concise error messages
        self.speech_handlers = {
            "elevenlabs": wrap_handler_with_error_handling(
                elevenlabs_speech_response, "ElevenLabs"
            ),
            "deepgram": wrap_handler_with_error_handling(
                deepgram_speech_response, "Deepgram"
            ),
            "inworld": wrap_handler_with_error_handling(
                inworld_speech_response, "Inworld"
            ),
            "rime": wrap_handler_with_error_handling(rime_speech_response, "Rime"),
            "neuphonic": wrap_handler_with_error_handling(
                neuphonic_speech_response, "Neuphonic"
            ),
            "hume": wrap_handler_with_error_handling(hume_speech_response, "Hume"),
            "cartesia": wrap_handler_with_error_handling(
                cartesia_speech_response, "Cartesia"
            ),
            "lmnt": wrap_handler_with_error_handling(lmnt_speech_response, "LMNT"),
            "smallest_ai": wrap_handler_with_error_handling(
                smallest_ai_speech_response, "Smallest AI"
            ),
        }
        self.transcription_handlers = {
            "elevenlabs": wrap_handler_with_error_handling(
                elevenlabs_transcription_response, "ElevenLabs"
            ),
            "deepgram": wrap_handler_with_error_handling(
                deepgram_transcription_response, "Deepgram"
            ),
            "smallest_ai": wrap_handler_with_error_handling(
                smallest_ai_transcription_response, "Smallest AI"
            ),
        }
        self.voice_validators = {
            "elevenlabs": validate_elevenlabs_voice,
            "cartesia": validate_cartesia_voice,
        }

    def get_speech_handler(self, provider):
        return self.speech_handlers.get(provider)

    def get_transcription_handler(self, provider):
        return self.transcription_handlers.get(provider)

    def get_voice_validator(self, provider):
        return self.voice_validators.get(provider)


def get_llm_parameters(model_name: str):
    """
    Returns parameters for LLM models, based on the project's supported API conventions.

    Defaults match common API defaults:
    - temperature: 1.0 (not 0.5) - more creative/varied responses
    - presence_penalty: 0.0 (not 1.0) - no topic repetition penalty
    - frequency_penalty: 0.0 (not 1.0) - no word repetition penalty
    - max_tokens: None - let model decide based on its limits
    - top_p: 1.0 - standard nucleus sampling

    NOTE: Penalty range is -2.0 to 2.0 (not 0.0 to 2.0).
    Negative values encourage repetition, positive values discourage it.
    """
    params = {
        "sliders": [
            {
                "label": "temperature",
                "min": 0.0,
                "max": 2.0,  # Supports 0.0 to 2.0 range
                "step": 0.1,
                "default": None,
            },
            {
                "label": "max_tokens",
                "min": 1,
                "max": 65536,
                "step": 1,
                "default": None,  # None = use provider default
            },
            {
                "label": "top_p",
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "default": None,  # Already correct
            },
            {
                "label": "presence_penalty",
                "min": -2.0,  # Fixed: Allow negative values (was 0.0)
                "max": 2.0,
                "step": 0.1,
                "default": None,
            },
            {
                "label": "frequency_penalty",
                "min": -2.0,  # Fixed: Allow negative values (was 0.0)
                "max": 2.0,
                "step": 0.1,
                "default": None,
            },
        ],
        "responseFormat": [
            {
                "value": "json",
            },
            {
                "value": "text",
            },
        ],
    }

    # Add reasoning parameters if the model supports reasoning
    try:
        if litellm.supports_reasoning(model_name):
            params["reasoning"] = {
                "dropdowns": [
                    {
                        "label": "reasoning_effort",
                        "options": [
                            "none",
                            "minimal",
                            "low",
                            "medium",
                            "high",
                            "xhigh",
                        ],
                        "default": "medium",
                        "description": "Controls the intensity of reasoning. Higher effort = more thorough analysis.",
                    },
                ],
                # "sliders": [
                #     {
                #         "label": "thinking_budget",
                #         "min": 1024,
                #         "max": 8192,
                #         "step": 512,
                #         "default": 2048,
                #         "description": "Maximum tokens for reasoning/thinking (Anthropic models). Higher = deeper reasoning.",
                #     },
                # ],
            }
            logger.info(
                f"Model {model_name} supports reasoning. Added reasoning parameters."
            )
    except Exception as e:
        # If litellm.supports_reasoning fails, log and continue without reasoning params
        logger.warning(f"Could not check reasoning support for {model_name}: {str(e)}")

    return params


def get_default_tts_parameters(model_name: str):
    """Returns default parameters for TTS models."""
    return {
        "sliders": [
            {
                "label": "temperature",
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "default": 0.5,
            },
        ],
    }


TTS_PARAMETER_HANDLERS = {
    "elevenlabs": get_elevenlabs_tts_parameters,
    "deepgram": get_deepgram_tts_parameters,
    "inworld": get_inworld_tts_parameters,
    "neuphonic": get_neuphonic_tts_parameters,
    "rime": get_rime_tts_parameters,
    "hume": get_hume_tts_parameters,
    "cartesia": get_cartesia_tts_parameters,
    "lmnt": get_lmnt_tts_parameters,
    "smallest_ai": get_smallest_ai_tts_parameters,
    "default": get_default_tts_parameters,
}


def get_default_stt_parameters(model_name: str):
    """Returns default parameters for STT models."""
    return {
        "sliders": [
            {
                "label": "temperature",
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "default": 0.0,
            },
        ],
    }


STT_PARAMETER_HANDLERS = {
    "elevenlabs": get_elevenlabs_stt_parameters,
    "smallest_ai": get_smallest_ai_stt_parameters,
    "default": get_default_stt_parameters,
}


# ================== IMAGE GENERATION PARAMETERS ==================


def get_openai_image_parameters(model_name: str):
    """Returns parameters for OpenAI image generation models (DALL-E, GPT-Image)."""
    # DALL-E 3 parameters
    if "dall-e-3" in model_name.lower():
        return {
            "dropdowns": [
                {
                    "label": "size",
                    "options": ["1024x1024", "1792x1024", "1024x1792"],
                    "default": "1024x1024",
                },
                {
                    "label": "quality",
                    "options": ["standard", "hd"],
                    "default": "standard",
                },
                {
                    "label": "style",
                    "options": ["vivid", "natural"],
                    "default": "vivid",
                },
            ],
        }
    # DALL-E 2 parameters
    elif "dall-e-2" in model_name.lower():
        return {
            "dropdowns": [
                {
                    "label": "size",
                    "options": ["256x256", "512x512", "1024x1024"],
                    "default": "1024x1024",
                },
            ],
            "sliders": [
                {
                    "label": "n",
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "default": 1,
                },
            ],
        }
    # GPT-Image-1 parameters
    elif "gpt-image" in model_name.lower():
        return {
            "dropdowns": [
                {
                    "label": "size",
                    "options": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                    "default": "auto",
                },
                {
                    "label": "quality",
                    "options": ["low", "medium", "high"],
                    "default": "high",
                },
                {
                    "label": "background",
                    "options": ["transparent", "opaque", "auto"],
                    "default": "auto",
                },
            ],
            "sliders": [
                {
                    "label": "n",
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "default": 1,
                },
            ],
        }
    # Default OpenAI image parameters
    return {
        "dropdowns": [
            {
                "label": "size",
                "options": ["1024x1024", "1792x1024", "1024x1792"],
                "default": "1024x1024",
            },
            {
                "label": "quality",
                "options": ["standard", "hd"],
                "default": "standard",
            },
        ],
    }


def get_stability_image_parameters(model_name: str):
    """Returns parameters for Stability AI image generation models (SD, FLUX)."""
    return {
        "dropdowns": [
            {
                "label": "aspect_ratio",
                "options": [
                    "1:1",
                    "16:9",
                    "21:9",
                    "2:3",
                    "3:2",
                    "4:5",
                    "5:4",
                    "9:16",
                    "9:21",
                ],
                "default": "1:1",
            },
            {
                "label": "output_format",
                "options": ["png", "jpeg", "webp"],
                "default": "png",
            },
        ],
        "sliders": [
            {
                "label": "cfg_scale",
                "min": 1.0,
                "max": 10.0,
                "step": 0.5,
                "default": 7.0,
            },
            {
                "label": "steps",
                "min": 1,
                "max": 50,
                "step": 1,
                "default": 30,
            },
            {
                "label": "seed",
                "min": 0,
                "max": 4294967294,
                "step": 1,
                "default": 0,
            },
        ],
    }


def get_together_image_parameters(model_name: str):
    """Returns parameters for Together AI image generation models."""
    return {
        "dropdowns": [
            {
                "label": "output_format",
                "options": ["jpeg", "png"],
                "default": "jpeg",
            },
        ],
        "sliders": [
            {
                "label": "width",
                "min": 256,
                "max": 1440,
                "step": 32,
                "default": 1024,
            },
            {
                "label": "height",
                "min": 256,
                "max": 1440,
                "step": 32,
                "default": 1024,
            },
            {
                "label": "guidance_scale",
                "min": 0.0,
                "max": 10.0,
                "step": 0.5,
                "default": 3.5,
            },
            {
                "label": "steps",
                "min": 1,
                "max": 50,
                "step": 1,
                "default": 20,
            },
            {
                "label": "n",
                "min": 1,
                "max": 4,
                "step": 1,
                "default": 1,
            },
            {
                "label": "seed",
                "min": 0,
                "max": 4294967294,
                "step": 1,
                "default": 0,
            },
        ],
    }


def get_replicate_image_parameters(model_name: str):
    """Returns parameters for Replicate image generation models."""
    # FLUX models on Replicate
    if "flux" in model_name.lower():
        return {
            "dropdowns": [
                {
                    "label": "aspect_ratio",
                    "options": [
                        "1:1",
                        "16:9",
                        "21:9",
                        "2:3",
                        "3:2",
                        "4:5",
                        "5:4",
                        "3:4",
                        "4:3",
                        "9:16",
                        "9:21",
                    ],
                    "default": "1:1",
                },
                {
                    "label": "output_format",
                    "options": ["webp", "jpg", "png"],
                    "default": "webp",
                },
            ],
            "sliders": [
                {
                    "label": "guidance",
                    "min": 0.0,
                    "max": 10.0,
                    "step": 0.5,
                    "default": 3.5,
                },
                {
                    "label": "num_inference_steps",
                    "min": 1,
                    "max": 50,
                    "step": 1,
                    "default": 28,
                },
                {
                    "label": "num_outputs",
                    "min": 1,
                    "max": 4,
                    "step": 1,
                    "default": 1,
                },
                {
                    "label": "output_quality",
                    "min": 0,
                    "max": 100,
                    "step": 1,
                    "default": 80,
                },
                {
                    "label": "seed",
                    "min": 0,
                    "max": 4294967294,
                    "step": 1,
                    "default": 0,
                },
            ],
        }
    # SDXL and other models on Replicate
    return {
        "sliders": [
            {
                "label": "width",
                "min": 512,
                "max": 1536,
                "step": 8,
                "default": 1024,
            },
            {
                "label": "height",
                "min": 512,
                "max": 1536,
                "step": 8,
                "default": 1024,
            },
            {
                "label": "guidance_scale",
                "min": 1.0,
                "max": 50.0,
                "step": 0.5,
                "default": 7.5,
            },
            {
                "label": "num_inference_steps",
                "min": 1,
                "max": 100,
                "step": 1,
                "default": 50,
            },
            {
                "label": "num_outputs",
                "min": 1,
                "max": 4,
                "step": 1,
                "default": 1,
            },
            {
                "label": "seed",
                "min": 0,
                "max": 4294967294,
                "step": 1,
                "default": 0,
            },
        ],
        "dropdowns": [
            {
                "label": "scheduler",
                "options": [
                    "DDIM",
                    "DPMSolverMultistep",
                    "HeunDiscrete",
                    "KarrasDPM",
                    "K_EULER_ANCESTRAL",
                    "K_EULER",
                    "PNDM",
                ],
                "default": "K_EULER",
            },
            {
                "label": "refine",
                "options": [
                    "no_refiner",
                    "expert_ensemble_refiner",
                    "base_image_refiner",
                ],
                "default": "no_refiner",
            },
        ],
    }


def get_bedrock_image_parameters(model_name: str):
    """Returns parameters for AWS Bedrock image generation models (Titan, Nova Canvas)."""
    # Nova Canvas parameters
    if "nova" in model_name.lower():
        return {
            "dropdowns": [
                {
                    "label": "quality",
                    "options": ["standard", "premium"],
                    "default": "standard",
                },
                {
                    "label": "style",
                    "options": [
                        "photorealism",
                        "3d_animated_family_film",
                        "design_sketch",
                        "flat_vector_illustration",
                        "graphic_novel_illustration",
                        "maximalism",
                        "midcentury_retro",
                        "soft_digital_painting",
                    ],
                    "default": "photorealism",
                },
            ],
            "sliders": [
                {
                    "label": "width",
                    "min": 320,
                    "max": 4096,
                    "step": 64,
                    "default": 1024,
                },
                {
                    "label": "height",
                    "min": 320,
                    "max": 4096,
                    "step": 64,
                    "default": 1024,
                },
                {
                    "label": "cfg_scale",
                    "min": 1.1,
                    "max": 10.0,
                    "step": 0.1,
                    "default": 6.5,
                },
                {
                    "label": "number_of_images",
                    "min": 1,
                    "max": 5,
                    "step": 1,
                    "default": 1,
                },
                {
                    "label": "seed",
                    "min": 0,
                    "max": 2147483646,
                    "step": 1,
                    "default": 0,
                },
            ],
        }
    # Titan Image Generator parameters
    return {
        "dropdowns": [
            {
                "label": "quality",
                "options": ["standard", "premium"],
                "default": "standard",
            },
        ],
        "sliders": [
            {
                "label": "width",
                "min": 512,
                "max": 1408,
                "step": 64,
                "default": 1024,
            },
            {
                "label": "height",
                "min": 512,
                "max": 1408,
                "step": 64,
                "default": 1024,
            },
            {
                "label": "cfg_scale",
                "min": 1.1,
                "max": 10.0,
                "step": 0.1,
                "default": 8.0,
            },
            {
                "label": "number_of_images",
                "min": 1,
                "max": 5,
                "step": 1,
                "default": 1,
            },
            {
                "label": "seed",
                "min": 0,
                "max": 2147483646,
                "step": 1,
                "default": 42,
            },
        ],
    }


def get_vertex_image_parameters(model_name: str):
    """Returns parameters for Google Vertex AI image generation models (Imagen)."""
    return {
        "dropdowns": [
            {
                "label": "aspect_ratio",
                "options": ["1:1", "3:4", "4:3", "9:16", "16:9"],
                "default": "1:1",
            },
            {
                "label": "sample_image_size",
                "options": ["1K", "2K"],
                "default": "1K",
            },
            {
                "label": "output_mime_type",
                "options": ["image/png", "image/jpeg"],
                "default": "image/png",
            },
        ],
        "sliders": [
            {
                "label": "sample_count",
                "min": 1,
                "max": 4,
                "step": 1,
                "default": 1,
            },
            {
                "label": "seed",
                "min": 1,
                "max": 2147483647,
                "step": 1,
                "default": 1,
            },
            {
                "label": "compression_quality",
                "min": 0,
                "max": 100,
                "step": 1,
                "default": 75,
            },
        ],
        "booleans": [
            {
                "label": "enhance_prompt",
                "default": True,
            },
            {
                "label": "add_watermark",
                "default": True,
            },
        ],
    }


def get_default_image_parameters(model_name: str):
    """Returns default parameters for image generation models."""
    return {
        "dropdowns": [
            {
                "label": "size",
                "options": ["512x512", "1024x1024", "1024x1792", "1792x1024"],
                "default": "1024x1024",
            },
            {
                "label": "quality",
                "options": ["standard", "hd"],
                "default": "standard",
            },
        ],
        "sliders": [
            {
                "label": "n",
                "min": 1,
                "max": 4,
                "step": 1,
                "default": 1,
            },
        ],
    }


IMAGE_PARAMETER_HANDLERS = {
    "openai": get_openai_image_parameters,
    "azure": get_openai_image_parameters,  # Azure uses OpenAI-compatible params
    "stability": get_stability_image_parameters,
    "stabilityai": get_stability_image_parameters,
    "together_ai": get_together_image_parameters,
    "replicate": get_replicate_image_parameters,
    "bedrock": get_bedrock_image_parameters,
    "vertex_ai": get_vertex_image_parameters,
    "vertex_ai_image_models": get_vertex_image_parameters,
    "default": get_default_image_parameters,
}


def get_model_parameters(provider: str, model_name: str, model_type: str):
    """
    Dispatcher function to get parameters for a given provider and model.
    """
    logger.info(
        f"Fetching parameters for provider: {provider}, model: {model_name}, type: {model_type}"
    )

    if model_type == "tts":
        handler = TTS_PARAMETER_HANDLERS.get(
            provider, TTS_PARAMETER_HANDLERS["default"]
        )
        return handler(model_name)
    elif model_type == "stt":
        if provider == "deepgram" and model_name == "flux-general-en":
            return get_deepgram_flux_parameters(model_name)
        handler = STT_PARAMETER_HANDLERS.get(
            provider, STT_PARAMETER_HANDLERS["default"]
        )
        return handler(model_name)
    elif model_type == "llm":
        return get_llm_parameters(model_name)
    elif model_type == "image":
        handler = IMAGE_PARAMETER_HANDLERS.get(
            provider, IMAGE_PARAMETER_HANDLERS["default"]
        )
        return handler(model_name)

    # Default to an empty dict if no specific handler is found
    return {}
