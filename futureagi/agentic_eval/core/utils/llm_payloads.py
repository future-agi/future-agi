"""
Utilities for building provider-aware LLM message content blocks.

This module provides helper functions to standardize various input types
(images, audio, PDFs, etc.) into the appropriate content block format for
different LLM providers (OpenAI, Vertex AI, Bedrock, etc.).
"""

import base64
import json
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Union

import requests

from agentic_eval.core.utils.model_config import LiteLlmProvider
from tfc.utils.storage import download_image_from_url

ContentBlock = Dict[str, Any]
Message = Dict[str, Any]


def _provider_value(provider: Union[str, LiteLlmProvider]) -> str:
    """Extract string value from provider enum or string."""
    return provider.value if isinstance(provider, LiteLlmProvider) else str(provider)


def extract_media_bytes(input_value: Any) -> Optional[bytes]:
    """
    Extract raw bytes from common input formats.

    Handles:
    - Raw bytes or bytearray
    - Dictionaries with 'bytes' or 'file_data' keys

    Returns:
        bytes or None if extraction fails
    """
    if isinstance(input_value, (bytes, bytearray)):
        return bytes(input_value)
    if isinstance(input_value, dict):
        # Check common keys used in the codebase
        for key in ["bytes", "file_data"]:
            if key in input_value and isinstance(input_value[key], (bytes, bytearray)):
                return bytes(input_value[key])
    return None


def normalize_to_bytes(input_value: Any) -> Optional[bytes]:
    """
    Convert various input types to raw bytes.

    Handles:
    - Raw bytes/dict with bytes
    - Local file paths (reads file content)

    Returns:
        bytes or None if normalization fails
    """
    # 1. Direct bytes/dict extraction
    raw = extract_media_bytes(input_value)
    if raw:
        return raw

    # 2. Local file path resolution
    if isinstance(input_value, str):
        path = input_value.strip()
        if any(path.startswith(p) for p in ("/", "./", "../")) and os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()

    return None


def standardize_to_data_uri(input_value: Any, default_mime: str) -> str:
    """
    Standardize various inputs into a data URI or URL string.

    Handles:
    - Raw bytes/bytearrays → data URI
    - Dicts with bytes → data URI
    - Local file paths → reads and converts to data URI
    - Existing data URIs or URLs → returns as-is

    Args:
        input_value: The input to standardize
        default_mime: MIME type to use for data URIs

    Returns:
        A data URI or URL string
    """
    # 1. Attempt to get raw bytes from input (handles dicts/paths too)
    raw_bytes = normalize_to_bytes(input_value)
    if raw_bytes:
        # Determine mime type if it was a file path
        mime = default_mime
        if isinstance(input_value, str) and os.path.isfile(input_value.strip()):
            mime = mimetypes.guess_type(input_value.strip())[0] or default_mime

        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    if isinstance(input_value, str):
        s = input_value.strip()

        # 2. Return as-is if it's already a URL or encoded data
        if s.startswith(("http://", "https://", "data:", "gs://", "s3://")):
            return s

        # 3. If it's a plain string that looks like base64, assume it needs a prefix
        if len(s) > 100 and "," not in s and not s.startswith("http"):
            return f"data:{default_mime};base64,{s}"

        return s

    return str(input_value)


def build_text_content(text: Any) -> ContentBlock:
    return {"type": "text", "text": "" if text is None else str(text)}


def _coerce_image_url(image_input: Union[str, bytes], *, media_type: str) -> str:
    """
    Returns a URL usable in an `image_url` content block.
    Accepts: http(s) URL, data URI, base64 string, or raw bytes.
    """
    if isinstance(image_input, bytes):
        return (
            f"data:{media_type};base64,{base64.b64encode(image_input).decode('utf-8')}"
        )

    s = str(image_input).strip()
    if s.startswith(("http://", "https://", "data:image/")):
        return s

    return f"data:{media_type};base64,{s}"


def build_image_content(
    *,
    provider: Union[str, LiteLlmProvider],
    image_input: Union[str, bytes, dict],
    media_type: str = "image/jpeg",
) -> ContentBlock:
    """
    Provider-aware image payload.
    LiteLLM expects `image_url` blocks and will transform them per-provider.
    """
    p = _provider_value(provider)
    standardized = standardize_to_data_uri(image_input, media_type)

    if p == LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value and isinstance(
        standardized, str
    ):
        if standardized.startswith(("http://", "https://")):
            img_bytes = download_image_from_url(standardized)
            mime = mimetypes.guess_type(standardized)[0] or media_type
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }

    return {"type": "image_url", "image_url": {"url": standardized}}


def _coerce_audio_base64(audio_input: Union[str, bytes]) -> str:
    if isinstance(audio_input, bytes):
        return base64.b64encode(audio_input).decode("utf-8")

    s = str(audio_input).strip()
    if s.startswith("data:audio/") and ";base64," in s:
        return s.split(",", 1)[1]
    return s


def build_audio_content(
    *,
    provider: Union[str, LiteLlmProvider],
    audio_input: Union[str, bytes],
    audio_format: str = "mp3",
) -> ContentBlock:
    """
    Provider-aware audio payload.
    - OpenAI: `input_audio` block (base64 only).
    - Vertex: `image_url` block with a `data:audio/...;base64,...` URL (current repo convention).
    """
    p = _provider_value(provider)
    if p == LiteLlmProvider.OPENAI.value:
        return {
            "type": "input_audio",
            "input_audio": {
                "data": _coerce_audio_base64(audio_input),
                "format": audio_format,
            },
        }

    if p == LiteLlmProvider.VERTEX_AI.value:
        s = (
            audio_input.decode("utf-8")
            if isinstance(audio_input, bytes)
            else str(audio_input).strip()
        )
        if not s.startswith("data:audio/"):
            s = f"data:audio/{audio_format};base64,{_coerce_audio_base64(s)}"
        return {"type": "image_url", "image_url": {"url": s}}

    raise NotImplementedError(f"Audio payload not supported for provider={p}")


def _coerce_pdf_bytes(pdf_input: Union[str, bytes]) -> bytes:
    if isinstance(pdf_input, bytes):
        return pdf_input

    s = str(pdf_input).strip()
    if s.startswith("data:") and ";base64," in s:
        return base64.b64decode(s.split(",", 1)[1])
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        with open(s, "rb") as f:
            return f.read()


def build_pdf_content(
    *,
    provider: Union[str, LiteLlmProvider],
    pdf_input: Optional[Union[str, bytes]] = None,
    file_id: Optional[str] = None,
    filename: str = "x.pdf",
    mime_type: str = "application/pdf",
) -> ContentBlock:
    """
    Provider-aware PDF payload.
    LiteLLM expects a `file` block and will transform it per-provider.

    - OpenAI: `file_data` (data URL) is required.
    - Vertex: `file_id` is required (must be a remotely accessible URL).
    - Bedrock Anthropic: prefer `file_data` (data URL). If only `file_id` is provided and it
      is a URL, we download and inline it as `file_data` to avoid Bedrock URL limitations.
    """
    p = _provider_value(provider)
    if p == LiteLlmProvider.OPENAI.value:
        if pdf_input is None:
            raise ValueError("pdf_input is required for OpenAI PDF payloads")
        pdf_bytes = _coerce_pdf_bytes(pdf_input)
        pdf_data_url = (
            f"data:{mime_type};base64,{base64.b64encode(pdf_bytes).decode('utf-8')}"
        )
        return {
            "type": "file",
            "file": {"filename": filename, "file_data": pdf_data_url},
        }

    if p == LiteLlmProvider.VERTEX_AI.value:
        if not file_id:
            raise ValueError("file_id is required for Vertex AI PDF payloads")
        return {"type": "file", "file": {"file_id": file_id, "format": mime_type}}

    if p == LiteLlmProvider.ANTHROPIC.value:
        # Hosted Anthropic supports `file` blocks; LiteLLM will transform this to a `document`.
        if pdf_input is not None:
            pdf_bytes = _coerce_pdf_bytes(pdf_input)
            pdf_data_url = (
                f"data:{mime_type};base64,{base64.b64encode(pdf_bytes).decode('utf-8')}"
            )
            return {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": pdf_data_url,
                    "format": mime_type,
                },
            }

        if not file_id:
            raise ValueError(
                "Either pdf_input or file_id is required for Anthropic PDF payloads"
            )

        # If it's a URL, omit `format` so LiteLLM can infer `document_url` and use `source: {type:url,...}`.
        if str(file_id).startswith(("http://", "https://")):
            return {"type": "file", "file": {"file_id": file_id}}
        return {"type": "file", "file": {"file_id": file_id, "format": mime_type}}

    if p == LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value:
        if pdf_input is not None:
            pdf_bytes = _coerce_pdf_bytes(pdf_input)
            pdf_data_url = (
                f"data:{mime_type};base64,{base64.b64encode(pdf_bytes).decode('utf-8')}"
            )
            return {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": pdf_data_url,
                    "format": mime_type,
                },
            }

        if file_id and str(file_id).startswith(("http://", "https://")):
            import requests

            r = requests.get(file_id, timeout=60)
            r.raise_for_status()
            pdf_data_url = (
                f"data:{mime_type};base64,{base64.b64encode(r.content).decode('utf-8')}"
            )
            return {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": pdf_data_url,
                    "format": mime_type,
                },
            }

        if file_id:
            # Last resort: let LiteLLM try to route `file_id` (may fail on Bedrock for some URLs).
            return {"type": "file", "file": {"file_id": file_id, "format": mime_type}}

        raise ValueError(
            "Either pdf_input or file_id is required for Bedrock Anthropic PDF payloads"
        )

    raise NotImplementedError(
        f"Direct PDF payload not supported for provider={p}; extract text/OCR and send as text instead."
    )


def build_file_reference_content(
    *,
    provider: Union[str, LiteLlmProvider],
    file_id: str,
    mime_type: str,
) -> ContentBlock:
    """
    Generic reference to a remotely accessible file, for providers that support `file_id` references.
    """
    p = _provider_value(provider)
    if p == LiteLlmProvider.VERTEX_AI.value:
        return {"type": "file", "file": {"file_id": file_id, "format": mime_type}}
    if p == LiteLlmProvider.ANTHROPIC.value:
        # If it's a URL, omit `format` so LiteLLM can infer document_url.
        if str(file_id).startswith(("http://", "https://")):
            return {"type": "file", "file": {"file_id": file_id}}
        return {"type": "file", "file": {"file_id": file_id, "format": mime_type}}
    if p == LiteLlmProvider.AWS_BEDROCK_ANTHROPIC.value:
        # Prefer inlining for Bedrock Claude to avoid URL limitations.
        if str(file_id).startswith(("http://", "https://")):
            import requests

            r = requests.get(file_id, timeout=60)
            r.raise_for_status()
            data_url = (
                f"data:{mime_type};base64,{base64.b64encode(r.content).decode('utf-8')}"
            )
            return {
                "type": "file",
                "file": {"file_data": data_url, "format": mime_type},
            }
        return {"type": "file", "file": {"file_id": file_id, "format": mime_type}}
    raise NotImplementedError(f"File reference payload not supported for provider={p}")


def build_user_message(content: Sequence[ContentBlock]) -> Message:
    return {"role": "user", "content": list(content)}


# ---------------------------------------------------------------------------
# Shared multimodal detection + content block building
# ---------------------------------------------------------------------------

_SUPPORTED_MEDIA = {"image", "images", "audio", "pdf"}

_IMAGE_EXT_PAT = re.compile(
    r"https?://\S+\.(png|jpg|jpeg|gif|webp|svg|bmp|tiff|mp4)(\?|$)",
    re.IGNORECASE,
)


def is_url_like(value: Any) -> bool:
    """Check if value is a remote reference or data URI."""
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return (
        text.startswith("http://")
        or text.startswith("https://")
        or text.startswith("s3://")
        or text.startswith("gs://")
        or text.startswith("data:")
    )


def build_media_content_block(
    value: Any,
    media_type: str,
    key: str,
) -> List[ContentBlock]:
    """
    Build content blocks for a single input of known modality.

    Wraps media in XML tags (<key>...</key>) for model context.
    Handles: image, images, audio, pdf.

    Args:
        value: The input value (URL, base64, data URI, JSON array, etc.)
        media_type: Detected modality ("image", "images", "audio", "pdf")
        key: Parameter name used for XML tagging

    Returns:
        List of content blocks. Empty list if value is empty/None.
    """
    blocks: List[ContentBlock] = []

    if media_type in ("image", "images"):
        image_inputs: List[Any]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                image_inputs = parsed if isinstance(parsed, list) else [value]
            except (json.JSONDecodeError, TypeError):
                image_inputs = [value]
        elif isinstance(value, list):
            image_inputs = value
        else:
            image_inputs = [value]

        for img_num, img in enumerate(image_inputs, start=1):
            img_str = str(img).strip() if img else ""
            if img_str.startswith("data:"):
                url = img_str
            elif img_str.startswith(("http://", "https://")):
                # Download and base64-encode — S3/private URLs aren't accessible from providers.
                # Uses download_image_from_url which validates with Pillow.
                try:
                    img_bytes = download_image_from_url(img_str)
                    mime = mimetypes.guess_type(img_str)[0] or "image/jpeg"
                    url = f"data:{mime};base64,{base64.b64encode(img_bytes).decode('utf-8')}"
                except Exception:
                    url = img_str
            else:
                url = standardize_to_data_uri(img, "image/jpeg")
            tag = f"{key}_{img_num}" if len(image_inputs) > 1 else key
            blocks.extend([
                {"type": "text", "text": f"<{tag}>"},
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": f"</{tag}>"},
            ])

    elif media_type == "audio":
        if not value or (isinstance(value, str) and not value.strip()):
            return blocks
        # Audio must be a data URI — providers reject raw audio URLs.
        # Download and base64-encode if it's a remote URL.
        val_str = str(value).strip()
        if val_str.startswith("data:"):
            data_uri = val_str
        elif val_str.startswith(("http://", "https://")):
            from agentic_eval.core.llm.audio_utils import download_audio_url_to_base64

            b64_data, audio_fmt = download_audio_url_to_base64(val_str)
            data_uri = f"data:audio/{audio_fmt};base64,{b64_data}"
        else:
            data_uri = standardize_to_data_uri(value, "audio/mp3")
        blocks.extend([
            {"type": "text", "text": f"<{key}>"},
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": f"</{key}>"},
        ])

    elif media_type == "pdf":
        if not value or (isinstance(value, str) and not value.strip()):
            return blocks
        if is_url_like(value) and not str(value).startswith("data:"):
            pdf_block: ContentBlock = {"type": "file", "file": {"file_id": value, "format": "application/pdf"}}
        else:
            data_uri = standardize_to_data_uri(value, "application/pdf")
            pdf_block = {"type": "file", "file": {"file_data": data_uri, "format": "application/pdf"}}
        blocks.extend([
            {"type": "text", "text": f"<{key}>"},
            pdf_block,
            {"type": "text", "text": f"</{key}>"},
        ])

    return blocks


def detect_and_build_media_blocks(
    inputs: Dict[str, Any],
    required_keys: List[str],
    input_data_types: Optional[Dict[str, str]] = None,
    image_urls: Optional[List[str]] = None,
) -> tuple:
    """
    Two-stage modality detection + content block building.

    Stage 1: Fast regex for extensioned image URLs (no network call).
    Stage 2: Batch detect_input_type() for remaining values (content sniffing).

    Args:
        inputs: Dict of input values keyed by parameter name.
        required_keys: Keys to process from inputs.
        input_data_types: Explicit modality overrides per key (from eval config).
        image_urls: Additional explicit image URLs to include.

    Returns:
        (media_blocks, key_to_modality_map)
        - media_blocks: content blocks for all detected media
        - key_to_modality_map: {key: detected_modality} for keys with media
    """
    input_data_types = input_data_types or {}
    key_media_types: Dict[str, str] = {}
    remaining: Dict[str, Any] = {}

    # Stage 1: fast regex + explicit types
    for key in required_keys:
        val = inputs.get(key, "")
        if not val:
            continue

        # Normalize JSON array strings
        if isinstance(val, str) and val.strip().startswith("["):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    val = parsed
            except (json.JSONDecodeError, ValueError):
                pass

        # Check explicit type first
        explicit_type = input_data_types.get(key, "")
        if explicit_type and explicit_type.lower() in _SUPPORTED_MEDIA:
            key_media_types[key] = explicit_type.lower()
            continue

        # Lists go to Stage 2
        if isinstance(val, list):
            remaining[key] = val
            continue

        if not isinstance(val, str) or not val.strip():
            continue

        # Fast regex for image URLs
        if _IMAGE_EXT_PAT.search(val):
            key_media_types[key] = "image"
            continue

        remaining[key] = val

    # Stage 2: batch detect_input_type for remaining values
    if remaining:
        from agentic_eval.core.utils.functions import detect_input_type

        try:
            detected = detect_input_type(remaining) or {}
        except Exception:
            detected = {}

        for key, media_type in detected.items():
            if isinstance(media_type, str) and media_type in _SUPPORTED_MEDIA:
                key_media_types[key] = media_type
            elif str(media_type).lower() == "file":
                # 'file' means the URL returned non-200 or unrecognized
                # content. If it has an audio extension, the recording is
                # inaccessible — raise a clear error instead of silently
                # passing the URL as text.
                val = remaining.get(key, "") if isinstance(remaining, dict) else ""
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    from urllib.parse import urlparse
                    _path = urlparse(val).path.lower()
                    _audio_exts = (".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac", ".wma", ".webm")
                    if _path.endswith(_audio_exts):
                        raise ValueError(
                            f"Audio recording is not accessible for '{key}'. "
                            f"The file could not be downloaded — please ensure "
                            f"the recording URL is valid and accessible."
                        )

    # Build content blocks from detected types
    media_blocks: List[ContentBlock] = []
    for key in required_keys:
        media_type = key_media_types.get(key)
        if not media_type:
            continue
        val = inputs.get(key, "")
        blocks = build_media_content_block(val, media_type, key)
        media_blocks.extend(blocks)

    # Add explicit image_urls not already covered
    if image_urls:
        for url in image_urls:
            if isinstance(url, str) and url.strip():
                media_blocks.append({"type": "image_url", "image_url": {"url": url.strip()}})

    return media_blocks, key_media_types
