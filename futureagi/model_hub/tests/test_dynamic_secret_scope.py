"""Worker credential resolution never depends on ambient thread context."""

from importlib import import_module
from unittest.mock import Mock, patch

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db
tenants = tenants_fixture


def _secret(a, b, scope):
    owner = b if scope == "organization" else a
    workspace = owner["workspace"]
    if scope == "workspace":
        workspace = Workspace.objects.create(
            name="private secret", organization=a["org"], created_by=a["user"], is_default=False,
        )
    clear_workspace_context()
    return SecretModel.no_workspace_objects.create(
        name="synthetic", organization=owner["org"], workspace=workspace, key="synthetic-test-credential",
    )


@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
@pytest.mark.parametrize("location", ["params", "headers"])
@pytest.mark.parametrize("module_name", ["dynamic_columns", "develop_dataset"])
def test_api_secret_scope_without_ambient_context(tenants, scope, location, module_name):
    module = import_module(f"model_hub.views.{module_name}")
    a, b, _ = tenants
    secret = _secret(a, b, scope)
    config = {
        "url": "http://127.0.0.1:9/synthetic", "method": "POST", "output_type": "string",
        location: {"credential": {"type": "Secret", "value": str(secret.id)}},
    }
    clear_workspace_context()
    response = Mock(text="ok", status_code=200)
    with patch.object(module.requests, "request", return_value=response) as send:
        _, info = module.AddApiColumnView()._make_api_call(a["cells"][0], config)
    if scope == "own":
        assert info["response_status"] == 200
        assert send.call_args.kwargs[location]["credential"] == "synthetic-test-credential"
    else:
        send.assert_not_called()
        assert info["response_status"] == 400


@pytest.mark.parametrize("provider", ["pinecone", "qdrant", "weaviate"])
@pytest.mark.parametrize("scope", ["own", "organization", "workspace"])
@pytest.mark.parametrize("module_name", ["dynamic_columns", "develop_dataset"])
def test_vector_secret_scope_without_ambient_context(tenants, scope, provider, module_name):
    module = import_module(f"model_hub.views.{module_name}")
    a, b, _ = tenants
    secret = _secret(a, b, scope)
    config = {
        "sub_type": provider, "api_key": str(secret.id), "url": "http://127.0.0.1:9/synthetic",
        "index_name": "synthetic", "collection_name": "synthetic", "top_k": 1,
        "embedding_config": {"type": "openai", "model": "synthetic"},
    }
    provider_module = Mock()
    provider_module.Pinecone.return_value.Index.return_value.query.return_value = {"matches": []}
    provider_module.QdrantClient.return_value.search.return_value = []
    clear_workspace_context()
    with patch("tfc.utils.lazy_extras.load_extra", return_value=provider_module), patch.object(module, "require_serving", create=True), patch.object(module.model_manager, "get_embeddings", return_value=[0.1]), patch.object(module.AddVectorDBColumnView, "_get_api_key_for_provider", return_value="synthetic-embedding-key"):
        module.AddVectorDBColumnView()._query_vector_db("text", config, a["org"].id, a["workspace"].id)
    constructor = {"pinecone": provider_module.Pinecone, "qdrant": provider_module.QdrantClient, "weaviate": provider_module.AuthApiKey}[provider]
    if scope == "own":
        constructor.assert_called_once()
        assert constructor.call_args.kwargs["api_key"] == "synthetic-test-credential"
    else:
        constructor.assert_not_called()
