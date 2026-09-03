"""
Base Query Builder for ClickHouse analytics queries.

Provides the abstract interface and shared utilities that all concrete
query builders inherit from.
"""

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from django.conf import settings

# ClickHouse zero-value for UUID columns. dictGetOrDefault on Nullable(UUID)
# dictionary columns may return this instead of NULL — see dashboard.py:1919.
NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _unix_microseconds(value: datetime) -> int:
    """Encode a UTC DateTime64(6) bound without driver precision loss."""

    utc_value = (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    )
    delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


@dataclass(frozen=True)
class BoundedDateTimeRange:
    """One finite base window plus conjunctive exclusions inside it."""

    start: datetime
    end: datetime
    exclusions: tuple[tuple[datetime, datetime], ...]
    empty: bool


def _parse_dt(val: Any) -> datetime | None:
    """Parse a datetime value from various formats.

    Handles ISO 8601 strings (with or without timezone), Python datetime
    objects, and the ``%Y-%m-%dT%H:%M:%S.%fZ`` format commonly sent by
    the frontend.

    Args:
        val: A datetime object or an ISO-format string.

    Returns:
        A timezone-naive ``datetime`` instance, or ``None`` if parsing fails.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return (
            val.astimezone(UTC).replace(tzinfo=None) if val.tzinfo is not None else val
        )
    if isinstance(val, str):
        # Try standard ISO format first (handles 'Z' and '+00:00')
        cleaned = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
            return (
                dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt
            )
        except (ValueError, AttributeError):
            pass
        # Fallback: try strptime with common formats
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
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Either a single project_id (per-project mode) OR a list of
        # project_ids (org-scoped mode where the caller resolved the org's
        # projects in Python and passes them down). Builders use
        # `project_where()` which switches its emitted SQL based on which
        # mode is active.
        self.project_id = project_id
        self.project_ids: list[str] | None = (
            [str(p) for p in project_ids] if project_ids else None
        )
        self.params: dict[str, Any] = {}
        if self.project_ids:
            # ClickHouse parameterized IN expects a tuple
            self.params["project_ids"] = tuple(self.project_ids)
        else:
            self.params["project_id"] = project_id

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def build(self) -> tuple[str, dict[str, Any]]:
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
        # CH25 close-out: the v2 spans table uses `is_deleted` (UInt8 column
        # from schema 002_spans_v2.sql) rather than the PeerDB-managed
        # `_peerdb_is_deleted` of the legacy CDC mirror. All query builders
        # that inherit from BaseQueryBuilder target the v2 spans table.
        return (
            f"WHERE {self.project_filter_sql(table_alias)} AND {prefix}is_deleted = 0"
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
            interval: One of ``"minute"``, ``"hour"``, ``"day"``, ``"week"``,
                ``"month"``, ``"year"``.

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
    def is_datetime_complement_filter(item: dict[str, Any]) -> bool:
        """Return whether a time leaf must survive base-window replacement."""

        column_id = item.get("column_id") or item.get("columnId")
        config = item.get("filter_config") or item.get("filterConfig") or {}
        operator = config.get("filter_op") or config.get("filterOp")
        return column_id in {"created_at", "start_time"} and operator in {
            "not_equals",
            "not_between",
            "is_null",
        }

    @staticmethod
    def parse_time_range(
        filters: list[dict],
        *,
        strict: bool = False,
    ) -> tuple[datetime | None, datetime | None]:
        """Extract one exact half-open request window from datetime filters.

        The frontend sends filters as a list of dicts.  This method looks for
        entries whose ``column_id`` is ``"created_at"`` or ``"start_time"``
        and extracts the time boundaries from the ``filter_config``.

        Contiguous operators are represented exactly against the direct-write
        ``DateTime64(6)`` column while keeping every downstream read in the
        half-open form ``start_time >= start_date AND start_time < end_date``:

        - ``equals value`` becomes ``[value, value + 1 microsecond)``.
        - ``greater_than value`` becomes ``[value + 1 microsecond, ...)``.
        - ``greater_than_or_equal value`` becomes ``[value, ...)``.
        - ``less_than value`` becomes ``[..., value)``.
        - ``less_than_or_equal value`` becomes ``[..., value + 1 microsecond)``.
        - ``between [start, end]`` is the list time-range contract and remains
          the half-open interval ``[start, end)``.

        Multiple positive time filters are intersected. ``is_not_null`` is a
        no-op for the non-null ``spans.start_time`` column. Missing bounds
        retain the historical finite defaults: 30 days ago for the lower bound
        and request-time ``now`` for the upper bound. In strict mode the
        complement operators are retained inside that finite base window:
        ``not_equals`` excludes one DateTime64(6) point, ``not_between``
        excludes the half-open range ``[start, end)``, and ``is_null`` is an
        exact empty set because the physical time column is non-null.

        This tuple API returns an equal boundary for an exact-empty request.
        Bounded readers recognize that shape before issuing a ClickHouse query;
        query builders also add ``0 = 1`` as a fail-closed SQL guard.

        If no start date is found the default is *now - 30 days*. If no end
        date is found the default is *now*.

        Args:
            filters: The list of filter dicts from the frontend request.
            strict: Validate malformed values and retain complement semantics
                instead of preserving the historical best-effort behaviour
                used by older non-list consumers.

        Returns:
            A ``(start_date, end_date)`` tuple of ``datetime`` objects.
        """
        analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(
            filters,
            strict=strict,
        )
        return analyzed.start, analyzed.end

    @staticmethod
    def analyze_bounded_datetime_filters(
        filters: list[dict],
        *,
        strict: bool = True,
    ) -> BoundedDateTimeRange:
        """Normalize bounded datetime leaves without widening their meaning.

        Positive leaves form one prunable base interval. Complement leaves are
        conjunctive exclusions evaluated only inside that interval. Malformed
        values remain request errors; a logically contradictory but otherwise
        valid filter is an exact empty result, not a validation error.
        """

        start_date: datetime | None = None
        end_date: datetime | None = None
        precision = timedelta(microseconds=1)
        exclusions: list[tuple[datetime, datetime]] = []
        force_empty = False
        has_explicit_lower = False
        has_explicit_upper = False

        def parsed_bound(value: Any, *, operator: str) -> datetime | None:
            parsed = _parse_dt(value)
            if parsed is None and strict:
                raise ValueError(
                    f"Datetime filter operator {operator!r} requires a valid "
                    "ISO-8601 timestamp."
                )
            return parsed

        def exclusive_after(value: datetime, *, operator: str) -> datetime:
            try:
                return value + precision
            except OverflowError as exc:
                raise ValueError(
                    f"Datetime filter operator {operator!r} exceeds the "
                    "DateTime64(6) range."
                ) from exc

        def intersect_lower(value: datetime) -> None:
            nonlocal start_date
            start_date = value if start_date is None else max(start_date, value)

        def intersect_upper(value: datetime) -> None:
            nonlocal end_date
            end_date = value if end_date is None else min(end_date, value)

        for f in filters:
            col_id = f.get("column_id") or f.get("columnId")
            config = f.get("filter_config") or f.get("filterConfig") or {}
            if col_id not in ("created_at", "start_time"):
                continue

            op = config.get("filter_op") or config.get("filterOp")
            val = config.get("filter_value", config.get("filterValue"))
            filter_type = config.get("filter_type") or config.get("filterType")
            if filter_type and str(filter_type).lower() not in {
                "date",
                "datetime",
                "timestamp",
            }:
                if strict:
                    raise ValueError(
                        f"{col_id!r} must use the datetime filter type on "
                        "bounded analytics endpoints."
                    )
                continue

            if op == "is_null":
                if strict:
                    force_empty = True
                continue
            if op == "is_not_null":
                continue

            if op == "not_equals":
                if not strict:
                    continue
                parsed = parsed_bound(val, operator=op)
                if parsed is None:
                    continue
                exclusions.append((parsed, exclusive_after(parsed, operator=op)))
                continue

            if op == "not_between":
                if not strict:
                    continue
                if not isinstance(val, (list, tuple)) or len(val) != 2:
                    if strict:
                        raise ValueError(
                            "Datetime filter operator 'not_between' requires two "
                            "ISO-8601 timestamps."
                        )
                    continue
                lower = parsed_bound(val[0], operator=op)
                upper = parsed_bound(val[1], operator=op)
                if lower is None or upper is None:
                    continue
                if lower > upper:
                    if strict:
                        raise ValueError(
                            "Datetime filter operator 'not_between' requires its "
                            "start timestamp to be before or equal to its end."
                        )
                    continue
                if lower < upper:
                    exclusions.append((lower, upper))
                continue

            if op in {
                "equals",
                "greater_than",
                "greater_than_or_equal",
                "less_than",
                "less_than_or_equal",
            }:
                parsed = parsed_bound(val, operator=str(op))
                if parsed is None:
                    continue
                if op == "equals":
                    intersect_lower(parsed)
                    intersect_upper(exclusive_after(parsed, operator=op))
                    has_explicit_lower = True
                    has_explicit_upper = True
                elif op == "greater_than":
                    intersect_lower(exclusive_after(parsed, operator=op))
                    has_explicit_lower = True
                elif op == "greater_than_or_equal":
                    intersect_lower(parsed)
                    has_explicit_lower = True
                elif op == "less_than":
                    intersect_upper(parsed)
                    has_explicit_upper = True
                else:
                    intersect_upper(exclusive_after(parsed, operator=op))
                    has_explicit_upper = True
                continue

            if op == "between":
                if not isinstance(val, (list, tuple)) or len(val) != 2:
                    if strict:
                        raise ValueError(
                            "Datetime filter operator 'between' requires two "
                            "ISO-8601 timestamps."
                        )
                    continue
                lower = parsed_bound(val[0], operator=op)
                upper = parsed_bound(val[1], operator=op)
                if lower is None or upper is None:
                    continue
                if lower > upper:
                    if strict:
                        raise ValueError(
                            "Datetime filter operator 'between' requires its "
                            "start timestamp to be before or equal to its end."
                        )
                    continue
                intersect_lower(lower)
                intersect_upper(upper)
                has_explicit_lower = True
                has_explicit_upper = True
                continue

            if strict:
                raise ValueError(f"Unsupported datetime filter operator {op!r}.")

        # The default bounded lookback applies when no time filter is supplied.
        # An earlier ten-year window bypassed partition pruning
        # and forced full-history scans for every dashboard-default page-load
        # (regression caught in kartik perf sweep; 100ms+ p95 just from the
        # unbounded window). The reviewed default matches the dashboard view;
        # users wanting older data set an explicit filter, which uses the path
        # above and gets accurate pruning anyway.
        request_now = datetime.utcnow()
        if not start_date:
            start_date = request_now - timedelta(
                days=settings.ANALYTICS_DEFAULT_LOOKBACK_DAYS
            )
        if not end_date:
            end_date = request_now

        if start_date >= end_date:
            return BoundedDateTimeRange(
                start=start_date,
                end=start_date,
                exclusions=(),
                empty=True,
            )

        # Merge raw exclusions for SQL. Do not clamp their bound parameters to
        # a relative default window: builders freeze that window independently,
        # and even a few microseconds of request-time drift at the lower edge
        # would otherwise re-include rows that the customer excluded.
        merged_exclusions: list[tuple[datetime, datetime]] = []
        for lower, upper in sorted(exclusions):
            if merged_exclusions and lower <= merged_exclusions[-1][1]:
                merged_exclusions[-1] = (
                    merged_exclusions[-1][0],
                    max(merged_exclusions[-1][1], upper),
                )
            else:
                merged_exclusions.append((lower, upper))

        # Clamp only for contradiction proof. Coverage is asserted only when
        # both base edges came from explicit request values; relative defaults
        # deliberately remain queryable rather than guessing across clocks.
        clamped = sorted(
            (
                max(start_date, lower),
                min(end_date, upper),
            )
            for lower, upper in exclusions
            if upper > start_date and lower < end_date
        )
        merged: list[tuple[datetime, datetime]] = []
        for lower, upper in clamped:
            if lower >= upper:
                continue
            if merged and lower <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
            else:
                merged.append((lower, upper))

        exclusion_covers_base = bool(
            has_explicit_lower
            and has_explicit_upper
            and merged
            and merged[0][0] <= start_date
            and merged[-1][1] >= end_date
            and all(
                current[1] >= following[0]
                for current, following in zip(merged, merged[1:], strict=False)
            )
        )
        if force_empty or exclusion_covers_base:
            return BoundedDateTimeRange(
                start=start_date,
                end=start_date,
                exclusions=tuple(merged_exclusions),
                empty=True,
            )
        return BoundedDateTimeRange(
            start=start_date,
            end=end_date,
            exclusions=tuple(merged_exclusions),
            empty=False,
        )

    @staticmethod
    def bounded_datetime_exclusion_sql(
        filters: list[dict],
        *,
        column: str = "start_time",
        param_prefix: str = "bounded_datetime",
    ) -> tuple[str, dict[str, Any]]:
        """Compile strict datetime complements inside the finite base window.

        Positive operators deliberately emit no residual fragment, preserving
        their existing SQL byte-for-byte. Every value remains a driver-bound
        parameter; ``column`` and ``param_prefix`` are internal constants at
        call sites, never request-controlled identifiers.
        """

        if not column.replace("_", "").isalnum():
            raise ValueError("datetime predicate column must be an identifier")
        if not param_prefix.replace("_", "").isalnum():
            raise ValueError("datetime predicate prefix must be an identifier")

        analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(
            filters,
            strict=True,
        )
        if analyzed.empty:
            return "0 = 1", {}

        predicates: list[str] = []
        params: dict[str, Any] = {}
        for index, (lower, upper) in enumerate(analyzed.exclusions):
            lower_param = f"{param_prefix}_{index}_start"
            upper_param = f"{param_prefix}_{index}_end"
            # clickhouse-driver renders a bound ``datetime`` at whole-second
            # precision.  Complements such as ``not_equals`` deliberately use
            # one-microsecond ranges, so bind epoch microseconds explicitly.
            params[lower_param] = _unix_microseconds(lower)
            params[upper_param] = _unix_microseconds(upper)
            predicates.append(
                f"({column} < fromUnixTimestamp64Micro(%({lower_param})s) OR "
                f"{column} >= fromUnixTimestamp64Micro(%({upper_param})s))"
            )
        return " AND ".join(predicates), params

    @staticmethod
    def window_days_covering(filters: list[dict]) -> int:
        """Look-back days (from ``now()``) that cover the requested time window.

        Eval-config discovery bounds its scan to ``created_at >= now() - N
        days``. To surface every config with data anywhere in the *requested*
        range — not a fixed 30 days — ``N`` must reach back to the window start.
        Returns the ceil day-count from the parsed start to now (min 1); with no
        explicit time filter the parsed default is ``now - 30d``, so the default
        view stays ~30. Pair with ``candidate_config_ids`` so the scan stays
        bounded by the eval table's leading sort key regardless of depth.
        """
        start_date, _ = BaseQueryBuilder.parse_time_range(filters)
        delta = datetime.utcnow() - start_date
        return max(1, delta.days + (1 if (delta.seconds or delta.microseconds) else 0))

    # ------------------------------------------------------------------
    # Time-series zero-fill helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_timestamp(ts: date | datetime, interval: str) -> datetime:
        """Normalize *ts* to the start of its time bucket.

        Strips timezone info and truncates to the start of the given
        interval bucket.
        """
        if isinstance(ts, date) and not isinstance(ts, datetime):
            ts = datetime(ts.year, ts.month, ts.day)
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
            if interval == "minute":
                current += timedelta(minutes=1)
            elif interval == "hour":
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
        rows: list[tuple],
        columns: list[str],
        interval: str,
        start_date: datetime,
        end_date: datetime,
        value_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
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
        existing: dict[datetime, dict[str, Any]] = {}
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
        result: list[dict[str, Any]] = []
        for ts in self._generate_timestamp_range(start_date, end_date, interval):
            if ts in existing:
                result.append(existing[ts])
            else:
                zero_point: dict[str, Any] = {"timestamp": ts.isoformat()}
                for key in value_keys:
                    zero_point[key] = 0
                result.append(zero_point)

        return result
