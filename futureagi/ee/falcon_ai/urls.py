from django.urls import path

from ee.falcon_ai.views import (
    ConversationDetailView,
    ConversationListView,
    FileUploadView,
    MessageFeedbackView,
    StreamStatusView,
)
from ee.falcon_ai.views_connectors import (
    MCPConnectorAuthenticateView,
    MCPConnectorDetailView,
    MCPConnectorDiscoverView,
    MCPConnectorListView,
    MCPConnectorOAuthCallbackView,
    MCPConnectorTestView,
    MCPConnectorToolsView,
)
from ee.falcon_ai.views_memory import MemoryDeleteView, MemoryListView
from ee.falcon_ai.views_quick_analysis import QuickAnalysisView
from ee.falcon_ai.views_skills import SkillDetailView, SkillListView

urlpatterns = [
    # Conversations
    path("conversations/", ConversationListView.as_view(), name="falcon-conversations"),
    path(
        "conversations/<uuid:conversation_id>/",
        ConversationDetailView.as_view(),
        name="falcon-conversation-detail",
    ),
    path(
        "conversations/<uuid:conversation_id>/stream-status/",
        StreamStatusView.as_view(),
        name="falcon-stream-status",
    ),
    path(
        "messages/<uuid:message_id>/feedback/",
        MessageFeedbackView.as_view(),
        name="falcon-message-feedback",
    ),
    # File upload
    path("files/upload/", FileUploadView.as_view(), name="falcon-file-upload"),
    # Skills
    path("skills/", SkillListView.as_view(), name="falcon-skills"),
    path(
        "skills/<uuid:skill_id>/",
        SkillDetailView.as_view(),
        name="falcon-skill-detail",
    ),
    # Memory
    path("memory/", MemoryListView.as_view(), name="falcon-memory"),
    path(
        "memory/<uuid:memory_id>/",
        MemoryDeleteView.as_view(),
        name="falcon-memory-delete",
    ),
    # MCP Connectors
    path(
        "mcp-connectors/", MCPConnectorListView.as_view(), name="falcon-mcp-connectors"
    ),
    path(
        "mcp-connectors/<uuid:connector_id>/",
        MCPConnectorDetailView.as_view(),
        name="falcon-mcp-connector-detail",
    ),
    path(
        "mcp-connectors/<uuid:connector_id>/discover/",
        MCPConnectorDiscoverView.as_view(),
        name="falcon-mcp-connector-discover",
    ),
    path(
        "mcp-connectors/<uuid:connector_id>/test/",
        MCPConnectorTestView.as_view(),
        name="falcon-mcp-connector-test",
    ),
    path(
        "mcp-connectors/<uuid:connector_id>/tools/",
        MCPConnectorToolsView.as_view(),
        name="falcon-mcp-connector-tools",
    ),
    path(
        "mcp-connectors/<uuid:connector_id>/authenticate/",
        MCPConnectorAuthenticateView.as_view(),
        name="falcon-mcp-connector-authenticate",
    ),
    path(
        "mcp-connectors/<uuid:connector_id>/oauth/callback/",
        MCPConnectorOAuthCallbackView.as_view(),
        name="falcon-mcp-connector-oauth-callback",
    ),
    # Quick analysis (single-shot LLM for Imagine dynamic widgets)
    path("quick-analysis/", QuickAnalysisView.as_view(), name="falcon-quick-analysis"),
]
