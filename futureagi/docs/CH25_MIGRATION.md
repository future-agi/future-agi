# ClickHouse 25.3 + fi-collector Migration — State & Cutover

**Branch:** `feat/ch25-spans-migration`
**Last updated:** 2026-05-28

This doc captures **what's already cut over** from the legacy PeerDB-CDC
spans path to fi-collector + v2 typed-JSON, **what loopholes still remain**,
and the **cutover playbook** to fully decommission the legacy chain.

For the deeper design rationale (typed Maps, AggregatingMergeTree rollups,
storage policy), see the internal docs repo under `clickhouse-analytics/`.

---

## The two switches that control the cutover

| Switch                                  | Scope                                        | Dev/compose default | Prod default  | Effect when set                                                                                                           |
| --------------------------------------- | -------------------------------------------- | ------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `CH25_DROP_LEGACY_CDC_CHAIN` env var    | Boot-time schema apply + PeerDB mirror setup | **True**            | **False**     | Skip the 4 legacy DDL entries; issue `DROP IF EXISTS` for stale ones; skip the `tracer_observation_span` PeerDB connector |
| `drop_legacy_observation_span` mgmt cmd | One-shot PG table drop                       | dry-run audit       | dry-run audit | When run with `--force-drop` and the audit is clean, drops the PG `tracer_observation_span` table                         |

### `CH25_DROP_LEGACY_CDC_CHAIN`

Read at module import by `tracer/services/clickhouse/schema.py` and at
shell expand by `scripts/peerdb-setup-mirrors.sh`. Both sides honor the
same set of truthy values: `1`, `true`, `yes`, `on` (case-insensitive).

- **In docker compose:** the main `docker-compose.yml` sets it to `true`
  by default. fi-collector is the canonical writer for `spans`; the
  legacy chain is not created at all on a fresh boot.
- **In prod:** unset (defaults to `false`). The legacy `spans_mv`
  continues to write to `spans` alongside fi-collector; `spans` is
  double-written but ReplacingMergeTree dedupes on `_version`.

When the flag flips True on an existing CH instance, the boot hook in
`model_hub/apps.py:_ensure_analytics_schema()` runs
`get_legacy_chain_drop_statements()` first to clean up the stale tables
(since `CREATE IF NOT EXISTS` can't drop). The drops are MV-first so
dependency order holds, and every drop is `IF EXISTS` so reruns are
no-ops.

### `drop_legacy_observation_span` (Django mgmt command)

Audits whether the 4 PG readers have been migrated off
`tracer_observation_span`. By default it runs as a dry-run and prints a
green/red checklist. Pass `--force-drop` to actually drop the PG table;
the command refuses unless `CH25_DROP_LEGACY_CDC_CHAIN` is set AND the
audit is clean AND the operator confirms (or passes `--yes`).

Today the audit is **red** — 15 queries remain across the 4 readers.
That's the gate keeping the PG table alive for now.

---

## fi-collector — packaging and scale path

fi-collector is the OTLP gRPC receiver that writes spans directly to CH
25.3 via the v2 typed-JSON schema. It replaces the
PG → PeerDB → CH spans CDC path.

**Packaging:** ships as a top-level compose service in
`docker-compose.yml`, started by default with the rest of the backend.
Builds from `./fi-collector/Dockerfile`; the standalone test rig at
`fi-collector/docker-compose.standalone.yml` stays useful for collector-
only testing without the rest of the stack.

**Ports** (bound to 127.0.0.1):

- `4317` — OTLP gRPC ingest (configurable via `FI_COLLECTOR_OTLP_PORT`)
- `9464` — admin /healthz + Prometheus metrics (`FI_COLLECTOR_ADMIN_PORT`)

**Required env** (already wired in compose):

- `FI_CH_URL=http://clickhouse:8123` — same ClickHouse the backend uses
- `FI_GRPC_ADDR=:4317`, `FI_ADMIN_ADDR=:9464`
- `FI_DEAD_LETTER_FILE=/var/lib/fi-collector/dead_letter.jsonl`

**Dead-letter queue:** spans the collector couldn't write (typically
transient CH unavailability) are persisted to the `fi-collector-data`
named volume so they survive container restarts and can be replayed.

**Scaling path:** when single-host throughput becomes the bottleneck,
move fi-collector to its own deployment (its own cluster, k8s
Deployment with N replicas, etc.) — **no backend code change needed**.
SDKs send to the OTLP endpoint; backend reads from CH. The compose
service is the dev/single-host shape; the scale-out is a re-deploy
concern.

**ClickHouse version floor:** the v2 typed-JSON columns
(`002_spans_v2.sql`) require CH 25.3+. The compose `clickhouse` service
was bumped from 24.10 → 25.3-alpine in the same change that wired
fi-collector. **Devs upgrading from older compose stacks: run
`docker compose down -v` to reset the CH volume.** Typed-JSON tables
can't be created on top of a 24.x data directory.

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

### 1. Schema DDL still creates the legacy chain at boot (prod default)

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

**In dev / compose:** `CH25_DROP_LEGACY_CDC_CHAIN=true` is the default —
these 4 entries are filtered out at schema apply, and stale instances get
`DROP IF EXISTS`-ed on boot.

**In prod:** the flag is unset. The legacy chain stays until ops flips
the flag and runs through the cutover playbook below.

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

The Django model in `tracer/models/observation_span.py` is now marked
`[DEPRECATED]` in its docstring, but the table can't be dropped yet
because 4 PG readers still query it:

| Path                                                     | Queries today |
| -------------------------------------------------------- | ------------- |
| `tracer/socket.py`                                       | 5             |
| `tracer/utils/sql_queries.py`                            | 6             |
| `model_hub/utils/SQL_queries.py`                         | 3             |
| `ee/usage/management/commands/backfill_usage_summary.py` | 1             |

Run `python manage.py drop_legacy_observation_span` for the live audit
checklist. The command refuses `--force-drop` until all four show 0.

**Dev / compose:** the drop can be run safely once a future PR migrates
those readers to v2 CH `spans`. The mgmt command will then succeed.

**Prod:** runs manually after ops verifies the same audit is clean.

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

## Deployment

This branch ships as a **read-path swap on top of an already-running
write-path**. It is not a write-path change — fi-collector deployment
is independent and doesn't gate this PR.

### Rolling-deploy compatibility

- Old pods (pre-PR) read `tracer_observation_span` + `span_metrics_hourly`
  (still flowing via CDC). New pods read v2 `spans` + `spans_hourly_rollup`
  (also live).
- Both old and new tables/MVs are still in `SCHEMA_DDL_STATEMENTS` when
  `CH25_DROP_LEGACY_CDC_CHAIN` is unset (prod default), so a rolling
  deploy never has a window where one side is missing.
- `ensure_clickhouse_schema()` is idempotent (`CREATE IF NOT EXISTS`),
  so new pods coming up no-op on schema if it's already there.
- **No DB migrations** in this PR — no PG schema changes, no destructive
  CH DDL.

### Prerequisites (verify on the target CH cluster)

1. **CH version is 25.3.** Required by the v2 typed-JSON columns.
   Check: `SELECT version()`.
2. **`spans` is populated.** True if PeerDB CDC is still flowing OR
   fi-collector is live. Check:
   `SELECT count() FROM spans WHERE created_at >= now() - INTERVAL 1 HOUR`.
3. **`spans_hourly_rollup` is backfilled for the dashboard window.** ⚠️
   This is the one prerequisite that can silently break dashboards.

#### The backfill landmine

`spans_hourly_rollup_mv` is an **incremental** MV — it catches new
inserts only. If the rollup table was added recently, older hours have
no rows, and dashboard time-series silently shows empty bars for those
windows.

Check coverage:

```sql
SELECT toStartOfDay(hour) AS day, countMerge(n) AS span_count
FROM spans_hourly_rollup
WHERE project_id = '<active project>'
  AND hour >= now() - INTERVAL 90 DAY
GROUP BY day ORDER BY day;
```

If days are missing, run the manual backfill from
`v2/schema/010_hourly_downsample.sql` (the comment block at line 28-29
shows the exact `INSERT INTO spans_hourly_rollup SELECT … FROM spans`
statement). Run **before** the new code ships.

### Validation post-deploy

In order of "fastest to catch a regression":

1. **Dashboard time-series** — pick an active project, compare last-24h
   values between a canary pod and a production pod. Within 1–2% is
   healthy.
2. **Voice list smoke** — open the voice-call list for a project with
   voice traces; confirm per-row `span_attributes` columns aren't empty
   (would mean Map reconstruction is broken or `spans` lacks attrs).
3. **`get_span_attribute_keys_ch`** — open any "add filter" dropdown
   that exposes span-attribute keys. Empty for an active project = bug.
4. **CH error rate** — watch query-failure rate for 30 min post-deploy.
   New queries (`spans FINAL`, `spans_hourly_rollup` GROUP BY) should
   be cheaper than legacy, not more expensive.

### Rollback

Trivial because nothing was removed:

- Revert the commits and redeploy. Old code reads
  `tracer_observation_span` + `span_metrics_hourly`, still flowing.
- Or revert just one commit if a single path is misbehaving — they're
  scoped narrowly (time-series, voice-list, etc. each have their own
  commit).
- No DDL or data rollback needed.

### What does NOT happen on this deploy

To be explicit — the following are **future PR concerns**:

- ❌ Setting `CH25_DROP_LEGACY_CDC_CHAIN=true` in prod
- ❌ Stopping PeerDB CDC for `tracer_observation_span` in prod
- ❌ Dropping legacy DDL from prod CH instances
- ❌ Dropping the PG `tracer_observation_span` model
- ❌ Any fi-collector deployment change (it's a separate deployable)

Those follow the 6-step cutover playbook above after this PR has
soaked for the agreed-on window.

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
