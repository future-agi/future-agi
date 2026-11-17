# Gateway journey foundation

Source-only authoring foundation for the eleven gateway packets, based on
`da4c81cc623f909ce38f53e01b557ee8374d5adb` on `gw/JOURNEYS-708J`.
This is not a walk, runtime acceptance, an activation, or a substitute for the packets.
No product assertions have executed. The base's deferred MCP/733/harness inputs are
not silently imported. Original packet obligations, including money and controls,
remain mandatory.

## Homes and ownership

All gateway journey code belongs under `e2e/journeys/gateway/`. The packet homes are:

| Directory | Authoritative packet suffix under JOURNEYS-708/packets | Scope                                   |
| --------- | ------------------------------------------------------ | --------------------------------------- |
| `J27-L3/` | `J27/L3/PACKET.json`                                   | Actual Redis loss/recovery              |
| `J27-L4/` | `J27/L4/PACKET.json`                                   | Gateway/served EE restart and drain     |
| `J27-L5/` | `J27/L5/PACKET.json`                                   | Adjacent-image rollback and restoration |
| `J27-L6/` | `J27/L6/PACKET.json`                                   | Registration, workers and settlement    |
| `J11/`    | `J11/gateway/PACKET.json`                              | W01/W02 and the cloud money joins       |
| `J12/`    | `J12/gateway/PACKET.json`                              | Money faults and stock CAT-2 refusals   |
| `J13/`    | `J13/gateway/PACKET.json`                              | W03/W04 scoped budgets and caps         |
| `J14/`    | `J14/gateway/PACKET.json`                              | W07–W12, attach and the L-CC joins      |
| `J15/`    | `J15/gateway/PACKET.json`                              | W05/W13–W18, independently gated        |
| `J29/`    | `J29/gateway/PACKET.json`                              | Offered surfaces, strict widths/assets  |
| `J31/`    | `J31/gateway/PACKET.json`                              | W06 retained launch set and money joins |

Each packet author adds its own `<case>.spec.ts` and, only when needed, small
packet-local fixtures/oracles. These directories are reserved authoring homes;
this foundation intentionally creates no empty or skipped placeholder specs.
`_shared/` contains reusable readers, existing hooks, annotations and fixture
clients. Do not edit product code, `e2e/lib`, `e2e/flows`, `e2e/harness`, Playwright
configs, the shared runtime, or the other journey suites here. A new source pin
must be rechecked when the candidate changes.

## Selection contract

The sole runtime is `<WORKSPACE>/futureagi/tmp/gw-walk-59k-kit/`, including its
accepted additive Stage A/B interfaces, not a consumer-owned runner.

**Current interface is insufficient.** `ship/w20/rehearsal.sh` takes its reviewed
walk grep as argument 5 and passes it to Playwright with
`--config playwright.walk.config.ts`. `ship/walk_contract.py:shape` currently
accepts only `Wdd`/`HRddd`, the `mcp-catalog`/`standalone` topology and Chromium plus
Firefox. `params/w20.json` selects W20; its historic heads are not this candidate.
This worktree has no `playwright.walk.config.ts`, and `e2e/playwright.config.ts`
only discovers `flows/**/*.spec.ts` and `harness/**/*.spec.ts`. A file argument or
grep alone cannot make it discover `journeys/`.

Foundation owed item **F01** requires the runtime owner to bind these additive
invocation-list selections and discover `journeys/gateway/**/*.spec.ts` in its
accepted config. Do not edit either config here, invent CLI flags, substitute a
broad W20 grep, or claim a zero-test run passed.

The contract for that future binding is:

1. `selection(packet)` supplies the exact directory relative to `e2e/`, the
   `@journey-<packet>` tag and a boundary-aware title grep. A test title comes from
   `journeyTitle(packet, caseId, phase)` and starts with
   `GW-JOURNEY-<packet>.<caseId> [<phase>]`. Case IDs are unique within a packet.
   Do not put that prefix on enclosing suites or other packet tests.
2. An admitted invocation-list row binds the packet, exact spec path, exact full
   test titles, phase/control ID, project/browser, profile, width, tenant set,
   required assertion rows, expected count and unique output root. Directory/tag
   selection is a candidate filter, not proof of exact execution.
3. Before dispatch, the runner's collected inventory must equal those exact
   selected identities. Afterwards, reconcile actual starts/terminals and row
   attachments against the same inventory. Extra, absent, skipped, duplicated,
   zero-test or aborted cases refuse certification. Do not silently deduplicate.
4. Keep W20 invocation, scratch, reporter, context-observer and fixture entry
   points intact. The runner owns orchestration, census, Compose/container IDs,
   egress controls and landing. This library implements none of those.
5. Bind actual profile/browsers per packet. W05 Console and L-CC need WebKit;
   J29 gateway needs every admitted width including 320 px. L6 is a backend
   lifecycle proof, not an invented browser walk. J27 faults require exclusive
   stacks and one fault worker. Do not inherit W20's two-replica shape.

No default config, runtime invocation, or reporter is changed by this foundation.
The external obligation register is
`<WORKSPACE>/futureagi/tmp/journeys-708j/FOUNDATION.json`.

## Assertion and control records

`PACKET_ROWS` copies all packet-local assertion IDs exactly. Always identify a row
by **(packet, row)**: J12/A01 is not J31/A01. Descriptive row IDs are not new legs.

Each test declares a nonempty unique list through
`journeyAnnotations(packet, rows, phase, control?)`. It emits one
`journey-assertion` annotation per row with schema
`gateway-journey-assertion/v1`, packet, row, phase and optional control descriptor.
This is a coverage declaration only. Do not list rows whose complete assertions
the test does not check. Existing `flowAnnotation` conventions still apply to
existing flows; these journey tests use `@journey-<packet>`, not a fictitious
`flows/gateway` catalog entry or borrowed `WALK-E2E` identity.

Wrap the complete checks for each declared row once in
`assertionRow(testInfo, packet, row, phase, asyncCheck)`. It creates a named
Playwright step and `journey-row` JSON attachments for `started` and
`passed`/`failed`, with schema `gateway-journey-row/v1`. It rethrows failures.
Reporter admission/row reconciliation is still F01; attachments alone cannot
certify a test, and a failed attachment is not automatically an assertion kill.
The runner must verify the intended Playwright assertion failure, not a setup,
transport, missing hook or syntax failure. Runtime/certificate rows are proved
through the accepted runtime evidence join, never by a fake successful callback.

Phases are explicit, separately inventoried invocations:

- **baseline**: unchanged candidate and complete positive checks.
- **negative**: an expected real product refusal; test itself passes only with
  the exact refusal and zero forbidden effects. Requires a packet control ID.
- **red**: one approved isolated defect must fail the unchanged assertion at
  `control.expectedFailureRow`. Use Playwright's explicit expected-failure
  handling only for this invocation. Unexpected pass or another failure cause
  fails qualification. Never broadly catch an error and declare the control good.
- **restored**: remove only the defect, use the original candidate and permitted
  fresh fixture scope, and run the same complete checks green. Requires the same
  control ID, without an expected-failure row. Preservation tests may NOT reseed
  state within the measured fault/restore interval.

Keep baseline/red/restored originals separate from emission onward. No rewriting
or sanitizing landed logs. An oracle-only fixture checks the detector, not real
service behavior. Missing controls remain owed, never `test.skip`, `fixme`, soft
assertions, unconditional pass, or fallback response shapes.

## Readers and privacy

Pass the existing harness `probe` and `GatewayAdmin` instances; do not construct
parallel clients or inline SQL writers. Every query is read-only, scoped to the
minted organization/project/cohort and parameter-bound. Missing tables, malformed
responses and failed reads throw; they never become empty/zero success.

| Module                 | What it supplies                                                                                   | Important boundary                                                                        |
| ---------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `postgres-config.ts`   | Config versions, exact section equality, key ownership/status, credit account, exact audit changes | Includes tombstones; no config, secret or audit-metadata dump                             |
| `postgres-requests.ts` | Request logs, ordered attempts, tool-call rows by request cohort                                   | Logs are PG on this candidate; not monetary settlement authority                          |
| `postgres-mcp.ts`      | Credential metadata, approvals, consent and refresh-record lineage                                 | No encrypted tokens/hashes; row version is not a Redis epoch                              |
| `postgres-exports.ts`  | Connection lifecycle and all sync runs, exact endpoint equality                                    | No credentials, certificates, URLs or free-form diagnostics returned                      |
| `clickhouse.ts`        | All spans of a minted trace and its separate curated trace                                         | PG project-ownership check; both replacing tables read with FINAL and tombstone exclusion |
| `gateway-state.ts`     | Credits, selected runtime settings, org identity/section equality and typed key absence            | Reuses local-only admin API; unavailable require_redis/replica/Redis witnesses stay owed  |

Source/serializer/DDL provenance is beside each query and endpoint projection.
Do not assume a gateway mirror is the PG ledger, `outbox_pending=0` proves all
settlements, a PG credential version proves a Redis epoch, or aggregate balances
prove per-request conservation. Private ledger entries, authoritative Redis state,
cap activation/revision tables and missing cleanup fields need the exact reviewed
contracts listed in FOUNDATION.json. Do not open `futureagi/ee/**` to invent them.

PG numeric money is returned as strings; compare integer micros with `BigInt` or
an independently pinned decimal oracle. Admin JSON integer reads reject values
outside JavaScript's safe range. Do not use floating tolerance to accept money.
No raw bodies, headers, key prefixes, credential ciphertext or session tokens
belong in attachments. Boolean equality reads compare expected synthetic config
inside PG rather than exporting sensitive sections. Gateway expected sections
must use gateway serialization, not assume `config_push.py` is an identity map.

## Fixtures and UI

`fault-fixtures.ts` is a typed facade over existing `MockUpstream` latency/HTTP
status controls and `WebhookSink` channel stop/restore and synthetic credential
revocation. It adds no endpoint or runtime action. Read/control endpoints must be
loopback, and the existing managed-mock/public-network inspection and runner
egress guard remain mandatory. Returned observations contain only counts/statuses.
A response delay is not a mid-stream, drain, settlement or commit-before-ack barrier.
All unsupported fault families are owed requests, not pass-producing stubs.

Use the accepted Stage A identity/fixture seam. Current `provisionActor` reads
`/accounts/keys/`; `ScopeActors.workspaceAdmin()` is a workspace admin, not an org
Admin. Neither is a substitute for the packets' explicit personal-key creation,
zero-key signup census and org Owner/Admin/Member/Viewer matrix. F09 tracks this
harness extension. Do not auto-mint or reuse an organization/system key.

`selectors.ts` only exposes hooks found in actual product components, with source
comments. Mock-grid testids are excluded. Prefer exact roles/labels/minted text
inside the right root. Reuse `GatewayGridReader` for display ordering and
virtualized-row counts; don't assume every row is rendered. `data-testid="run-id"`
is intentionally excluded because it exists only on the hidden-runs branch.
No product testid was added, and no unverified testid name is proposed as fact.

Attach all seeded **non-secret IDs** with `attachSeededIds(testInfo, packet, ids)`.
Never attach an actor or response object. Mint names/scopes per test; do not select
"the first row". Compare exact sets including cardinality/multiplicity, exact
request/tenant membership and the separately seeded negative witness; never
`toContainText` where exclusion is owed. Querying only expected output IDs can
hide extras: request/tool-call readers intentionally return every row for the
request cohort. No global cleanup, truncation or production data.

## Waits and validation boundary

Use `POLL` from the harness, named for the dependency being awaited:
`SPAN_VISIBLE` for collector/curated arrival, `CDC_VISIBLE` only for actual CDC
mirrors, `GATEWAY_SYNC` for config/key propagation, `SCHEDULE_TICK` for schedule
work and `ASYNC_JOB` for the corresponding job. A test's total timeout accounts
for sequential waits and UI actions. Do not reuse these as invented lifecycle
SLAs; fixed drain/recovery/settlement deadlines must come from accepted contracts.
No sleep-based proof, retrospective timeout widening or retries masking failure.

Local validation for this authoring stage is syntax/parse only: each erasable
TypeScript file with Node 26 `--check`, JSON parsing for FOUNDATION.json, and a
Markdown parse for this contract. No tests, type checking, Playwright, browser,
stack, build, cloud/planner or provider call is authorized here. Subsequent
independent review, runner binding and admitted runtime execution are still owed.
