"""Gateway-only mcp-vendor-credential-v1; no session or admin-token fallback."""

import hmac
import json

from django.views.decorators.csrf import csrf_exempt

from agentcc.services import mcp_vendor_credentials as vendor
from agentcc.services.mcp_upstream_oauth import internal_secret
from agentcc.services.mcp_vendor_http import VendorUnavailable
from agentcc.views.mcp_internal_tokens import _json


@csrf_exempt
def vendor_credential(request):
    if not vendor.enabled():
        return _json({"error": "not_found"}, status=404)
    if request.method != "POST":
        return _json({"error": "method_not_allowed"}, status=405)
    secret = internal_secret()
    sent = request.headers.get("X-Agentcc-MCP-Internal", "")
    if secret is None or not hmac.compare_digest(sent.encode(), secret.encode()):
        return _json({"error": "unauthorized"}, status=401)
    if len(request.body) > 4096:
        return _json({"error": "invalid_request"}, status=400)
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeError):
        body = None
    if not vendor.valid_request(body):
        return _json({"error": "invalid_request"}, status=400)
    try:
        return _json(vendor.answer_request(body))
    except VendorUnavailable:
        return _json({"error": "vendor_unavailable"}, status=503)
