import type { AssertionRow } from '../_shared/packet-rows';

export const PACKET = 'J27-L5' as const;
export type Row = AssertionRow<typeof PACKET>;

// Authoring provenance: JOURNEYS-708/packets/J27/L5/PACKET.json, all ten rows,
// authoritative_observation_contract and execution_sequence_for_later_admission.
// There is no top-level state_protocol in L5; those two sections ARE its protocol.
// This register is NOT an executable runtime descriptor, receipt, or wire schema.
export const debts = {
  runtime: {
    kind: 'missing runner fault hook', foundation: ['F01', 'F17', 'F19'],
    owner: 'shared walk runtime owner and main',
    reason: 'No accepted J27-L5 discovery/invocation/reporter/evidence join. walk_contract.py:shape accepts only Wdd/HRddd and W20 topology; the candidate default config excludes journeys. Current-hash 59k certificate, exact candidate/LM-708 tuple and independent reviews remain unbound.',
  },
  pair: {
    kind: 'missing runner fault hook', foundation: ['F03', 'F19'],
    owner: 'gateway image producer, shared runtime owner and main',
    reason: 'No accepted immediate-predecessor release-sequence record, two distinct qualified immutable images, source-to-image relationship or callable gateway-only replacement/roll-forward hook. Git parentage, tags and healthy boot cannot supply this proof.',
  },
  schedule: {
    kind: 'missing runner fault hook', foundation: ['F03', 'F19'],
    owner: 'main, HOSTED-OPS and shared runtime owner',
    reason: 'No reserved exclusive rehearsal window or resource-identity/lock witness, and no accepted admission plus immediately-before-mutation no-overlap recheck. W20 two-replica rehearsal cannot replace the required isolated single-replica rollback.',
  },
  redis: {
    kind: 'missing product hook', foundation: ['F02', 'F03'],
    owner: 'gateway state owner and shared runtime owner',
    reason: 'StateProbe has PG/CH/API reads but no accepted Redis probe for container/run_id/volume/namespace identity, complete selected hold/budget/epoch inventory, original expiry/PTTL, command-name FLUSH counters or continuous lifecycle/no-reset accounting. Gateway credits are aggregates, not these witnesses.',
  },
  barrier: {
    kind: 'missing runner fault hook', foundation: ['F03', 'F04'],
    owner: 'shared runtime, gateway billing and local mock fixture owners',
    reason: 'No reviewed real-admission outstanding-hold and settlement/expiry-writer barrier with a fixed operation deadline and TTL witness. MockUpstream latency is before-response delay, not a settlement/expiry barrier; direct hold writes, draining all holds or TTL extension cannot substitute.',
  },
  ledger: {
    kind: 'unpinned wire body', foundation: ['F05'],
    owner: 'gateway billing and main independent money owner',
    reason: 'No public reviewed per-request private-ledger projection/capture or supported worker-redelivery/request-replay fixture contract. Account totals and request logs cannot establish one terminal settlement, one debit, correct release/refund, budget conservation or absence of orphaned holds. No private EE source or guessed ledger columns are used.',
  },
  identity: {
    kind: 'unpinned wire body', foundation: ['F09', 'F10'],
    owner: 'main Stage A, personal-key and credential owners',
    reason: 'No accepted fresh two-org personal/scoped credential fixture, actual credential epoch/tombstone observation and exact stale/revoked/cross-tenant denial body for this candidate. provisionActor reads organization keys; PG row versions are not Redis epochs. Deferred 733/MCP inputs are not imported.',
  },
  admission: {
    kind: 'unpinned wire body', foundation: ['F03', 'F04', 'F05', 'F09', 'F14'],
    owner: 'gateway admission, budget and shared runtime fixture owners',
    reason: 'No bound synthetic model/price/request/credential/scope contract, precise refusal status and code, or request-correlated target-side nonce/count and authoritative no-hold/no-debit witness. No readiness-close/drain/revalidate/open timeline hook. No generic non-2xx or mocked success is an admission assertion.',
  },
  mutation: {
    kind: 'missing runner fault hook', foundation: ['F07'],
    owner: 'shared runtime fixture owner with independent reviewer',
    reason: 'No accepted control-only Redis replacement, scoped remove-hold/reset-budget/regress-epoch mutation, or duplicate-debit observation injection binding. Each needs a separately isolated fixture, exact detector failure and restored genuine product path, never a setup failure or global flush.',
  },
} as const;
export type Debt = keyof typeof debts;

export interface OwedRow {
  row: Row;
  caseId: string;
  step: string;
  debts: readonly Debt[];
}

// Every complete row is OWED, not partially passed. The optional public-state
// reader in control-plane-state.ts proves only a supplement to A03/A05/A06.
// Do not use journeyAnnotations/assertionRow for these admission guards: those
// APIs declare and emit complete behavioral assertion coverage.
export const owedRows: readonly OwedRow[] = [
  { row: 'L5-A01', caseId: 'L5-EVIDENCE', step: 'join exact executed inventory, immutable tuple and CLEAN 59k evidence', debts: ['runtime'] },
  { row: 'L5-A02', caseId: 'L5-PAIR', step: 'qualify adjacent distinct images and observe exactly one current-to-previous replacement', debts: ['pair', 'runtime'] },
  { row: 'L5-A03', caseId: 'L5-IDENTITY', step: 'compare Redis and supporting identities through rollback and restoration with no flush/reset/reseed', debts: ['redis', 'pair', 'barrier'] },
  { row: 'L5-A04', caseId: 'L5-HOLDS', step: 'compare nonempty real outstanding holds including request/owner/scope/amount/state/original expiry', debts: ['redis', 'barrier', 'ledger'] },
  { row: 'L5-A05', caseId: 'L5-BUDGETS', step: 'preserve exact scope cap/spent/reserved, enforce remaining arithmetic and deny without side effects', debts: ['redis', 'barrier', 'admission', 'ledger'] },
  { row: 'L5-A06', caseId: 'L5-EPOCHS', step: 'preserve actual epochs/revocations/tombstones and demonstrate denied and valid credential behavior', debts: ['redis', 'identity', 'admission'] },
  { row: 'L5-A07', caseId: 'L5-ORDER', step: 'close/drain/revalidate/replace/sync before reopening with zero not-ready admission and measured interruption', debts: ['pair', 'schedule', 'barrier', 'admission', 'redis'] },
  { row: 'L5-A08', caseId: 'L5-SETTLEMENT', step: 'release barriers, reconcile every real request and replay through actual workers without another debit', debts: ['barrier', 'ledger', 'redis', 'admission'] },
  { row: 'L5-A09', caseId: 'L5-RESTORED', step: 'restore original current digest and repeat the complete positive sequence after all detector reds', debts: ['pair', 'redis', 'barrier', 'ledger', 'identity', 'admission', 'mutation', 'runtime'] },
  { row: 'L5-A10', caseId: 'L5-SCHEDULE', step: 'prove separate resources and nonoverlap at entry and immediately before mutation', debts: ['schedule', 'pair'] },
];

// A binding owner must supply these actual boundaries, not turn this array into
// callbacks or supply receipt-shaped booleans. No endpoint/argv is invented here.
export const stateProtocol = [
  'admission: accepted candidate/tuple, qualified adjacent pair, independent money owner, exclusive reservation; no Saturday walk overlap',
  'runtime: accepted step-1 before PREPARE, real inventory/read-set, complete native/interpreter/Compose accounting before WORKLOAD_STARTED',
  'baseline: current image ready; two synthetic orgs, personal/scoped credentials, revoked and current epochs; real nonzero holds and consumed/reserved budgets plus exhausted and settled controls',
  'before: freeze known settlement/expiry writers only; attach minted request/hold/scope/settlement IDs; integer money and original TTL/deadlines must span the predeclared operation deadline',
  'rollback: close/drain, revalidate authoritative state and served parts, replace gateway only exactly once current-to-immediate-previous; preserve Redis process/container/volumes, data, config and migrations',
  'after-rollback: compare complete authoritative inventory BEFORE releasing writers; no TTL rejuvenation; verify readiness ordering, real denials with zero side effects and valid target-side delivery',
  'settle: release the real barriers; reconcile all carried/new requests and duplicate redelivery; no orphan, double debit or unexplained budget change',
  'restore: same original current digest, same Redis/data, no reseed; repeat fresh-ID admission and exactly-once settlement checks; preserve all failures',
  'land: separate unmodified originals per invocation/control; all required files, CLEAN lister/adjudication, independent Sol and money review; main alone joins release disposition',
] as const;

// PACKET.outputs.files_required_relative_to_landed_root, exact inventory.
// Only the admitted runtime may produce/land these; authoring writes none of them.
export const requiredOutputs = [
  'RESULT.json', 'receipt.json', 'identity.json', 'executed-inventory.json',
  'pair-provenance.json', 'pair-qualification-reference.json', 'schedule-isolation.json',
  'topology-identity.json', 'gateway-image-transition.json', 'readiness-timeline.json',
  'lifecycle-events.json', 'redis-no-flush.json', 'state/before.json',
  'state/after-rollback.json', 'state/after-restore.json', 'state-diff.json',
  'requests.json', 'admission-results.json', 'mock-delivery-counts.json',
  'settlement-reconciliation.json', 'worker-events.json', 'restored-results.json',
  'controls/red-green-index.json', '59k-certificate.json', 'audits/lister-result.json',
  'reviews/adjudication-reference.json', 'reviews/sol-reference.json', 'reviews/money-reference.json',
] as const;
