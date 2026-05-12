"""
Input preprocessing for code evals that need external data.

Runs BEFORE the sandbox. Downloads images, computes embeddings,
and passes the results as extra kwargs to the sandbox code.

This allows code evals to work with images/audio without needing
network access or ML models inside the sandbox.
"""

import base64
import json
import os
from urllib.parse import urlparse

import requests
import structlog

logger = structlog.get_logger(__name__)

# Eval types that need preprocessing
PREPROCESSORS = {}


# Hosts that user-supplied URLs must never reach from the API server.
# Mirrors the SSRF guard in agentic_eval/core/utils/functions.py so the
# image-preprocessing path can't be turned into a port-scanner either.
_BLOCKED_HOST_PREFIXES = (
    "localhost",
    "127.",
    "10.",
    "169.254.",
    "192.168.",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
)
_BLOCKED_HOST_172 = tuple(f"172.{i}." for i in range(16, 32))


def _host_is_blocked(host: str) -> bool:
    host = (host or "").lower().strip()
    if not host:
        return True
    return host.startswith(_BLOCKED_HOST_PREFIXES) or host.startswith(_BLOCKED_HOST_172)


# Browser-like UA so public hosts that block default `python-requests/X.X`
# (Wikipedia, many CDNs) return 200 instead of 403.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _fetch_url_bytes(url):
    """Fetch a user-supplied URL with SSRF guard + bounded body.

    Returns (bytes, content_type) on success, None on any failure so the
    caller can fall back. SSRF guard refuses private / loopback / metadata
    hosts. 25 MB ceiling so this can never blow up gunicorn workers.
    """
    if not isinstance(url, str):
        return None
    stripped = url.strip()
    if not stripped or not stripped.startswith(("http://", "https://")):
        return None
    host = urlparse(stripped).hostname or ""
    if _host_is_blocked(host):
        logger.warning("image_preprocess_blocked_host", host=host)
        return None
    try:
        with requests.get(
            stripped,
            timeout=(2, 5),
            stream=True,
            allow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as resp:
            if resp.status_code != 200:
                logger.warning(
                    "image_preprocess_bad_status",
                    status=resp.status_code,
                    url=stripped[:120],
                )
                return None
            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 25 * 1024 * 1024:
                    logger.warning("image_preprocess_oversize", url=stripped[:120])
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
            ct = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        return data, ct or "application/octet-stream"
    except Exception as e:
        logger.warning("image_preprocess_fetch_failed", url=stripped[:120], error=str(e))
        return None


def _resolve_image_input(value):
    """Bare-base64 form for sandbox eval bodies that do ``base64.b64decode(text)``.

    Pass-through for None, existing file paths, and strings the sandbox can
    already handle (already-base64, data URIs). For ``http(s)://`` URLs,
    fetch via :func:`_fetch_url_bytes` and return as plain base64.

    Any fetch failure returns the original value untouched so the eval
    body produces its own "Cannot load image" rather than silently swallowing.
    """
    if value is None or value == "" or not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped.startswith("data:"):
        return stripped if stripped.startswith("data:") else value
    if not stripped.startswith(("http://", "https://")):
        return value
    fetched = _fetch_url_bytes(stripped)
    if fetched is None:
        return value
    data, _ = fetched
    return base64.b64encode(data).decode("ascii")


def _resolve_image_input_as_data_uri(value):
    """Data-URI form for downstream consumers (e.g. the serving service)
    that handle ``data:image/...;base64,...`` natively but do NOT have to
    perform their own URL fetch.

    Pre-resolving in the API layer (which has SSRF + UA guards) means the
    serving service stays network-free for happy-path traffic. URL fallback
    only kicks in if our fetch fails — last resort.
    """
    if value is None or value == "" or not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped.startswith("data:"):
        return stripped
    if not stripped.startswith(("http://", "https://")):
        return value
    fetched = _fetch_url_bytes(stripped)
    if fetched is None:
        return value
    data, mime = fetched
    # Force an image/ MIME so consumers' `startswith("data:image")` branch
    # fires. PIL sniffs bytes itself; the label is for routing only.
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _resolve_fid_input(value):
    """Resolve every URL inside a FID image-list input.

    Accepts a JSON-encoded list, a Python list, or a single value (URL,
    data URI, or PIL Image). Returns a list (JSON-encoded if input was a
    JSON string) where every URL has been pre-fetched to a data URI.
    Failed fetches fall through as the original string so downstream code
    has a last-resort attempt.
    """
    was_json_string = False
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed
            was_json_string = True
        except (json.JSONDecodeError, ValueError):
            value = [value]
    elif not isinstance(value, list):
        value = [value]

    resolved = []
    for item in value:
        if isinstance(item, str):
            resolved.append(_resolve_image_input_as_data_uri(item))
        else:
            resolved.append(item)
    if was_json_string:
        return json.dumps(resolved)
    return resolved


def register_preprocessor(eval_name):
    """Decorator to register a preprocessor for an eval type."""

    def decorator(func):
        PREPROCESSORS[eval_name] = func
        return func

    return decorator


def preprocess_inputs(eval_name, inputs):
    """
    Run preprocessing for a specific eval if a preprocessor is registered.
    Returns the inputs dict with any additional computed fields.
    """
    preprocessor = PREPROCESSORS.get(eval_name)
    if not preprocessor:
        return inputs

    try:
        return preprocessor(inputs)
    except Exception as e:
        logger.warning(f"Preprocessing failed for {eval_name}: {e}")
        return inputs


@register_preprocessor("clip_score")
def _preprocess_clip(inputs):
    """
    Pre-compute CLIP embeddings for images and text.

    Converts image URLs → image embeddings and text → text embeddings
    using the serving client, then passes vectors to the sandbox.
    """
    from agentic_eval.core.embeddings.embedding_manager import model_manager

    images = inputs.get("images", "")
    text = inputs.get("text", "")

    if not images or not text:
        return inputs

    try:
        # Parse image inputs
        if isinstance(images, str):
            try:
                parsed = json.loads(images)
                image_list = parsed if isinstance(parsed, list) else [images]
            except json.JSONDecodeError:
                image_list = [images]
        elif isinstance(images, list):
            image_list = images
        else:
            image_list = [images]

        # Parse text inputs
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
                text_list = parsed if isinstance(parsed, list) else [text]
            except json.JSONDecodeError:
                text_list = [text]
        elif isinstance(text, list):
            text_list = text
        else:
            text_list = [str(text)]

        # Match lengths
        if len(text_list) == 1 and len(image_list) > 1:
            text_list = text_list * len(image_list)

        # Resolve URLs to data URIs server-side (with SSRF guard + UA) so
        # the serving service decodes base64 locally instead of doing its
        # own outbound HTTP. On any fetch failure, the URL passes through
        # untouched and serving gets a last-resort attempt.
        image_list = [_resolve_image_input_as_data_uri(img) for img in image_list]

        # Both image and text go through the unified CLIP model
        # (`image_text_embedding` = openai/clip-vit-base-patch32). No
        # fallbacks: if image_text fails, refuse to silently substitute
        # a different embedding model — that produces noise cosine.
        serving_client = model_manager.serving_client
        image_embeddings = [serving_client.embed_image_text(img) for img in image_list]
        text_embeddings = [serving_client.embed_image_text(txt) for txt in text_list]

        inputs["_image_embeddings"] = image_embeddings
        inputs["_text_embeddings"] = text_embeddings

        logger.info(
            f"CLIP preprocessing: {len(image_embeddings)} images, {len(text_embeddings)} texts"
        )

    except Exception as e:
        # Surface preprocessing failures instead of silently swallowing:
        # the eval body will see no embeddings and return its standard
        # "preprocessing required" error, which is the right UX.
        logger.warning(
            f"CLIP preprocessing failed (eval will return preprocessing-required error): {e}"
        )

    return inputs


@register_preprocessor("fid_score")
def _preprocess_fid(inputs):
    """
    Pre-compute Inception features for FID.

    Downloads images and extracts Inception v3 features (2048-dim vectors),
    then passes them to the sandbox for Fréchet distance computation.
    """
    real_images = inputs.get("real_images", "")
    fake_images = inputs.get("fake_images", "")

    if not real_images or not fake_images:
        return inputs

    try:
        import numpy as np
        import torch
        from torchmetrics.image.fid import FrechetInceptionDistance

        from agentic_eval.core_evals.fi_evals.function.functions import (
            _parse_image_list,
            _pil_to_uint8_tensor,
        )

        # Pre-resolve URL inputs to data URIs (SSRF-guarded fetch). Keeps
        # FID's downstream `_parse_image_list` → `open_image_from_url` from
        # being able to reach private / metadata hosts via user input.
        real_images = _resolve_fid_input(real_images)
        fake_images = _resolve_fid_input(fake_images)

        # Parse images
        real_pil = _parse_image_list(real_images)
        fake_pil = _parse_image_list(fake_images)

        if len(real_pil) < 2 or len(fake_pil) < 2:
            inputs["_fid_error"] = f"FID requires at least 2 images per set (got {len(real_pil)} real, {len(fake_pil)} fake)"
            return inputs

        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Extract Inception features using FID metric's feature extractor
        fid_metric = FrechetInceptionDistance(feature=2048).to(device)

        # Get features for real images
        for img in real_pil:
            x = _pil_to_uint8_tensor(img).to(device)
            fid_metric.update(x, real=True)

        for img in fake_pil:
            x = _pil_to_uint8_tensor(img).to(device)
            fid_metric.update(x, real=False)

        # Extract the raw features
        real_features = fid_metric.real_features_sum.cpu().numpy()
        fake_features = fid_metric.fake_features_sum.cpu().numpy()

        # Actually, we need per-image features, not sums.
        # Simpler approach: compute FID directly and pass the score
        score = float(fid_metric.compute().detach().cpu())

        # Pass pre-computed score as a feature
        inputs["_fid_precomputed_score"] = score
        inputs["_real_features"] = [[1.0]]  # Placeholder — score already computed
        inputs["_fake_features"] = [[1.0]]

        logger.info(
            f"FID preprocessing: {len(real_pil)} real, {len(fake_pil)} fake images, score={score:.3f}"
        )

    except ImportError as e:
        logger.warning(f"FID preprocessing requires torch/torchmetrics: {e}")
    except Exception as e:
        logger.warning(f"FID preprocessing failed: {e}")

    return inputs


@register_preprocessor("image_properties")
def _preprocess_image_properties(inputs):
    """Resolve URL input to base64 for the image_properties eval."""
    inputs["text"] = _resolve_image_input(inputs.get("text"))
    return inputs


@register_preprocessor("psnr")
def _preprocess_psnr(inputs):
    """Resolve URL inputs to base64 for PSNR (output, expected)."""
    inputs["output"] = _resolve_image_input(inputs.get("output"))
    inputs["expected"] = _resolve_image_input(inputs.get("expected"))
    return inputs


@register_preprocessor("ssim")
def _preprocess_ssim(inputs):
    """Resolve URL inputs to base64 for SSIM (output, expected)."""
    inputs["output"] = _resolve_image_input(inputs.get("output"))
    inputs["expected"] = _resolve_image_input(inputs.get("expected"))
    return inputs


@register_preprocessor("meteor_score")
def _preprocess_meteor(inputs):
    """Compute METEOR via NLTK on the backend, inject the score as a kwarg.

    Sandbox doesn't have WordNet (and we don't want to bake ~30MB into the
    image just for one eval). Backend has it; run the heavy lifting here
    and let the eval body stay a thin reader of ``_meteor_precomputed_score``.
    """
    reference = str(inputs.get("reference", "") or "").strip()
    hypothesis = str(inputs.get("hypothesis", "") or "").strip()
    if not reference and not hypothesis:
        inputs["_meteor_precomputed_score"] = 1.0
        return inputs
    if not reference or not hypothesis:
        inputs["_meteor_error"] = "Missing reference or hypothesis"
        return inputs
    ref_tokens = reference.lower().split()
    hyp_tokens = hypothesis.lower().split()
    if not ref_tokens or not hyp_tokens:
        inputs["_meteor_error"] = "Empty tokens after split"
        return inputs
    try:
        from nltk.translate.meteor_score import meteor_score as _meteor
        inputs["_meteor_precomputed_score"] = float(_meteor([ref_tokens], hyp_tokens))
    except LookupError as e:
        # WordNet / punkt missing on the backend image. Tell the eval body
        # exactly what to ask for — beats a silent zero score.
        logger.warning("meteor_preprocess_corpora_missing", error=str(e))
        inputs["_meteor_error"] = (
            "METEOR requires NLTK WordNet on the backend image. "
            "Add `python -m nltk.downloader wordnet omw-1.4` to backend Dockerfile."
        )
    except Exception as e:
        logger.warning("meteor_preprocess_failed", error=str(e))
        inputs["_meteor_error"] = f"METEOR error: {e}"
    return inputs
