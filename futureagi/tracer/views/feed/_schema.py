"""Shared documentation-only authentication context for error-feed reads."""

from drf_yasg import openapi

# Keep this out of the runtime query serializers: authentication may ignore the
# selector (including malformed values) for an active, accessible pinned key.
WORKSPACE_QUERY_PARAMETER = openapi.Parameter(
    "workspace_id",
    openapi.IN_QUERY,
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
    required=False,
    description=(
        "Workspace selector used by authentication for unpinned API keys. "
        "X-Workspace-Id takes precedence. Ignored for an active workspace-pinned "
        "key with current access; use a key scoped to the intended workspace."
    ),
)
