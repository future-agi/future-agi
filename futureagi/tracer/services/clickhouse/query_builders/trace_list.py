"""
Trace List Query Builder for ClickHouse.

Replaces the ``list_traces()`` method in ``tracer.views.trace`` with a
two-phase ClickHouse query strategy:

Phase 1 -- Paginated trace IDs + root span data from the denormalized
``spans`` table (``WHERE parent_span_id IS NULL``).

Phase 2 -- Eval scores from ``tracer_eval_logger FINAL`` for those
trace IDs, grouped by ``(trace_id, custom_eval_config_id)``.

The two result sets are merged in Python.
"""

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from tracer.services.clickhouse.eval_logger_table import (
    eval_logger_live_state_columns,
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.eval_status import (
    non_terminal_eval_marker,
)
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    normalize_filter_op,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    LatestFilterPredicate,
    partition_trace_filter_plans,
    supports_trace_filters,
    targets_trace_filter_domain,
)
from tracer.utils.filter_operators import normalize_span_attribute_filter_type

# On the v2 schema (PARTITION BY toDate(start_time), PK on toStartOfHour(
# start_time)) start_time prunes partitions and the PK; created_at prunes
# nothing and scans the whole project.
TIME_FILTER_COLUMN = "start_time"  # Options: "created_at" | "start_time"

_INDEXED_TRACE_ANY_SPAN_ANCHOR_COLUMNS = frozenset(
    {
        "id",
        "trace_id",
        "parent_span_id",
        "trace_session_id",
        "end_user_id",
        "custom_eval_config_id",
        "model",
        "provider",
        "status",
        "eval_status",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "input_length",
        "output_length",
    }
)

# A latest-state trace classifier touches every physical span for each
# candidate trace.  On the largest production tenant, one 25-trace any-span
# classifier crossed the 512 MiB per-query safety ceiling even though its
# identity-only projection completed quickly.  Twenty is the largest batch
# already exercised below that ceiling by the bounded graph path; seed reads
# remain independently capped and may still acquire 200 identities at once.
_BULK_ANY_SPAN_CLASSIFY_BATCH_SIZE = 20

# Historical task/eval population proofs classify membership only and replay
# witnesses separately.  The 100-identity envelope is production-qualified for
# that internal bulk shape and keeps 10k+sentinel proofs within 128 queries.
_BULK_IDENTITY_CLASSIFY_BATCH_SIZE = 100

# Exact asynchronous graphs only return membership identities, never hydrated
# span payloads.  Their global child classifier was production-qualified at a
# five-thousand-trace finite batch; the reader halves a resource-limited batch
# recursively, so this is an initial query size rather than a result ceiling.
_EXACT_GRAPH_IDENTITY_CLASSIFY_BATCH_SIZE = 5_000
# Organization classifiers must carry a composite (project, trace) identity.
# Keep their separately rendered payload at the previously safe 1k envelope;
# a 5k composite tuple alone exceeds ClickHouse's default parser limit.
_EXACT_GRAPH_ORG_IDENTITY_CLASSIFY_BATCH_SIZE = 1_000

# Normal trace pages classify identities only and hydrate at most the final
# public page in a separate bounded statement.  Production replay showed that
# 100 candidates can cross the interactive statement deadline on the largest
# tenant; 80 keeps the same exact membership/order semantics while leaving the
# page hydration query enough wall time.  Bulk population proofs retain their
# independently qualified 100-identity envelope above.
_NORMAL_LIST_IDENTITY_CLASSIFY_BATCH_SIZE = 80

# Structured span attributes are decoded from ``span_attributes_raw`` during
# latest-state replay.  They can also be combined with the native typed Maps,
# so even the normal 80-trace identity batch can make ClickHouse materialize too
# many ColumnMap/JSON vectors at once.  Keep those classifiers on the smaller
# production-qualified envelope and reduce their input block fourfold. This
# changes only physical query chunking; every candidate still goes through the
# same exact latest-state predicate before it can enter a public page.
# The ten-candidate envelope was qualified on the largest production tenant;
# larger structured batches can exceed the bounded statement profile. Ten
# keeps identical predicates and order and changes only physical chunking.
_STRUCTURED_ANY_SPAN_CLASSIFY_BATCH_SIZE = 10
_STRUCTURED_CLASSIFY_MAX_BLOCK_SIZE = 2_048
_STRUCTURED_CLASSIFY_MAX_COLUMN_BYTES = 1 * 1024 * 1024

# A long-window list gets a small, partitioned sparse-value proof before it
# enters the ordered-root fallback. Sixty-four is one global exhaustiveness
# sentinel across four adjacent time strata. The four probes share one 900 ms
# allowance and each statement is capped at 192 MiB. This accommodates the
# largest observed production granule without approaching the normal 512 MiB
# statement envelope. The selector discards the entire probe set on any
# sentinel or resource failure, so none of these limits can change membership.
_LONG_WINDOW_ANCHOR_SENTINEL = 64
_LONG_WINDOW_ANCHOR_TIMEOUT_MS = 900
_LONG_WINDOW_ANCHOR_STRATA = 4
_LONG_WINDOW_ANCHOR_MAX_BYTES_TO_READ = 192 * 1024 * 1024
# Candidate witnesses are finite (at most 512 exact trace identities), so try
# their complete request window in one statement first. The selector splits a
# resource-limited statement into adjacent half-open children and accepts a
# negative only after every child succeeds. This avoids paying eight scans for
# every root batch while preserving chronological fallback for heavy Map/JSON.
_LONG_WINDOW_CANDIDATE_WITNESS_STRATA = 1
_LONG_WINDOW_ORDERED_ROOT_INITIAL_SLICE = timedelta(hours=1)
# Long exact text values (recording URLs, UUID-like IDs, and full transcript
# messages) are normally highly selective but expensive to rediscover by
# replaying every span in tiny root batches. This is only a physical-plan
# heuristic: the finite candidate witness remains a necessary prefilter and
# the ordinary latest-state classifier still decides exact membership.
_SELECTIVE_EXACT_TEXT_MIN_LENGTH = 32
_USER_DETAIL_FILTER_TIMEOUT_MS = 9_500
_UNINDEXED_POSITIVE_MICRO_SEED_WIDTH = timedelta(minutes=5)
_UNINDEXED_POSITIVE_MICRO_SEED_STRATA = 4
_CANONICAL_TRACE_ID_SEED_PREDICATE = re.compile(
    r"trace_id (?:=|IN) %\(latest_filter_param_\d+\)s"
)
# These public voice values parse provider-specific ``raw_log`` JSON.  Running
# that expression while discovering roots makes a sparse 12-month filter scan
# every root in a wide time slice before its finite LIMIT can help.  Acquire a
# cheap root-ordered identity superset instead and keep the existing finite
# latest-state classifier authoritative for membership.  ``call_type`` already
# used this lane; ``ended_reason`` has the same provider-normalization shape.
_CLASSIFIER_ONLY_ROOT_SEED_METRICS = frozenset({"call_type", "ended_reason"})


def _unix_microseconds(value: datetime) -> int:
    """Encode DateTime64(6) without driver tuple-datetime precision loss."""

    utc_value = (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    )
    delta = utc_value - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


class TraceListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated trace list view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of traces per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        eval_config_ids: List of ``CustomEvalConfig`` UUID strings to
            fetch eval scores for.
    """

    TABLE = "spans"
    EVAL_TABLE = "tracer_eval_logger"
    # Eval storage is selected independently from the spans generation. A
    # CH25 span query can legitimately read the legacy-named authoritative
    # eval table on the same connection.
    _EVAL_LOGGER_SOURCE = staticmethod(eval_logger_source)
    # Filter compiler class; the v2 list builder overrides this to the v2
    # builder so it reads the v2 dimension tables (end_users, etc.).
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilder

    # Mapping from sort column names the frontend sends to actual
    # ClickHouse column names on the root span.
    SORT_FIELD_MAP: dict[str, str] = {
        "created_at": "start_time",
        "start_time": "start_time",
        "latency": "latency_ms",
        "latency_ms": "latency_ms",
        "cost": "cost",
        "total_tokens": "total_tokens",
        "name": "trace_name",
        "trace_name": "trace_name",
        "status": "status",
    }

    # All available light columns for configurable column selection.
    AVAILABLE_COLUMNS: list[str] = [
        "trace_id",
        "trace_name",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "trace_session_id",
        "project_id",
    ]

    def __init__(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        project_version_id: str | None = None,
        search: str | None = None,
        columns: list[str] | None = None,
        annotation_label_ids: list[str] | None = None,
        annotation_label_ids_by_project: dict[str, list[str]] | None = None,
        eval_filter_metadata: dict[str, Any] | None = None,
        bounded_internal_scan: bool = False,
        bounded_identity_only: bool = False,
        bounded_membership_filters: list[dict] | None = None,
        bounded_bulk_scan: bool = False,
        bounded_include_filter_witnesses: bool = True,
        bounded_population_proof: bool = False,
        bounded_global_span_witnesses: bool = False,
        bounded_sampling_salt: str | None = None,
        bounded_sampling_rate: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self._eval_config_ids_known = eval_config_ids is not None
        self.eval_config_ids = eval_config_ids or []
        self.project_version_id = project_version_id
        self.search = search.strip() if search else None
        self.columns = columns
        self._annotation_label_set_known = annotation_label_ids is not None
        self.annotation_label_ids = annotation_label_ids or []
        self.annotation_label_ids_by_project = (
            {
                str(project_key): list(
                    dict.fromkeys(str(label_id) for label_id in label_ids if label_id)
                )
                for project_key, label_ids in annotation_label_ids_by_project.items()
            }
            if annotation_label_ids_by_project is not None
            else None
        )
        self.eval_filter_metadata = eval_filter_metadata
        self._bounded_internal_scan = bool(bounded_internal_scan)
        self._bounded_identity_only = bool(bounded_identity_only)
        # Graph sampling may narrow root seeding/order to one temporal
        # stratum. Trace membership must still inspect latest state across the
        # caller's complete request window: a root and its matching children
        # can legitimately fall on opposite sides of a stratum boundary.
        self._bounded_membership_filters = (
            list(bounded_membership_filters)
            if bounded_membership_filters is not None
            else None
        )
        self._bounded_bulk_scan = bool(bounded_bulk_scan)
        if self._bounded_bulk_scan and not self._bounded_identity_only:
            raise ValueError("bounded_bulk_scan requires bounded_identity_only")
        self._bounded_include_filter_witnesses = bool(bounded_include_filter_witnesses)
        if (
            not self._bounded_include_filter_witnesses
            and not self._bounded_identity_only
        ):
            raise ValueError(
                "membership-only classification requires bounded_identity_only"
            )
        self._bounded_population_proof = bool(bounded_population_proof)
        if self._bounded_population_proof and not (
            self._bounded_internal_scan
            and self._bounded_identity_only
            and self._bounded_bulk_scan
            and project_id is not None
            and project_ids is None
        ):
            raise ValueError(
                "bounded_population_proof requires one-project internal "
                "identity-only bulk classification"
            )
        self._bounded_global_span_witnesses = bool(bounded_global_span_witnesses)
        if self._bounded_global_span_witnesses and not (
            self._bounded_internal_scan
            and self._bounded_identity_only
            and self._bounded_bulk_scan
            and not self._bounded_include_filter_witnesses
        ):
            raise ValueError(
                "bounded_global_span_witnesses requires internal membership-only "
                "bulk classification"
            )
        if (bounded_sampling_salt is None) != (bounded_sampling_rate is None):
            raise ValueError(
                "bounded_sampling_salt and bounded_sampling_rate must be paired"
            )
        if bounded_sampling_rate is not None and not (
            0 <= float(bounded_sampling_rate) <= 100
        ):
            raise ValueError("bounded_sampling_rate must be between 0 and 100")
        self._bounded_sampling_salt = bounded_sampling_salt
        self._bounded_sampling_rate = bounded_sampling_rate
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None
        # The default range is derived from ``utcnow``. Pin it once so the
        # bounded selector and every seed/classifier query use identical
        # half-open boundaries instead of drifting forward by microseconds.
        self._bounded_request_window = BaseQueryBuilder.parse_time_range(
            self.filters, strict=True
        )
        if self._bounded_membership_filters is not None:
            membership_window = BaseQueryBuilder.parse_time_range(
                self._bounded_membership_filters,
                strict=True,
            )
            seed_start, seed_end = self._bounded_request_window
            membership_start, membership_end = membership_window
            if not (membership_start <= seed_start < seed_end <= membership_end):
                raise ValueError(
                    "bounded membership window must contain the seed window"
                )
            if self._bounded_membership_shape(
                self.filters
            ) != self._bounded_membership_shape(self._bounded_membership_filters):
                raise ValueError(
                    "bounded membership filters may differ only by positive time bounds"
                )

    def parse_time_range(
        self, filters: list[dict]
    ) -> tuple[datetime | None, datetime | None]:
        if filters is self.filters or filters == self.filters:
            return self._bounded_request_window
        return BaseQueryBuilder.parse_time_range(filters, strict=True)

    def supports_bounded_filter_scan(self) -> bool:
        """Whether the latest-state bounded reader can represent this request."""

        bounded_filters = self._bounded_filters()
        try:
            self._partition_trace_filter_plans(bounded_filters)
        except (TypeError, ValueError):
            return False
        return (
            supports_trace_filters(bounded_filters)
            and self.bounded_filter_degraded_error_code() is None
        )

    def _bounded_filters(self) -> list[dict[str, Any]]:
        """Represent free-text search as a literal latest-root predicate.

        The legacy ``ILIKE`` query scanned the full requested window and also
        interpreted user ``%``/``_`` characters as wildcards.  The bounded
        selector instead treats the search value as a literal, case-insensitive
        ``trace_name`` substring.  Reusing the normal root-filter compiler keeps
        raw seed pruning and latest-state classification identical to an
        explicit trace-name filter without mutating the public filter payload.
        """

        filters = list(self.filters)
        if self.search:
            filters.append(
                {
                    "column_id": "trace_name",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": self.search,
                    },
                }
            )
        return filters

    def _partition_trace_filter_plans(
        self,
        filters: list[dict[str, Any]],
    ) -> tuple[list[LatestFilterPredicate], list[dict[str, Any]]]:
        """Compile predicates with this list surface's filter semantics.

        Public voice system metrics use one response-normalized contract across
        voice lists and trace graphs. Raw provider values remain explicit
        SPAN_ATTRIBUTE filters such as ``call.status``.
        """

        return partition_trace_filter_plans(
            filters,
            filter_builder_cls=self._FILTER_BUILDER_CLS,
        )

    def _bounded_match_filters(self) -> list[dict[str, Any]]:
        """Return the full-window predicates used for latest-state membership."""

        filters = (
            list(self._bounded_membership_filters)
            if self._bounded_membership_filters is not None
            else list(self.filters)
        )
        if self.search:
            filters.append(
                {
                    "column_id": "trace_name",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": self.search,
                    },
                }
            )
        return filters

    @staticmethod
    def _bounded_membership_shape(filters: list[dict]) -> list[dict]:
        """Remove positive time bounds while retaining every membership leaf."""

        return [
            item
            for item in filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
            or BaseQueryBuilder.is_datetime_complement_filter(item)
        ]

    def _active_non_time_filters(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.filters
            if isinstance(item, dict)
            and (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]

    def _uses_full_window_time_only_bulk_identity_scan(self) -> bool:
        """Use one finite ordered seed for a time-only bulk continuation.

        Filter-mode queue adds freeze an all-history window when the request
        omits an explicit date. Walking that empty 1971-to-now tail in two-day
        slices can outlive the client's bounded continuation budget after the
        last real row has already been committed. This narrow mode still reads
        only one project's root identities with an ordered keyset and a finite
        LIMIT. If the wide seed exceeds its read budget, the shared selector
        halves it before publishing anything, preserving the bounded fallback.
        """

        request_start, request_end = self._bounded_request_window
        return bool(
            self._bounded_internal_scan
            and self._bounded_identity_only
            and self._bounded_bulk_scan
            and not self._bounded_population_proof
            and not self._bounded_global_span_witnesses
            and self.project_id is not None
            and self.project_ids is None
            and not self.sort_params
            and not self.search
            and not self._active_non_time_filters()
            and self._bounded_sampling_rate is None
            and request_end - request_start >= timedelta(minutes=5)
        )

    def should_retry_filter_wide_read_budget(self) -> bool:
        """Allow safe halving only for the opt-in full-window bulk seed.

        The ordinary selector fails closed after any required ClickHouse read
        exceeds its budget.  This one mode deliberately starts with the whole
        frozen request window, so it can retry that unpublished identity-only
        seed on narrower adjacent slices without changing membership or order.
        """

        return self._uses_full_window_time_only_bulk_identity_scan()

    def requires_cursor_for_long_filtered_read(self) -> bool:
        """Whether a long filtered list must retain a signed scan checkpoint."""

        request_start, request_end = self._bounded_request_window
        return bool(
            (self._active_non_time_filters() or self.search)
            and request_end - request_start > timedelta(hours=1)
        )

    def _positive_exact_end_user_seed_filter(self) -> dict[str, Any] | None:
        """Return the sole exact user alias filter eligible for root seeding.

        User-detail trace pages add one structural ``user``/``user_id`` or
        ``end_user_id`` equality while retaining a separate time predicate.
        An explicitly raw ``SPAN_ATTRIBUTE`` with the same key must keep its
        ordinary attribute semantics. Other relational shapes stay on the
        candidate classifier path: negation, substring matching, and
        combinations can be common enough that they are not safe selective
        seeds.
        """

        active_filters = self._active_non_time_filters()
        if self.search or len(active_filters) != 1:
            return None
        item = active_filters[0]
        key = item.get("column_id") or item.get("columnId")
        if key not in {"end_user_id", "user", "user_id"}:
            return None
        config = item.get("filter_config") or item.get("filterConfig") or {}
        if not isinstance(config, dict):
            return None
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        if col_type not in {"", "NORMAL", "SYSTEM_METRIC", "TRACE_END_USER"}:
            return None
        filter_type = str(
            config.get("filter_type") or config.get("filterType") or ""
        ).lower()
        if filter_type not in {"", "text"}:
            return None
        operation = normalize_filter_op(
            config.get("filter_op") or config.get("filterOp")
        )
        value = config.get("filter_value", config.get("filterValue"))
        if operation == "equals":
            if not isinstance(value, str) or not value:
                return None
        elif operation == "in":
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(member, str) or not member for member in value)
            ):
                return None
        else:
            return None
        return item

    def _positive_relational_seed_filter(self) -> dict[str, Any] | None:
        """Return one positive relation that is necessary for every match.

        A project-scoped eval value, a positive annotator selection, and the
        existing ``has_eval=true`` / ``has_annotation=true`` relations all
        have a physical positive witness.  Any one of them can therefore
        narrow root discovery even when the request has additional ``AND``
        predicates: the ordinary finite latest-state classifier still repeats
        *every* public filter before publishing a row.  Prefer annotator and
        eval-value witnesses over the broader boolean existence relations.

        Pure negative-existence predicates remain classifier-only because
        absence has no positive row with which to seed candidates.  Global
        annotator ``not_equals``/``not_in`` is different: its public contract
        requires at least one annotation and excludes traces annotated by the
        selected users.  The complete Score relation is therefore a safe,
        usually selective candidate seed; the finite classifier still repeats
        the exclusion before publication.  Eval value ``not_in``/
        ``not_equals`` likewise selects a positive eval row whose value
        satisfies the negated comparison.

        Voice calls delegate to this trace builder with one private conversation
        root invariant.  That marker is structural, not a public filter, so it
        is ignored when deciding whether the relation is the sole user leaf.
        """

        if self.search or self.project_id is None or self.project_ids is not None:
            return None
        active_filters = [
            item
            for item in self._active_non_time_filters()
            if not item.get("_eval_task_trace_root")
        ]
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        positive_eval_operations = {
            "equals",
            "not_equals",
            "in",
            "not_in",
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
            "between",
            "not_between",
            "contains",
            "not_contains",
            "starts_with",
            "ends_with",
            "is_not_null",
        }
        for index, item in enumerate(active_filters):
            key = item.get("column_id") or item.get("columnId")
            config = item.get("filter_config") or item.get("filterConfig") or {}
            if not key or not isinstance(config, dict):
                continue
            col_type = str(
                config.get("col_type") or config.get("colType") or ""
            ).upper()
            filter_type = str(
                config.get("filter_type") or config.get("filterType") or ""
            ).lower()
            operation = normalize_filter_op(
                config.get("filter_op") or config.get("filterOp")
            )
            value = config.get("filter_value", config.get("filterValue"))

            if key == "annotator":
                # ``annotator`` is a Score relation independent of col_type,
                # except that an explicit SPAN_ATTRIBUTE means the caller
                # intentionally selected a raw customer attribute with the
                # same name. ``is_not_null`` also has a positive Score-row
                # witness, so it can seed candidates without scanning every
                # trace in the requested window. ``is_null`` remains
                # classifier-only because absence has no row to seed from.
                if col_type not in {"", "NORMAL", "SYSTEM_METRIC", "ANNOTATION"}:
                    continue
                if filter_type not in {"", "text", "annotator"}:
                    continue
                values = value if isinstance(value, (list, tuple)) else [value]
                if operation == "is_not_null":
                    candidates.append((0, index, item))
                    continue
                if (
                    operation in {"equals", "in", "not_equals", "not_in"}
                    and values
                    and all(isinstance(member, str) and member for member in values)
                ):
                    candidates.append((0, index, item))
                continue

            if col_type == "EVAL_METRIC" and key not in {
                "annotator",
                "has_annotation",
                "has_eval",
                "my_annotations",
            }:
                # Every supported value comparison except ``is_null`` is
                # compiled as trace_id IN (matching latest live eval rows).
                # Config/template resolution is project-scoped by the filter
                # compiler used below.
                if isinstance(key, str) and operation in positive_eval_operations:
                    candidates.append((1, index, item))
                continue

            if key not in {"has_eval", "has_annotation"}:
                continue
            if key == "has_eval" and not self._eval_config_ids_known:
                # The eval table has no project id. Candidate-first membership
                # is safe only when the endpoint supplied its authoritative
                # active project config set.
                continue
            if (
                key == "has_annotation"
                and self._annotation_label_set_known
                and not self.annotation_label_ids
            ):
                # Completeness across a known empty label set is vacuously
                # true; there is no positive Score witness for root seeding.
                continue
            wants_relation = value is True or (
                isinstance(value, str) and value.strip().lower() == "true"
            )
            if operation == "equals" and wants_relation:
                candidates.append((2, index, item))

        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate[:2])[2]

    def _positive_relational_seed(self) -> tuple[str, dict[str, Any]]:
        """Compile the exact project-scoped relation used by root discovery.

        The same filter compiler remains in the finite classifier below.  The
        seed therefore only reduces candidate acquisition; it never publishes
        a relation match by itself.  Relation time scoping is deliberately off:
        evals and annotations may be written long after their visible root.
        ``strict_trace_project_correlation`` binds eval membership to configs
        owned by this project, matching the exact graph contract and preventing
        a colliding trace id in another project from entering the seed.
        """

        filter_item = self._positive_relational_seed_filter()
        if filter_item is None:
            return "", {}
        filter_builder = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            score_date_scope=False,
            span_date_scope=False,
            strict_trace_project_correlation=True,
            trace_project_eval_config_ids=(
                self.eval_config_ids if self._eval_config_ids_known else None
            ),
            annotation_label_set_known=self._annotation_label_set_known,
            eval_filter_metadata=self.eval_filter_metadata,
        )
        predicate, params = filter_builder.translate([filter_item])
        return predicate or "", params

    @staticmethod
    def _root_plan_runs_only_in_classifier(plan: LatestFilterPredicate) -> bool:
        """Return whether root discovery must avoid a provider JSON predicate.

        The seed remains a complete superset when this predicate is omitted:
        every matching trace still has a canonical root in the requested
        window.  The exact candidate classifier repeats all root predicates
        against latest state before a row can be published.
        """

        return plan.source_metric in _CLASSIFIER_ONLY_ROOT_SEED_METRICS

    def _positive_exact_end_user_span_seed(self) -> tuple[str, dict[str, Any]]:
        """Compile the direct span predicate for candidate-first user seeding.

        ``user``/``user_id`` values are external identifiers, so resolve them
        through the existing curated end-user/remap expansion before probing
        the indexed span UUID. ``end_user_id`` is already a physical structural
        UUID and can use the normal direct-column compiler. The returned
        predicate is a necessary candidate condition only; the existing finite
        latest-state classifier remains authoritative for publication.
        """

        filter_item = self._positive_exact_end_user_seed_filter()
        if filter_item is None:
            return "", {}
        key = filter_item.get("column_id") or filter_item.get("columnId")
        config = (
            filter_item.get("filter_config") or filter_item.get("filterConfig") or {}
        )
        operation = normalize_filter_op(
            config.get("filter_op") or config.get("filterOp")
        )
        value = config.get("filter_value", config.get("filterValue"))
        filter_builder = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
            project_id=self.project_id,
            project_ids=self.project_ids,
            score_date_scope=False,
            span_date_scope=True,
            strict_enduser_project_correlation=True,
        )
        if key in {"user", "user_id"}:
            external_column = filter_builder._ENDUSER_STRING_COLUMNS[key]
            inner = filter_builder._build_column_condition(
                external_column,
                "text",
                operation,
                value,
            )
            if not inner:
                return "", {}
            predicate = (
                "end_user_id IN ("
                f"{filter_builder._enduser_dimension_id_subquery(inner)}"
                ")"
            )
        else:
            predicate = filter_builder._build_column_condition(
                "end_user_id",
                "text",
                operation,
                value,
            )
        return predicate or "", dict(filter_builder._params)

    def supports_filter_candidate_seed_page(self) -> bool:
        """Use an exact relational candidate before public or bulk roots."""

        public_list_or_bulk_identity = (
            not self._bounded_identity_only and not self._bounded_bulk_scan
        ) or (self._bounded_identity_only and self._bounded_bulk_scan)
        return bool(
            not self._bounded_internal_scan
            and public_list_or_bulk_identity
            and not self._bounded_population_proof
            and not self.sort_params
            and (
                self._positive_exact_end_user_seed_filter() is not None
                or self._positive_relational_seed_filter() is not None
            )
        )

    @staticmethod
    def filter_candidate_seed_proves_result_order() -> bool:
        """Candidate-first membership is followed by ordered root selection."""

        return True

    def _positive_exact_end_user_seed(self) -> tuple[str, dict[str, Any]]:
        """Compile an indexed, project/time-scoped trace-membership superset."""

        filter_item = self._positive_exact_end_user_seed_filter()
        if filter_item is None:
            return "", {}
        filter_builder = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
            project_id=self.project_id,
            project_ids=self.project_ids,
            score_date_scope=False,
            span_date_scope=True,
            strict_enduser_project_correlation=True,
        )
        predicate, params = filter_builder.translate([filter_item])
        return predicate or "", params

    def recommended_filter_query_timeout_ms(self) -> int | None:
        """Use the request deadline for every public filtered trace page.

        The endpoint owns one 9.5-second wall, while finite candidate, query-count,
        byte, memory, thread and result controls still bound each physical read.
        Optional anchor/proof builders retain their independently shorter timeout
        recommendations. Internal bulk/workflow readers keep their existing
        statement contract.
        """

        if (
            not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
        ):
            return _USER_DETAIL_FILTER_TIMEOUT_MS
        return None

    def _structured_attribute_filter_count(self) -> int:
        """Count canonical array/object leaves evaluated from JSON overflow."""

        count = 0
        for item in self._bounded_match_filters():
            if not isinstance(item, dict):
                continue
            key = item.get("column_id") or item.get("columnId")
            if key in {"created_at", "start_time"}:
                continue
            config = item.get("filter_config") or item.get("filterConfig") or {}
            if not isinstance(config, dict):
                continue
            filter_type = normalize_span_attribute_filter_type(
                str(config.get("filter_type") or config.get("filterType") or ""),
                config.get("filter_value", config.get("filterValue")),
            )
            if filter_type in {"array", "map"}:
                count += 1
        return count

    def _custom_span_attribute_filter_count(self) -> int:
        """Count filters that replay custom typed-Map/JSON span state."""

        filter_builder_cls = self._FILTER_BUILDER_CLS
        promoted_system_keys = set(getattr(filter_builder_cls, "SYSTEM_METRIC_MAP", {}))
        for mapping_name in (
            "VOICE_SYSTEM_METRIC_EXPRS",
            "VOICE_SYSTEM_METRIC_STR_MAP",
            "VOICE_SYSTEM_METRIC_STR_EXPRS",
        ):
            promoted_system_keys.update(getattr(filter_builder_cls, mapping_name, {}))

        count = 0
        for item in self._bounded_match_filters():
            if not isinstance(item, dict):
                continue
            key = item.get("column_id") or item.get("columnId")
            if not key or key in {"created_at", "start_time"}:
                continue
            config = item.get("filter_config") or item.get("filterConfig") or {}
            if not isinstance(config, dict):
                continue
            col_type = str(
                config.get("col_type") or config.get("colType") or ""
            ).upper()
            if col_type == "SPAN_ATTRIBUTE" and key not in promoted_system_keys:
                count += 1
        return count

    def bounded_filter_degraded_error_code(self) -> str | None:
        """Explain why a supported filter must not use the broad legacy read."""

        # The bounded reader has one fixed newest-first order.  Free-text
        # search is compiled as a root predicate above; an arbitrary custom
        # sort still cannot be answered in that hard-coded order.
        if self.sort_params:
            return "unsupported_filter_modifiers"
        if not self._active_non_time_filters() and not self.search:
            return None
        if not supports_trace_filters(self._bounded_filters()):
            return (
                "unsupported_filter_shape"
                if targets_trace_filter_domain(self.filters)
                else None
            )
        return None

    def filter_seed_proves_result_order(self) -> bool:
        """Only root seeds can prove a canonical root-order prefix.

        Any-span filters seed the directly-indexable matching child span.
        Child order is unrelated to root order, so those reads exhaust the
        complete request window before returning page 1 or page N.
        """

        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        return not any(plan.scope == "any" for plan in plans)

    def filter_seed_proves_population_bound(self) -> bool:
        """Whether the direct seed may prove only exhaustion or an oversize set.

        Historical eval tasks configured above the executable 10k buffer do not
        consume the reader's newest-first page. They accept a result only after
        the complete filtered population is exhausted, and reject it as soon as
        a 10k+1 sentinel is exact-classified. In that one mode an unordered,
        directly filtered physical-span seed is both cheaper and sufficient;
        it must never be used to expose a numbered trace-list prefix.
        """

        if not self._bounded_population_proof:
            return False
        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        return any(plan.scope == "any" for plan in plans)

    @staticmethod
    def filter_cursor_seed_keyset_is_safe() -> bool:
        """Ordered-root seeds may keyset at the public trace cursor.

        The cursor predicate is applied to physical roots *before* ``LIMIT 1
        BY trace``.  If a newer raw root is tombstoned and the canonical live
        root is older than the cursor, that older physical root still satisfies
        the predicate and seeds the trace for latest-state classification.  The
        direct any-span child seed does not share this property; cursor reads
        must continue to select ``build_filter_ordered_seed_page`` first.
        """

        return True

    def _filter_anchor_plans(self) -> list[LatestFilterPredicate]:
        """Return directly selective any-span leaves safe for a broad probe.

        Typed Map/system predicates can use deployed skip indexes and stop at
        the 513-row sentinel.  Structured JSON extraction has no such index;
        probing it across the complete UI window was itself the expensive
        query and could consume the endpoint deadline before the bounded
        root-ordered fallback ran.
        """

        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        error_status_plan = self._selective_error_status_anchor_plan()
        candidates = [
            (index, plan)
            for index, plan in enumerate(plans)
            if plan.scope == "any" and self._plan_uses_indexed_anchor(plan)
        ]
        if error_status_plan is not None:
            # The public status comparison is case-insensitive and therefore
            # compiles through lowerUTF8(), which cannot use the deployed raw
            # status bloom.  Prefer this equivalent positive raw witness for
            # sparse ERROR/ERRORED/FAILED discovery; latest-state replay below
            # remains authoritative and removes stale physical versions.
            candidates.append((-1, error_status_plan))
        candidates.sort(
            key=lambda item: (
                item[1].raw_witness_rank is None,
                item[1].raw_witness_rank
                if item[1].raw_witness_rank is not None
                else 1_000_000,
                item[0],
            )
        )
        return [plan for _, plan in candidates]

    def _selective_error_status_anchor_plan(
        self,
    ) -> LatestFilterPredicate | None:
        """Return the indexed positive error witness used by Display.

        Trace status has any-span semantics.  A matching trace must therefore
        have at least one physical error-status row somewhere in its history.
        The raw status bloom is a safe candidate superset for equals/IN error
        filters; the finite global classifier still decides current membership.
        """

        for item_index, item in enumerate(self._active_non_time_filters()):
            key = str(item.get("column_id") or item.get("columnId") or "")
            if key != "status":
                continue
            config = item.get("filter_config") or item.get("filterConfig") or {}
            operation = normalize_filter_op(
                str(config.get("filter_op") or config.get("filterOp") or "")
            )
            raw_value = config.get("filter_value", config.get("filterValue"))
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            normalized_values = {
                str(value).strip().upper() for value in values if value is not None
            }
            if (
                operation in {"equals", "in"}
                and normalized_values
                and normalized_values <= {"ERROR", "ERRORED", "FAILED"}
            ):
                item_plans, residual = self._partition_trace_filter_plans([item])
                if residual or len(item_plans) != 1 or item_plans[0].scope != "any":
                    return None
                plan = item_plans[0]
                anchor_param = f"trace_error_status_anchor_values_{item_index}"
                return LatestFilterPredicate(
                    aggregates=plan.aggregates,
                    predicate=plan.predicate,
                    seed_predicate=plan.seed_predicate,
                    params={
                        **plan.params,
                        anchor_param: tuple(sorted(normalized_values)),
                    },
                    scope=plan.scope,
                    raw_witness_predicate=f"status IN %({anchor_param})s",
                    raw_key_witness_predicate=plan.raw_key_witness_predicate,
                    raw_witness_rank=0,
                    source_metric=plan.source_metric,
                )
        return None

    def _uses_global_error_status_anchor(self) -> bool:
        """Whether a sparse list may prove its error candidate population.

        A request-window child probe cannot prove trace absence because the
        root can be in-window while its matching child is outside it.  For the
        long-window Display error shape, probe the indexed error witness across
        the project's complete retained history instead.  Exhausting that
        finite superset is then an exact population proof after classification.
        Graph strata retain their request-window sampling contract.
        """

        request_start, request_end = self._bounded_request_window
        return bool(
            not getattr(self, "_bounded_anchor_probe", False)
            and request_end - request_start > timedelta(hours=1)
            and self._selective_error_status_anchor_plan() is not None
        )

    def _selective_exact_text_anchor_plan(
        self,
    ) -> LatestFilterPredicate | None:
        """Return one long exact typed-Map leaf for global candidate discovery.

        Compile the leaf independently so a preceding broad predicate cannot
        replace the selective value witness.  The returned predicate is only a
        necessary physical-row witness; the ordinary latest-state classifier
        still applies the complete filter conjunction to every candidate.
        """

        for item in self._active_non_time_filters():
            if not isinstance(
                item, dict
            ) or not self._candidate_witness_filter_is_selective_exact_text(item):
                continue
            item_plans, residual = self._partition_trace_filter_plans([item])
            if residual or len(item_plans) != 1 or item_plans[0].scope != "any":
                continue
            plan = item_plans[0]
            raw_witness = self._exact_graph_authoritative_raw_witness(plan)
            if raw_witness and "JSONExtract" not in raw_witness:
                return plan
        return None

    def _uses_global_selective_exact_text_anchor(self) -> bool:
        """Whether a fresh interactive list may discover exact-text candidates.

        The all-history witness is deliberately narrow: one project, no search,
        sort, sampling, graph/bulk/population mode, and a long selective exact
        text leaf.  Voice lists use an internal trace delegate, so internal mode
        alone is not excluded; identity/bulk modes remain excluded.
        """

        request_start, request_end = self._bounded_request_window
        return bool(
            self.project_id is not None
            and self.project_ids is None
            and not self.search
            and not self.sort_params
            and self._bounded_membership_filters is None
            and not self._bounded_identity_only
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and not self._bounded_global_span_witnesses
            and self._bounded_sampling_rate is None
            and not getattr(self, "_bounded_anchor_probe", False)
            and request_end - request_start > timedelta(hours=1)
            and self._selective_exact_text_anchor_plan() is not None
        )

    def _graph_key_witness_plans(self) -> list[LatestFilterPredicate]:
        """Return positive any-span Map keys for graph-only discovery.

        A trace may satisfy two attribute filters on different child spans, so
        discovery may retain only one key leaf. The complete latest-state
        classifier still applies every value/filter leaf to each candidate.
        """

        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        candidates = [
            (index, plan)
            for index, plan in enumerate(plans)
            if plan.scope == "any" and plan.raw_key_witness_predicate
        ]
        candidates.sort(
            key=lambda item: (
                item[1].raw_witness_rank is None,
                item[1].raw_witness_rank
                if item[1].raw_witness_rank is not None
                else 1_000_000,
                item[0],
            )
        )
        return [plan for _, plan in candidates]

    def _positive_typed_map_anchor_plan(self) -> LatestFilterPredicate | None:
        """Return the best rank-zero scalar Map equality/IN anchor."""

        for plan in self._filter_anchor_plans():
            predicate = str(plan.raw_witness_predicate or plan.seed_predicate or "")
            if (
                plan.raw_witness_rank == 0
                and "JSONExtract" not in predicate
                and re.search(
                    r"\bmapContains\(span_attr_(?:str|num|bool),",
                    predicate,
                )
            ):
                return plan
        return None

    def _positive_typed_map_candidate_plan(
        self,
    ) -> LatestFilterPredicate | None:
        """Return one positive scalar Map leaf safe for finite discovery.

        Rank-zero equality/IN leaves keep their selective raw key/value
        witness. Other positive scalar operations (for example ``>`` or
        ``is_not_null``) may add their raw-row seed predicate only when the
        compiler proves a missing-key default cannot satisfy it; unsafe shapes
        keep key presence alone. This helper is intentionally limited to the
        optional exact graph candidate probe; authoritative partitioning
        continues to require the narrower rank-zero plan above.
        """

        for plan in self._graph_key_witness_plans():
            key_witness = str(plan.raw_key_witness_predicate or "")
            if re.search(
                r"\bhas\(span_attr_(?:str|num|bool)\.keys,",
                key_witness,
            ):
                return plan
        return None

    @staticmethod
    def _exact_graph_authoritative_raw_witness(
        plan: LatestFilterPredicate,
    ) -> str:
        """Return an exhaustive raw witness for one authoritative scalar leaf.

        Typed Maps need the graph-specific value witness when it is safe.  If
        a missing key's physical default can satisfy the independently reduced
        value (notably ``false`` for boolean Maps), key presence is the only
        exhaustive raw superset.  Direct scalar columns such as ``model`` have
        no Map key witness and use their compiler-proven positive raw
        predicate.  The latest-state replay remains authoritative in every
        case; this predicate only chooses the physical identities to reduce.
        """

        for candidate in (
            plan.raw_graph_value_witness_predicate,
            plan.raw_key_witness_predicate,
            plan.raw_witness_predicate,
        ):
            predicate = str(candidate or "").strip()
            if predicate and "JSONExtract" not in predicate:
                return predicate
        return ""

    def _filter_exact_zero_probe_plans(
        self,
    ) -> list[LatestFilterPredicate] | None:
        """Disable request-window-only negative proofs for trace membership.

        A trace's positive datetime filter binds its canonical root, while an
        any-span attribute may be satisfied by a current child at any
        timestamp.  The former short-window UNION scanned child witnesses only
        inside the root window, so an empty result could incorrectly exclude a
        valid trace whose sole matching child was written before or after that
        window.  Ordered canonical-root acquisition plus the finite global
        classifier is the authoritative exact path.

        Voice-call builders retain a separate compatibility query, but their
        capability hook declines the same request-window-only negative proof.
        Generic trace pages deliberately expose no exact-zero shortcut until
        a candidate-scoped all-history proof is available.
        """

        return None

    def supports_filter_exact_zero_probe(self) -> bool:
        """Whether this generic public trace page can prove an empty result."""

        return self._filter_exact_zero_probe_plans() is not None

    @staticmethod
    def filter_exact_zero_probe_proves_global_membership() -> bool:
        """Return false for every request-window-only trace zero probe.

        Subclasses may retain their SQL builder for compatibility, but the
        shared selector must not let a temporal child-witness query terminate
        an exact trace/voice page. A future implementation may opt in only
        after proving absence across all child timestamps for finite roots.
        """

        return False

    @staticmethod
    def recommended_filter_exact_zero_probe_timeout_ms() -> int:
        return 1_500

    @staticmethod
    def recommended_filter_exact_zero_probe_max_bytes() -> int:
        return 256 * 1024 * 1024

    def build_filter_exact_zero_probe(self) -> tuple[str, dict[str, Any]]:
        """Build an exact-zero proof from independent any-span raw witnesses."""

        any_span_plans = self._filter_exact_zero_probe_plans()
        if any_span_plans is None:
            raise ValueError("exact-zero probe is unavailable for this filter shape")

        request_start, request_end = self._bounded_request_window
        params: dict[str, Any] = {
            **self.params,
            "exact_zero_start_us": _unix_microseconds(request_start),
            "exact_zero_end_us": _unix_microseconds(request_end),
        }
        branches: list[str] = []
        witness_conditions: list[str] = []
        for witness_index, plan in enumerate(any_span_plans):
            params.update(plan.params)
            branches.append(
                f"""
                SELECT trace_id, toUInt16({witness_index}) AS witness_kind
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  AND toDate(start_time) >= toDate(fromUnixTimestamp64Micro(%(exact_zero_start_us)s))
                  AND toDate(start_time) <= toDate(fromUnixTimestamp64Micro(%(exact_zero_end_us)s))
                  AND start_time >= fromUnixTimestamp64Micro(%(exact_zero_start_us)s)
                  AND start_time < fromUnixTimestamp64Micro(%(exact_zero_end_us)s)
                WHERE {plan.raw_witness_predicate}
                GROUP BY trace_id
                """
            )
            witness_conditions.append(f"countIf(witness_kind = {witness_index}) > 0")

        query = f"""
        SELECT trace_id
        FROM (
            {" UNION ALL ".join(branches)}
        ) AS raw_filter_witnesses
        GROUP BY trace_id
        HAVING {" AND ".join(witness_conditions)}
        LIMIT 1
        """
        return query, params

    @staticmethod
    def _plan_uses_indexed_anchor(plan: LatestFilterPredicate) -> bool:
        """Return whether an any-span predicate has a safe broad sentinel."""

        predicate = " ".join(
            str(plan.raw_witness_predicate or plan.seed_predicate or "").split()
        )
        if not predicate or predicate.replace(" ", "") == "1=1":
            return False
        if "JSONExtract" in predicate:
            return False
        if re.search(r"\bhas\(span_attr_(?:str|num|bool)\.keys,", predicate):
            return True
        if "mapContains(span_attr_" in predicate:
            if "NOT mapContains(span_attr_" in predicate:
                return False
            if any(
                fragment in predicate
                for fragment in (
                    " != ",
                    " NOT IN ",
                    " NOT BETWEEN ",
                    "positionUTF8(",
                    "startsWith(",
                    "endsWith(",
                )
            ) or re.search(r"\bIS\s+(?:NOT\s+)?NULL\b", predicate):
                return False
            if "lowerUTF8(" in predicate and not any(
                companion in predicate
                for companion in (
                    "has(arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))",
                    "hasAny(arrayMap(x -> lowerUTF8(x), mapValues(span_attr_str))",
                )
            ):
                return False
            return True
        if any(
            fragment in predicate
            for fragment in (
                "lowerUTF8(",
                "positionUTF8(",
                "startsWith(",
                "endsWith(",
            )
        ):
            return False
        if any(
            fragment in predicate for fragment in (" != ", " NOT IN ", " NOT BETWEEN ")
        ) or re.search(r"\bIS\s+(?:NOT\s+)?NULL\b", predicate):
            return False
        return any(
            re.search(rf"\b{re.escape(column)}\b", predicate)
            for column in _INDEXED_TRACE_ANY_SPAN_ANCHOR_COLUMNS
        )

    @classmethod
    def _plan_uses_selective_graph_anchor(cls, plan: LatestFilterPredicate) -> bool:
        """Return whether a graph may probe an entire temporal stratum.

        A Map-key bloom is useful for the list endpoint's tightly capped,
        optional sparse probe. It is not selective when a common text/boolean
        key still requires value evaluation across a large graph stratum.
        Numeric equality/IN retains its separate value-index companion; numeric
        ranges use the bounded temporal sample lane as well.
        """

        if not cls._plan_uses_indexed_anchor(plan):
            return False
        predicate = " ".join(
            str(plan.raw_witness_predicate or plan.seed_predicate or "").split()
        )
        typed_map_kinds = set(re.findall(r"\bspan_attr_(str|num|bool)\b", predicate))
        if typed_map_kinds:
            # Keep the legacy bounded-sample scheduler conservative. Exact
            # graph/list readers use the UTF-8 value companion directly; this
            # older lane still has numeric-specific retry/stratum assumptions.
            return typed_map_kinds == {"num"} and any(
                fragment in predicate
                for fragment in (
                    "has(mapValues(span_attr_num)",
                    "hasAny(mapValues(span_attr_num)",
                )
            )
        return True

    def _has_unindexed_any_span_filter(self) -> bool:
        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        return any(
            plan.scope == "any" and "JSONExtract" in plan.seed_predicate
            for plan in plans
        )

    def requires_unindexed_graph_sample_slice(self) -> bool:
        """Use micro-slices when no positive index-usable superset exists.

        This is expression-aware rather than column-name-aware. Text comparison
        wrappers, root text fields, JSON extraction, and negative/null-only
        shapes use fixed temporal micro-slices unless another positive predicate
        has a deployed index companion ClickHouse can actually apply.
        """

        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        return bool(plans) and not any(
            self._plan_uses_selective_graph_anchor(plan) for plan in plans
        )

    def _unindexed_positive_micro_seed_plan(self) -> LatestFilterPredicate | None:
        """Return one exact raw call-type seed safe for a five-minute probe.

        Provider-backed ``call_type`` is derived from the JSON overflow and has
        no production skip index. Its positive equality/IN predicate is still
        a complete *raw* superset for latest-state voice-root membership: every
        latest live match has a physical live root row with the same value.
        Restrict this optimization to that explicitly supported shape;
        negative/null JSON predicates must keep the ordered candidate path
        because raw absence is not a latest-state proof.
        """

        plans, residual_filters = self._partition_trace_filter_plans(
            self._bounded_filters()
        )
        active_filters = self._active_non_time_filters()
        if residual_filters or len(plans) != len(active_filters):
            return None
        for item, plan in zip(active_filters, plans, strict=True):
            config = item.get("filter_config") or item.get("filterConfig") or {}
            if not isinstance(config, dict):
                continue
            key = str(item.get("column_id") or item.get("columnId") or "")
            operation = normalize_filter_op(
                str(config.get("filter_op") or config.get("filterOp") or "")
            )
            if (
                key == "call_type"
                and operation in {"equals", "in"}
                and plan.scope == "root"
                and "JSONExtract" in plan.seed_predicate
                and not self._plan_uses_indexed_anchor(plan)
            ):
                return plan
        return None

    def recommended_filter_unindexed_micro_seed_width(self) -> timedelta | None:
        """Request one newest fixed-width JSON seed before ordered fallback."""

        request_start, request_end = self._bounded_request_window
        if (
            (not self._bounded_identity_only or self._bounded_internal_scan)
            and request_end - request_start > timedelta(hours=1)
            and self._unindexed_positive_micro_seed_plan() is not None
        ):
            return _UNINDEXED_POSITIVE_MICRO_SEED_WIDTH
        return None

    def recommended_filter_unindexed_micro_seed_strata(self) -> int | None:
        """Sample fixed micro-slices across the complete long window."""

        if self.recommended_filter_unindexed_micro_seed_width() is not None:
            return _UNINDEXED_POSITIVE_MICRO_SEED_STRATA
        return None

    def build_filter_unindexed_micro_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
    ) -> tuple[str, dict[str, Any]]:
        """Seed finite raw JSON matches inside one fixed micro-slice."""

        return self.build_filter_seed_page(
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
            _unindexed_positive_micro_seed=True,
        )

    @staticmethod
    def filter_unindexed_micro_seed_proves_result_order() -> bool:
        """A matching child's timestamp does not prove its root-trace order."""

        return False

    def recommended_filter_seed_batch_size(self) -> int:
        """Amortize cheap identity seeds without widening classification.

        Bulk identity-only eval/task selection has its own 200-row envelope.
        Normal any-span lists also seed only root identity, ID, and timestamp;
        acquiring 200 ordered roots reduces sparse long-window round trips.
        The independent classifier recommendation remains at the
        production-safe 50-trace ceiling and hydrates presentation state.
        """

        if self._bounded_bulk_scan:
            return 200
        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        public_non_time_filters = [
            item
            for item in self._active_non_time_filters()
            if not item.get("_eval_task_trace_root")
        ]
        request_start, request_end = self._bounded_request_window
        if (
            request_end - request_start <= timedelta(hours=1)
            and any(plan.scope == "any" for plan in plans)
            and not self._has_unindexed_any_span_filter()
        ):
            return 512
        if request_end - request_start > timedelta(hours=1) and any(
            plan.scope == "any" for plan in plans
        ):
            # A positive typed-Map leaf can first resolve its latest state for
            # the complete finite root batch.  Acquire the selector's full
            # working set so a sparse value does not pay three root reads before
            # that exact prefilter can remove stale historical witnesses.
            if (
                not self._bounded_identity_only
                and not self._bounded_internal_scan
                and not self.search
                and len(public_non_time_filters) == 1
                and self._positive_typed_map_anchor_plan() is not None
            ):
                return 512
            return 200
        return 50

    def recommended_filter_initial_slice_width(self) -> timedelta | None:
        """Avoid under-filled five-minute root reads for long Map filters.

        Normal list pages with a positive typed-Map leaf ultimately fall back
        to ``build_filter_ordered_seed_page`` whenever the optional witness
        probe is unavailable or broad.  That seed reads only root identities,
        is ordered by the indexed ``start_time`` key, and retains a finite
        LIMIT.  Starting it at one hour removes several serial five/ten/twenty-
        minute reads on sparse and deep pages without changing membership,
        pagination order, or the exact latest-state classifier.  Graph, eval,
        task, population-proof, and other internal modes keep the conservative
        shared five-minute default.
        """

        request_start, request_end = self._bounded_request_window
        request_width = request_end - request_start
        if self._uses_full_window_time_only_bulk_identity_scan():
            return request_width
        if (
            not self._bounded_identity_only
            and not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and request_width >= timedelta(minutes=5)
            and (
                self._positive_exact_end_user_seed_filter() is not None
                or self._positive_relational_seed_filter() is not None
            )
        ):
            # The root seed is constrained by an exact project-scoped
            # relational membership subquery. Read the requested window once
            # instead of serially proving empty two-day slices for a sparse
            # relation.
            return request_width
        if (
            not self._bounded_identity_only
            and not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and request_end - request_start > timedelta(hours=1)
            and self._positive_typed_map_anchor_plan() is not None
        ):
            return _LONG_WINDOW_ORDERED_ROOT_INITIAL_SLICE
        return None

    def _uses_default_newest_first_partition_walk(self) -> bool:
        request_start, request_end = self._bounded_request_window
        return bool(
            not self._bounded_identity_only
            and not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and request_end - request_start >= timedelta(minutes=5)
            and not self.sort_params
            and not self.search
            and not self._active_non_time_filters()
        )

    def allow_repeated_eager_identity_prefix_flushes(self) -> bool:
        """Classify each sufficient tail batch until the default page closes."""

        return self._uses_default_newest_first_partition_walk()

    def recommended_filter_max_slice_width(self) -> timedelta | None:
        """Permit adaptive sparse cursor widening on index-pruned root seeds.

        A default newest-first page starts at the same five-minute tail for a
        one-month and a twelve-month request.  Once an adjacent slice is
        exhausted, allowing it to double to the request width reaches sparse
        historical data logarithmically without inflating the first read just
        because the lower bound is older.  Dense tails still stop as soon as
        the exact page prefix is classified.  Cursor reads retain the same
        progression and halve a read-budget failure before publishing.
        """

        request_start, request_end = self._bounded_request_window
        request_width = request_end - request_start
        if self._uses_full_window_time_only_bulk_identity_scan():
            return request_width
        if self._uses_default_newest_first_partition_walk():
            return request_width
        if (
            not self._bounded_identity_only
            and not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and request_width >= timedelta(minutes=5)
            and (
                self._positive_exact_end_user_seed_filter() is not None
                or self._positive_relational_seed_filter() is not None
            )
        ):
            return request_width
        if (
            not self._bounded_identity_only
            and not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and request_width > timedelta(hours=1)
            and self._positive_typed_map_anchor_plan() is not None
        ):
            return request_width
        return None

    def recommended_filter_classify_batch_size(self) -> int | None:
        """Keep the candidate-trace latest-state scan below CH's memory ceiling."""

        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        if self._custom_span_attribute_filter_count():
            # Custom attributes replay typed-Map or JSON overflow state for
            # every physical span of each candidate trace. Keep list, graph,
            # and eval/task classifiers on the same qualified ten-trace
            # envelope. Built-in denormalized metrics retain their wider
            # indexed paths.
            return _STRUCTURED_ANY_SPAN_CLASSIFY_BATCH_SIZE
        if (
            not self._bounded_population_proof
            and self._unindexed_positive_micro_seed_plan() is not None
        ):
            # A call-type classifier parses JSON for every physical span of
            # each candidate trace. Keep the same twenty-trace envelope already
            # qualified by the bounded graph union instead of allowing the
            # identity-only list/eval fast paths to widen it beyond twenty.
            return _BULK_ANY_SPAN_CLASSIFY_BATCH_SIZE
        # Explicit identity-only consumers retain their established envelopes.
        # A historical task can opt into membership-only classification and
        # replay witnesses separately; all one-phase any-span consumers stay at
        # the production-proven graph batch.
        if self._bounded_bulk_scan:
            if any(plan.scope == "any" for plan in plans):
                if self._bounded_population_proof:
                    # Population-proof classifiers may attach witnesses in the
                    # same exact pass. Production-qualified 100-trace batches
                    # keep the complete 10k+sentinel proof within 128 queries.
                    return _BULK_IDENTITY_CLASSIFY_BATCH_SIZE
                if not self._bounded_include_filter_witnesses:
                    return _BULK_IDENTITY_CLASSIFY_BATCH_SIZE
                return _BULK_ANY_SPAN_CLASSIFY_BATCH_SIZE
            return 200
        request_start, request_end = self._bounded_request_window
        if (
            request_end - request_start <= timedelta(hours=1)
            and any(plan.scope == "any" for plan in plans)
            and not self._has_unindexed_any_span_filter()
        ):
            return 512
        # A normal list now projects only membership/order identities during
        # classification and hydrates its final public page separately. Keep
        # this recommendation isolated from graph/eval/task builders, which set
        # bounded_identity_only explicitly and must retain their old batches.
        if not self._bounded_identity_only and not self._bounded_internal_scan:
            return _NORMAL_LIST_IDENTITY_CLASSIFY_BATCH_SIZE
        return 50

    def recommended_filter_classify_read_settings(self) -> dict[str, int] | None:
        """Cap blocks and columns that materialize custom Map/JSON values."""

        if not self._custom_span_attribute_filter_count():
            return None
        return {
            "max_block_size": _STRUCTURED_CLASSIFY_MAX_BLOCK_SIZE,
            "preferred_max_column_in_block_size_bytes": (
                _STRUCTURED_CLASSIFY_MAX_COLUMN_BYTES
            ),
        }

    def use_identity_only_filter_classification(self) -> bool:
        """Defer presentation hydration until the exact public page is proven.

        Eval/task and graph readers already request the identity-only
        projection explicitly.  Normal trace lists can use the same lightweight
        latest-state membership projection while scanning ordered candidates,
        then hydrate only the final page through one separately bounded query.
        """

        return not self._bounded_identity_only

    def fill_bounded_cursor_page_across_slices(self) -> bool:
        """Fill one public trace page before publishing a slice checkpoint.

        Sparse filters can produce only a handful of matches in each finite
        time slice.  Those matches are already exact and can safely accumulate
        across adjacent slices inside the selector's existing request wall,
        query-count, memory, and byte limits.  Internal, bulk, population-proof,
        and sampled readers retain their smaller checkpoint contract.
        """

        return bool(
            not self._bounded_internal_scan
            and not self._bounded_identity_only
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and self._bounded_sampling_rate is None
            and 1 <= self.page_size <= 500
        )

    @staticmethod
    def recommended_filter_page_hydration_reserve_ms() -> int:
        """Reserve one bounded statement for exact-root page hydration."""

        return 750

    def bounded_filter_seed_identity(
        self, row: dict[str, Any]
    ) -> tuple[str, str, str, Any] | tuple[str, str] | str:
        """Keyset selective seeds by physical span, public rows by trace."""

        if row.get("matched_span_id"):
            return (
                str(row.get("project_id") or self.project_id or ""),
                str(row.get("trace_id") or ""),
                str(row.get("matched_span_id") or ""),
                row.get("start_time"),
            )
        if self.project_ids is not None:
            return (
                str(row.get("project_id") or ""),
                str(row.get("trace_id") or ""),
            )
        return str(row.get("trace_id") or "")

    def bounded_filter_seed_order_token(
        self,
        row: dict[str, Any],
    ) -> tuple[str, str, str] | tuple[str, str] | str:
        if row.get("matched_span_id"):
            return (
                str(row.get("matched_span_id") or ""),
                str(row.get("trace_id") or ""),
                str(row.get("project_id") or ""),
            )
        if self.project_ids is not None:
            return (
                str(row.get("trace_id") or ""),
                str(row.get("project_id") or ""),
            )
        return str(row.get("trace_id") or "")

    def bounded_filter_row_identity(self, row: dict[str, Any]) -> tuple[str, str] | str:
        """Keep same-text trace IDs distinct across organization projects."""

        trace_id = str(row.get("trace_id") or "")
        if self.project_ids is not None:
            return str(row.get("project_id") or ""), trace_id
        return trace_id

    def bounded_filter_row_order_token(
        self, row: dict[str, Any]
    ) -> tuple[str, str] | str:
        """Match the result query's deterministic tenant-aware order."""

        trace_id = str(row.get("trace_id") or "")
        if self.project_ids is not None:
            return trace_id, str(row.get("project_id") or "")
        return trace_id

    def allow_filter_anchor_probe_for_initial_continuation(self) -> bool:
        """Run complete sparse witnesses only on a fresh cursor page."""

        return bool(
            self._uses_global_error_status_anchor()
            or self._uses_global_selective_exact_text_anchor()
        )

    def supports_filter_anchor_probe(self) -> bool:
        """Whether a direct any-span leaf can classify sparse vs common."""

        # The positive user-detail shape already has an exact, indexed,
        # remap-bounded membership seed.  A speculative any-span anchor adds a
        # shorter statement timeout in front of that authoritative path and can
        # make a healthy large-remap request fail before the seed is attempted.
        if self._positive_exact_end_user_seed_filter() is not None:
            return False
        return bool(self._filter_anchor_plans())

    def filter_anchor_probe_proves_complete_population(self) -> bool:
        """Return true only for complete-history necessary witnesses.

        Ordinary temporal child anchors remain positive accelerators: the
        canonical root may be in the requested window while its matching child
        lies outside it.  The specialized error and selective exact-text
        anchors deliberately scan an indexed necessary witness across complete
        retained project history, so exhausting their sentinel proves the full
        candidate population before authoritative latest-state classification.
        """

        return bool(
            self._uses_global_error_status_anchor()
            or self._uses_global_selective_exact_text_anchor()
        )

    def supports_graph_key_witness_probe(self) -> bool:
        """Whether graph discovery can use one cheap typed-Map key leaf."""

        return bool(self._graph_key_witness_plans())

    def skip_full_window_filter_anchor_probe(self) -> bool:
        """Avoid the 513-row broad sentinel outside a short trace window.

        The full sparse/common probe is useful for short windows, but on the
        largest tenant its fixed 513-row scan crossed the former 750 ms native
        client cliff under load before the ordered fallback could start. Skip
        that avoidable broad work instead of relying only on timeout headroom.
        Graph strata provide a smaller explicit ``anchor_probe_limit`` and are
        not covered by this full-window recommendation.
        """

        request_start, request_end = self._bounded_request_window
        return bool(
            request_end - request_start > timedelta(hours=1)
            and not self._uses_global_error_status_anchor()
            and not self._uses_global_selective_exact_text_anchor()
            and self.recommended_filter_anchor_probe_limit() is None
        )

    def recommended_filter_anchor_probe_limit(self) -> int | None:
        """Skip speculative whole-window reads for long-window lists.

        Production showed that a single optional partition can read more than
        500 MiB when the server-locked read-only profile cannot accept the
        caller's per-query timeout/read settings. Two such probes exhausted the
        case read ceiling before the exact ordered-root fallback ran. Short
        windows retain the existing 513-row probe, and graph callers retain
        their explicit per-stratum ``anchor_probe_limit`` contract.

        Membership-only historical eval reads already use the 100-identity
        exact classifier envelope and can optionally prefilter each acquired
        batch with finite typed-Map witnesses.  A broad whole-window anchor
        cannot prove that selector's final membership/order, so do not spend
        its fixed request budget before ordered candidate acquisition.
        """

        if (
            self._uses_global_error_status_anchor()
            or self._uses_global_selective_exact_text_anchor()
        ):
            return _LONG_WINDOW_ANCHOR_SENTINEL

        # The production product-path gate showed that even partitioned long-
        # window probes routinely hit their deliberately tight row/byte caps
        # before the ordered-root reader.  They are optional and their failure
        # cannot prove membership, so spending that request budget only delays
        # the unchanged exact identity classifier.  Returning ``None`` makes
        # ``skip_full_window_filter_anchor_probe`` select the ordered path.
        # Short-window callers still use the selector's ordinary 513-row probe;
        # graph callers pass an explicit per-stratum limit and are unaffected.
        return None

    def recommended_filter_anchor_probe_timeout_ms(self) -> int | None:
        """Bound total optional long-window list probe wall time.

        The selector shares this allowance across every partition and also
        uses the remainder as each statement's timeout. It is intentionally
        not multiplied by the four strata: ordered roots must retain the rest
        of the request deadline whenever the speculative probe is incomplete.
        """

        if self._uses_global_selective_exact_text_anchor():
            # This is the authoritative candidate-acquisition path, not an
            # optional speculative probe. Use the enclosing list request's
            # normal statement/read ceilings instead of the 900 ms / 192 MiB
            # accelerator caps that caused the proven fallback loop.
            return None
        if self.recommended_filter_anchor_probe_limit() is not None:
            return _LONG_WINDOW_ANCHOR_TIMEOUT_MS
        return None

    def recommended_filter_anchor_probe_strata(self) -> int | None:
        """Partition only the optional long-window list probe."""

        if (
            self._uses_global_error_status_anchor()
            or self._uses_global_selective_exact_text_anchor()
        ):
            # This query intentionally covers complete retained history; date
            # strata would turn the finite superset back into a temporal sample.
            return 1
        if self.recommended_filter_anchor_probe_limit() is not None:
            return _LONG_WINDOW_ANCHOR_STRATA
        return None

    def recommended_filter_anchor_probe_max_bytes_to_read(self) -> int | None:
        """Return the per-stratum byte ceiling for optional list probes."""

        if self._uses_global_selective_exact_text_anchor():
            return None
        if self.recommended_filter_anchor_probe_limit() is not None:
            return _LONG_WINDOW_ANCHOR_MAX_BYTES_TO_READ
        return None

    def _build_global_typed_map_witness_probe(
        self,
        *,
        anchor: LatestFilterPredicate,
        limit: int,
        limit_param: str,
        after_trace_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build one stable all-history typed-Map candidate sentinel."""

        raw_witness_predicate = self._exact_graph_authoritative_raw_witness(anchor)
        if not raw_witness_predicate:
            return "", {}
        params: dict[str, Any] = {
            **self.params,
            **{
                key: value
                for key, value in anchor.params.items()
                if f"%({key})s" in raw_witness_predicate
            },
            limit_param: int(limit),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = (
                "AND project_version_id = %(project_version_id)s"
            )
        keyset_fragment = ""
        if after_trace_id is not None:
            if not str(after_trace_id):
                raise ValueError("exact candidate cursor must be non-empty")
            params["exact_graph_candidate_after_trace_id"] = str(after_trace_id)
            keyset_fragment = (
                "AND trace_id > %(exact_graph_candidate_after_trace_id)s"
            )
        query = f"""
        SELECT trace_id
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          {project_version_fragment}
          {keyset_fragment}
        WHERE {raw_witness_predicate}
        ORDER BY trace_id ASC
        LIMIT 1 BY trace_id
        LIMIT %({limit_param})s
        """
        return query, params

    def build_filter_anchor_probe(
        self,
        *,
        limit: int,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
        _graph_key_witness: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Return a finite, stable any-span candidate sentinel.

        Follow the ``spans`` sorting-key suffix before de-duplicating trace
        identities.  This lets ClickHouse stop after the sentinel without a
        full match-set sort and prevents parallel part scheduling from choosing
        a different raw trace sample on every replica/run.  Every row remains
        only a superset seed; the finite classifier resolves its physical
        latest state before it can become a result.
        """

        if limit <= 0 or (limit == 1 and slice_start is None and slice_end is None):
            raise ValueError("anchor probe limit must include a sentinel")
        if (
            not _graph_key_witness
            and slice_start is None
            and slice_end is None
            and self._uses_global_error_status_anchor()
        ):
            anchor = self._selective_error_status_anchor_plan()
            if anchor is None:  # pragma: no cover - guarded by capability hook
                raise ValueError("trace error-status anchor is unavailable")
            anchor_predicate = anchor.raw_witness_predicate
            if not anchor_predicate:  # pragma: no cover - constructed above
                raise ValueError("trace error-status witness is unavailable")
            anchor_params = {
                key: value
                for key, value in anchor.params.items()
                if f"%({key})s" in anchor_predicate
            }
            params: dict[str, Any] = {
                **self.params,
                **anchor_params,
                "filter_anchor_limit": int(limit),
            }
            project_version_fragment = ""
            if self.project_version_id:
                params["project_version_id"] = self.project_version_id
                project_version_fragment = (
                    "AND project_version_id = %(project_version_id)s"
                )
            sampling_fragment = ""
            if self._bounded_sampling_rate is not None:
                params["bounded_sampling_salt"] = str(self._bounded_sampling_salt)
                params["bounded_sampling_rate"] = float(self._bounded_sampling_rate)
                sampling_fragment = """
                  AND modulo(
                      cityHash64(%(bounded_sampling_salt)s, toString(trace_id)), 100
                  ) < %(bounded_sampling_rate)s
                """
            identity_projection = (
                "project_id, trace_id" if self.project_ids is not None else "trace_id"
            )
            identity_limit_by = (
                "project_id, trace_id" if self.project_ids is not None else "trace_id"
            )
            query = f"""
            SELECT {identity_projection}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND is_deleted = 0
              {project_version_fragment}
            WHERE {anchor_predicate}
              {sampling_fragment}
            LIMIT 1 BY {identity_limit_by}
            LIMIT %(filter_anchor_limit)s
            """
            return query, params
        if (
            not _graph_key_witness
            and slice_start is None
            and slice_end is None
            and self._uses_global_selective_exact_text_anchor()
        ):
            anchor = self._selective_exact_text_anchor_plan()
            if anchor is None:  # pragma: no cover - guarded by capability hook
                raise ValueError("selective exact-text anchor is unavailable")
            return self._build_global_typed_map_witness_probe(
                anchor=anchor,
                limit=limit,
                limit_param="filter_anchor_limit",
            )

        request_start, request_end = self.parse_time_range(self.filters)
        if (slice_start is None) != (slice_end is None):
            raise ValueError("anchor probe slice values must be provided together")
        anchor_start = request_start if slice_start is None else slice_start
        anchor_end = request_end if slice_end is None else slice_end
        if not request_start <= anchor_start < anchor_end <= request_end:
            raise ValueError("trace anchor slice must stay inside the request window")
        self.start_date, self.end_date = request_start, request_end
        anchor_plans = (
            self._graph_key_witness_plans()
            if _graph_key_witness
            else self._filter_anchor_plans()
        )
        if not anchor_plans:
            raise ValueError("trace anchor probe requires an indexed any-span filter")
        anchor = anchor_plans[0]
        raw_anchor_predicate = anchor.raw_witness_predicate or anchor.seed_predicate
        numeric_value_indexed = any(
            fragment in raw_anchor_predicate
            for fragment in (
                "has(mapValues(span_attr_num)",
                "hasAny(mapValues(span_attr_num)",
            )
        )
        anchor_predicate = (
            raw_anchor_predicate
            if not _graph_key_witness or numeric_value_indexed
            else anchor.raw_key_witness_predicate
        )
        if not anchor_predicate:  # pragma: no cover - guarded by plan selection
            raise ValueError("trace graph key witness predicate is unavailable")
        anchor_params = {
            key: value
            for key, value in anchor.params.items()
            if f"%({key})s" in anchor_predicate
        }
        params: dict[str, Any] = {
            **self.params,
            **anchor_params,
            "filter_anchor_start": anchor_start,
            "filter_anchor_end": anchor_end,
            "filter_anchor_start_us": _unix_microseconds(anchor_start),
            "filter_anchor_end_us": _unix_microseconds(anchor_end),
            "filter_anchor_limit": int(limit),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        sampling_fragment = ""
        if self._bounded_sampling_rate is not None:
            params["bounded_sampling_salt"] = str(self._bounded_sampling_salt)
            params["bounded_sampling_rate"] = float(self._bounded_sampling_rate)
            sampling_fragment = """
              AND modulo(
                  cityHash64(%(bounded_sampling_salt)s, toString(trace_id)), 100
              ) < %(bounded_sampling_rate)s
            """
        identity_projection = (
            "project_id, trace_id" if self.project_ids is not None else "trace_id"
        )
        identity_limit_by = (
            "project_id, trace_id" if self.project_ids is not None else "trace_id"
        )
        project_order_fragment = (
            "project_id DESC,\n            " if self.project_ids is not None else ""
        )
        query = f"""
        SELECT {identity_projection}
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND is_deleted = 0
          {project_version_fragment}
          AND start_time >= fromUnixTimestamp64Micro(%(filter_anchor_start_us)s)
          AND start_time < fromUnixTimestamp64Micro(%(filter_anchor_end_us)s)
        WHERE {anchor_predicate}
          {sampling_fragment}
        ORDER BY
            {project_order_fragment}observation_type DESC,
            service_name DESC,
            toStartOfHour(start_time) DESC,
            trace_id DESC,
            id DESC
        LIMIT 1 BY {identity_limit_by}
        LIMIT %(filter_anchor_limit)s
        """
        return query, params

    def build_filter_graph_key_witness_probe(
        self,
        *,
        limit: int,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return a finite graph candidate sentinel using key presence only."""

        # Call the v1 implementation directly. On a V2 builder both public
        # ``build*`` methods are rewrite-wrapped; dispatching through ``self``
        # here would append every required SETTINGS assignment twice.
        query, params = TraceListQueryBuilder.build_filter_anchor_probe(
            self,
            limit=limit,
            slice_start=slice_start,
            slice_end=slice_end,
            _graph_key_witness=True,
        )
        params["filter_graph_key_witness"] = 1
        return query, params

    def build_exact_graph_candidate_witness_probe(
        self,
        *,
        limit: int,
        after_trace_id: str | None = None,
        before_start_time: datetime | None = None,
        before_id: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return one keyset page of a necessary exact-graph superset.

        Positive scalar typed-Map predicates have an exhaustive raw witness:
        default-safe comparisons use a key/value witness, while shapes whose
        missing-key default could satisfy an independently aggregated value
        keep key presence alone. Every latest-live matching span is therefore
        still represented even with conflicting rows tied at the maximum
        version. Historical matching rows and tombstones may also survive
        before ReplacingMergeTree merges, so this result is deliberately *not*
        authoritative. The probe retains tombstones because deletion is also
        independently aggregated at latest state; the exact graph reader must
        replay every returned trace through its ordinary classifier, which
        resolves deletion and verifies the canonical root request window.

        Child spans have no maximum duration relative to their root, so raw
        span-attribute witnesses are intentionally all-time.  Positive
        relational witnesses (for example the Score population required by a
        global negative annotator filter) instead select canonical roots and
        are safely bounded to the frozen request window.  A ``limit`` sentinel
        lets the caller use either shortcut only when the complete candidate
        population is demonstrably small; a full sentinel falls back to
        exhaustive root enumeration without publishing rows.
        """

        if limit <= 1:
            raise ValueError("exact graph candidate limit must include a sentinel")
        if not (
            self._bounded_global_span_witnesses
            and self._bounded_internal_scan
            and self._bounded_identity_only
            and self._bounded_bulk_scan
            and not self._bounded_include_filter_witnesses
            and self.project_id is not None
            and self.project_ids is None
            and not self.search
            and self._bounded_sampling_rate is None
        ):
            return "", {}
        anchor = self._positive_typed_map_candidate_plan()
        if anchor is None:
            # A global negative annotator filter is not a raw span-attribute
            # witness, but it does have an exact positive Score population:
            # traces with at least one project-scoped annotation, minus traces
            # annotated by the excluded users.  Acquire only canonical roots
            # in the frozen graph window through that relation.  A full
            # sentinel still falls back to the exhaustive root walk and every
            # returned identity still crosses the unchanged latest-state
            # classifier, so this is a latency optimization only.
            if self._positive_relational_seed_filter() is None:
                return "", {}
            request_start, request_end = self.parse_time_range(self.filters)
            if after_trace_id is not None:
                raise ValueError("relational candidate cursor must use root ordering")
            return TraceListQueryBuilder.build_filter_ordered_seed_page(
                self,
                slice_start=request_start,
                slice_end=request_end,
                limit=limit,
                before_start_time=before_start_time,
                before_id=before_id,
                _positive_relation_candidate_first=True,
            )
        if (before_start_time is None) != (before_id is None):
            raise ValueError("candidate root keyset values must be provided together")
        if before_start_time is not None:
            raise ValueError("raw candidate cursor must use trace identity ordering")
        return self._build_global_typed_map_witness_probe(
            anchor=anchor,
            limit=limit,
            limit_param="exact_graph_candidate_limit",
            after_trace_id=after_trace_id,
        )

    def exact_graph_candidate_witness_replays_global_membership(self) -> bool:
        """Whether the optional graph candidate probe scans retained history."""

        return self._positive_typed_map_candidate_plan() is not None

    def exact_graph_candidate_witness_has_deployed_value_index(self) -> bool:
        """Whether the all-history witness has a proven deployed value index.

        Short root windows normally avoid replaying retained child history.
        The exception is a scalar equality/IN witness whose compiled predicate
        includes either the deployed numeric value bloom or the exhaustive
        Unicode-safe companion for the deployed ASCII string-value bloom.
        Key-only, boolean, range, and non-ASCII string witnesses stay on the
        request-window fallback until their own production path is proven.
        """

        plan = self._positive_typed_map_candidate_plan()
        if plan is None:
            return False
        predicate = self._exact_graph_authoritative_raw_witness(plan)
        if not predicate:
            return False
        return any(
            fragment in predicate
            for fragment in (
                "arrayMap(x -> lower(x), mapValues(span_attr_str))",
                "has(mapValues(span_attr_num)",
                "hasAny(mapValues(span_attr_num)",
            )
        )

    def _exact_graph_authoritative_anchor_plan(
        self,
    ) -> LatestFilterPredicate | None:
        """Return the sole any-span leaf supported by the partitioned graph lane.

        A raw anchor is only a necessary condition when a request contains
        additional leaves; treating it as the complete graph membership set
        would silently drop traces whose sibling spans satisfy those leaves.
        The authoritative lane therefore accepts exactly one non-time positive
        rank-zero scalar any-span equals/IN filter and no relational/root
        residual. This includes typed Maps and direct scalar columns such as
        ``model``. Other shapes keep the existing fail-closed classifier path.
        """

        if not (
            self._bounded_global_span_witnesses
            and self._bounded_internal_scan
            and self._bounded_identity_only
            and self._bounded_bulk_scan
            and not self._bounded_include_filter_witnesses
            and self.project_id is not None
            and self.project_ids is None
            and not self.search
            and self._bounded_sampling_rate is None
        ):
            return None
        active_filters = self._active_non_time_filters()
        if len(active_filters) != 1:
            return None
        try:
            plans, residual_filters = self._partition_trace_filter_plans(active_filters)
        except (TypeError, ValueError):
            return None
        if residual_filters or len(plans) != 1:
            return None
        plan = plans[0]
        predicate = self._exact_graph_authoritative_raw_witness(plan)
        if not (
            plan.scope == "any"
            and plan.raw_witness_rank == 0
            and plan.aggregates
            and plan.predicate
            and predicate
        ):
            return None
        return plan

    def exact_graph_supports_authoritative_anchor_partition(self) -> bool:
        """Whether this graph can replace repeated all-history classification."""

        return self._exact_graph_authoritative_anchor_plan() is not None

    def build_exact_graph_anchor_scan_bounds(self) -> tuple[str, dict[str, Any]]:
        """Freeze a conservative physical range without scanning tenant rows.

        The authoritative lane needs bounds that cover every retained physical
        version, but they do not need to be project-local extrema. Active-part
        metadata is exact for the table's physical coverage and a global range
        can only add empty project slices; it cannot omit a project row. This
        avoids spending the interactive request budget aggregating all retained
        spans before the real filtered read begins.
        """

        if self._exact_graph_authoritative_anchor_plan() is None:
            return "", {}
        query = """
        SELECT
            minOrNull(min_time) AS min_start_time,
            maxOrNull(max_time) AS max_start_time
        FROM system.parts
        WHERE active
          AND database = currentDatabase()
          AND table = 'spans'
        """
        return query, {}

    def build_exact_graph_latest_anchor_partition(
        self,
        *,
        partition_start: datetime,
        partition_end: datetime,
        before_trace_id: str | None = None,
        limit: int = 50_001,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve authoritative latest-live attribute membership in one range.

        ``toStartOfHour(start_time)`` belongs to the production ReplacingMergeTree
        identity, so callers may combine hours but may never bisect one. Mutable
        deletion/key/value predicates are intentionally evaluated *after* the
        version collapse. The ordered trace-id keyset bounds transport memory;
        a full page is continued rather than interpreted as sampling.
        """

        plan = self._exact_graph_authoritative_anchor_plan()
        if plan is None:
            return "", {}
        if not (
            partition_start < partition_end
            and partition_start.minute == 0
            and partition_start.second == 0
            and partition_start.microsecond == 0
            and partition_end.minute == 0
            and partition_end.second == 0
            and partition_end.microsecond == 0
        ):
            raise ValueError(
                "exact graph anchor partition must use whole-hour scan bounds"
            )
        if not 1 <= int(limit) <= 100_001:
            raise ValueError("exact graph anchor partition limit is invalid")

        params: dict[str, Any] = {
            **self.params,
            **plan.params,
            "exact_graph_anchor_start_us": _unix_microseconds(partition_start),
            "exact_graph_anchor_end_us": _unix_microseconds(partition_end),
            "exact_graph_anchor_limit": int(limit),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        keyset_fragment = ""
        physical_keyset_fragment = ""
        if before_trace_id is not None:
            if not str(before_trace_id):
                raise ValueError("exact graph anchor cursor must be non-empty")
            params["exact_graph_anchor_after_trace_id"] = str(before_trace_id)
            keyset_fragment = (
                "AND grouped_trace_id > %(exact_graph_anchor_after_trace_id)s"
            )
            # ``trace_id`` is immutable within the full physical RMT identity,
            # so this transport cursor is safe before the latest-state collapse.
            # Keep the outer guard as an executable ordering assertion while
            # avoiding a complete partition replay on every large page.
            physical_keyset_fragment = (
                "AND trace_id > %(exact_graph_anchor_after_trace_id)s"
            )
        aggregate_fragment = ",\n                    ".join(plan.aggregates)
        raw_witness_predicate = self._exact_graph_authoritative_raw_witness(plan)
        if not raw_witness_predicate:
            return "", {}
        query = f"""
        WITH raw_anchor_identities AS (
            SELECT
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time) AS start_hour,
                trace_id,
                id
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              {project_version_fragment}
              AND is_deleted = 0
              AND toDate(start_time) >= toDate(
                    fromUnixTimestamp64Micro(%(exact_graph_anchor_start_us)s)
                  )
              AND toDate(start_time) <= toDate(
                    fromUnixTimestamp64Micro(%(exact_graph_anchor_end_us)s)
                  )
              AND start_time >= fromUnixTimestamp64Micro(
                    %(exact_graph_anchor_start_us)s
                  )
              AND start_time < fromUnixTimestamp64Micro(
                    %(exact_graph_anchor_end_us)s
                  )
              {physical_keyset_fragment}
            WHERE {raw_witness_predicate}
            GROUP BY
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
        )
        SELECT grouped_trace_id AS trace_id
        FROM (
            SELECT
                trace_id AS grouped_trace_id,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted,
                {aggregate_fragment}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              {project_version_fragment}
              AND toDate(start_time) >= toDate(
                    fromUnixTimestamp64Micro(%(exact_graph_anchor_start_us)s)
                  )
              AND toDate(start_time) <= toDate(
                    fromUnixTimestamp64Micro(%(exact_graph_anchor_end_us)s)
                  )
              AND start_time >= fromUnixTimestamp64Micro(
                    %(exact_graph_anchor_start_us)s
                  )
              AND start_time < fromUnixTimestamp64Micro(
                    %(exact_graph_anchor_end_us)s
                  )
              {physical_keyset_fragment}
              AND (
                    project_id,
                    observation_type,
                    service_name,
                    toStartOfHour(start_time),
                    trace_id,
                    id
                  ) IN (
                    SELECT
                        project_id,
                        observation_type,
                        service_name,
                        start_hour,
                        trace_id,
                        id
                    FROM raw_anchor_identities
                  )
            GROUP BY
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
        ) AS latest_anchor_spans
        WHERE latest_is_deleted = 0
          AND ({plan.predicate})
          {keyset_fragment}
        GROUP BY grouped_trace_id
        ORDER BY grouped_trace_id
        LIMIT %(exact_graph_anchor_limit)s
        """
        return query, params

    def build_exact_graph_root_membership_query(
        self,
        *,
        candidate_trace_ids: list[str] | tuple[str, ...],
        request_start: datetime,
        request_end: datetime,
    ) -> tuple[str, dict[str, Any]]:
        """Verify live canonical roots after version collapse for finite IDs."""

        normalized_trace_ids = tuple(
            dict.fromkeys(str(trace_id) for trace_id in candidate_trace_ids if trace_id)
        )
        if not normalized_trace_ids:
            return "", {}
        if len(normalized_trace_ids) > 512:
            raise ValueError("exact graph root batch exceeds 512 identities")
        if request_start >= request_end:
            raise ValueError("exact graph root window is empty")

        params: dict[str, Any] = {
            **self.params,
            "exact_graph_root_trace_ids": normalized_trace_ids,
            "exact_graph_root_start_us": _unix_microseconds(request_start),
            "exact_graph_root_end_us": _unix_microseconds(request_end),
            "exact_graph_root_limit": len(normalized_trace_ids),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        query = f"""
        SELECT grouped_trace_id AS trace_id
        FROM (
            SELECT
                trace_id AS grouped_trace_id,
                argMax(tuple(parent_span_id), _peerdb_version).1
                    AS latest_parent_span_id,
                argMax(start_time, _peerdb_version) AS latest_start_time,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              {project_version_fragment}
              AND trace_id IN %(exact_graph_root_trace_ids)s
            GROUP BY
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
        ) AS latest_root_spans
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          AND latest_start_time >= fromUnixTimestamp64Micro(
                %(exact_graph_root_start_us)s
              )
          AND latest_start_time < fromUnixTimestamp64Micro(
                %(exact_graph_root_end_us)s
              )
        GROUP BY grouped_trace_id
        ORDER BY grouped_trace_id
        LIMIT %(exact_graph_root_limit)s
        """
        return query, params

    def build_exact_graph_latest_root_partition(
        self,
        *,
        partition_start: datetime,
        partition_end: datetime,
        request_start: datetime,
        request_end: datetime,
        before_trace_id: str | None = None,
        limit: int = 50_001,
    ) -> tuple[str, dict[str, Any]]:
        """Enumerate authoritative live roots in one physical time range.

        Long exact attribute graphs can discover hundreds of thousands of
        matching trace IDs. Replaying all retained history once per finite ID
        batch merely to verify their roots does not scale. This cursor instead
        collapses each physical root version exactly once in disjoint whole-
        hour ranges; the reader intersects the resulting trace IDs with the
        independently authoritative attribute population.

        The partition bounds are physical pruning only. The caller's frozen
        half-open request window is re-applied to ``latest_start_time`` after
        version collapse, including for the first and last partial hour.
        """

        if self._exact_graph_authoritative_anchor_plan() is None:
            return "", {}
        if not (
            partition_start < partition_end
            and request_start < request_end
            and partition_start.minute == 0
            and partition_start.second == 0
            and partition_start.microsecond == 0
            and partition_end.minute == 0
            and partition_end.second == 0
            and partition_end.microsecond == 0
        ):
            raise ValueError(
                "exact graph root partition must use ordered whole-hour bounds"
            )
        if not 1 <= int(limit) <= 100_001:
            raise ValueError("exact graph root partition limit is invalid")

        params: dict[str, Any] = {
            **self.params,
            "exact_graph_root_partition_start_us": _unix_microseconds(partition_start),
            "exact_graph_root_partition_end_us": _unix_microseconds(partition_end),
            "exact_graph_root_start_us": _unix_microseconds(request_start),
            "exact_graph_root_end_us": _unix_microseconds(request_end),
            "exact_graph_root_partition_limit": int(limit),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        keyset_fragment = ""
        physical_keyset_fragment = ""
        if before_trace_id is not None:
            if not str(before_trace_id):
                raise ValueError("exact graph root cursor must be non-empty")
            params["exact_graph_root_after_trace_id"] = str(before_trace_id)
            keyset_fragment = (
                "AND grouped_trace_id > %(exact_graph_root_after_trace_id)s"
            )
            # Trace ID is immutable within the physical RMT identity, so the
            # cursor can prune subsequent transport pages before collapse.
            physical_keyset_fragment = (
                "AND trace_id > %(exact_graph_root_after_trace_id)s"
            )

        query = f"""
        SELECT grouped_trace_id AS trace_id
        FROM (
            SELECT
                trace_id AS grouped_trace_id,
                argMax(tuple(parent_span_id), _peerdb_version).1
                    AS latest_parent_span_id,
                argMax(start_time, _peerdb_version) AS latest_start_time,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              {project_version_fragment}
              AND toDate(start_time) >= toDate(
                    fromUnixTimestamp64Micro(%(exact_graph_root_partition_start_us)s)
                  )
              AND toDate(start_time) <= toDate(
                    fromUnixTimestamp64Micro(%(exact_graph_root_partition_end_us)s)
                  )
              AND start_time >= fromUnixTimestamp64Micro(
                    %(exact_graph_root_partition_start_us)s
                  )
              AND start_time < fromUnixTimestamp64Micro(
                    %(exact_graph_root_partition_end_us)s
                  )
              {physical_keyset_fragment}
            GROUP BY
                project_id,
                observation_type,
                service_name,
                toStartOfHour(start_time),
                trace_id,
                id
        ) AS latest_root_spans
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          AND latest_start_time >= fromUnixTimestamp64Micro(
                %(exact_graph_root_start_us)s
              )
          AND latest_start_time < fromUnixTimestamp64Micro(
                %(exact_graph_root_end_us)s
              )
          {keyset_fragment}
        GROUP BY grouped_trace_id
        ORDER BY grouped_trace_id
        LIMIT %(exact_graph_root_partition_limit)s
        """
        return query, params

    def _candidate_witness_plans(self) -> list[LatestFilterPredicate]:
        """Return positive any-span leaves safe for a finite exact prefilter.

        The probe is scoped to a caller-supplied set of at most 512 trace
        identities and a bounded time slice.  Unlike a broad raw anchor it can
        therefore replay the latest physical state, including JSON overflow,
        without weakening semantics.  Each returned leaf is only a necessary
        condition; the ordinary classifier still applies root, residual, and
        every other filter before a row can be published.
        """

        unsupported_internal_mode = (
            self._bounded_identity_only or self._bounded_internal_scan
        ) and not self.supports_filter_candidate_witness_prefilter_without_hydration()
        if (
            unsupported_internal_mode
            or self._bounded_membership_filters is not None
            or self.search
        ):
            return []

        try:
            plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        except (TypeError, ValueError):
            return []

        positive_operations = {"equals", "in", "contains"}
        candidates: list[tuple[bool, int, LatestFilterPredicate]] = []
        plan_index = 0
        for item in self._bounded_filters():
            if not isinstance(item, dict):
                return []
            key = item.get("column_id") or item.get("columnId")
            if key in {"created_at", "start_time"}:
                continue
            try:
                item_plans, item_residual = self._partition_trace_filter_plans([item])
            except (TypeError, ValueError):
                return []
            if item_residual:
                continue
            if len(item_plans) != 1 or plan_index >= len(plans):
                return []
            plan = plans[plan_index]
            plan_index += 1
            config = item.get("filter_config") or item.get("filterConfig") or {}
            if not isinstance(config, dict):
                return []
            operation = normalize_filter_op(
                str(config.get("filter_op") or config.get("filterOp") or "")
            )
            if (
                plan.scope == "any"
                and operation in positive_operations
                and plan.aggregates
                and plan.predicate
            ):
                candidates.append(
                    (
                        self._candidate_witness_filter_is_expensive(item),
                        plan_index - 1,
                        plan,
                    )
                )

        if plan_index != len(plans):
            return []
        # A conjunction needs only one necessary leaf. Prefer the structured
        # or nested leaf that made the speculative witness worthwhile; using a
        # preceding scalar leaf would recreate the broad typed-Map scan this
        # optimization deliberately avoids.
        candidates.sort(key=lambda item: (not item[0], item[1]))
        return [plan for _, _, plan in candidates]

    def _candidate_witness_anchor_plan(self) -> LatestFilterPredicate | None:
        """Return one compatible witness plan for legacy capability checks."""

        plans = self._candidate_witness_plans()
        return plans[0] if plans else None

    def supports_filter_candidate_witness_prefilter_without_hydration(self) -> bool:
        """Allow the exact membership-only historical selector optimization.

        The raw witness probe is safe without page hydration only for the
        one-project internal bulk mode whose classifier already returns its
        final identity/order projection. Witness-carrying and population proofs
        keep their established one/two-phase protocols, and graph membership-
        window scans retain their wider temporal contract.
        """

        return bool(
            self._bounded_internal_scan
            and self._bounded_identity_only
            and self._bounded_bulk_scan
            and not self._bounded_include_filter_witnesses
            and not self._bounded_population_proof
            and self._bounded_membership_filters is None
            and self.project_id is not None
            and self.project_ids is None
            and not self.search
        )

    def use_buffered_identity_filter_classification_without_hydration(self) -> bool:
        """Amortize sparse eval candidates only when the safe probe can run.

        Unlike normal list identity classification, this path must not reserve
        or issue presentation hydration. The reader retains only finite seed
        identities and still publishes exclusively exact classifier rows.
        """

        return bool(
            self.supports_filter_candidate_witness_prefilter_without_hydration()
            and self.prefer_filter_candidate_witness_probe_first()
        )

    @staticmethod
    def _candidate_witness_filter_is_selective_exact_text(
        item: dict[str, Any],
    ) -> bool:
        """Return whether one exact text leaf is selective enough to prefilter."""

        key = str(item.get("column_id") or item.get("columnId") or "")
        if key in {"created_at", "start_time"}:
            return False
        config = item.get("filter_config") or item.get("filterConfig") or {}
        if not isinstance(config, dict):
            return False
        filter_type = normalize_span_attribute_filter_type(
            str(config.get("filter_type") or config.get("filterType") or ""),
            config.get("filter_value", config.get("filterValue")),
        )
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        operation = normalize_filter_op(
            str(config.get("filter_op") or config.get("filterOp") or "")
        )
        if (
            col_type != "SPAN_ATTRIBUTE"
            or filter_type != "text"
            or operation not in {"equals", "in"}
        ):
            return False
        raw_value = config.get("filter_value", config.get("filterValue"))
        values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
        return bool(
            values
            and all(
                isinstance(value, str)
                and len(value.strip()) >= _SELECTIVE_EXACT_TEXT_MIN_LENGTH
                for value in values
            )
        )

    @staticmethod
    def _candidate_witness_filter_is_expensive(item: dict[str, Any]) -> bool:
        """Return whether one filter merits finite candidate prefiltering.

        Structured values and flattened array paths are expensive to replay.
        Long exact text values are a second important shape: they are usually
        selective (recording URLs, external IDs, full messages), so resolving
        one necessary typed-Map leaf for a finite root batch removes almost all
        candidates before the ten-root authoritative classifier. Short common
        scalar values retain the cheaper classifier-only path.
        """

        key = str(item.get("column_id") or item.get("columnId") or "")
        if key in {"created_at", "start_time"}:
            return False
        config = item.get("filter_config") or item.get("filterConfig") or {}
        if not isinstance(config, dict):
            return False
        filter_type = normalize_span_attribute_filter_type(
            str(config.get("filter_type") or config.get("filterType") or ""),
            config.get("filter_value", config.get("filterValue")),
        )
        if filter_type in {"array", "map"}:
            return True
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        if bool(
            col_type == "SPAN_ATTRIBUTE"
            and any(component.isdigit() for component in key.split("."))
        ):
            return True
        return TraceListQueryBuilder._candidate_witness_filter_is_selective_exact_text(
            item
        )

    def _interactive_candidate_witness_is_expensive(self) -> bool:
        """Return whether an interactive filter merits a speculative witness.

        A short scalar typed-Map equality such as ``final_status = Rejected`` is
        already cheaper through the exact finite identity classifier.  Its
        request-window witness cannot use the time primary key together with
        trace identity efficiently on large tenants and can exhaust the read
        byte cap before doing useful work.  Keep the optional witness for the
        shapes whose exact replay is materially heavier: structured JSON
        arrays/objects, flattened nested array paths (the numeric component in
        keys such as ``conversation.transcript.16.message.role``), and long
        exact text values that can discard nearly the whole finite root batch.
        """

        for item in self._bounded_match_filters():
            if not isinstance(item, dict):
                continue
            config = item.get("filter_config") or item.get("filterConfig") or {}
            if not isinstance(config, dict):
                continue
            operation = normalize_filter_op(
                str(config.get("filter_op") or config.get("filterOp") or "")
            )
            if operation not in {"equals", "in", "contains"}:
                continue
            if not self._candidate_witness_filter_is_expensive(item):
                continue
            try:
                item_plans, item_residual = self._partition_trace_filter_plans([item])
            except (TypeError, ValueError):
                continue
            if (
                not item_residual
                and len(item_plans) == 1
                and item_plans[0].scope == "any"
                and item_plans[0].aggregates
                and item_plans[0].predicate
            ):
                return True
        return False

    def prefer_filter_candidate_witness_probe_first(self) -> bool:
        """Use a finite global witness before expensive long-window replay."""

        request_start, request_end = self._bounded_request_window
        interactive_list = bool(
            not self._bounded_identity_only
            and not self._bounded_internal_scan
            and not self._bounded_bulk_scan
            and not self._bounded_population_proof
            and self.project_id is not None
            and self.project_ids is None
            and not self.search
        )
        return bool(
            (
                (
                    interactive_list
                    and self._interactive_candidate_witness_is_expensive()
                )
                or self.supports_filter_candidate_witness_prefilter_without_hydration()
            )
            and request_end - request_start > timedelta(hours=1)
            and self._candidate_witness_plans()
        )

    def recommended_filter_max_query_count(self) -> int | None:
        """Reserve the existing hard contract for exact sparse fallbacks."""

        return 128 if self.prefer_filter_candidate_witness_probe_first() else None

    def recommended_filter_candidate_witness_probe_strata(self) -> int | None:
        """Resolve the finite candidate batch in one all-history query."""

        if not self.prefer_filter_candidate_witness_probe_first():
            return None
        return _LONG_WINDOW_CANDIDATE_WITNESS_STRATA

    def filter_candidate_witness_replays_global_membership(self) -> bool:
        """Whether candidate-witness absence covers all child timestamps."""

        return self._candidate_witness_anchor_plan() is not None

    def recommended_filter_candidate_witness_probe_timeout_ms(self) -> int | None:
        """Give the finite latest-state anchor one normal CH statement budget."""

        if not self.prefer_filter_candidate_witness_probe_first():
            return None
        return 1_500

    def recommended_filter_candidate_witness_probe_max_bytes(self) -> int | None:
        """Bound the wider finite candidate collapse below the list read cap."""

        if not self.prefer_filter_candidate_witness_probe_first():
            return None
        return 256 * 1024 * 1024

    def recommended_filter_candidate_witness_probe_total_ms(self) -> int | None:
        """Reserve enough wall time for three sparse candidate batches."""

        if not self.prefer_filter_candidate_witness_probe_first():
            return None
        return 4_500

    def recommended_filter_candidate_witness_fallback_classify_batch_size(
        self,
    ) -> int | None:
        """Return the production-safe exact batch behind optional witnesses.

        The probe may be unavailable on a locked profile, fail one temporal
        stratum, or find a broad value. Custom span attributes retain the
        ten-identity classifier behind this optional optimization. Built-in
        and internal modes keep their independently bounded envelopes.
        """

        if self._candidate_witness_anchor_plan() is None:
            return None
        if self._custom_span_attribute_filter_count():
            return _STRUCTURED_ANY_SPAN_CLASSIFY_BATCH_SIZE
        if self.supports_filter_candidate_witness_prefilter_without_hydration():
            return _BULK_IDENTITY_CLASSIFY_BATCH_SIZE
        if not self._bounded_identity_only and not self._bounded_internal_scan:
            return _NORMAL_LIST_IDENTITY_CLASSIFY_BATCH_SIZE
        return _BULK_ANY_SPAN_CLASSIFY_BATCH_SIZE

    def build_filter_candidate_witness_probe(
        self,
        seed_rows: list[dict[str, Any]],
        *,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return finite candidates satisfying one global necessary leaf.

        Physical span versions are collapsed first and the selected leaf is
        reduced at trace scope across all history.  The candidate identities
        are finite, so this can safely prove that one required any-span leaf is
        absent without assuming a maximum trace duration. Using exactly one
        leaf matters because two filters may be satisfied by sibling spans.
        Survivors still pass the complete latest-state classifier before
        publication. ``slice_start``/``slice_end`` are accepted for backwards
        compatibility but never narrow this global membership replay.
        """

        if not isinstance(seed_rows, list) or not seed_rows or len(seed_rows) > 512:
            return "", {}
        anchor = self._candidate_witness_anchor_plan()
        if anchor is None:
            return "", {}

        org_scope = self.project_ids is not None
        if org_scope:
            allowed_projects = set(self.project_ids or ())
            identities: list[tuple[str, str]] = []
            for row in seed_rows:
                if not isinstance(row, dict):
                    return "", {}
                project_id = str(row.get("project_id") or "")
                trace_id = str(row.get("trace_id") or "")
                if not project_id or project_id not in allowed_projects or not trace_id:
                    return "", {}
                identities.append((project_id, trace_id))
            candidate_identities = tuple(dict.fromkeys(identities))
            if not candidate_identities or len(candidate_identities) > 512:
                return "", {}
            candidate_fragment = (
                "AND (project_id, trace_id) IN %(filter_candidate_trace_identities)s"
            )
            candidate_params: dict[str, Any] = {
                "filter_candidate_trace_identities": candidate_identities,
            }
            candidate_count = len(candidate_identities)
        else:
            if not self.project_id:
                return "", {}
            trace_ids: list[str] = []
            for row in seed_rows:
                if not isinstance(row, dict):
                    return "", {}
                row_project_id = row.get("project_id")
                if row_project_id and str(row_project_id) != str(self.project_id):
                    return "", {}
                trace_id = str(row.get("trace_id") or "")
                if not trace_id:
                    return "", {}
                trace_ids.append(trace_id)
            candidate_trace_ids = tuple(dict.fromkeys(trace_ids))
            if not candidate_trace_ids or len(candidate_trace_ids) > 512:
                return "", {}
            candidate_fragment = "AND trace_id IN %(filter_candidate_trace_ids)s"
            candidate_params = {
                "filter_candidate_trace_ids": candidate_trace_ids,
            }
            candidate_count = len(candidate_trace_ids)

        if (slice_start is None) != (slice_end is None):
            raise ValueError("candidate witness slice values must be provided together")
        if not anchor.aggregates or not anchor.predicate:
            return "", {}
        params: dict[str, Any] = {
            **self.params,
            **anchor.params,
            **candidate_params,
            "filter_candidate_witness_limit": candidate_count,
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"

        grouped_project_select = (
            "project_id AS grouped_project_id," if org_scope else ""
        )
        grouped_project_public = (
            "grouped_project_id AS project_id," if org_scope else ""
        )
        physical_group_by = (
            "project_id, trace_id, id, start_time"
            if org_scope
            else "trace_id, id, start_time"
        )
        aggregate_fragment = ",\n                    ".join(anchor.aggregates)
        candidate_result_fragment = (
            "AND (grouped_project_id, grouped_trace_id) "
            "IN %(filter_candidate_trace_identities)s"
            if org_scope
            else "AND grouped_trace_id IN %(filter_candidate_trace_ids)s"
        )
        query = f"""
        SELECT
            {grouped_project_public} grouped_trace_id AS trace_id
        FROM (
            SELECT
                {grouped_project_select}
                trace_id AS grouped_trace_id,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted,
                {aggregate_fragment}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              {project_version_fragment}
              {candidate_fragment}
            GROUP BY {physical_group_by}
        ) AS latest_anchor_spans
        WHERE 1 = 1
          {candidate_result_fragment}
        GROUP BY {"grouped_project_id, " if org_scope else ""}grouped_trace_id
        HAVING max(toUInt8(latest_is_deleted = 0 AND ({anchor.predicate}))) = 1
        LIMIT %(filter_candidate_witness_limit)s
        """
        return query, params

    def build_filter_ordered_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: Any = None,
        direction: str = "older",
        _positive_user_candidate_first: bool = False,
        _positive_relation_candidate_first: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Return a root-ordered superset after a common anchor sentinel.

        This path never builds an unbounded trace-id Set. Finite roots are
        classified against all any-span filters, and the reader stops only
        when the returned root prefix is mathematically closed.
        """

        if direction not in {"older", "newer"}:
            raise ValueError("trace seed direction must be older or newer")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_id is None):
            raise ValueError("trace keyset values must be provided together")
        if (
            _positive_user_candidate_first
            and not self.supports_filter_candidate_seed_page()
        ):
            raise ValueError("trace user candidate seed is unavailable")
        if (
            _positive_relation_candidate_first
            and self._positive_relational_seed_filter() is None
        ):
            raise ValueError("trace relation candidate seed is unavailable")
        if _positive_user_candidate_first and _positive_relation_candidate_first:
            raise ValueError("trace candidate seed modes are mutually exclusive")
        request_start, request_end = self.parse_time_range(self.filters)
        if not request_start <= slice_start < slice_end <= request_end:
            raise ValueError("trace seed slice must stay inside the request window")
        self.start_date, self.end_date = request_start, request_end
        self.params.update({"start_date": request_start, "end_date": request_end})
        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        # Provider-normalized root strings parse unindexed JSON. Keep ordinary
        # ordered seeds as cheap root-identity supersets; candidate
        # classification below remains the exact latest-state authority. The
        # dedicated call-type micro-seed may still opt into its raw predicate
        # on one explicitly bounded slice.
        root_plans = [
            plan
            for plan in plans
            if plan.scope == "root"
            and not self._root_plan_runs_only_in_classifier(plan)
        ]
        root_seed_plan_predicates = [
            (plan, plan.raw_witness_predicate or plan.seed_predicate)
            for plan in root_plans
        ]
        trace_id_prewhere_predicates = [
            predicate
            for _plan, predicate in root_seed_plan_predicates
            if _CANONICAL_TRACE_ID_SEED_PREDICATE.fullmatch(predicate)
        ]
        root_seed_predicates = [
            predicate
            for _plan, predicate in root_seed_plan_predicates
            if not _CANONICAL_TRACE_ID_SEED_PREDICATE.fullmatch(predicate)
        ]
        params: dict[str, Any] = {
            **self.params,
            "filter_slice_start": slice_start,
            "filter_slice_end": slice_end,
            # clickhouse-driver formats bound ``datetime`` values at whole-
            # second precision.  Keep the datetimes above for the bounded-read
            # orchestration contract, but bind epoch microseconds in SQL so a
            # row in the final fractional second is not lost at a slice edge.
            "filter_slice_start_us": _unix_microseconds(slice_start),
            "filter_slice_end_us": _unix_microseconds(slice_end),
            "filter_seed_limit": int(limit),
        }
        for plan, seed_predicate in root_seed_plan_predicates:
            params.update(
                {
                    key: value
                    for key, value in plan.params.items()
                    if f"%({key})s" in seed_predicate
                }
            )
        if _positive_user_candidate_first:
            end_user_seed_predicate, end_user_seed_params = (
                self._positive_exact_end_user_span_seed()
            )
        else:
            end_user_seed_predicate, end_user_seed_params = (
                self._positive_exact_end_user_seed()
            )
        params.update(end_user_seed_params)
        if end_user_seed_predicate and not _positive_user_candidate_first:
            root_seed_predicates.append(end_user_seed_predicate)
        relation_seed_predicate = ""
        if _positive_relation_candidate_first:
            relation_seed_predicate, relation_seed_params = (
                self._positive_relational_seed()
            )
            if not relation_seed_predicate:
                raise ValueError("trace relation candidate predicate is unavailable")
            params.update(relation_seed_params)
        root_predicate = " AND ".join(root_seed_predicates)
        predicate_fragment = f"AND {root_predicate}" if root_predicate else ""
        relation_predicate_fragment = (
            f"\n          AND ({relation_seed_predicate})"
            if relation_seed_predicate
            else ""
        )
        trace_id_prewhere_predicate = " AND ".join(trace_id_prewhere_predicates)
        trace_id_prewhere_fragment = (
            f"AND {trace_id_prewhere_predicate}" if trace_id_prewhere_predicate else ""
        )
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="trace_ordered_time_exclusion",
            )
        )
        params.update(datetime_params)
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        sampling_fragment = ""
        if self._bounded_sampling_rate is not None:
            params["bounded_sampling_salt"] = str(self._bounded_sampling_salt)
            params["bounded_sampling_rate"] = float(self._bounded_sampling_rate)
            sampling_fragment = """
              AND modulo(
                  cityHash64(%(bounded_sampling_salt)s, toString(trace_id)), 100
              ) < %(bounded_sampling_rate)s
            """
        keyset_fragment = ""
        if before_start_time is not None:
            if not slice_start <= before_start_time < slice_end:
                raise ValueError("trace keyset must stay inside its slice")
            cursor_prefix = "filter_before" if direction == "older" else "filter_after"
            params[f"{cursor_prefix}_start_us"] = _unix_microseconds(before_start_time)
            if self.project_ids is not None:
                if not (
                    isinstance(before_id, tuple)
                    and len(before_id) == 2
                    and all(isinstance(value, str) for value in before_id)
                ):
                    raise ValueError(
                        "org trace keyset must be a (trace_id, project_id) tuple"
                    )
                params[f"{cursor_prefix}_id"] = before_id[0]
                params[f"{cursor_prefix}_project_id"] = before_id[1]
                comparator = "<" if direction == "older" else ">"
                keyset_fragment = f"""
              AND (
                  toUnixTimestamp64Micro(start_time) {comparator} %({cursor_prefix}_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %({cursor_prefix}_start_us)s
                      AND (
                          trace_id {comparator} %({cursor_prefix}_id)s
                          OR (
                              trace_id = %({cursor_prefix}_id)s
                              AND toString(project_id) {comparator} %({cursor_prefix}_project_id)s
                          )
                      )
                  )
              )
            """
            else:
                params[f"{cursor_prefix}_id"] = str(before_id)
                comparator = "<" if direction == "older" else ">"
                keyset_fragment = f"""
              AND (
                  toUnixTimestamp64Micro(start_time) {comparator} %({cursor_prefix}_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %({cursor_prefix}_start_us)s
                      AND trace_id {comparator} %({cursor_prefix}_id)s
                  )
              )
            """
        identity_select = (
            "project_id, trace_id" if self.project_ids is not None else "trace_id"
        )
        order_direction = "DESC" if direction == "older" else "ASC"
        identity_order = (
            f"trace_id {order_direction}, toString(project_id) {order_direction}"
            if self.project_ids is not None
            else f"trace_id {order_direction}"
        )
        identity_limit_by = (
            "project_id, trace_id" if self.project_ids is not None else "trace_id"
        )
        candidate_cte = ""
        candidate_membership_fragment = ""
        if _positive_user_candidate_first:
            if not end_user_seed_predicate:
                raise ValueError("trace user candidate predicate is unavailable")
            candidate_cte = f"""
        WITH matching_user_trace_identities AS (
            SELECT DISTINCT project_id, trace_id
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND ({end_user_seed_predicate})
        )
            """
            candidate_membership_fragment = """
          AND (project_id, trace_id) IN (
              SELECT project_id, trace_id
              FROM matching_user_trace_identities
          )
            """
        query = f"""
        {candidate_cte}
        SELECT {identity_select}, id AS root_span_id, start_time
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND is_deleted = 0
          {project_version_fragment}
          {trace_id_prewhere_fragment}
          {candidate_membership_fragment}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)
          AND start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)
        WHERE 1 = 1
          {predicate_fragment}{relation_predicate_fragment}{datetime_fragment}
          {sampling_fragment}
          {keyset_fragment}
        ORDER BY start_time {order_direction}, {identity_order}
        LIMIT 1 BY {identity_limit_by}
        LIMIT %(filter_seed_limit)s
        """
        return query, params

    def build_filter_candidate_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Seed ordered roots from an exact positive relational candidate."""

        positive_user = self._positive_exact_end_user_seed_filter() is not None
        positive_relation = self._positive_relational_seed_filter() is not None
        if not positive_user and not positive_relation:
            # Preserve the established domain error for unsupported callers.
            raise ValueError("trace user candidate seed is unavailable")

        return TraceListQueryBuilder.build_filter_ordered_seed_page(
            self,
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
            before_start_time=before_start_time,
            before_id=before_id,
            _positive_user_candidate_first=positive_user,
            _positive_relation_candidate_first=positive_relation,
        )

    def build_filter_navigation_seed_page(
        self,
        *,
        direction: str,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        cursor_start_time: datetime | None = None,
        cursor_order_token: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return raw roots ordered outwards from an exact navigation anchor.

        ``older`` keeps the list's descending order. ``newer`` flips the SQL
        order before ``LIMIT 1 BY trace`` so each raw seed is a lower bound on
        the canonical latest-live root; reversing a descending result in Python
        would not provide that proof.
        """

        return TraceListQueryBuilder.build_filter_ordered_seed_page(
            self,
            slice_start=slice_start,
            slice_end=slice_end,
            limit=limit,
            before_start_time=cursor_start_time,
            before_id=cursor_order_token,
            direction=direction,
        )

    def build_filter_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: Any = None,
        _unindexed_positive_micro_seed: bool = False,
        _deduplicate_traces: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Return a bounded root-order superset for latest-state classification.

        ``_deduplicate_traces`` is reserved for the asynchronous exact graph
        reader. It collapses sibling-span witnesses to one trace identity per
        slice before keyset pagination; normal trace-list pagination retains
        physical witness identities and behavior unchanged.
        """

        if not self.supports_bounded_filter_scan():
            raise ValueError("unsupported bounded trace filter scan")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if (before_start_time is None) != (before_id is None):
            raise ValueError("trace keyset values must be provided together")

        request_start, request_end = self.parse_time_range(self.filters)
        plans, _ = self._partition_trace_filter_plans(self._bounded_filters())
        root_plans = [
            plan
            for plan in plans
            if plan.scope == "root"
            and not self._root_plan_runs_only_in_classifier(plan)
        ]
        any_span_plans = [plan for plan in plans if plan.scope == "any"]
        allowed_start, allowed_end = request_start, request_end
        if _deduplicate_traces and any_span_plans:
            # A trace's datetime filter applies to its canonical root. Child
            # spans that satisfy an any-span attribute may start just outside
            # that root window, so the exact graph witness walk must retain
            # the same adjacent-day envelope as the list and monolithic exact
            # graph readers. The latest-state classifier below still applies
            # the original root datetime bounds before membership is accepted.
            allowed_start -= timedelta(days=1)
            allowed_end += timedelta(days=1)
        if not allowed_start <= slice_start < slice_end <= allowed_end:
            raise ValueError("trace seed slice must stay inside the request window")
        self.start_date, self.end_date = request_start, request_end
        self.params.update({"start_date": request_start, "end_date": request_end})

        # One directly-indexable any-span leaf is a complete candidate anchor:
        # every trace satisfying all filters must contain a span satisfying
        # this leaf. The classifier below applies every leaf against global
        # latest state. Applying all leaves here would be wrong because two
        # different child spans may satisfy two different trace filters.
        if _unindexed_positive_micro_seed:
            micro_seed_plan = self._unindexed_positive_micro_seed_plan()
            if micro_seed_plan is None:
                raise ValueError("unindexed positive trace seed is unavailable")
            if slice_end - slice_start > _UNINDEXED_POSITIVE_MICRO_SEED_WIDTH:
                raise ValueError("unindexed positive trace seed exceeds micro-slice")
            seed_plans = [micro_seed_plan]
            # Unlike the key-only indexed anchor, this optional probe needs the
            # exact positive JSON predicate to reduce the candidate population.
            seed_predicates = [micro_seed_plan.seed_predicate]
        else:
            indexed_any_span_plans = [
                plan for plan in any_span_plans if self._plan_uses_indexed_anchor(plan)
            ]
            seed_plans = (
                [
                    indexed_any_span_plans[0]
                    if indexed_any_span_plans
                    else any_span_plans[0]
                ]
                if any_span_plans
                else root_plans
            )
            seed_predicates = [
                plan.raw_witness_predicate or plan.seed_predicate for plan in seed_plans
            ]
        params: dict[str, Any] = {
            **self.params,
            "filter_slice_start": slice_start,
            "filter_slice_end": slice_end,
            # Preserve DateTime64(6) slice edges across clickhouse-driver's
            # whole-second datetime interpolation.  The datetime parameters
            # remain available to bounded-reader instrumentation and fakes.
            "filter_slice_start_us": _unix_microseconds(slice_start),
            "filter_slice_end_us": _unix_microseconds(slice_end),
            "filter_seed_limit": int(limit),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        for plan, seed_predicate in zip(seed_plans, seed_predicates, strict=True):
            params.update(
                {
                    key: value
                    for key, value in plan.params.items()
                    if f"%({key})s" in seed_predicate
                }
            )

        end_user_seed_predicate, end_user_seed_params = (
            self._positive_exact_end_user_seed()
        )
        params.update(end_user_seed_params)
        if end_user_seed_predicate:
            seed_predicates.append(end_user_seed_predicate)

        predicate = " AND ".join(seed_predicates)
        predicate_fragment = f"AND {predicate}" if predicate else ""
        # Trace datetime leaves bind to the displayed root timestamp. An
        # any-span seed is only a superset, so defer the complement to root
        # classification; applying it to the matching child could hide a
        # trace whose root is valid.
        datetime_predicate = ""
        if not any_span_plans:
            datetime_predicate, datetime_params = (
                BaseQueryBuilder.bounded_datetime_exclusion_sql(
                    self.filters,
                    column="start_time",
                    param_prefix="trace_seed_time_exclusion",
                )
            )
            params.update(datetime_params)
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )

        sampling_fragment = ""
        if self._bounded_sampling_rate is not None:
            params["bounded_sampling_salt"] = str(self._bounded_sampling_salt)
            params["bounded_sampling_rate"] = float(self._bounded_sampling_rate)
            sampling_fragment = """
              AND modulo(
                  cityHash64(%(bounded_sampling_salt)s, toString(trace_id)), 100
              ) < %(bounded_sampling_rate)s
            """

        keyset_fragment = ""
        if before_start_time is not None:
            if not slice_start <= before_start_time < slice_end:
                raise ValueError("trace keyset must stay inside its slice")
            params["filter_before_start_us"] = _unix_microseconds(before_start_time)
            if _deduplicate_traces:
                if self.project_ids is not None:
                    if not (
                        isinstance(before_id, tuple)
                        and len(before_id) == 2
                        and all(isinstance(value, str) for value in before_id)
                    ):
                        raise ValueError(
                            "deduplicated org trace keyset must be a "
                            "(trace_id, project_id) tuple"
                        )
                    params["filter_before_id"] = before_id[0]
                    params["filter_before_project_id"] = before_id[1]
                    keyset_fragment = """
              AND (
                  toUnixTimestamp64Micro(start_time) < %(filter_before_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %(filter_before_start_us)s
                      AND (
                          trace_id < %(filter_before_id)s
                          OR (
                              trace_id = %(filter_before_id)s
                              AND toString(project_id) < %(filter_before_project_id)s
                          )
                      )
                  )
              )
            """
                else:
                    params["filter_before_id"] = str(before_id)
                    keyset_fragment = """
              AND (
                  toUnixTimestamp64Micro(start_time) < %(filter_before_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %(filter_before_start_us)s
                      AND trace_id < %(filter_before_id)s
                  )
              )
            """
            elif any_span_plans:
                if not (
                    isinstance(before_id, tuple)
                    and len(before_id) == 3
                    and all(isinstance(value, str) for value in before_id)
                ):
                    raise ValueError(
                        "any-span keyset must be an (id, trace_id, project_id) tuple"
                    )
                params["filter_before_id"] = before_id[0]
                params["filter_before_trace_id"] = before_id[1]
                params["filter_before_project_id"] = before_id[2]
                keyset_fragment = """
              AND (
                  toUnixTimestamp64Micro(start_time) < %(filter_before_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %(filter_before_start_us)s
                      AND (
                          id < %(filter_before_id)s
                          OR (
                              id = %(filter_before_id)s
                              AND (
                                  trace_id < %(filter_before_trace_id)s
                                  OR (
                                      trace_id = %(filter_before_trace_id)s
                                      AND toString(project_id) < %(filter_before_project_id)s
                                  )
                              )
                          )
                      )
                  )
              )
            """
            else:
                if self.project_ids is not None:
                    if not (
                        isinstance(before_id, tuple)
                        and len(before_id) == 2
                        and all(isinstance(value, str) for value in before_id)
                    ):
                        raise ValueError(
                            "org trace keyset must be a (trace_id, project_id) tuple"
                        )
                    params["filter_before_id"] = before_id[0]
                    params["filter_before_project_id"] = before_id[1]
                    keyset_fragment = """
              AND (
                  toUnixTimestamp64Micro(start_time) < %(filter_before_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %(filter_before_start_us)s
                      AND (
                          trace_id < %(filter_before_id)s
                          OR (
                              trace_id = %(filter_before_id)s
                              AND toString(project_id) < %(filter_before_project_id)s
                          )
                      )
                  )
              )
            """
                else:
                    params["filter_before_id"] = str(before_id)
                    keyset_fragment = """
              AND (
                  toUnixTimestamp64Micro(start_time) < %(filter_before_start_us)s
                  OR (
                      toUnixTimestamp64Micro(start_time) = %(filter_before_start_us)s
                      AND trace_id < %(filter_before_id)s
                  )
              )
            """

        if _deduplicate_traces:
            # Exact graphs need trace identities, not every matching sibling
            # span. Collapse the raw necessary-superset witness inside the
            # slice before applying the outer keyset. Applying the keyset to
            # raw spans first would let a trace reappear from an older sibling
            # on the next page and recreate the production fanout failure.
            deduplicated_order = (
                "ORDER BY witness_start_time DESC, trace_id DESC, "
                "toString(project_id) DESC"
                if self.project_ids is not None
                else "ORDER BY witness_start_time DESC, trace_id DESC"
            )
            deduplicated_keyset_fragment = keyset_fragment.replace(
                "(start_time)", "(witness_start_time)"
            )
            deduplicated_root_fragment = (
                ""
                if any_span_plans
                else "AND (parent_span_id IS NULL OR parent_span_id = '')"
            )
            query = f"""
        SELECT project_id, trace_id, witness_start_time AS start_time
        FROM (
            SELECT
                project_id,
                trace_id,
                max(start_time) AS witness_start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND is_deleted = 0
              {project_version_fragment}
              {deduplicated_root_fragment}
              AND start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)
              AND start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)
            WHERE 1 = 1
              {predicate_fragment}{datetime_fragment}
              {sampling_fragment}
            GROUP BY project_id, trace_id
        ) AS deduplicated_trace_witnesses
        WHERE 1 = 1
          {deduplicated_keyset_fragment}
        {deduplicated_order}
        LIMIT %(filter_seed_limit)s
        """
            return query, params

        if any_span_plans:
            select_fragment = "project_id, trace_id, id AS matched_span_id, start_time"
            root_fragment = ""
            order_fragment = (
                "ORDER BY start_time DESC, id DESC, trace_id DESC, "
                "toString(project_id) DESC"
            )
            limit_by_fragment = "LIMIT 1 BY project_id, trace_id, id, start_time"
        else:
            select_fragment = (
                "project_id, trace_id, id AS root_span_id, start_time"
                if self.project_ids is not None
                else "trace_id, id AS root_span_id, start_time"
            )
            root_fragment = "AND (parent_span_id IS NULL OR parent_span_id = '')"
            order_fragment = (
                "ORDER BY start_time DESC, trace_id DESC, toString(project_id) DESC"
                if self.project_ids is not None
                else "ORDER BY start_time DESC, trace_id DESC"
            )
            limit_by_fragment = (
                "LIMIT 1 BY project_id, trace_id"
                if self.project_ids is not None
                else "LIMIT 1 BY trace_id"
            )

        query = f"""
        SELECT {select_fragment}
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND is_deleted = 0
          {project_version_fragment}
          {root_fragment}
          AND start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s)
          AND start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s)
        WHERE 1 = 1
          {predicate_fragment}{datetime_fragment}
          {sampling_fragment}
          {keyset_fragment}
        {order_fragment}
        {limit_by_fragment}
        LIMIT %(filter_seed_limit)s
        """
        return query, params

    def exact_graph_filter_witness_range(self) -> tuple[datetime, datetime]:
        """Return the canonical-root enumeration window for an exact graph.

        Public trace time filters bind to the canonical root span, so the
        exhaustive seed walk stays exactly inside that half-open window. Once
        a finite root batch is known, the exact graph classifier uses project
        and trace identity (and ``idx_trace_id``) to inspect child witnesses at
        any timestamp. No maximum trace duration is assumed here.
        """

        request_start, request_end = self.parse_time_range(self.filters)
        return request_start, request_end

    def build_filter_match_query(
        self,
        candidate_ids: list[str],
        *,
        candidate_full_state: bool = False,
        candidate_trace_identities: list[tuple[str, str]] | None = None,
        candidate_identity_only: bool | None = None,
        include_filter_witnesses: bool = True,
        result_limit: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Classify bounded trace IDs against their latest span versions.

        The schema-specific physical identity is resolved with ``argMax`` below.
        Legacy CDC keys on exact ``start_time``; CH25 overrides the grouping to
        its deployed observation/service/start-hour RMT key, where a newer poll
        may carry a producer-corrected timestamp. One candidate-trace scan then
        selects the newest live root and evaluates any-span membership. This
        avoids both the production-timeout nested physical-ID set and a false
        negative when the raw seed root was tombstoned but another root for the
        trace remains live.
        """

        trace_ids = tuple(dict.fromkeys(str(value) for value in candidate_ids if value))
        if not trace_ids:
            return "", {}
        org_scope = self.project_ids is not None
        if self._bounded_global_span_witnesses:
            candidate_limit = (
                _EXACT_GRAPH_ORG_IDENTITY_CLASSIFY_BATCH_SIZE
                if org_scope
                else _EXACT_GRAPH_IDENTITY_CLASSIFY_BATCH_SIZE
            )
        else:
            candidate_limit = 200 if self._bounded_bulk_scan else 512
        trace_identities: tuple[tuple[str, str], ...] = ()
        if org_scope:
            if candidate_trace_identities is None:
                trace_identities = tuple(
                    (str(project_id), trace_id)
                    for project_id in self.project_ids or ()
                    for trace_id in trace_ids
                )
            else:
                trace_identities = tuple(
                    dict.fromkeys(
                        (str(project_id), str(trace_id))
                        for project_id, trace_id in candidate_trace_identities
                        if project_id and trace_id
                    )
                )
            if not trace_identities:
                return "", {}
            trace_ids = tuple(
                dict.fromkeys(trace_id for _, trace_id in trace_identities)
            )
        candidate_count = len(trace_identities) if org_scope else len(trace_ids)
        if candidate_count > candidate_limit:
            raise ValueError("candidate trace batch exceeds bounded limit")
        output_limit = candidate_count if result_limit is None else int(result_limit)
        if not 1 <= output_limit <= candidate_limit:
            raise ValueError("trace result limit exceeds bounded limit")
        if not self.supports_bounded_filter_scan():
            raise ValueError("unsupported bounded trace filter scan")

        match_filters = self._bounded_match_filters()
        request_start, request_end = BaseQueryBuilder.parse_time_range(
            match_filters, strict=True
        )
        self.start_date, self.end_date = request_start, request_end
        self.params.update({"start_date": request_start, "end_date": request_end})
        has_explicit_time_filter = any(
            (item.get("column_id") or item.get("columnId"))
            in {"created_at", "start_time"}
            for item in match_filters
        )
        # A continuous-task classifier receives identities from a separate
        # arrival/change seed.  Its default 30-day UI window is not membership:
        # an old span updated now must still be replayed against latest state.
        # Preserve an explicit user time filter, however, because that *is*
        # part of the task's selection contract.
        scope_to_request_window = not candidate_full_state or has_explicit_time_filter
        plans, residual_filters = self._partition_trace_filter_plans(match_filters)
        root_plans = [plan for plan in plans if plan.scope == "root"]
        any_span_plans = [plan for plan in plans if plan.scope == "any"]
        # Public trace time filters bind only to the canonical root. Once a
        # finite candidate trace identity is known, non-root membership must
        # inspect every current child version: ingestion enforces no maximum
        # trace duration and a child may be written days after its root. This
        # is the same contract used by exact graphs, task/eval selection, and
        # list pagination. Root-only classifiers can still prune their physical
        # scan to the request window because no child can affect membership.
        has_non_root_membership = bool(any_span_plans or residual_filters)
        scope_span_witnesses_to_request_window = (
            scope_to_request_window
            and not self._bounded_global_span_witnesses
            and not has_non_root_membership
        )
        candidate_witness_start = request_start
        candidate_witness_end = request_end
        if scope_span_witnesses_to_request_window and any_span_plans:
            candidate_witness_start -= timedelta(days=1)
            candidate_witness_end += timedelta(days=1)
        params: dict[str, Any] = {
            **self.params,
            "candidate_trace_ids": trace_ids,
        }
        if org_scope:
            params["candidate_trace_identities"] = trace_identities
        if scope_to_request_window:
            params.update(
                {
                    "candidate_start_date": request_start,
                    "candidate_end_date": request_end,
                    "candidate_start_date_us": _unix_microseconds(request_start),
                    "candidate_end_date_us": _unix_microseconds(request_end),
                }
            )
        prune_candidate_versions_to_request_window = (
            scope_span_witnesses_to_request_window
            and self.filter_classifier_has_exact_start_time_identity()
        )
        if prune_candidate_versions_to_request_window:
            params.update(
                {
                    "candidate_witness_start_date_us": _unix_microseconds(
                        candidate_witness_start
                    ),
                    "candidate_witness_end_date_us": _unix_microseconds(
                        candidate_witness_end
                    ),
                }
            )
        candidate_time_fragment = ""
        if prune_candidate_versions_to_request_window:
            # start_time is part of the immutable physical span identity. All
            # versions of a witness span therefore stay in the same daily
            # partition, so pruning outside the list/oracle's adjacent-day
            # envelope before argMax cannot hide a newer version. Canonical
            # root membership remains bound to the original request window.
            candidate_time_fragment = """
                  AND toDate(start_time) >= toDate(fromUnixTimestamp64Micro(%(candidate_witness_start_date_us)s))
                  AND toDate(start_time) <= toDate(fromUnixTimestamp64Micro(%(candidate_witness_end_date_us)s))
                  AND start_time >= fromUnixTimestamp64Micro(%(candidate_witness_start_date_us)s)
                  AND start_time < fromUnixTimestamp64Micro(%(candidate_witness_end_date_us)s)
            """
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = "AND project_version_id = %(project_version_id)s"
        for plan in plans:
            params.update(plan.params)

        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                match_filters,
                column="latest_start_time",
                param_prefix="trace_match_time_exclusion",
            )
        )
        params.update(datetime_params)
        datetime_root_fragment = (
            f"\n                    AND {datetime_predicate}"
            if datetime_predicate
            else ""
        )

        plan_aggregates = [aggregate for plan in plans for aggregate in plan.aggregates]
        plan_aggregate_fragment = (
            ",\n                        "
            + ",\n                        ".join(plan_aggregates)
            if plan_aggregates
            else ""
        )
        root_aggregate_aliases = [
            aggregate.rsplit(" AS ", 1)[1].strip()
            for plan in root_plans
            for aggregate in plan.aggregates
        ]
        if any(not alias for alias in root_aggregate_aliases):
            raise AssertionError("root latest-state aggregate requires an alias")

        if scope_to_request_window:
            canonical_root_condition = f"""(
                    latest_start_time >= fromUnixTimestamp64Micro(%(candidate_start_date_us)s)
                    AND latest_start_time < fromUnixTimestamp64Micro(%(candidate_end_date_us)s){datetime_root_fragment}
                    AND (
                        latest_parent_span_id IS NULL
                        OR latest_parent_span_id = ''
                    )
                )"""
        else:
            canonical_root_condition = """(
                    latest_parent_span_id IS NULL
                    OR latest_parent_span_id = ''
                )"""
        canonical_root_order = "tuple(latest_start_time, grouped_id)"
        canonical_root_aggregates = [
            (
                f"argMaxIf(tuple({alias}), {canonical_root_order}, "
                f"{canonical_root_condition}).1 AS {alias}"
            )
            for alias in root_aggregate_aliases
        ]
        canonical_root_aggregate_fragment = (
            ",\n                "
            + ",\n                ".join(canonical_root_aggregates)
            if canonical_root_aggregates
            else ""
        )
        root_predicate = " AND ".join(plan.predicate for plan in root_plans) or "1 = 1"

        # A request-window predicate is safe only when every membership predicate
        # is root-scoped. Any-span and residual predicates deliberately evaluate
        # globally for the finite candidate identity because no maximum trace
        # duration is enforced.
        if scope_span_witnesses_to_request_window:
            any_span_window_condition = """(
                    latest_start_time >= fromUnixTimestamp64Micro(%(candidate_start_date_us)s)
                    AND latest_start_time < fromUnixTimestamp64Micro(%(candidate_end_date_us)s)
                )"""
            any_span_having = " AND ".join(
                plan.grouped_match_predicate(any_span_window_condition)
                for plan in any_span_plans
            )
        else:
            any_span_having = " AND ".join(
                plan.grouped_match_predicate() for plan in any_span_plans
            )
        any_span_having_fragment = (
            f"\n              AND {any_span_having}" if any_span_having else ""
        )

        # Identity-only eval/task selectors must retain the exact physical
        # child span that proved each any-span filter.  A trace-level result id
        # alone cannot later bind ``final_status`` (or another child attribute)
        # to the evaluation mapping: separate leaves may be satisfied by
        # separate children, and OTel span ids can be reused across start
        # times.  Project + trace are carried by the surrounding result; each
        # tuple below preserves the remaining immutable identity fields.
        witness_selects: list[str] = []
        witness_aliases: list[str] = []
        identity_only = (
            self._bounded_identity_only
            if candidate_identity_only is None
            else bool(candidate_identity_only)
        )
        # Session graphs deliberately use the identity-only trace classifier,
        # but their reducer still needs the canonical root's session identity.
        # A session filter has already computed that latest-state value as one
        # of the root-plan aggregates, so expose the existing alias instead of
        # hydrating presentation columns or issuing another ClickHouse query.
        identity_session_alias = None
        if identity_only:
            identity_session_alias = next(
                (
                    aggregate.rsplit(" AS ", 1)[1].strip()
                    for plan in root_plans
                    for aggregate in plan.aggregates
                    if "trace_session_id" in aggregate
                ),
                None,
            )
        identity_session_public_fragment = (
            f", {identity_session_alias} AS trace_session_id"
            if identity_session_alias
            else ""
        )
        if self._bounded_identity_only and include_filter_witnesses:
            for witness_index, plan in enumerate(any_span_plans):
                witness_alias = f"filter_witness_{witness_index}"
                witness_aliases.append(witness_alias)
                if plan.exclude_group_matches:
                    # Group absence has no positive attribute span. The live
                    # canonical root is nevertheless an exact row-level
                    # is-null witness because the group proof established that
                    # no span in this trace contains the key.
                    witness_condition = canonical_root_condition
                else:
                    witness_condition = f"({plan.predicate})"
                if (
                    scope_span_witnesses_to_request_window
                    and not plan.exclude_group_matches
                ):
                    witness_condition = (
                        f"{any_span_window_condition} AND {witness_condition}"
                    )
                witness_selects.append(
                    "argMinIf("
                    "tuple(grouped_id, latest_start_time), "
                    "tuple(latest_start_time, grouped_id), "
                    f"{witness_condition}"
                    f") AS {witness_alias}"
                )
        witness_select_fragment = (
            ",\n                " + ",\n                ".join(witness_selects)
            if witness_selects
            else ""
        )
        witness_public_fragment = (
            ", " + ", ".join(witness_aliases) if witness_aliases else ""
        )

        residual_predicate = "1 = 1"
        if residual_filters:
            has_eval_residual = any(
                (item.get("column_id") or item.get("columnId")) == "has_eval"
                for item in residual_filters
            )
            if org_scope:
                requires_project_label_sets = any(
                    (item.get("column_id") or item.get("columnId")) == "has_annotation"
                    for item in residual_filters
                )
                if (
                    requires_project_label_sets
                    and self.annotation_label_ids_by_project is None
                ):
                    raise ValueError(
                        "organization has_annotation requires per-project labels"
                    )
                # A trace id is tenant-local, not globally unique.  Compile the
                # finite candidate batch into one project-scoped relational
                # branch per tenant so an eval, annotation, or end-user match
                # in project A cannot admit the same textual trace id from B.
                # All branch placeholders are namespaced because each filter
                # compiler starts its deterministic counter at one.
                identities_by_project: dict[str, list[str]] = {}
                for candidate_project_id, candidate_trace_id in trace_identities:
                    identities_by_project.setdefault(candidate_project_id, []).append(
                        candidate_trace_id
                    )

                residual_branches: list[str] = []
                for branch_index, (
                    candidate_project_id,
                    project_trace_ids,
                ) in enumerate(identities_by_project.items()):
                    branch_label_ids = self.annotation_label_ids
                    branch_label_set_known = False
                    if self.annotation_label_ids_by_project is not None:
                        if (
                            candidate_project_id
                            not in self.annotation_label_ids_by_project
                        ):
                            raise ValueError(
                                "missing annotation label scope for organization project"
                            )
                        branch_label_ids = self.annotation_label_ids_by_project[
                            candidate_project_id
                        ]
                        branch_label_set_known = True
                    residual_builder = self._FILTER_BUILDER_CLS(
                        table=self.TABLE,
                        query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
                        annotation_label_ids=branch_label_ids,
                        project_id=candidate_project_id,
                        score_date_scope=scope_span_witnesses_to_request_window,
                        span_date_scope=scope_span_witnesses_to_request_window,
                        candidate_ids_param="candidate_trace_ids",
                        strict_trace_project_correlation=True,
                        annotation_label_set_known=branch_label_set_known,
                    )
                    branch_predicate, branch_filter_params = residual_builder.translate(
                        residual_filters
                    )
                    branch_predicate = branch_predicate or "1 = 1"
                    branch_sources = {
                        **params,
                        **branch_filter_params,
                        "project_id": candidate_project_id,
                        "candidate_trace_ids": tuple(dict.fromkeys(project_trace_ids)),
                    }
                    placeholder_names = set(
                        re.findall(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s", branch_predicate)
                    )
                    for placeholder_name in sorted(placeholder_names):
                        if placeholder_name not in branch_sources:
                            raise AssertionError(
                                "unbound organization residual parameter "
                                f"{placeholder_name!r}"
                            )
                        namespaced_name = (
                            f"org_residual_{branch_index}_{placeholder_name}"
                        )
                        branch_predicate = branch_predicate.replace(
                            f"%({placeholder_name})s",
                            f"%({namespaced_name})s",
                        )
                        params[namespaced_name] = branch_sources[placeholder_name]
                    branch_project_param = (
                        f"org_residual_{branch_index}_outer_project_id"
                    )
                    params[branch_project_param] = candidate_project_id
                    residual_branches.append(
                        "(project_id = "
                        f"toUUID(%({branch_project_param})s) "
                        f"AND ({branch_predicate}))"
                    )
                residual_predicate = " OR ".join(residual_branches) or "0 = 1"
            else:
                residual_builder = self._FILTER_BUILDER_CLS(
                    table=self.TABLE,
                    query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
                    annotation_label_ids=self.annotation_label_ids,
                    project_id=self.project_id,
                    project_ids=self.project_ids,
                    score_date_scope=scope_span_witnesses_to_request_window,
                    span_date_scope=scope_span_witnesses_to_request_window,
                    candidate_ids_param="candidate_trace_ids",
                    strict_trace_project_correlation=bool(
                        self._positive_relational_seed_filter() is not None
                        or has_eval_residual
                    ),
                    trace_project_eval_config_ids=(
                        self.eval_config_ids if self._eval_config_ids_known else None
                    ),
                    strict_enduser_project_correlation=True,
                    annotation_label_set_known=self._annotation_label_set_known,
                    eval_filter_metadata=self.eval_filter_metadata,
                )
                residual_predicate, residual_params = residual_builder.translate(
                    residual_filters
                )
                params.update(residual_params)
                residual_predicate = residual_predicate or "1 = 1"

        candidate_identity_scan_fragment = (
            "AND (project_id, trace_id) IN %(candidate_trace_identities)s"
            if org_scope
            else ""
        )
        # Keep the finite candidate tuple at the physical scan only.
        # grouped_trace_id/grouped_project_id are direct aliases of those
        # scoped columns, so repeating the tuple after aggregation is
        # redundant and can push a 5k driver-expanded query beyond
        # ClickHouse's 256-KiB parser envelope.
        grouped_project_select_fragment = (
            "project_id AS grouped_project_id," if org_scope else ""
        )
        physical_group_by = self.filter_classifier_physical_group_by(
            org_scope=org_scope
        )
        trace_group_by = (
            "grouped_project_id, grouped_trace_id" if org_scope else "grouped_trace_id"
        )
        result_order = (
            "start_time DESC, trace_id DESC, toString(project_id) DESC"
            if org_scope
            else "start_time DESC, trace_id DESC"
        )

        if identity_only:
            identity_project_select = (
                "grouped_project_id AS project_id,\n                "
                if org_scope
                else ""
            )
            per_trace_select_fragment = f"""{identity_project_select}grouped_trace_id AS trace_id,
                argMaxIf(
                    tuple(grouped_id, latest_start_time),
                    {canonical_root_order},
                    {canonical_root_condition}
                ) AS canonical_root_identity{witness_select_fragment}"""
            public_select_fragment = (
                "project_id, trace_id, canonical_root_identity.1 AS root_span_id, "
                "canonical_root_identity.2 AS start_time"
                f"{identity_session_public_fragment}{witness_public_fragment}"
                if org_scope
                else (
                    "trace_id, canonical_root_identity.1 AS root_span_id, "
                    "canonical_root_identity.2 AS start_time"
                    f"{identity_session_public_fragment}{witness_public_fragment}"
                )
            )
            hydrate_root_aggregate_fragment = ""
        else:
            root_fields = (
                ("grouped_id", "root_span_id"),
                ("latest_trace_name", "trace_name"),
                ("latest_name", "span_name"),
                ("latest_observation_type", "observation_type"),
                ("latest_status", "status"),
                ("latest_start_time", "start_time"),
                ("latest_end_time", "end_time"),
                ("latest_latency_ms", "latency_ms"),
                ("latest_cost", "cost"),
                ("latest_total_tokens", "total_tokens"),
                ("latest_prompt_tokens", "prompt_tokens"),
                ("latest_completion_tokens", "completion_tokens"),
                ("latest_model", "model"),
                ("latest_provider", "provider"),
                ("latest_trace_session_id", "trace_session_id"),
                ("latest_project_id", "project_id"),
            )
            canonical_fields = [
                (
                    f"argMaxIf(tuple({source}), {canonical_root_order}, "
                    f"{canonical_root_condition}).1 AS {alias}"
                )
                for source, alias in root_fields
            ]
            per_trace_select_fragment = (
                "grouped_trace_id AS trace_id,\n                "
                + ",\n                ".join(canonical_fields)
            )
            public_select_fragment = ", ".join(
                ["root_span_id", "trace_id", *[alias for _, alias in root_fields[1:]]]
            )
            hydrate_root_aggregate_fragment = """,
                    argMax(trace_name, _peerdb_version) AS latest_trace_name,
                    argMax(name, _peerdb_version) AS latest_name,
                    argMax(observation_type, _peerdb_version)
                        AS latest_observation_type,
                    argMax(tuple(status), _peerdb_version).1 AS latest_status,
                    argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time,
                    argMax(tuple(latency_ms), _peerdb_version).1
                        AS latest_latency_ms,
                    argMax(tuple(cost), _peerdb_version).1 AS latest_cost,
                    argMax(tuple(total_tokens), _peerdb_version).1
                        AS latest_total_tokens,
                    argMax(tuple(prompt_tokens), _peerdb_version).1
                        AS latest_prompt_tokens,
                    argMax(tuple(completion_tokens), _peerdb_version).1
                        AS latest_completion_tokens,
                    argMax(tuple(model), _peerdb_version).1 AS latest_model,
                    argMax(tuple(provider), _peerdb_version).1 AS latest_provider,
                    argMax(tuple(trace_session_id), _peerdb_version).1
                        AS latest_trace_session_id,
                    argMax(project_id, _peerdb_version) AS latest_project_id"""

        query = f"""
        SELECT {public_select_fragment}
        FROM (
            SELECT
                {per_trace_select_fragment}
                {canonical_root_aggregate_fragment}
            FROM (
                SELECT
                    {grouped_project_select_fragment}
                    id AS grouped_id,
                    trace_id AS grouped_trace_id,
                    argMax(tuple(parent_span_id), _peerdb_version).1
                        AS latest_parent_span_id,
                    argMax(start_time, _peerdb_version) AS latest_start_time,
                    argMax(is_deleted, _peerdb_version) AS latest_is_deleted
                    {hydrate_root_aggregate_fragment}
                    {plan_aggregate_fragment}
                FROM {self.TABLE}
                PREWHERE {self.project_filter_sql()}
                  {project_version_fragment}
                  AND trace_id IN %(candidate_trace_ids)s
                  {candidate_identity_scan_fragment}
                  {candidate_time_fragment}
                GROUP BY {physical_group_by}
            )
            WHERE latest_is_deleted = 0
            GROUP BY {trace_group_by}
            HAVING countIf({canonical_root_condition}) > 0
              AND {root_predicate}
              {any_span_having_fragment}
        ) AS latest_candidates
        WHERE {residual_predicate}
        ORDER BY {result_order}
        LIMIT {output_limit}
        """
        return query, params

    @staticmethod
    def filter_classifier_has_exact_start_time_identity() -> bool:
        """Whether exact time bounds preserve every physical row version."""

        return True

    @staticmethod
    def filter_classifier_physical_group_by(*, org_scope: bool) -> str:
        """Return the legacy CDC replacement identity used by the classifier."""

        return (
            "project_id, trace_id, id, start_time"
            if org_scope
            else "trace_id, id, start_time"
        )

    def build_filter_navigation_target_query(
        self,
        *,
        target_id: str,
        result_limit: int = 2,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve an exact filtered navigation target by latest trace state."""

        normalized_target = str(target_id or "")
        if not normalized_target:
            raise ValueError("navigation target id must be non-empty")
        # Call the base implementation explicitly. V2 subclasses wrap every
        # public build method once; a dynamic self-call here would rewrite the
        # nested query twice before this method's outer V2 boundary sees it.
        return TraceListQueryBuilder.build_filter_match_query(
            self,
            [normalized_target],
            candidate_identity_only=True,
            include_filter_witnesses=False,
            result_limit=result_limit,
        )

    def _build_filter_match_query_from_seed_rows(
        self,
        candidate_rows: list[dict[str, Any]],
        *,
        candidate_identity_only: bool | None = None,
        include_filter_witnesses: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        """Replay root-seeded candidates by bounded trace identity.

        A raw seed root can have a newer tombstone while another physical root
        for the same trace remains live, so its root ID is deliberately not a
        classifier constraint.
        """

        trace_ids = [str(row.get("trace_id") or "") for row in candidate_rows]
        if self.project_ids is None:
            return self.build_filter_match_query(
                trace_ids,
                candidate_identity_only=candidate_identity_only,
                include_filter_witnesses=include_filter_witnesses,
            )
        trace_identities = [
            (str(row.get("project_id") or ""), str(row.get("trace_id") or ""))
            for row in candidate_rows
            if row.get("project_id") and row.get("trace_id")
        ]
        return self.build_filter_match_query(
            trace_ids,
            candidate_trace_identities=trace_identities,
            candidate_identity_only=candidate_identity_only,
            include_filter_witnesses=include_filter_witnesses,
        )

    def build_filter_match_query_from_seed_rows(
        self,
        candidate_rows: list[dict[str, Any]],
        *,
        include_filter_witnesses: bool | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if include_filter_witnesses is None:
            include_filter_witnesses = self._bounded_include_filter_witnesses
        return self._build_filter_match_query_from_seed_rows(
            candidate_rows,
            include_filter_witnesses=include_filter_witnesses,
        )

    def build_filter_identity_match_query_from_seed_rows(
        self,
        candidate_rows: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """Classify membership/order without reading presentation columns."""

        return self._build_filter_match_query_from_seed_rows(
            candidate_rows,
            candidate_identity_only=True,
            include_filter_witnesses=self._bounded_include_filter_witnesses,
        )

    def build_filter_page_hydration_query(
        self,
        candidate_rows: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate light columns for exact canonical roots on the proven page."""

        normalized_identities = tuple(
            dict.fromkeys(
                (
                    str(row.get("project_id") or self.project_id or ""),
                    str(row.get("trace_id") or ""),
                    str(row.get("root_span_id") or ""),
                    _unix_microseconds(row["start_time"]),
                )
                for row in candidate_rows
                if (row.get("project_id") or self.project_id)
                and row.get("trace_id")
                and row.get("root_span_id")
                and isinstance(row.get("start_time"), datetime)
            )
        )
        if len(normalized_identities) != len(candidate_rows):
            raise ValueError("page hydration requires exact canonical root identities")
        if not normalized_identities:
            return "", {}
        if len(normalized_identities) > 512:
            raise ValueError("page hydration exceeds bounded trace limit")

        params: dict[str, Any] = {
            **self.params,
            "page_hydration_trace_ids": tuple(
                dict.fromkeys(identity[1] for identity in normalized_identities)
            ),
            "page_hydration_root_identities": normalized_identities,
            "page_hydration_root_dates": tuple(
                dict.fromkeys(
                    (
                        row["start_time"].replace(tzinfo=UTC)
                        if row["start_time"].tzinfo is None
                        else row["start_time"].astimezone(UTC)
                    ).date()
                    for row in candidate_rows
                    if isinstance(row.get("start_time"), datetime)
                )
            ),
        }
        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = (
                "AND latest_project_version_id = %(project_version_id)s"
            )
        result_order = (
            "start_time DESC, trace_id DESC, project_id DESC"
            if self.project_ids is not None
            else "start_time DESC, trace_id DESC"
        )
        query = f"""
        SELECT
            root_span_id,
            trace_id,
            latest_trace_name AS trace_name,
            latest_name AS span_name,
            latest_observation_type AS observation_type,
            latest_status AS status,
            start_time,
            latest_end_time AS end_time,
            latest_latency_ms AS latency_ms,
            latest_cost AS cost,
            latest_total_tokens AS total_tokens,
            latest_prompt_tokens AS prompt_tokens,
            latest_completion_tokens AS completion_tokens,
            latest_model AS model,
            latest_provider AS provider,
            latest_trace_session_id AS trace_session_id,
            project_id
        FROM (
            SELECT
                toString(project_id) AS project_id,
                trace_id,
                id AS root_span_id,
                start_time,
                argMax(tuple(parent_span_id), _peerdb_version).1
                    AS latest_parent_span_id,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted,
                argMax(tuple(project_version_id), _peerdb_version).1
                    AS latest_project_version_id,
                argMax(trace_name, _peerdb_version) AS latest_trace_name,
                argMax(name, _peerdb_version) AS latest_name,
                argMax(observation_type, _peerdb_version)
                    AS latest_observation_type,
                argMax(tuple(status), _peerdb_version).1 AS latest_status,
                argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time,
                argMax(tuple(latency_ms), _peerdb_version).1 AS latest_latency_ms,
                argMax(tuple(cost), _peerdb_version).1 AS latest_cost,
                argMax(tuple(total_tokens), _peerdb_version).1
                    AS latest_total_tokens,
                argMax(tuple(prompt_tokens), _peerdb_version).1
                    AS latest_prompt_tokens,
                argMax(tuple(completion_tokens), _peerdb_version).1
                    AS latest_completion_tokens,
                argMax(tuple(model), _peerdb_version).1 AS latest_model,
                argMax(tuple(provider), _peerdb_version).1 AS latest_provider,
                argMax(tuple(trace_session_id), _peerdb_version).1
                    AS latest_trace_session_id
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND trace_id IN %(page_hydration_trace_ids)s
              AND toDate(start_time) IN %(page_hydration_root_dates)s
              AND (
                  toString(project_id), trace_id, id,
                  toUnixTimestamp64Micro(start_time)
              ) IN %(page_hydration_root_identities)s
            GROUP BY project_id, trace_id, id, start_time
        ) AS latest_page_roots
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          {project_version_fragment}
        ORDER BY {result_order}
        LIMIT {len(normalized_identities)}
        """
        return query, params

    def bounded_filter_page_hydration_identity(
        self,
        row: dict[str, Any],
    ) -> tuple[str, str, str, Any]:
        """Return the immutable canonical-root tuple used by page hydration."""

        start_time = row.get("start_time")
        return (
            str(row.get("project_id") or self.project_id or ""),
            str(row.get("trace_id") or ""),
            str(row.get("root_span_id") or ""),
            _unix_microseconds(start_time)
            if isinstance(start_time, datetime)
            else start_time,
        )

    def _span_time_window(
        self, params: dict[str, Any], column: str = "start_time"
    ) -> str:
        """Bound a page-scoped span probe to the request window ± 1 day.

        Page trace_ids come from the windowed page scan; every span of an
        in-window trace starts within the window ± max trace duration (prod
        max ≈ 5h « 1d). Empty when no build() ran (standalone callers).
        """
        if self.start_date is None:
            return ""
        params["start_date"] = self.start_date
        params["end_date"] = self.end_date
        return (
            f"AND {column} >= %(start_date)s - INTERVAL 1 DAY\n"
            f"          AND {column} < %(end_date)s + INTERVAL 1 DAY"
        )

    # ------------------------------------------------------------------
    # Phase 1: Paginated trace list
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated root-span trace data.

        Returns:
            A ``(query_string, params)`` tuple.  The query returns one row
            per trace with root-span metadata.
        """
        if self.search:
            raise ValueError(
                "unsafe legacy filtered trace read blocked: bounded_search_required"
            )
        if error_code := self.bounded_filter_degraded_error_code():
            raise ValueError(f"unsafe legacy filtered trace read blocked: {error_code}")
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Translate attribute / metric filters
        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column=TIME_FILTER_COLUMN,
                param_prefix="trace_list_time_exclusion",
            )
        )
        self.params.update(datetime_params)

        # Sorting
        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            order_clause = (
                "ORDER BY start_time DESC, trace_id DESC, project_id DESC"
                if self.project_ids is not None
                else "ORDER BY start_time DESC"
            )
        elif self.project_ids is not None:
            order_clause = f"{order_clause}, trace_id DESC, project_id DESC"

        # Prefix-fetch pagination: read the sorted prefix [0, offset +
        # 2*page_size) in ONE bounded top-K pass and let the view dedup by
        # trace id then slice [offset, offset + page_size) — see
        # tracer/services/clickhouse/page_dedup.py. Preserves the global
        # dedup `LIMIT 1 BY trace_id` provided (a trace — even a multi-root
        # one whose roots sort pages apart — can never appear on two pages)
        # without its O(window) full sort. No SQL OFFSET; slicing in Python.
        offset = self.page_number * self.page_size
        self.params["limit"] = offset + 2 * self.page_size

        # Build optional filter fragment
        filter_fragment = f"AND {extra_where}" if extra_where else ""
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )

        # Optional project_version_id filter (used by prototype tab)
        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        # Search filter on trace_name
        search_fragment = ""
        if self.search:
            search_fragment = "AND trace_name ILIKE %(search)s"
            self.params["search"] = f"%{self.search}%"

        # Configurable columns — only SELECT requested columns.
        # trace_id is always included.
        if self.columns:
            valid = [c for c in self.columns if c in self.AVAILABLE_COLUMNS]
            if "trace_id" not in valid:
                valid.insert(0, "trace_id")
            if self.project_ids is not None and "project_id" not in valid:
                valid.insert(0, "project_id")
            # Alias 'name' to 'span_name' for backward compatibility
            select_cols = []
            for c in valid:
                if c == "name":
                    select_cols.append("name AS span_name")
                else:
                    select_cols.append(c)
            select_clause = ",\n            ".join(select_cols)
        else:
            select_clause = """trace_id,
            trace_name,
            name AS span_name,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            cost,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            model,
            provider,
            trace_session_id,
            project_id"""

        # Phase 1: light columns only (no input/output/attrs/metadata).
        # Heavy columns are fetched in build_content_query() for just the
        # returned trace_ids — avoids OOM on large tables.
        #
        # PERF: no `LIMIT 1 BY trace_id`. That clause deduped multi-root /
        # duplicate-version traces, but forced CH to read + full-sort EVERY
        # root span in the window before applying ORDER BY … LIMIT —
        # O(roots-in-window) memory that OOM-crashed the server at millions
        # of traces. Without it, `ORDER BY … LIMIT n` runs as a bounded
        # top-N (size-n heap, O(n) memory). Duplicate trace_ids on a page
        # (multi-root traces, un-merged ReplacingMergeTree versions) are
        # rare; the view dedups the returned page by trace_id in Python,
        # keeping the first occurrence — the same row `LIMIT 1 BY` kept.
        query = f"""
        SELECT
            {select_clause}
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s{datetime_fragment}
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        {order_clause}
        LIMIT %(limit)s
        """
        return query, self.params

    def build_id_query(
        self,
        *,
        created_at_floor: datetime | None = None,
        created_at_ceiling: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Filtered trace ids only — same root-span predicate/window as build(),
        no pagination/order. Lets the eval resolver select the same traces this
        list endpoint returns.

        ``created_at_floor`` (continuous eval tasks only): floor the root-span
        scan on CH arrival time (``created_at``) instead of event time
        (``start_time``), so a trace whose root span landed in CH after its
        ``start_time`` is still picked up. ``None`` keeps the ``start_time``
        window used by the UI list and historical tasks.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        if created_at_floor is not None:
            # Window on arrival (created_at), not start_time. NOTE: cross-table
            # filter membership subqueries (span_date_scope) still window on
            # start_time, so a filtered task can miss an arrival whose start_time
            # predates parse_time_range's window — pre-existing residual (worse
            # before this change), tracked as a follow-up.
            self.params["created_at_floor"] = created_at_floor
            time_where = "AND created_at >= %(created_at_floor)s"
            if created_at_ceiling is not None:
                self.params["created_at_ceiling"] = created_at_ceiling
                time_where += " AND created_at < %(created_at_ceiling)s"
        else:
            time_where = (
                f"AND {TIME_FILTER_COLUMN} >= %(start_date)s "
                f"AND {TIME_FILTER_COLUMN} < %(end_date)s"
            )
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column=TIME_FILTER_COLUMN,
                param_prefix="trace_id_time_exclusion",
            )
        )
        self.params.update(datetime_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        search_fragment = ""
        if self.search:
            search_fragment = "AND trace_name ILIKE %(search)s"
            self.params["search"] = f"%{self.search}%"

        query = f"""
        SELECT trace_id
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          {time_where}{datetime_fragment}
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        LIMIT 1 BY trace_id
        """
        return query, self.params

    def build_content_query(
        self,
        trace_ids: list[str],
        *,
        root_identities: list[tuple[str, str, str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Fetch heavy columns (input, output, attributes) for a page of traces.

        Resolve every physical root to its latest version before reading heavy
        payloads.  When the bounded page supplies physical root identities,
        preserve those exact roots so a reused trace ID cannot hydrate content
        from another project/version/root between Phase 1 and Phase 1b.
        """
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "content_trace_ids": tuple(trace_ids),
        }

        normalized_identities = tuple(
            dict.fromkeys(
                (
                    str(project_id),
                    str(trace_id),
                    str(root_span_id),
                    _unix_microseconds(start_time),
                )
                for project_id, trace_id, root_span_id, start_time in (
                    root_identities or []
                )
                if project_id and trace_id and root_span_id and start_time is not None
            )
        )
        identity_fragment = ""
        if normalized_identities:
            params["content_root_identities"] = normalized_identities
            params["content_root_dates"] = tuple(
                dict.fromkeys(
                    start_time.date()
                    for _, _, _, start_time in (root_identities or [])
                    if isinstance(start_time, datetime)
                )
            )
            identity_fragment = """
              AND toDate(start_time) IN %(content_root_dates)s
              AND (
                  toString(project_id), trace_id, id,
                  toUnixTimestamp64Micro(start_time)
              )
                    IN %(content_root_identities)s
            """

        project_version_fragment = ""
        if self.project_version_id:
            params["project_version_id"] = self.project_version_id
            project_version_fragment = (
                "AND latest_project_version_id = %(project_version_id)s"
            )

        span_window = self._span_time_window(params)
        query = f"""
        SELECT
            toString(project_id) AS project_id,
            trace_id,
            latest_input AS input,
            latest_output AS output,
            latest_attrs_string AS attrs_string,
            latest_attrs_number AS attrs_number,
            latest_attrs_bool AS attrs_bool,
            latest_attributes_extra AS attributes_extra,
            toJSONString(latest_metadata) AS metadata,
            {self._trace_tags_select_sql()}
        FROM (
            SELECT
                project_id,
                trace_id,
                id AS root_span_id,
                start_time,
                argMax(tuple(parent_span_id), _peerdb_version).1
                    AS latest_parent_span_id,
                argMax(is_deleted, _peerdb_version) AS latest_is_deleted,
                argMax(tuple(project_version_id), _peerdb_version).1
                    AS latest_project_version_id,
                argMax(tuple(input), _peerdb_version).1 AS latest_input,
                argMax(tuple(output), _peerdb_version).1 AS latest_output,
                argMax(attrs_string, _peerdb_version) AS latest_attrs_string,
                argMax(attrs_number, _peerdb_version) AS latest_attrs_number,
                argMax(attrs_bool, _peerdb_version) AS latest_attrs_bool,
                argMax(tuple(attributes_extra), _peerdb_version).1
                    AS latest_attributes_extra,
                argMax(metadata, _peerdb_version) AS latest_metadata
            FROM {self.TABLE}
            PREWHERE trace_id IN %(content_trace_ids)s
              AND {self.project_filter_sql()}
              {identity_fragment}
              {span_window}
            GROUP BY project_id, trace_id, id, start_time
        ) AS latest_physical_roots
        {self._trace_tags_join_sql()}
        WHERE latest_is_deleted = 0
          AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
          {project_version_fragment}
        ORDER BY start_time DESC, root_span_id DESC
        LIMIT 1 BY project_id, trace_id
        """
        return query, params

    @staticmethod
    def _trace_tags_select_sql() -> str:
        """Return the legacy trace-tag projection used outside CH25."""

        return (
            "dictGetOrDefault('trace_dict', 'tags', toUUID(trace_id), '[]') "
            "AS trace_tags"
        )

    @staticmethod
    def _trace_tags_join_sql() -> str:
        """Return an optional source join for the trace-tag projection."""

        return ""

    def build_span_attributes_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Aggregate span attributes across all spans of each trace.

        Returns one row per trace with groupArrayDistinct for each attribute key.
        Skips raw/large content keys.
        """
        if not trace_ids:
            return "", {}

        params = {**self.params, "attr_trace_ids": tuple(trace_ids)}
        span_window = self._span_time_window(params)
        query = f"""
        SELECT
            toString(project_id) AS project_id,
            trace_id,
            attributes_extra
        FROM {self.TABLE}
        PREWHERE trace_id IN %(attr_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND attributes_extra != '{{}}'
          AND attributes_extra != ''
          {span_window}
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query to count total matching traces (for pagination).

        Returns:
            A ``(query_string, params)`` tuple returning a single count.
        """
        if self.search:
            raise ValueError(
                "unsafe legacy filtered trace count blocked: bounded_search_required"
            )
        if error_code := self.bounded_filter_degraded_error_code():
            raise ValueError(
                f"unsafe legacy filtered trace count blocked: {error_code}"
            )
        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        # Merge params -- reuse the same start/end dates
        params = dict(self.params)
        params.update(extra_params)
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column=TIME_FILTER_COLUMN,
                param_prefix="trace_count_time_exclusion",
            )
        )
        params.update(datetime_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )

        # Optional project_version_id filter
        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            params["project_version_id"] = self.project_version_id

        # Search filter (reuse from build())
        search_fragment = ""
        if self.search:
            search_fragment = "AND trace_name ILIKE %(search)s"
            params["search"] = f"%{self.search}%"

        count_identity = (
            "uniq(project_id, trace_id)"
            if self.project_ids is not None
            else "uniq(trace_id)"
        )
        query = f"""
        SELECT {count_identity} AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s{datetime_fragment}
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        """
        return query, params

    # ------------------------------------------------------------------
    # Span count per trace (optional — only if columns include span_count)
    # ------------------------------------------------------------------

    def build_span_count_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Count spans and errors per trace for a page of trace IDs."""
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "sc_trace_ids": tuple(trace_ids),
        }
        span_window = self._span_time_window(params)
        query = f"""
        SELECT
            trace_id,
            count() AS span_count,
            countIf(status = 'ERROR') AS error_count
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND trace_id IN %(sc_trace_ids)s
          {span_window}
        WHERE is_deleted = 0
        GROUP BY trace_id
        """
        return query, params

    @staticmethod
    def pivot_span_count_results(
        data: list[dict],
    ) -> dict[str, dict[str, int]]:
        """Pivot span count results into ``{trace_id: {span_count, error_count}}``."""
        result: dict[str, dict[str, int]] = {}
        for row in data:
            tid = str(row.get("trace_id", ""))
            if tid:
                result[tid] = {
                    "span_count": row.get("span_count", 0),
                    "error_count": row.get("error_count", 0),
                }
        return result

    # ------------------------------------------------------------------
    # Phase 2: Eval scores for a set of trace IDs
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-2 eval-scores query for a page of trace IDs.

        Queries ``tracer_eval_logger FINAL`` grouped by
        ``(trace_id, custom_eval_config_id)`` to produce one aggregated
        score row per (trace, eval config) pair.

        Args:
            trace_ids: List of trace ID strings from Phase 1.

        Returns:
            A ``(query_string, params)`` tuple.  Returns empty query if
            no trace_ids or no eval_config_ids.
        """
        if not trace_ids or not self.eval_config_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        eval_table, _ = self._EVAL_LOGGER_SOURCE(include_cdc_tombstone_guard=True)
        eval_version = eval_logger_version_column(eval_table)
        if eval_table.endswith("_v2"):
            status_projection = "'completed' AS status"
            skipped_reason_projection = (
                "CAST(NULL AS Nullable(String)) AS skipped_reason"
            )
        else:
            status_projection = "status"
            skipped_reason_projection = "skipped_reason"
        live_columns = eval_logger_live_state_columns(eval_table)
        live_projection = ",\n                ".join(
            f"{column} AS latest_state_{index}"
            for index, column in enumerate(live_columns)
        )
        live_predicate = " AND ".join(
            (
                f"latest_state_{index} = 0"
                if column != "deleted"
                else f"(latest_state_{index} = 0 OR latest_state_{index} IS NULL)"
            )
            for index, column in enumerate(live_columns)
        )

        # Aggregates are computed only over *completed*, non-errored rows so a
        # non-terminal (pending/running) or skipped row never skews a score nor
        # masquerades as a real value. The per-status counts let the pivot pick
        # one cell state per (trace, config) by the precedence
        # completed > errored > skipped > running > pending.
        # ``success_count`` excludes non-terminal/skipped/errored rows via
        # ``status NOT IN (...)``: a bare ``error = 0`` guard also matches
        # pending/running/skipped rows (they carry ``error = 0`` and a NULL
        # output). NOT-IN (rather than ``status = 'completed'``) keeps legacy
        # rows whose mirrored ``status`` is empty/NULL counted as completed.
        # ``str_lists`` keeps every completed ``output_str_list`` so the pivot
        # can compute per-choice percentages for CHOICES evals.
        # ``output_str`` is Nullable(String); ClickHouse 3-valued logic makes
        # ``NULL != 'ERROR'`` NULL (not TRUE), so use ``ifNull(...)`` to keep
        # the comparison NULL-safe.
        # New per-status columns are appended after ``str_lists`` so the pivot's
        # positional column fallbacks (0..7) stay valid.
        query = f"""
        SELECT
            trace_id,
            toString(custom_eval_config_id) AS eval_config_id,
            -- ifNotFinite(, NULL): avgIf over an all-NULL group returns NaN, which
            -- json.dumps(allow_nan=False) rejects. NULL serializes as null.
            ifNotFinite(avgIf(
                output_float,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS avg_score,
            ifNotFinite(avgIf(
                CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS pass_rate,
            countIf(
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS success_count,
            countIf(
                error = 1 OR ifNull(output_str, '') = 'ERROR' OR status = 'errored'
            ) AS error_count,
            count() AS eval_count,
            groupArrayIf(
                output_str_list,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS str_lists,
            countIf(status = 'skipped') AS skipped_count,
            countIf(status = 'running') AS running_count,
            countIf(status = 'pending') AS pending_count,
            anyIf(skipped_reason, status = 'skipped') AS skipped_reason
        -- Candidate-scoped latest replay: live/tombstone predicates belong
        -- outside LIMIT 1 BY id. Applying them in the inner scan resurrects an
        -- older score when its newest physical version is a deletion marker.
        FROM (
            SELECT
                trace_id,
                custom_eval_config_id,
                output_float,
                output_bool,
                output_str,
                output_str_list,
                error,
                {status_projection},
                {skipped_reason_projection},
                {live_projection}
            FROM {eval_table}
            WHERE trace_id IN %(trace_ids)s
              AND custom_eval_config_id IN %(eval_config_ids)s
            ORDER BY {eval_version} DESC
            LIMIT 1 BY id
        )
        WHERE {live_predicate}
        GROUP BY trace_id, custom_eval_config_id
        """
        return query, params

    def build_eval_replay_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Pack exact page eval cells into at most one result row per trace.

        ``build_eval_query`` returns one row per ``(trace, config)``.  The
        public API permits 500 traces and a real project may own more than ten
        eval configs, so that otherwise-correct shape can exceed the shared
        5,001 result-row guard.  Packing only the *outer* result preserves every
        exact cell and all status counters while bounding the physical result
        row count by the already-finite trace page.
        """

        query, params = self.build_eval_query(trace_ids)
        if not query:
            return "", {}
        packed_query = f"""
        SELECT
            trace_id,
            groupArray(tuple(
                eval_config_id,
                avg_score,
                pass_rate,
                success_count,
                error_count,
                eval_count,
                str_lists,
                skipped_count,
                running_count,
                pending_count,
                skipped_reason
            )) AS eval_rows
        FROM (
            {query}
        ) AS exact_eval_cells
        GROUP BY trace_id
        """
        return packed_query, params

    @staticmethod
    def expand_eval_replay_rows(
        eval_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Expand packed eval replay rows into the established pivot shape."""

        columns = (
            "eval_config_id",
            "avg_score",
            "pass_rate",
            "success_count",
            "error_count",
            "eval_count",
            "str_lists",
            "skipped_count",
            "running_count",
            "pending_count",
            "skipped_reason",
        )
        expanded: list[dict[str, Any]] = []
        for row in eval_rows:
            packed_cells = row.get("eval_rows")
            # Compatibility for callers/tests that supply the historical
            # already-expanded result shape. Production replay is packed.
            if packed_cells is None:
                expanded.append(row)
                continue
            trace_id = str(row.get("trace_id") or "")
            for cell in packed_cells or ():
                if not isinstance(cell, (list, tuple)) or len(cell) != len(columns):
                    raise ValueError("invalid packed eval replay row")
                expanded.append(
                    {
                        "trace_id": trace_id,
                        **dict(zip(columns, cell, strict=True)),
                    }
                )
        return expanded

    # ------------------------------------------------------------------
    # Phase 3: Annotations for a set of trace IDs
    # ------------------------------------------------------------------

    ANNOTATION_TABLE = "model_hub_score"

    def build_annotation_query(
        self,
        trace_ids: list[str],
        annotation_label_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build annotation query for a page of trace IDs."""
        if not trace_ids or not annotation_label_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "label_ids": tuple(annotation_label_ids),
        }
        # Bound only the spans (sp) join side; the score (s) side keeps no
        # upper bound so annotations created after the window still resolve.
        sp_window = self._span_time_window(params, column="sp.start_time")

        query = f"""
        SELECT
            if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
            ) AS trace_id,
            toString(s.label_id) AS label_id,
            anyLast(s.value) AS value,
            toString(anyLast(s.annotator_id)) AS annotator_id
        FROM {self.ANNOTATION_TABLE} AS s FINAL
        LEFT JOIN {self.TABLE} AS sp
          ON sp.id = s.observation_span_id
         AND sp._peerdb_is_deleted = 0
         {sp_window}
        WHERE s._peerdb_is_deleted = 0
          AND s.deleted = false
          AND if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
              ) IN %(trace_ids)s
          AND s.label_id IN %(label_ids)s
        GROUP BY trace_id, label_id
        """
        return query, params

    def build_user_id_query(self, trace_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch user_id strings from ClickHouse for a page of trace IDs.

        Uses enduser_dict to resolve end_user_id UUIDs to user_id strings
        in a single query. Returns one user_id per trace (uses `any()`
        aggregation to pick the first non-null value across all spans).
        """
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "user_trace_ids": tuple(trace_ids),
        }
        span_window = self._span_time_window(params)

        query = f"""
        SELECT trace_id, user_id
        FROM (
            SELECT
                trace_id,
                dictGetOrDefault('enduser_dict', 'user_id', any(end_user_id), '') AS user_id
            FROM {self.TABLE}
            PREWHERE trace_id IN %(user_trace_ids)s
            WHERE {self.project_filter_sql()}
              AND _peerdb_is_deleted = 0
              AND end_user_id IS NOT NULL
              AND end_user_id != toUUID('00000000-0000-0000-0000-000000000000')
              {span_window}
            GROUP BY trace_id
        )
        WHERE user_id != ''
        """
        return query, params

    def resolve_user_ids(self, trace_ids: list[str], analytics) -> dict[str, str]:
        """Resolve user_id strings for a page of trace IDs.

        Single-query lookup using ClickHouse enduser_dict:
        - Queries ClickHouse for user_id strings via dictionary lookup (~50-100ms)
        - No PostgreSQL round-trip needed

        Args:
            trace_ids: List of trace ID strings to resolve users for.
            analytics: Analytics service instance for executing CH queries.

        Returns:
            Dict mapping trace_id → user_id string.
        """
        if not trace_ids:
            return {}

        user_query, user_params = self.build_user_id_query(trace_ids)
        if not user_query:
            return {}

        result = analytics.execute_ch_query(user_query, user_params, timeout_ms=9_500)

        # Build trace_id → user_id mapping (filter already applied in query)
        user_id_map = {
            str(row.get("trace_id", "")): row.get("user_id")
            for row in result.data
            if row.get("user_id")
        }

        return user_id_map

    @staticmethod
    def pivot_annotation_results(
        annotation_rows: list[dict],
        label_types: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Pivot annotation results keyed by trace_id.

        Returns:
            ``{trace_id: {label_id: annotation_value}}``.
        """
        import json

        label_types = label_types or {}
        result: dict[str, dict[str, Any]] = {}
        for row in annotation_rows:
            trace_id = str(row.get("trace_id", ""))
            label_id = str(row.get("label_id", ""))
            label_type = label_types.get(label_id, "").lower()

            raw_val = row.get("value", "{}")
            if isinstance(raw_val, str):
                try:
                    val = json.loads(raw_val)
                except (json.JSONDecodeError, TypeError):
                    val = {}
            else:
                val = raw_val if isinstance(raw_val, dict) else {}

            if label_type in ("numeric", "star"):
                value_key = "value" if label_type == "numeric" else "rating"
                value = val.get(value_key) if isinstance(val, dict) else val
            elif label_type == "thumbs_up_down":
                thumb_val = val.get("value") if isinstance(val, dict) else val
                value = thumb_val in (True, "up", 1, "true")
            elif label_type == "categorical":
                value = val.get("selected", []) if isinstance(val, dict) else val
            elif label_type == "text":
                value = val.get("text", val) if isinstance(val, dict) else val
            else:
                value = val

            result.setdefault(trace_id, {})[label_id] = value

        return result

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    @staticmethod
    def pivot_eval_results(
        eval_rows: list[tuple],
        eval_columns: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Pivot eval query results into a nested dict keyed by trace_id.

        Args:
            eval_rows: Rows from the Phase-2 eval query.
            eval_columns: Column names for those rows.

        Returns:
            A dict of ``{trace_id: {eval_config_id: score_dict}}``.
        """
        result: dict[str, dict[str, Any]] = {}
        col_idx = {name: i for i, name in enumerate(eval_columns)}

        def _get(row, key, idx, default=None):
            if isinstance(row, dict):
                return row.get(key, default)
            return (
                row[col_idx.get(key, idx)]
                if len(row) > col_idx.get(key, idx)
                else default
            )

        import json as _json

        for row in eval_rows:
            trace_id = str(_get(row, "trace_id", 0, ""))
            config_id = str(_get(row, "eval_config_id", 1, ""))
            avg_score = _get(row, "avg_score", 2)
            pass_rate = _get(row, "pass_rate", 3)
            success_count = _get(row, "success_count", 4, 0) or 0
            error_count = _get(row, "error_count", 5, 0) or 0
            str_lists = _get(row, "str_lists", 7, []) or []

            # All rows errored — surface an explicit error marker so the
            # UI can render an error state (distinct from "no eval run").
            if success_count == 0 and error_count > 0:
                result.setdefault(trace_id, {})[config_id] = {"error": True}
                continue

            # CHOICES eval: compute per-choice percentage across all
            # non-errored eval rows for this (trace, config) pair. Caller
            # spreads into ``{config_id}**{choice}`` columns.
            #
            # ClickHouse stores ``output_str_list`` as ``String DEFAULT '[]'``,
            # so non-CHOICES evals (Pass/Fail, score) come back as the string
            # ``'[]'`` — truthy, slipping past the ``if not sl`` guard. Only
            # treat entries with actual choice values as CHOICES data; empty
            # inner lists must fall through to ``avg_score``/``pass_rate``.
            parsed = []
            for sl in str_lists:
                if not sl:
                    continue
                if isinstance(sl, list):
                    if sl:
                        parsed.append([str(x) for x in sl])
                elif isinstance(sl, str) and sl.startswith("["):
                    try:
                        p = _json.loads(sl)
                        if isinstance(p, list) and p:
                            parsed.append([str(x) for x in p])
                    except _json.JSONDecodeError:
                        continue
            if parsed:
                total = len(parsed)
                counts: dict[str, int] = {}
                for lst in parsed:
                    for choice in set(lst):
                        counts[choice] = counts.get(choice, 0) + 1
                per_choice = {k: round(100.0 * v / total, 2) for k, v in counts.items()}
                result.setdefault(trace_id, {})[config_id] = {
                    "per_choice": per_choice,
                }
                continue

            # ClickHouse ``avgIf`` returns NaN when no rows pass the
            # condition (or when all matching values are NULL). Python's
            # ``bool(float('nan'))`` is True, so a plain ``if avg_score``
            # guard leaks NaN into the JSON response and trips DRF's
            # strict encoder. Filter non-finite values explicitly.
            def _finite(v):
                return (
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                )

            avg_val = round(avg_score * 100, 2) if _finite(avg_score) else None
            pass_val = round(pass_rate, 2) if _finite(pass_rate) else None

            # No completed score: surface a non-terminal / skipped lifecycle
            # marker (skipped > running > pending) so the cell renders a
            # loading/pending/skipped state instead of a misleading blank.
            if avg_val is None and pass_val is None:
                marker = non_terminal_eval_marker(
                    {
                        "skipped_count": _get(row, "skipped_count", 8, 0) or 0,
                        "running_count": _get(row, "running_count", 9, 0) or 0,
                        "pending_count": _get(row, "pending_count", 10, 0) or 0,
                        "skipped_reason": _get(row, "skipped_reason", 11, None),
                    }
                )
                if marker is not None:
                    result.setdefault(trace_id, {})[config_id] = marker
                    continue

            score_data = {
                "avg_score": avg_val,
                "pass_rate": pass_val,
                "count": _get(row, "eval_count", 6, 0) or 0,
            }
            result.setdefault(trace_id, {})[config_id] = score_data

        return result
