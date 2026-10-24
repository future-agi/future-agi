from types import SimpleNamespace

from ai_tools.base import ToolContext
from ai_tools.tools.web.kb_search import KBSearchInput, KBSearchTool
from model_hub.models.develop_dataset import KnowledgeBaseFile


class FakeEmbeddingManager:
    last_kwargs = None
    instantiated = False

    def __init__(self):
        type(self).instantiated = True

    def retrieve_rag_based_examples(self, **kwargs):
        type(self).last_kwargs = kwargs
        return [
            {
                "chunk_text": "retrieved content",
                "score": 0.9,
                "metadata": {"organization_id": "org-current"},
            }
        ]


def make_context():
    return ToolContext(
        user=SimpleNamespace(id="user-1"),
        organization=SimpleNamespace(id="org-current"),
        workspace=SimpleNamespace(id="workspace-current"),
    )


def test_kb_search_scopes_retrieval_by_tool_context_org(monkeypatch):
    context = make_context()
    captured_get_kwargs = {}

    def fake_get(**kwargs):
        captured_get_kwargs.update(kwargs)
        return SimpleNamespace(
            id=kwargs["id"],
            organization=kwargs["organization"],
            workspace_id=context.workspace_id,
        )

    FakeEmbeddingManager.last_kwargs = None
    FakeEmbeddingManager.instantiated = False
    monkeypatch.setattr(KnowledgeBaseFile.objects, "get", fake_get)
    monkeypatch.setattr(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager",
        FakeEmbeddingManager,
    )

    result = KBSearchTool().execute(
        KBSearchInput(query="billing policy", kb_id="kb-current", top_k=3),
        context,
    )

    assert not result.is_error
    assert captured_get_kwargs == {
        "id": "kb-current",
        "organization": context.organization,
        "deleted": False,
    }
    assert FakeEmbeddingManager.last_kwargs is not None
    assert FakeEmbeddingManager.last_kwargs["filter_by"] == {
        "organization_id": str(context.organization_id)
    }
    assert FakeEmbeddingManager.last_kwargs["top_k"] == 3


def test_kb_search_rejects_kb_outside_current_context(monkeypatch):
    def fake_get(**kwargs):
        raise KnowledgeBaseFile.DoesNotExist

    FakeEmbeddingManager.last_kwargs = None
    FakeEmbeddingManager.instantiated = False
    monkeypatch.setattr(KnowledgeBaseFile.objects, "get", fake_get)
    monkeypatch.setattr(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager",
        FakeEmbeddingManager,
    )

    result = KBSearchTool().execute(
        KBSearchInput(query="billing policy", kb_id="kb-other-org"),
        make_context(),
    )

    assert result.is_error
    assert result.error_code == "NOT_FOUND"
    assert not FakeEmbeddingManager.instantiated
    assert FakeEmbeddingManager.last_kwargs is None


def test_kb_search_rejects_kb_from_another_workspace(monkeypatch):
    def fake_get(**kwargs):
        return SimpleNamespace(
            id=kwargs["id"],
            organization=kwargs["organization"],
            workspace_id="workspace-other",
        )

    FakeEmbeddingManager.last_kwargs = None
    FakeEmbeddingManager.instantiated = False
    monkeypatch.setattr(KnowledgeBaseFile.objects, "get", fake_get)
    monkeypatch.setattr(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager",
        FakeEmbeddingManager,
    )

    result = KBSearchTool().execute(
        KBSearchInput(query="billing policy", kb_id="kb-other-workspace"),
        make_context(),
    )

    assert result.is_error
    assert result.error_code == "PERMISSION_DENIED"
    assert not FakeEmbeddingManager.instantiated
    assert FakeEmbeddingManager.last_kwargs is None
