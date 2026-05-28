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
            "description": (
                "List workspaces the current user can access in their "
                "organization. Returns paginated workspace records with id, "
                "name, display name, and member counts. Filter by name with "
                "the search query param."
            ),
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
            "description": (
                "List users in the current organization. Returns paginated "
                "user records with id, name, email, role, and status. Filter "
                "by name/email with the search query param."
            ),
            "method": "GET",
            "detail": False,
        },
    },
)(UserListAPIView)
