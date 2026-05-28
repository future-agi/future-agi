# ClickHouse 25.3 + fi-collector Migration — State & Cutover

**Branch:** `feat/ch25-spans-migration`
**Last updated:** 2026-05-28

This doc captures **what's already cut over** from the legacy PeerDB-CDC
spans path to fi-collector + v2 typed-JSON, **what loopholes still remain**,
and the **cutover playbook** to fully decommission the legacy chain.

For the deeper design rationale (typed Maps, AggregatingMergeTree rollups,
storage policy), see the internal docs repo under `clickhouse-analytics/`.

---

## TL;DR — current state

| Component                                                               | Source                                                             | Status                                          |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------- |
| Voice trace list (Phase 1b attrs hydration)                             | `tracer/views/trace.py:4144`                                       | ✅ v2 `spans`                                   |
| Dashboard time-series                                                   | `query_builders/time_series.py`                                    | ✅ v2 `spans_hourly_rollup`                     |
| Span attribute keys discovery                                           | `query_service.get_span_attribute_keys_ch`                         | ✅ v2 typed Maps                                |
| CDC lag health check                                                    | `services/clickhouse/client.py`                                    | ✅ no longer tracks `tracer_observation_span`   |
| PG↔CH consistency monitor                                              | `services/clickhouse/consistency.py`                               | ✅ no longer tracks `tracer_observation_span`   |
| Trace list / span list / trace detail / span graph                      | `query_builders/{trace_list,span_list,trace_detail,span_graph}.py` | ✅ v2 `spans`                                   |
| Annotations / filters / eval metrics                                    | analytics builders                                                 | ✅ v2 `spans`                                   |
| `tracer_trace`, `trace_session`, `tracer_eval_logger`, `tracer_enduser` | PeerDB CDC → CH                                                    | ⏸ **intentionally kept** (still actively used) |
| `tracer_observation_span` CDC mirror                                    | PeerDB CDC → CH                                                    | ⚠️ still flowing — see loopholes below          |

The branch's last 4 commits (`8431494b7`, `32d1b7da7`, `e8df911be`, `ff09b3e8f`)
cut the last active CH read paths against `tracer_observation_span`. As of
this commit, **no CH analytics query in `tracer/views/` or
`tracer/services/clickhouse/` reads from `tracer_observation_span`.**

---

## Remaining loopholes (in priority order)

### 1. Schema DDL still creates the legacy chain at boot

`tracer/services/clickhouse/schema.py` keeps these in `SCHEMA_DDL_STATEMENTS`:

- `tracer_observation_span` — CDC landing table (was PeerDB target)
- `spans_mv` — MV that reads `tracer_observation_span` and writes enriched
  rows into `spans` (with `trace_dict` dictGet lookups)
- `span_metrics_hourly` — legacy aggregate table (no longer read by any
  query builder)
- `span_metrics_hourly_mv` — MV that fed the above

These run on every Django app boot via `apps.py`'s `ensure_clickhouse_schema()`.
While the PeerDB CDC stream is live they're harmless (double-write into `spans`
is dedup'd by ReplacingMergeTree on `_version`), but they're dead code once
CDC stops.

**Why not removed yet:** removing cascades into 4 asserts in `test_clickhouse.py`
(lines 72–73, 279, 282, 310) that pin ordering / structure of the DDL registry.
Removal is bundled with the PeerDB teardown so the tests change once, not twice.

### 2. PeerDB CDC stream for `tracer_observation_span` is still flowing

In production, PeerDB still mirrors the PG `tracer_observation_span` table to
CH. This means `spans_mv` is silently double-writing into `spans` alongside
fi-collector. ReplacingMergeTree dedup keeps results correct, but:

- We pay the CDC infra cost.
- We pay the `spans_mv` JSON-shred cost on every CDC row (the same cost that
  caused OOMs and motivated this whole migration).
- We can't drop `tracer_observation_span` from the PG → fi-collector ingest
  path until we know fi-collector is the sole CH writer.

### 3. No automated cutover health-check

There's no script that compares `spans` row counts written by `spans_mv`
(legacy path) vs fi-collector (new path) on the same time window. Before
stopping CDC, someone has to manually run a SQL comparison and trust the
result.

A safe cutover wants:

```sql
SELECT
  toStartOfHour(start_time)                AS hour,
  countIf(semconv_source = 'pg_cdc')       AS legacy_count,
  countIf(semconv_source = 'fi_collector') AS new_count,
  legacy_count - new_count                 AS gap
FROM spans
WHERE created_at >= now() - INTERVAL 24 HOUR
GROUP BY hour ORDER BY hour;
```

`semconv_source` already exists on the v2 schema (see
`v2/schema/002_spans_v2.sql:128`) — fi-collector sets it; `spans_mv` needs
to set it too if we want a clean differentiator. Today both paths leave it
empty, so this query needs `spans_mv` to be patched first.

### 4. Stale comments + EE one-shot scripts

- `trace.py:1092`, `trace.py:2996` and `v2/eval_loader.py` still mention
  the legacy `tracer_observation_span` in docstrings. Cleanup-grade.
- `ee/internal/scripts/` has several one-shot backfill scripts that query
  `tracer_observation_span` (`data_migration_span_attributes*`,
  `backfill_spans_maps`, `backfill_fi_convention_spans`). They've already
  run; keep for archival or delete after we're sure no rerun is needed.

### 5. PG `tracer_observation_span` Django model still exists

Out of scope for the CH25 cutover, but worth knowing: `socket.py`,
`tracer/utils/sql_queries.py`, `model_hub/utils/SQL_queries.py`, and
`ee/usage/management/commands/backfill_usage_summary.py` still read the
PG table via Django `connection`. fi-collector + the OTLP ingest path
mean writes have moved to CH-first, but the PG model and its readers are
a **separate decom migration**.

---

## Cutover playbook (the order matters)

1. **Patch `spans_mv` to stamp `semconv_source = 'pg_cdc'`** so we can
   differentiate legacy vs new rows in `spans`.
2. **Run the health-check** above for at least 24h. Acceptable gap depends
   on traffic; for high-volume projects, < 1% per hour is a reasonable bar.
3. **Stop the PeerDB connector** for the `tracer_observation_span` table.
   Other connectors (`tracer_trace`, `trace_session`, `tracer_eval_logger`,
   `tracer_enduser`) stay.
4. **Wait 2× the longest dashboard window** (90 days for the
   `spans_hourly_rollup` retention) before considering CDC truly retired —
   in case rollback is needed.
5. **Drop the DDL chain in one PR:**
   - Remove from `SCHEMA_DDL_STATEMENTS`:
     - `("tracer_observation_span", CDC_OBSERVATION_SPAN)`
     - `("spans_mv", SPANS_MV)`
     - `("span_metrics_hourly", SPAN_METRICS_HOURLY)`
     - `("span_metrics_hourly_mv", SPAN_METRICS_HOURLY_MV)`
   - Update `test_clickhouse.py` asserts (lines 72–73, 279, 282, 310).
   - Add a manual cleanup migration that issues `DROP TABLE` /
     `DROP VIEW` on existing CH instances (since `CREATE IF NOT EXISTS`
     in the DDL registry won't drop pre-existing tables).
6. **Tear down PeerDB infra** if `tracer_observation_span` was the last
   connector pinning the stack. (Today it isn't — the other 4 connectors
   keep PeerDB alive.)

---

## Test reference map

Unit tests pinning the migration (all under `tracer/tests/test_clickhouse.py`):

| Class                                               | Tests | Purpose                                                                                                |
| --------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------ |
| `TestTimeSeriesQueryBuilder`                        | 13    | v2 `spans_hourly_rollup` path: `*Merge` combinators, partition column, response contract               |
| `TestVoiceCallListPhase1bMigration`                 | 4     | Voice list Phase 1b reads `spans FINAL` not the CDC mirror; typed-Map reconstruction                   |
| `TestSchemaCreation` (~`test_clickhouse.py:72-300`) | —     | Asserts the legacy DDL still exists in the registry; **these break** when step 5 of the playbook lands |

Run them:

```bash
.venv/bin/python -m pytest tracer/tests/test_clickhouse.py \
    -m unit -q --no-header
```

E2E coverage for the fi-collector path lives in `docs/E2E_TESTS.md`.

---

## What stays (intentional)

These CDC mirrors and CH dictionaries are **not** part of this migration:

- `tracer_trace` → `trace_dict` (used by `spans_mv` enrichment and the
  dashboard project-id routing query)
- `trace_session` → `trace_session_dict` (used by `eval_metrics_hourly_mv`
  for session-target eval rows where `trace_id IS NULL`)
- `tracer_eval_logger` (read directly by the voice list eval-config
  discovery query and the analytics eval-metrics path)
- `tracer_enduser` → `enduser_dict` (end-user attribute attachment)

Their CDC streams + DDL are out of scope for the spans cutover.
