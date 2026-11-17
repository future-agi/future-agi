"""Embedding Similarity and Semantic List Contains score the same in the backend.

Both system evals used to be sandboxed code (``CustomCodeEval``) that POSTed
to model serving from inside the code executor. The executor's egress guard
denies private and cluster addresses, so on hosted every run scored 0.0 with
"Embedding error". They now run as the backend functions behind the
``EmbeddingSimilarity`` and ``SemanticListContains`` evaluators
(``calculate_embedding_similarity`` and ``calculate_semantic_list_contains``).

The switch must not change a result. For the same inputs the backend gives
the sandbox code's score, its pass/fail at the templates' 0.5 threshold, and
its reason. Each expectation in the tables below was worked out by hand from
that code, which ``legacy_serving_evals/`` keeps byte for byte as it was
seeded at SYSTEM_EVALS_VERSION 28 (the ``.txt`` suffix keeps the YAML
formatter off it). A deterministic fake embedder makes every cosine an
exact ratio: integer vectors with integer norms, plus two unit vectors on
either side of the 4-place rounding boundary. Every row also runs through the
legacy code itself, with urlopen answered by the same fake, so a slip in a
hand-worked number fails as a table error, not as a backend difference.

A fake embedder is a fair stand-in because both paths reach the same model:
serving answers ``/model/v1/embed`` from one SentenceTransformer
('all-MiniLM-L6-v2'), and neither path sends a model name.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import sys
import types
import urllib.request
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import yaml
from requests.adapters import HTTPAdapter

from agentic_eval.core_evals.fi_evals.function.wrapper import (
    EmbeddingSimilarity,
    SemanticListContains,
)
from evaluations.engine.registry import get_eval_class
from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    _yaml_to_template_fields,
    load_yaml_evals,
)
from model_hub.utils.scoring import determine_pass_fail

LEGACY_DIR = Path(__file__).resolve().parent / "legacy_serving_evals"
SERVING = "http://serving-under-test:8080"
# Both templates' pass_threshold.
PASS_THRESHOLD = 0.5


def _unit(cos: float) -> tuple[float, float, float, float]:
    """A unit vector whose cosine with (1, 0, 0, 0) is ``cos``."""
    return (cos, math.sqrt(1 - cos * cos), 0.0, 0.0)


TEXT = {
    # embedding_similarity
    "The cat sat.": (1, 0, 0, 0),
    "A cat was sitting.": (5, 0, 0, 0),
    "refund issued": (1, 0, 0, 0),
    "money returned": (3, 4, 0, 0),
    "almost half": (1, 0, 0, 0),
    "half": _unit(0.499975),
    "just under half": (1, 0, 0, 0),
    "under half": _unit(0.49994),
    "up": (1, 0, 0, 0),
    "down": (-1, 0, 0, 0),
    "blank vector": (0, 0, 0, 0),
    "anything": (1, 0, 0, 0),
    "naïve café ☕": (4, 3, 0, 0),
    "naive cafe": (1, 0, 0, 0),
    "42": (0, 1, 0, 0),
    "a photo of a cat": (1, 0, 0, 0),
    "https://example.com/docs": (0, 0, 1, 0),
    # semantic_list_contains
    "Refunds take five days": (1, 0, 0, 0),
    "Shipping is free": (0, 1, 0, 0),
    "money back": (3, 4, 0, 0),
    "delivery cost": (1, 2, 2, 4),
    "Shipping": (0, 0, 0, 1),
    "The capital of France is Paris": (1, 1, 1, 1),
    "Paris": (0, 0, 1, 0),
    "Berlin": (3, -4, 0, 0),
    "Madrid": (0, 0, 3, -4),
    "Paris is lovely": (1, 0, 0, 0),
    "Berlin is cold": (0, 1, 0, 0),
    "We ship worldwide": (1, 0, 0, 0),
    "international delivery": (7, 1, 1, 7),
    "Orders go everywhere": (0, 0, 1, 0),
    "global shipping": (1, 1, 3, 5),
    "The weather is nice": (1, 0, 0, 0),
    "Your refund was issued": (0, 1, 0, 0),
    "Thanks": (0, 0, 1, 0),
    "You have 5 items": (0, 0, 0, 1),
    "5": (0, 0, 1, 0),
    "alpha": (1, 0, 0, 0),
    "{'a': 1}": (0, 1, 0, 0),
    "paris": (0, 1, 0, 0),
    "Café au lait": (1, 0, 0, 0),
    "Très bien": (0, 1, 0, 0),
    "CAFÉ": (0, 0, 1, 0),
    "咖啡": (3, 4, 0, 0),
    "...!!!": (1, 0, 0, 0),
    "dots": (2, 0, 0, 0),
    "rome wasn't built in a day": (1, 0, 0, 0),
    "Rome": (0, 1, 0, 0),
    "void": (0, 0, 0, 0),
}
IMAGE = {
    "https://cdn.example.com/cat.png": (3, 4, 0, 0),
    "https://cdn.example.com/kitten.JPG?size=large": (0, 1, 0, 0),
}
AUDIO = {
    "https://cdn.example.com/hello.wav": (1, 0, 0, 0),
    "https://cdn.example.com/hi.mp3?dl=1": (1, 1, 1, 1),
}


def _media_bytes(url: str) -> bytes:
    """The fake file behind a media URL: the URL itself, so the vector lookup
    can recover it from the data URI the evaluator sends to serving."""
    return url.encode()


class FakeServing:
    """Model serving with a fixed vector per input.

    It answers the HTTP API, for the legacy code's urlopen and for the
    backend's real ``ModelServingClient`` (through ``_ServingHTTP``), and the
    client's methods for the ``embedding_manager`` stand-in. An input missing
    from the tables gets a hash-derived vector and is recorded, so a table that
    misses one fails instead of silently scoring against an arbitrary vector.
    """

    def __init__(self):
        self.unknown = []
        self.media_headers = []
        # (path, timeout) of each request the backend's client sent.
        self.timeouts = []

    def _lookup(self, table, key):
        if key in table:
            return [float(x) for x in table[key]]
        self.unknown.append(key)
        digest = hashlib.sha256(str(key).encode()).digest()
        return [float(b) + 1.0 for b in digest[:4]]

    def _media(self, table, data_uri):
        header, _, payload = data_uri.partition(",")
        self.media_headers.append(header)
        return self._lookup(table, base64.b64decode(payload).decode())

    # The serving HTTP API.
    def answer(self, path, body):
        if path == "/embed":
            return {"embeddings": [self._lookup(TEXT, t) for t in body["text"]]}
        if path == "/embed/image":
            return {"embeddings": [self._media(IMAGE, body["image"])]}
        if path == "/embed/audio":
            return {"embeddings": [self._media(AUDIO, body["audio"])]}
        raise AssertionError(f"unexpected serving path {path}")

    # ModelServingClient.
    def embed_text_batch(self, texts):
        return [self._lookup(TEXT, t) for t in texts]

    def embed_text(self, text):
        # Like the real client: the first vector only, even for a list.
        return self._lookup(TEXT, text if isinstance(text, str) else text[0])

    def embed_image(self, image):
        return self._media(IMAGE, image)

    def embed_audio(self, audio):
        return self._media(AUDIO, audio)


class _ServingHTTP(HTTPAdapter):
    """The network behind a real ``ModelServingClient``'s session: ``fake``
    answers each request, which never leaves the process, and the timeout the
    client gave it is recorded. The backend's requests to serving run through
    the client's own code, timeouts included."""

    def __init__(self, fake):
        super().__init__()
        self.fake = fake

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        path = urlsplit(request.url).path.removeprefix("/model/v1")
        self.fake.timeouts.append((path, timeout))
        body = self.fake.answer(path, json.loads(request.body))
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(body).encode()
        response.url = request.url
        response.request = request
        return response


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, *_args):
        return self._body


@cache
def _legacy_evaluate(name):
    path = LEGACY_DIR / f"{name}.yaml.txt"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    namespace: dict = {}
    exec(compile(code, str(path), "exec"), namespace)  # noqa: S102 - repo fixture
    return namespace["evaluate"]


def _run_legacy(name, fake, monkeypatch, output, expected, params):
    """The sandboxed code, with serving and media downloads answered by
    ``fake``. Returns (score, reason) as custom_code_eval would store them."""

    def urlopen(request, timeout=None):
        url = request.full_url
        prefix = f"{SERVING}/model/v1"
        if url.startswith(prefix):
            body = json.loads(request.data)
            return _Response(json.dumps(fake.answer(url[len(prefix) :], body)).encode())
        return _Response(_media_bytes(url))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = _legacy_evaluate(name)(
        None, output, expected, None, _model_serving_url=SERVING, **params
    )
    # The sandbox renames "score" to "result"; custom_code_eval clamps it.
    return float(min(max(result["score"], 0), 1)), result["reason"]


def _no_embedding_manager(fake):
    """The real embedding_manager imports Django models and ClickHouse and
    alerts on a missing serving; this stand-in answers from ``fake``."""
    module = types.ModuleType("agentic_eval.core.embeddings.embedding_manager")
    module.model_manager = SimpleNamespace(
        text_model=lambda text, to_list=False: fake.embed_text(text),
        serving_client=fake,
    )
    return module


def _run_backend(evaluator, fake, monkeypatch, output, expected, params):
    """The backend evaluator, with model serving and downloads answered by
    ``fake``. Serving is a real ``ModelServingClient`` whose session ``fake``
    answers (the autouse ``_model_serving_reachable`` fixture passes its
    availability check). Returns (score, reason) as FunctionEvaluator stores
    them."""
    from agentic_eval.core.embeddings import serving_client

    client = serving_client.ModelServingClient(base_url=SERVING)
    client.session.mount("http://", _ServingHTTP(fake))
    monkeypatch.setattr(serving_client, "get_serving_client", lambda: client)
    monkeypatch.setitem(
        sys.modules,
        "agentic_eval.core.embeddings.embedding_manager",
        _no_embedding_manager(fake),
    )
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.safe_fetch",
        lambda url, **_kwargs: SimpleNamespace(
            status_code=200, content=_media_bytes(url), headers={}
        ),
    )
    result = evaluator._evaluate(output=output, expected=expected, **params)
    return result["metrics"][0]["value"], result["reason"]


# (output, expected, score, passed, reason)
EMBEDDING_SIMILARITY_CASES = {
    "parallel vectors of any length": (
        "The cat sat.",
        "A cat was sitting.",
        1.0,
        True,
        "Cosine similarity (text): 1.0000",
    ),
    "cosine 3/5": (
        "refund issued",
        "money returned",
        0.6,
        True,
        "Cosine similarity (text): 0.6000",
    ),
    "0.499975 rounds up to a pass": (
        "almost half",
        "half",
        0.5,
        True,
        "Cosine similarity (text): 0.5000",
    ),
    "0.49994 rounds down to a fail": (
        "just under half",
        "under half",
        0.4999,
        False,
        "Cosine similarity (text): 0.4999",
    ),
    "negative cosine scores 0": (
        "up",
        "down",
        0.0,
        False,
        "Cosine similarity (text): -1.0000",
    ),
    "zero vector scores 0": (
        "blank vector",
        "anything",
        0.0,
        False,
        "Cosine similarity (text): 0.0000",
    ),
    "unicode text": (
        "naïve café ☕",
        "naive cafe",
        0.8,
        True,
        "Cosine similarity (text): 0.8000",
    ),
    "a number is embedded as its text": (
        42,
        "42",
        1.0,
        True,
        "Cosine similarity (text): 1.0000",
    ),
    "a URL without a media extension is text": (
        "https://example.com/docs",
        "https://example.com/docs",
        1.0,
        True,
        "Cosine similarity (text): 1.0000",
    ),
    "image URLs": (
        "https://cdn.example.com/cat.png",
        "https://cdn.example.com/kitten.JPG?size=large",
        0.8,
        True,
        "Cosine similarity (image): 0.8000",
    ),
    "audio URLs at exactly the threshold": (
        "https://cdn.example.com/hello.wav",
        "https://cdn.example.com/hi.mp3?dl=1",
        0.5,
        True,
        "Cosine similarity (audio): 0.5000",
    ),
    "text against an image URL": (
        "a photo of a cat",
        "https://cdn.example.com/cat.png",
        0.6,
        True,
        "Cosine similarity (text-vs-image): 0.6000",
    ),
    "missing output": (
        None,
        "anything",
        0.0,
        False,
        "Missing output or expected value",
    ),
    "missing expected": (
        "anything",
        None,
        0.0,
        False,
        "Missing output or expected value",
    ),
    "empty output": ("", "anything", 0.0, False, "Empty output"),
    "blank output": ("   ", "anything", 0.0, False, "Empty output"),
    "empty expected": ("anything", "", 0.0, False, "Empty expected value"),
    "blank expected": ("anything", "\n\t", 0.0, False, "Empty expected value"),
}

# (output, expected, params, score, passed, reason)
SEMANTIC_LIST_CONTAINS_CASES = {
    "comma-separated phrases, one semantic match": (
        "Refunds take five days. Shipping is free!",
        "money back, delivery cost",
        {},
        0.5,
        True,
        "Matched 1/2 phrases (threshold=0.5; partial matches allowed). "
        "'money back': 0.8; 'delivery cost': 0.4.",
    ),
    "two of three, one by substring": (
        "Refunds take five days. Shipping is free!",
        "money back, delivery cost, Shipping",
        {},
        0.6667,
        True,
        "Matched 2/3 phrases (threshold=0.5; partial matches allowed). "
        "'money back': 0.8; 'delivery cost': 0.4; 'Shipping': 1.0.",
    ),
    "JSON list, one of three is a fail": (
        "The capital of France is Paris.",
        '["Paris", "Berlin", "Madrid"]',
        {},
        0.3333,
        False,
        "Matched 1/3 phrases (threshold=0.5; partial matches allowed). "
        "'Paris': 1.0; 'Berlin': -0.1; 'Madrid': -0.1.",
    ),
    "match_all with one of three": (
        "The capital of France is Paris.",
        '["Paris", "Berlin", "Madrid"]',
        {"match_all": True},
        0.0,
        False,
        "Matched 1/3 phrases (threshold=0.5; all phrases required). "
        "'Paris': 1.0; 'Berlin': -0.1; 'Madrid': -0.1.",
    ),
    "match_all with every phrase, given as a list": (
        "Paris is lovely. Berlin is cold.",
        ["Paris", "Berlin"],
        {"match_all": True},
        1.0,
        True,
        "Matched 2/2 phrases (threshold=0.5; all phrases required). "
        "'Paris': 1.0; 'Berlin': 1.0.",
    ),
    "similarity exactly at a 0.7 threshold matches": (
        "We ship worldwide.",
        "international delivery",
        {"similarity_threshold": 0.7},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.7; partial matches allowed). "
        "'international delivery': 0.7.",
    ),
    "a threshold given as text": (
        "We ship worldwide.",
        "international delivery",
        {"similarity_threshold": "0.7"},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.7; partial matches allowed). "
        "'international delivery': 0.7.",
    ),
    "a 0.8 threshold rejects 0.7": (
        "We ship worldwide.",
        "international delivery",
        {"similarity_threshold": 0.8},
        0.0,
        False,
        "Matched 0/1 phrases (threshold=0.8; partial matches allowed). "
        "'international delivery': 0.7.",
    ),
    "an integer threshold": (
        "We ship worldwide.",
        "international delivery",
        {"similarity_threshold": 1},
        0.0,
        False,
        "Matched 0/1 phrases (threshold=1.0; partial matches allowed). "
        "'international delivery': 0.7.",
    ),
    "the default threshold is 0.5, and 0.5 matches": (
        "Orders go everywhere",
        "global shipping",
        {},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). "
        "'global shipping': 0.5.",
    ),
    "the best sentence decides": (
        "The weather is nice. Your refund was issued.\nThanks!",
        '["money returned"]',
        {"similarity_threshold": 0.7},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.7; partial matches allowed). "
        "'money returned': 0.8.",
    ),
    "a JSON number is one phrase": (
        "You have 5 items",
        "5",
        {},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). '5': 1.0.",
    ),
    "a JSON object is one phrase": (
        "alpha",
        '{"a": 1}',
        {},
        0.0,
        False,
        "Matched 0/1 phrases (threshold=0.5; partial matches allowed). "
        "'{'a': 1}': 0.0.",
    ),
    "a JSON string is one phrase": (
        "paris!",
        '"Paris"',
        {},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). 'Paris': 1.0.",
    ),
    "a number as the output": (
        42,
        '["42"]',
        {},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). '42': 1.0.",
    ),
    "unicode, a case-insensitive substring and a semantic match": (
        "Café au lait. Très bien!",
        '["CAFÉ", "咖啡"]',
        {},
        1.0,
        True,
        "Matched 2/2 phrases (threshold=0.5; partial matches allowed). "
        "'CAFÉ': 1.0; '咖啡': 0.8.",
    ),
    "an output with no sentence text is one sentence": (
        "...!!!",
        '["dots"]',
        {},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). 'dots': 1.0.",
    ),
    "a repeated phrase counts twice and is listed once": (
        "Paris",
        '["Paris", "Paris"]',
        {},
        1.0,
        True,
        "Matched 2/2 phrases (threshold=0.5; partial matches allowed). 'Paris': 1.0.",
    ),
    "phrases are trimmed": (
        "rome wasn't built in a day",
        '[" Rome "]',
        {},
        1.0,
        True,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). 'Rome': 1.0.",
    ),
    "a zero-vector sentence matches nothing": (
        "void",
        '["anything"]',
        {},
        0.0,
        False,
        "Matched 0/1 phrases (threshold=0.5; partial matches allowed). "
        "'anything': 0.0.",
    ),
    "an empty JSON list": (
        "anything",
        "[]",
        {},
        0.0,
        False,
        "No expected phrases provided",
    ),
    "no expected value": (
        "anything",
        None,
        {},
        0.0,
        False,
        "No expected phrases provided",
    ),
    "only commas": ("anything", " , ,", {}, 0.0, False, "No expected phrases provided"),
    "neither value, expected is checked first": (
        None,
        None,
        {},
        0.0,
        False,
        "No expected phrases provided",
    ),
    "empty output": ("", '["x"]', {}, 0.0, False, "No output text to evaluate"),
    "no output": (None, '["x"]', {}, 0.0, False, "No output text to evaluate"),
    "blank output": ("  \n ", '["x"]', {}, 0.0, False, "No output text to evaluate"),
}


@pytest.mark.parametrize(
    "output, expected, score, passed, reason",
    list(EMBEDDING_SIMILARITY_CASES.values()),
    ids=list(EMBEDDING_SIMILARITY_CASES),
)
def test_embedding_similarity_matches_the_yaml_code(
    output, expected, score, passed, reason, monkeypatch
):
    fake = FakeServing()
    legacy = _run_legacy(
        "embedding_similarity", fake, monkeypatch, output, expected, {}
    )
    assert legacy == (score, reason), "the hand-worked row disagrees with the YAML"
    assert determine_pass_fail(legacy[0], PASS_THRESHOLD) is passed
    assert fake.unknown == []
    legacy_media = fake.media_headers

    fake = FakeServing()
    backend = _run_backend(
        EmbeddingSimilarity(), fake, monkeypatch, output, expected, {}
    )

    assert backend == (score, reason)
    assert determine_pass_fail(backend[0], PASS_THRESHOLD) is passed
    assert fake.unknown == []
    # Media goes to serving as the same data URIs the sandbox code sent.
    assert fake.media_headers == legacy_media


@pytest.mark.parametrize(
    "output, expected, params, score, passed, reason",
    list(SEMANTIC_LIST_CONTAINS_CASES.values()),
    ids=list(SEMANTIC_LIST_CONTAINS_CASES),
)
def test_semantic_list_contains_matches_the_yaml_code(
    output, expected, params, score, passed, reason, monkeypatch
):
    fake = FakeServing()
    legacy = _run_legacy(
        "semantic_list_contains", fake, monkeypatch, output, expected, params
    )
    assert legacy == (score, reason), "the hand-worked row disagrees with the YAML"
    assert determine_pass_fail(legacy[0], PASS_THRESHOLD) is passed
    assert fake.unknown == []

    fake = FakeServing()
    backend = _run_backend(
        SemanticListContains(), fake, monkeypatch, output, expected, params
    )

    assert backend == (score, reason)
    assert determine_pass_fail(backend[0], PASS_THRESHOLD) is passed
    assert fake.unknown == []


class _ServingDown(FakeServing):
    def answer(self, path, body):
        raise OSError("serving answered 503")

    def embed_text_batch(self, texts):
        raise RuntimeError("serving answered 503")

    embed_text = embed_text_batch


@pytest.mark.parametrize(
    "name, evaluator, legacy_reason",
    [
        (
            "embedding_similarity",
            EmbeddingSimilarity,
            "Embedding error (text/text): serving answered 503",
        ),
        (
            "semantic_list_contains",
            SemanticListContains,
            "Embedding service error: serving answered 503",
        ),
    ],
)
def test_an_embedding_failure_is_an_error_not_a_zero_score(
    name, evaluator, legacy_reason, monkeypatch
):
    """The one intended difference: the sandbox code scored a failed embedding
    call 0.0, which reads as a real result. The backend fails the eval."""
    output, expected = "refund issued", "money returned"

    assert _run_legacy(name, _ServingDown(), monkeypatch, output, expected, {}) == (
        0.0,
        legacy_reason,
    )
    with pytest.raises(ValueError, match="serving answered 503"):
        _run_backend(evaluator(), _ServingDown(), monkeypatch, output, expected, {})


# The sandboxed code gave serving's audio endpoint 60 seconds, and the code
# executor ended the whole run after 30. In the backend nothing else bounds
# that request, so the request itself must: ModelServingClient.embed_audio
# would wait 3,000,000 seconds (about 35 days).
AUDIO_TIMEOUT_SECONDS = 60
AUDIO_PAIR = (
    "https://cdn.example.com/hello.wav",
    "https://cdn.example.com/hi.mp3?dl=1",
)


def test_the_audio_request_is_bounded_like_the_yaml_code(monkeypatch):
    fake = FakeServing()

    backend = _run_backend(EmbeddingSimilarity(), fake, monkeypatch, *AUDIO_PAIR, {})

    assert backend == (0.5, "Cosine similarity (audio): 0.5000")
    assert fake.timeouts == [
        ("/embed/audio", AUDIO_TIMEOUT_SECONDS),
        ("/embed/audio", AUDIO_TIMEOUT_SECONDS),
    ]


class _AudioStalls(FakeServing):
    """Serving takes the audio request and never answers, so the client's
    session read times out."""

    def answer(self, path, body):
        if path == "/embed/audio":
            raise requests.exceptions.ReadTimeout("serving stopped answering")
        return super().answer(path, body)


def test_a_stalled_audio_request_ends_as_an_error(monkeypatch):
    fake = _AudioStalls()

    with pytest.raises(ValueError) as raised:
        _run_backend(EmbeddingSimilarity(), fake, monkeypatch, *AUDIO_PAIR, {})

    assert str(raised.value) == (
        "Embedding error (audio/audio): Request timed out after "
        f"{AUDIO_TIMEOUT_SECONDS}s"
    )
    assert fake.timeouts == [("/embed/audio", AUDIO_TIMEOUT_SECONDS)]


def test_extra_run_kwargs_are_ignored(monkeypatch):
    """Every surface hands the evaluator more than output and expected (the
    engine's serving URL, row context, ground truth); none may fail the run."""
    extra = {
        "_model_serving_url": SERVING,
        "row_context": {"a": 1},
        "input": None,
        "context": None,
    }

    assert _run_backend(
        EmbeddingSimilarity(),
        FakeServing(),
        monkeypatch,
        "refund issued",
        "money returned",
        extra,
    ) == (0.6, "Cosine similarity (text): 0.6000")
    assert _run_backend(
        SemanticListContains(),
        FakeServing(),
        monkeypatch,
        "Orders go everywhere",
        "global shipping",
        extra,
    ) == (
        1.0,
        "Matched 1/1 phrases (threshold=0.5; partial matches allowed). "
        "'global shipping': 0.5.",
    )


# ─────────────────────────────────────────────────────────────────────
# The seeded templates
# ─────────────────────────────────────────────────────────────────────


@cache
def _definitions():
    return {definition["name"]: definition for definition in load_yaml_evals()}


@pytest.mark.parametrize(
    "name, eval_type_id, evaluator",
    [
        ("embedding_similarity", "EmbeddingSimilarity", EmbeddingSimilarity),
        ("semantic_list_contains", "SemanticListContains", SemanticListContains),
    ],
)
def test_the_system_template_runs_the_backend_function(name, eval_type_id, evaluator):
    fields = _yaml_to_template_fields(copy.deepcopy(_definitions()[name]))
    config = fields["config"]

    assert config["eval_type_id"] == eval_type_id
    assert "code" not in config
    assert get_eval_class(eval_type_id) is evaluator
    # What a binding sees is unchanged: a code-type, score eval on output and
    # expected that passes at 0.5. eval_type "code" is what forwards a
    # binding's params as run kwargs on the dataset, preview and playground.
    assert fields["owner"] == "system"
    assert fields["eval_type"] == "code"
    assert fields["pass_threshold"] == PASS_THRESHOLD
    assert config["output"] == "score"
    assert config["required_keys"] == ["output", "expected"]


def test_semantic_list_contains_keeps_its_parameter_defaults():
    params = _definitions()["semantic_list_contains"]["config"]["config"]

    assert params["similarity_threshold"]["default"] == 0.5
    assert params["match_all"]["default"] is False


def test_existing_databases_reseed_the_switched_templates():
    # The seeder skips a database already at this version.
    assert SYSTEM_EVALS_VERSION >= 29
