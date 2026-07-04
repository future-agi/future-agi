import uuid
import pytest
from unittest.mock import patch, MagicMock

from ai_tools.tests.conftest import run_tool
from model_hub.models.kb import KnowledgeBase

@pytest.fixture(scope="session")
def django_db_setup():
    """Override and disable database setup entirely since we mock all database calls."""
    pass

@pytest.fixture
def tool_context():
    """Override database-dependent tool_context fixture with a clean in-memory mock."""
    from ai_tools.base import ToolContext
    user = MagicMock()
    org = MagicMock()
    workspace = MagicMock()
    
    org.id = uuid.uuid4()
    user.organization = org
    
    return ToolContext(user=user, organization=org, workspace=workspace)

class TestKBSearchTool:
    def test_kb_search_same_organization_success(self, tool_context):
        kb_id = str(uuid.uuid4())
        mock_kb = MagicMock(spec=KnowledgeBase)

        mock_results = [{"chunk_text": "hello test chunk content", "score": 0.95}]

        # Pre-import to ensure module is loaded in sys.modules
        import agentic_eval.core.embeddings.embedding_manager

        # Mock the KnowledgeBase.objects.get lookup and the EmbeddingManager retrieve method
        with patch("model_hub.models.kb.KnowledgeBase.objects.get") as mock_get, \
             patch("agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.retrieve_rag_based_examples") as mock_retrieve:
            
            mock_get.return_value = mock_kb
            mock_retrieve.return_value = mock_results

            result = run_tool(
                "search_knowledge_base",
                {"query": "hello", "kb_id": kb_id, "top_k": 3},
                tool_context
            )

            # Assertions
            assert not result.is_error
            assert "hello test chunk content" in result.content
            
            mock_get.assert_called_once_with(
                id=kb_id,
                organization_id=tool_context.organization.id,
            )
            mock_retrieve.assert_called_once_with(
                query="hello",
                table_name="syn",
                eval_id=kb_id,
                meta_data_col="chunk_text",
                input_type="text",
                top_k=3,
                threshold=0.25,
            )

    def test_kb_search_different_organization_denied(self, tool_context):
        kb_id = str(uuid.uuid4())

        # Mock the KnowledgeBase.objects.get lookup to raise DoesNotExist
        with patch("model_hub.models.kb.KnowledgeBase.objects.get") as mock_get:
            mock_get.side_effect = KnowledgeBase.DoesNotExist

            result = run_tool(
                "search_knowledge_base",
                {"query": "secret query", "kb_id": kb_id},
                tool_context
            )

            # Should fail with KB_NOT_FOUND error
            assert result.is_error
            assert result.error_code == "KB_NOT_FOUND"
            assert "not found or permission denied" in result.content
            mock_get.assert_called_once_with(
                id=kb_id,
                organization_id=tool_context.organization.id,
            )

    def test_kb_search_invalid_uuid_fails(self, tool_context):
        # Mock the KnowledgeBase.objects.get lookup to raise ValueError (for invalid UUID string)
        with patch("model_hub.models.kb.KnowledgeBase.objects.get") as mock_get:
            mock_get.side_effect = ValueError("invalid UUID")

            result = run_tool(
                "search_knowledge_base",
                {"query": "query", "kb_id": "not-a-valid-uuid"},
                tool_context
            )

            assert result.is_error
            assert result.error_code == "KB_NOT_FOUND"
            mock_get.assert_called_once_with(
                id="not-a-valid-uuid",
                organization_id=tool_context.organization.id,
            )
