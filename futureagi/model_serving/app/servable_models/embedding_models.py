import base64
import io
import threading
from io import BytesIO

import librosa
import numpy as np
import requests
import structlog
from app.servable_models import ModelServing
from app.utils.utils import download_audio_from_url
from PIL import Image
from sentence_transformers import SentenceTransformer
from transformers import AutoProcessor, CLIPModel, Wav2Vec2Model, Wav2Vec2Processor

logger = structlog.get_logger(__name__)

# Browser-like UA so public hosts that block default `python-requests/X.X`
# (Wikipedia, many CDNs) don't return 403 when CLIP / image preprocessing
# tries to fetch a user-supplied URL. S3 public-read buckets don't need
# this — they accept any UA — but it costs nothing and unblocks the rest.
_IMAGE_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


class TextEmbeddingModel(ModelServing):
    _model_instance = None
    _lock = threading.Lock()

    def __init__(self, model_name="all-MiniLM-L6-v2"):
        if TextEmbeddingModel._model_instance is None:
            with TextEmbeddingModel._lock:
                if TextEmbeddingModel._model_instance is None:
                    logger.info(f"Loading text embedding model: {model_name}")
                    TextEmbeddingModel._model_instance = SentenceTransformer(model_name)
        self.model = TextEmbeddingModel._model_instance

    def preprocess(self, data):
        """Convert string to list if needed."""
        if isinstance(data, str):
            return [data]
        return data

    def forward(self, data):
        """Generate embeddings for preprocessed text data."""
        try:
            embeddings = self.model.encode(data)
            # Always return consistent format
            if isinstance(embeddings, np.ndarray):
                return embeddings.tolist()
            return embeddings
        except Exception as e:
            logger.error(f"Error in TextEmbeddingModel forward: {e}")
            raise

    def postprocess(self, output):
        return output


class ImageEmbeddingModel(ModelServing):
    _model_instance = None
    _lock = threading.Lock()

    def __init__(self):
        if ImageEmbeddingModel._model_instance is None:
            with ImageEmbeddingModel._lock:
                if ImageEmbeddingModel._model_instance is None:
                    logger.info("Loading image embedding model: clip-ViT-B-32")
                    ImageEmbeddingModel._model_instance = SentenceTransformer(
                        "clip-ViT-B-32"
                    )
        self.model = ImageEmbeddingModel._model_instance

    def preprocess(self, data):
        """Preprocess image data from various formats."""
        if isinstance(data, str):
            # Handle base64 image
            if data.startswith("data:image"):
                try:
                    header, encoded = data.split(",", 1)
                    image_data = base64.b64decode(encoded)
                    return Image.open(io.BytesIO(image_data))
                except Exception as e:
                    logger.error(f"Error processing base64 image: {e}")
                    raise ValueError(f"Invalid base64 image: {e}")  # noqa: B904
            # Handle image URL
            elif data.startswith(("http://", "https://")):
                try:
                    response = requests.get(data, timeout=10, headers=_IMAGE_FETCH_HEADERS)
                    response.raise_for_status()
                    return Image.open(io.BytesIO(response.content))
                except Exception as e:
                    logger.error(f"Error processing image URL: {e}")
                    raise ValueError(f"Failed to fetch image from URL: {e}") from e
            else:
                # Handle text input for multimodal models
                return data
        elif isinstance(data, Image.Image | io.BytesIO):
            return data
        else:
            raise ValueError(
                "Unsupported input type. Expected string (text/URL/base64) or PIL Image"
            )

    def forward(self, data):
        """Generate embeddings for preprocessed image data."""
        try:
            # ✅ FIXED: Don't call preprocess again - data is already preprocessed
            embeddings = self.model.encode(data)
            if isinstance(embeddings, np.ndarray):
                return embeddings.tolist()
            return embeddings
        except Exception as e:
            logger.error(f"Error in ImageEmbeddingModel forward: {e}")
            raise

    def postprocess(self, output):
        return output


class AudioEmbeddingModel(ModelServing):
    _model_instance = None
    _lock = threading.Lock()

    def __init__(self):
        if AudioEmbeddingModel._model_instance is None:
            with AudioEmbeddingModel._lock:
                if AudioEmbeddingModel._model_instance is None:
                    logger.info("Loading audio embedding model: wav2vec2")

                    AudioEmbeddingModel._model_instance = {
                        "model": Wav2Vec2Model.from_pretrained(
                            "facebook/wav2vec2-base"
                        ),
                        "processor": Wav2Vec2Processor.from_pretrained(
                            "facebook/wav2vec2-base"
                        ),
                    }
        self.model = AudioEmbeddingModel._model_instance["model"]
        self.processor = AudioEmbeddingModel._model_instance["processor"]

    def preprocess(self, data):
        """Preprocess audio data from URL."""
        audio_waveform, _ = self.open_audio_from_url(data)
        return audio_waveform

    def forward(self, data):
        """Generate embeddings for preprocessed audio data."""
        try:
            inp = self.processor(
                data, sampling_rate=16000, return_tensors="pt", padding=True
            )
            outputs = self.model(**inp)
            # Use the last hidden state mean as embedding
            embeddings = outputs.last_hidden_state.mean(dim=1)
            embeddings = embeddings.detach().numpy()
            return embeddings[0].tolist()
        except Exception as e:
            logger.error(f"Error in AudioEmbeddingModel forward: {e}")
            raise

    def open_audio_from_url(self, audio_url: str) -> tuple:
        """
        Downloads and opens an audio file from a URL, returning the loaded audio data.

        Args:
            audio_url: URL of the audio file to download

        Returns:
            tuple: (audio_waveform, sampling_rate) from librosa

        Raises:
            ValueError: If audio cannot be downloaded or loaded
        """
        # Process audio input using the existing download_audio_from_url function
        audio_bytes = download_audio_from_url(audio_url)
        if audio_bytes is None:
            raise ValueError(f"Failed to download audio from URL: {audio_url}")

        # Create a BytesIO object from the audio bytes
        audio_buffer = BytesIO(audio_bytes)

        # Load audio from the buffer
        audio_waveform, sampling_rate = librosa.load(audio_buffer, sr=16000)

        if audio_waveform is None:
            raise ValueError("Failed to load audio data with librosa")

        return audio_waveform, sampling_rate

    def postprocess(self, output):
        return output


class ImageTextEmbeddingModel(ModelServing):
    _model_instance = None
    _processor_instance = None
    _lock = threading.Lock()

    def __init__(self):
        if ImageTextEmbeddingModel._model_instance is None:
            with ImageTextEmbeddingModel._lock:
                if ImageTextEmbeddingModel._model_instance is None:
                    logger.info(
                        "Loading image-text embedding model: clip-vit-base-patch32"
                    )
                    ImageTextEmbeddingModel._model_instance = CLIPModel.from_pretrained(
                        "openai/clip-vit-base-patch32"
                    )
                    ImageTextEmbeddingModel._processor_instance = (
                        AutoProcessor.from_pretrained("openai/clip-vit-base-patch32")
                    )
        self.model = ImageTextEmbeddingModel._model_instance
        self.processor = ImageTextEmbeddingModel._processor_instance

    def preprocess(self, data):
        """Preprocess image or text data."""
        if isinstance(data, str):
            # Handle base64 image
            if data.startswith("data:image"):
                try:
                    header, encoded = data.split(",", 1)
                    image_data = base64.b64decode(encoded)
                    return {"data": Image.open(io.BytesIO(image_data)), "type": "image"}
                except Exception as e:
                    logger.error(f"Error processing base64 image: {e}")
                    raise ValueError(f"Invalid base64 image: {e}")  # noqa: B904
            # Handle image URL
            elif data.startswith(("http://", "https://")):
                try:
                    response = requests.get(data, timeout=10, headers=_IMAGE_FETCH_HEADERS)
                    response.raise_for_status()
                    return {
                        "data": Image.open(io.BytesIO(response.content)),
                        "type": "image",
                    }
                except Exception as e:
                    logger.error(f"Error processing image URL: {e}")
                    raise ValueError(f"Failed to fetch image from URL: {e}") from e
            else:
                # Handle text input
                return {"data": data, "type": "text"}
        elif isinstance(data, Image.Image | io.BytesIO):
            return {"data": data, "type": "image"}
        else:
            raise ValueError(
                "Unsupported input type. Expected string (text/URL/base64) or PIL Image"
            )

    def forward(self, data):
        """Generate embeddings for preprocessed image or text data."""
        try:
            # ✅ FIXED: Work with already preprocessed data
            if data["type"] == "text":
                inputs = self.processor(
                    text=[data["data"]], return_tensors="pt", padding=True
                )
                embeddings = self.model.get_text_features(**inputs)
            else:
                inputs = self.processor(images=data["data"], return_tensors="pt")
                embeddings = self.model.get_image_features(**inputs)

            return embeddings.detach().numpy().flatten().astype(np.float32).tolist()
        except Exception as e:
            logger.error(f"Error in ImageTextEmbeddingModel forward: {e}")
            raise

    def postprocess(self, output):
        return output


class SynDataEmbeddingModel(ModelServing):
    _model_instance = None
    _lock = threading.Lock()

    def __init__(self):
        if SynDataEmbeddingModel._model_instance is None:
            with SynDataEmbeddingModel._lock:
                if SynDataEmbeddingModel._model_instance is None:
                    logger.info(
                        "Loading synthetic data embedding model: all-MiniLM-L6-v2"
                    )
                    SynDataEmbeddingModel._model_instance = SentenceTransformer(
                        "all-MiniLM-L6-v2"
                    )
        self.model = SynDataEmbeddingModel._model_instance

    def preprocess(self, data):
        """Convert string to list if needed."""
        if isinstance(data, str):
            return [data]
        return data

    def forward(self, data):
        """Generate embeddings for preprocessed text data."""
        try:
            embeddings = self.model.encode(
                data, batch_size=64, normalize_embeddings=True, show_progress_bar=False
            )
            return embeddings
        except Exception as e:
            logger.error(f"Error in SynDataEmbeddingModel forward: {e}")
            raise

    def postprocess(self, output):
        return output
