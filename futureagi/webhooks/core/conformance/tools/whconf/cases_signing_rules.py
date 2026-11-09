"""signing.json: the rules block (DESIGN.md sections 5.1-5.6)."""

from __future__ import annotations

from . import reference as R


def signing_rules() -> dict:
    rules = {
        "scheme": "Standard Webhooks v1 (DESIGN.md section 5.2)",
        "key": "base64decode(secret without the 'whsec_' prefix); a create or rotate accepts 24-64 key bytes, "
        "padded standard base64 only",
        "signed_content": "ascii(webhook-id) + '.' + ascii(decimal t) + '.' + body bytes",
        "signature": "base64(HMAC-SHA256(key, signed_content)), sent as 'v1,<signature>'",
        "several_signatures": "space-delimited, newest secret first; a receiver accepts if ANY v1 entry matches and "
        "ignores entries whose version it does not know",
        "tolerance_s": R.TOLERANCE_S,
        "tolerance_rule": "reject when |now - t| > 300 (t + 300 passes, t + 301 fails, both directions)",
        "compare": "constant time",
        "verify_results": [
            "ok",
            "missing_headers",
            "invalid_timestamp",
            "timestamp_too_old",
            "timestamp_too_new",
            "no_matching_signature",
        ],
        "header_order": "content-type, user-agent, webhook-id, webhook-timestamp, webhook-signature, "
        "X-FutureAGI-Event, X-FutureAGI-Delivery, X-FutureAGI-Attempt, then X-FutureAGI-Sequence "
        "(real deliveries) or X-FutureAGI-Test: 1 (tests); custom headers are not part of these vectors",
        "transport_headers": "the headers in these vectors are what the delivery core builds. The Transport then "
        "adds its own: Host (the URL host, IDNA, with the port when it is not the default), Content-Length (the "
        "body's byte length) and Accept-Encoding: identity (DESIGN.md section 6.2), and sends no Cookie, no "
        "proxy or forwarding header and no other encoding. These names are reserved, so no custom header may "
        "set them (DESIGN.md section 5.1)",
    }
    return rules
