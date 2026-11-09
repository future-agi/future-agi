"""HEC event framing and bounded, value-free acknowledgements.

HEC is an audit-event destination, not an OTLP span destination. It shares
the existing metadata projection, credential vault and guarded HTTP client.
A probe sends one synthetic event but never creates a destination, delivery,
audit entry or tool-call row. The supplied token is never echoed, even as a
suffix. A collector response is untrusted: only its integer code is used.
"""

import json

from django.utils import timezone

from agentcc.models.siem import STREAM_AUDIT_EVENTS
from agentcc.services import siem_payloads, siem_signing

MAX_ACK_BYTES = 4096


def validate_streams(streams):
    from agentcc.services.siem_destinations import DestinationError

    if streams != [STREAM_AUDIT_EVENTS]:
        raise DestinationError("HEC destinations export audit events only.")


def encode_events(rows, redactor):
    events = siem_payloads.audit_events(rows, redactor)
    return b"\n".join(
        siem_signing.encode(
            {
                "time": row.started_at.timestamp(),
                "sourcetype": "agentcc:tool_call",
                "event": {**event, "organization_id": str(row.organization_id)},
            }
        )
        for row, event in zip(rows, events, strict=True)
    )


def acknowledgement(response):
    """Read at most 4097 bytes inside the delivery's wall-clock deadline."""
    raw = bytearray()
    for chunk in response.iter_content(chunk_size=MAX_ACK_BYTES + 1):
        raw.extend(chunk)
        if len(raw) > MAX_ACK_BYTES:
            return "hec_invalid_ack"
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return "hec_invalid_ack"
    code = body.get("code") if isinstance(body, dict) else None
    if type(code) is not int:
        return "hec_invalid_ack"
    if code == 0:
        return ""
    if code in (1, 2, 3, 4):
        return "hec_token_rejected"
    return "hec_rejected"


def test_connection(data):
    from agentcc.services.siem_delivery import _post
    from agentcc.services.siem_destinations import DestinationError, _header_value, _url
    from agentcc.services.siem_settings import export_enabled

    if not export_enabled():
        raise DestinationError(
            "SIEM export is not turned on for this deployment.",
            code="siem_export_unavailable",
            status=409,
        )
    url = _url("hec", data["url"])
    credential = _header_value(data["token"])
    raw = siem_signing.encode(
        {
            "time": timezone.now().timestamp(),
            "sourcetype": "agentcc:connection_test",
            "event": {"type": "siem.connection_test", "synthetic": True},
        }
    )
    headers = {
        "Content-Type": "application/json",
        "User-Agent": siem_signing.USER_AGENT,
        "Authorization": "Splunk " + credential,
    }
    code, _, error = _post(url, raw, headers, hec=True)
    if code in (401, 403) or error == "hec_token_rejected":
        raise DestinationError(
            "The collector rejected the HEC token. Check the token and try again.",
            code="hec_token_rejected",
        )
    if code is None or not 200 <= code < 300 or error:
        raise DestinationError(
            "The collector did not accept the test event. Check its URL and availability.",
            code="hec_connection_failed",
        )
    return {
        "connected": True,
        "message": "Connection successful. One synthetic event sent; nothing saved.",
    }
