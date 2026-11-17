import asyncio
import os
import re
import traceback
from typing import Any

import structlog
from app.config.settings import APPROVED_HUGGINGFACE_MODELS, SUPPORTED_MODELS
from app.servable_models import ModelServing
from app.utils.load_model import ModelLoader
from app.v1.models import InferModelRequest

try:
    from fastapi import APIRouter, HTTPException
except ImportError:
    # Handle case where fastapi is not available
    raise ImportError("FastAPI is required but not installed")  # noqa: B904

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1")

# Cap concurrent audio inferences to bound per-pod memory.
_AUDIO_INFER_CONCURRENCY = int(os.getenv("AUDIO_INFER_CONCURRENCY", "10"))
_audio_infer_sem = asyncio.Semaphore(_AUDIO_INFER_CONCURRENCY)

# model_params a request may pass to each servable's constructor. The built-in
# models take none: their weights are fixed by server config, and a
# `model_name` there would choose what SentenceTransformer loads.
_ALLOWED_MODEL_PARAMS = {
    "openai": frozenset({"model", "api_key"}),
    "huggingface": frozenset({"model", "api_key"}),
}


# The OpenAI client sends its key as "Authorization: Bearer <key>". A key
# with a space, line break, other control character or non-ASCII character
# is not a legal header value, and the HTTP stack (httpcore/h11) quotes the
# whole header, key included, in the error it raises. So such a key is
# refused here, before any client is built, and the refusal never repeats it.
# The Hugging Face loader never uses its api_key, so that key is not checked.
_HEADER_SAFE_API_KEY = re.compile(r"[\x21-\x7e]+")
_MALFORMED_API_KEY_DETAIL = (
    "The OpenAI api_key is malformed: it may only contain visible ASCII "
    "characters, with no spaces or line breaks. Re-enter the key and try again."
)


def _refuse_malformed_api_key(model_name: str, params: dict) -> None:
    key = params.get("api_key")
    if model_name != "openai" or key is None:
        return
    if not isinstance(key, str) or not _HEADER_SAFE_API_KEY.fullmatch(key):
        raise HTTPException(status_code=400, detail=_MALFORMED_API_KEY_DETAIL)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """exc and the exceptions it chains, outermost first."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    return chain


def _traceback_without_messages(exc: BaseException) -> str:
    """The traceback of exc and its chain, with every exception message left out.

    Exception text can quote request data: when the HTTP stack refuses a
    header it repeats the header, a customer's api_key included. Types, files,
    lines and source lines are enough to debug from and carry no request data.
    """
    parts = []
    for link in _exception_chain(exc):
        frames = traceback.format_list(traceback.extract_tb(link.__traceback__))
        exc_type = type(link)
        parts.append(
            f"{exc_type.__module__}.{exc_type.__qualname__} (message withheld)\n"
            + "".join(frames)
        )
    return "--- caused by ---\n".join(parts)


def _validated_model_params(model_name: str, model_params: dict | None) -> dict:
    """Refuse request-chosen loader arguments outside the server allowlist."""
    params = model_params or {}
    unexpected = sorted(
        set(params) - _ALLOWED_MODEL_PARAMS.get(model_name, frozenset())
    )
    if unexpected:
        raise HTTPException(
            status_code=400,
            detail=f"model_params {unexpected} are not accepted for '{model_name}'",
        )
    _refuse_malformed_api_key(model_name, params)
    if model_name == "huggingface":
        model = params.get("model")
        if not isinstance(model, str) or model not in APPROVED_HUGGINGFACE_MODELS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Hugging Face model is not on this server's allowlist. "
                    f"Approved models: {sorted(APPROVED_HUGGINGFACE_MODELS)}"
                ),
            )
    return params


@router.get("/models")
async def list_models():
    """
    List all supported models and their loading status.

    Returns:
        Dict containing supported models and their current status
    """
    logger.info("Retrieving list of supported models")
    try:
        model_status = ModelLoader.get_model_status()
        return {
            "models": SUPPORTED_MODELS,
            "loaded_models": [name for name, loaded in model_status.items() if loaded],
            "status": model_status,
        }
    except Exception as e:
        logger.error(f"Error retrieving model list: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve model list"
        ) from e


@router.get("/models/{model_name}/status")
async def get_model_status(model_name: str):
    """
    Get the status of a specific model.

    Args:
        model_name: The name of the model to check

    Returns:
        Dict containing model status information
    """
    if model_name not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_name}' not supported. Supported models: {SUPPORTED_MODELS}",
        )

    try:
        model_status = ModelLoader.get_model_status()
        return {
            "model_name": model_name,
            "loaded": model_status.get(model_name, False),
            "supported": True,
        }
    except Exception as e:
        logger.error(f"Error getting model status for {model_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get model status") from e


@router.post("/infer/{model_name}")
async def infer(model_name: str, data: InferModelRequest) -> dict[str, Any]:
    """
    ✅ IMPROVED: Perform inference using the specified model with better validation and error handling.

    Args:
        model_name (str): The name of the model to use for inference.
        data (InferModelRequest): Input data for the model.

    Returns:
        Dict[str, Any]: The inference result with embeddings.

    Raises:
        HTTPException: If the model is not found or if inference fails.
    """
    try:
        logger.info(f"Starting inference with model: {model_name}")

        # ✅ FIXED: Validate model name before attempting to load
        if model_name not in SUPPORTED_MODELS:
            logger.warning(f"Unsupported model requested: {model_name}")
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_name}' not supported. Supported models: {SUPPORTED_MODELS}",
            )

        model_params = _validated_model_params(model_name, data.model_params)

        # Get the model with proper error handling
        try:
            model: ModelServing = ModelLoader.get_model(model_name, **model_params)
            if model is None:
                raise RuntimeError(f"Model {model_name} could not be loaded")
        except ValueError as e:
            logger.error(f"Model validation error: {e}")
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RuntimeError as e:
            logger.error(f"Model loading error: {e}")
            raise HTTPException(
                status_code=503, detail=f"Failed to load model: {e}"
            ) from e

        logger.debug(f"Processing input data with type: {data.input_type}")

        # ✅ IMPROVED: Better input validation and preprocessing
        input_data = None
        if data.input_type == "text" and data.text:
            input_data = data.text
        elif data.input_type == "image" and data.image:
            input_data = data.image
        elif data.input_type == "audio" and data.audio:
            input_data = data.audio
        elif data.input_type == "image-text":
            if data.text:
                input_data = data.text
            elif data.image:
                input_data = data.image
            else:
                raise HTTPException(
                    status_code=400,
                    detail="For image-text model, either 'text' or 'image' field is required",
                )
        else:
            # Fallback to text if available
            if data.text:
                input_data = data.text
                logger.debug("Falling back to text input data")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"No valid input data provided for input_type: {data.input_type}",
                )

        if not input_data:
            raise HTTPException(status_code=400, detail="No input data provided")

        # ✅ IMPROVED: Process data through the proper pipeline
        logger.debug("Preprocessing input data")
        preprocessed_data = model.preprocess(input_data)

        logger.debug("Performing model inference")
        model_output = model.forward(preprocessed_data)

        logger.debug("Postprocessing model output")
        final_output = model.postprocess(model_output)

        logger.info("Model inference completed successfully")

        # ✅ FIXED: Ensure final_output is JSON serializable
        if hasattr(final_output, "tolist"):
            final_output = final_output.tolist()

        return {
            "embeddings": final_output,
            "model_name": model_name,
            "input_type": data.input_type,
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"ValueError during inference: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # Neither the log nor the 500 carries exception text, and the 500 does
        # not chain e. The detail is the response body as-is (main.py's 500
        # handler does not run for an HTTPException), and Sentry's FastAPI
        # integration captures a 5xx HTTPException with its whole chain, whose
        # text can quote request data (see _traceback_without_messages).
        logger.error(
            f"Unexpected error during inference: {type(e).__name__}",
            traceback=_traceback_without_messages(e),
        )
        raise HTTPException(
            status_code=500, detail=f"Internal server error ({type(e).__name__})"
        ) from None


@router.post("/embed")
async def embed_text(data: InferModelRequest) -> dict[str, Any]:
    """
    ✅ IMPROVED: Convenience endpoint for text embedding using the default text model.
    """
    try:
        logger.info("Starting text embedding process")
        if not data.text:
            raise HTTPException(status_code=400, detail="Text input is required")

        # Use the generic infer endpoint
        data.input_type = "text"
        return await infer("text_embedding", data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Text embedding failed with error: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Text embedding failed: {e}"
        ) from e


@router.post("/embed/image")
async def embed_image(data: InferModelRequest) -> dict[str, Any]:
    """
    ✅ IMPROVED: Convenience endpoint for image embedding.
    """
    try:
        logger.info("Starting image embedding process")
        if not data.image:
            raise HTTPException(status_code=400, detail="Image input is required")

        # Use the generic infer endpoint
        data.input_type = "image"
        return await infer("image_embedding", data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image embedding failed with error: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Image embedding failed: {e}"
        ) from e


@router.post("/embed/audio")
async def embed_audio(data: InferModelRequest) -> dict[str, Any]:
    """
    ✅ IMPROVED: Convenience endpoint for audio embedding.
    """
    try:
        logger.info("Starting audio embedding process")
        if not data.audio:
            raise HTTPException(status_code=400, detail="Audio input is required")

        data.input_type = "audio"
        async with _audio_infer_sem:
            return await infer("audio_embedding", data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio embedding failed with error: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Audio embedding failed: {e}"
        ) from e


@router.post("/embed/image-text")
async def embed_image_text(data: InferModelRequest) -> dict[str, Any]:
    """
    ✅ IMPROVED: Convenience endpoint for image-text embedding.
    """
    try:
        logger.info("Starting image-text embedding process")
        if not data.text and not data.image:
            raise HTTPException(
                status_code=400,
                detail="Either text or image input is required for image-text embedding",
            )

        # Use the generic infer endpoint
        data.input_type = "image-text"
        return await infer("image_text_embedding", data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image-text embedding failed with error: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Image-text embedding failed: {e}"
        ) from e


@router.post("/embed/syn-data")
async def embed_syn_data(data: InferModelRequest) -> dict[str, Any]:
    """
    ✅ IMPROVED: Convenience endpoint for synthetic data embedding.
    """
    try:
        logger.info("Starting synthetic data embedding process")
        if not data.text:
            raise HTTPException(
                status_code=400,
                detail="Text input is required for synthetic data embedding",
            )

        # Use the generic infer endpoint
        data.input_type = "text"
        return await infer("syn_data_embedding", data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Synthetic data embedding failed with error: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Synthetic data embedding failed: {e}"
        ) from e


@router.get("/health")
async def health_check():
    """
    ✅ NEW: Health check endpoint for monitoring.
    """
    try:
        model_status = ModelLoader.get_model_status()
        return {
            "status": "healthy",
            "models": {
                "supported": SUPPORTED_MODELS,
                "loaded": [name for name, loaded in model_status.items() if loaded],
            },
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unhealthy") from e
