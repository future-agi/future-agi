"""
Base Query Builder for ClickHouse analytics queries.

Provides the abstract interface and shared utilities that all concrete
query builders inherit from.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple


def _to_utc_naive(dt: datetime) -> datetime:
    """Convert a (possibly tz-aware) datetime to UTC-naive.

    ClickHouse DateTime columns store UTC. The frontend now sends
    offset-aware ISO strings reflecting the user's local timezone (e.g.
    ``2026-05-06T00:00:00+05:30`` for "midnight today in IST"). We must
    convert to UTC *before* stripping tzinfo — otherwise dropping the
    offset silently shifts the bound by the user's UTC offset (a 5.5h
    silent skew for IST users picking "today").
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def _parse_dt(val: Any) -> Optional[datetime]:
    """Parse a datetime value from various formats.

    Accepts ISO 8601 strings with or without timezone offset, Python
    datetime objects, and the legacy ``%Y-%m-%dT%H:%M:%S.%fZ`` format.
    Always returns a UTC-naive ``datetime`` so that ClickHouse — which
    interprets bare DateTime values as UTC — bounds correctly regardless
    of the caller's local timezone.

    Args:
        val: A datetime object or an ISO-format string.

    Returns:
        A UTC-naive ``datetime`` instance, or ``None`` if parsing fails.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return _to_utc_naive(val)
    if isinstance(val, str):
        # Try standard ISO format first (handles 'Z' and explicit offsets
        # like '+05:30'). fromisoformat in Py3.11+ handles 'Z' natively;
        # rewrite for older runtimes.
        cleaned = val.replace("Z", "+00:00")
        try:
            return _to_utc_naive(datetime.fromisoformat(cleaned))
        except (ValueError, AttributeError):
            pass
        # Fallback: try strptime with common formats. The 'Z'-suffixed
        # variants are inherently UTC; the bare ones are assumed UTC by
        # convention because no offset means "the value is already in CH's
        # native timezone (UTC)".
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


class BaseQueryBuilder(ABC):
    """Base class for all ClickHouse query builders.

    Provides shared utilities for parameter management, project scoping,
    time-range parsing, time bucketing, and result formatting.  Subclasses
    must implement :meth:`build` which returns a ``(query_string, params)``
    tuple ready for ``ClickHouseClient.execute_read()``.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        project_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        # Either a single project_id (per-project mode) OR a list of
        # project_ids (org-scoped mode where the caller resolved the org's
        # projects in Python and passes them down). Builders use
        # `project_where()` which switches its emitted SQL based on which
        # mode is active.
        self.project_id = project_id
        self.project_ids: Optional[List[str]] = (
            [str(p) for p in project_ids] if project_ids else None
        )
        self.params: Dict[str, Any] = {}
        if self.project_ids:
            # ClickHouse parameterized IN expects a tuple
            self.params["project_ids"] = tuple(self.project_ids)
        else:
            self.params["project_id"] = project_id

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def build(self) -> Tuple[str, Dict[str, Any]]:
        """Build and return ``(query_string, params_dict)``."""
        pass

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def project_where(self, table_alias: str = "") -> str:
        """Return the base WHERE clause for project scoping and soft-delete exclusion.

        Switches between single-project (`project_id = %(project_id)s`) and
        multi-project (`project_id IN %(project_ids)s`) based on which mode
        the builder was constructed with.

        Args:
            table_alias: Optional table alias to prefix column names with.

        Returns:
            A ``WHERE`` clause fragment.
        """
        prefix = f"{table_alias}." if table_alias else ""
        return (
            f"WHERE {self.project_filter_sql(table_alias)} "
            f"AND {prefix}_peerdb_is_deleted = 0"
        )

    def project_filter_sql(self, table_alias: str = "") -> str:
        """Return just the project_id filter expression (no WHERE keyword).

        Useful for builders that splice the project filter into hand-written
        WHERE clauses elsewhere (e.g. content/attribute lookup queries).
        """
        prefix = f"{table_alias}." if table_alias else ""
        if self.project_ids is not None:
            return f"{prefix}project_id IN %(project_ids)s"
        return f"{prefix}project_id = %(project_id)s"

    @staticmethod
    def time_bucket_expr(interval: str) -> str:
        """Return the ClickHouse time-bucketing function name for *interval*.

        Args:
            interval: One of ``"hour"``, ``"day"``, ``"week"``, ``"month"``,
                ``"year"``.

        Returns:
            The ClickHouse function name, e.g. ``"toStartOfHour"``.
        """
        mapping = {
            "minute": "toStartOfMinute",
            "hour": "toStartOfHour",
            "day": "toStartOfDay",
            "week": "toMonday",
            "month": "toStartOfMonth",
            "year": "toStartOfYear",
        }
        return mapping.get(interval, "toStartOfHour")

    @staticmethod
    def parse_time_range(
        filters: List[Dict],
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Extract ``start_date`` and ``end_date`` from the frontend filter format.

        The frontend sends filters as a list of dicts.  This method looks for
        entries whose ``column_id`` is ``"created_at"`` or ``"start_time"``
        and extracts the time boundaries from the ``filter_config``.

        Supported ``filter_op`` values:
        - ``"greater_than"`` -- sets *start_date*.
        - ``"less_than"`` -- sets *end_date*.
        - ``"between"`` -- sets both from a two-element list.

        If no start date is found the default is *now - 7 days*.  If no end
        date is found the default is *now*.

        Args:
            filters: The list of filter dicts from the frontend request.

        Returns:
            A ``(start_date, end_date)`` tuple of ``datetime`` objects.
        """
        start_date: Optional[datetime] = None
        end_date: Optional[datetime] = None

        for f in filters:
            col_id = f.get("column_id") or f.get("columnId")
            config = f.get("filter_config") or f.get("filterConfig", {})
            if col_id not in ("created_at", "start_time"):
                continue

            op = config.get("filter_op") or config.get("filterOp")
            val = config.get("filter_value", config.get("filterValue"))

            if op == "greater_than" and val:
                start_date = _parse_dt(val)
            elif op == "less_than" and val:
                end_date = _parse_dt(val)
            elif op == "between" and isinstance(val, list) and len(val) == 2:
                start_date = _parse_dt(val[0])
                end_date = _parse_dt(val[1])

        if not start_date:
            start_date = datetime.utcnow() - timedelta(days=3650)
        if not end_date:
            end_date = datetime.utcnow()
        return start_date, end_date

    # ------------------------------------------------------------------
    # Time-series zero-fill helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_timestamp(ts: datetime, interval: str) -> datetime:
        """Normalize *ts* to the start of its time bucket.

        Strips timezone info and truncates to the start of the given
        interval bucket.
        """
        if ts.tzinfo:
            ts = ts.replace(tzinfo=None)

        interval = interval.lower()
        if interval == "minute":
            return ts.replace(second=0, microsecond=0)
        elif interval == "hour":
            return ts.replace(minute=0, second=0, microsecond=0)
        elif interval == "day":
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)
        elif interval == "week":
            days_since_monday = ts.weekday()
            week_start = ts - timedelta(days=days_since_monday)
            return week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        elif interval == "month":
            return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif interval == "year":
            return ts.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _generate_timestamp_range(
        start_date: datetime,
        end_date: datetime,
        interval: str,
    ) -> Generator[datetime, None, None]:
        """Yield normalized timestamps from *start_date* to *end_date*."""
        interval = interval.lower()
        current = BaseQueryBuilder._normalize_timestamp(start_date, interval)
        if end_date.tzinfo:
            end_date = end_date.replace(tzinfo=None)

        while current <= end_date:
            yield current
            if interval == "hour":
                current += timedelta(hours=1)
            elif interval == "day":
                current += timedelta(days=1)
            elif interval == "week":
                current += timedelta(weeks=1)
            elif interval == "month":
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
            elif interval == "year":
                current = current.replace(year=current.year + 1)
            else:
                current += timedelta(days=1)

    def format_time_series(
        self,
        rows: List[Tuple],
        columns: List[str],
        interval: str,
        start_date: datetime,
        end_date: datetime,
        value_keys: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Convert ClickHouse result rows to time-series format with zero-fill.

        The first column is assumed to be the time bucket.  Remaining columns
        become value fields.  Missing time buckets are filled with zeros.

        Args:
            rows: Raw rows from ClickHouse.
            columns: Column names corresponding to each row element.
            interval: Time interval used for bucket generation.
            start_date: Start of the time range.
            end_date: End of the time range.
            value_keys: If provided, only include these keys in each data
                point (besides ``"timestamp"``).  Defaults to all non-time
                columns.

        Returns:
            A list of dicts with ``"timestamp"`` and value fields, sorted
            chronologically with gaps zero-filled.
        """
        if value_keys is None:
            value_keys = columns[1:] if len(columns) > 1 else []

        # Build lookup of existing data keyed by normalized timestamp
        existing: Dict[datetime, Dict[str, Any]] = {}
        for row in rows:
            # Support both dict rows (from execute_ch_query) and tuple rows
            if isinstance(row, dict):
                ts = row.get(columns[0]) if columns else None
            else:
                ts = row[0]
            if isinstance(ts, str):
                ts = _parse_dt(ts)
            if ts is None:
                continue
            normalized = self._normalize_timestamp(ts, interval)
            point = {"timestamp": normalized.isoformat()}
            for i, col in enumerate(columns[1:], start=1):
                if isinstance(row, dict):
                    val = row.get(col, 0)
                else:
                    val = row[i] if i < len(row) else 0
                point[col] = round(val, 9) if isinstance(val, float) else (val or 0)
            existing[normalized] = point

        # Generate full timestamp range and fill gaps
        result: List[Dict[str, Any]] = []
        for ts in self._generate_timestamp_range(start_date, end_date, interval):
            if ts in existing:
                result.append(existing[ts])
            else:
                zero_point: Dict[str, Any] = {"timestamp": ts.isoformat()}
                for key in value_keys:
                    zero_point[key] = 0
                result.append(zero_point)

        return result
