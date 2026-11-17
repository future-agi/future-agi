"""F02 synthetic external boundaries; real auth, ORM, dispatch and threads remain."""

import asyncio
from types import SimpleNamespace

import pytest

from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.views import dynamic_columns
from tfc.temporal.drop_in import workflow as task_workflow


@pytest.fixture
def transport(monkeypatch):
    """Keep delay/apply_async/runner/serialization/workflow and sync DB wrapper real."""
    captured = []

    class Client:
        namespace = "synthetic-no-network"

        async def start_workflow(self, run, payload, **kwargs):
            assert run is task_workflow.TaskRunnerWorkflow.run
            captured.append(payload)

    async def client():
        return Client()

    monkeypatch.setattr("tfc.temporal.common.client.get_client", client)

    def execute(payload, module):
        async def run_activity(name, envelope, **options):
            assert name in {"conditional_column_async", "add_vector_db_column_async"}
            activity = getattr(module, name)
            # Real activity sync wrapper and real row-thread wrapper. The only
            # substituted boundary is Temporal transport (no server/heartbeats).
            return await asyncio.to_thread(
                activity.run_sync,
                *envelope["args"],
                **envelope["kwargs"],
            )

        monkeypatch.setattr(task_workflow.workflow, "execute_activity", run_activity)
        return asyncio.run(task_workflow.TaskRunnerWorkflow().run(payload))

    return SimpleNamespace(captured=captured, execute=execute)


@pytest.fixture
def providers(monkeypatch):
    reveals, calls = [], []
    for model in (ApiKey, SecretModel):
        getter = model.actual_key.fget

        def reveal(row, getter=getter):
            reveals.append((type(row).__name__, str(row.pk)))
            return getter(row)

        monkeypatch.setattr(model, "actual_key", property(reveal))

    def embeddings(text, provider, model, *, model_params):
        assert model_params["api_key"] == "synthetic-embedding-only"
        calls.append("embedding")
        return [0.1, 0.2]

    class VectorClient:
        def __init__(self, *, api_key, **kwargs):
            assert api_key == "synthetic-vector-only"
            calls.append("vector")
            self.query = self

        def Index(self, name):
            return SimpleNamespace(
                query=lambda **kw: {
                    "matches": [{"metadata": {"answer": "synthetic-result"}}]
                }
            )

        def search(self, **kwargs):
            return [SimpleNamespace(payload={"answer": "synthetic-result"})]

        def get(self, *args):
            return self

        def with_near_vector(self, *args):
            return self

        def with_hybrid(self, **kwargs):
            return self

        def with_limit(self, value):
            return self

        def do(self):
            return {"data": {"Get": {"synthetic": [{"answer": "synthetic-result"}]}}}

    def extra(name, group):
        assert group == "vectordb"
        if name == "pinecone":
            return SimpleNamespace(Pinecone=VectorClient)
        if name == "qdrant_client":
            return SimpleNamespace(QdrantClient=VectorClient)
        assert name == "weaviate"
        return SimpleNamespace(
            AuthApiKey=lambda *, api_key: api_key,
            connect_to_wcs=lambda **kw: VectorClient(api_key=kw["auth_credentials"]),
        )

    monkeypatch.setattr("tfc.utils.lazy_extras.load_extra", extra)
    monkeypatch.setattr(dynamic_columns.model_manager, "get_embeddings", embeddings)
    # Inert serving health boundary only, not model allowlist/authorization.
    monkeypatch.setattr(dynamic_columns, "serving_available", lambda: True)
    monkeypatch.setattr(dynamic_columns, "require_serving", lambda: None)
    return SimpleNamespace(reveals=reveals, calls=calls)
