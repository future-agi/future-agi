"""Embedding Similarity and Semantic List Contains, scored in the backend.

Both built-in evals used to be sandboxed code that POSTed to model serving
from inside the code executor. The executor's egress guard denies private and
cluster addresses, so those calls never arrived: on hosted, and on self-hosted
installs that run serving beside the executor, every run scored 0.0 with
"Embedding error". The scoring below is that code, ported line for line from
the YAML seeded at SYSTEM_EVALS_VERSION 28, with each HTTP call replaced by an
``Embedder``. ``ServingEmbedder`` sends serving the same requests from the
backend process.

One deliberate difference: where that code returned 0.0 on an embedding
failure, these raise ``EmbeddingError``, so the eval ends as an ERROR that
carries the reason. Malformed successful responses are embedding failures too;
validation preserves the scoring contract only for well-formed vectors.
"""

from __future__ import annotations

import base64
import json
import math
import re
from typing import Any, Protocol

from agentic_eval.core.embeddings.serving_client import ServingUnavailableError
from agentic_eval.core.embeddings.validation import validate_embedding_batch

_URL = re.compile(r"https?://")
_AUDIO_URL = re.compile(r"\.(mp3|wav|ogg|m4a|flac|aac)(\?|$)", re.I)
_IMAGE_URL = re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|bmp|tiff)(\?|$)", re.I)
_SENTENCE_BREAKS = re.compile(r"[.!?\n]+")

# How the sandboxed code downloaded an image or audio URL.
_DOWNLOAD_HEADERS = {"User-Agent": "FutureAGI-Eval/1.0"}
_DOWNLOAD_TIMEOUT_SECONDS = 15
# The sandboxed code's timeout for serving's audio endpoint. The executor also
# ended the whole run after 30 seconds; in the backend nothing else bounds the
# request, and ModelServingClient.embed_audio allows 3,000,000 seconds.
_AUDIO_EMBED_TIMEOUT_SECONDS = 60


class Embedder(Protocol):
    def texts(self, texts: list[str]) -> list[list[float]]: ...

    def image(self, url: str) -> list[float]: ...

    def audio(self, url: str) -> list[float]: ...


class EmbeddingError(ValueError):
    """An embedding call failed. A ValueError, so eval error handling shows
    this reason instead of a generic failure."""


class ServingEmbedder:
    """Model serving, called from the backend with the requests the sandboxed
    code sent: one ``/embed`` batch for all texts, and for an image or audio
    URL a data URI of the downloaded file."""

    def __init__(self, client=None):
        self._client = client

    def _serving(self):
        if self._client is None:
            from agentic_eval.core.embeddings import serving_client

            self._client = serving_client.get_serving_client()
        return self._client

    def texts(self, texts):
        return self._serving().embed_text_batch(list(texts))

    def image(self, url):
        return self._serving().embed_image(_download_as_data_uri(url, "image/jpeg"))

    def audio(self, url):
        # embed_audio's request, with a bounded timeout. The client's other
        # callers keep embed_audio as it is.
        response = self._serving()._make_request(
            "/embed/audio",
            {
                "audio": _download_as_data_uri(url, "audio/wav"),
                "input_type": "audio",
            },
            timeout=_AUDIO_EMBED_TIMEOUT_SECONDS,
        )
        return response["embeddings"]


def _download_as_data_uri(url: str, mime: str) -> str:
    """Download through the SSRF guard: the URL is user input, and the backend,
    unlike the sandbox, can reach private addresses."""
    from tfc.utils.ssrf_guard import safe_fetch

    response = safe_fetch(
        url,
        method="GET",
        headers=_DOWNLOAD_HEADERS,
        timeout=_DOWNLOAD_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code} downloading {url}")
    return f"data:{mime};base64,{base64.b64encode(response.content).decode()}"


def cosine(a, b) -> float:
    """Cosine similarity, 0.0 when either vector is all zeros."""
    validate_embedding_batch([a, b], 2)
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _modality(value: str) -> str:
    """The modality to embed a value in: audio or image for a URL with that
    kind of file extension, else text."""
    s = str(value).strip() if value else ""
    is_url = _URL.match(s)
    if is_url and _AUDIO_URL.search(s):
        return "audio"
    if is_url and _IMAGE_URL.search(s):
        return "image"
    return "text"


def _embed(embedder: Embedder, modality: str, value: str) -> list[float]:
    if modality == "image":
        return embedder.image(value)
    if modality == "audio":
        return embedder.audio(value)
    embeddings = embedder.texts([value])
    validate_embedding_batch(embeddings, 1)
    return embeddings[0]


def _result(score: float, reason: str) -> dict[str, Any]:
    return {"result": score, "reason": reason}


def embedding_similarity(output: Any, expected: Any, embedder: Embedder) -> dict:
    """Cosine similarity of the output's and the expected value's embeddings.

    Each side is embedded in its own modality: an image or audio URL as that
    media, anything else as text. The score is the similarity clamped to
    [0, 1] and rounded to 4 places.
    """
    if output is None or expected is None:
        return _result(0.0, "Missing output or expected value")
    if isinstance(output, str) and not output.strip():
        return _result(0.0, "Empty output")
    if isinstance(expected, str) and not expected.strip():
        return _result(0.0, "Empty expected value")

    generated, reference = str(output), str(expected)
    gen_type, ref_type = _modality(generated), _modality(reference)
    try:
        if gen_type == "text" and ref_type == "text":
            # One batch, as the sandboxed code sent it.
            embeddings = embedder.texts([generated, reference])
            validate_embedding_batch(embeddings, 2)
            emb_a, emb_b = embeddings[0], embeddings[1]
        else:
            emb_a = _embed(embedder, gen_type, generated)
            emb_b = _embed(embedder, ref_type, reference)
        similarity = cosine(emb_a, emb_b)
    except ServingUnavailableError:
        raise
    except Exception as e:
        raise EmbeddingError(f"Embedding error ({gen_type}/{ref_type}): {e}") from e

    modality = gen_type if gen_type == ref_type else f"{gen_type}-vs-{ref_type}"
    return _result(
        float(round(max(0.0, min(1.0, similarity)), 4)),
        f"Cosine similarity ({modality}): {similarity:.4f}",
    )


def _expected_phrases(expected: Any) -> list[str]:
    """A JSON value (a list, or one phrase), else a comma-separated string."""
    value = expected if expected is not None else ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:  # json.JSONDecodeError is one
            value = [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        value = [value]
    return [str(p).strip() for p in value]


def semantic_list_contains(
    output: Any,
    expected: Any,
    embedder: Embedder,
    similarity_threshold: Any = 0.5,
    match_all: Any = False,
) -> dict:
    """The share of expected phrases the output contains, or with
    ``match_all`` 1.0 only when it contains every one.

    A phrase is contained when it is a case-insensitive substring of the
    output, or when its embedding's cosine similarity with the best-matching
    sentence of the output reaches ``similarity_threshold``.
    """
    threshold = float(similarity_threshold)
    require_all = bool(match_all)

    generated = str(output) if output is not None else ""
    phrases = _expected_phrases(expected)
    if not phrases:
        return _result(0.0, "No expected phrases provided")
    if not generated.strip():
        return _result(0.0, "No output text to evaluate")

    # One embedding of a whole paragraph scores low against a single word, so
    # each phrase is compared with each sentence and keeps the best.
    sentences = [s.strip() for s in _SENTENCE_BREAKS.split(generated) if s.strip()]
    if not sentences:
        sentences = [generated]

    try:
        embeddings = embedder.texts(sentences + phrases)
        # Validate even vectors skipped by substring matches or best-match scoring.
        validate_embedding_batch(embeddings, len(sentences) + len(phrases))
        sentence_embeddings = embeddings[: len(sentences)]
        phrase_embeddings = embeddings[len(sentences) :]
    except ServingUnavailableError:
        raise
    except Exception as e:
        raise EmbeddingError(f"Embedding service error: {e}") from e

    generated_lower = generated.lower()
    matches = []
    similarities = {}
    for i, phrase in enumerate(phrases):
        if phrase.lower() in generated_lower:
            similarities[phrase] = 1.0
            matches.append(True)
            continue
        best = max(cosine(se, phrase_embeddings[i]) for se in sentence_embeddings)
        similarities[phrase] = round(best, 4)
        matches.append(best >= threshold)

    matched_count = sum(matches)
    total = len(matches)
    if require_all:
        score = 1.0 if total > 0 and matched_count == total else 0.0
    else:
        score = matched_count / total if total > 0 else 0.0

    details = "; ".join(f"'{k}': {v}" for k, v in similarities.items())
    mode = "all phrases required" if require_all else "partial matches allowed"
    reason = (
        f"Matched {matched_count}/{total} phrases (threshold={threshold}; {mode}). "
        f"{details}."
    )
    return _result(float(round(score, 4)), reason)
