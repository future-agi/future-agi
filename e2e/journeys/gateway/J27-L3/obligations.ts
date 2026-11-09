import type { AssertionRow } from '../_shared/packet-rows';

/**
 * Exact packet-to-authoring map, not a reporter, runtime adapter or admission
 * layer. Authority: <WORKSPACE>/omega-launch/plan/JOURNEYS-708/packets/J27/L3/PACKET.json.
 * Full row assertions remain in that immutable packet. Nothing here narrows them.
 * Source base: da4c81cc623f909ce38f53e01b557ee8374d5adb; immutable foundation:
 * 7543e2d995fe58b620745aa81ce1a76c8855ef0e. Rebind after candidate changes.
 */
export const L3_OWED = [
  {
    id: 'L3-O01', kind: 'missing runner fault hook', foundation: ['F01', 'F03', 'F19'],
    owner: 'shared walk runtime owner with HOSTED-OPS and main',
    reason: 'No accepted exclusive-stack, actual-Redis-container-ID stop/start interface, raw wait receipts, namespace-connectivity witness, effective managed.require_redis attestation, or fixed recovery deadline is callable from this candidate. KIT is unaccepted. ship/walk_contract.py:shape accepts Wdd/HRddd only; ship/w20/rehearsal.sh still selects a two-replica W20 topology and fixed output paths. Candidate e2e/playwright.config.ts excludes journeys. No consumer Docker/shell/HTTP bypass is permitted.',
  },
  {
    id: 'L3-O02', kind: 'missing product hook', foundation: ['F02', 'F05'],
    owner: 'gateway billing/state owners and main money owner',
    reason: 'lib/state-probe.ts has pg/ch/API only, not a reviewed Redis reader. _shared/gateway-state.ts deliberately cannot read effective managed.require_redis, Redis identity, per-request holds, epochs or tombstones. _shared/postgres-config.ts account totals and _shared/postgres-requests.ts logs are not the authoritative request ledger. Need a public reviewed schema-qualified projection, outage-unavailable observation plus still-available durable state, all seeded IDs, worker outcomes and exact integer-money reconciliation. Do not guess private ledger tables or inspect EE source.',
  },
  {
    id: 'L3-O03', kind: 'missing product hook', foundation: ['F09', 'F10'],
    owner: 'main Stage A identity owner and gateway MCP owner',
    reason: 'lib/fixtures.ts/provisioning.ts obtains legacy org keys through /accounts/keys/; it does not implement explicit personally owned key creation. Need synthetic C-LIVE/C-REVOKED/C-OLD/C-CURRENT fixtures with authoritative epochs, both new and established MCP sessions and request-correlated mock counters. _shared/postgres-mcp.ts row version is explicitly not a Redis epoch. Manifest defers FA-733-COMPOSE/MCP-KEY-USER-1 and excludes MCP-708I-PART2; deferred source cannot be assumed present.',
  },
  {
    id: 'L3-O04', kind: 'missing runner fault hook', foundation: ['F04'],
    owner: 'shared walk fixture owner',
    reason: 'M-HELD needs an approved local mock completion barrier AFTER durable admission, release DURING the confirmed outage and exact pre-stop delivery/terminal worker outcomes. lib/upstream.ts supports a delay before response, not this barrier. Need ID-bound nonce-correlated managed and MCP sinks with value-free request counters; no guessed programmable route or delay-as-barrier substitute.',
  },
  {
    id: 'L3-O05', kind: 'unpinned wire body', foundation: ['F05', 'F10', 'F19'],
    owner: 'gateway managed/MCP contract owners and independent money reviewer',
    reason: 'No candidate-bound managed/MCP invocation, exact bounded Redis-unavailable denial status/reason, personal-credential creation/rotation/revoke, idempotency replay or authoritative state capture body was supplied for the complete required topology. Existing generic admin methods and old MCP profile examples cannot pin this composition. Bind public serializers or accepted captures and fixed refusal/recovery/settlement budgets before writing live requests; auth errors, hangs and crashes are not outage passes.',
  },
  {
    id: 'L3-O06', kind: 'missing runner fault hook', foundation: ['F01', 'F07', 'F17', 'F19'],
    owner: 'shared runtime owner with independent Sol and main',
    reason: 'Need accepted C03 binding-negative controls that refuse before mutation and C04 inherited executed controls for page/dedicated worker/service worker/WebSocket nonlocal attempts, blocked/aborted attempts, missing identity, cut output, incomplete lifecycle and nonqualifying rejected packets. Need immutable 59k/18d/two-list native/browser evidence joins, CLEAN independently adjudicated lister, money acceptance and main exact-tuple disposition. This lane cannot fabricate runtime evidence or implement another admission layer.',
  },
  {
    id: 'L3-O07', kind: 'missing product hook', foundation: ['F02', 'F05', 'F07'],
    owner: 'gateway state/capture owners and packet author after hook binding',
    reason: 'The authored C02 pure sub-oracle uses only synthetic unit records, not captured originals. Need reviewed authoritative before/fault/after capture projection and source/tuple/request/hold/settlement/credential joins, complete amounts/conservation/idempotency assertions, independently reviewed capture-derived perturbations, and unchanged full oracle red/restored execution. Synthetic detector green is never L3-A04/A05/A07/A08/A09 coverage or C02 qualification.',
  },
] as const;

export type OwedId = (typeof L3_OWED)[number]['id'];
interface OwedRow {
  row: AssertionRow<'J27-L3'>;
  logicalCase: 'L3-BASELINE' | 'L3-OUTAGE' | 'L3-RESTORE' | 'ALL';
  status: 'OWED';
  productStatus: 'NOT_WALKED';
  owed: readonly OwedId[];
  intendedStep: string;
}

// Every inventory row is assigned exactly once. No partial row is declared as
// journey-assertion; no placeholder test passes/skips/fixmes a missing hook.
export const L3_ROW_MAP = [
  { row: 'L3-A01', logicalCase: 'L3-BASELINE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O01', 'L3-O02', 'L3-O06'], intendedStep: 'L3-A01 bind candidate/LM708/images/migrations/one-replica/config and actual Redis container/network/volume' },
  { row: 'L3-A02', logicalCase: 'L3-BASELINE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O02', 'L3-O03', 'L3-O04', 'L3-O05'], intendedStep: 'L3-A02 valid managed/MCP local delivery and persisted settled/held/live/revoked/superseded baseline' },
  { row: 'L3-A03', logicalCase: 'L3-OUTAGE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O01', 'L3-O04'], intendedStep: 'L3-A03 stop only actual bound Redis, prove namespace failure, then submit active managed/MCP clients' },
  { row: 'L3-A04', logicalCase: 'L3-OUTAGE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O01', 'L3-O02', 'L3-O04', 'L3-O05'], intendedStep: 'L3-A04 exact bounded managed Redis-unavailable denial; zero delivery/authorization/new hold/debit/settlement' },
  { row: 'L3-A05', logicalCase: 'L3-OUTAGE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O01', 'L3-O02', 'L3-O03', 'L3-O05'], intendedStep: 'L3-A05 new and established MCP sessions deny live/revoked/superseded credentials; exact reasons and zero delivery' },
  { row: 'L3-A06', logicalCase: 'L3-RESTORE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O01', 'L3-O02'], intendedStep: 'L3-A06 restart same container/volume/persistence without reset/reseed/image change inside admitted fixed bound' },
  { row: 'L3-A07', logicalCase: 'L3-RESTORE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O02', 'L3-O04', 'L3-O05', 'L3-O07'], intendedStep: 'L3-A07 all holds terminal, exactly-once money, denied requests remain inert, idempotency replay and aggregate/scope reconciliation' },
  { row: 'L3-A08', logicalCase: 'L3-RESTORE', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O02', 'L3-O03', 'L3-O05', 'L3-O07'], intendedStep: 'L3-A08 authoritative epochs/tombstones preserved, stale sessions refused, fresh current managed/MCP success and exact billing' },
  { row: 'L3-A09', logicalCase: 'ALL', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O02', 'L3-O05', 'L3-O07'], intendedStep: 'L3-A09 exact before/fault/after source+run+tuple+browser+org+request+money+credential joins; Redis fault is unavailable' },
  { row: 'L3-A10', logicalCase: 'ALL', status: 'OWED', productStatus: 'NOT_WALKED',
    owed: ['L3-O01', 'L3-O06'], intendedStep: 'L3-A10 complete executed inventory and 59k/18d certification, zero vendor/production delivery and no unapproved attempts' },
] as const satisfies readonly OwedRow[];

export const L3_CONTROL_MAP = [
  { id: 'L3-C01-REAL-OUTAGE', status: 'OWED', owed: ['L3-O01', 'L3-O02', 'L3-O03', 'L3-O04', 'L3-O05'],
    steps: ['green-before: healthy personally owned managed/MCP paths',
      'negative: confirmed real Redis stop then exact denials and zero post-stop effects',
      'restored: same Redis/current credentials green; every hold reconciles; retired credentials refused'],
    rows: ['L3-A02', 'L3-A03', 'L3-A04', 'L3-A05', 'L3-A06', 'L3-A07', 'L3-A08'] },
  { id: 'L3-C02-ORACLE-FALLIBILITY', status: 'SYNTHETIC_SUPPORT_AUTHORED_CAPTURE_REPLAY_OWED', owed: ['L3-O07'],
    steps: ['separate managed-delivery and MCP-delivery injected records', 'separate missing-hold record',
      'separate duplicate-debit record', 'separate revived-superseded and revoked-credential records',
      'unchanged oracle must return exact failure row/cause', 'unchanged untouched original restored clean'],
    rows: ['L3-A04', 'L3-A05', 'L3-A07', 'L3-A08', 'L3-A09'] },
  { id: 'L3-C03-BINDING-REFUSALS', status: 'OWED', owed: ['L3-O01', 'L3-O06'],
    steps: ['red: mismatched Redis ID', 'red: mismatched volume', 'red: missing require_redis attestation',
      'red: missing phase state', 'red: dirty candidate', 'red: wrong candidate',
      'each refuses before any fault action', 'restored exact accepted binding preflight green'],
    rows: ['L3-A01', 'L3-A03', 'L3-A09'] },
  { id: 'L3-C04-INHERITED-RUNTIME', status: 'OWED', owed: ['L3-O06'],
    steps: ['red: nonlocal page/dedicated-worker/service-worker/WebSocket including blocked/aborted attempts',
      'red: missing identity', 'red: cut output', 'red: incomplete lifecycle',
      'red: nonqualifying rejected packet', 'restore inert telemetry/local endpoints/bindings and execute clean L3'],
    rows: ['L3-A10'] },
] as const;

// Packet output originals belong to the future admitted create-only run root,
// NEVER this authoring directory. No file below is being represented as produced.
export const L3_OUTPUT_CUSTODY = {
  landedRoot: '<WORKSPACE>/omega-launch/plan/JOURNEYS-708/packets/J27/L3/outputs/${RUN_ID}/${TUPLE}/${BROWSER}',
  rehearsalRoot: '<WORKSPACE>/omega-launch/plan/JOURNEYS-708/packets/J27/L3/rehearsal/${REHEARSAL_ID}',
  mutationPolicy: 'No rewriting/sanitizing captured originals; derived synthetic red records are separate from emission onward.',
  phaseBarriers: ['baseline snapshots and fixture mutations persisted', 'actual bound Redis stop confirmed',
    'post-stop managed/MCP attempts and durable fault state captured', 'same Redis restarted',
    'recovery and terminal-state convergence', 'after snapshot and full reconciliation'],
  inflight: 'M-HELD admitted before stop retains its pre-stop delivery count; release approved completion barrier DURING outage; delivered usage settles exactly once.',
  redisFaultRead: 'Explicit unavailable result, last baseline preserved, still-available durable ledger captured; never successful synthetic Redis fault read.',
  waitBudgets: 'No invented timeout or POLL.GATEWAY_SYNC as Redis SLA; bind named REDIS_RECOVERY and HOLD_TERMINAL_CONVERGENCE budgets from reviewed contracts.',
  releaseDisposition: 'NOT_WALKED while unbound; always-applicable L3 holds release. Actual executed behavior failures are WALKED_FAIL, not NOT_WALKED. Main alone finalizes the walked list.',
} as const;
