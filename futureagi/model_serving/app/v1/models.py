from typing import Any

from pydantic import BaseModel


class InferModelRequest(BaseModel):
    text: str | list[str] | None = None
    image: str | bytes | None = None  # base64 encoded image or image data
    audio: str | bytes | None = None  # audio data
    input_type: str | None = "text"  # text, image, audio, image-text
    audio_model: str | None = None  # override per-request audio backend
    model_params: dict[str, Any] | None = (
        None  # Optional parameters for model initialization
    )
