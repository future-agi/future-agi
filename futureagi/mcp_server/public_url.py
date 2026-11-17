"""The one public base URL of this deployment's MCP server.

Settings > MCP Server shows ``<base>/mcp`` to users, and the MCP server itself
advertises the same base in the OAuth issuer, the protected-resource metadata
and the 401 ``WWW-Authenticate`` header's ``resource_metadata``. They must
agree, or an IDE that follows the page fails discovery.

Precedence: the ``MCP_SERVER_BASE_URL`` environment variable, then
``settings.MCP_SERVER_HOST``, then ``settings.BASE_URL`` (the public API URL;
its default is Future AGI Cloud's only on a cloud deployment and the install's
own ``http://localhost:8000`` everywhere else). Returns None when none is set,
so callers hide the endpoint instead of inventing one.
"""

from __future__ import annotations

import os

from django.conf import settings


def mcp_public_base_url() -> str | None:
    for value in (
        os.environ.get("MCP_SERVER_BASE_URL", ""),
        getattr(settings, "MCP_SERVER_HOST", None) or "",
        getattr(settings, "BASE_URL", None) or "",
    ):
        value = str(value).strip().rstrip("/")
        if value:
            return value
    return None


def mcp_public_url() -> str | None:
    """The MCP endpoint users connect to: ``<base>/mcp``, or None."""
    base = mcp_public_base_url()
    return f"{base}/mcp" if base else None
