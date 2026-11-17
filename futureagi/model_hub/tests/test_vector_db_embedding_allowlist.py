"""Vector-DB column requests may only pick approved Hugging Face models.

``embedding_config.model`` is sent to model serving's customer-selectable
Hugging Face loader (dynamic_columns.py ``_query_vector_db`` ->
``/model/v1/infer/huggingface``). Model serving is the authoritative check
(model_serving/tests/test_model_allowlist.py); these pin the request
contract so an unapproved ID, a local path or a ``model_name`` override is
a 400 at the API instead of a failed row. They also pin the embedding
catalog to the allowlist and the backend mirror to serving's list. DB-free:
serializers, the static catalog view, and serving's settings read as text.
"""

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_hub.serializers.contracts import (
    APPROVED_HUGGINGFACE_EMBEDDING_MODELS,
    RerunOperationRequestSerializer,
    VectorDBColumnRequestSerializer,
)

HF_TYPES = ["huggingface", "sentence_transformers"]

UNAPPROVED_MODEL_IDS = [
    "attacker/evil-embedder",
    "nomic-ai/nomic-embed-text-v1",
    "all-mpnet-base-v2 ",
    "sentence-transformers/all-mpnet-base-v2-evil",
    "",
]

LOCAL_PATHS = [
    "/tmp/evil-model",
    "./evil-model",
    "../../tmp/evil-model",
    "~/evil-model",
    "file:///tmp/evil-model",
    "C:\\models\\evil",
    "/root/.cache/huggingface/hub/models--sentence-transformers--all-mpnet-base-v2",
]


def _vector_db_errors(embedding_config):
    serializer = VectorDBColumnRequestSerializer(
        data={
            "column_id": str(uuid.uuid4()),
            "sub_type": "pinecone",
            "api_key": str(uuid.uuid4()),
            "embedding_config": embedding_config,
        }
    )
    serializer.is_valid()
    return serializer.errors


def _rerun_errors(config):
    serializer = RerunOperationRequestSerializer(
        data={"operation_type": "vector_db", "config": config}
    )
    serializer.is_valid()
    return serializer.errors


@pytest.mark.parametrize("embedding_type", HF_TYPES)
@pytest.mark.parametrize("model", UNAPPROVED_MODEL_IDS)
def test_unapproved_huggingface_model_is_refused(embedding_type, model):
    errors = _vector_db_errors({"type": embedding_type, "model": model})
    assert "embedding_config" in errors


@pytest.mark.parametrize("embedding_type", HF_TYPES)
@pytest.mark.parametrize("path", LOCAL_PATHS)
def test_local_path_is_refused(embedding_type, path):
    errors = _vector_db_errors({"type": embedding_type, "model": path})
    assert "embedding_config" in errors


@pytest.mark.parametrize(
    "embedding_config",
    [
        {"type": "huggingface", "model": "all-mpnet-base-v2", "model_name": "/tmp/x"},
        {
            "type": "huggingface",
            "model": "all-mpnet-base-v2",
            "trust_remote_code": True,
        },
        {"type": "huggingface", "model": ["all-mpnet-base-v2"]},
        ["huggingface", "/tmp/evil-model"],
    ],
)
def test_model_name_override_and_malformed_config_are_refused(embedding_config):
    errors = _vector_db_errors(embedding_config)
    assert "embedding_config" in errors


def test_rerun_cannot_swap_in_an_unapproved_model():
    errors = _rerun_errors(
        {"embedding_config": {"type": "huggingface", "model": "/tmp/evil-model"}}
    )
    assert "config" in errors


@pytest.mark.parametrize(
    "embedding_config",
    [
        {"type": "huggingface", "model": "all-mpnet-base-v2"},
        {"type": "sentence_transformers", "model": "all-mpnet-base-v2"},
        {"type": "huggingface", "model": "sentence-transformers/all-MiniLM-L6-v2"},
        # No model: the backend default (all-mpnet-base-v2) is used.
        {"type": "huggingface"},
        # OpenAI models are remote API names, never loaded by serving.
        {"type": "openai", "model": "text-embedding-3-large"},
        # What the UI sends when no embedding is configured.
        {"type": "", "model": ""},
        {},
    ],
)
def test_approved_and_non_huggingface_configs_are_accepted(embedding_config):
    assert _vector_db_errors(embedding_config) == {}


def test_rerun_with_approved_model_is_accepted():
    errors = _rerun_errors(
        {"embedding_config": {"type": "huggingface", "model": "all-mpnet-base-v2"}}
    )
    assert errors == {}


@pytest.mark.parametrize(
    "embedding_type",
    [["huggingface"], {"huggingface": True}, 1, None],
    ids=["list", "dict", "int", "null"],
)
def test_non_string_type_is_a_400_not_a_500(embedding_type):
    # At 3bebea08fb an unhashable type (list or dict) made the frozenset
    # membership test raise TypeError, which DRF turns into an HTTP 500.
    config = {"type": embedding_type, "model": "/tmp/evil-model"}
    assert "embedding_config" in _vector_db_errors(config)
    assert "config" in _rerun_errors({"embedding_config": config})


def _embedding_catalog():
    from rest_framework.test import APIRequestFactory, force_authenticate

    from model_hub.views.develop_dataset import GetEmbeddingsListView

    request = APIRequestFactory().get("/model-hub/embeddings/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    response = GetEmbeddingsListView.as_view()(request)
    assert response.status_code == 200
    return response.data["result"]["embeddings"]


def test_embedding_catalog_offers_only_approved_huggingface_models():
    # GET /model-hub/embeddings/ must not offer Hugging Face models that
    # add_vector_db_column and model serving refuse.
    provider = _embedding_catalog()["huggingface"]
    model = provider["config_schema"]["model"]
    approved = ", ".join(sorted(APPROVED_HUGGINGFACE_EMBEDDING_MODELS))
    assert model["default"] in APPROVED_HUGGINGFACE_EMBEDDING_MODELS
    assert f"Approved models: {approved}." in model["description"]
    assert "vast collection" not in provider["description"]


def test_embedding_catalog_does_not_offer_sentence_transformers_models():
    # Model serving answers every sentence_transformers request with 404
    # (the type is not in its SUPPORTED_MODELS), so no model works under
    # it, approved or not. The entry, its key and its default stay (the
    # API journey reads them); its text must not claim any model works.
    provider = _embedding_catalog()["sentence_transformers"]
    model = provider["config_schema"]["model"]
    for text in (provider["description"], model["description"]):
        assert "not available on this server" in text
        assert "Approved" not in text
    assert model["default"]


SERVING_SETTINGS = (
    Path(__file__).resolve().parents[2] / "model_serving/app/config/settings.py"
)


def _serving_approved_huggingface_models():
    # Read as text: importing the serving settings needs torch and the
    # serving stack, which the backend test env does not have.
    assert SERVING_SETTINGS.is_file(), f"missing {SERVING_SETTINGS}"
    tree = ast.parse(SERVING_SETTINGS.read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and [getattr(t, "id", None) for t in node.targets]
            == ["APPROVED_HUGGINGFACE_MODELS"]
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "frozenset"
            and len(node.value.args) == 1
        ):
            return frozenset(ast.literal_eval(node.value.args[0]))
    raise AssertionError(
        f"APPROVED_HUGGINGFACE_MODELS = frozenset({{...}}) not found in "
        f"{SERVING_SETTINGS}"
    )


def test_backend_allowlist_mirror_matches_model_serving():
    # Drift in either direction fails closed, but silently: the backend
    # would refuse a model serving loads, or accept one serving refuses.
    assert APPROVED_HUGGINGFACE_EMBEDDING_MODELS == (
        _serving_approved_huggingface_models()
    )
