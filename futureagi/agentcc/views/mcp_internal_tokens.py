"""The gateway's internal per-user token endpoint (M25x, ``mcp-upstream-token-v1``):
``POST /agentcc/internal/mcp/upstream-token/``.

Authenticated only by ``X-Agentcc-MCP-Internal: <AGENTCC_MCP_DJANGO_SECRET>``,
compared in constant time. No other credential is read: not a user's token,
not an API key, not the gateway admin token (a secret equal to the admin
token is refused, so the two are never interchangeable). A bad or missing
secret is 401 and nothing is looked up.

Every answer carries ``Cache-Control: no-store``. No middleware records a
request or response body (django-structlog logs the method and path,
deployment telemetry skips anonymous calls), and this view logs ids and
outcomes only.
"""

import hmac
import json

import structlog
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from agentcc import mcp_features
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens

logger = structlog.get_logger(__name__)

SECRET_HEADER = "X-Agentcc-MCP-Internal"
MAX_BODY_BYTES = 4096


def _json(body: dict, status: int = 200) -> JsonResponse:
    # Also used by the vendor credential endpoint; neither authentication
    # boundary changes when the connect policy refuses token delivery.
    from agentcc.services import microsoft_connect_gate as microsoft

    if body.get("error") == microsoft.CODE:
        status = 409
        # Keep a body's own message: a held partner binding's (BINDING_MESSAGE)
        # or a stopped grant's (GRANT_MESSAGE).
        body = {"message": microsoft.MESSAGE, **body}
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


@csrf_exempt
@require_POST
def upstream_token(request):
    secret = connect.internal_secret()
    sent = request.headers.get(SECRET_HEADER, "")
    if secret is None or not hmac.compare_digest(sent.encode(), secret.encode()):
        logger.warning("mcp_upstream_token_unauthorized", configured=secret is not None)
        return _json({"error": "unauthorized"}, status=401)
    if len(request.body) > MAX_BODY_BYTES:
        return _json({"error": "invalid_request"}, status=400)
    try:
        body = json.loads(request.body or b"null")
    except ValueError:
        body = None
    if not tokens.valid_request(body):
        return _json({"error": "invalid_request"}, status=400)
    if mcp_features.hidden_server(body["org_id"], body["server_id"]):
        return mcp_features.not_found()
    answer = tokens.answer_request(body)
    return _json(answer.body())
