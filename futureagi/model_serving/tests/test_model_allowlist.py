"""Customer-selected model loading must stay on a server-owned allowlist.

``POST /model/v1/infer/{model_name}`` forwarded request ``model_params``
straight into the servable constructors, so an authenticated dataset user
could make the serving container load any Hugging Face repo or local
directory, with ``trust_remote_code=True`` on the ``huggingface`` loader:
model-load code execution (T6 #1). These tests pin the containment:

* the ``huggingface`` loader only loads model IDs on the server allowlist;
* local / cache filesystem paths are refused before any loader runs;
* built-in models take no request ``model_name`` override;
* ``trust_remote_code`` is never True;
* an approved model still loads, with the exact loader arguments.

The heavy ML stack (torch, transformers, sentence-transformers, librosa, av)
and FastAPI are stubbed only when they are not installed, so these run
DB-free and network-free on a bare host, and against the real packages in
the serving test image. Every loader is mocked; nothing is downloaded.
"""

import asyncio
import importlib
import importlib.machinery
import sys
import time
import types
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs


def _install_stub_if_missing(name: str, module: types.ModuleType | None = None):
    try:
        importlib.import_module(name)
    except ImportError:
        sys.modules[name] = module if module is not None else MagicMock(name=name)


def _fastapi_stub() -> types.ModuleType:
    """Just enough FastAPI for app.v1.endpoints to import and raise."""
    fastapi = types.ModuleType("fastapi")
    # A real transformers import calls importlib.util.find_spec("fastapi"),
    # which raises "fastapi.__spec__ is None" for a bare module.
    fastapi.__spec__ = importlib.machinery.ModuleSpec("fastapi", None)

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None):
            super().__init__(status_code, detail)
            self.status_code = status_code
            self.detail = detail
            self.headers = headers

    class APIRouter:
        # Real FastAPI route decorators register and return the function.
        def __init__(self, *args, **kwargs):
            pass

        def _route(self, *args, **kwargs):
            return lambda func: func

        get = post = put = delete = patch = _route

    fastapi.HTTPException = HTTPException
    fastapi.APIRouter = APIRouter
    return fastapi


_install_stub_if_missing("fastapi", _fastapi_stub())
for _heavy in ("torch", "transformers", "sentence_transformers", "librosa", "av"):
    _install_stub_if_missing(_heavy)

from app.servable_models import (  # noqa: E402
    embedding_model,
    embedding_models,
    external_models,
)
from app.utils.load_model import ModelLoader  # noqa: E402
from app.v1 import endpoints  # noqa: E402
from app.v1.models import InferModelRequest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

APPROVED_MODEL = "all-mpnet-base-v2"

# Exactly the IDs the product configures for this path; see the citations
# next to APPROVED_HUGGINGFACE_MODELS in app/config/settings.py.
EXPECTED_ALLOWLIST = {
    "all-mpnet-base-v2",
    "sentence-transformers/all-mpnet-base-v2",
    "all-MiniLM-L6-v2",
    "sentence-transformers/all-MiniLM-L6-v2",
}

UNAPPROVED_MODEL_IDS = [
    "attacker/evil-embedder",
    # A real repo that needs remote code to load.
    "nomic-ai/nomic-embed-text-v1",
    # Near misses of an approved ID must not pass.
    "all-mpnet-base-v2 ",
    "ALL-MPNET-BASE-V2",
    "sentence-transformers/all-mpnet-base-v2-evil",
    "",
]

LOCAL_PATHS = [
    "/tmp/evil-model",
    "./evil-model",
    "../../tmp/evil-model",
    "all-mpnet-base-v2/../../tmp/evil-model",
    "~/evil-model",
    "file:///tmp/evil-model",
    "C:\\models\\evil",
    # The HF cache directory of an approved model is still a path.
    "/root/.cache/huggingface/hub/models--sentence-transformers--all-mpnet-base-v2",
    "/proc/self/cwd/all-mpnet-base-v2",
]


@pytest.fixture(autouse=True)
def _fresh_models(monkeypatch):
    """No cached model or singleton may make a load test pass vacuously."""
    ModelLoader.unload_all_models()
    for cls in (
        embedding_model.EmbeddingModel,
        embedding_models.TextEmbeddingModel,
        embedding_models.SynDataEmbeddingModel,
    ):
        monkeypatch.setattr(cls, "_model_instance", None)
    yield
    ModelLoader.unload_all_models()


@pytest.fixture
def hf_loader(monkeypatch):
    loader = MagicMock(name="external_models.SentenceTransformer")
    loader.return_value.encode.return_value = [[0.1, 0.2]]
    monkeypatch.setattr(external_models, "SentenceTransformer", loader)
    return loader


@pytest.fixture
def builtin_loaders(monkeypatch):
    text_loader = MagicMock(name="embedding_models.SentenceTransformer")
    text_loader.return_value.encode.return_value = [[0.3, 0.4]]
    default_loader = MagicMock(name="embedding_model.SentenceTransformer")
    default_loader.return_value.encode.return_value = [[0.5, 0.6]]
    monkeypatch.setattr(embedding_models, "SentenceTransformer", text_loader)
    monkeypatch.setattr(embedding_model, "SentenceTransformer", default_loader)
    return types.SimpleNamespace(text=text_loader, default=default_loader)


def _infer(servable, model_params=None):
    request = InferModelRequest(
        text=["hello"], input_type="text", model_params=model_params
    )
    return asyncio.run(endpoints.infer(servable, request))


def _assert_refused(call):
    with pytest.raises(HTTPException) as excinfo:
        call()
    assert excinfo.value.status_code == 400, excinfo.value.detail
    return excinfo.value


# --- arbitrary model IDs ----------------------------------------------------


@pytest.mark.parametrize("model_id", UNAPPROVED_MODEL_IDS)
def test_unapproved_huggingface_model_id_is_refused(hf_loader, model_id):
    _assert_refused(
        lambda: _infer("huggingface", {"model": model_id, "api_key": "hf-key"})
    )
    hf_loader.assert_not_called()


@pytest.mark.parametrize("model_id", [None, 123, ["all-mpnet-base-v2"]])
def test_non_string_huggingface_model_is_refused(hf_loader, model_id):
    _assert_refused(
        lambda: _infer("huggingface", {"model": model_id, "api_key": "hf-key"})
    )
    hf_loader.assert_not_called()


def test_missing_huggingface_model_is_refused(hf_loader):
    _assert_refused(lambda: _infer("huggingface", {"api_key": "hf-key"}))
    hf_loader.assert_not_called()


# --- local / cache filesystem paths -----------------------------------------


@pytest.mark.parametrize("path", LOCAL_PATHS)
def test_local_path_is_refused_for_huggingface_loader(hf_loader, path):
    _assert_refused(lambda: _infer("huggingface", {"model": path, "api_key": "hf-key"}))
    hf_loader.assert_not_called()


# --- model / model_name overrides -------------------------------------------


# TextEmbeddingModel and EmbeddingModel take a ``model_name`` that picks what
# SentenceTransformer loads (EmbeddingModel with trust_remote_code=True). At
# f2c4e6b0c9 a request ``model_name`` only failed because it collided with
# ModelLoader.get_model's own ``model_name`` parameter (an accidental 500);
# the refusal must be explicit, not depend on a parameter name.


@pytest.mark.parametrize("override", ["/tmp/evil-model", "attacker/evil-embedder"])
def test_model_name_override_on_text_embedding_is_refused(builtin_loaders, override):
    _assert_refused(lambda: _infer("text_embedding", {"model_name": override}))
    builtin_loaders.text.assert_not_called()


def test_model_name_override_on_default_embedding_is_refused(builtin_loaders):
    _assert_refused(
        lambda: _infer("embedding", {"model_name": "attacker/evil-embedder"})
    )
    builtin_loaders.default.assert_not_called()


def test_model_name_override_through_embed_route_is_refused(builtin_loaders):
    request = InferModelRequest(
        text="hello", model_params={"model_name": "/tmp/evil-model"}
    )
    _assert_refused(lambda: asyncio.run(endpoints.embed_text(request)))
    builtin_loaders.text.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        {"model_name": "/tmp/evil-model"},
        {"trust_remote_code": True},
        {"cache_folder": "/tmp/attacker-cache"},
    ],
)
def test_extra_loader_kwargs_on_huggingface_are_refused(hf_loader, extra):
    _assert_refused(
        lambda: _infer(
            "huggingface", {"model": APPROVED_MODEL, "api_key": "hf-key", **extra}
        )
    )
    hf_loader.assert_not_called()


# --- trust_remote_code -------------------------------------------------------


def test_huggingface_loader_never_trusts_remote_code(hf_loader):
    external_models.HuggingFaceEmbeddingModel(model=APPROVED_MODEL)

    hf_loader.assert_called_once()
    assert hf_loader.call_args.kwargs.get("trust_remote_code") is False


# --- approved models keep working -------------------------------------------


def test_allowlist_is_server_owned_and_exactly_the_cited_models():
    from app.config import settings

    allowlist = getattr(settings, "APPROVED_HUGGINGFACE_MODELS", None)
    assert isinstance(allowlist, frozenset)
    assert set(allowlist) == EXPECTED_ALLOWLIST


@pytest.mark.parametrize("model_id", sorted(EXPECTED_ALLOWLIST))
def test_approved_huggingface_model_loads_with_exact_args(hf_loader, model_id):
    result = _infer("huggingface", {"model": model_id, "api_key": "hf-key"})

    hf_loader.assert_called_once_with(model_id, trust_remote_code=False)
    hf_loader.return_value.encode.assert_called_once_with(
        ["hello"], convert_to_tensor=False
    )
    assert result == {
        "embeddings": [[0.1, 0.2]],
        "model_name": "huggingface",
        "input_type": "text",
    }


def test_builtin_text_embedding_without_params_still_loads(builtin_loaders):
    result = _infer("text_embedding")

    builtin_loaders.text.assert_called_once_with("all-MiniLM-L6-v2")
    assert result["embeddings"] == [[0.3, 0.4]]


def test_openai_embedding_still_accepts_model_and_api_key(monkeypatch):
    client = MagicMock(name="OpenAI client")
    client.embeddings.create.return_value.data = [
        types.SimpleNamespace(embedding=[0.7, 0.8])
    ]
    openai_ctor = MagicMock(return_value=client)
    monkeypatch.setattr(external_models.openai, "OpenAI", openai_ctor)

    result = _infer("openai", {"model": "text-embedding-3-small", "api_key": "sk-test"})

    openai_ctor.assert_called_once_with(api_key="sk-test")
    client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small", input=["hello"]
    )
    assert result["embeddings"] == [[0.7, 0.8]]


# --- credentials stay out of logs and cache identity ------------------------


# ModelLoader built each cache key from every request kwarg, so the customer's
# Hugging Face / OpenAI api_key was in the key, which INFO logs ("Loading
# model ... with key", "Successfully loaded model"), cache-hit, cleanup and
# unload logs, the 503 detail and the usage endpoints all repeat.
SYNTHETIC_KEY = "sk-t6-synthetic-credential-6f1c0a9e"
OTHER_SYNTHETIC_KEY = "sk-t6-synthetic-credential-0b7d22e4"


def _assert_no_credential(events, *extra):
    for event in events:
        assert SYNTHETIC_KEY not in repr(event), event
    for text in extra:
        assert SYNTHETIC_KEY not in str(text), text


@pytest.fixture
def openai_ctor(monkeypatch):
    client = MagicMock(name="OpenAI client")
    client.embeddings.create.return_value.data = [
        types.SimpleNamespace(embedding=[0.7, 0.8])
    ]
    ctor = MagicMock(return_value=client)
    monkeypatch.setattr(external_models.openai, "OpenAI", ctor)
    return ctor


@pytest.mark.parametrize(
    "servable,model",
    [("huggingface", APPROVED_MODEL), ("openai", "text-embedding-3-small")],
)
def test_api_key_is_not_logged_on_load_or_cache_hit(
    hf_loader, openai_ctor, servable, model
):
    params = {"model": model, "api_key": SYNTHETIC_KEY}
    with capture_logs() as events:
        _infer(servable, dict(params))
        _infer(servable, dict(params))  # cache hit

    assert any("Loading model" in e["event"] for e in events), events
    assert any("cached model" in e["event"] for e in events), events
    _assert_no_credential(
        events,
        *ModelLoader.get_loaded_models(),
        *ModelLoader.get_model_usage_info(),
    )


def test_api_key_is_not_logged_or_returned_when_the_load_fails(hf_loader):
    hf_loader.side_effect = OSError("weights unavailable")

    with capture_logs() as events, pytest.raises(HTTPException) as excinfo:
        _infer("huggingface", {"model": APPROVED_MODEL, "api_key": SYNTHETIC_KEY})

    assert excinfo.value.status_code == 503
    assert any("Failed to load model" in e["event"] for e in events), events
    _assert_no_credential(events, excinfo.value.detail)


def test_api_key_is_not_logged_on_cleanup_or_unload(hf_loader, openai_ctor):
    _infer("huggingface", {"model": APPROVED_MODEL, "api_key": SYNTHETIC_KEY})
    _infer("openai", {"model": "text-embedding-3-small", "api_key": SYNTHETIC_KEY})

    with capture_logs() as events:
        for key in list(ModelLoader._model_last_used):
            ModelLoader._model_last_used[key] = time.time() - 10 * 60 * 60
        ModelLoader._cleanup_idle_models()
        _infer("huggingface", {"model": APPROVED_MODEL, "api_key": SYNTHETIC_KEY})
        assert ModelLoader.unload_model("huggingface") is True
        _infer("openai", {"model": "text-embedding-3-small", "api_key": SYNTHETIC_KEY})
        ModelLoader.unload_all_models()

    assert any("Auto-removed idle model" in e["event"] for e in events), events
    assert any("Unloaded model instance" in e["event"] for e in events), events
    assert any("Unloaded all models" in e["event"] for e in events), events
    _assert_no_credential(events)


def test_credential_specific_clients_stay_separate(openai_ctor):
    # Hashing the key must not merge two customers' clients into one.
    for api_key in (SYNTHETIC_KEY, OTHER_SYNTHETIC_KEY, SYNTHETIC_KEY):
        _infer("openai", {"model": "text-embedding-3-small", "api_key": api_key})

    assert [c.kwargs["api_key"] for c in openai_ctor.call_args_list] == [
        SYNTHETIC_KEY,
        OTHER_SYNTHETIC_KEY,
    ]
    assert len(ModelLoader.get_loaded_models()) == 2


# --- credentials stay out of inference-failure logs -------------------------


# A pasted OpenAI key with a trailing space or newline passed model_params
# validation. The real HTTP stack then refuses the "Authorization: Bearer <key>"
# header, and httpcore/h11 quote the whole header, key included, in
# LocalProtocolError. OpenAI wraps that as APIConnectionError("Connection
# error."), but infer() logged traceback.format_exc() verbatim, so the chained
# exceptions put the key into serving's ERROR logs. These tests drive the real
# openai -> httpx -> httpcore -> h11 stack over an in-memory network backend.
MALFORMED_KEYS = [
    pytest.param(SYNTHETIC_KEY + " ", id="trailing-space"),
    pytest.param(SYNTHETIC_KEY + "\n", id="trailing-newline"),
]


@pytest.fixture
def offline_openai_stack(monkeypatch):
    """The real OpenAI client with no network: connections go to a mock stream."""
    import socket

    import httpcore
    import openai

    def _no_network(*args, **kwargs):
        raise AssertionError("a test tried to open a real network connection")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(
        httpcore._sync.connection_pool, "SyncBackend", lambda: httpcore.MockBackend([])
    )
    monkeypatch.setattr(
        openai._base_client.SyncAPIClient, "_sleep_for_retry", lambda *a, **k: None
    )
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    ctor = MagicMock(name="openai.OpenAI", wraps=openai.OpenAI)
    monkeypatch.setattr(external_models.openai, "OpenAI", ctor)
    return ctor


def _assert_no_credential_anywhere(events, caplog, *extra):
    _assert_no_credential(events, caplog.text, *extra)
    for record in caplog.records:
        assert SYNTHETIC_KEY not in record.getMessage(), record
        assert SYNTHETIC_KEY not in repr(record.args), record


def _exception_chain(exc):
    seen = []
    while exc is not None and all(exc is not s for s in seen):
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


@pytest.mark.parametrize("api_key", MALFORMED_KEYS)
def test_malformed_openai_api_key_is_refused_before_the_client_is_built(
    offline_openai_stack, caplog, api_key
):
    caplog.set_level("DEBUG")
    with capture_logs() as events, pytest.raises(HTTPException) as excinfo:
        _infer("openai", {"model": "text-embedding-3-small", "api_key": api_key})

    _assert_no_credential_anywhere(events, caplog, excinfo.value.detail)
    assert excinfo.value.status_code == 400, excinfo.value.detail
    # Generic: says what is wrong with the key, never repeats it.
    assert "api_key" in excinfo.value.detail
    assert "spaces" in excinfo.value.detail
    offline_openai_stack.assert_not_called()


@pytest.mark.parametrize("api_key", MALFORMED_KEYS)
def test_inference_failure_logs_never_carry_the_credential(
    offline_openai_stack, caplog, monkeypatch, api_key
):
    # Skip the up-front refusal so the request really reaches the HTTP stack:
    # whatever an inference failure chains, its text must not be logged.
    monkeypatch.setattr(
        endpoints, "_refuse_malformed_api_key", lambda *a, **k: None, raising=False
    )
    # Serving's default LOG_LEVEL. At DEBUG, httpcore's and openai's own debug
    # records repeat the failed header; the refusal above keeps a malformed key
    # from ever reaching them (the previous test captures DEBUG).
    caplog.set_level("INFO")
    with capture_logs() as events, pytest.raises(HTTPException) as excinfo:
        _infer("openai", {"model": "text-embedding-3-small", "api_key": api_key})

    # Non-vacuous: the real h11 header check failed and quoted the key.
    chain = _exception_chain(excinfo.value)
    assert any(
        "Illegal header value" in str(exc) and SYNTHETIC_KEY in str(exc)
        for exc in chain
    ), chain
    assert any("inference" in e["event"] for e in events), events

    _assert_no_credential_anywhere(events, caplog, excinfo.value.detail)
    assert excinfo.value.status_code == 500
    # Nothing downstream (the 500 handler's sentry capture) walks the chain.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_huggingface_api_key_with_whitespace_is_not_logged(hf_loader, caplog):
    # The Hugging Face loader never uses the key, so a pasted key with a
    # trailing newline still loads, and is still kept out of every log.
    caplog.set_level("DEBUG")
    with capture_logs() as events:
        result = _infer(
            "huggingface", {"model": APPROVED_MODEL, "api_key": SYNTHETIC_KEY + "\n"}
        )

    assert result["embeddings"] == [[0.1, 0.2]]
    _assert_no_credential_anywhere(
        events,
        caplog,
        *ModelLoader.get_loaded_models(),
        *ModelLoader.get_model_usage_info(),
    )
