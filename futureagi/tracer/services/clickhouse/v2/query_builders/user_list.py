"""
v2 UserList query builder — targets the CH 25.3 spans schema.

The v1 UserListQueryBuilder already emits CH25-native SQL (it was written
post-migration targeting `end_users FINAL`, `span_user_rollup`, and the v2
`spans` table with `is_deleted`). The only missing piece is the v2 SETTINGS
clause (`optimize_use_projections = 1`, `use_skip_indexes_if_final = 1`,
`optimize_aggregation_in_order = 1`) which enables the `proj_by_end_user`
projection for the `raw_spans_light` CTE and keeps skip indexes active
through the `end_users FINAL` read.

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

    pass


__all__ = ["UserListQueryBuilderV2"]
