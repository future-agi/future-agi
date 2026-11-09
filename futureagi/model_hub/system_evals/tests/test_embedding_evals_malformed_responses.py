"""Malformed HTTP 200 embeddings must fail the eval, not manufacture a score.

Keep the parity fixtures unchanged. These cases exercise the real serving
client at its HTTP boundary, including its media unwrapping and text batching.
Semantic List Contains treats even media URLs as text; only Similarity routes
those inputs to the image/audio endpoints.
"""

from __future__ import annotations

import socket

import pytest
import requests

from agentic_eval.core_evals.fi_evals.function.embedding_evals import EmbeddingError
from agentic_eval.core_evals.fi_evals.function.wrapper import (
    EmbeddingSimilarity,
    SemanticListContains,
)

from .test_embedding_evals_backend_parity import FakeServing, _run_backend

TEXT_PAIR = ("refund issued", "money returned")
IMAGE_PAIR = (
    "https://cdn.example.com/cat.png",
    "https://cdn.example.com/kitten.JPG?size=large",
)
AUDIO_PAIR = (
    "https://cdn.example.com/hello.wav",
    "https://cdn.example.com/hi.mp3?dl=1",
)
EVAL_CASES = {
    "similarity-text": (EmbeddingSimilarity, *TEXT_PAIR),
    "similarity-image": (EmbeddingSimilarity, *IMAGE_PAIR),
    "similarity-audio": (EmbeddingSimilarity, *AUDIO_PAIR),
    "similarity-text-image": (EmbeddingSimilarity, TEXT_PAIR[0], IMAGE_PAIR[0]),
    "similarity-image-text": (EmbeddingSimilarity, IMAGE_PAIR[0], TEXT_PAIR[0]),
    "similarity-text-audio": (EmbeddingSimilarity, TEXT_PAIR[0], AUDIO_PAIR[0]),
    "similarity-audio-text": (EmbeddingSimilarity, AUDIO_PAIR[0], TEXT_PAIR[0]),
    "similarity-image-audio": (EmbeddingSimilarity, IMAGE_PAIR[0], AUDIO_PAIR[0]),
    "similarity-audio-image": (EmbeddingSimilarity, AUDIO_PAIR[0], IMAGE_PAIR[0]),
    "contains-text": (SemanticListContains, *TEXT_PAIR),
    "contains-substring": (SemanticListContains, "refund issued", "refund"),
    "contains-image-url": (SemanticListContains, IMAGE_PAIR[0], "money returned"),
    "contains-audio-url": (SemanticListContains, AUDIO_PAIR[0], "money returned"),
}
MALFORMED_VECTORS = {
    "empty": [],
    "dimension": [1.0],
    "non-numeric": [1.0, "not-a-number", 0.0, 0.0],
    "boolean": [True, 0.0, 0.0, 0.0],
    "null-entry": [None, 0.0, 0.0, 0.0],
    "nan": [float("nan"), 0.0, 0.0, 0.0],
    "infinity": [float("inf"), 0.0, 0.0, 0.0],
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("network access is forbidden in embedding response tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


class _ChangedResponse(FakeServing):
    def __init__(self, change, response_number=1):
        super().__init__()
        self.change = change
        self.response_number = response_number
        self.requests = 0

    def answer(self, path, body):
        response = super().answer(path, body)
        self.requests += 1
        if self.requests == self.response_number:
            return self.change(response)
        return response


def _replace_last_vector(vector):
    def change(response):
        response["embeddings"][-1] = vector
        return response

    return change


@pytest.mark.parametrize("case", EVAL_CASES)
@pytest.mark.parametrize("defect", MALFORMED_VECTORS)
def test_malformed_vectors_error_before_scoring(case, defect, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]
    fake = _ChangedResponse(_replace_last_vector(MALFORMED_VECTORS[defect]))

    with pytest.raises(EmbeddingError, match="Invalid embedding response:"):
        _run_backend(evaluator(), fake, monkeypatch, output, expected, {})


@pytest.mark.parametrize(
    "case", ["similarity-image", "similarity-audio", "similarity-image-audio"]
)
@pytest.mark.parametrize("defect", MALFORMED_VECTORS)
def test_second_media_vector_is_validated(case, defect, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]
    fake = _ChangedResponse(
        _replace_last_vector(MALFORMED_VECTORS[defect]), response_number=2
    )

    with pytest.raises(EmbeddingError, match="Invalid embedding response:"):
        _run_backend(evaluator(), fake, monkeypatch, output, expected, {})


@pytest.mark.parametrize("case", EVAL_CASES)
@pytest.mark.parametrize("count_change", [-1, 1], ids=["missing", "extra"])
def test_vector_count_must_match_inputs(case, count_change, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]

    def change(response):
        if count_change < 0:
            response["embeddings"].pop()
        else:
            response["embeddings"].append([1.0, 0.0, 0.0, 0.0])
        return response

    with pytest.raises(EmbeddingError, match="Invalid embedding response:"):
        _run_backend(
            evaluator(), _ChangedResponse(change), monkeypatch, output, expected, {}
        )


@pytest.mark.parametrize(
    "case", ["similarity-text", "similarity-image", "similarity-audio", "contains-text"]
)
@pytest.mark.parametrize(
    "body",
    [None, [], {}, {"embeddings": None}, {"embeddings": "not-vectors"}],
    ids=["null-body", "list-body", "missing-field", "null-field", "string-field"],
)
def test_malformed_response_envelopes_error(case, body, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]
    fake = _ChangedResponse(lambda _response: body)

    with pytest.raises(EmbeddingError, match="Invalid embedding response:"):
        _run_backend(evaluator(), fake, monkeypatch, output, expected, {})


@pytest.mark.parametrize(
    "case", ["similarity-text", "similarity-image", "similarity-audio", "contains-text"]
)
def test_invalid_json_response_is_an_embedding_error(case, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]

    def invalid_json(_response):
        # Requests may reject non-finite JSON before vector validation, depending
        # on its JSON decoder. Never leak the decoder's body into the error text.
        raise requests.exceptions.JSONDecodeError("invalid JSON", "private-body", 0)

    monkeypatch.setattr(requests.Response, "json", invalid_json)

    with pytest.raises(EmbeddingError, match="Invalid embedding response:") as raised:
        _run_backend(evaluator(), FakeServing(), monkeypatch, output, expected, {})
    assert "private-body" not in str(raised.value)


@pytest.mark.parametrize(
    "position", [0, 1, 2, 3], ids=["sentence-1", "sentence-2", "phrase-1", "phrase-2"]
)
def test_contains_validates_the_entire_batch_before_substring_matches(
    position, monkeypatch
):
    def change(response):
        response["embeddings"][position] = []
        return response

    with pytest.raises(EmbeddingError, match="Invalid embedding response:"):
        _run_backend(
            SemanticListContains(),
            _ChangedResponse(change),
            monkeypatch,
            "Refunds take five days. Shipping is free!",
            ["Refunds", "Shipping"],
            {},
        )


@pytest.mark.parametrize(
    "case",
    [
        "similarity-text",
        "similarity-image",
        "similarity-audio",
        "similarity-text-image",
        "similarity-image-audio",
        "contains-text",
    ],
)
def test_valid_zero_vectors_still_score_zero(case, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]
    fake = _ChangedResponse(_replace_last_vector([0.0, 0.0, 0.0, 0.0]))

    score, reason = _run_backend(evaluator(), fake, monkeypatch, output, expected, {})

    assert score == 0.0
    assert (
        "0.0000" in reason
        if evaluator is EmbeddingSimilarity
        else "Matched 0/1" in reason
    )


@pytest.mark.parametrize("case", ["similarity-image", "similarity-audio"])
def test_flat_single_media_vectors_remain_supported(case, monkeypatch):
    evaluator, output, expected = EVAL_CASES[case]
    fake = _ChangedResponse(lambda response: {"embeddings": response["embeddings"][0]})

    score, _reason = _run_backend(evaluator(), fake, monkeypatch, output, expected, {})

    assert score == {"similarity-image": 0.8, "similarity-audio": 0.5}[case]
