"""
Session List Query Builder for ClickHouse.

Replaces the ``list_sessions()`` method in ``tracer.views.trace_session``
with a ClickHouse query that groups the denormalized ``spans`` table by
``trace_session_id``.

Because the ``spans`` table denormalizes trace context (including session
ID) into every span row, we can compute per-session aggregates in a single
``GROUP BY`` without JOINs.
"""

import re
from datetime import datetime, timedelta
from typing import Any

from tracer.services.clickhouse.eval_logger_table import (
    eval_logger_live_state_columns,
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.query_builders.base import (
    NIL_UUID,
    BaseQueryBuilder,
    _unix_microseconds,
)
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    build_numeric_filter_predicate,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    partition_span_filter_plans,
)
from tracer.services.clickhouse.query_builders.session_filters import (
    SESSION_ID_FILTER_COLS,
    build_session_id_filter_clause,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    bounded_survivor_map_subquery,
    remap_left_join,
    resolved_id_expr,
    survivor_map_subquery,
)

_SESSION_FILTER_ANCHOR_SENTINEL = 64
_SESSION_FILTER_ANCHOR_TIMEOUT_MS = 900
_SESSION_FILTER_ANCHOR_STRATA = 4
_SESSION_FILTER_ANCHOR_MAX_BYTES = 192 * 1024 * 1024
_USER_DETAIL_FILTER_TIMEOUT_MS = 9_500
_SESSION_ROLLUP_TABLE = "spans_per_session"


class SessionListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated session list view.

    Computes per-session aggregates:
    - ``min(start_time)`` -- session start
    - ``max(end_time)`` -- session end
    - ``sum(cost)`` -- total cost
    - ``sum(total_tokens)`` -- total tokens
    - ``uniqExact(trace_id)`` -- exact number of traces
    - ``argMin(input, start_time)`` -- first user message
    - ``argMax(input, start_time)`` -- last user message

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of sessions per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        user_id: Optional end-user ID to restrict sessions.
    """

    TABLE = "spans"
    # The v2 subclass swaps this for the CH25-aware compiler.  Keeping the
    # compiler behind a class attribute also lets the bounded session selector
    # compile candidate-scoped residual predicates without leaking v1 column
    # names into a v2-only deployment.
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilder

    # Mapping from frontend sort column names to ClickHouse expressions
    SORT_FIELD_MAP: dict[str, str] = {
        "created_at": "session_start",
        "start_time": "session_start",
        "end_time": "session_end",
        "duration": "duration",
        "total_cost": "total_cost",
        "total_tokens": "total_tokens",
        "traces_count": "traces_count",
        "total_traces_count": "traces_count",
    }

    # Session-level filter columns that map to computed aggregates
    SESSION_FILTER_MAP: dict[str, str] = {
        "duration": "duration",
        "total_cost": "total_cost",
        "total_tokens": "total_tokens",
        "traces_count": "traces_count",
        "total_traces_count": "traces_count",
    }

    MESSAGE_FILTER_MAP: dict[str, str] = {
        "first_message": "first_message",
        "last_message": "last_message",
    }

    # Aggregate projections shared by build() and build_id_query() so a HAVING on
    # any of these aliases resolves in both (build_id_query returns only session_id
    # but still applies the same HAVING).
    _AGGREGATE_SELECT = (
        "min(start_time) AS session_start, "
        "max(end_time) AS session_end, "
        "dateDiff('second', min(start_time), max(end_time)) AS duration, "
        "sum(cost) AS total_cost, "
        "sum(total_tokens) AS total_tokens, "
        "uniqExact(trace_id) AS traces_count"
    )

    _CANDIDATE_SORT_FIELDS: dict[str, str] = {
        **SORT_FIELD_MAP,
        "session": "session_id",
        "session_id": "session_id",
        "trace_session_id": "session_id",
        "first_message": "first_message",
        "last_message": "last_message",
    }

    def __init__(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        user_id: str | None = None,
        eval_config_ids: list[str] | None = None,
        annotation_label_ids: list[str] | None = None,
        annotation_label_ids_by_project: dict[str, list[str]] | None = None,
        eval_filter_metadata: dict[str, Any] | None = None,
        bounded_internal_scan: bool = False,
        bounded_sampling_salt: str | None = None,
        bounded_sampling_rate: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.user_id = user_id
        self._eval_config_ids_known = eval_config_ids is not None
        self.eval_config_ids = eval_config_ids or []
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
        request_start, request_end = self._bounded_request_window
        # ``clickhouse-driver`` renders bound datetimes at whole-second
        # precision.  An exact datetime filter is a one-microsecond half-open
        # window, so bind the authoritative bounds as epoch microseconds and
        # reconstruct DateTime64(6) in ClickHouse.
        self.params["start_date_us"] = _unix_microseconds(request_start)
        self.params["end_date_us"] = _unix_microseconds(request_end)

    def parse_time_range(
        self, filters: list[dict]
    ) -> tuple[datetime | None, datetime | None]:
        if filters is self.filters or filters == self.filters:
            return self._bounded_request_window
        return BaseQueryBuilder.parse_time_range(filters, strict=True)

    def _bounded_scalar_span_filters(self) -> list[dict[str, Any]]:
        """Return root-span predicates not handled at session level.

        Session identity, end-user membership, aggregate/message predicates and
        the request window are evaluated by the session CTEs.  Everything else
        (notably customer attributes such as ``final_status``) is compiled as a
        latest-state root-span predicate for a finite candidate-session batch.
        """

        session_columns = {
            "created_at",
            "start_time",
            "end_time",
            *self._SESSION_ID_FILTER_COLS,
            *self._ENDUSER_ID_FILTER_COLS,
            *self.SESSION_FILTER_MAP,
            *self.MESSAGE_FILTER_MAP,
        }
        return [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId")) not in session_columns
        ]

    def _bounded_span_filter_parts(self):
        return partition_span_filter_plans(
            self._bounded_scalar_span_filters(),
            group_attribute_nulls=True,
        )

    @staticmethod
    def _bounded_has_eval_values(
        residual_filters: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[bool, ...]:
        """Parse the one relational filter supported by the session classifier.

        ``has_eval`` cannot safely narrow the raw session seed: absence has no
        positive row witness.  It *can* be evaluated exactly after the seed has
        produced a finite session batch, because those sessions imply a finite
        set of tenant/window-scoped root trace IDs.  Keep this parser deliberately
        strict so malformed or newly introduced relational shapes fail closed.
        """

        values: list[bool] = []
        missing = object()
        for item in residual_filters:
            if not isinstance(item, dict):
                raise ValueError("invalid relational session filter")
            column_id = item.get("column_id") or item.get("columnId")
            if column_id != "has_eval":
                raise ValueError("unsupported relational session filter")
            config = item.get("filter_config") or item.get("filterConfig")
            if not isinstance(config, dict):
                raise ValueError("invalid has_eval session filter")
            filter_type = config.get("filter_type") or config.get("filterType")
            filter_op = config.get("filter_op") or config.get("filterOp")
            if str(filter_type or "").lower() != "boolean" or filter_op != "equals":
                raise ValueError("invalid has_eval session filter")
            raw_value = config.get("filter_value", config.get("filterValue", missing))
            if isinstance(raw_value, bool):
                value = raw_value
            elif isinstance(raw_value, str) and raw_value.strip().lower() in {
                "true",
                "false",
            }:
                value = raw_value.strip().lower() == "true"
            else:
                raise ValueError("invalid has_eval session filter")
            values.append(value)
        return tuple(values)

    @staticmethod
    def _bounded_relational_filter_groups(
        residual_filters: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split the legacy ``has_eval`` lane from other relational leaves.

        ``has_eval`` already has a purpose-built finite latest-state compiler
        whose SQL contract is pinned by the session-list tests.  Annotation,
        annotator, ``has_annotation`` and eval-value leaves share the canonical
        trace relational compiler, but are applied only after a finite session
        batch has yielded its trace IDs.
        """

        has_eval_filters: list[dict[str, Any]] = []
        generic_filters: list[dict[str, Any]] = []
        for item in residual_filters:
            if not isinstance(item, dict):
                raise ValueError("invalid relational session filter")
            column_id = item.get("column_id") or item.get("columnId")
            if column_id == "has_eval":
                has_eval_filters.append(item)
            else:
                generic_filters.append(item)
        return has_eval_filters, generic_filters

    def _validate_bounded_relational_filters(
        self,
        residual_filters: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Validate relational leaves without issuing a ClickHouse read.

        Organization-wide session pages are accepted only when every
        ``has_annotation`` branch has an authoritative project-local label set.
        The membership compiler below keeps every other relational leaf inside
        a finite per-project branch, so tenant-local trace ids never cross-match.
        """

        has_eval_filters, generic_filters = self._bounded_relational_filter_groups(
            residual_filters
        )
        self._bounded_has_eval_values(has_eval_filters)

        allowed_keys = {"annotator", "has_annotation", "my_annotations"}
        allowed_types = {"ANNOTATION", "EVAL_METRIC"}
        for item in generic_filters:
            column_id = item.get("column_id") or item.get("columnId")
            config = item.get("filter_config") or item.get("filterConfig")
            if not column_id or not isinstance(config, dict):
                raise ValueError("invalid relational session filter")
            column_type = str(
                config.get("col_type") or config.get("colType") or ""
            ).upper()
            if column_id not in allowed_keys and column_type not in allowed_types:
                raise ValueError("unsupported relational session filter")
            if column_id == "has_annotation":
                self._FILTER_BUILDER_CLS._parse_boolean_meta_filter(
                    "has_annotation",
                    config.get("filter_value", config.get("filterValue")),
                    config.get("filter_op") or config.get("filterOp"),
                )
                if self.project_ids is not None:
                    if self.annotation_label_ids_by_project is None:
                        raise ValueError(
                            "organization has_annotation requires per-project labels"
                        )
                    missing_projects = set(self.project_ids) - set(
                        self.annotation_label_ids_by_project
                    )
                    if missing_projects:
                        raise ValueError(
                            "missing annotation label scope for organization project"
                        )
        return has_eval_filters, generic_filters

    def _bounded_relational_membership_plan(
        self,
        relational_filters: list[dict[str, Any]],
        *,
        scope_to_request_window: bool,
        available_params: dict[str, Any],
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        """Compile exact trace predicates over a finite derived trace set.

        The shared trace filter compiler normally receives candidate trace IDs
        as a Python parameter.  Session classification starts from session IDs,
        so those trace IDs exist only inside ``resolved_root_sessions``.  This
        method replaces the compiler's trusted internal candidate placeholder
        with a CTE over that already-bounded relation.  Any relational leaf
        containing a table read but lacking that candidate guard is rejected;
        this prevents an accidental whole-project score/eval scan.
        """

        if not relational_filters:
            return "", (), {}
        candidate_param = "session_relational_trace_ids"
        candidate_placeholder = f"%({candidate_param})s"
        merged_params: dict[str, Any] = {}
        needs_candidate_cte = False
        org_scope = self.project_ids is not None
        branch_projects = self.project_ids if org_scope else [self.project_id]
        branch_predicates: list[str] = []

        for branch_index, branch_project_id in enumerate(branch_projects):
            if not branch_project_id:
                raise ValueError("relational session filter requires a project scope")
            branch_label_ids = self.annotation_label_ids
            branch_label_set_known = self._annotation_label_set_known
            if org_scope and self.annotation_label_ids_by_project is not None:
                if branch_project_id not in self.annotation_label_ids_by_project:
                    raise ValueError(
                        "missing annotation label scope for organization project"
                    )
                branch_label_ids = self.annotation_label_ids_by_project[
                    branch_project_id
                ]
                branch_label_set_known = True

            leaf_predicates: list[str] = []
            for leaf_index, item in enumerate(relational_filters):
                filter_builder = self._FILTER_BUILDER_CLS(
                    table=self.TABLE,
                    annotation_label_ids=branch_label_ids,
                    query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_TRACE,
                    project_id=branch_project_id,
                    score_date_scope=scope_to_request_window,
                    span_date_scope=scope_to_request_window,
                    candidate_ids_param=candidate_param,
                    strict_trace_project_correlation=(
                        org_scope
                        or (item.get("column_id") or item.get("columnId")) == "has_eval"
                    ),
                    trace_project_eval_config_ids=(
                        self.eval_config_ids
                        if self._eval_config_ids_known and not org_scope
                        else None
                    ),
                    annotation_label_set_known=branch_label_set_known,
                    eval_filter_metadata=(
                        self.eval_filter_metadata if not org_scope else None
                    ),
                )
                predicate, leaf_params = filter_builder.translate([item])
                if not predicate:
                    raise ValueError("relational session filter compiled no predicate")

                normalized_sql = f" {' '.join(predicate.upper().split())} "
                used_candidate_scope = candidate_placeholder in predicate
                if " FROM " in normalized_sql and not used_candidate_scope:
                    raise ValueError(
                        "relational session filter is missing finite candidate scope"
                    )
                if used_candidate_scope:
                    candidate_select = (
                        "(SELECT trace_id FROM candidate_relational_trace_ids)"
                    )
                    if org_scope:
                        candidate_select = (
                            "(SELECT trace_id FROM candidate_relational_trace_ids "
                            "WHERE project_id = toUUID(%(project_id)s))"
                        )
                    predicate = predicate.replace(
                        candidate_placeholder, candidate_select
                    )
                    needs_candidate_cte = True

                # Each leaf compiler starts its deterministic parameter counter
                # at one. Namespace branch-local values before combining leaves;
                # request window values intentionally remain shared.
                branch_sources = {
                    **available_params,
                    **leaf_params,
                    "project_id": branch_project_id,
                }
                for placeholder_name in sorted(
                    set(re.findall(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s", predicate))
                ):
                    if placeholder_name in leaf_params or (
                        org_scope and placeholder_name == "project_id"
                    ):
                        if org_scope:
                            namespaced_name = (
                                f"session_relational_{branch_index}_{leaf_index}_"
                                f"{placeholder_name}"
                            )
                        else:
                            namespaced_name = (
                                f"session_relational_{leaf_index}_{placeholder_name}"
                            )
                        predicate = predicate.replace(
                            f"%({placeholder_name})s", f"%({namespaced_name})s"
                        )
                        merged_params[namespaced_name] = branch_sources[
                            placeholder_name
                        ]
                    elif placeholder_name not in branch_sources:
                        raise AssertionError(
                            f"unbound session relational parameter {placeholder_name!r}"
                        )
                leaf_predicates.append(f"({predicate})")

            branch_predicate = " AND ".join(leaf_predicates) or "1 = 1"
            if org_scope:
                outer_project_param = (
                    f"session_relational_{branch_index}_outer_project_id"
                )
                merged_params[outer_project_param] = branch_project_id
                branch_predicate = (
                    "(project_id = "
                    f"toUUID(%({outer_project_param})s) "
                    f"AND ({branch_predicate}))"
                )
            branch_predicates.append(branch_predicate)

        ctes = ""
        if needs_candidate_cte:
            candidate_project_select = ""
            if org_scope:
                candidate_project_select = "project_id, "
            ctes = f""",
        candidate_relational_trace_ids AS (
            SELECT DISTINCT {candidate_project_select}toString(trace_id) AS trace_id
            FROM resolved_root_sessions
            WHERE notEmpty(toString(trace_id))
        )"""
        combined_predicate = (
            " OR ".join(branch_predicates)
            if org_scope
            else " AND ".join(branch_predicates)
        )
        return ctes, (f"({combined_predicate})",), merged_params

    def _bounded_eval_membership_ctes(
        self,
        *,
        has_eval_values: tuple[bool, ...],
        scope_to_request_window: bool,
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        """Return finite latest-eval CTEs and root-trace membership predicates.

        The only eval-table read is keyed by trace IDs derived from the already
        candidate-scoped ``resolved_root_sessions`` relation.  Latest state is
        collapsed by eval identity before the live predicate is applied, so a
        tombstone cannot resurrect an older eval.  The predicate is deliberately
        applied to root traces before session aggregation: this preserves the
        established session-list rule that a session matches ``has_eval=false``
        when it contains at least one root trace without an eval.
        """

        if not has_eval_values:
            return "", (), {}
        scoped_config_ids = (
            tuple(self.eval_config_ids) if self._eval_config_ids_known else None
        )
        if scoped_config_ids is None:
            from tracer.models.custom_eval_config import CustomEvalConfig

            project_ids = self.project_ids or (
                [self.project_id] if self.project_id else []
            )
            scoped_config_ids = tuple(
                str(config_id)
                for config_id in CustomEvalConfig.objects.filter(
                    project_id__in=project_ids,
                    deleted=False,
                ).values_list("id", flat=True)
            )
            # The builder is reused across every finite classifier batch in
            # one public session-page proof. Freeze the project-scoped lookup
            # after its first compilation so a 1,000-batch walk cannot issue
            # 1,000 identical PostgreSQL reads. Explicit empty remains a known
            # empty set and therefore never falls back again.
            self.eval_config_ids = list(scoped_config_ids)
            self._eval_config_ids_known = True
        eval_params: dict[str, Any] = {}
        if scoped_config_ids:
            eval_params["session_project_eval_config_ids"] = scoped_config_ids
            eval_project_scope = (
                "\n              AND eval_scan.custom_eval_config_id IN "
                "%(session_project_eval_config_ids)s"
            )
        else:
            # An explicit empty project config set is authoritative. Keep the
            # finite CTE shape so positive/negative predicates retain their
            # existing set semantics without reading unrelated eval rows.
            eval_project_scope = "\n              AND 0 = 1"
        eval_table, _ = eval_logger_source()
        eval_version = eval_logger_version_column(eval_table)
        eval_live_columns = eval_logger_live_state_columns(eval_table)
        _, eval_live_predicate = eval_logger_source(
            "latest_eval", include_cdc_tombstone_guard=True
        )
        live_projection = ",\n                ".join(
            f"eval_scan.{column}" for column in eval_live_columns
        )
        eval_date_scope = (
            "\n              AND eval_scan.created_at >= "
            "fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC') - INTERVAL 7 DAY"
            if scope_to_request_window
            else ""
        )
        ctes = f""",
        candidate_eval_trace_ids AS (
            SELECT DISTINCT trace_id
            FROM resolved_root_sessions
            WHERE isNotNull(trace_id)
        ),
        latest_candidate_evals AS (
            SELECT
                eval_scan.id,
                eval_scan.trace_id,
                {live_projection}
            FROM {eval_table} AS eval_scan
            PREWHERE eval_scan.trace_id IN (
                SELECT trace_id FROM candidate_eval_trace_ids
            ){eval_project_scope}{eval_date_scope}
            ORDER BY eval_scan.{eval_version} DESC
            LIMIT 1 BY eval_scan.id
        ),
        live_candidate_eval_trace_ids AS (
            SELECT DISTINCT toString(latest_eval.trace_id) AS trace_id
            FROM latest_candidate_evals AS latest_eval
            WHERE {eval_live_predicate}
              AND isNotNull(latest_eval.trace_id)
        )"""
        predicates = tuple(
            "trace_id "
            + ("IN" if value else "NOT IN")
            + " (SELECT trace_id FROM live_candidate_eval_trace_ids)"
            for value in has_eval_values
        )
        return ctes, predicates, eval_params

    @staticmethod
    def _bounded_root_witness_plan(plans: list[Any] | tuple[Any, ...]):
        """Choose the most selective safe any-span witness deterministically."""

        candidates = [
            (
                getattr(plan, "raw_witness_rank", None)
                if getattr(plan, "raw_witness_rank", None) is not None
                else 100,
                index,
                plan,
            )
            for index, plan in enumerate(plans)
            if getattr(plan, "raw_witness_predicate", None) is not None
        ]
        return min(candidates, default=(None, None, None))[-1]

    def bounded_filter_degraded_error_code(self) -> str | None:
        """Explain why the finite session bulk selector cannot represent a shape."""

        if self.sort_params:
            # Bulk selection has a fixed newest-session order.  An arbitrary UI
            # sort would require a global aggregate before a finite prefix can
            # be proven.
            return "unsupported_filter_modifiers"
        try:
            _, residual = self._bounded_span_filter_parts()
        except (TypeError, ValueError):
            return "unsupported_filter_shape"
        if residual:
            try:
                self._validate_bounded_relational_filters(residual)
            except ValueError as exc:
                if (
                    "unsupported relational" in str(exc)
                    or "per-project labels" in str(exc)
                    or "annotation label scope" in str(exc)
                ):
                    return "unsupported_relational_session_filter"
                return "unsupported_filter_shape"
        return None

    def supports_bounded_filter_scan(self) -> bool:
        active = any(
            (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
            for item in self.filters
        )
        return (
            active or self._bounded_internal_scan
        ) and self.bounded_filter_degraded_error_code() is None

    @staticmethod
    def recommended_filter_seed_batch_size() -> int:
        return 200

    @staticmethod
    def recommended_filter_classify_batch_size() -> int:
        # Attribute argMax replay still touches complete Map state for every
        # physical root in the candidate sessions.  Production's largest
        # tenant exceeded the endpoint deadline at 200; keep seed acquisition
        # broad and split only the exact latest-state replay.
        return 50

    @staticmethod
    def filter_seed_proves_result_order() -> bool:
        """The newest raw root is an upper bound for a session's true start.

        Classification returns ``min(live root start_time)``.  An unseen
        session whose newest raw root is below a proved cutoff cannot move ahead
        of that cutoff after latest-version/tombstone replay.
        """

        return True

    def supports_filter_candidate_seed_page(self) -> bool:
        """Use the per-session rollup for the ordinary newest-session cursor.

        The prior default selector replayed every physical root version in the
        requested window before applying ``LIMIT``. On dense tenants that made
        even page one proportional to the full span population. The rollup is
        already keyed at session grain and carries a mergeable first-seen
        state, so it can acquire a small ordered candidate page before the
        existing finite latest-state classifier validates membership.

        Keep aggregate/message/user/custom filters and custom sorts on their
        existing exact bounded paths; this seed is only the unfiltered/date-
        filtered default list.
        """

        if self.sort_params or self._bounded_sampling_rate is not None:
            return False
        return all(
            (item.get("column_id") or item.get("columnId"))
            in {"created_at", "start_time"}
            for item in self.filters
        )

    @staticmethod
    def filter_candidate_seed_proves_result_order() -> bool:
        """The rollup first-seen state orders the candidate session stream.

        The public sampled lane deliberately keeps this insert-only order after
        exact classification. Historical live versions can add false-positive/
        older candidates, but cannot hide a current live session; the
        authoritative finite classifier removes tombstones and resolves remaps
        before publication without replacing the seed's cursor tuple.
        """

        return True

    @staticmethod
    def bounded_filter_row_order_token(row: dict[str, Any]) -> str:
        """Keep rollup-seeded pages on their raw, insert-only cursor order.

        Exact latest-state replay may move a session's physical ``start_time``
        forward (for example after a historical root is tombstoned) and may
        remap its raw UUID to a canonical UUID. Neither correction may rewrite
        the signed rollup cursor: doing so can reject an older seed as being
        newer than the previous page and skip that live session forever.
        Non-rollup classifiers do not carry the hidden token and retain their
        existing canonical-ID order.
        """

        return str(row.get("_seed_order_id") or row.get("session_id") or "")

    def recommended_filter_initial_slice_width(self) -> timedelta | None:
        if not self.supports_filter_candidate_seed_page():
            return None
        start, end = self._bounded_request_window
        return end - start

    def recommended_filter_cursor_seed_batch_size(self) -> int | None:
        """Oversample the cheap rollup before exact latest-state replay."""

        return 101 if self.supports_filter_candidate_seed_page() else None

    def recommended_filter_max_slice_width(self) -> timedelta | None:
        return self.recommended_filter_initial_slice_width()

    def filter_candidate_seed_is_sampled(self) -> bool:
        """Expose that an insert-only rollup supplied candidate ordering."""

        return self.supports_filter_candidate_seed_page()

    def build_filter_candidate_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return one ordered, session-grain rollup candidate page."""

        if not self.supports_filter_candidate_seed_page():
            raise ValueError("session rollup candidate seed is unavailable")
        if limit <= 0 or limit > 512:
            raise ValueError("session rollup seed limit must be between 1 and 512")
        if (before_start_time is None) != (before_id is None):
            raise ValueError("session rollup keyset values must be provided together")
        request_start, request_end = self._bounded_request_window
        if not request_start <= slice_start < slice_end <= request_end:
            raise ValueError("session rollup slice must stay inside request window")

        params: dict[str, Any] = {
            **self.params,
            "filter_slice_start_us": _unix_microseconds(slice_start),
            "filter_slice_end_us": _unix_microseconds(slice_end),
            "filter_seed_limit": int(limit),
        }
        keyset_clause = ""
        if before_start_time is not None:
            params["filter_before_start_time_us"] = _unix_microseconds(
                before_start_time
            )
            params["filter_before_session_id"] = str(before_id)
            keyset_clause = """
          AND (
              start_time < fromUnixTimestamp64Micro(
                  %(filter_before_start_time_us)s, 'UTC'
              )
              OR (
                  start_time = fromUnixTimestamp64Micro(
                      %(filter_before_start_time_us)s, 'UTC'
                  )
                  AND toString(session_id) < %(filter_before_session_id)s
              )
          )
            """

        query = f"""
        WITH rollup_sessions AS (
            SELECT
                trace_session_id AS session_id,
                minMerge(first_seen) AS start_time
            FROM {_SESSION_ROLLUP_TABLE}
            PREWHERE {self.project_filter_sql()}
              AND hour_first_seen >= toStartOfHour(
                  fromUnixTimestamp64Micro(%(filter_slice_start_us)s, 'UTC')
              )
              AND hour_first_seen < toStartOfHour(
                  fromUnixTimestamp64Micro(%(filter_slice_end_us)s, 'UTC')
              ) + INTERVAL 1 HOUR
            GROUP BY trace_session_id
        )
        SELECT session_id, start_time
        FROM rollup_sessions
        WHERE start_time >= fromUnixTimestamp64Micro(
                  %(filter_slice_start_us)s, 'UTC'
              )
          AND start_time < fromUnixTimestamp64Micro(
                  %(filter_slice_end_us)s, 'UTC'
              )
          {keyset_clause}
        ORDER BY start_time DESC, toString(session_id) DESC
        LIMIT %(filter_seed_limit)s
        """
        return query, params

    def supports_filter_anchor_probe(self) -> bool:
        """Use one positive any-span leaf only as an optional sparse probe.

        The ordinary seed is deliberately root ordered so it can prove the
        newest public page.  A positive raw witness cannot provide that order,
        but an exhausted finite sentinel *does* provide the complete candidate
        set.  The selector still replays every returned session through exact
        latest state before publication; a full/slow sentinel is discarded and
        falls back to the unchanged root-ordered path.
        """

        if self._bounded_sampling_rate is not None:
            # Sampling is defined on the remap-resolved public session ID.  A
            # raw-alias witness must not change that internal hash population.
            return False
        # A sole positive end-user filter is already an indexed, remap-bounded
        # membership seed. Do not put the generic 900 ms speculative anchor in
        # front of the exact CrossProjectUserDetailPage query.
        if self._positive_exact_end_user_detail_filter():
            return False
        try:
            plans, residual = self._bounded_span_filter_parts()
            self._validate_bounded_relational_filters(residual)
        except (TypeError, ValueError):
            return False
        return self._bounded_root_witness_plan(plans) is not None

    def recommended_filter_query_timeout_ms(self) -> int | None:
        """Use the request's remaining wall time for public session filters.

        Finite candidate, query-count, byte, memory, thread and result controls
        remain authoritative. Optional anchor probes retain their shorter caps.
        """

        return _USER_DETAIL_FILTER_TIMEOUT_MS

    def recommended_filter_anchor_probe_limit(self) -> int | None:
        if not self.supports_filter_anchor_probe():
            return None
        return _SESSION_FILTER_ANCHOR_SENTINEL

    def recommended_filter_anchor_probe_timeout_ms(self) -> int | None:
        if not self.supports_filter_anchor_probe():
            return None
        return _SESSION_FILTER_ANCHOR_TIMEOUT_MS

    def recommended_filter_anchor_probe_strata(self) -> int | None:
        if not self.supports_filter_anchor_probe():
            return None
        return _SESSION_FILTER_ANCHOR_STRATA

    def recommended_filter_anchor_probe_max_bytes_to_read(self) -> int | None:
        if not self.supports_filter_anchor_probe():
            return None
        return _SESSION_FILTER_ANCHOR_MAX_BYTES

    def build_filter_anchor_probe(
        self,
        *,
        limit: int,
        slice_start: datetime | None = None,
        slice_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return a finite raw any-span witness sentinel for sparse filters.

        Every latest-live match necessarily has a physical live row satisfying
        the chosen positive witness.  Historical matches are harmless false
        positives because ``build_filter_match_query`` remains authoritative.
        """

        if limit <= 0 or limit > 512:
            # Partitioned probes share one aggregate sentinel.  Earlier strata
            # may consume all but its final slot, so the last statement must be
            # allowed to ask for exactly one row; returning it means the shared
            # sentinel was reached and the exact ordered fallback takes over.
            raise ValueError(
                "session anchor limit must stay inside the bounded sentinel"
            )
        request_start, request_end = self.parse_time_range(self.filters)
        if (slice_start is None) != (slice_end is None):
            raise ValueError("session anchor slice values must be provided together")
        anchor_start = request_start if slice_start is None else slice_start
        anchor_end = request_end if slice_end is None else slice_end
        if not request_start <= anchor_start < anchor_end <= request_end:
            raise ValueError("session anchor slice must stay inside request window")

        plans, residual = self._bounded_span_filter_parts()
        self._validate_bounded_relational_filters(residual)
        anchor = self._bounded_root_witness_plan(plans)
        if anchor is None or not anchor.raw_witness_predicate:
            raise ValueError("session anchor requires a positive scalar witness")
        witness = anchor.raw_witness_predicate
        params: dict[str, Any] = {
            **self.params,
            **{
                key: value
                for key, value in anchor.params.items()
                if f"%({key})s" in witness
            },
            "filter_anchor_start": anchor_start,
            "filter_anchor_end": anchor_end,
            "filter_anchor_start_us": _unix_microseconds(anchor_start),
            "filter_anchor_end_us": _unix_microseconds(anchor_end),
            "filter_anchor_limit": int(limit),
        }
        query = f"""
        SELECT
            trace_session_id AS session_id,
            start_time
        FROM {self.TABLE}
        PREWHERE {self.project_filter_sql()}
          AND _peerdb_is_deleted = 0
          AND start_time >= fromUnixTimestamp64Micro(%(filter_anchor_start_us)s, 'UTC')
          AND start_time < fromUnixTimestamp64Micro(%(filter_anchor_end_us)s, 'UTC')
        WHERE isNotNull(trace_session_id)
          AND trace_session_id != toUUID('{NIL_UUID}')
          AND ({witness})
        ORDER BY
            observation_type DESC,
            service_name DESC,
            toStartOfHour(start_time) DESC,
            trace_id DESC,
            id DESC,
            start_time DESC
        LIMIT 1 BY trace_session_id
        LIMIT %(filter_anchor_limit)s
        """
        return query, params

    def supports_candidate_first_page(self) -> bool:
        """Return true for the exact root-time ordered fast path.

        The physical-root selector below proves the default/created-at ordering.
        Session-id filters are applied to remap-resolved root IDs before paging.
        Positive end-user filters use a user-ID-keyed membership selector;
        negated/null filters use the same project/time-scoped latest-state
        membership stream without unsafe ID pruning. Session aggregate/message
        predicates and their sorts are computed from the narrow physical-latest
        root stream before paging. Arbitrary span/eval/annotation filters remain
        off the list path; the internal bulk selector can classify scalar span
        filters after it has a finite session-ID batch.
        """

        for item in self.filters:
            column_id = item.get("column_id") or item.get("columnId")
            if column_id in {"created_at", "start_time"}:
                continue
            if column_id in self._SESSION_ID_FILTER_COLS:
                continue
            if column_id in self._ENDUSER_ID_FILTER_COLS:
                config = item.get("filter_config") or item.get("filterConfig") or {}
                operator = config.get("filter_op") or config.get("filterOp")
                if operator in {
                    "equals",
                    "in",
                    "not_equals",
                    "not_in",
                    "is_null",
                    "is_not_null",
                }:
                    continue
            if (
                column_id in self.SESSION_FILTER_MAP
                or column_id in self.MESSAGE_FILTER_MAP
                or column_id == "end_time"
            ):
                continue
            return False
        return all(
            (item.get("column_id") or item.get("columnId"))
            in self._CANDIDATE_SORT_FIELDS
            and str(item.get("direction") or "desc").lower() in {"asc", "desc"}
            for item in self.sort_params
        )

    def supports_candidate_cursor_page(self) -> bool:
        """Use the exact keyset fast path for a finite positive identity seed.

        Cursor mode normally uses the generic bounded classifier so arbitrary
        span predicates can publish a resumable prefix.  A user-detail page is
        different: the view has already resolved its external ``user_id`` to a
        positive ``end_user_id`` set, and ``_candidate_session_ctes`` can apply
        that selective membership before reading root sessions.  Sending this
        shape through the generic root scan makes a sparse user search replay
        unrelated roots until its wall deadline.

        An explicit positive session-ID filter is even narrower.  The selected
        IDs (including remap aliases) are a finite authorization-scoped seed,
        so every other candidate-safe session/user predicate can be evaluated
        against only those sessions.  Keeping that shape on the generic scan
        path turns a one-session lookup into a project-wide 12-month search.

        Keep both exceptions deliberately narrow.  Without a positive session
        seed, negative/null user filters, extra session/span predicates, and
        custom sorts retain the existing bounded path and its semantics.
        """

        if self.sort_params or not self.supports_candidate_first_page():
            return False
        return (
            bool(self._candidate_positive_filter_values(self._SESSION_ID_FILTER_COLS))
            or self._positive_exact_end_user_detail_filter()
        )

    def _positive_exact_end_user_detail_filter(self) -> bool:
        """Recognize only the structural user-detail membership predicate."""

        active_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            not in {"created_at", "start_time"}
        ]
        if len(active_filters) != 1:
            return False
        item = active_filters[0]
        if (item.get("column_id") or item.get("columnId")) not in (
            self._ENDUSER_ID_FILTER_COLS
        ):
            return False
        config = item.get("filter_config") or item.get("filterConfig") or {}
        operator = config.get("filter_op") or config.get("filterOp")
        raw_values = config.get("filter_value", config.get("filterValue"))
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        return operator in {"equals", "in"} and any(values)

    def _candidate_order_clause(self) -> str:
        if not self.sort_params:
            return "ORDER BY session_start DESC, session_id DESC"
        parts: list[str] = []
        sorted_by_session_id = False
        tie_direction = "DESC"
        for item in self.sort_params:
            column_id = item.get("column_id") or item.get("columnId")
            column = self._CANDIDATE_SORT_FIELDS[column_id]
            direction = str(item.get("direction") or "desc").upper()
            tie_direction = direction
            parts.append(f"{column} {direction}")
            sorted_by_session_id = sorted_by_session_id or column == "session_id"
        if not sorted_by_session_id:
            # A total order is required for stable numbered pages.
            parts.append(f"session_id {tie_direction}")
        return f"ORDER BY {', '.join(parts)}"

    def _candidate_positive_filter_values(
        self, columns: frozenset[str]
    ) -> tuple[str, ...]:
        """Return positive equality/IN IDs used to prune candidate identities."""

        values: list[str] = []
        for item in self.filters:
            column_id = item.get("column_id") or item.get("columnId")
            if column_id not in columns:
                continue
            config = item.get("filter_config") or item.get("filterConfig") or {}
            operator = config.get("filter_op") or config.get("filterOp")
            if operator not in {"equals", "in"}:
                continue
            raw = config.get("filter_value", config.get("filterValue"))
            raw_values = raw if isinstance(raw, list) else [raw]
            values.extend(str(value) for value in raw_values if value)
        return tuple(dict.fromkeys(values))

    def _candidate_survivor_map_sql(
        self,
        params: dict[str, Any],
        session_ids: tuple[str, ...],
    ) -> str:
        """Return an exact survivor map for one finite session-ID set.

        The remap table is ordered only by ``old_id``. Resolve old-ID inputs with
        a primary-key point probe, then perform one authoritative reverse
        ``new_id`` pass for both old and new inputs. The caller materializes this
        relation once as a scalar tuple array, so ClickHouse never repeats that
        reverse pass when the classifier consumes the map in several stages.
        """

        if not session_ids:
            raise ValueError("candidate survivor map requires bounded IDs")
        params["candidate_filter_session_id_array"] = list(session_ids)
        return """
            WITH
            candidate_filter_ids AS (
                SELECT arrayJoin(
                    CAST(%(candidate_filter_session_id_array)s AS Array(UUID))
                ) AS candidate_id
            ),
            candidate_target_new_ids AS (
                SELECT DISTINCT new_id
                FROM trace_session_id_remap FINAL
                PREWHERE old_id IN (
                    SELECT candidate_id FROM candidate_filter_ids
                )
                UNION DISTINCT
                SELECT candidate_id AS new_id
                FROM candidate_filter_ids
            ),
            candidate_remap_groups AS (
                SELECT
                    new_id,
                    argMin(old_id, toString(old_id)) AS survivor_id,
                    arrayDistinct(
                        arrayConcat(groupArray(old_id), [new_id])
                ) AS group_ids
                FROM trace_session_id_remap FINAL
                WHERE new_id IN (
                    SELECT new_id FROM candidate_target_new_ids
                )
                GROUP BY new_id
            )
            SELECT arrayJoin(group_ids) AS any_id, survivor_id
            FROM candidate_remap_groups
        """

    def _candidate_survivor_map_ctes(
        self,
        params: dict[str, Any],
        session_ids: tuple[str, ...],
    ) -> str:
        """Materialize one finite map as a query-wide scalar tuple array.

        ClickHouse table CTEs are macros, not materialized results. The session
        classifier references its map while seeding, expanding and replaying;
        embedding the base relation directly would repeat the dimension/remap
        reads for each reference. A scalar subquery is executed once as a set,
        while the tiny array can be expanded repeatedly without table reads.
        """

        map_sql = self._candidate_survivor_map_sql(params, session_ids)
        return f"""
        (
            SELECT groupArray(tuple(any_id, survivor_id))
            FROM ({map_sql})
        ) AS candidate_session_pairs,
        ts_survivor_map AS (
            SELECT
                tupleElement(pair, 1) AS any_id,
                tupleElement(pair, 2) AS survivor_id
            FROM (
                SELECT arrayJoin(candidate_session_pairs) AS pair
            )
        )
        """

    def _candidate_session_ctes(
        self,
        params: dict[str, Any],
        *,
        candidate_session_ids: tuple[str, ...] = (),
        root_filter_plans: tuple[Any, ...] = (),
        candidate_full_state: bool = False,
        include_trace_id: bool = False,
        additional_root_ctes: str = "",
        root_membership_predicates: tuple[str, ...] = (),
    ) -> str:
        """Build the shared exact session candidate CTEs.

        Root identities are selected from the root projection and replayed to
        latest physical state. Structural session predicates bind to the
        remap-resolved session ID. Positive end-user predicates build a narrow
        membership set from only the requested user IDs, replaying those span
        identities before resolving both user and session remaps.
        """

        if candidate_full_state and not candidate_session_ids:
            raise ValueError("full-state session scan requires bounded candidates")

        resolved_session = resolved_id_expr("latest_trace_session_id", "ts_remap")
        resolved_session_clause = build_session_id_filter_clause(
            self.filters,
            params,
            session_col="session_id",
            param_prefix="candidate_sess_",
        )
        aggregate_clause = self._build_having_clauses()
        # `_build_having_clauses` maintains the legacy builder contract by
        # binding into `self.params`; copy those generated values into this
        # candidate statement's independent parameter dict.
        params.update(self.params)

        candidate_columns = {
            item.get("column_id") or item.get("columnId")
            for item in [*self.filters, *self.sort_params]
        }
        needs_end_time = bool(candidate_columns & {"end_time", "duration"})
        needs_cost = "total_cost" in candidate_columns
        needs_tokens = "total_tokens" in candidate_columns
        needs_traces = bool(candidate_columns & {"traces_count", "total_traces_count"})
        needs_messages = bool(candidate_columns & {"first_message", "last_message"})

        latest_metric_columns: list[str] = []
        resolved_metric_columns: list[str] = []
        session_metric_columns: list[str] = []
        scalar_filter_aggregates: list[str] = []
        scalar_filter_aliases: list[str] = []
        for plan in root_filter_plans:
            params.update(plan.params)
            for aggregate in plan.aggregates:
                scalar_filter_aggregates.append(aggregate)
                alias = aggregate.rsplit(" AS ", 1)[-1].strip()
                if not alias or alias == aggregate:
                    raise ValueError(
                        "latest-state span filter aggregate requires an alias"
                    )
                scalar_filter_aliases.append(alias)
        if needs_end_time:
            latest_metric_columns.append(
                "argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time"
            )
            resolved_metric_columns.append("latest_end_time AS end_time")
            session_metric_columns.extend(
                [
                    "max(end_time) AS session_end",
                    "dateDiff('second', min(start_time), max(end_time)) AS duration",
                ]
            )
        if needs_cost:
            latest_metric_columns.append(
                "argMax(tuple(cost), _peerdb_version).1 AS latest_cost"
            )
            resolved_metric_columns.append("latest_cost AS cost")
            session_metric_columns.append("sum(cost) AS total_cost")
        if needs_tokens:
            latest_metric_columns.append(
                "argMax(tuple(total_tokens), _peerdb_version).1 AS latest_total_tokens"
            )
            resolved_metric_columns.append("latest_total_tokens AS total_tokens")
            session_metric_columns.append("sum(total_tokens) AS total_tokens")
        if needs_traces or include_trace_id:
            resolved_metric_columns.append("trace_id")
        if needs_traces:
            session_metric_columns.append("uniqExact(trace_id) AS traces_count")
        if needs_messages:
            latest_metric_columns.append(
                "argMax(tuple(input), _peerdb_version).1 AS latest_input"
            )
            resolved_metric_columns.append("latest_input AS input")
            session_metric_columns.extend(
                [
                    "argMin(input, start_time) AS first_message",
                    "argMax(input, start_time) AS last_message",
                ]
            )
        latest_metric_select = (
            ",\n                " + ",\n                ".join(latest_metric_columns)
            if latest_metric_columns
            else ""
        )
        resolved_metric_select = (
            ",\n                " + ",\n                ".join(resolved_metric_columns)
            if resolved_metric_columns
            else ""
        )
        session_metric_select = (
            ",\n                " + ",\n                ".join(session_metric_columns)
            if session_metric_columns
            else ""
        )

        end_time_filters = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId")) == "end_time"
        ]
        root_value_clause = ""
        if end_time_filters:
            filter_builder = ClickHouseFilterBuilder(
                table=self.TABLE,
                annotation_label_ids=self.annotation_label_ids,
                project_id=self.project_id,
                project_ids=self.project_ids,
            )
            root_value_clause, root_value_params = filter_builder.translate(
                end_time_filters
            )
            params.update(root_value_params)

        has_explicit_time_filter = any(
            (item.get("column_id") or item.get("columnId"))
            in {"created_at", "start_time"}
            for item in self.filters
        )
        scope_to_request_window = not candidate_full_state or has_explicit_time_filter
        span_time_scope = (
            "\n              AND toDate(start_time) BETWEEN "
            "toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND "
            "toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))"
            "\n              AND start_time >= "
            "fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')"
            "\n              AND start_time < "
            "fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')"
            if scope_to_request_window
            else ""
        )
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_candidate_time_exclusion",
            )
        )
        params.update(datetime_params)
        if datetime_predicate:
            span_time_scope += f"\n              AND {datetime_predicate}"

        positive_session_ids = self._candidate_positive_filter_values(
            self._SESSION_ID_FILTER_COLS
        )
        seed_session_ids = candidate_session_ids or positive_session_ids
        # The ordinary candidate page has no finite Python session-id set yet,
        # but it does have a finite request scope: physical root rows in the
        # selected project(s) and time window. Use those raw IDs as an exact
        # remap superset, then expand only the consolidation groups they touch.
        # Historical/tombstoned root versions can add harmless map rows; latest
        # physical replay below remains authoritative for page membership.
        ts_map_ctes = f"""
        candidate_root_raw_session_ids AS (
            SELECT DISTINCT trace_session_id AS raw_session_id
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
            WHERE (parent_span_id IS NULL OR parent_span_id = '')
              AND isNotNull(trace_session_id)
              AND trace_session_id != toUUID('{NIL_UUID}')
        ),
        candidate_root_session_group_ids AS (
            SELECT DISTINCT remap_match.new_id
            FROM trace_session_id_remap AS remap_match FINAL
            WHERE remap_match.old_id IN (
                SELECT raw_session_id FROM candidate_root_raw_session_ids
            )
               OR remap_match.new_id IN (
                SELECT raw_session_id FROM candidate_root_raw_session_ids
            )
        ),
        ts_survivor_map AS (
            SELECT
                any_id,
                argMin(survivor_id, toString(survivor_id)) AS survivor_id
            FROM (
                SELECT
                    arrayJoin(arrayDistinct(arrayConcat(
                        groupArray(remap.old_id),
                        [remap.new_id]
                    ))) AS any_id,
                    argMin(remap.old_id, toString(remap.old_id)) AS survivor_id
                FROM trace_session_id_remap AS remap FINAL
                WHERE remap.new_id IN (
                    SELECT new_id FROM candidate_root_session_group_ids
                )
                GROUP BY remap.new_id
            )
            GROUP BY any_id
        )
        """
        candidate_session_cte = ""
        root_session_seed = ""
        if seed_session_ids:
            params["candidate_filter_session_ids"] = seed_session_ids
            # A seed may carry any member of a consolidation group: its
            # survivor old ID, a non-survivor old ID, or the deterministic new
            # ID. Resolve the finite old-ID side by primary key, reverse the
            # resulting/new input IDs in one authoritative pass, and materialize
            # that tiny map once. This preserves the exact many-old→one-new
            # survivor rule without repeating the non-key scan at every CTE use.
            ts_map_ctes = self._candidate_survivor_map_ctes(params, seed_session_ids)
            resolved_candidate_session = resolved_id_expr(
                "candidate_raw_session_id", "candidate_ts_remap"
            )
            candidate_session_cte = f""",
        candidate_filter_sessions AS (
            SELECT DISTINCT {resolved_candidate_session} AS session_id
            FROM (
                SELECT arrayJoin(
                    CAST(%(candidate_filter_session_id_array)s AS Array(UUID))
                ) AS candidate_raw_session_id
            ) AS candidate_raw_sessions
            LEFT JOIN ts_survivor_map AS candidate_ts_remap
                ON candidate_raw_session_id = candidate_ts_remap.any_id
        )"""
            root_session_seed = """
              AND (
                  trace_session_id IN (
                      SELECT session_id FROM candidate_filter_sessions
                  )
                  OR trace_session_id IN (
                      SELECT any_id
                      FROM ts_survivor_map
                      WHERE survivor_id IN (
                          SELECT session_id FROM candidate_filter_sessions
                      )
                  )
              )
            """

        scalar_filter_ctes = ""
        scalar_filter_membership = ""
        if root_filter_plans:
            scalar_aggregate_select = (
                ",\n                "
                + ",\n                ".join(scalar_filter_aggregates)
            )
            scalar_alias_select = ",\n                " + ",\n                ".join(
                scalar_filter_aliases
            )
            resolved_scalar_session = resolved_id_expr(
                "latest_trace_session_id", "scalar_ts_remap"
            )
            scalar_filter_having = " AND ".join(
                plan.grouped_match_predicate() for plan in root_filter_plans
            )
            scalar_filter_ctes = f""",
        candidate_scalar_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
            WHERE 1 = 1
              {root_session_seed}
        ),
        latest_candidate_scalar_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(trace_session_id), _peerdb_version).1
                    AS latest_trace_session_id,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
                {scalar_aggregate_select}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_scalar_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_candidate_scalar_spans AS (
            SELECT
                project_id,
                {resolved_scalar_session} AS session_id,
                trace_id
                {scalar_alias_select}
            FROM latest_candidate_scalar_spans
            LEFT JOIN ts_survivor_map AS scalar_ts_remap
                ON latest_trace_session_id = scalar_ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND isNotNull(latest_trace_session_id)
              AND latest_trace_session_id != toUUID('{NIL_UUID}')
        ),
        matching_scalar_traces AS (
            SELECT project_id, session_id, trace_id
            FROM resolved_candidate_scalar_spans
            GROUP BY project_id, session_id, trace_id
            HAVING {scalar_filter_having}
        ),
        matching_scalar_sessions AS (
            SELECT project_id, session_id
            FROM matching_scalar_traces
            GROUP BY project_id, session_id
        )"""
            if self.project_ids is not None:
                scalar_filter_membership = (
                    "(project_id, session_id) IN ("
                    "SELECT project_id, session_id FROM matching_scalar_sessions)"
                )
            else:
                scalar_filter_membership = (
                    "session_id IN (SELECT session_id FROM matching_scalar_sessions)"
                )

        user_filter_items = [
            item
            for item in self.filters
            if (item.get("column_id") or item.get("columnId"))
            in self._ENDUSER_ID_FILTER_COLS
        ]
        user_ids: list[str] = []
        for item in user_filter_items:
            config = item.get("filter_config") or item.get("filterConfig") or {}
            raw = config.get("filter_value", config.get("filterValue"))
            values = raw if isinstance(raw, list) else [raw]
            user_ids.extend(str(value) for value in values if value)
        if self.user_id:
            user_ids.append(str(self.user_id))
        user_ids = list(dict.fromkeys(user_ids))
        user_filter_ops = {
            (item.get("filter_config") or item.get("filterConfig") or {}).get(
                "filter_op"
            )
            or (item.get("filter_config") or item.get("filterConfig") or {}).get(
                "filterOp"
            )
            for item in user_filter_items
        }
        positive_user_seed = bool(user_ids) and user_filter_ops <= {"equals", "in"}
        user_null_op = self._user_null_filter_op()
        resolved_user_clause = (
            "" if user_null_op else self._build_resolved_user_clause(params)
        )
        user_ctes = ""
        user_membership = ""
        user_root_seed = ""
        user_root_ids_cte = ""
        eu_map_cte = ""
        if resolved_user_clause or user_null_op:
            if positive_user_seed:
                params["candidate_filter_user_ids"] = tuple(user_ids)
                eu_map = bounded_survivor_map_subquery(
                    "end_user_id_remap",
                    candidate_param="candidate_filter_user_ids",
                )
            else:
                eu_map = survivor_map_subquery("end_user_id_remap")
            resolved_user_session = resolved_id_expr(
                "latest_trace_session_id", "user_ts_remap"
            )
            resolved_user = resolved_id_expr("latest_end_user_id", "user_eu_remap")
            eu_map_cte = f",\n        eu_survivor_map AS ({eu_map})"

            # Page-list user filters are positive and can seed by requested
            # user IDs.  The bounded bulk classifier already has <=200 session
            # IDs, so it scopes by those IDs instead; this also makes NOT IN and
            # null-presence semantics exact without scanning every user span in
            # the project.
            if candidate_session_ids:
                user_seed_clause = """
              AND (
                  trace_session_id IN (
                      SELECT session_id FROM candidate_filter_sessions
                  )
                  OR trace_session_id IN (
                      SELECT any_id
                      FROM ts_survivor_map
                      WHERE survivor_id IN (
                          SELECT session_id FROM candidate_filter_sessions
                      )
                  )
              )
                """
            elif positive_user_seed:
                user_seed_clause = """
              AND (
                  end_user_id IN %(candidate_filter_user_ids)s
                  OR end_user_id IN (
                      SELECT any_id
                      FROM eu_survivor_map
                      WHERE survivor_id IN %(candidate_filter_user_ids)s
                  )
              )
                """
                # ``matching_user_sessions`` is a selective, remap-aware
                # superset for the exact positive user filter.  Reuse it when
                # acquiring root identities so an organization user page does
                # not replay every root in the requested window before applying
                # the membership it has already computed.
                user_root_seed = """
              AND trace_session_id IN (
                  SELECT session_id FROM matching_user_root_ids
              )
                """
                user_root_ids_cte = f""",
        matching_user_root_ids AS (
            SELECT arrayJoin(arrayFilter(
                session_key -> session_key != toUUID('{NIL_UUID}'),
                arrayPushBack(
                    groupUniqArray(user_session_aliases.any_id),
                    matching_user_sessions.session_id
                )
            )) AS session_id
            FROM matching_user_sessions
            LEFT JOIN ts_survivor_map AS user_session_aliases
                ON matching_user_sessions.session_id = user_session_aliases.survivor_id
            GROUP BY matching_user_sessions.session_id
        )"""
            else:
                # NOT IN / null-presence predicates cannot be seeded by the
                # requested user IDs without changing their meaning.  The scan
                # remains project/time/root-column only and replays latest
                # physical state before forming session membership.
                user_seed_clause = ""
            if positive_user_seed and not seed_session_ids:
                # A user-detail request has no finite session IDs yet. First
                # replay only spans for the finite requested user aliases, then
                # expand only the session-remap groups touched by those exact
                # live spans. This avoids materializing either tenant-global
                # remap while preserving many-old-to-one-new semantics.
                resolved_raw_session = resolved_id_expr(
                    "matching_user_raw_sessions.raw_session_id", "user_ts_remap"
                )
                resolved_matching_user_clause = resolved_user_clause.replace(
                    "end_user_id", f"({resolved_user})"
                )
                ts_map_ctes = f"""
        eu_survivor_map AS ({eu_map}),
        candidate_user_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
              {user_seed_clause}
        ),
        latest_user_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(trace_session_id), _peerdb_version).1 AS latest_trace_session_id,
                argMax(tuple(end_user_id), _peerdb_version).1 AS latest_end_user_id,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_user_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        matching_user_raw_sessions AS (
            SELECT latest_trace_session_id AS raw_session_id
            FROM latest_user_spans
            LEFT JOIN eu_survivor_map AS user_eu_remap
                ON latest_end_user_id = user_eu_remap.any_id
            WHERE latest_is_deleted = 0
              AND isNotNull(latest_trace_session_id)
              AND latest_trace_session_id != toUUID('{NIL_UUID}')
              AND isNotNull(latest_end_user_id)
              AND latest_end_user_id != toUUID('{NIL_UUID}')
              AND {resolved_matching_user_clause}
            GROUP BY raw_session_id
        ),
        candidate_user_session_group_ids AS (
            SELECT DISTINCT remap_match.new_id
            FROM trace_session_id_remap AS remap_match FINAL
            WHERE remap_match.old_id IN (
                SELECT raw_session_id FROM matching_user_raw_sessions
            )
               OR remap_match.new_id IN (
                SELECT raw_session_id FROM matching_user_raw_sessions
            )
        ),
        ts_survivor_map AS (
            SELECT
                any_id,
                argMin(survivor_id, toString(survivor_id)) AS survivor_id
            FROM (
                SELECT
                    arrayJoin(arrayDistinct(arrayConcat(
                        groupArray(remap.old_id),
                        [remap.new_id]
                    ))) AS any_id,
                    argMin(remap.old_id, toString(remap.old_id)) AS survivor_id
                FROM trace_session_id_remap AS remap FINAL
                WHERE remap.new_id IN (
                    SELECT new_id FROM candidate_user_session_group_ids
                )
                GROUP BY remap.new_id
            )
            GROUP BY any_id
        ),
        matching_user_sessions AS (
            SELECT {resolved_raw_session} AS session_id
            FROM matching_user_raw_sessions
            LEFT JOIN ts_survivor_map AS user_ts_remap
                ON matching_user_raw_sessions.raw_session_id = user_ts_remap.any_id
            GROUP BY session_id
        )
        {user_root_ids_cte}"""
                eu_map_cte = ""
                user_ctes = ""
            else:
                user_ctes = f""",
        candidate_user_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
              {user_seed_clause}
        ),
        latest_user_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(trace_session_id), _peerdb_version).1 AS latest_trace_session_id,
                argMax(tuple(end_user_id), _peerdb_version).1 AS latest_end_user_id,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_user_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_user_spans AS (
            SELECT
                {resolved_user_session} AS session_id,
                {resolved_user} AS end_user_id
            FROM latest_user_spans
            LEFT JOIN ts_survivor_map AS user_ts_remap
                ON latest_trace_session_id = user_ts_remap.any_id
            LEFT JOIN eu_survivor_map AS user_eu_remap
                ON latest_end_user_id = user_eu_remap.any_id
            WHERE latest_is_deleted = 0
              AND isNotNull(latest_trace_session_id)
              AND latest_trace_session_id != toUUID('{NIL_UUID}')
        ),
        matching_user_sessions AS (
            SELECT session_id
            FROM resolved_user_spans
            WHERE isNotNull(end_user_id)
              AND end_user_id != toUUID('{NIL_UUID}')
              {f"AND {resolved_user_clause}" if resolved_user_clause else ""}
            GROUP BY session_id
        )
        {user_root_ids_cte}"""
            membership_op = "NOT IN" if user_null_op == "is_null" else "IN"
            user_membership = (
                f"AND session_id {membership_op} "
                "(SELECT session_id FROM matching_user_sessions)"
            )

        session_predicate = (
            f"AND {resolved_session_clause}" if resolved_session_clause else ""
        )
        all_root_membership_predicates = tuple(root_membership_predicates) + (
            (scalar_filter_membership,) if scalar_filter_membership else ()
        )
        root_membership = (
            "\n              AND "
            + "\n              AND ".join(all_root_membership_predicates)
            if all_root_membership_predicates
            else ""
        )
        org_project_count_cte = ""
        org_project_count_join = ""
        org_project_count_select = ""
        org_project_evidence_select = ""
        if self.project_ids is not None:
            # Session UUIDs are generated globally, but an imported/direct-write
            # tenant can still reuse one.  Detect that impossible-to-represent
            # org identity before page hydration: every enrichment API is keyed
            # by the public UUID alone, so merging two projects would be worse
            # than a sanitized retryable failure.  The view rejects project_count
            # > 1 before issuing those hydration reads.
            org_project_count_cte = """,
        candidate_session_project_counts AS (
            SELECT
                session_id,
                uniqExact(project_id) AS project_count
            FROM resolved_root_sessions
            GROUP BY session_id
        )"""
            org_project_count_join = (
                "INNER JOIN candidate_session_project_counts USING (session_id)"
            )
            org_project_count_select = ", max(project_count) AS project_count"
            # Cross-project user-detail rows still need one authoritative
            # project identity for route construction and enrichment. The
            # collision guard proves this aggregate has exactly one project
            # before the view consumes it.
            org_project_evidence_select = ", any(project_id) AS project_id"
        return f"""
        {ts_map_ctes}
        {candidate_session_cte}
        {eu_map_cte}
        {user_ctes},
        candidate_root_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
            WHERE (parent_span_id IS NULL OR parent_span_id = '')
              {root_session_seed}
              {user_root_seed}
        ),
        latest_roots AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(parent_span_id), _peerdb_version).1 AS latest_parent_span_id,
                argMax(tuple(trace_session_id), _peerdb_version).1 AS latest_trace_session_id,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
                {latest_metric_select}
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{span_time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_root_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_root_sessions AS (
            SELECT
                {resolved_session} AS session_id,
                project_id,
                start_time
                {resolved_metric_select}
            FROM latest_roots
            LEFT JOIN ts_survivor_map AS ts_remap
                ON latest_trace_session_id = ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
              AND isNotNull(latest_trace_session_id)
              AND latest_trace_session_id != toUUID('{NIL_UUID}')
              {f"AND {root_value_clause}" if root_value_clause else ""}
        )
        {scalar_filter_ctes}
        {additional_root_ctes}
        {org_project_count_cte},
        sessions AS (
            SELECT
                session_id,
                min(start_time) AS session_start
                {session_metric_select}
                {org_project_count_select}
                {org_project_evidence_select}
            FROM resolved_root_sessions
            {org_project_count_join}
            WHERE 1 = 1
              {session_predicate}
              {user_membership}
              {root_membership}
            GROUP BY session_id
            {f"HAVING {aggregate_clause}" if aggregate_clause else ""}
        )
        """

    def build_filter_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Seed a finite newest-first superset of session IDs.

        This query deliberately reads only raw root identity/order columns.
        Newer tombstones may create false-positive seeds, but cannot hide a
        live session; ``build_filter_match_query`` replays latest state before
        admitting any seed into the result.
        """

        if not self.supports_bounded_filter_scan():
            raise ValueError("unsupported bounded session filter scan")
        # Seed rows contain only an identity/order tuple. Normal list reads use
        # raw session IDs so every adjacent slice avoids a broad FINAL remap;
        # the finite classifier resolves each raw ID to its survivor and expands
        # the complete consolidation group exactly once. Eval sampling retains
        # its canonical-ID hash contract below. Population proofs may acquire
        # the shared 512-row working set, while callers split exact latest-state
        # replay using the smaller recommended classifier batch.
        if limit <= 0 or limit > 512:
            raise ValueError("session seed limit must be between 1 and 512")
        if (before_start_time is None) != (before_id is None):
            raise ValueError("session keyset values must be provided together")
        request_start, request_end = self.parse_time_range(self.filters)
        if not request_start <= slice_start < slice_end <= request_end:
            raise ValueError("session seed slice must stay inside the request window")

        params: dict[str, Any] = {
            **self.params,
            "filter_slice_start": slice_start,
            "filter_slice_end": slice_end,
            "filter_slice_start_us": _unix_microseconds(slice_start),
            "filter_slice_end_us": _unix_microseconds(slice_end),
            "filter_seed_limit": int(limit),
        }
        _plans, residual = self._bounded_span_filter_parts()
        self._validate_bounded_relational_filters(residual)
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_seed_time_exclusion",
            )
        )
        # The helper deliberately accepts only a bare identifier. Qualify its
        # trusted output at the call site so ClickHouse's analyzer cannot
        # substitute the outer ``max(...) AS start_time`` aggregate alias back
        # into this physical-row predicate.
        datetime_predicate = datetime_predicate.replace(
            "start_time", "seed_spans.start_time"
        )
        params.update(datetime_params)
        datetime_fragment = (
            f"\n                  AND {datetime_predicate}"
            if datetime_predicate
            else ""
        )
        outer_predicates: list[str] = []
        if before_start_time is not None:
            if not slice_start <= before_start_time < slice_end:
                raise ValueError("session keyset must stay inside its slice")
            params["filter_before_start_time"] = before_start_time
            params["filter_before_start_time_us"] = _unix_microseconds(
                before_start_time
            )
            params["filter_before_session_id"] = str(before_id)
            outer_predicates.append(
                "(start_time < fromUnixTimestamp64Micro("
                "%(filter_before_start_time_us)s, 'UTC') OR ("
                "start_time = fromUnixTimestamp64Micro("
                "%(filter_before_start_time_us)s, 'UTC') AND "
                "toString(session_id) < %(filter_before_session_id)s))"
            )
        if self._bounded_sampling_rate is not None:
            params["bounded_sampling_salt"] = str(self._bounded_sampling_salt)
            params["bounded_sampling_rate"] = float(self._bounded_sampling_rate)
            outer_predicates.append(
                "modulo(cityHash64(%(bounded_sampling_salt)s, "
                "toString(session_id)), 100) < %(bounded_sampling_rate)s"
            )
        outer_where = (
            f"WHERE {' AND '.join(outer_predicates)}" if outer_predicates else ""
        )

        seed_source = f"""
            SELECT
                seed_spans.trace_session_id AS session_id,
                max(seed_spans.start_time) AS start_time
            FROM {self.TABLE} AS seed_spans
            PREWHERE {self.project_filter_sql()}
              AND seed_spans._peerdb_is_deleted = 0
              AND seed_spans.start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s, 'UTC')
              AND seed_spans.start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s, 'UTC'){datetime_fragment}
            WHERE (seed_spans.parent_span_id IS NULL OR seed_spans.parent_span_id = '')
              AND isNotNull(seed_spans.trace_session_id)
              AND seed_spans.trace_session_id != toUUID('{NIL_UUID}')
            GROUP BY seed_spans.trace_session_id
        """
        if self._bounded_sampling_rate is not None:
            # Sampling is defined on the canonical public session ID. Keep the
            # pre-existing remap only for this internal sampled lane; moving the
            # hash to raw aliases would change which straddlers are selected.
            ts_map = survivor_map_subquery("trace_session_id_remap")
            resolved_session = resolved_id_expr("raw_trace_session_id", "seed_ts_remap")
            seed_source = f"""
            SELECT
                {resolved_session} AS session_id,
                max(start_time) AS start_time
            FROM (
                SELECT
                    seed_spans.trace_session_id AS raw_trace_session_id,
                    seed_spans.start_time AS start_time
                FROM {self.TABLE} AS seed_spans
                PREWHERE {self.project_filter_sql()}
                  AND seed_spans._peerdb_is_deleted = 0
                  AND seed_spans.start_time >= fromUnixTimestamp64Micro(%(filter_slice_start_us)s, 'UTC')
                  AND seed_spans.start_time < fromUnixTimestamp64Micro(%(filter_slice_end_us)s, 'UTC'){datetime_fragment}
                WHERE (seed_spans.parent_span_id IS NULL OR seed_spans.parent_span_id = '')
                  AND isNotNull(seed_spans.trace_session_id)
                  AND seed_spans.trace_session_id != toUUID('{NIL_UUID}')
            ) AS raw_roots
            LEFT JOIN ({ts_map}) AS seed_ts_remap
                ON raw_trace_session_id = seed_ts_remap.any_id
            GROUP BY session_id
            """
        query = f"""
        WITH seed_sessions AS (
            {seed_source}
        )
        SELECT session_id, start_time
        FROM seed_sessions
        {outer_where}
        ORDER BY start_time DESC, toString(session_id) DESC
        LIMIT %(filter_seed_limit)s
        """
        return query, params

    def build_filter_match_query(
        self,
        candidate_ids: list[str],
        *,
        candidate_full_state: bool = False,
        _candidate_seed_rows: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Classify at most 200 session IDs against exact latest root state."""

        session_ids = tuple(
            dict.fromkeys(str(value) for value in candidate_ids if value)
        )
        if not session_ids:
            return "", {}
        if len(session_ids) > 200:
            raise ValueError("candidate session batch exceeds bounded limit")
        if not self.supports_bounded_filter_scan():
            raise ValueError("unsupported bounded session filter scan")

        plans, residual = self._bounded_span_filter_parts()
        has_eval_filters, relational_filters = (
            self._validate_bounded_relational_filters(residual)
        )
        if self.project_ids is not None:
            # The eval log has no project column.  Org reads therefore compile
            # has_eval through the same project-branched trace predicate as the
            # other relational leaves, where config ids and a spans join bind
            # each textual trace id back to its tenant.
            relational_filters = [*has_eval_filters, *relational_filters]
            has_eval_values: tuple[bool, ...] = ()
        else:
            has_eval_values = self._bounded_has_eval_values(has_eval_filters)
        start_date, end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            **self.params,
            "start_date": start_date,
            "end_date": end_date,
            "bounded_match_limit": len(session_ids),
        }
        has_explicit_time_filter = any(
            (item.get("column_id") or item.get("columnId"))
            in {"created_at", "start_time"}
            for item in self.filters
        )
        scope_to_request_window = not candidate_full_state or has_explicit_time_filter
        eval_ctes, eval_predicates, eval_params = self._bounded_eval_membership_ctes(
            has_eval_values=has_eval_values,
            scope_to_request_window=scope_to_request_window,
        )
        params.update(eval_params)
        relational_ctes, relational_predicates, relational_params = (
            self._bounded_relational_membership_plan(
                relational_filters,
                scope_to_request_window=scope_to_request_window,
                available_params=params,
            )
        )
        params.update(relational_params)
        candidate_ctes = self._candidate_session_ctes(
            params,
            candidate_session_ids=session_ids,
            root_filter_plans=tuple(plans),
            candidate_full_state=candidate_full_state,
            include_trace_id=bool(has_eval_values or relational_filters),
            additional_root_ctes=f"{eval_ctes}{relational_ctes}",
            root_membership_predicates=(
                *eval_predicates,
                *relational_predicates,
            ),
        )

        seed_order_ctes = ""
        seed_order_join = ""
        seed_order_select = "session_start AS start_time"
        seed_order_clause = "ORDER BY start_time DESC, toString(session_id) DESC"
        if _candidate_seed_rows is not None:
            seed_rows_by_id: dict[str, datetime] = {}
            for row in _candidate_seed_rows:
                raw_session_id = str(row.get("session_id") or "")
                seed_start = row.get("start_time")
                if not raw_session_id or not isinstance(seed_start, datetime):
                    raise ValueError(
                        "session rollup classifier requires UUID/time seed rows"
                    )
                previous = seed_rows_by_id.get(raw_session_id)
                if previous is None or seed_start > previous:
                    seed_rows_by_id[raw_session_id] = seed_start
            if tuple(seed_rows_by_id) != session_ids:
                raise ValueError("session rollup seed identities changed during replay")

            params["candidate_seed_order_ids"] = list(seed_rows_by_id)
            params["candidate_seed_order_start_us"] = [
                _unix_microseconds(value) for value in seed_rows_by_id.values()
            ]
            resolved_seed_session = resolved_id_expr("raw_session_id", "seed_ts_remap")
            seed_order_ctes = f""",
        candidate_seed_order_rows AS (
            SELECT
                toUUID(tupleElement(seed_pair, 1)) AS raw_session_id,
                fromUnixTimestamp64Micro(
                    toInt64(tupleElement(seed_pair, 2)), 'UTC'
                ) AS seed_start_time
            FROM (
                SELECT arrayJoin(arrayZip(
                    %(candidate_seed_order_ids)s,
                    %(candidate_seed_order_start_us)s
                )) AS seed_pair
            )
        ),
        candidate_group_rollup_order_rows AS (
            SELECT
                trace_session_id AS raw_session_id,
                minMerge(first_seen) AS seed_start_time
            FROM {_SESSION_ROLLUP_TABLE}
            PREWHERE {self.project_filter_sql()}
              AND hour_first_seen >= toStartOfHour(
                  fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              )
              AND hour_first_seen < toStartOfHour(
                  fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')
              ) + INTERVAL 1 HOUR
            WHERE trace_session_id IN (
                SELECT any_id FROM ts_survivor_map
            )
            GROUP BY trace_session_id
            HAVING seed_start_time >= fromUnixTimestamp64Micro(
                       %(start_date_us)s, 'UTC'
                   )
               AND seed_start_time < fromUnixTimestamp64Micro(
                       %(end_date_us)s, 'UTC'
                   )
        ),
        candidate_seed_order_sources AS (
            SELECT raw_session_id, seed_start_time
            FROM candidate_seed_order_rows
            UNION ALL
            SELECT raw_session_id, seed_start_time
            FROM candidate_group_rollup_order_rows
        ),
        resolved_candidate_seed_order AS (
            SELECT
                {resolved_seed_session} AS session_id,
                seed_start_time,
                raw_session_id
            FROM candidate_seed_order_sources
            LEFT JOIN ts_survivor_map AS seed_ts_remap
                ON raw_session_id = seed_ts_remap.any_id
        ),
        candidate_seed_order AS (
            SELECT
                session_id,
                argMax(
                    tuple(seed_start_time, raw_session_id),
                    tuple(seed_start_time, toString(raw_session_id))
                ) AS seed_order
            FROM resolved_candidate_seed_order
            GROUP BY session_id
        )"""
            seed_order_join = "INNER JOIN candidate_seed_order USING (session_id)"
            seed_order_select = """
            tupleElement(seed_order, 1) AS start_time,
            tupleElement(seed_order, 1) AS _seed_order_start,
            toString(tupleElement(seed_order, 2)) AS _seed_order_id"""
            seed_order_clause = "ORDER BY _seed_order_start DESC, _seed_order_id DESC"
        query = f"""
        WITH
        {candidate_ctes}
        {seed_order_ctes}
        SELECT session_id, {seed_order_select}
            {", project_id, project_count" if self.project_ids is not None else ""}
        FROM sessions
        {seed_order_join}
        {seed_order_clause}
        LIMIT %(bounded_match_limit)s
        """
        return query, params

    def build_filter_match_query_from_seed_rows(
        self,
        candidate_rows: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """Classify rollup seeds while preserving their signed raw order.

        The exact classifier still decides current membership and canonical
        identity. Only the public pagination tuple comes from the insert-only
        rollup seed. The existing candidate survivor map is finite and expands
        only remap groups touched by this at-most-200-row batch.
        """

        candidate_ids = [
            str(row.get("session_id") or "")
            for row in candidate_rows
            if row.get("session_id")
        ]
        if not self.supports_filter_candidate_seed_page():
            return self.build_filter_match_query(candidate_ids)
        return self.build_filter_match_query(
            candidate_ids,
            _candidate_seed_rows=candidate_rows,
        )

    def build_candidate_page_query(self) -> tuple[str, dict[str, Any]]:
        """Select only the exact session page from physical latest root spans.

        Unlike the historical query, this first pass does not read cost/token/
        enrichment columns for every session. It replays the direct-write
        physical identity ``(project, trace, span id, start_time)`` with
        latest-wins tombstone semantics. When membership or ordering depends on
        aggregate/message metrics, only those narrow root columns are computed
        before the page; full content/attribute enrichment remains page-scoped.
        """

        if not self.supports_candidate_first_page():
            raise ValueError("session request is not candidate-page safe")
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "limit": self.page_size + 1,
            "offset": self.page_number * self.page_size,
        }
        candidate_ctes = self._candidate_session_ctes(params)
        order_clause = self._candidate_order_clause()
        query = f"""
        WITH
        {candidate_ctes}
        SELECT
            session_id,
            session_start,
            {"project_id," if self.project_ids is not None else ""}
            {"project_count," if self.project_ids is not None else ""}
            {"max(project_count) OVER() AS max_project_count," if self.project_ids is not None else ""}
            count() OVER() AS total_count
        FROM sessions
        {order_clause}
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, params

    def build_candidate_cursor_page_query(
        self,
        *,
        before_start_time: datetime | None = None,
        before_session_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Select an exact finite-identity cursor page in stable root order.

        The cursor order is the same total order as the numbered default page:
        ``(session_start DESC, session_id DESC)``.  ``remaining_count`` is
        evaluated after the keyset predicate, so the view can reconstruct an
        exact current total as ``seen_rows + remaining_count`` without an
        offset scan or a second count statement.
        """

        if not self.supports_candidate_cursor_page():
            raise ValueError("session request is not candidate-cursor safe")
        if (before_start_time is None) != (before_session_id is None):
            raise ValueError("session cursor keyset values must be provided together")

        self.start_date, self.end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            **self.params,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "limit": self.page_size + 1,
        }
        keyset_clause = ""
        if before_start_time is not None:
            params["cursor_before_start_us"] = _unix_microseconds(before_start_time)
            params["cursor_before_session_id"] = str(before_session_id)
            keyset_clause = """
        WHERE session_start < fromUnixTimestamp64Micro(
                  %(cursor_before_start_us)s, 'UTC'
              )
           OR (
               session_start = fromUnixTimestamp64Micro(
                   %(cursor_before_start_us)s, 'UTC'
               )
               AND session_id < toUUID(%(cursor_before_session_id)s)
           )
            """

        candidate_ctes = self._candidate_session_ctes(params)
        query = f"""
        WITH
        {candidate_ctes}
        SELECT
            session_id,
            session_start,
            {"project_id," if self.project_ids is not None else ""}
            {"project_count," if self.project_ids is not None else ""}
            {"max(project_count) OVER() AS max_project_count," if self.project_ids is not None else ""}
            count() OVER() AS remaining_count
        FROM sessions
        {keyset_clause}
        ORDER BY session_start DESC, session_id DESC
        LIMIT %(limit)s
        """
        return query, params

    def build_candidate_count_query(self) -> tuple[str, dict[str, Any]]:
        """Count exact candidate sessions when an out-of-range page is empty.

        ``count() OVER()`` has no carrier row on an empty page.  This fallback
        repeats only the physical identity/latest-state selector; it does not
        run the historical content/cost/token aggregation.
        """

        if not self.supports_candidate_first_page():
            raise ValueError("session request is not candidate-page safe")
        start_date, end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "start_date": start_date,
            "end_date": end_date,
        }
        candidate_ctes = self._candidate_session_ctes(params)
        query = f"""
        WITH
        {candidate_ctes}
        SELECT count() AS total{", max(project_count) AS max_project_count" if self.project_ids is not None else ""}
        FROM sessions
        """
        return query, params

    def build_page_metrics_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate aggregates for an already selected <=200-session page."""

        ids = tuple(dict.fromkeys(str(value) for value in session_ids if value))
        if not ids:
            return "", {}
        if len(ids) > 200:
            raise ValueError("candidate session page exceeds bounded limit")
        start_date, end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "start_date": start_date,
            "end_date": end_date,
            "candidate_session_ids": ids,
        }
        # Hydration owns a finite page (<=200 IDs). Reuse the candidate-scoped
        # remap so a page read cannot materialize the tenant-global bridge.
        ts_map_ctes = self._candidate_survivor_map_ctes(params, ids)
        resolved_session = resolved_id_expr("latest_trace_session_id", "ts_remap")
        query = f"""
        WITH
        {ts_map_ctes},
        candidate_root_identities AS (
            SELECT DISTINCT
                project_id,
                trace_id,
                id,
                start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) BETWEEN
                  toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
                  toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
              AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')
              AND (
                  trace_session_id IN %(candidate_session_ids)s
                  OR trace_session_id IN (
                      SELECT any_id
                      FROM ts_survivor_map
                      WHERE survivor_id IN %(candidate_session_ids)s
                  )
              )
              AND (parent_span_id IS NULL OR parent_span_id = '')
        ),
        latest_roots AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(parent_span_id), _peerdb_version).1 AS latest_parent_span_id,
                argMax(tuple(trace_session_id), _peerdb_version).1 AS latest_trace_session_id,
                argMax(tuple(end_time), _peerdb_version).1 AS latest_end_time,
                argMax(tuple(cost), _peerdb_version).1 AS latest_cost,
                argMax(tuple(total_tokens), _peerdb_version).1 AS latest_total_tokens,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) BETWEEN
                  toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
                  toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
              AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_root_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_roots AS (
            SELECT
                {resolved_session} AS session_id,
                trace_id,
                start_time,
                latest_end_time AS end_time,
                latest_cost AS cost,
                latest_total_tokens AS total_tokens
            FROM latest_roots
            LEFT JOIN ts_survivor_map AS ts_remap
                ON latest_trace_session_id = ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
              AND {resolved_session} IN %(candidate_session_ids)s
        )
        SELECT
            session_id,
            min(start_time) AS session_start,
            max(end_time) AS session_end,
            dateDiff('second', min(start_time), max(end_time)) AS duration,
            sum(cost) AS total_cost,
            sum(total_tokens) AS total_tokens,
            uniqExact(trace_id) AS traces_count
        FROM resolved_roots
        GROUP BY session_id
        """
        return query, params

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the session list query.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Translate span-level filters (exclude session-level aggregate
        # filters AND end_user_id filters handled via subquery)
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(span_filters)
        self.params.update(extra_params)

        # Build HAVING clauses for aggregate-level filters
        having_clauses = self._build_having_clauses()

        # Sorting
        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            order_clause = "ORDER BY session_start DESC"

        # Pagination
        offset = self.page_number * self.page_size
        self.params["limit"] = self.page_size + 1  # +1 for has_more
        self.params["offset"] = offset

        # Optional user filter (legacy path via self.user_id kwarg)
        if self.user_id:
            self.params["user_id"] = self.user_id

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""
        message_select = self._message_aggregate_select()

        # Resolve session IDs new→old before grouping so cross-cutover spans
        # remain one session. User membership is handled separately below.
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_list_time_exclusion",
            )
        )
        self.params.update(datetime_params)
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )
        time_where = (
            "AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC') "
            "AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')"
            f"{datetime_fragment}"
        )
        from_where = self._session_from_where(
            self.params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

        # Keep the common path light. Message aggregates are added only when
        # first_message/last_message participate in filtering.
        query = f"""
        SELECT
            trace_session_id AS session_id,
            {self._AGGREGATE_SELECT}
            {message_select}
        {from_where}
        GROUP BY trace_session_id
        {having_fragment}
        {order_clause}
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_id_query(
        self,
        *,
        created_at_floor: datetime | None = None,
        created_at_ceiling: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Filtered session ids only — same grouped, remap-aware scan as build(),
        no pagination/order. Lets the eval resolver select the same sessions this
        list endpoint returns.

        ``created_at_floor`` (continuous eval tasks only): floor the span scan on
        CH arrival time (``created_at``) instead of event time (``start_time``),
        so a session whose spans landed in CH after their ``start_time`` is still
        picked up. ``None`` keeps the ``start_time`` window used by the UI list
        and historical tasks.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(span_filters)
        self.params.update(extra_params)

        having_clauses = self._build_having_clauses()
        if self.user_id:
            self.params["user_id"] = self.user_id

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""
        message_select = self._message_aggregate_select()
        if created_at_floor is not None:
            # Window on arrival (created_at), not start_time. NOTE: with a user
            # filter, the membership subqueries still window on start_time, so a
            # filtered task can miss an arrival whose start_time predates
            # parse_time_range's window — pre-existing residual, tracked as a
            # follow-up.
            self.params["created_at_floor"] = created_at_floor
            self.params["created_at_floor_us"] = _unix_microseconds(created_at_floor)
            time_where = (
                "AND created_at >= "
                "fromUnixTimestamp64Micro(%(created_at_floor_us)s, 'UTC')"
            )
            if created_at_ceiling is not None:
                self.params["created_at_ceiling"] = created_at_ceiling
                self.params["created_at_ceiling_us"] = _unix_microseconds(
                    created_at_ceiling
                )
                time_where += (
                    " AND created_at < "
                    "fromUnixTimestamp64Micro(%(created_at_ceiling_us)s, 'UTC')"
                )
        else:
            time_where = (
                "AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC') "
                "AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')"
            )
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_id_time_exclusion",
            )
        )
        self.params.update(datetime_params)
        if datetime_predicate:
            time_where += f"\n          AND {datetime_predicate}"
        from_where = self._session_from_where(
            self.params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

        query = f"""
        SELECT
            trace_session_id AS session_id,
            {self._AGGREGATE_SELECT}
            {message_select}
        {from_where}
        GROUP BY trace_session_id
        {having_fragment}
        """
        return query, self.params

    def build_content_query(self, session_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch first/last messages for a page of session IDs.

        P3b step1.5 (DESIGN §3 / id_remap_sql): ``session_ids`` are the OLD
        curated ids emitted by the (resolved) browse ``build()``. A straddler's
        NEW-deterministic-id spans carry ``trace_session_id = new_id``, so we
        resolve each span's ``trace_session_id`` new→old through
        ``trace_session_id_remap`` and BOTH filter (``IN session_ids``) and
        ``GROUP BY`` the RESOLVED id — else the new-id spans are missed and a
        straddler's first/last message is computed off only its old-id half.
        Pre-flip the remap is a no-op → byte-identical (gate B).
        """
        ids = tuple(dict.fromkeys(str(value) for value in session_ids if value))
        if not ids:
            return "", {}
        if len(ids) > 200:
            raise ValueError("content session page exceeds bounded limit")
        # The bounded endpoint calls this method without calling ``build`` first.
        # Derive its exact request window here so both the raw candidate read and
        # four-field latest-state replay can prune the start_time partitions.
        content_start_date, content_end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "content_session_ids": ids,
            "content_start_date": content_start_date,
            "content_end_date": content_end_date,
        }
        content_exclusion, content_exclusion_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_content_time_exclusion",
            )
        )
        params.update(content_exclusion_params)
        content_exclusion_fragment = (
            f"\n              AND {content_exclusion}" if content_exclusion else ""
        )
        # The page contains at most 200 canonical session IDs.  Building the
        # global survivor map here scans ``trace_session_id_remap`` twice even
        # though hydration can only return those finite sessions.  Expand only
        # their consolidation groups and materialize the finite map once,
        # preserving the identical old/new -> survivor mapping while keeping
        # the span read page-scoped.
        # An unmapped direct-CH session still follows the explicit raw-ID arm
        # below and ``resolved_id_expr`` falls back to that raw ID.
        ts_map_ctes = self._candidate_survivor_map_ctes(params, ids)
        resolved_ts = resolved_id_expr("latest_trace_session_id", "ts_remap")
        query = f"""
        WITH
        {ts_map_ctes},
        candidate_root_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) BETWEEN
                  toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
                  toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
              AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'){content_exclusion_fragment}
              AND (
                  trace_session_id IN %(content_session_ids)s
                  OR trace_session_id IN (
                      SELECT any_id
                      FROM ts_survivor_map
                      WHERE survivor_id IN %(content_session_ids)s
                  )
              )
              AND (parent_span_id IS NULL OR parent_span_id = '')
        ),
        latest_roots AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(parent_span_id), _peerdb_version).1 AS latest_parent_span_id,
                argMax(tuple(trace_session_id), _peerdb_version).1 AS latest_trace_session_id,
                argMax(tuple(input), _peerdb_version).1 AS latest_input,
                argMax(_peerdb_is_deleted, _peerdb_version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND toDate(start_time) BETWEEN
                  toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
                  toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
              AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
              AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'){content_exclusion_fragment}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_root_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_roots AS (
            SELECT
                {resolved_ts} AS session_id,
                start_time,
                latest_input AS input
            FROM latest_roots
            LEFT JOIN ts_survivor_map AS ts_remap
                ON latest_trace_session_id = ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
              AND {resolved_ts} IN %(content_session_ids)s
        )
        SELECT
            session_id,
            argMin(input, start_time) AS first_message,
            argMax(input, start_time) AS last_message
        FROM resolved_roots
        GROUP BY session_id
        """
        return query, params

    def has_having_filters(self) -> bool:
        """Return True if any filters target aggregate columns (requiring HAVING)."""
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id in self.SESSION_FILTER_MAP or col_id in self.MESSAGE_FILTER_MAP:
                return True
        return False

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query to count total matching sessions (for pagination).

        Uses a fast ``count(DISTINCT ...)`` path when no HAVING clauses are
        needed, and falls back to the full aggregation subquery when aggregate
        filters (duration, cost, tokens, traces_count) are present.

        Returns:
            A ``(query_string, params)`` tuple returning a single count.
        """
        if not self.has_having_filters():
            return self._build_simple_count_query()
        return self._build_aggregated_count_query()

    def _build_simple_count_query(self) -> tuple[str, dict[str, Any]]:
        """Fast count using count(DISTINCT ...) — no GROUP BY needed."""
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(span_filters)

        params = dict(self.params)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        # P3b step1.5: same id-remap-resolved scan as build() (trace_session_id
        # always, end_user_id when filtered) so `count(DISTINCT trace_session_id)`
        # unifies a straddler and the count matches the listed rows (else
        # has_more/pagination lies). Pre-flip a byte-identical no-op (gate B).
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_simple_count_time_exclusion",
            )
        )
        params.update(datetime_params)
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )
        time_where = (
            "AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC') "
            "AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')"
            f"{datetime_fragment}"
        )
        from_where = self._session_from_where(
            params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

        query = f"""
        SELECT count(DISTINCT trace_session_id) AS total
        {from_where}
        """
        return query, params

    def _build_aggregated_count_query(self) -> tuple[str, dict[str, Any]]:
        """Full aggregation count — required when HAVING clauses exist."""
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(span_filters)

        params = dict(self.params)
        params.update(extra_params)

        having_clauses = self._build_having_clauses()

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""
        message_select = self._message_aggregate_select()

        # P3b step1.5: same id-remap-resolved scan as build()/simple-count so the
        # HAVING-filtered session count unifies a straddler identically (group on
        # the resolved trace_session_id). Pre-flip a byte-identical no-op (gate B).
        datetime_predicate, datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_aggregate_count_time_exclusion",
            )
        )
        params.update(datetime_params)
        datetime_fragment = (
            f"\n          AND {datetime_predicate}" if datetime_predicate else ""
        )
        time_where = (
            "AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC') "
            "AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')"
            f"{datetime_fragment}"
        )
        from_where = self._session_from_where(
            params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

        # Select the aggregate aliases so HAVING on `duration`/`total_cost`/
        # `total_tokens`/`traces_count` resolves (otherwise CH raises Code 47
        # "Unknown expression identifier" — TH-4316).
        query = f"""
        SELECT count() AS total FROM (
            SELECT
                trace_session_id,
                dateDiff('second', min(start_time), max(end_time)) AS duration,
                sum(cost) AS total_cost,
                sum(total_tokens) AS total_tokens,
                uniqExact(trace_id) AS traces_count
                {message_select}
            {from_where}
            GROUP BY trace_session_id
            {having_fragment}
        )
        """
        return query, params

    def build_span_attributes_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Fetch span attributes for root spans belonging to the given sessions.

        Restricts to root spans only (where custom user-defined attributes
        are typically set) and caps results at 500 rows to prevent unbounded
        scans on sessions with many traces.

        Returns one row per root span with trace_session_id,
        span_attributes_raw, and typed Map columns (span_attr_str,
        span_attr_num) as fallback when the raw JSON blob is empty.
        """
        ids = tuple(dict.fromkeys(str(value) for value in session_ids if value))
        if not ids:
            return "", {}
        if len(ids) > 200:
            raise ValueError("session attribute page exceeds bounded limit")

        # Preserve the raw session-ID prefilter while adding the list request's
        # finite partition window; this legacy method does not replay versions.
        attr_start_date, attr_end_date = self.parse_time_range(self.filters)
        params = {
            **self.params,
            "attr_session_ids": ids,
            "attr_start_date": attr_start_date,
            "attr_end_date": attr_end_date,
        }
        attr_exclusion, attr_exclusion_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                self.filters,
                column="start_time",
                param_prefix="session_attr_time_exclusion",
            )
        )
        params.update(attr_exclusion_params)
        attr_exclusion = attr_exclusion.replace("start_time", "s.start_time")
        attr_exclusion_fragment = (
            f"\n          AND {attr_exclusion}" if attr_exclusion else ""
        )
        # P3b step1.5 (DESIGN §3 / id_remap_sql): `session_ids` are OLD curated ids
        # from the resolved browse; resolve each span's `trace_session_id` new→old
        # so a straddler's NEW-id spans' attributes attach to the OLD session id
        # the page lists. Filter + project the RESOLVED id. Pre-flip: no-op (gate
        # B). The committed PREWHERE micro-opt becomes a WHERE (the id-remap join
        # dominates the cost at scale anyway).
        #
        # Single-level SELECT (NOT a nested re-projection): the v1→v2 rewrite turns
        # bare `span_attributes_raw` into `toJSONString(attributes_extra) AS
        # span_attributes_raw`; a `<alias>.span_attributes_raw` reference would be
        # mangled by that bare-token rewrite. So the JSON/Map attribute columns
        # stay BARE (CH binds them to `s` — the remap join has no such columns),
        # and only `trace_session_id` is read prefixed as `s.trace_session_id`
        # (not a rewrite-special token) to feed the resolve expression.
        ts_map_ctes = self._candidate_survivor_map_ctes(params, ids)
        resolved_ts = resolved_id_expr("s.trace_session_id", "ts_remap")
        query = f"""
        WITH
        {ts_map_ctes}
        SELECT
            {resolved_ts} AS session_id,
            span_attributes_raw,
            span_attr_str,
            span_attr_num
        FROM {self.TABLE} AS s
        LEFT JOIN ts_survivor_map AS ts_remap
            ON s.trace_session_id = ts_remap.any_id
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND toDate(s.start_time) BETWEEN
              toDate(fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')) AND
              toDate(fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'))
          AND s.start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
          AND s.start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC'){attr_exclusion_fragment}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND (
              s.trace_session_id IN %(attr_session_ids)s
              OR s.trace_session_id IN (
                  SELECT any_id
                  FROM ts_survivor_map
                  WHERE survivor_id IN %(attr_session_ids)s
              )
          )
          AND (
            (span_attributes_raw != '{{}}' AND span_attributes_raw != '')
            OR length(mapKeys(span_attr_str)) > 0
            OR length(mapKeys(span_attr_num)) > 0
          )
          AND {resolved_ts} IN %(attr_session_ids)s
        LIMIT 500
        """
        return query, params

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_sessions(
        rows: list[tuple],
        columns: list[str],
    ) -> list[dict[str, Any]]:
        """Convert ClickHouse rows to the session list response format.

        Args:
            rows: Raw rows from ClickHouse (dicts or tuples).
            columns: Column names.

        Returns:
            List of session dicts matching the frontend's expected shape.
        """
        results: list[dict[str, Any]] = []
        col_idx = {name: i for i, name in enumerate(columns)}

        def _get(row, key, idx, default=None):
            if isinstance(row, dict):
                return row.get(key, default)
            return (
                row[col_idx.get(key, idx)]
                if len(row) > col_idx.get(key, idx)
                else default
            )

        for row in rows:
            session_id = str(_get(row, "session_id", 0, ""))
            if session_id == NIL_UUID:
                continue
            session_start = _get(row, "session_start", 1)
            session_end = _get(row, "session_end", 2)
            duration_val = _get(row, "duration", 3, 0)

            results.append(
                {
                    "session_id": session_id,
                    "session_name": None,
                    "start_time": (
                        session_start.isoformat()
                        if hasattr(session_start, "isoformat")
                        else session_start
                    ),
                    "end_time": (
                        session_end.isoformat()
                        if hasattr(session_end, "isoformat")
                        else session_end
                    ),
                    "duration": float(duration_val) if duration_val else 0,
                    "total_cost": float(_get(row, "total_cost", 4, 0) or 0),
                    "total_tokens": int(_get(row, "total_tokens", 5, 0) or 0),
                    "total_traces_count": int(_get(row, "traces_count", 6, 0) or 0),
                    "first_message": _get(row, "first_message", 7, "") or "",
                    "last_message": _get(row, "last_message", 8, "") or "",
                }
            )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Filter `column_id`s that select a SET of end-user UUIDs against the
    # spans `end_user_id` column. The cross-project user-detail page (and the
    # session view's `user_id` query param) inject one of these as a synthetic
    # `end_user_id IN (...)` filter via `trace_session.py` (it resolves the raw
    # `user_id` string to a list of curated `EndUser.id`s in PG, then passes
    # them here). These must NOT flow into `ClickHouseFilterBuilder.translate()`
    # — they are resolved through the id-remap on a wrapped layer instead (see
    # `_build_resolved_user_clause` / P3b step1.5), so a cross-cutover straddler
    # unifies. `user` is the FilterBuilder alias for `end_user_id`.
    _ENDUSER_ID_FILTER_COLS = frozenset({"end_user_id", "user"})
    _SESSION_ID_FILTER_COLS = SESSION_ID_FILTER_COLS

    def _build_end_user_subquery(self) -> str:
        """Compatibility shim for pre-remap session-list code paths.

        End-user filtering now happens in ``_build_resolved_user_clause`` after
        the span row's ``end_user_id`` has been resolved new->old. Returning an
        empty fragment here prevents duplicate raw predicates.
        """
        return ""

    def _extract_span_filters(self) -> list[dict]:
        """Extract filters that apply at the span level (pre-GROUP BY).

        Filters on aggregate columns (duration, total_cost, etc.) are
        handled separately via HAVING clauses. ``end_user_id``/``user``
        identity filters are ALSO excluded here — they are resolved through
        the id-remap by ``_build_resolved_user_clause`` (P3b step1.5) rather
        than compiled raw by ``ClickHouseFilterBuilder``.
        """
        span_filters: list[dict] = []
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id in self.SESSION_FILTER_MAP or col_id in self.MESSAGE_FILTER_MAP:
                continue
            if (
                col_id in self._ENDUSER_ID_FILTER_COLS
                or col_id in self._SESSION_ID_FILTER_COLS
            ):
                continue
            span_filters.append(f)
        return span_filters

    def _build_resolved_session_clause(self, params: dict[str, Any]) -> str:
        # Applied in the OUTER WHERE of `_session_from_where`, where the column
        # is already projected as the remap-resolved `trace_session_id`.
        return build_session_id_filter_clause(
            self.filters,
            params,
            session_col="trace_session_id",
            param_prefix="sess_",
        )

    def _build_resolved_user_clause(self, params: dict[str, Any]) -> str:
        """Build the id-remap-resolved end-user predicate for the session scan.

        Returns a WHERE-fragment that constrains the (already id-remap-resolved)
        ``end_user_id`` column, or ``""`` when there is no user filter. P3b
        step1.5 (DESIGN §3 / id_remap_sql): the user is selected by the OLD
        curated id(s) — ``self.user_id`` and/or the synthetic ``end_user_id``
        IN-filter both carry ``str(EndUser.id)`` values resolved in PG. A
        cross-cutover straddler's NEW (deterministic-id) spans carry
        ``end_user_id = new_id``; by binding this predicate to the RESOLVED
        (new→old) column produced by the wrapped scan, old + new spans select
        as ONE user. Pre-flip the resolved id == the span's own id, so the
        predicate is identical to the committed bare ``end_user_id = ...`` /
        ``IN (...)`` (gate B). Mutates ``params`` with any bound id values.

        Combines, when both present, ``self.user_id`` (equality) AND every
        extracted ``end_user_id``/``user`` filter (IN / NOT IN) with ``AND`` —
        matching how the committed code would have ``AND``-stitched a
        ``user_clause`` plus a synthetic-filter fragment.
        """
        clauses: list[str] = []

        if self.user_id:
            params["user_id"] = self.user_id
            clauses.append("end_user_id = %(user_id)s")

        eu_param_idx = 0
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id not in self._ENDUSER_ID_FILTER_COLS:
                continue
            config = f.get("filter_config") or f.get("filterConfig") or {}
            filter_op = config.get("filter_op") or config.get("filterOp")
            raw_val = config.get("filter_value", config.get("filterValue"))
            ids = raw_val if isinstance(raw_val, list) else [raw_val]
            ids = [str(v) for v in ids if v]
            if not ids:
                # An empty id-set means "match nothing" — preserve that
                # (the synthetic filter falls back to [NIL_UUID] upstream, but
                # guard here too so we never silently drop the constraint).
                clauses.append("0 = 1")
                continue
            outer_op = "NOT IN" if filter_op in ("not_equals", "not_in") else "IN"
            eu_param_idx += 1
            pname = f"eu_remap_{eu_param_idx}"
            params[pname] = tuple(ids)
            clauses.append(f"end_user_id {outer_op} %({pname})s")

        return " AND ".join(clauses)

    def _user_null_filter_op(self) -> str | None:
        """Return ``is_null``/``is_not_null`` when a user filter tests presence.

        A ``user_id``/``end_user_id`` filter with a null operator carries no
        value to resolve — it asks "does this session have a user at all" —
        and is answered by ``_build_user_presence_clause`` instead of the
        id-set membership in ``_build_resolved_user_clause``.
        """
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id not in self._ENDUSER_ID_FILTER_COLS:
                continue
            config = f.get("filter_config") or f.get("filterConfig") or {}
            op = config.get("filter_op") or config.get("filterOp")
            if op in ("is_null", "is_not_null"):
                return op
        return None

    def _build_user_presence_clause(self, null_op: str) -> str:
        """Membership over sessions that have ANY end user.

        ``is_not_null`` → the session IS in that set; ``is_null`` → it is NOT.
        The outer session query groups by remap-resolved ``trace_session_id``,
        so the presence set must resolve session ids too. Otherwise a straddler
        whose user appears only on its deterministic-id spans can be compared
        against the old survivor id and misclassified as user-less.
        """
        ts_join = remap_left_join(
            "us.trace_session_id", "trace_session_id_remap", "user_presence_ts_remap"
        )
        resolved_ts = resolved_id_expr("us.trace_session_id", "user_presence_ts_remap")
        membership = f"""(
            SELECT trace_session_id
            FROM (
                SELECT {resolved_ts} AS trace_session_id
                FROM (
                    SELECT trace_session_id
                    FROM {self.TABLE}
                    {self.project_where()}
                      AND trace_session_id IS NOT NULL
                      AND trace_session_id != toUUID('{NIL_UUID}')
                      AND end_user_id IS NOT NULL
                      AND end_user_id != toUUID('{NIL_UUID}')
                      AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
                      AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')
                ) AS us
                {ts_join}
            )
            GROUP BY trace_session_id
        )"""
        op = "NOT IN" if null_op == "is_null" else "IN"
        return f"trace_session_id {op} {membership}"

    def _build_session_user_membership_clause(self, params: dict[str, Any]) -> str:
        null_op = self._user_null_filter_op()
        if null_op:
            return self._build_user_presence_clause(null_op)

        resolved_user_clause = self._build_resolved_user_clause(params)
        if not resolved_user_clause:
            return ""

        ts_join = remap_left_join(
            "us.trace_session_id", "trace_session_id_remap", "user_ts_remap"
        )
        eu_join = remap_left_join(
            "us.end_user_id", "end_user_id_remap", "user_eu_remap"
        )
        resolved_ts = resolved_id_expr("us.trace_session_id", "user_ts_remap")
        resolved_eu = resolved_id_expr("us.end_user_id", "user_eu_remap")
        return f"""trace_session_id IN (
            SELECT trace_session_id
            FROM (
                SELECT
                    {resolved_ts} AS trace_session_id,
                    {resolved_eu} AS end_user_id
                FROM (
                    SELECT trace_session_id, end_user_id
                    FROM {self.TABLE}
                    {self.project_where()}
                      AND trace_session_id IS NOT NULL
                      AND trace_session_id != toUUID('{NIL_UUID}')
                      AND end_user_id IS NOT NULL
                      AND start_time >= fromUnixTimestamp64Micro(%(start_date_us)s, 'UTC')
                      AND start_time < fromUnixTimestamp64Micro(%(end_date_us)s, 'UTC')
                ) AS us
                {ts_join}
                {eu_join}
            )
            WHERE {resolved_user_clause}
            GROUP BY trace_session_id
        )"""

    # Span columns the session aggregates read (kept narrow so the id-remap
    # wrap projects only what the GROUP BY needs).
    _SESSION_SCAN_COLS = (
        "trace_session_id",
        "trace_id",
        "start_time",
        "end_time",
        "cost",
        "total_tokens",
    )

    def _session_from_where(
        self,
        params: dict[str, Any],
        *,
        time_where: str,
        filter_fragment: str,
    ) -> str:
        """Return the ``FROM … WHERE …`` clause for a session aggregation query.

        The scan resolves ``trace_session_id`` new→old before grouping, so a
        cross-cutover straddler remains one session. User filters use a separate
        remap-aware membership subquery; this selects sessions without shrinking
        their aggregate rows. Span predicates remain on the inner root-span scan.

        GATE B: pre-flip every span's id lives in the remap ``old_id`` column, so
        NO span matches a ``new_id`` → ``resolved_id_expr`` (zero-uuid-guarded,
        NOT a COALESCE) returns each span's own id and the LEFT JOIN(s) add
        nothing → the wrapped scan is a transparent pass-through, byte-identical
        (result-set) to the committed bare scan.
        """
        base_predicates = f"""{self.project_where()}
          AND trace_session_id IS NOT NULL
          AND trace_session_id != toUUID('{NIL_UUID}')
          AND (parent_span_id IS NULL OR parent_span_id = '')
          {time_where}
          {filter_fragment}"""

        resolved_session_clause = self._build_resolved_session_clause(params)
        user_membership_clause = self._build_session_user_membership_clause(params)

        # `trace_session_id` resolution is UNCONDITIONAL (closes the browse split);
        # User membership is resolved in a separate session-id subquery so
        # selecting a user does not shrink the session's displayed aggregates.
        ts_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")

        # Inner scan projects only columns required by session aggregation.
        scan_cols = list(self._SESSION_SCAN_COLS)
        if self._needs_message_aggregates():
            scan_cols.append("input")
        outer_select = [f"{resolved_ts} AS trace_session_id"] + [
            f"rs.{c} AS {c}" for c in scan_cols if c != "trace_session_id"
        ]
        outer_clauses = [
            c for c in (resolved_session_clause, user_membership_clause) if c
        ]

        inner_cols = ", ".join(scan_cols)
        outer_select_sql = ",\n                ".join(outer_select)
        where_clause = (
            f"\n        WHERE {' AND '.join(outer_clauses)}" if outer_clauses else ""
        )
        return f"""FROM (
            SELECT
                {outer_select_sql}
            FROM (
                SELECT {inner_cols}
                FROM {self.TABLE}
                {base_predicates}
            ) AS rs
            {ts_join}
        ){where_clause}"""

    def _build_having_clauses(self) -> str:
        """Build HAVING clause fragments for aggregate-level filters."""
        conditions: list[str] = []
        param_counter = 900  # Use high numbers to avoid conflicts

        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if (
                col_id not in self.SESSION_FILTER_MAP
                and col_id not in self.MESSAGE_FILTER_MAP
            ):
                continue

            config = f.get("filter_config") or f.get("filterConfig") or {}
            filter_op = config.get("filter_op") or config.get("filterOp")
            filter_value = config.get("filter_value", config.get("filterValue"))
            ch_col = (
                self.SESSION_FILTER_MAP.get(col_id) or self.MESSAGE_FILTER_MAP[col_id]
            )

            if col_id in self.MESSAGE_FILTER_MAP:
                if filter_op in ("is_null", "is_not_null"):
                    conditions.append(
                        f"({ch_col} IS NULL OR {ch_col} = '')"
                        if filter_op == "is_null"
                        else f"({ch_col} IS NOT NULL AND {ch_col} != '')"
                    )
                    continue
                text_op = {
                    "equals": "=",
                    "not_equals": "!=",
                    "contains": "ILIKE",
                    "not_contains": "NOT ILIKE",
                    "starts_with": "ILIKE",
                    "ends_with": "ILIKE",
                }.get(filter_op)
                if text_op is None:
                    conditions.append("0 = 1")
                    continue
                param_counter += 1
                param_name = f"having_{param_counter}"
                if filter_op in ("contains", "not_contains"):
                    filter_value = f"%{filter_value}%"
                elif filter_op == "starts_with":
                    filter_value = f"{filter_value}%"
                elif filter_op == "ends_with":
                    filter_value = f"%{filter_value}"
                self.params[param_name] = filter_value
                conditions.append(f"{ch_col} {text_op} %({param_name})s")
                continue

            param_counter += 1
            param_name = f"having_{param_counter}"
            conditions.append(
                build_numeric_filter_predicate(
                    ch_col,
                    filter_op,
                    filter_value,
                    param_prefix=param_name,
                    params=self.params,
                )
            )

        return " AND ".join(conditions)

    def _has_message_filters(self) -> bool:
        return any(
            (f.get("column_id") or f.get("columnId")) in self.MESSAGE_FILTER_MAP
            for f in self.filters
        )

    def _has_message_sort(self) -> bool:
        return any(
            (s.get("column_id") or s.get("columnId")) in self.MESSAGE_FILTER_MAP
            for s in self.sort_params
        )

    def _needs_message_aggregates(self) -> bool:
        """The argMin/argMax message aggregates must be projected whenever a
        message column is filtered OR sorted on. Sorting alone (without a
        matching filter) still emits ``ORDER BY first_message`` via
        ``translate_sort``, so the column must be selected or CH fails with
        "Unknown expression identifier".
        """
        return self._has_message_filters() or self._has_message_sort()

    def _message_aggregate_select(self) -> str:
        if not self._needs_message_aggregates():
            return ""
        return (
            ",\n            argMin(input, start_time) AS first_message,"
            "\n            argMax(input, start_time) AS last_message"
        )
