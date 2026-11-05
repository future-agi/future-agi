"""
v2 UserList query builder — targets the CH 25.3 spans schema.

The v1 UserListQueryBuilder already emits CH25-native SQL targeting the retained
`span_user_rollup` plus compact `end_users` fallback for bounded cursor
candidates, `end_users FINAL` for curated labels, and the v2 `spans` table for
finite exact replay. The rollup cannot retract tombstones or corrections, so it
is never a published correctness source. This wrapper adds the v2 SETTINGS clause (`optimize_use_projections = 1`,
`use_skip_indexes_if_final = 0`, `optimize_aggregation_in_order = 1`). Keeping
the generic builder in correctness mode avoids relying on every present and
future skip-index expression being invariant across physical user versions.

`V2RewriteMixin` wraps every `build*` method to append these settings. The
token rewrite pass is a harmless no-op on already-v2 SQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from tracer.services.clickhouse.query_builders.user_list import (
    UserListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin

if TYPE_CHECKING:
    from tracer.services.derived_metrics.base_list_current import CurrentFactsRead


class UserListQueryBuilderV2(V2RewriteMixin, UserListQueryBuilder):
    """Native exact reader, with an inactive source-qualified activity seam."""

    def bind_current_facts(self, read: CurrentFactsRead | None) -> None:
        from tracer.services.derived_metrics.base_list_current import CurrentFactsRead

        if read is not None and not isinstance(read, CurrentFactsRead):
            raise TypeError("expected a qualified current-fact read")
        self._current_facts = read

    def build_candidate_page_query(
        self,
        *,
        cursor_mode: bool = False,
        cursor_before: tuple[Any, str] | None = None,
        scalar_witness: str = "",
        scalar_witness_params: dict[str, Any] | None = None,
        label_witness: str = "",
        label_witness_params: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        from tracer.selectors.user_activity_slice import (
            activity_read_available,
            build_activity_page,
        )

        read = getattr(self, "_current_facts", None)
        if (
            activity_read_available(self, read)
            and self.limit is not None
            and self.offset is not None
            and not scalar_witness
            and not label_witness
        ):
            return build_activity_page(
                self,
                read,
                limit=self.limit,
                offset=self.offset,
                cursor_mode=cursor_mode,
                cursor_before=cursor_before,
            )
        return super().build_candidate_page_query(
            cursor_mode=cursor_mode,
            cursor_before=cursor_before,
            scalar_witness=scalar_witness,
            scalar_witness_params=scalar_witness_params,
            label_witness=label_witness,
            label_witness_params=label_witness_params,
        )

    def build_dimension_candidate_query(
        self,
        *,
        limit: int,
        before_first_seen: datetime | None = None,
        before_end_user_id: str | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        from tracer.selectors.user_activity_slice import (
            activity_read_available,
            build_activity_page,
        )

        read = getattr(self, "_current_facts", None)
        if activity_read_available(self, read):
            if before_first_seen is not None and before_end_user_id is None:
                raise ValueError("dimension candidate cursor must be provided together")
            if (window_start is None) != (window_end is None):
                raise ValueError("dimension candidate window must be provided together")
            window = (
                (window_start, window_end)
                if window_start is not None
                else self.parse_time_range(self.filters)
            )
            if window[0] is None or window[1] is None or window[0] >= window[1]:
                raise ValueError("dimension candidate window is invalid")
            return build_activity_page(
                self,
                read,
                limit=limit,
                cursor_mode=True,
                cursor_before=(before_first_seen, str(before_end_user_id))
                if before_end_user_id is not None
                else None,
                window=window,
                # The manager reuses these exact usage rows after admission.
                # Aggregate only the finite candidate page, never all users.
            )
        return super().build_dimension_candidate_query(
            limit=limit,
            before_first_seen=before_first_seen,
            before_end_user_id=before_end_user_id,
            window_start=window_start,
            window_end=window_end,
        )


__all__ = ["UserListQueryBuilderV2"]
