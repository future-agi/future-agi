from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

DASHBOARD_BASE_URL = os.environ.get(
    "DASHBOARD_BASE_URL", "https://app.futureagi.com"
).rstrip("/")


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Generate a markdown table from headers and rows.

    Args:
        headers: Column header names.
        rows: List of row data (each row is a list of values).

    Returns:
        Markdown-formatted table string.
    """
    if not rows:
        return "_No data found._"

    # Convert all values to strings
    str_rows = [[str(v) if v is not None else "—" for v in row] for row in rows]

    # Header row
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"

    # Data rows
    data_lines = []
    for row in str_rows:
        # Pad row if shorter than headers
        padded = row + ["—"] * (len(headers) - len(row))
        data_lines.append("| " + " | ".join(padded[: len(headers)]) + " |")

    return "\n".join([header_line, separator] + data_lines)


def dashboard_link(
    entity_type: str,
    entity_id: str,
    workspace_id: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    """Generate a dashboard link for an entity.

    Args:
        entity_type: Type of entity (evaluation, dataset, trace, project, agent).
        entity_id: UUID of the entity.
        workspace_id: Optional workspace UUID for URL construction.
        label: Optional display text for the link.

    Returns:
        Markdown link string.
    """
    path_map = {
        "dataset": f"dashboard/develop/{entity_id}",
        "evaluation": f"dashboard/evaluations/{entity_id}",
        "project": f"dashboard/observe/{entity_id}",
        "agent": f"dashboard/agents/playground/{entity_id}",
        "prompt_template": f"dashboard/workbench/create/{entity_id}",
        "experiment": f"dashboard/develop/experiment/{entity_id}/data",
        "dashboard": f"dashboard/dashboards/{entity_id}",
        "trace": "dashboard/observe",
    }
    path = path_map.get(entity_type, "dashboard/get-started")
    url = f"{DASHBOARD_BASE_URL}/{path}"
    display = label or f"{entity_type} {str(entity_id)}"
    return f"[{display}]({url})"


def format_datetime(dt: Optional[datetime]) -> str:
    """Format a datetime as human-readable relative time.

    Args:
        dt: Datetime to format. If None, returns "—".

    Returns:
        Relative time string like "2 hours ago" or absolute date.
    """
    if dt is None:
        return "—"

    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt
    seconds = int(diff.total_seconds())

    if seconds < 0:
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    if seconds < 604800:
        days = seconds // 86400
        return f"{days}d ago"

    return dt.strftime("%Y-%m-%d %H:%M UTC")


def truncate(text: Any, max_len: int = 500) -> str:
    """Truncate text with indicator if too long.

    Args:
        text: Text to truncate (will be str()'d).
        max_len: Maximum character length.

    Returns:
        Truncated string with "[truncated, N chars]" suffix if needed.
    """
    if text is None:
        return "—"
    s = str(text)
    if len(s) <= max_len:
        return s
    return s[:max_len] + f" [truncated, {len(s)} chars total]"


def format_uuid(uuid_val: Any) -> str:
    """Format a UUID as short form for display."""
    if uuid_val is None:
        return "—"
    s = str(uuid_val)
    return f"`{s}`"


def format_number(val: Any, decimals: int = 2) -> str:
    """Format a number for display."""
    if val is None:
        return "—"
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def format_status(status: Optional[str]) -> str:
    """Format a status string with emoji indicator."""
    if status is None:
        return "—"
    status_map = {
        "completed": "completed",
        "failed": "FAILED",
        "pending": "pending",
        "processing": "processing...",
        "active": "active",
        "inactive": "inactive",
    }
    return status_map.get(status.lower(), status)


def section(title: str, content: str) -> str:
    """Create a markdown section."""
    return f"## {title}\n\n{content}"


def key_value_block(pairs: list[tuple[str, Any]]) -> str:
    """Create a key-value block for display.

    Args:
        pairs: List of (label, value) tuples.

    Returns:
        Markdown-formatted key-value block.
    """
    lines = []
    for label, value in pairs:
        if value is not None:
            lines.append(f"**{label}:** {value}")
    return "\n".join(lines)
