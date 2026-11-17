"""Shared response copy for a config write refused before commit."""

from agentcc.services.routing_key_rules import with_rejection_param

# Existing site-gated refusal copy; render only the field, not an internal reason.
MCP_CREDENTIAL_PLACEMENT_REFUSED_MESSAGE = (
    "The MCP credential configuration at {param} cannot be applied. No changes were "
    "saved. Correct the credential, the server URL or the catalog reference, then "
    "retry."
)


def mcp_credential_placement_refused_response(gm, error):
    return with_rejection_param(
        gm.custom_error_response(
            400,
            MCP_CREDENTIAL_PLACEMENT_REFUSED_MESSAGE.format(param=error.param),
            code=error.code,
        ),
        error,
    )
