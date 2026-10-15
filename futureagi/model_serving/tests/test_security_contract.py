import asyncio

from app.servable_models import embedding_model
from app.v1.endpoints import health_check, require_internal_service_token


def test_inference_auth_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-service-token")

    try:
        asyncio.run(require_internal_service_token(None))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("missing service token was accepted")


def test_health_is_open_and_returns_status_only():
    assert asyncio.run(health_check()) == {"status": "healthy"}


def test_embedding_constructor_defaults_remote_code_to_false(monkeypatch):
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            calls.append((model_name, kwargs))

    monkeypatch.delenv("TRUST_REMOTE_CODE", raising=False)
    monkeypatch.setattr(embedding_model, "SentenceTransformer", FakeSentenceTransformer)
    embedding_model.EmbeddingModel._model_instance = None

    embedding_model.EmbeddingModel("test-model")

    assert calls == [("test-model", {"trust_remote_code": False})]
