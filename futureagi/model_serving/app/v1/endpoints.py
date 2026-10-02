import asyncio
import os
import traceback
from typing import Any

import structlog
from app.config.settings import SUPPORTED_MODELS
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

        # Get the model with proper error handling
        try:
            # ✅ IMPROVED: Pass model_params to the model loader
            model_params = data.model_params or {}
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
        logger.error(f"Unexpected error during inference: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500, detail=f"Internal server error: {str(e)}"
        ) from e


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
