"""Fixed refusal assertions, independent of the atomic-push sibling's tests."""

import json

FIELD = "mcp.servers.atomic.credential.secret.type"
CODE = "mcp_credential_placement_unsupported"
RAW_REASON = "internal-placement-refusal-detail-not-for-the-response"
REFUSAL_MESSAGE = (
    f"The MCP credential configuration at {FIELD} cannot be applied. No changes were "
    "saved. Correct the credential, the server URL or the catalog reference, then "
    "retry."
)


def _assert_refusal(response):
    body = response.json()
    assert response.status_code == 400, body
    assert body["code"] == CODE
    assert body["param"] == FIELD
    assert body["message"] == REFUSAL_MESSAGE
    assert RAW_REASON not in json.dumps(body)
    assert "gateway_synced" not in (body.get("result") or {})
