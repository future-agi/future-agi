import base64
import io
import os
import threading
import time
from typing import Any

import numpy as np
import requests
from PIL import Image
from requests.adapters import HTTPAdapter

import structlog

logger = structlog.get_logger(__name__)

# The standalone install runs `serving` only with the `ml` compose profile, so
# every embedding-backed feature has to cope with it being absent.
SERVING_UNAVAILABLE_MESSAGE = (
    "Model serving is not running, so embedding-based features are unavailable. "
    "Start it with the ml profile: `docker compose --profile ml up -d`."
)

# How long a probe result is trusted. Short enough that enabling the profile
# is picked up quickly, long enough that a missing host costs one DNS lookup
# per process every half minute instead of one per embedding call.
SERVING_PROBE_TTL_SECONDS = 30
SERVING_PROBE_TIMEOUT_SECONDS = 2


class ServingUnavailableError(ValueError, ConnectionError):
    """Model serving is not configured or not reachable.

    A ConnectionError so existing ``except ConnectionError`` fail-open paths
    keep catching it, and a ValueError so eval error handling
    (``get_specific_error_message``) shows this message verbatim instead of a
    generic failure.
    """

    def __init__(self, message: str = SERVING_UNAVAILABLE_MESSAGE):
        super().__init__(message)


class ModelServingClient:
    """
    Client for communicating with the model serving service.
    Features: connection pooling, retry logic, proper error handling, and timeouts.
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.getenv('MODEL_SERVING_URL', 'http://serving:8080')



        self.default_timeout = int(os.getenv('MODEL_SERVING_TIMEOUT', '120'))
        self.max_retries = int(os.getenv('MODEL_SERVING_MAX_RETRIES', '3'))

        self._health_check_cache = {}  
        self._health_check_cache_ttl = 60  # 1 minute
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with connection pooling and built-in retry."""
        session = requests.Session()

        # Configure adapter with built-in retry
        adapter = HTTPAdapter(
            max_retries=self.max_retries,  # Built-in retry support
            pool_connections=10,
            pool_maxsize=20,
            pool_block=False
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'ModelServingClient/1.0'
        })

        return session

    def _require_serving(self) -> None:
        """Fail fast on the cached probe verdict. Without it, every call to a
        missing host pays a DNS lookup and the adapter's retries."""
        if not serving_available(self.base_url):
            raise ServingUnavailableError()

    def _make_request(self, endpoint: str, data: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        """Make a request to the serving service with built-in retry."""
        url = f"{self.base_url}/model/v1{endpoint}"
        request_timeout = timeout or self.default_timeout
        self._require_serving()

        try:
            logger.debug(f"Making request to {url}")
            start_time = time.time()

            # Session handles retries automatically via HTTPAdapter
            response = self.session.post(url, json=data, timeout=request_timeout)

            response_time = time.time() - start_time
            logger.debug(f"Request completed in {response_time:.2f}s")

            # Handle response status codes
            if response.status_code == 200:
                response_json= response.json()
                if response_json["embeddings"]:
                    if isinstance(response_json["embeddings"][0], list):
                        # import numpy as np
                        response_json["embeddings"]=response_json["embeddings"][0]

                        # response_json["embeddings"]=np.array(response_json["embeddings"][0]).tolist()
                        # print(f"Response embeddings: {response_json['embeddings']}, type of embeddings: {type(response_json['embeddings'])}")
                return response_json
            elif response.status_code == 404:
                raise ValueError(f"Model or endpoint not found: {response.text}")
            elif response.status_code == 400:
                raise ValueError(f"Bad request: {response.text}")
            else:
                response.raise_for_status()
                return response.json()

        except requests.exceptions.Timeout:
            logger.error(f"Request to {url} timed out after {request_timeout}s")
            raise TimeoutError(f"Request timed out after {request_timeout}s")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to serving service: {e}")
            mark_serving_unavailable(self.base_url)
            raise ConnectionError(f"Failed to connect to serving service: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to serving service failed: {e}")
            raise RuntimeError(f"Request failed: {e}")

    def embed_text(self, text: str | list[str], model_name: str = "text_embedding") -> list[float]:
        """
        ✅ IMPROVED: Get text embeddings from the serving service with input validation.
        """
        if not text:
            raise ValueError("Text input cannot be empty")

        # Ensure text is in the right format
        if isinstance(text, str):
            text_list = [text]
        else:
            text_list = text

        if not all(isinstance(t, str) for t in text_list):
            raise ValueError("All text inputs must be strings")

        data = {
            "text": text_list,
            "input_type": "text"
        }

        try:
            response = self._make_request("/embed", data)

            return response["embeddings"]
        except Exception as e:
            logger.error(f"Text embedding failed: {e}")
            raise

    def embed_text_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Batch text embedding: send N texts in one request, get N vectors back.

        Deliberately does NOT go through _make_request — that path unwraps
        embeddings[0] for single-input convenience, which silently drops all
        but the first vector for a batch. The /embed endpoint already returns
        one vector per input in order, so we return it as-is.
        """
        if not texts:
            return []
        if not all(isinstance(t, str) for t in texts):
            raise ValueError("All text inputs must be strings")
        self._require_serving()

        data = {"text": texts, "input_type": "text"}
        response = self.session.post(
            f"{self.base_url}/model/v1/embed", json=data, timeout=self.default_timeout
        )
        if response.status_code != 200:
            response.raise_for_status()
        embeddings = response.json().get("embeddings", [])
        if len(embeddings) != len(texts):
            # Contract violation — caller decides how to fall back rather
            # than us silently returning misaligned vectors.
            raise ValueError(
                f"serving returned {len(embeddings)} embeddings for "
                f"{len(texts)} texts"
            )
        return embeddings

    def embed_image(self, image: str | Image.Image | bytes, model_name: str = "image_embedding") -> list[float]:
        """
        ✅ IMPROVED: Get image embeddings from the serving service with better image handling.
        """
        if image is None:
            raise ValueError("Image input cannot be None")

        processed_image = self._process_image_input(image)

        data = {
            "image": processed_image,
            "input_type": "image"
        }

        try:
            response = self._make_request("/embed/image", data)
            return response["embeddings"]
        except Exception as e:
            logger.error(f"Image embedding failed: {e}")
            raise

    def embed_audio(
        self,
        audio_data: str | bytes,
        model_name: str = "audio_embedding",
    ) -> list[float]:
        """
        ✅ IMPROVED: Get audio embeddings from the serving service with input validation.
        """
        if audio_data is None:
            raise ValueError("Audio input cannot be None")

        processed_audio = self._process_audio_input(audio_data)

        data = {
            "audio": processed_audio,
            "input_type": "audio",
        }

        try:
            response = self._make_request("/embed/audio", data, timeout=3000000)
            return response["embeddings"]
        except Exception as e:
            logger.error(f"Audio embedding failed: {e}")
            raise

    def embed_image_text(self, content: str | Image.Image | bytes, model_name: str = "image_text_embedding") -> list[float]:
        """
        Get image-text embeddings from the serving service.

        Routes URL / data-URI strings to the *image* branch (so CLIP encodes
        the pixels) and plain strings to the *text* branch (so CLIP encodes
        the caption). Treating every string as text — the previous behaviour
        — embedded the URL/base64 literal in the text encoder, producing
        garbage similarities that hovered near the noise floor.
        """
        if content is None:
            raise ValueError("Content input cannot be None")

        data = {"input_type": "image-text"}

        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith(("http://", "https://", "data:")):
                data["image"] = self._process_image_input(content)
            else:
                data["text"] = content
        else:
            # PIL Image, bytes — always the image branch.
            data["image"] = self._process_image_input(content)

        try:
            response = self._make_request("/embed/image-text", data)
            return response["embeddings"]
        except Exception as e:
            logger.error(f"Image-text embedding failed: {e}")
            raise

    def get_syn_data_embedding(self, text: str | list[str]) -> list[float]:
        """Get synthetic data embeddings from the serving service."""
        self._require_serving()
        data = {
            "text": text,
            "input_type": "text"
        }
        try:
            # Session handles retries automatically via HTTPAdapter
            response = self.session.post(f"{self.base_url}/model/v1/embed/syn-data", json=data, timeout=self.default_timeout)
            # Handle response status codes
            if response.status_code == 200:
                return response.json()["embeddings"]
            elif response.status_code == 404:
                raise ValueError(f"Model or endpoint not found: {response.text}")
            elif response.status_code == 400:
                raise ValueError(f"Bad request: {response.text}")
            else:
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"get_syn_data_embedding failed: {e}")
            raise

    def _process_image_input(self, image: str | Image.Image | bytes) -> str:
        """
        ✅ NEW: Process different image input formats into base64.
        """
        if isinstance(image, str):
            # Already a string (URL or base64)
            return image
        elif isinstance(image, Image.Image):
            # Convert PIL Image to base64
            buffer = io.BytesIO()
            # Use format from image if available, otherwise default to JPEG
            format_name = getattr(image, 'format', 'JPEG') or 'JPEG'
            image.save(buffer, format=format_name)
            image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
            return f"data:image/{format_name.lower()};base64,{image_data}"
        elif isinstance(image, bytes):
            # Convert bytes to base64
            image_data = base64.b64encode(image).decode('utf-8')
            return f"data:image/jpeg;base64,{image_data}"
        else:
            raise ValueError(f"Unsupported image type: {type(image)}")

    def _process_audio_input(self, audio_data) -> str:
        """
        ✅ NEW: Process different audio input formats.
        """
        if isinstance(audio_data, str):
            # If it's already a string, check it it is a link else assume it's base64 encoded
            if audio_data.startswith('http://') or audio_data.startswith('https://'):
                return audio_data
            elif not audio_data.startswith('data:'):
                return f"data:audio/wav;base64,{audio_data}"
            return audio_data
        elif isinstance(audio_data, bytes):
            # Convert bytes to base64
            audio_b64 = base64.b64encode(audio_data).decode('utf-8')
            return f"data:audio/wav;base64,{audio_b64}"
        elif isinstance(audio_data, list):
            # If it's a waveform array, convert to base64
            import io
            import wave

            # Convert list to numpy array
            audio_array = np.array(audio_data, dtype=np.float32)

            # Create a temporary WAV file in memory
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)  # 16kHz sample rate
                wav_file.writeframes((audio_array * 32767).astype(np.int16).tobytes())

            # Convert to base64
            audio_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            return f"data:audio/wav;base64,{audio_b64}"
        elif isinstance(audio_data, np.ndarray):
            # If it's a numpy array, convert to base64
            import io
            import wave

            # Create a temporary WAV file in memory
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)  # 16kHz sample rate
                wav_file.writeframes((audio_data * 32767).astype(np.int16).tobytes())

            # Convert to base64
            audio_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            return f"data:audio/wav;base64,{audio_b64}"
        else:
            raise ValueError(f"Unsupported audio type: {type(audio_data)}")

    def health_check(self, use_cache: bool = True) -> bool:
        """
        ✅ IMPROVED: Check if the serving service is healthy with caching.
        """
        current_time = time.time()
        cache_key = "health_check"

        # Check cache if enabled
        if use_cache and cache_key in self._health_check_cache:
            cached_result, cached_time = self._health_check_cache[cache_key]
            if current_time - cached_time < self._health_check_cache_ttl:
                logger.debug("Using cached health check result")
                return cached_result

        try:
            url = f"{self.base_url}/model/v1/models"
            response = self.session.get(url, timeout=5)
            is_healthy = response.status_code == 200

            # Cache the result
            if use_cache:
                self._health_check_cache[cache_key] = (is_healthy, current_time)

            logger.debug(f"Health check result: {'healthy' if is_healthy else 'unhealthy'}")
            return is_healthy

        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            # Cache negative result for shorter time
            if use_cache:
                self._health_check_cache[cache_key] = (False, current_time - self._health_check_cache_ttl + 10)
            return False

    def get_model_status(self) -> dict[str, Any]:
        """
        ✅ NEW: Get detailed status of all models from the serving service.
        """
        try:
            url = f"{self.base_url}/model/v1/models"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get model status: {e}")
            raise

    def get_embeddings(
        self,
        text: list[str],
        model_provider: str,
        model_name: str,
        model_params: dict[str, Any] | None = None
    ) -> list[list[float]]:
        """
        Get embeddings from the model_serving API.

        Args:
            text: A list of strings to embed.
            model_provider: The provider of the model (e.g., 'huggingface', 'openai').
            model_name: The specific model to use.
            model_params: Additional parameters for the model, like API keys.

        Returns:
            A list of embeddings.
        """
        self._require_serving()
        endpoint = f"{self.base_url}/model/v1/infer/{model_provider}"

        # ✅ IMPROVED: Pass model name correctly based on provider
        params = model_params or {}
        params["model"] = model_name
        
        request_body: dict[str, Any] = {
            "text": text,
            "input_type": "text",
            "model_params": params
        }

        try:
            logger.info(f"Requesting embeddings from {endpoint} for model {model_name}")
            response = self.session.post(endpoint, json=request_body,timeout=30000)

             # Handle response status codes
            if response.status_code == 200:
                response_json= response.json()
                if response_json["embeddings"]:
                    if isinstance(response_json["embeddings"][0], list):
                        # import numpy as np
                        response_json["embeddings"]=response_json["embeddings"][0]

                logger.info("Successfully received embeddings.")
                return response_json["embeddings"]
            else:
                response.raise_for_status()  # Raise an exception for bad status codes

                result = response.json()
                logger.info("Successfully received embeddings.")
                return result.get("embeddings", [])

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get embeddings from model_serving: {e}")
            # Depending on requirements, you might want to re-raise or handle differently
            raise

    def close(self):
        """
        ✅ NEW: Properly close the session and clean up resources.
        """
        if hasattr(self, 'session'):
            self.session.close()
            logger.debug("Model serving client session closed")


_serving_client = None


def get_serving_client() -> ModelServingClient:
    """Get the global serving client instance."""
    global _serving_client
    if _serving_client is None:
        _serving_client = ModelServingClient()
    return _serving_client


def close_serving_client():
    """Close the global serving client and clean up resources."""
    global _serving_client
    if _serving_client is not None:
        _serving_client.close()
        _serving_client = None


# base_url -> (available, monotonic time of the probe)
_probe_cache: dict[str, tuple[bool, float]] = {}
_probe_lock = threading.Lock()


def serving_base_url() -> str:
    """``MODEL_SERVING_URL``, normalised. Empty means serving is switched off."""
    return os.getenv("MODEL_SERVING_URL", "http://serving:8080").strip().rstrip("/")


def _probe(base_url: str) -> bool:
    # Plain GET, no retry adapter: a missing host should cost one lookup.
    # A server that answers but has no /health (an older serving image, the
    # E2E mock) is asked for the model list instead, which is what the
    # client's own health_check() uses.
    try:
        response = requests.get(
            f"{base_url}/health", timeout=SERVING_PROBE_TIMEOUT_SECONDS
        )
        if response.status_code == 404:
            response = requests.get(
                f"{base_url}/model/v1/models", timeout=SERVING_PROBE_TIMEOUT_SECONDS
            )
        return response.status_code == 200
    except requests.RequestException:
        return False


def serving_available(base_url: str | None = None, use_cache: bool = True) -> bool:
    """Whether model serving answers its health check.

    Results are cached per URL for ``SERVING_PROBE_TTL_SECONDS``, and one
    thread probes while the others wait for its answer, so callers can ask
    before every embedding call. ``use_cache=False`` forces a fresh probe and
    refreshes the cache.
    """
    base = serving_base_url() if base_url is None else base_url.strip().rstrip("/")
    if not base:
        return False

    cached = _probe_cache.get(base)
    if use_cache and cached and time.monotonic() - cached[1] < SERVING_PROBE_TTL_SECONDS:
        return cached[0]

    with _probe_lock:
        cached = _probe_cache.get(base)
        if use_cache and cached and time.monotonic() - cached[1] < SERVING_PROBE_TTL_SECONDS:
            return cached[0]
        available = _probe(base)
        _probe_cache[base] = (available, time.monotonic())

    # Log the transition, not every probe: absent serving is a normal state.
    if not available and (cached is None or cached[0]):
        logger.warning("model_serving_unavailable", url=base)
    return available


def mark_serving_unavailable(base_url: str | None = None) -> None:
    """Record a failed call so the next callers fail fast until the TTL expires."""
    base = serving_base_url() if base_url is None else base_url.strip().rstrip("/")
    if base:
        _probe_cache[base] = (False, time.monotonic())


def require_serving() -> None:
    """Raise ``ServingUnavailableError`` unless model serving is reachable."""
    if not serving_available():
        raise ServingUnavailableError()
