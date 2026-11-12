import hashlib
import hmac
import secrets
import threading
import time

import structlog
from app.config.settings import MODEL_NAME_TO_CLASS_MAPPING, SUPPORTED_MODELS

logger = structlog.get_logger(__name__)

# Request kwargs that may appear verbatim in a model's cache key. The key is
# logged (load, cache hit, failure, cleanup, unload), returned in the 503
# detail and listed by the usage helpers, so every other kwarg, such as a
# customer's api_key, is replaced by a keyed digest: different credentials
# still get different cached clients, but no credential is ever written out.
_PLAIN_KEY_KWARGS = frozenset({"model"})
# Per process: cache keys never leave this process, and a digest in a log
# line cannot be checked against a guessed key.
_KEY_DIGEST_SECRET = secrets.token_bytes(32)


def _cache_key_part(name: str, value: object) -> str:
    if name in _PLAIN_KEY_KWARGS:
        return f"{name}={value}"
    digest = hmac.new(
        _KEY_DIGEST_SECRET, str(value).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{name}=hmac-sha256:{digest}"


class ModelLoader:
    """
    Model loader with lazy loading and proper error handling.
    Only loads models when they are requested, not all at startup.
    """

    _models: dict[str, object] = {}
    _loading_locks: dict[str, threading.Lock] = {}
    _global_lock = threading.Lock()
    _model_last_used: dict[str, float] = {}  # Track when each model was last used
    _cleanup_interval = 30 * 60  # 30 minutes in seconds
    _cleanup_thread: threading.Thread | None = None
    _stop_cleanup = threading.Event()

    @classmethod
    def _get_or_create_lock(cls, model_name: str) -> threading.Lock:
        """Get or create a lock for a specific model."""
        if model_name not in cls._loading_locks:
            with cls._global_lock:
                if model_name not in cls._loading_locks:
                    cls._loading_locks[model_name] = threading.Lock()
        return cls._loading_locks[model_name]

    @classmethod
    def _cleanup_idle_models(cls):
        """Remove models that haven't been used for the cleanup interval."""
        current_time = time.time()
        models_to_remove = []

        for model_key, last_used in cls._model_last_used.items():
            if current_time - last_used > cls._cleanup_interval:
                models_to_remove.append(model_key)

        for model_key in models_to_remove:
            with cls._get_or_create_lock(model_key):
                if model_key in cls._models:
                    del cls._models[model_key]
                    del cls._model_last_used[model_key]
                    logger.info(f"Auto-removed idle model: {model_key}")

    @classmethod
    def _cleanup_worker(cls):
        """Background worker that periodically cleans up idle models."""
        while not cls._stop_cleanup.wait(60):  # Check every minute
            try:
                cls._cleanup_idle_models()
            except Exception as e:
                logger.error(f"Error in cleanup worker: {e}")

    @classmethod
    def _start_cleanup_thread(cls):
        """Start the background cleanup thread if not already running."""
        if cls._cleanup_thread is None or not cls._cleanup_thread.is_alive():
            cls._stop_cleanup.clear()
            cls._cleanup_thread = threading.Thread(
                target=cls._cleanup_worker, daemon=True
            )
            cls._cleanup_thread.start()
            logger.info("Started model cleanup thread")

    @classmethod
    def _update_model_usage(cls, model_key: str):
        """Update the last used timestamp for a model."""
        cls._model_last_used[model_key] = time.time()

    @classmethod
    def load_models(cls):
        """
        ✅ OPTIMIZED: This method is now a no-op.
        Models are loaded lazily when requested.
        """
        logger.info("Model loader initialized. Models will be loaded on demand.")
        cls._start_cleanup_thread()

    @classmethod
    def get_model(cls, model_name: str, **kwargs) -> object | None:
        """
        ✅ FIXED: Get a model with lazy loading and proper error handling.

        Args:
            model_name: The name of the model to load

        Returns:
            The loaded model instance

        Raises:
            ValueError: If model is not supported
            RuntimeError: If model loading fails
        """
        # Validate model name
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(
                f"Model '{model_name}' is not supported. Supported models: {SUPPORTED_MODELS}"
            )

        # ✅ IMPROVED: Generate a unique key for the model instance based on its parameters
        instance_key = model_name
        if kwargs:
            # Create a stable key from kwargs; credentials only as a digest.
            sorted_kwargs = sorted(kwargs.items())
            instance_key += "_" + "&".join(
                _cache_key_part(k, v) for k, v in sorted_kwargs
            )

        # Return cached model if already loaded
        if instance_key in cls._models:
            logger.debug(f"Returning cached model: {instance_key}")
            cls._update_model_usage(instance_key)  # Update usage timestamp
            return cls._models[instance_key]

        # Load model with thread safety
        model_lock = cls._get_or_create_lock(instance_key)
        with model_lock:
            # Double-check after acquiring lock
            if instance_key in cls._models:
                logger.debug(f"Returning cached model (double-check): {instance_key}")
                cls._update_model_usage(instance_key)  # Update usage timestamp
                return cls._models[instance_key]

            try:
                logger.info(f"Loading model: {model_name} with key: {instance_key}")
                model_class = MODEL_NAME_TO_CLASS_MAPPING.get(model_name)

                if not model_class:
                    raise ValueError(f"No model class found for '{model_name}'")

                # Initialize model with correct arguments from kwargs
                model_instance = model_class(**kwargs)
                cls._models[instance_key] = model_instance
                cls._update_model_usage(instance_key)  # Set initial usage timestamp

                logger.info(f"Successfully loaded model: {instance_key}")
                return model_instance

            except Exception as e:
                logger.error(
                    f"Failed to load model '{instance_key}': {e}", exc_info=True
                )
                raise RuntimeError(f"Failed to load model '{instance_key}': {e}") from e

    @classmethod
    def get_loaded_models(cls) -> dict[str, object]:
        """Get all currently loaded models."""
        return cls._models.copy()

    @classmethod
    def unload_model(cls, model_name: str) -> bool:
        """
        Unload a specific model from memory.

        Args:
            model_name: The name of the model to unload

        Returns:
            True if model was unloaded, False if it wasn't loaded
        """
        # Note: This simple implementation unloads all instances of a model type.
        # A more advanced implementation might handle specific instances.
        keys_to_unload = [key for key in cls._models if key.startswith(model_name)]
        if not keys_to_unload:
            return False

        unloaded = False
        for key in keys_to_unload:
            with cls._get_or_create_lock(key):
                if key in cls._models:
                    del cls._models[key]
                    if key in cls._model_last_used:
                        del cls._model_last_used[key]
                    logger.info(f"Unloaded model instance: {key}")
                    unloaded = True
        return unloaded

    @classmethod
    def unload_all_models(cls):
        """Unload all models from memory."""
        with cls._global_lock:
            model_names = list(cls._models.keys())
            cls._models.clear()
            cls._model_last_used.clear()
            logger.info(f"Unloaded all models: {model_names}")

    @classmethod
    def get_model_status(cls) -> dict[str, bool]:
        """Get the loading status of all supported models.

        Checks if any instance of each model type is loaded, accounting for
        instance keys that include parameters (e.g., 'text_embedding_model=foo').
        """
        status = {}
        for model in SUPPORTED_MODELS:
            # Check if any loaded model key starts with this model name
            # This handles both exact matches and parameterized instances
            is_loaded = any(
                key == model or key.startswith(f"{model}_") for key in cls._models
            )
            status[model] = is_loaded
        return status

    @classmethod
    def get_model_usage_info(cls) -> dict[str, dict]:
        """Get detailed information about model usage and last access times."""
        current_time = time.time()
        usage_info = {}

        for model_key, last_used in cls._model_last_used.items():
            idle_time = current_time - last_used
            usage_info[model_key] = {
                "loaded": model_key in cls._models,
                "last_used": last_used,
                "idle_time_seconds": idle_time,
                "idle_time_minutes": idle_time / 60,
                "will_be_cleaned_up": idle_time > cls._cleanup_interval,
            }

        return usage_info

    @classmethod
    def stop_cleanup_thread(cls):
        """Stop the background cleanup thread."""
        cls._stop_cleanup.set()
        if cls._cleanup_thread and cls._cleanup_thread.is_alive():
            cls._cleanup_thread.join(timeout=5)
            logger.info("Stopped model cleanup thread")
