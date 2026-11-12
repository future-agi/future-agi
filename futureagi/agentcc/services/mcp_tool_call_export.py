"""The explorer's download of the filtered tool-call view (CSV or NDJSON).

Its columns are SIEM export's closed, metadata-only columns
(siem_payloads.COLUMNS): never a call's arguments, result, error text or raw
identities, whatever the org stored or consented to capture. Each value is
bounded and redacted as SIEM export's are, the org's privacy redactor included
(the view passes it). A CSV cell that a
spreadsheet would read as a formula is prefixed with an apostrophe. Each
download is audited (actor, format, filters, row count) before it streams, and
refused when that audit row cannot be written.
"""

import csv
import io
import json

from accounts.models.audit_log import AuditScope
from agentcc.services.siem_payloads import COLUMNS, audit_values
from tfc.utils import audit

MAX_EXPORT_ROWS = 10_000
AUDIT_ACTION = "agentcc.mcp_tool_calls_exported"
FILTER_PARAMS = (
    "run_id",
    "agent_id",
    "api_key_id",
    "server_id",
    "tool",
    "outcome",
    "billing_mode",
    "from",
    "to",
    "min_latency_ms",
    "max_latency_ms",
)
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


class ExportAuditUnavailable(Exception):
    pass


def _cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def csv_lines(queryset, redactor=None):
    buf = io.StringIO()
    writer = csv.writer(buf)

    def flush():
        text = buf.getvalue()
        buf.seek(0)
        buf.truncate()
        return text

    writer.writerow(COLUMNS)
    yield flush()
    for row in queryset.iterator(chunk_size=500):
        writer.writerow([_cell(v) for v in audit_values(row, redactor).values()])
        yield flush()


def ndjson_lines(queryset, redactor=None):
    for row in queryset.iterator(chunk_size=500):
        yield json.dumps(audit_values(row, redactor), ensure_ascii=True) + "\n"


def applied_filters(params):
    return {
        name: str(params.get(name))[:128] for name in FILTER_PARAMS if params.get(name)
    }


def record_export(organization, actor, export_format, filters, row_count):
    entry = audit.log_audit(
        organization=organization,
        actor=actor,
        action=AUDIT_ACTION,
        scope=AuditScope.ORGANIZATION,
        target_id=organization.id,
        changes={},
        metadata={"format": export_format, "filters": filters, "row_count": row_count},
    )
    if entry is None:
        raise ExportAuditUnavailable()
