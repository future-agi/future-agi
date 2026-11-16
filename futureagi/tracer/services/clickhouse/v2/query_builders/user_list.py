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

from tracer.services.clickhouse.query_builders.user_list import (
    UserListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class UserListQueryBuilderV2(V2RewriteMixin, UserListQueryBuilder):
    """Drop-in v2 UserList builder — adds SETTINGS for projection routing."""


__all__ = ["UserListQueryBuilderV2"]
