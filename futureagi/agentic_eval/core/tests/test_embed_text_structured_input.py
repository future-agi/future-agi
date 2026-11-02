"""Trace-eval inputs come from span attributes, so a mapped value can be a dict
(a chat message, a JSON ``input.value``). Before every FutureAGI eval the
few-shot lookup embeds each input value as text. ``embed_text`` checked
"all items are strings" by iterating the value, which for a dict walks its
string keys, so the dict itself was posted as ``"text"``. Serving's
``InferModelRequest.text`` is ``str | list[str]``, so it answered 422 and the
client raised ``RuntimeError("Request failed: 422 ...")`` inside
``run_eval_entry_activity``.

The feedback side stores these values as ``str(value)`` (``data_formatter``),
so the query must be embedded as the same string.
"""

from unittest.mock import Mock

import pytest

from agentic_eval.core.embeddings.embedding_manager import (
    EmbeddingManager,
    model_manager,
)
from agentic_eval.core.embeddings import serving_client
from agentic_eval.core.embeddings.serving_client import ModelServingClient

BASE = "http://futureagi-application-embedding-service:8080"
VECTOR = [0.1, 0.2, 0.3]

CHAT_MESSAGE = {"role": "user", "content": "What is the refund policy?"}
CHAT_INPUT = {
    "messages": [
        {"role": "system", "content": "You are a support bot."},
        {"role": "user", "content": "Where is my order #1234?"},
    ],
    "model": "gpt-4o",
}


@pytest.fixture(autouse=True)
def _serving_reachable(monkeypatch):
    """Serving answers its health probe (no real host in tests)."""
    monkeypatch.setattr(serving_client, "_probe", lambda base_url: True)
    serving_client._probe_cache.clear()
    yield
    serving_client._probe_cache.clear()


class ServingHTTPError(Exception):
    """Stands in for the HTTP error ``raise_for_status`` raises."""


def _fake_serving(sent):
    """Answer like serving's ``/model/v1/embed``: the body must validate as
    ``text: str | list[str]`` or the response is 422."""

    def post(url, json=None, timeout=None):
        sent.append(json)
        text = json.get("text")
        valid = isinstance(text, str) or (
            isinstance(text, list) and all(isinstance(t, str) for t in text)
        )
        response = Mock()
        if not valid:
            response.status_code = 422
            response.text = '{"detail":"Validation error"}'
            response.raise_for_status.side_effect = ServingHTTPError(
                f"422 Client Error: Unprocessable Entity for url: {url}"
            )
            return response
        response.status_code = 200
        response.json.return_value = {"embeddings": [VECTOR], "input_type": "text"}
        return response

    return post


def _client(monkeypatch, sent):
    client = ModelServingClient(base_url=BASE)
    monkeypatch.setattr(client.session, "post", _fake_serving(sent))
    return client


def _embed(client, value):
    try:
        return client.embed_text(value), None
    except Exception as exc:  # the bug surfaces as RuntimeError
        return None, exc


@pytest.mark.parametrize(
    "value", [CHAT_MESSAGE, CHAT_INPUT], ids=["chat_message", "chat_input"]
)
def test_dict_input_is_sent_as_text_serving_accepts(monkeypatch, value):
    sent = []
    client = _client(monkeypatch, sent)

    embedding, error = _embed(client, value)

    assert error is None, f"embed_text raised {type(error).__name__}: {error}"
    assert embedding == VECTOR
    assert sent[0]["text"] == [str(value)]


def test_plain_text_request_is_unchanged(monkeypatch):
    sent = []
    client = _client(monkeypatch, sent)

    assert client.embed_text("What is the refund policy?") == VECTOR
    assert sent[0] == {"text": ["What is the refund policy?"], "input_type": "text"}


def test_a_real_serving_failure_is_still_an_error(monkeypatch):
    client = ModelServingClient(base_url=BASE)
    response = Mock(status_code=503, text="unhealthy")
    response.raise_for_status.side_effect = ServingHTTPError("503 Server Error")
    monkeypatch.setattr(client.session, "post", lambda *a, **k: response)

    with pytest.raises(ServingHTTPError):
        client.embed_text("hello")


def test_few_shot_lookup_embeds_a_dict_input_and_searches(monkeypatch):
    """The eval path: ``retrieve_rag_based_examples`` with a dict span value
    reaches the vector search instead of failing on a 422."""
    sent = []
    client = _client(monkeypatch, sent)
    monkeypatch.setattr(model_manager, "_serving_client", client)
    monkeypatch.setattr(model_manager, "_use_serving", True)

    manager = EmbeddingManager.__new__(EmbeddingManager)
    manager.db_client = Mock()
    manager.db_client.vector_similarity_search.return_value = []
    manager.input_types = None

    manager.retrieve_rag_based_examples(
        CHAT_MESSAGE,
        "feedbacks",
        "eval-1",
        "input",
        input_type="text",
        organization_id="org-1",
    )

    assert sent[0]["text"] == [str(CHAT_MESSAGE)]
    assert manager.db_client.vector_similarity_search.called, (
        "query embedding failed, so the feedback search never ran"
    )
    assert manager.db_client.vector_similarity_search.call_args.args[1] == VECTOR
