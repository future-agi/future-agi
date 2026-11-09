import csv
import io
import json
from decimal import Decimal

from agentcc.services.provider_label import provider_label

CSV_COLUMNS = [
    "started_at",
    "request_id",
    "model",
    "provider",
    "status_code",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost",
    "is_error",
    "error_message",
    "cache_hit",
    "fallback_used",
    "guardrail_triggered",
    "user_id",
    "session_id",
    "routing_strategy",
]

MAX_EXPORT_ROWS = 10_000


def _value(log, column):
    """A column's value for the customer's download: the provider as customer
    screens name it, "Future AGI managed" for a managed call;
    the export has no upstream column."""
    if column == "provider":
        return provider_label(log.provider, log.billing_mode)
    return getattr(log, column, "")


def export_csv(queryset):
    """Generator that yields CSV rows from a request log queryset."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header row
    writer.writerow(CSV_COLUMNS)
    buf.seek(0)
    yield buf.read()
    buf.seek(0)
    buf.truncate()

    for log in queryset.iterator(chunk_size=1000):
        writer.writerow([_value(log, col) for col in CSV_COLUMNS])
        buf.seek(0)
        yield buf.read()
        buf.seek(0)
        buf.truncate()


def export_json(queryset):
    """Stream one JSON array without materializing the request log queryset."""
    yield "["
    first = True
    for log in queryset.iterator(chunk_size=1000):
        row = {}
        for col in CSV_COLUMNS:
            val = _value(log, col)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, Decimal):
                val = float(val)
            elif isinstance(val, (int, float, bool)):
                pass  # Keep as-is
            else:
                val = str(val) if val is not None else None
            row[col] = val
        if not first:
            yield ",\n"
        yield json.dumps(row)
        first = False
    yield "]\n"
