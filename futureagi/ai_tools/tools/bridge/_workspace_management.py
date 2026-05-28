"""Bridge registration for accounts APIViews.

WorkspaceListAPIView and UserListAPIView are plain APIViews (not ModelViewSets).
The bridge auto-detects APIView subclasses and dispatches via HTTP method names
(get/post/...) instead of action names (list/retrieve/...). The serializer is
auto-discovered from the @validated_request decorator's closure.
"""

from accounts.views.workspace_management import (
    UserListAPIView,
    WorkspaceListAPIView,
)
from ai_tools.drf_bridge import expose_to_mcp

expose_to_mcp(
    category="users",
    tools={
        "get": {
            "name": "list_workspaces",
            "method": "GET",
            "detail": False,
        },
    },
)(WorkspaceListAPIView)


expose_to_mcp(
    category="users",
    tools={
        "get": {
            "name": "list_users",
            "method": "GET",
            "detail": False,
        },
    },
)(UserListAPIView)
