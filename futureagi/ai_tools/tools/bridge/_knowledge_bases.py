"""Bridge registration for KnowledgeBaseViewSet (model_hub)."""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.kb import KnowledgeBaseViewSet

expose_to_mcp(category="datasets")(KnowledgeBaseViewSet)
