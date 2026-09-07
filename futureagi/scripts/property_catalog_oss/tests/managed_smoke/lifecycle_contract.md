# Parent integration contract: full managed lifecycle gate

Updated 2026-09-06: actual migration/ORM onboarding, continuous supervisor,
sequencer/consumer, qualification, FOLLOW reader selection and authenticated
property/value reads have passed on standalone ClickHouse. Typed value parity,
pagination and old control-enum upgrade preservation also passed. Restarting the
actual supervisor, sequencer and consumer preserved identity and one FOLLOW
event; all typed API checks passed again. No identity or activation is faked.

The older interface checklist below records staging history, not missing code.
Remaining release gates are authoritative in the parent
`docs/catalog-managed-lifecycle-plan.md`: full root Compose/image/UI, all sources,
current live freshness, failures/replicas, upgrade and optional migration.

## Available parent interfaces (not full-stack execution evidence)

- `inspect_installation(client, environment=..., target_database=...,
  candidate_topic=..., ordered_topic=...)` uses bounded SELECTs. The live fixture
  tests empty tables, the actual JSON/ARRAY JOIN query over a valid coordinator
  plan, exact nondefault coordinates, and no activation writes.
- `resolve_installation(...)` persists adjacent `runtime-identity-v1.json`.
  The fixture tests fresh resolution with no legacy settings, restart byte
  stability, and missing-file read-only refusal without SQL. It uses separate
  temporary runtime directories for fresh and metadata-adoption scenarios.
- Parent `_supervisor_config()` now allows all legacy version variables absent;
  its handler resolves the descriptor before discovery. Parent reports the Go
  singleton waits for the adjacent file. Full process ordering remains untested
  here; the library check is not a substitute for empty-install service startup.

## Integrated interfaces and remaining execution boundaries

- Parent root Compose removes legacy version/producer defaults and wires
  managed reader activation with one shared fresh fence/identity volume. Real
  root Compose/release-image startup is not yet verified by this harness.
  Synthetic identity probes must
  remain SELECT-only and must not allocate a tenant/workspace.
- The application lane initializes a **new** `managed_smoke` PostgreSQL database
  with the actual Django migrations and normal organization/workspace/project
  models. The separate relational fixture uses normal eval templates/configs,
  simulation configs, annotation labels and dataset columns. Its live phases
  remain unverified until executed successfully; offline fixture tests do not
  prove source-to-catalog coverage. No lookalike source tables are permitted.
- Backend definition/value reads use managed reader settings and the installation
  identity contract. PropertyCatalogReader/ValueReader retain compatible-build,
  authorization and completion checks. Fixtures cannot force activation, author
  completion records, or set a workspace allowlist to bypass missing wiring.

The existing operator interface remains:

```text
python manage.py ch25_property_catalog_oss_supervisor --once
python manage.py ch25_property_catalog_oss_supervisor --once --initial-backfill
```

The first performs normal automatic bootstrap/reconciliation. The second must
skip already-active workspaces and preserve their active build. Neither is a
replacement for source schema bootstrap, grants, tenancy, or completion proof.

## Required staged service graph

Extend only the generated disposable project after those interfaces exist:

```text
new PostgreSQL + new ClickHouse + new Kafka
  -> application schema/fixture job + OSS role/catalog bootstrap jobs
  -> candidate and ordered topic creation
  -> supervisor (writer) -- shared fresh runtime volume -- sequencer (reader)
  -> singleton sequencer with exclusive fresh spool volume
  -> actual ordered consumer with separate INSERT/ledger SELECT identities
  -> read-only definition/value API verification
```

Reuse root Compose environment contracts for the actual Go consumer and
sequencer. Retain `--seed-from-delivery-ledger`, separate source/control/consumer/
ledger/API principals, matching candidate/ordered topics, producer retirement
and drain-proof paths, and a bounded wall shorter than its finite lease. Mount
the runtime volume at both processes' expected paths. The sequencer owns only
its own spool. All volumes must be fresh and project-labelled; no existing
external networks or user data mounts.

## Assertions required before full success

| Phase | Required live evidence |
| --- | --- |
| Empty install | No workspaces/activations; supervisor remains healthy; a valid destination-bound installation identity is persisted once without synthetic tenant allocation. |
| Workspace onboarding | Insert actual OSS org/workspace/project fixtures after startup; ordinary polling creates a qualified initial build without initial-backfill/version/reader environment edits. |
| Relational-only onboarding | A real active workspace with no Observe project qualifies its relational definitions automatically. Missing/null inventory is not accepted as an authorized empty set; no fake project or raw unscoped span query is allowed. |
| Project scope transitions | Add the first Observe project and ingest real OTLP; then remove the last project. Project bindings retire, relational definitions remain, foreign/deleted project requests are rejected, and source-scope changes cannot publish an obsolete frozen build. |
| Source parity | Query canonical spans and all relational fixture sources; compare catalog definitions/values through actual readers, including strings/numbers/booleans/arrays/nulls/maps and deletion behavior. |
| Hot candidate path | Emit through the real collector or checked-in candidate boundary; observe candidate -> singleton -> ordered topic -> real guarded consumer -> delivery ledger. |
| Bootstrap retry | Interrupt only this run's supervisor after durable checkpoint evidence; restart within the lease, prove same build token/frozen cutoffs and progress, then full qualification. |
| Incremental | Add source changes, let normal polling run, prove a later compatible active revision, no stale/deleted suggestions, and stable installation identity. |
| Immutable capture during mutation | After real VALUES completion, update only the live synthetic source without notification. Observe unchanged exact capture/node/parts, matching real source and final audits, successful captured activation and its original API value. Its newly observed repair generation must remain pending. A separate full repair must bind and acknowledge that exact notice, use a new capture, pass real audits and expose the update within the bounded 180-second correctness wait, reporting the original 75-second latency target separately. A fixture-only post-completion API rendezvous must never author a product receipt or repair ACK. Repeat all typed/scope/pagination API assertions afterward. |
| Unnotified imports | Old-version historical imports and commits after the final audit must be detected, durably scheduled and exposed through qualified APIs without losing previous valid history. Background repair must not conceal the exact fault being tested. |
| Relational changes | Seed/update/delete each normal ORM definition family and verify configured values, search/cursors and workspace isolation. Parent updates/deletes must be detected even if child timestamps do not change. Actual dataset-cell value ingestion is a separate required path, not established by column-definition tests. |
| Operator action | Run `--once --initial-backfill` twice after activation; active revision/token do not rotate. |
| Failure isolation | Introduce one workspace-local invalid source/ownership fixture; it cannot activate, while a second eligible workspace progresses. |
| Restart | Restart only newly created supervisor/sequencer/consumer; identity bytes and accepted ledger state persist; duplicate delivery has no duplicate visible suggestions. |
| Completion | Real qualification, drain, all terminal checkpoints, source coverage, and activation-reader validations succeed with finite leases and no gap/poison/conflict evidence. |
| Replicated parity | Shared-Keeper replicas agree on identity and qualified data; lag, partial visibility and uncertain data/ledger/control acknowledgements cannot advance durable consumption or expose unqualified state. Standalone success is not evidence for this gate. |

An expired incomplete build is not permission to erase/relabel it. Use only
the parent's reviewed fenced recovery policy. A refused or incomplete run is
reported as failed/blocked with the exact evidence, never promoted to E2E pass.

The transport-only lane deliberately leaves `managed_workspace_lifecycle`
blocked. The application lane replaces it only after its actual driver and
implemented assertions succeed; its `full_lifecycle_gate` remains incomplete
until all requirements above and the parent release plan have live evidence.
Success is never inferred from source imports, unit tests, running containers,
empty tables, or Kafka ACKs. A failed application stage remains failed.
