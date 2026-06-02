"""
ClickHouse Query Builders subpackage.

Provides modular, composable query builders for all ClickHouse analytical
queries.  Each builder inherits from :class:`BaseQueryBuilder` and implements
the :meth:`build` method to produce a ``(query_string, params_dict)`` tuple
ready for execution via ``ClickHouseClient.execute_read()``.

Typical usage::

    from tracer.services.clickhouse.query_builders import (
        TimeSeriesQueryBuilder,
        ClickHouseFilterBuilder,
    )

    builder = TimeSeriesQueryBuilder(
        project_id="...",
        filters=[...],
        interval="day",
    )
    query, params = builder.build()
    rows, columns, _ = ch_client.execute_read(query, params)
    result = builder.format_result(rows, columns)
"""

from tracer.services.clickhouse.query_builders.agent_graph import (
    AgentGraphQueryBuilder,
)
from tracer.services.clickhouse.query_builders.annotation_graph import (
    AnnotationGraphQueryBuilder,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
)
from tracer.services.clickhouse.query_builders.error_analysis import (
    ErrorAnalysisQueryBuilder,
)
from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.query_builders.session_analytics import (
    SessionAnalyticsQueryBuilder,
)
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallListQueryBuilder,
)

__all__ = [
    "AgentGraphQueryBuilder",
    "AnnotationGraphQueryBuilder",
    "BaseQueryBuilder",
    "ClickHouseFilterBuilder",
    "DashboardQueryBuilder",
    "MonitorMetricsQueryBuilder",
    "TimeSeriesQueryBuilder",
    "TraceListQueryBuilder",
    "UserListQueryBuilder",
    "SpanListQueryBuilder",
    "SessionAnalyticsQueryBuilder",
    "SessionListQueryBuilder",
    "EvalMetricsQueryBuilder",
    "ErrorAnalysisQueryBuilder",
    "VoiceCallListQueryBuilder",
]
