import os
from typing import Any


def trust_remote_code_enabled() -> bool:
    return os.getenv("TRUST_REMOTE_CODE", "false").lower() == "true"

# ✅ IMPROVED: Model configurations with environment variable support
MODEL_NAME_TO_SENTENCE_TRANSFORMER_MODEL_MAPPING = {
    "embedding": os.getenv("DEFAULT_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
    "text_embedding": os.getenv("TEXT_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    "syn_data_embedding": os.getenv("SYN_DATA_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
}

# ✅ IMPROVED: Image model configurations
IMAGE_MODEL_CONFIGS = {
    "image_embedding": {
        "model_name": os.getenv("IMAGE_EMBEDDING_MODEL", "clip-ViT-B-32"),
        "model_type": "sentence_transformer",
    },
    "image_text_embedding": {
        "model_name": os.getenv(
            "IMAGE_TEXT_EMBEDDING_MODEL", "openai/clip-vit-base-patch32"
        ),
        "model_type": "transformers_clip",
    },
}

# ✅ IMPROVED: Audio model configurations
AUDIO_MODEL_CONFIGS = {
    "audio_embedding": {
        "model_name": os.getenv("AUDIO_EMBEDDING_MODEL", "wav2clip"),
        "model_type": "wav2clip",
    }
}

# ✅ NEW: Model loading timeouts and retry settings
MODEL_LOADING_CONFIG = {
    "timeout_seconds": int(os.getenv("MODEL_LOADING_TIMEOUT", "300")),
    "retry_attempts": int(os.getenv("MODEL_LOADING_RETRIES", "3")),
    "retry_delay_seconds": int(os.getenv("MODEL_LOADING_RETRY_DELAY", "5")),
}

# ✅ NEW: Memory management settings
MEMORY_CONFIG = {
    "max_models_in_memory": int(os.getenv("MAX_MODELS_IN_MEMORY", "5")),
    "model_unload_after_seconds": int(os.getenv("MODEL_UNLOAD_AFTER_SECONDS", "3600")),
    "enable_model_unloading": os.getenv("ENABLE_MODEL_UNLOADING", "false").lower()
    == "true",
}

# ✅ NEW: Request timeout settings
REQUEST_CONFIG = {
    "default_timeout_seconds": int(os.getenv("REQUEST_TIMEOUT", "30")),
    "max_batch_size": int(os.getenv("MAX_BATCH_SIZE", "32")),
    "enable_batching": os.getenv("ENABLE_BATCHING", "false").lower() == "true",
}

# ✅ NEW: Logging configuration
LOGGING_CONFIG = {
    "level": os.getenv("LOG_LEVEL", "INFO"),
    "format": os.getenv(
        "LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ),
    "enable_performance_logging": os.getenv(
        "ENABLE_PERFORMANCE_LOGGING", "true"
    ).lower()
    == "true",
}

# ✅ IMPROVED: Complete model configuration mapping
ALL_MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    # Text models
    "text_embedding": {
        "model_name": MODEL_NAME_TO_SENTENCE_TRANSFORMER_MODEL_MAPPING[
            "text_embedding"
        ],
        "model_type": "sentence_transformer",
        "input_types": ["text"],
        "description": "General purpose text embedding model",
    },
    "syn_data_embedding": {
        "model_name": MODEL_NAME_TO_SENTENCE_TRANSFORMER_MODEL_MAPPING[
            "syn_data_embedding"
        ],
        "model_type": "sentence_transformer",
        "input_types": ["text"],
        "description": "Synthetic data embedding model",
    },
    # Image models
    "image_embedding": {
        "model_name": IMAGE_MODEL_CONFIGS["image_embedding"]["model_name"],
        "model_type": IMAGE_MODEL_CONFIGS["image_embedding"]["model_type"],
        "input_types": ["image", "text"],
        "description": "CLIP-based image embedding model",
    },
    "image_text_embedding": {
        "model_name": IMAGE_MODEL_CONFIGS["image_text_embedding"]["model_name"],
        "model_type": IMAGE_MODEL_CONFIGS["image_text_embedding"]["model_type"],
        "input_types": ["image", "text"],
        "description": "Multimodal image-text embedding model",
    },
    # Audio models
    "audio_embedding": {
        "model_name": AUDIO_MODEL_CONFIGS["audio_embedding"]["model_name"],
        "model_type": AUDIO_MODEL_CONFIGS["audio_embedding"]["model_type"],
        "input_types": ["audio"],
        "description": "Audio embedding model using wav2clip",
    },
}


# ✅ IMPROVED: Model validation helpers
def validate_model_config(model_name: str) -> bool:
    """Validate that a model configuration is complete and valid."""
    if model_name not in ALL_MODEL_CONFIGS:
        return False

    config = ALL_MODEL_CONFIGS[model_name]
    required_fields = ["model_name", "model_type", "input_types", "description"]

    return all(field in config for field in required_fields)


def get_model_info(model_name: str) -> dict[str, Any]:
    """Get comprehensive information about a model."""
    if not validate_model_config(model_name):
        raise ValueError(f"Invalid model configuration for {model_name}")

    return ALL_MODEL_CONFIGS[model_name].copy()


def get_models_by_input_type(input_type: str) -> list[str]:
    """Get all models that support a specific input type."""
    return [
        model_name
        for model_name, config in ALL_MODEL_CONFIGS.items()
        if input_type in config.get("input_types", [])
    ]
