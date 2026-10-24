"""
Knowledge Base Search tool for AI agents.

Searches a knowledge base using vector similarity (RAG) to find
relevant content chunks. Uses the EmbeddingManager + ClickHouse
vector database internally.
"""

from typing import Optional

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class KBSearchInput(PydanticBaseModel):
    query: str = Field(
        description="Search query to find relevant content in the knowledge base"
    )
    kb_id: str = Field(description="Knowledge base ID to search in")
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of most relevant chunks to return (default 5)",
    )


@register_tool
class KBSearchTool(BaseTool):
    name = "search_knowledge_base"
    description = (
        "Search a knowledge base (RAG) for relevant content. "
        "Returns the most relevant text chunks based on semantic similarity. "
        "Use this to find specific information from uploaded documents, "
        "PDFs, or other knowledge sources."
    )
    category = "web"
    input_model = KBSearchInput

    def execute(self, params: KBSearchInput, context: ToolContext) -> ToolResult:
        try:
            from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
            from model_hub.models.develop_dataset import KnowledgeBaseFile

            try:
                # BaseTool.run sets workspace_context, so the default manager also
                # applies workspace scoping for workspace-bound KBs.
                kb = KnowledgeBaseFile.objects.get(
                    id=params.kb_id,
                    organization=context.organization,
                    deleted=False,
                )
            except KnowledgeBaseFile.DoesNotExist:
                return ToolResult.not_found("Knowledge base", params.kb_id)

            context_workspace_id = getattr(
                getattr(context, "workspace", None), "id", None
            )
            if (
                getattr(kb, "workspace_id", None)
                and context_workspace_id
                and str(kb.workspace_id) != str(context_workspace_id)
            ):
                return ToolResult.permission_denied(
                    "Knowledge base is not available in the current workspace."
                )

            manager = EmbeddingManager()
            filter_by = {"organization_id": str(context.organization_id)}
            results = manager.retrieve_rag_based_examples(
                query=params.query,
                table_name="syn",
                eval_id=params.kb_id,
                meta_data_col="chunk_text",
                input_type="text",
                filter_by=filter_by,
                top_k=params.top_k,
                threshold=0.25,
            )

            if not results:
                return ToolResult(
                    content=f"No relevant content found in knowledge base for: **{params.query}**",
                    data={"query": params.query, "kb_id": params.kb_id, "results": []},
                )

            # Format results for LLM consumption
            lines = [f"## Knowledge Base Results for: {params.query}\n"]
            result_data = []

            for i, result in enumerate(results, 1):
                # Extract chunk text and metadata from result
                if isinstance(result, dict):
                    chunk_text = result.get("chunk_text", result.get("text", ""))
                    metadata = result.get("metadata", {})
                    score = result.get("score", result.get("similarity", 0))
                elif isinstance(result, (list, tuple)) and len(result) >= 2:
                    chunk_text = (
                        result[0] if isinstance(result[0], str) else str(result[0])
                    )
                    score = result[1] if isinstance(result[1], (int, float)) else 0
                    metadata = result[2] if len(result) > 2 else {}
                else:
                    chunk_text = str(result)
                    score = 0
                    metadata = {}

                # Handle case where chunk_text is in metadata
                if not chunk_text and isinstance(metadata, dict):
                    chunk_text = metadata.get("chunk_text", "")

                if not chunk_text:
                    continue

                # Truncate very long chunks
                display_text = (
                    chunk_text[:800] + "..." if len(chunk_text) > 800 else chunk_text
                )

                lines.append(f"### Chunk {i} (relevance: {score:.2f})")
                lines.append(f"{display_text}\n")

                result_data.append(
                    {
                        "chunk_text": chunk_text,
                        "score": score,
                        "metadata": metadata if isinstance(metadata, dict) else {},
                    }
                )

            if not result_data:
                return ToolResult(
                    content=f"No relevant content found in knowledge base for: **{params.query}**",
                    data={"query": params.query, "kb_id": params.kb_id, "results": []},
                )

            return ToolResult(
                content="\n".join(lines),
                data={
                    "query": params.query,
                    "kb_id": params.kb_id,
                    "results": result_data,
                },
            )

        except Exception as e:
            logger.error("kb_search_error", error=str(e), kb_id=params.kb_id)
            return ToolResult.error(
                f"Knowledge base search failed: {str(e)}",
                error_code="KB_SEARCH_ERROR",
            )
