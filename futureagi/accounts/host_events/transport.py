"""POST one signed batch to EE and classify the answer. No Django or database dependency."""

from dataclasses import dataclass, field

import requests

from accounts.host_events.signing import SIGNATURE_HEADER, signature_header

# A removal waits for this before the next 30 s stream re-check, so keep both short.
CONNECT_TIMEOUT_SECONDS = 1.0
READ_TIMEOUT_SECONDS = 2.0
_MAX_RESPONSE_BYTES = 262_144
DELIVERED_OUTCOMES = ("applied", "replayed", "ignored")


@dataclass(frozen=True)
class SendResult:
    # Event ids EE accepted (applied now, or already applied before).
    delivered: frozenset
    # A short code for the rows that were not delivered, or "".
    error: str = ""
    # EE's clock when it received the batch (its answer's ``receivedAtMs``), when it answered.
    received_at_ms: int | None = None
    # Per delivered event id: EE's outcome, and its purge receipt for a lifecycle event (3.22).
    outcomes: dict = field(default_factory=dict)
    receipts: dict = field(default_factory=dict)


def _received_at(payload):
    value = payload.get("receivedAtMs")
    return value if type(value) is int and value > 0 else None


def _results(payload):
    """(outcome per answered id, receipt per id that carries one), or None when malformed."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None
    outcomes, receipts = {}, {}
    for item in payload["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        if isinstance(item.get("outcome"), str):
            outcomes[item["id"]] = item["outcome"]
        if isinstance(item.get("receipt"), dict):
            receipts[item["id"]] = item["receipt"]
    return outcomes, receipts


def send_batch(config, body, event_ids, *, now=None, session=None, timeout=None):
    """Deliver ``body`` (already encoded) and report which of ``event_ids`` EE took."""
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature_header(config.secret, config.key_id, body, now=now),
    }
    poster = session.post if session is not None else requests.post
    options = {}
    if config.ca_file:
        # EE's own CA, for this request only (OMEGA_HOST_EVENTS_CA_FILE). Unset, the default
        # trust (and a caller's session.verify) applies as before.
        options["verify"] = config.ca_file
    try:
        response = poster(
            config.url,
            data=body,
            headers=headers,
            timeout=timeout or (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            **options,
        )
    except requests.Timeout:
        return SendResult(frozenset(), "ee_timeout")
    except requests.RequestException:
        return SendResult(frozenset(), "ee_unreachable")
    if response.status_code == 401:
        # A wrong or rotated secret, or clocks more than five minutes apart.
        return SendResult(frozenset(), "ee_refused_signature")
    if response.status_code != 200:
        return SendResult(frozenset(), f"ee_http_{response.status_code}")
    content = response.content[: _MAX_RESPONSE_BYTES + 1]
    if len(content) > _MAX_RESPONSE_BYTES:
        return SendResult(frozenset(), "ee_response_too_large")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    answered = _results(payload)
    if answered is None:
        return SendResult(frozenset(), "ee_response_malformed")
    outcomes, receipts = answered
    delivered = frozenset(
        event_id
        for event_id in event_ids
        if outcomes.get(event_id) in DELIVERED_OUTCOMES
    )
    missing = set(event_ids) - delivered
    # A purge still running (READINESS lane 3.22) is asked about again later; it is no refusal.
    pending = bool(missing) and all(
        outcomes.get(event_id) == "pending" for event_id in missing
    )
    error = "" if not missing else "ee_purge_pending" if pending else "ee_event_refused"
    return SendResult(
        delivered,
        error,
        _received_at(payload),
        {event_id: outcomes[event_id] for event_id in delivered},
        {
            event_id: receipts[event_id]
            for event_id in delivered
            if event_id in receipts
        },
    )
