"""Business logic for the Observe Users list and CSV export.

HTTP-free layer between the request boundary and the response: scope resolution,
ClickHouse query/execute, row formatting, span-attribute enrichment, and CSV
serialization. ``UsersView`` keeps only (de)serialization and response building.
"""

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import structlog

from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

logger = structlog.get_logger(__name__)


# (header, source field) — column order is the frontend export contract.
USERS_EXPORT_COLUMNS = [
    ("User ID", "user_id"),
    ("User ID Type", "user_id_type"),
    ("User ID Hash", "user_id_hash"),
    ("First Active", "activated_at"),
    ("Last Active", "last_active"),
    ("No. of Traces", "num_traces"),
    ("No. of Sessions", "num_sessions"),
    ("Avg Session Duration (s)", "avg_session_duration"),
    ("Total Tokens", "total_tokens"),
    ("Total Cost ($)", "total_cost"),
    ("Avg Latency / Trace (ms)", "avg_trace_latency"),
    ("No. of LLM Calls", "num_llm_calls"),
    ("Guardrails Triggered", "num_guardrails_triggered"),
    ("Evals Pass Rate (%)", "bool_eval_pass_rate"),
    ("Input Tokens", "input_tokens"),
    ("Output Tokens", "output_tokens"),
]


# CSV-injection guard: a cell starting with one of these executes as a formula
# in Excel/Sheets, so customer-controlled strings get a leading quote prefixed.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

_SKIP_ATTR_PREFIXES = (
    "raw.",
    "llm.input_messages",
    "llm.output_messages",
    "input.value",
    "output.value",
)

_CH_TIMEOUT_MS = 30000

# Hard cap on export rows. Bounds worker memory + latency for the large-workspace
# case this feature targets (matches agentcc's MAX_EXPORT_ROWS); a hit is logged
# and signalled in-band rather than silently truncating the download.
MAX_EXPORT_ROWS = 10_000


def _users_attr_enrichment_query(project_id=None):
    """Build the Observe-Users span-attribute enrichment query (DESIGN §3).

    P3b step1.5 DUAL id-remap so a cross-cutover straddler's attributes unify
    under the OLD curated id: resolve each span's ``end_user_id`` new→old via
    ``end_user_id_remap``, then both filter AND re-project on the resolved id so
    the caller buckets new-id spans under the old id. Resolve+filter lives in a
    wrapped ``WHERE`` (not ``PREWHERE``, which can't see the joined column).

    Returns ``(sql, params)``; the caller binds ``%(eu_ids)s``.
    """
    from tracer.services.clickhouse.v2.id_remap_sql import (
        remap_left_join,
        resolved_id_expr,
    )

    params: dict = {}
    project_clause = ""
    if project_id:
        params["attr_pid"] = str(project_id)
        project_clause = "AND project_id = toUUID(%(attr_pid)s)"

    remap_join = remap_left_join("end_user_id", "end_user_id_remap")
    resolved = resolved_id_expr("end_user_id")
    sql = f"""
    SELECT
        resolved_end_user_id AS end_user_id,
        attributes_extra,
        attrs_string,
        attrs_number
    FROM (
        SELECT
            {resolved} AS resolved_end_user_id,
            attributes_extra,
            attrs_string,
            attrs_number
        FROM spans
        {remap_join}
        WHERE is_deleted = 0
          {project_clause}
          AND (
            (attributes_extra != '{{}}' AND attributes_extra != '')
            OR length(mapKeys(attrs_string)) > 0
            OR length(mapKeys(attrs_number)) > 0
          )
    )
    WHERE resolved_end_user_id IN %(eu_ids)s
    """
    from tracer.services.clickhouse.v2.query_builders.filters import (
        _append_v2_settings,
    )

    return _append_v2_settings(sql), params


class UsersListManager:
    """Owns the Observe Users list + CSV export business logic."""

    def __init__(
        self,
        *,
        organization_id: str,
        allowed_project_ids: list[str],
        project_id: str | None = None,
        search: str | None = None,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
    ):
        self.organization_id = str(organization_id)
        self.project_id = str(project_id) if project_id else None
        self.search = search
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.scoped_project_ids, self.empty_scope = self._resolve_scope(
            self.project_id, allowed_project_ids
        )

    @staticmethod
    def _resolve_scope(
        project_id: str | None, allowed_project_ids: list[str]
    ) -> tuple[list[str], bool]:
        """Intersect the requested project with the caller's allowed projects.

        An out-of-scope project collapses to ``empty_scope`` — never an org-wide
        scan (CH25: the curated source has no ``workspace_id`` column to filter).
        """
        allowed_strs = {str(p) for p in allowed_project_ids}
        if project_id:
            if project_id in allowed_strs:
                return [project_id], False
            return [], True
        scoped = [str(p) for p in allowed_project_ids]
        return scoped, not scoped

    def _fetch_rows(
        self, *, limit: int | None, offset: int | None, max_rows: int | None = None
    ) -> tuple[list[dict], int, UserListQueryBuilderV2]:
        analytics = AnalyticsQueryService()
        builder = UserListQueryBuilderV2(
            organization_id=self.organization_id,
            project_ids=self.scoped_project_ids,
            search=self.search,
            limit=limit,
            offset=offset,
            max_rows=max_rows,
            filters=self.filters,
            sort_params=self.sort_params,
            empty_scope=self.empty_scope,
        )
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=_CH_TIMEOUT_MS)
        formatted = builder.format_rows(result.data)
        return formatted["table"], formatted["total_count"], builder

    def _enrich_with_span_attributes(self, rows: list[dict]) -> None:
        """Fold aggregated span attributes into each user row, in place (fail-open).

        Mutates ``rows`` and returns nothing — callers read the same list they
        passed in, so there is a single source of truth for the enriched rows.
        """
        end_user_ids = [r.get("end_user_id") for r in rows if r.get("end_user_id")]
        if not end_user_ids:
            return
        try:
            analytics = AnalyticsQueryService()
            attr_query, attr_params = _users_attr_enrichment_query(
                project_id=self.project_id
            )
            attr_params["eu_ids"] = tuple(str(e) for e in end_user_ids)
            attr_result = analytics.execute_ch_query(
                attr_query, attr_params, timeout_ms=_CH_TIMEOUT_MS
            )
            user_attrs: dict = {}
            for attr_row in attr_result.data:
                uid = str(attr_row.get("end_user_id", ""))
                raw = attr_row.get("attributes_extra", "{}")
                try:
                    attrs = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except (json.JSONDecodeError, TypeError):
                    attrs = {}
                # Fallback: merge from typed Map columns when raw is empty
                if not attrs:
                    str_map = attr_row.get("attrs_string") or {}
                    num_map = attr_row.get("attrs_number") or {}
                    if isinstance(str_map, dict):
                        attrs.update(str_map)
                    if isinstance(num_map, dict):
                        for k, v in num_map.items():
                            if k not in attrs:
                                attrs[k] = v
                if uid not in user_attrs:
                    user_attrs[uid] = {}
                for key, value in attrs.items():
                    if key.startswith(_SKIP_ATTR_PREFIXES):
                        continue
                    if isinstance(value, str) and len(value) > 500:
                        continue
                    if key not in user_attrs[uid]:
                        user_attrs[uid][key] = (
                            set() if isinstance(value, (str, int, float, bool)) else []
                        )
                    if isinstance(value, (str, int, float, bool)):
                        user_attrs[uid][key].add(
                            value if not isinstance(value, bool) else str(value).lower()
                        )
            for entry in rows:
                euid = str(entry.get("end_user_id", ""))
                for key, values in user_attrs.get(euid, {}).items():
                    if key not in entry:
                        if isinstance(values, set):
                            vals = sorted(values, key=str)
                            entry[key] = vals[0] if len(vals) == 1 else vals
                        else:
                            entry[key] = values
        except Exception as e:
            logger.warning(f"User span attribute enrichment failed: {e}")

    def _enrich_with_evals(self, rows: list[dict], builder: UserListQueryBuilderV2) -> None:
        """Enrich rows with eval pass rate (post-pagination for perf). Fail-open."""
        end_user_ids = [r.get("end_user_id") for r in rows if r.get("end_user_id")]
        if not end_user_ids:
            return
        try:
            eval_query, eval_params = builder.build_eval_query(
                [str(e) for e in end_user_ids]
            )
            if not eval_query:
                return
            analytics = AnalyticsQueryService()
            eval_result = analytics.execute_ch_query(
                eval_query, eval_params, timeout_ms=10000
            )
            eval_map = {
                str(r.get("end_user_id", "")): r for r in eval_result.data
            }
            for entry in rows:
                euid = str(entry.get("end_user_id", ""))
                ev = eval_map.get(euid, {})
                entry["bool_eval_pass_rate"] = ev.get("bool_eval_pass_rate", 0)
                entry["avg_output_float"] = ev.get("avg_output_float", 0)
        except Exception as e:
            logger.warning(f"User eval enrichment failed: {e}")

    def list_payload(self, *, page_size: int, current_page: int) -> dict:
        """Paginated list response: rows + span/eval enrichment + page totals."""
        rows, count, builder = self._fetch_rows(
            limit=page_size, offset=current_page * page_size
        )
        self._enrich_with_span_attributes(rows)
        self._enrich_with_evals(rows, builder)
        total_pages = (count // page_size) + (1 if count % page_size > 0 else 0)
        return {"table": rows, "total_count": count, "total_pages": total_pages}

    @classmethod
    def _format_export_cell(cls, value: Any):
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str) and value.startswith(_CSV_FORMULA_TRIGGERS):
            return "'" + value
        return value

    def iter_export_csv(self) -> Iterator[str]:
        """Stream the export as CSV text, header row first.

        The header is yielded BEFORE the ClickHouse fetch so the socket stays
        warm while the (slow) query runs — a buffered response would leave it
        idle past the LB read timeout. Rows are hard-capped at
        ``MAX_EXPORT_ROWS``; a cap hit or a mid-stream failure is logged and
        signalled in-band, since headers are already sent and the status can no
        longer change (otherwise a partial body reads as a clean 200).
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        def _drain() -> str:
            chunk = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            return chunk

        writer.writerow([header for header, _ in USERS_EXPORT_COLUMNS])
        yield _drain()

        try:
            # Fetch cap + 1 so a full page can be distinguished from a truncation.
            rows, _, _builder = self._fetch_rows(
                limit=None, offset=None, max_rows=MAX_EXPORT_ROWS + 1
            )
        except Exception:
            logger.exception(
                "users_export_failed",
                organization_id=self.organization_id,
                project_id=self.project_id,
            )
            writer.writerow(
                ["# export failed before completion; data may be incomplete"]
            )
            yield _drain()
            return

        truncated = len(rows) > MAX_EXPORT_ROWS
        if truncated:
            rows = rows[:MAX_EXPORT_ROWS]
            logger.warning(
                "users_export_truncated",
                organization_id=self.organization_id,
                project_id=self.project_id,
                max_rows=MAX_EXPORT_ROWS,
            )

        for row in rows:
            writer.writerow(
                [
                    self._format_export_cell(row.get(field))
                    for _, field in USERS_EXPORT_COLUMNS
                ]
            )
            yield _drain()

        if truncated:
            writer.writerow([f"# export truncated at {MAX_EXPORT_ROWS} rows"])
            yield _drain()
