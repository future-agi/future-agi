"""The /model/v1/embed request must match the serving schema.

Serving declares ``text: str | list[str] | None`` (model_serving/app/v1/models.py),
so any other JSON shape is answered with 422 Unprocessable Entity, which the
client turns into ``RuntimeError: Request failed: 422 ...``. Eval inputs that
are JSON objects (dicts) reach ``embed_text`` through the few-shot retrieval
path (``model_manager.text_model``), and used to be posted as a JSON object.
"""

from unittest.mock import patch

import pytest

from agentic_eval.core.embeddings import serving_client
from agentic_eval.core.embeddings.serving_client import ModelServingClient

BASE = "embed-payload-test"
VECTOR = [0.1, 0.2, 0.3]
REASONS = {200: "OK", 422: "Unprocessable Entity", 503: "Service Unavailable"}


class FakeResponse:
    """Stands in for the serving reply; raise_for_status mirrors requests."""

    def __init__(self, url, status, body):
        self.url = url
        self.status_code = status
        self.reason = REASONS[status]
        self._body = body
        self.text = repr(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            kind = "Client" if self.status_code < 500 else "Server"
            raise serving_client.requests.exceptions.HTTPError(
                f"{self.status_code} {kind} Error: {self.reason} for url: {self.url}",
                response=self,
            )


def _response(url, status, body):
    return FakeResponse(url, status, body)


class FakeEmbedService:
    """Answers like serving's /embed: 422 unless ``text`` is str or list[str]."""

    def __init__(self, status=200):
        self.status = status
        self.payloads = []

    def __call__(self, url, json=None, timeout=None):
        self.payloads.append(json)
        if self.status != 200:
            return _response(url, self.status, {"detail": "down"})
        text = json.get("text")
        valid = (
            text is None
            or isinstance(text, str)
            or (isinstance(text, list) and all(isinstance(t, str) for t in text))
        )
        if not valid:
            return _response(
                url,
                422,
                {"detail": [{"type": "string_type", "loc": ["body", "text"]}]},
            )
        items = [text] if isinstance(text, str) else text
        return _response(
            url,
            200,
            {
                "embeddings": [list(VECTOR) for _ in items],
                "model_name": "text_embedding",
            },
        )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(serving_client, "_probe", lambda base_url: True)
    serving_client._probe_cache.pop(BASE, None)
    c = ModelServingClient(base_url=BASE)
    yield c
    serving_client._probe_cache.pop(BASE, None)
    c.close()


def _call(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - the caller's outcome is asserted
        return exc


class TestEmbedTextPayload:
    def test_plain_text_still_returns_the_vector(self, client):
        service = FakeEmbedService()
        with patch.object(client.session, "post", side_effect=service):
            result = _call(client.embed_text, "hello")

        assert result == VECTOR
        assert service.payloads == [{"text": ["hello"], "input_type": "text"}]

    def test_json_object_input_is_embedded_not_rejected_with_422(self, client):
        """The report: ``RuntimeError: Request failed: 422 Client Error:
        Unprocessable Entity for url: .../model/v1/embed``."""
        service = FakeEmbedService()
        eval_input = {"question": "What is the refund window?", "lang": "en"}

        with patch.object(client.session, "post", side_effect=service):
            result = _call(client.embed_text, eval_input)

        assert not (isinstance(result, RuntimeError) and "422" in str(result)), (
            f"embed_text raised {result!r}"
        )
        assert result == VECTOR
        sent = service.payloads[-1]["text"]
        assert isinstance(sent, list) and all(isinstance(t, str) for t in sent)

    def test_nested_json_input_through_the_text_model_used_by_few_shot_retrieval(
        self, client, monkeypatch
    ):
        """Edge: a chat-style JSON eval input, embedded the way
        ``EmbeddingManager.retrieve_rag_based_examples`` does it."""
        from agentic_eval.core.embeddings.embedding_manager import model_manager

        monkeypatch.setattr(model_manager, "_serving_client", client)
        monkeypatch.setattr(model_manager, "_use_serving", True)
        service = FakeEmbedService()
        eval_input = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }

        with patch.object(client.session, "post", side_effect=service):
            result = _call(model_manager.text_model, eval_input)

        assert not isinstance(result, Exception), f"text model raised {result!r}"
        assert result == VECTOR

    def test_a_real_service_failure_is_still_raised(self, client):
        service = FakeEmbedService(status=503)
        with patch.object(client.session, "post", side_effect=service):
            result = _call(client.embed_text, "hello")

        assert isinstance(result, RuntimeError)
        assert "503" in str(result)


class FakeVectorDB:
    """Records the query vectors and answers with one stored feedback row."""

    def __init__(self):
        self.queries = []

    def vector_similarity_search(self, table_name, query_vector, *args, **kwargs):
        self.queries.append(query_vector)
        return [("row", {"item_id": "fb-1", "feedback_comment": "too vague"}, 0.1)]


class TestFewShotRetrievalWithStructuredInputs:
    """The few-shot lookup each eval runs, end to end, with JSON eval inputs."""

    @pytest.fixture
    def manager(self, client, monkeypatch):
        from agentic_eval.core.embeddings import embedding_manager as em

        monkeypatch.setattr(em.model_manager, "_serving_client", client)
        monkeypatch.setattr(em.model_manager, "_use_serving", True)
        monkeypatch.setattr(em, "serving_available", lambda: True)
        manager = em.EmbeddingManager.__new__(em.EmbeddingManager)
        manager.db_client = FakeVectorDB()
        monkeypatch.setattr(manager, "get_feedback_count", lambda **kwargs: 1)
        return manager

    @pytest.mark.parametrize(
        "eval_input",
        [
            {"question": "What is the refund window?", "lang": "en"},
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            ["first turn", "second turn"],
            42,
            0,
            0.0,
            False,
            "plain text",
        ],
        ids=[
            "dict",
            "message_list",
            "string_list",
            "number",
            "zero",
            "zero_float",
            "false",
            "string",
        ],
    )
    def test_feedback_examples_are_found_for_any_input_shape(
        self, client, manager, eval_input
    ):
        service = FakeEmbedService()
        with patch.object(client.session, "post", side_effect=service):
            examples = manager.retrieve_avg_rag_based_examples(
                eval_id="eval-1",
                inputs=[eval_input],
                input_cols=["input"],
                organization_id="org-1",
            )

        assert examples == [[{"item_id": "fb-1", "feedback_comment": "too vague"}]]
        assert service.payloads == [{"text": [str(eval_input)], "input_type": "text"}]

    @pytest.mark.parametrize(
        "eval_input",
        [None, {}, [], {"question": None}],
        ids=["none", "empty_dict", "empty_list", "dict_of_empty_values"],
    )
    def test_empty_inputs_are_still_skipped(self, client, manager, eval_input):
        service = FakeEmbedService()
        with patch.object(client.session, "post", side_effect=service):
            examples = manager.retrieve_avg_rag_based_examples(
                eval_id="eval-1",
                inputs=[eval_input],
                input_cols=["input"],
                organization_id="org-1",
            )

        assert examples == []
        assert service.payloads == []
