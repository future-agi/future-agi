"""Public projection of persisted integration diagnostics, including old rows."""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from integrations.services.export_failures import (
    UNSUPPORTED_SINCE,
    UNSUPPORTED_VERSION,
    failure_from_details,
    safe_details,
)

# Fixed English month abbreviations, independent of the server locale.
MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

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


def _stopped_since(connection, log) -> datetime:
    """When the import first failed with UNSUPPORTED_VERSION, in UTC."""
    cursor = connection.sync_cursor if isinstance(connection.sync_cursor, dict) else {}
    value = cursor.get(UNSUPPORTED_SINCE)
    if isinstance(value, str):
        try:
            since = datetime.fromisoformat(value)
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            return since.astimezone(UTC)
        except ValueError:
            pass
    return log.started_at.astimezone(UTC)


def stopped_import_sentence(connection, log) -> str:
    """The connection's state once its Langfuse no longer serves the list API.

    It says what failed, since when, why, and that nothing already imported
    changed. Sync History rows keep the failure's own message.
    """
    since = _stopped_since(connection, log)
    day = f"{since.day} {MONTHS[since.month - 1]} {since.year}"
    message = failure_from_details(connection, {"kind": UNSUPPORTED_VERSION}).message
    return (
        f"{message} Import stopped on {day}: Langfuse no longer serves the API "
        "this import reads. Everything already imported is unchanged."
    )


def public_status_message(connection) -> str | None:
    raw = connection.status_message
    if not raw or raw in FIXED_NOTES:
        return raw
    latest = getattr(connection, "latest_sync_log", None)
    log = latest[0] if latest else None
    if latest is None:
        log = connection.sync_logs.filter(deleted=False).first()
    if log and log.error_message:
        if (
            log.status == "failed"
            and safe_details(log.error_details).get("kind") == UNSUPPORTED_VERSION
        ):
            return stopped_import_sentence(connection, log)
        return public_sync_message(log)
    return "The last sync did not complete. Check Sync History for failure details."
