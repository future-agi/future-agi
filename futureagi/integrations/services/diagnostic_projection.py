"""Public projection of persisted integration diagnostics, including old rows."""

import re
from urllib.parse import urlsplit, urlunsplit

from integrations.services.export_failures import failure_from_details, safe_details

FIXED_NOTES = frozenset(
    {
        "Target project was deleted. Please reconfigure.",
        "Target project was deleted during sync. Please reconfigure.",
        "Project deleted during sync",
    }
)
SKIPPED_NOTE = re.compile(
    r"[0-9]{1,12} request logs? from before Datadog's 18-hour window (?:were|was) "
    r"not sent: Datadog accepts logs from the last 18 hours\."
)


def public_host(value: str) -> str:
    """Keep destination metadata, not historical URL credentials or controls."""
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        # Do not expose any path when it was supplied along with URL credentials.
        path = "" if parts.username or parts.query or parts.fragment else parts.path
        return urlunsplit((parts.scheme, host, path, "", ""))
    except ValueError:
        return ""


def public_sync_message(log) -> str | None:
    raw = log.error_message
    if not raw:
        return raw
    if raw in FIXED_NOTES or SKIPPED_NOTE.fullmatch(raw):
        return raw
    details = safe_details(log.error_details)
    message = failure_from_details(log.connection, details).message
    if log.status == "partial" and log.connection.platform == "langfuse":
        return f"Partial sync: {log.traces_fetched} traces ingested before error. {message}"
    return message


def public_status_message(connection) -> str | None:
    raw = connection.status_message
    if not raw or raw in FIXED_NOTES:
        return raw
    latest = getattr(connection, "latest_sync_log", None)
    log = latest[0] if latest else None
    if latest is None:
        log = connection.sync_logs.filter(deleted=False).first()
    if log and log.error_message:
        return public_sync_message(log)
    return "The last sync did not complete. Check Sync History for failure details."
