"""
v2 SessionList query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite, same as v2/span_list.py and v2/trace_list.py.
The v1 SessionList builder aggregates spans by trace_session_id; v2's
materialized `trace_session_id` column is queried unchanged. `V2RewriteMixin`
routes every inherited `build*` method's SQL through the v2 rewriter at one
boundary. All of this builder's queries target the migrated `spans` schema, so
there are no rewrite exclusions.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class SessionListQueryBuilderV2(V2RewriteMixin, SessionListQueryBuilder):
    """Drop-in v2 SessionList builder."""


__all__ = ["SessionListQueryBuilderV2"]
