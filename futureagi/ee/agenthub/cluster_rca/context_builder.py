"""Builds the 'Available filters' system-prompt section for the cluster RCA agent.

The agent works against a customer's project — span attribute keys are
customer-defined and won't appear in any built-in enum. Without this preload,
the agent burns 3-5 turns enumerating before it can write a useful filter.

We hit the existing tracer.services.clickhouse helper for span attribute keys
and format the top-N as a cached system-prompt section. Eval-name + annotation
preload lands in a follow-up when those helpers exist.
"""

from __future__ import annotations

import structlog

from tracer.services.clickhouse.span_attribute_lookups import (
    list_attribute_keys_for_project,
)

logger = structlog.get_logger(__name__)


# Built-in columns the agent can always filter by (no prefix). The list is
# stable across projects and matches the FilterEngine.DEFAULT_FIELD_MAP + the
# fields exposed on TraceErrorGroup / ErrorClusterTraces / Trace / Span.
_BUILTIN_COLUMNS: list[str] = [
    "cluster_id",
    "trace_id",
    "span_id",
    "session_id",
    "user_id",
    "external_id",
    "created_at",
    "status",
    "name",
    "fix_layer",
    "scan_issue_category",
    "scan_issue_group",
    "version",
    "latency_ms",
    "tokens",
    "cost",
]

# Cap on attribute keys we paste into the prompt. Past ~50 the marginal value
# drops fast and we're paying cache-prefix tokens for noise.
_TOP_N_ATTRIBUTES = 50


def build_project_schema_context(project_id: str | None) -> str:
    """Render the 'Available filters in this project' section.

    Returns the empty string when project_id is missing or the schema lookup
    fails — the agent's general harness instructions still apply. Logs but
    does not raise so a degraded ClickHouse never blocks the run.
    """
    if not project_id:
        return ""

    try:
        keys = list_attribute_keys_for_project(project_id)
    except Exception as exc:
        logger.warning(
            "cluster_rca_schema_context_failed",
            project_id=project_id,
            error=str(exc),
        )
        return ""

    top_keys = keys[:_TOP_N_ATTRIBUTES]

    lines: list[str] = ["# Available filters in this project (preloaded)", ""]

    lines.append("Built-in columns (no prefix in filter keys):")
    lines.append("  " + ", ".join(_BUILTIN_COLUMNS))
    lines.append("")

    if top_keys:
        lines.append(
            f"Span attributes — use `attr.<key>` (top {len(top_keys)} by occurrence):"
        )
        for k in top_keys:
            lines.append(f"  attr.{k.key} ({k.type}, {k.count:,} occurrences)")
    else:
        lines.append(
            "Span attributes — use `attr.<key>`: none discovered for this project yet."
        )
    lines.append("")

    # Eval / annotation discovery lands in a follow-up; for now nudge the agent
    # to query them on-demand via the harness tools.
    lines.append(
        "Eval metrics — use `eval.<name>`. Discover available names via "
        "`list(dimension='eval_results', filter={cluster_id: <id>})`."
    )
    lines.append(
        "Annotations — use `ann.<name>`. Discover via "
        "`list(dimension='scan_issues', filter={cluster_id: <id>})` for "
        "scanner-flagged categories."
    )

    return "\n".join(lines)
