"""KB search passes the authenticated organization to vector retrieval."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from ai_tools.base import ToolContext
from ai_tools.tools.web.kb_search import KBSearchInput, KBSearchTool


@pytest.fixture
def embedding_manager(monkeypatch):
    """Replace the lazy embedding import without loading models or services."""
    module_name = "agentic_eval.core.embeddings.embedding_manager"
    module = ModuleType(module_name)
    manager = MagicMock()
    module.EmbeddingManager = MagicMock(return_value=manager)
    monkeypatch.setitem(sys.modules, module_name, module)
    return manager


@pytest.mark.parametrize(
    "organization_id",
    ["org-current", UUID("12345678-1234-5678-1234-567812345678")],
)
@pytest.mark.parametrize("has_results", [True, False])
def test_kb_search_scopes_retrieval_by_tool_context_org(
    embedding_manager, organization_id, has_results
):
    context = ToolContext(
        user=SimpleNamespace(id="user-current"),
        organization=SimpleNamespace(id=organization_id),
        workspace=SimpleNamespace(id="workspace-current"),
    )
    chunks = (
        [{"chunk_text": "Relevant content", "score": 0.9, "metadata": {}}]
        if has_results
        else []
    )
    embedding_manager.retrieve_rag_based_examples.return_value = chunks
    params = KBSearchInput(query="Find relevant content", kb_id="kb-requested", top_k=3)

    result = KBSearchTool().execute(params, context)

    embedding_manager.retrieve_rag_based_examples.assert_called_once_with(
        query=params.query,
        table_name="syn",
        eval_id=params.kb_id,
        meta_data_col="chunk_text",
        input_type="text",
        top_k=3,
        threshold=0.25,
        filter_by={"organization_id": str(organization_id)},
    )
    assert not result.is_error
    assert result.data == {
        "query": params.query,
        "kb_id": params.kb_id,
        "results": chunks,
    }
    if has_results:
        assert "Relevant content" in result.content
    else:
        assert "No relevant content found" in result.content
