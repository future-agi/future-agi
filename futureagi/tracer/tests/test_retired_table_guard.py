"""Guards against naming a legacy-chain object that boot has already dropped.

``_ensure_analytics_schema()`` drops every name in ``_LEGACY_CDC_CHAIN_NAMES``
when ``CH25_DROP_LEGACY_CDC_CHAIN`` is on. ``get_all_schema_ddl()`` filters the
CREATE statements accordingly, but three *other* consumers kept naming the
dropped objects and were only caught in production:

* ``POST_DDL_ALTERS`` — ``ALTER TABLE {span,eval}_metrics_hourly REMOVE TTL``
* ``_warm_ch_cache`` — a warm query on ``tracer_trace``
* ``check_replication_lag`` / ``ConsistencyChecker`` — CDC probes on
  ``tracer_trace`` and ``trace_session``

Each produced a permanent ``Code: 60 — Could not find table`` on every pod boot
in both regions. These tests pin the invariant so the next consumer can't
reintroduce it.
"""

import pytest

from tracer.services.clickhouse import consistency as ch_consistency
from tracer.services.clickhouse import schema as ch_schema
from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)


@pytest.fixture
def chain_dropped(monkeypatch):
    """Force the post-cutover state (US + EU prod, and dev/compose)."""
    monkeypatch.setattr(ch_schema, "_DROP_LEGACY_CDC_CHAIN", True)


@pytest.fixture
def chain_retained(monkeypatch):
    """Force the pre-cutover state — legacy objects still exist."""
    monkeypatch.setattr(ch_schema, "_DROP_LEGACY_CDC_CHAIN", False)


def test_is_retired_only_for_chain_members(chain_dropped):
    assert ch_schema.is_retired_table("eval_metrics_hourly")
    assert ch_schema.is_retired_table("tracer_trace")
    # Live tables must never be reported as retired.
    assert not ch_schema.is_retired_table("spans")
    assert not ch_schema.is_retired_table("tracer_eval_logger")
    assert not ch_schema.is_retired_table("usage_apicalllog")


def test_nothing_is_retired_before_cutover(chain_retained):
    """With the flag off the legacy objects still exist — filter must no-op."""
    for name in ch_schema._LEGACY_CDC_CHAIN_NAMES:
        assert not ch_schema.is_retired_table(name)
    assert len(ch_schema.get_post_ddl_alters()) == len(ch_schema.POST_DDL_ALTERS)


def test_post_ddl_alters_never_target_a_dropped_table(chain_dropped):
    """The invariant that would have caught the original bug."""
    offenders = [
        table
        for table, _statement in ch_schema.get_post_ddl_alters()
        if table in ch_schema._LEGACY_CDC_CHAIN_NAMES
    ]
    assert offenders == [], (
        f"POST_DDL_ALTERS targets {offenders}, which _ensure_analytics_schema() "
        "drops earlier in the same boot — every such entry logs a Code: 60."
    )


def test_retired_alters_are_the_ttl_pair(chain_dropped):
    """Exactly the two REMOVE TTL statements drop out — nothing else."""
    kept = {stmt for _t, stmt in ch_schema.get_post_ddl_alters()}
    dropped = {stmt for _t, stmt in ch_schema.POST_DDL_ALTERS} - kept
    assert dropped == {
        "ALTER TABLE span_metrics_hourly REMOVE TTL",
        "ALTER TABLE eval_metrics_hourly REMOVE TTL",
    }


def test_trace_session_id_alter_survives_filtering(chain_dropped):
    """Regression: ``trace_session`` is a substring of ``trace_session_id``.

    ``trace_session`` is a retired table, but ``trace_session_id`` is a live
    column on ``tracer_eval_logger``. A filter that matched on statement text
    instead of an explicit target table would silently stop adding it and
    break the session-eval write path.
    """
    kept = "\n".join(stmt for _t, stmt in ch_schema.get_post_ddl_alters())
    assert "tracer_eval_logger ADD COLUMN IF NOT EXISTS trace_session_id" in kept
    assert "idx_trace_session_id trace_session_id" in kept


def test_consistency_checker_skips_dropped_mirrors(chain_dropped):
    live = ch_consistency.ConsistencyChecker._live_monitored_tables()
    ch_tables = [ch for _pg, ch in live]
    assert "tracer_trace" not in ch_tables
    assert "trace_session" not in ch_tables
    # tracer_eval_logger is deliberately NOT retired — it still has a mirror.
    assert "tracer_eval_logger" in ch_tables


def test_consistency_checker_full_set_before_cutover(chain_retained):
    live = ch_consistency.ConsistencyChecker._live_monitored_tables()
    assert live == ch_consistency.ConsistencyChecker.MONITORED_TABLES


def test_eval_metrics_raw_table_is_never_retired():
    """The fallback target must stay alive in every configuration.

    ``EvalMetricsQueryBuilder`` degrades from AGG_TABLE to RAW_TABLE when the
    aggregate is dropped. If RAW_TABLE ever joined the legacy chain the
    fallback would break with no path left.
    """
    assert EvalMetricsQueryBuilder.RAW_TABLE not in ch_schema._LEGACY_CDC_CHAIN_NAMES


def test_eval_metrics_agg_table_is_retired_post_cutover(chain_dropped):
    """Pins why the builder needs the guard at all."""
    assert ch_schema.is_retired_table(EvalMetricsQueryBuilder.AGG_TABLE)
