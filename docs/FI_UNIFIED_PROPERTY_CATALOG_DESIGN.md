# CATALOG Unified Property Catalog Design

Status: implemented and qualified on DEV on 2026-08-18. All 90 eligible DEV
workspaces are activated in the isolated catalog and the authenticated read
path is enabled for those workspaces. No production rollout is authorized.

The runtime also carries a dormant production deployment contract. It requires
a production-only acknowledgement, rejects DEV catalog database names, and
keeps the 1..256 workspace allowlist/revision-fence bound. This is a canary
mechanism, not authorization for a production activation or an all-workspace
hot-ingestion claim. In particular, it must not be enabled inside the
autoscaled collector deployment: the current local sequence/spool ownership
does not provide distributed single-writer failover for the one ordered hot
stream.

## 1. Objective

All property definitions must be searched and paginated from one ClickHouse
table. Callers must not merge system properties, custom attributes, evals,
annotations, dataset columns, or simulation definitions in the frontend.

The public contracts remain:

- `GET /tracer/dashboard/metrics/` — unified definition search and keyset
  pagination.
- `GET /tracer/dashboard/filter_values/` — unified value lookup routed by the
  selected stable `property_id`.

Property definitions are unified. Large fact/value populations remain in their
native stores and are reached through a server-owned value adapter.

## 2. Pre-release reset decision and current DEV state

Schemas 025–027 and their six DEV tables were never activated in production.
The earlier empty DEV tables were removed during the pre-release reset. The
clean schema was subsequently recreated only in
`fi_catalog_dev_kartik_0817j`, backfilled, qualified, and activated for all
90 eligible DEV workspaces. Canonical ClickHouse and PostgreSQL sources were
read-only throughout the rollout.

The replacement table inventory is exactly:

1. `property_definition_catalog`
2. `span_attribute_value_catalog`
3. `property_catalog_checkpoints`
4. `property_catalog_activations`
5. `property_catalog_deliveries`
6. `property_catalog_source_streams`

Only `property_definition_catalog` contains property definitions. The value
table contains selectable observed span-attribute values. The other four tables
are control-plane ledgers, not parallel definition catalogs.

The catalog is installed only from three clean CREATE-only schema files:

- `025_property_catalog_data.sql`
- `026_property_catalog_state.sql`
- `027_property_catalog_delivery.sql`

## 3. Definition row contract

Each row represents one versioned property-to-visibility binding. Required
columns are grouped below; exact ClickHouse types are pinned in schema 025.

### Tenant and publication identity

- `organization_id`, `workspace_id`
- `catalog_epoch`, `catalog_revision`, `build_token`, `projection_version`
- `binding_id` — domain-separated SHA-256 over tenant, scope, adapter, and
  stable property identity

`build_token` identifies one immutable attempt at a revision. Both definition
and hot-value rows carry it, and it must match the signed delivery envelope.
Rows from an abandoned or failed build are therefore never reinterpreted as
part of a later successful activation.

### Visibility

- `visibility_scope`: `always`, `workspace_default`, `project`,
  `agent_definition`, or `dataset`
- `visibility_id`

Workspace searches read already-projected workspace bindings. Project-filtered
searches read the workspace/always rows plus only the authorized project
bindings. The API never infers tenancy from a client-supplied UUID.

### Stable property identity and routing

- `property_id` — namespaced public identity
- `property_kind`: `system_attribute`, `custom_attribute`, `eval_template`,
  `eval_config`, `annotation`, or `dataset_column`
- `category`, `category_rank`, `source_rank`
- `definition_source`, `primary_source`, and bounded `source_tokens`
- `value_adapter`

Examples include `system_attribute:traces:model`,
`custom_attribute:customer.plan`, `eval_config:<uuid>`,
`annotation:<uuid>`, and `dataset_column:<uuid>`.

### Search and rendering

- `name`, `display_name`
- `sort_name_folded`, `search_text_folded`
- `value_type`, `output_type`, and `role`
- bounded canonical `definition_json` (maximum 32 KiB)
- `definition_sha256`

Folded strings are produced by the shared codec, not by ClickHouse `lower()`.
Python and Go golden fixtures must prove identical Unicode folding and JSON.

### Version and deletion state

- `source_adapter`, `source_version`, `source_fingerprint`
- `is_deleted`, `deleted_at`
- `state_sha256`, `emitted_at`
- producer stream and sequence identity

Deletes and lost visibility relationships are tombstones. A later restore is a
new live version. Rows are never physically removed from an active cursor
epoch.

## 4. Source adapters

| Adapter                 | Definition source                                         | Visibility output                                                |
| ----------------------- | --------------------------------------------------------- | ---------------------------------------------------------------- |
| System manifest         | One canonical checked-in manifest                         | Always/workspace plus source-specific project definitions        |
| Span attributes         | Kafka/direct collector extraction                         | Project bindings; reconciler also emits workspace-union bindings |
| Eval templates          | Tenant-scoped PostgreSQL snapshot                         | Workspace defaults and configured project bindings               |
| Eval configs            | Tenant/project PostgreSQL snapshot                        | Project bindings                                                 |
| Simulation eval configs | Active run-test/agent PostgreSQL snapshot                 | Agent-definition bindings                                        |
| Annotation labels       | Label definitions plus active project/Score relationships | Workspace defaults and project bindings                          |
| Dataset columns         | Active dataset/column PostgreSQL snapshot                 | Workspace and dataset bindings                                   |

PostgreSQL adapters use read-only repeatable-read snapshots. They do not mutate
source rows. A bounded minute-scale reconciler emits versioned rows through the
same property-catalog envelope. The durable AUTO lifecycle selects a full
streaming merge/diff when the active lineage anchor reaches 24 hours or 2,048
active revisions, repairing missed updates, relationship removals, bulk
changes, and tombstones.

## 5. Kafka/direct envelope

The transport uses one generic property-catalog envelope. It contains:

- tenant, epoch, revision, and source adapter
- producer stream, sequence, previous-payload digest
- source version/fingerprint and source-batch digest
- definition/value/tombstone counts and explicit gap outcome
- bounded chunks targeting only allowlisted property-catalog tables

The existing durable spool, deterministic JSON, SHA-256 chain, bounded chunks,
duplicate handling, and data-first/ledger-last commit order are retained.
Every chunk is fully decoded and validated before the first ClickHouse insert.

The collector continues to extract high-volume custom/system values after the
canonical span write succeeds. That hot path is acceleration only and emits no
definition rows. An authoritative bounded canonical-span reconciliation owns
the complete span-attribute definition/type union and value/source-audit proof.
Relational reconcilers publish definitions through the same transport rather
than writing a second catalog.

### Runtime tuning

Operational producer limits have one Go settings snapshot. YAML may set the
same fields and the environment wins at process startup. Invalid, zero, or
out-of-range overrides fail startup instead of silently falling back.

| Environment override | Default | Purpose |
| --- | ---: | --- |
| `FI_PROPERTY_CATALOG_REPLAY_INTERVAL` | `1s` | Durable-spool replay cadence |
| `FI_PROPERTY_CATALOG_QUEUE_DEPTH` | `64` | In-memory submission queue |
| `FI_PROPERTY_CATALOG_MAX_SPANS_PER_BATCH` | `20000` | Accepted canonical spans per hot batch |
| `FI_PROPERTY_CATALOG_MAX_KEYS_PER_SPAN` | `128` | Extracted keys per span |
| `FI_PROPERTY_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN` | `256` | Flattened array members per span |
| `FI_PROPERTY_CATALOG_MAX_ENCODED_BYTES_PER_SPAN` | `65536` | Encoded attribute budget per span |
| `FI_PROPERTY_CATALOG_MAX_CHUNK_ROWS` | `2000` | Rows per catalog envelope chunk |
| `FI_PROPERTY_CATALOG_MAX_CHUNK_BYTES` | `262144` | Bytes per catalog envelope chunk |
| `FI_PROPERTY_CATALOG_MAX_SPOOL_FILES` | `10000` | Durable spool file ceiling |
| `FI_PROPERTY_CATALOG_MAX_SPOOL_BYTES` | `536870912` | Durable spool byte ceiling |
| `FI_PROPERTY_CATALOG_KAFKA_DELIVERY_TIMEOUT` | `10s` | Producer delivery wall |
| `FI_PROPERTY_CATALOG_DELIVERY_TIMEOUT` | `10s` | Consumer data-plus-ledger and ClickHouse wall |

Environment values may lower or tune operational defaults only within the
reviewed hard bounds. Wire versions, hash sizes, record ceilings, and other
protocol invariants are named code constants and are intentionally not runtime
configuration.

## 6. Epoch, revision, and activation lifecycle

An issued cursor must never observe rows moving underneath it.

1. A coordinator durably allocates a never-reused revision and build token.
   Writers publish only while that exact lease is in `building` state; every
   chunk and ledger write rechecks the fence.
2. Every expected adapter/stream must report a contiguous, terminal,
   gap-free delivery chain.
3. Checkpoints prove source counts, emitted rows, tombstones, and rolling
   digests.
4. Workspace-union attribute bindings and every relational adapter complete.
5. Qualification rejects missing adapters, count drift, same-version state
   conflicts, gaps, truncated payloads, or incomplete streams.
6. Activation atomically advances the workspace to the qualified revision.
   Readers construct a conflict-checked lineage of every successfully active
   `(revision, build_token)` at or below the cursor revision and join both data
   tables to that lineage. Aborted and disabled builds are invisible even when
   later revisions activate.
7. Old revisions remain for at least the maximum signed-cursor lifetime and
   rollback window.

Fresh definitions become visible after the next bounded revision (target two
minutes). Values remain available from their native value readers.

### Scheduled lifecycle and sidecar isolation

Steady-state reconciliation uses exactly one default-off Temporal schedule per
deployed sidecar:

- interval: 120 seconds;
- queue: `property_catalog_dev_sidecar`;
- overlap policy: skip;
- admitted scope: exactly one organization/workspace allowlist entry;
- runtime: one Python worker and one Go collector/consumer boundary sharing one
  durable POSIX volume and revision-fence/drain-proof files;
- initial backfill: explicit operator action, never scheduled;
- normal scheduled mode: AUTO, resolved to incremental or full repair from
  persisted state;
- wall: scheduled/default work is hard-capped at 100 seconds; only the explicit
  management-command initial backfill may request `--initial-backfill-wall-ms`
  in `100001..1740000`, preserving at least 60 seconds of headroom inside an
  immutable initial-build lease of at most 1,800 seconds. Scheduled and default
  revisions retain the 600-second lease;
- full repair: selected at 24 hours or 2,048 active revisions since the
  lineage anchor.

There is no separate daily repair schedule. A second workspace requires an
independent sidecar, task queue, and durable volume; a sidecar refuses an
allowlist containing more than one workspace before starting work.

## 7. Unified search and pagination

Stable order is:

```text
(category_rank, source_rank, primary_source_folded,
 sort_name_folded, name, property_id)
```

The API resolves the latest non-conflicting binding state at or below the
activated revision, applies authorized visibility, removes tombstoned rows,
deduplicates by `property_id`, and fetches `page_size + 1` with keyset
pagination. Offset pagination is not used for the new reader.

The signed cursor binds:

- organization and workspace
- exact authorized project-scope fingerprint and optional agent/dataset scope
- category, source, eval mode, normalized search, and page size
- catalog epoch/revision and activation fingerprint
- last ordering tuple

Changing any scope/filter invalidates the cursor. Retained old revisions make
the same cursor deterministic even while newer Kafka deliveries arrive. Cursor
mode deliberately returns `total=null` and `total_is_exact=false`; it never
runs an expensive count merely to decorate a page.

One endpoint wall is at most 8.5 seconds. ClickHouse receives shrinking
per-query limits below that wall, finite rows/bytes/memory/threads, and a
`page_size + 1` response cap. Deadline or completeness failure returns a typed
sanitized 503; partial results are never published as complete.

## 8. API and frontend cutover

The local implementation now has:

1. `GET /tracer/dashboard/metrics/?cursor_mode=true` reading one activated
   definition table with signed keyset pagination. The non-cursor response is a
   deprecated rolling-compatibility shape.
2. `GET /tracer/dashboard/filter_values/` routing values by validated stable
   `property_id` to the native adapter.
3. One `usePropertyCatalog` infinite-query hook used by Widget Editor,
   Trace/Span/Session/Voice property pickers, Primary Graph, Journey
   Attributes, automation rules, and dataset pickers.
4. `/api/traces/span-attribute-keys/` retained for specialized attribute-only
   compatibility/eval-mapping flows, not as the unified definition source.
5. Typed not-ready handling and guarded legacy fallback for the explicit
   rolling-compatibility paths.

## 9. Implementation and rollout state

Complete in code and on DEV:

- clean six-table schemas and exact topology validation;
- Python/Go canonical codecs, durable spool, Kafka producer/consumer, and
  allowlisted ClickHouse sink;
- source adapters, bounded reconciliation, checkpoint qualification,
  activation lineage, lifecycle recovery, and daily/depth repair selection;
- single-table definition reader, value reader, signed cursors, unified API,
  frontend hook, and generated contracts;
- one-workspace-per-sidecar, 120-second default-off DEV schedule and dedicated
  queue;
- organization-wide DEV inventory of 90 workspaces and 263 owned projects;
- bounded initial backfill and activation of all 90 eligible workspaces in the
  isolated six-table catalog;
- authenticated unified reads enabled for the same 90-workspace admission set;
- upper stacked-PR frontend deployed from commit `7aa5c925f` by successful
  GitHub Actions run `32091720813`.

Still pending and deliberately not claimed:

1. Run the separately approved production deployment/backfill.
2. Compare complete legacy/unified pages and run dense Whatfix, sparse
   Colektia, Mudflap voice, all nine windows, all filter combinations, and
   production-scale frontend/API latency checks.
3. Observe a non-empty, naturally occurring hot-span delivery after an active
   incremental revision. The DEV ledger proves the 90-workspace Kafka
   terminal/drain handshake, but its 91 hot deliveries are zero-data terminal
   boundaries. No synthetic canonical span was inserted because source writes
   were outside the DEV safety authorization.

Production catalog schema/data remains untouched until separate user approval
after DEV evidence and code review. Historical read-only qualification does not
authorize or qualify a unified-catalog production rollout.

## 10. Mandatory proof gates

- Exact DDL/table/engine/topology inventory and zero unrelated schema drift
- Python/Go golden codec and Unicode order fixtures
- Stable concatenated pages across page sizes and Unicode edge cases
- Cursor tenant/filter/revision binding and tamper rejection
- Same-version conflict, stale replay, duplicate, tombstone, and restore tests
- Kafka poison message, crash-before-ledger, retry, and rebalance tests
- Checkpoint resume and incomplete-run non-activation
- Full source-adapter count/digest reconciliation
- Legacy/new ordered parity for all categories and scopes
- Dense Whatfix and sparse Colektia property searches and read-more chains
- 30m, 1h, 6h, 24h, 7d, 30d, 90d, 180d, and 365d API matrix
- Every request below 10 seconds or a truthful typed bounded failure
- Proof that no legacy PostgreSQL or ClickHouse row was mutated

### Current local and DEV evidence

Latest recorded evidence is:

- 404 focused Python property/unified-catalog tests passing;
- five catalog/auth/server/consumer Go packages passing in normal and race
  modes;
- 278 focused frontend tests passing;
- 112 generated/filter/OpenAPI contract tests passing;
- generated-contract validation at 980 paths / 1,325 operations and registry
  coverage at 789/789;
- changed-source Ruff/format and Python compilation, Prettier/ESLint, Go
  formatting, and diff checks green;
- 1,585/1,585 changed frontend tests passing in the final stacked-PR gate;
- exactly six DEV catalog tables: 91 activation rows, 918 checkpoint rows,
  11,426 delivery rows, 1,104 source-stream rows, 89,894 physical definition
  rows, and 2,151,977 physical value rows;
- 90/90 eligible workspaces active, covering the 263-project authorized
  inventory; 289 legacy projects without a workspace were excluded rather
  than guessed into a tenant;
- authenticated DEV definition reads for Trace, Span, Session, User, Voice,
  and Prompt sources returning HTTP 200 in 1.02–1.17 seconds; category pages
  retained exact invariant counts and cursor pages were disjoint;
- an authenticated custom-value read returning 17 values in 1.19 seconds,
  both reported user-session list shapes returning HTTP 200 in 1.84–1.89
  seconds, Eval detail/usage returning HTTP 200 in 0.34–0.45 seconds, and the
  25-row Simulate selector returning HTTP 200 in 1.54 seconds;
- no PostgreSQL property-catalog tables and no production changes.

This evidence proves the exercised local contracts and the named DEV scopes.
It does not prove Whatfix/Colektia/Mudflap production-population coverage,
production-scale latency, or any production result.

## 11. DEV cleanup evidence (2026-08-14)

- PostgreSQL contained none of the proposed five projection tables, related
  migrations, triggers, or functions; no PostgreSQL cleanup was required.
- The isolated ClickHouse database contained exactly the six obsolete catalog
  tables, all with zero rows.
- Those six fully qualified tables were dropped; the database was retained.
- The 351 unrelated ClickHouse tables retained metadata SHA-256
  `a472aa975568477e2b08b28c87f2d9148e1bacbf06109ed755f22f1225c67582`
  before and after cleanup.
- No production catalog schema or data was changed during cleanup.

Current state: the historical cleanup was followed by the clean 2026-08-18 DEV
rollout described above. Exactly six catalog tables now exist in the isolated
DEV database; no property-catalog table exists in PostgreSQL.

Before recreating the clean schema, DEV must also prove there is no pending
catalog spool or incompatible schema-version record. Any contradiction stops
rollout; it is not silently migrated.
