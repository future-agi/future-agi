# Tracer App — Specification

The tracer app is the **OTLP span ingestion and observability pipeline**. It receives OpenTelemetry
trace data from instrumented agents, normalises it, stores it in PostgreSQL (transactional) and
ClickHouse (analytics), and drives downstream error detection, clustering, and evaluation.

---

## Architecture

```
Client SDK (OTLP/HTTP or OTLP/gRPC)
    → OTLPTraceView / ObservationSpanService
    → Redis (payload staging)
    → Temporal activity: bulk_create_observation_span_task
    → PostgreSQL (Trace, ObservationSpan, EndUser, TraceSession)
    → ClickHouse (async dual-write, analytics)
    → scan_traces_task → embed_trace_inputs_task → cluster_scan_issues_task
```

---

## Ingest Entry Points

### HTTP: `POST /v1/traces`

Handled by `OTLPTraceView` (`views/`). Also bound at `/v1/traces/` (trailing-slash alias)
and `/api/public/otel/v1/traces` (Langfuse SDK v3+ compatibility path).

**Contract:**

| Aspect | Requirement |
|--------|-------------|
| Content-Type | `application/x-protobuf` (binary) or `application/json` |
| Content-Encoding | `gzip` is transparently decompressed |
| Auth | Django `IsAuthenticated` + workspace context from request middleware |
| Rate limit | EE only: `RateLimiter.check(org_id, "ingestion")` — no-op in OSS |
| Response | `ExportTraceServiceResponse` in matching format; `partial_success` carries rejection counts |
| Status | 200 OK always (spans processed asynchronously; not a promise of storage) |

**Invariants:**

- The view stores the raw payload to Redis and returns immediately — it never writes to the database
  synchronously.
- `partial_success.rejected_spans == 0` means the request was accepted, not that it was processed.
- Rate limit exceeded → 429 with `ExportTraceServiceResponse` carrying error message (EE only).

### gRPC: `Export(ExportTraceServiceRequest) → ExportTraceServiceResponse`

Handled by `ObservationSpanService` (in `services/grpc.py` or `grpc/`), registered via
`grpc_handlers(server)` in `handlers.py`.

Same contract as HTTP: store to Redis, queue Temporal activity, return immediately.

### Health: `GET /v1/health`

No authentication. Always returns 200 OK. Used by load-balancer liveness checks.

---

## Ingestion Pipeline — `bulk_create_observation_span_task`

Temporal activity (not Celery). Queue: `trace_ingestion`. Timeout: 3600s. Max retries: 0.

### Steps

1. **Retrieve and deserialise** the payload from Redis (`payload_storage.retrieve(payload_key)`).
   Supports `payload_format = "json"` (HTTP path) or `"protobuf"` (gRPC path).

2. **Parse OTEL structure**: `_parse_otel_request()` flattens
   `resource_spans → scope_spans → spans` into a list of dicts with:
   - `trace_id`, `span_id`, `parent_span_id` — hex-encoded (base64-decoded if needed)
   - `name`, `start_time_unix_nano`, `end_time_unix_nano`
   - `attributes` — OTLP key-value array converted to flat dict
   - `events` — list with timestamps
   - `status` — mapped: `STATUS_CODE_UNSET` → `"UNSET"`, etc.
   - `latency_ms` — `(end_time - start_time) / 1e6`
   - `resource_attributes` — extracted: `project_name`, `project_type`, etc.

3. **Normalise attributes**: `normalize_span_attributes()` converts OpenInference, OpenLLMetry,
   and other semantic convention variants to the internal `fi.*` namespace.

4. **PII scrub**: `scrub_pii_in_span_batch()` applies per-project PII rules to
   `input`/`output` fields. Rules fetched once per batch via `get_pii_settings_for_projects()`.

5. **Convert to model dicts**: `bulk_convert_otel_spans_to_observation_spans()` maps each
   parsed span dict to:
   - `observation_span` — dict of fields for `ObservationSpan`
   - `trace` — `uuid.UUID`
   - `project` — `Project` instance
   - `end_user`, `prompt_details`, `session_name` — optional extras

6. **Database writes** (inside `transaction.atomic()`):
   - `_fetch_or_create_traces()` — bulk get-or-create `Trace` rows using PostgreSQL COPY;
     handles `UniqueViolation` race conditions by re-fetching.
   - `_fetch_or_create_end_users()` — same pattern for `EndUser`.
   - `_fetch_or_create_sessions()` — same for `TraceSession`.
   - `_fetch_prompt_versions()` — link LLM spans to `PromptVersion` rows.
   - `_bulk_insert_observation_spans()` — PostgreSQL COPY insert of `ObservationSpan` rows.
   - `_bulk_update_traces()` — update `input`, `output`, `session` on `Trace` from root spans.

7. **Queue scanner**: `_trigger_trace_scanner()` queues `scan_traces_task` for any trace
   whose root span (`parent_span_id IS NULL`) has an `end_time` set, filtered to
   `project.type == "observe"` (not `"experiment"`).

8. **Usage metering** (outside transaction): emit `BillingEventType.TRACING_EVENT` with
   `amount = num_traces`. OSS stub is a no-op.

### Invariants

- All DB writes for a batch are in one `transaction.atomic()`. If any step raises, nothing is
  committed for that batch.
- `_fetch_or_create_traces()` uses `ON CONFLICT DO NOTHING` semantics via COPY; a `UniqueViolation`
  triggers a re-fetch of the already-inserted row. This is racy under extreme concurrency — the
  re-fetch may race with in-flight inserts from another worker.
- `_bulk_update_traces()` writes `input`/`output` only from root spans. If a batch contains
  multiple root spans for the same trace, the last one processed wins.
- Metering fires after the transaction commits; billing can under-count if the process crashes
  after commit but before `emit()`.

---

## Data Models

### `Trace` (`tracer_trace`)

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID, PK | |
| `project_id` | FK → `tracer_project` | |
| `name`, `input`, `output`, `error` | JSONField | Root span values written by `_bulk_update_traces()` |
| `session_id` | FK → `TraceSession`, nullable | |
| `external_id` | varchar, indexed | External reference key |
| `tags` | JSONArray | |
| `error_analysis_status` | enum | `pending / processing / completed / skipped / failed` |

**Indexes:** `(project, created_at)`, `(project_version)`, `(session)`, `(external_id)`

### `ObservationSpan` (`tracer_observation_span`)

| Field | Type | Notes |
|-------|------|-------|
| `id` | varchar 255, PK | Hex span_id |
| `trace_id` | FK → `tracer_trace` | |
| `parent_span_id` | varchar, nullable | `NULL` → root span |
| `observation_type` | enum | `tool / chain / llm / retriever / embedding / agent / reranker / unknown / guardrail / evaluator / conversation` |
| `start_time`, `end_time` | DateTime | |
| `input`, `output` | JSONField | Can be large |
| `model`, `model_parameters` | varchar / JSONField | LLM spans only |
| `latency_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens` | numeric | |
| `status`, `status_message` | enum | `UNSET / OK / ERROR` |
| `eval_status` | denormalized snapshot | **Design flaw** — see ADR 009 |
| `semconv_source` | enum | `traceai / otel_genai / openinference / openllmetry` |
| `span_attributes`, `resource_attributes` | JSONField | Raw OTEL for ClickHouse migration |

**Indexes:** `(trace, created_at)`, `(project, created_at)`, `(parent_span_id)`,
`(observation_type)`, GIN on `span_attributes`

### `EndUser` (`tracer_end_user`)

- **Unique constraint:** `(project, organization, user_id, user_id_type)`
- `user_id_type` is optional. `NULL` values are not unique in SQL, so multiple rows with
  `user_id_type = NULL` for the same `(project, organization, user_id)` can coexist —
  violating the deduplication intent.

### `TraceErrorGroup` (`tracer_trace_error_group`)

One row per error cluster. `cluster_id` is a short code (`"C001"`, etc.).
- **Unique constraint:** `(project_id, cluster_id)` where `deleted = False`
- `status`: `escalating / for_review / acknowledged / resolved`
- `success_trace_id`: FK to a trace where the same scenario succeeded (KNN-matched)
- `external_issue_url`, `external_issue_id`: Linear/GitHub/Jira integration

### `EvalLogger` (`tracer_eval_logger`)

Links evaluation results to spans. One row per eval × span.
- `output_bool`, `output_float`, `output_str`, `output_str_list` — typed output columns
- `error`, `error_message` — for failed evals

---

## Analytics Dual-Write

PostgreSQL is the source of truth. ClickHouse receives async write-through for analytical queries.

**`ClickHouseWriter`** (background thread):
- Batches writes with configurable `batch_size` (default 1000) and `flush_interval` (5s).
- Up to 3 retries per batch.
- Non-blocking — the ingestion pipeline never waits for ClickHouse confirmation.

**Invariants:**
- ClickHouse data has an unknown consistency window after a Postgres write. Analytics queries
  may read stale data for up to several seconds.
- If the `ClickHouseWriter` thread crashes, writes are lost (no replay from Postgres WAL).
- Analytics queries fall back to Postgres if ClickHouse is unavailable.

---

## Error Detection Pipeline

After ingestion, completed traces flow through a three-stage async pipeline:

### Stage 1: `scan_traces_task(trace_ids, project_id)`

- Waits `SCAN_DELAY_SECONDS = 10` (hardcoded) for straggler child spans.
- Invokes `TraceErrorAnalysisAgent` (EE) which produces `TraceErrorDetail` rows.
- Each detail has: `error_id`, `cluster_id` (temporary assignment), `category`, `impact`,
  `urgency_to_fix`, `location_spans`, `root_causes`, `recommendation`.
- On completion: chains to `embed_trace_inputs_task`.

### Stage 2: `embed_trace_inputs_task(trace_ids, project_id, trigger_clustering)`

- Computes embeddings for root span inputs via `kevinify`.
- Writes embeddings to ClickHouse for KNN lookup.
- If `trigger_clustering = True`: chains to `cluster_scan_issues_task`.

### Stage 3: `cluster_scan_issues_task(project_id)`

- Online incremental clustering (no full recompute).
- Fetches unclustered embeddings from ClickHouse.
- Soft-matches against existing cluster centroids (threshold distance).
- Runs HDBSCAN on unmatched errors to form new clusters.
- For each cluster: KNN-matches a "success trace" (nearest trace without errors).
- Writes cluster centroids to ClickHouse; creates/updates `TraceErrorGroup` in Postgres.

**Invariants:**
- The 10s delay in Stage 1 fires regardless of trace completeness — single-span traces wait
  the same as 100-span traces.
- Clustering is append-only. Old centroids are never expired or consolidated.

---

## Error Feed API

REST endpoints under `/tracer/feed/issues/`.

### `GET /tracer/feed/issues/`

**Query parameters:**
`project_id`, `search`, `status`, `fix_layer`, `source`, `issue_group`, `time_range_days`,
`sort_by` (`last_seen / first_seen / error_count`), `sort_dir`, `limit`, `offset`

**Response shape (per issue):**
```json
{
  "cluster_id": "C001",
  "source": "scanner",
  "error": {"name": "...", "type": "..."},
  "status": "escalating",
  "occurrences": 15,
  "trace_count": 10,
  "fix_layer": "Tools",
  "first_seen": "...",
  "last_seen": "...",
  "trends": [{"timestamp": "...", "value": 2, "users": 1}]
}
```

### `PATCH /tracer/feed/issues/{cluster_id}/`

Updates `status`, `severity`, `assignee` on a `TraceErrorGroup`.

### Sub-resource endpoints

| Path | Returns |
|------|---------|
| `{cluster_id}/overview/` | `OverviewResponse` — events over time, key moments, pattern summary |
| `{cluster_id}/traces/` | Paginated list of `TracePreview` in the cluster |
| `{cluster_id}/trends/` | KPI trends, daily events, heatmap |
| `{cluster_id}/sidebar/` | Timeline, AI metadata, evaluations, co-occurring issues |
| `{cluster_id}/deep-analysis/` | On-demand deep analysis dispatch |
| `{cluster_id}/root-cause/` | Root cause analysis for the cluster |
| `issues/stats/` | Aggregate counts by status |

---

## WebSocket: Real-Time Metrics

**Path:** `ws://.../ws/graph_data/`  
**Handler:** `GraphDataConsumer` (`socket.py`)

**Client message (subscribe):**
```json
{
  "projectId": "uuid",
  "filters": [{"columnId": "...", "filterConfig": {...}}],
  "interval": "hour|day|week|month",
  "property": "count|average|p50|p95|...",
  "graph": "trace|span|charts",
  "evalIds": ["eval_config_uuid"]
}
```

**Server pushes:**
- `"type": "traffic"` — COUNT(id) per interval bucket
- `"type": "latency"` — AVG(latency_ms) for root spans only (`parent_span_id IS NULL`)
- `"type": "cost"` — SUM(prompt_tokens × rate_input + completion_tokens × rate_output)
- `"type": "tokens"` — SUM(total_tokens)
- `"type": "evaluations"` — per eval-config time-series with `{timestamp, value, primary_traffic}`

**Invariants:**
- Missing time buckets are filled with zeros (no gaps in time series).
- Evaluation queries join `EvalLogger → ObservationSpan → TraceErrorGroup` without pagination.
  For large projects with >10k evaluations, this query can timeout.

---

## Semantic Convention Normalisation

`normalize_span_attributes()` (`utils/adapters/`) maps:

| Source namespace | Target namespace |
|-----------------|-----------------|
| OpenInference (`openinference.span_kind`, etc.) | `fi.*` |
| OpenLLMetry (`llm.request.*`, etc.) | `fi.*` |
| OTEL GenAI (`gen_ai.*`) | `fi.*` |
| TraceAI (`fi.*` already) | no-op |

`semconv_source` field records which adapter was applied.

---

## PII Scrubbing

`scrub_pii_in_span_batch()` applies per-project rules. Rules are loaded once per batch from
`get_pii_settings_for_projects()`. Scrubbing mutates `input`/`output` fields in-place before
database write.

---

## Known Design Issues

- **`eval_status` on `ObservationSpan` is stale** — see ADR 009.
- **Root span `input`/`output` is last-writer-wins** in a batch — see ADR 010.
- **Scanner 10s delay is unconditional** — see ADR 011.
- **ClickHouse consistency window is undefined** — see ADR 012.
- **`EndUser.user_id_type = NULL` breaks deduplication** — filed as issue #305.
- **Error cluster centroids never expire** — filed as issue #306.
- **WebSocket evaluation query has no pagination** — filed as issue #307.
