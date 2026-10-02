"""Direct-write CH25 Agent Graph query builder."""

from tracer.services.clickhouse.query_builders.agent_graph import (
    AgentGraphQueryBuilder,
)


class AgentGraphQueryBuilderV2(AgentGraphQueryBuilder):
    """Physical direct-write specialization.

    The base query is already native CH25 SQL.  Deliberately avoid the generic
    v1 rewrite mixin: applying it to this statement can wrap JSON expressions a
    second time and, more importantly, hides which physical version/tombstone
    fields define the latest-state boundary.
    """

    TABLE = "spans"
    VERSION_COLUMN = "_version"
    DELETED_COLUMN = "is_deleted"


__all__ = ["AgentGraphQueryBuilderV2"]
