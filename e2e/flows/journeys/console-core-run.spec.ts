import { test } from '../../lib/walk/fixtures';
import { checkConsoleAbsence, consoleAnnotation, consoleOptions, type ConsoleLeg } from './console-absence';

// JOURNEYS-708 v2 J16, full per-leg obligations. Mocked provider rehearsals never
// satisfy L1's real-model criterion, nor C1-C3. No provider calls in absence mode.
const FLOW = 'JRN-E2E-016';
const APPLIES = 'tuple:T-FULL & admitted:console';
// Pinned from services/omega-gateway/manifest/console-route-manifest.v1.json
// and EE packages/server/src/contracts/customer-openapi.v1.json, 7C candidate.
const CONVERSATIONS = '/omega/api/workspace/conversations';
const WORKFORCE = '/omega/api/workspace/workforce';
const legs: readonly ConsoleLeg[] = [
  { id: 'L1', title: 'Create and run an agent via BYOK', applies_when: APPLIES,
    flags_required: ['VITE_OMEGA_CONSOLE (runtime config)', 'console rollout stage',
      'VITE_OMEGA_CONSOLE_URL', 'VITE_OMEGA_EMBED_PARENT_ORIGINS'],
    steps: ['Sign in and open the Console embedded at the configured exact origin',
      'Create an agent and run it via BYOK using the provider supplied by the harness profile'],
    pass_criteria: 'Real model output (not scripted); a mock-llm receipt is a labelled rehearsal only',
    gap: 'HR-CJ-02: need admitted Console composition, embed-origin/manifest pins, host-assertion client and profile-selected mock provider with no-dispatch/request receipts; then assert created agent and terminal run by scoped API/PG reads',
    reads: [WORKFORCE, CONVERSATIONS], model: true },
  { id: 'L2', title: 'Stream output and stop a run', applies_when: APPLIES,
    flags_required: ['console rollout stage'],
    steps: ['Create an independent mock-backed running agent', 'Watch streamed output and stop the run'],
    pass_criteria: 'Streaming visible; stop honoured with a terminal state',
    gap: 'HR-CJ-03: need controlled long-running mock stream and run/event probe; prove streamed sequence, UI stop, terminal cancellation in API/storage and no sibling-tenant change',
    reads: [CONVERSATIONS], model: true },
  { id: 'L3', title: 'Open the exact run trace in Observe', applies_when: APPLIES,
    flags_required: ['agent_observe'],
    steps: ['Create an independent mock-backed run', 'Open its exact trace in FA Observe'],
    pass_criteria: 'Same trace id, same workspace, correct project, tenant attribution and masking',
    gap: 'HR-CJ-04: need admitted run fixture and run-to-Observe identity contract; correlate exact run/trace/project/workspace in API, PG and ClickHouse FINAL, including masking and foreign-tenant refusal',
    reads: [CONVERSATIONS, '/omega/api/workspace/observe/settings'], model: true },
  { id: 'L4', title: 'Show truncation and recover after switching context', applies_when: APPLIES,
    flags_required: ['EXT accepted on the RC1+picks pin'],
    steps: ['Produce capped output and confirm the truncation notice on Run Detail',
      'Switch run/workspace context while loading and await the new run detail'],
    pass_criteria: 'Notice shown; no permanent loading after a context switch; extra, not core',
    gap: 'HR-CJ-05: need outputTruncation-capable pinned daemon and deterministic over-limit mock output fixture; assert backend truncation metadata and newly selected run identity, not a stale SSE result',
    reads: [CONVERSATIONS], model: true },
  { id: 'L5', title: 'Run the agent with one platform key', applies_when: APPLIES,
    flags_required: ['per-org one-key enablement (only after J11 and J12 passed)'],
    steps: ['Verify J11 and J12 receipts at the same manifest before enabling the test org',
      'Repeat create/run with one platform key instead of BYOK'],
    pass_criteria: 'Same output criterion as L1, with one ledger debit per call',
    gap: 'HR-CJ-06: need digest-bound J11/J12 prerequisite reader and Console one-key provider fixture; prove one run, one request receipt and exactly one corresponding ledger debit (fixture grant is not a purchase)',
    reads: [WORKFORCE, CONVERSATIONS], model: true },
];

test.use(consoleOptions);
for (const leg of legs) {
  test(`${FLOW}.${leg.id}: J16.${leg.id} ${leg.title}`, {
    tag: ['@flow', '@journey', '@journey-J16', '@mock-provider'],
    annotation: consoleAnnotation('J16', FLOW, leg),
  }, async ({ scopeActors, scopeProbe, request, walk }, info) => {
    // Four actor UI waits (4 x 60s) + bounded HTTP/PG/API reads and provisioning headroom.
    test.setTimeout(480_000);
    await checkConsoleAbsence('J16', leg, { scopeActors, scopeProbe, request, walk }, info);
  });
}

// Positive-section imports sit here, below the absence cases, so the catalogued
// FLOWS.md locations of JRN-E2E-016.L1-L5 (line 51) do not move. Imports hoist.
import { randomUUID } from 'node:crypto';
import { appendFileSync, copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { Page, TestInfo } from '@playwright/test';
import type { ScopeActors } from '../../lib/scope-actors';
import { expect, type Walk } from '../../lib/walk/fixtures';
import { chromiumStrictArgs, FIREFOX_STRICT_PREFS } from '../../lib/walk/browsers';
import { E2E } from '../../lib/env';
import { CORE_ROUTE, computeCapabilityPassed, consoleLaunchOptions, consoleLegResult, type ConsoleLegState,
  type ConsoleOracleRecord } from './console-absence';
import {
  CONSOLE_SOURCE_ROOT, ConsoleBlocked, J16_CORE_HOOKS, REQUIRED_ENGINES, STAGE_A_BINDING_SCHEMA, STAGE_A_EXPORT,
  STAGE_A_EXTENSION_PINS, browserGate, coreApplicability, coreInvocationId, coreTenants, d5Grant, handle, heldUpstream,
  openRunAdapter, publicationGate, rehearsalStreamGate, resolveJ16Seam as resolveSeam, sha256, stageABinding,
  type AdmissionProofInputs, type AgentRecord, type ConsoleCoreRunAdapter, type CoreActor, type CoreApplicability,
  type CoreTenant, type SeamName, type SeamResolver,
  type ObserveWitness, type RelayReceipt, type RunEvent, type RunRecord, type StageABinding, type TraceView,
  type UpstreamState, type UpstreamWitness,
} from './console-core-seams';

// ===========================================================================
// POSITIVE J16 core: C1 = L1 AND L2, C2 = L3 (JOURNEYS-708 mandatory_core_gate).
// The absence loop above is unchanged and stays a non-qualifying rehearsal.
// Selection (packets/J16/CONSUMER-core.json, CONSUMER-BINDINGS.json "selection"):
// one describe per [J16 group=core tenant=<A|B> arm=<positive|negative|restored>]
// holding the exact canonical case titles; serial, so L1 creates and starts ONE
// run, L2 streams and stops that SAME run and L3 opens that SAME trace. Each
// leg writes its own value-free 'console-leg-result' receipt.
//   positive  live run through the approved D5/BYOK relay (adapter-routed; the
//             test itself never reaches a provider). No grant -> BLOCKED
//             D5_GRANT_MISSING before anything is provisioned.
//   negative  provider-free rehearsal. Every named one-factor fault below is
//             planted on a clone of the collected evidence, in
//             test.step('<id>:negative:<fault>'), and must be refused by EXACTLY
//             its named oracle and kind; anything else (setup, network, timeout,
//             another oracle) is not a kill.
//   restored  the same rehearsal without faults: every oracle passes, except
//             that L1.03 must REFUSE rehearsal provenance. L1.03's restored
//             positive is the live positive arm (mock never counts).
//   Both provider-free arms are BLOCKED FIXTURE_CONTRACT_MISSING at this head:
//   the HR-CJ-03 held stream carries no content before its hold and completes on
//   release (console-core-seams.ts rehearsalStreamGate), so it cannot serve L1.04
//   or L2. The same planted faults run in the fixture-free self-controls below.
// L1 order (packet C03 "fail before mutation"): read the admission proof inputs,
// C03 over the producer's route-manifest comparison, then the L1.01 admission round
// trip; only after both pass does the cell bind a provider, start the sibling mock,
// open a browser context or create an agent/run. A refusal there throws its named
// oracle failure at once; the orchestration control below proves the ordering.
// L2/L3 repeat the read-only C03 prerequisite (and negative-arm copied-manifest
// control) before opening their browser contexts, without repeating L1's round trip.
// Faults are planted at the evidence (adapter-output) boundary; product
// mutants are the kit's job. C04 (all-context egress) and C05 (evidence
// admission) are kit-owned: the walk fixture's boundary teardown plus Stage A
// E3-E5; receipts list them as kitOwnedObligations, never as passed.
// ===========================================================================
type Tenant = 'A' | 'B';
type Arm = 'positive' | 'negative' | 'restored';
/** The describe title CONSUMER-core's --grep selects (one tenant/arm per process). */
const cellTitle = (tenant: Tenant, arm: Arm) => `[J16 group=core tenant=${tenant} arm=${arm}]`;
type LegId = 'L1' | 'L2' | 'L3';
type OracleId = 'L1.01' | 'L1.02' | 'L1.03' | 'L1.04' | 'L1.05' | 'L2.01' | 'L2.02' | 'L2.03' | 'L2.04'
  | 'L3.01' | 'L3.02' | 'L3.03' | 'L3.04' | 'C01' | 'C02' | 'C03';
const CORE_LEGS: readonly LegId[] = ['L1', 'L2', 'L3'];
const REQUIRED: Readonly<Record<LegId, readonly OracleId[]>> = {
  L1: ['L1.01', 'L1.02', 'L1.03', 'L1.04', 'L1.05', 'C01', 'C02', 'C03'],
  L2: ['L2.01', 'L2.02', 'L2.03', 'L2.04', 'C01', 'C02', 'C03'],
  L3: ['L3.01', 'L3.02', 'L3.03', 'L3.04', 'C01', 'C02', 'C03'],
};
const KIT_OWNED = { C04: 'walk boundary teardown (lib/walk/boundary.ts assert) + Stage A E4 all-context observation',
  C05: 'Stage A E3 reporter, accepted lister, 18d and canonical-list receipts' } as const;
// Admission round-trip phases, exactly as gw-hr-cj console-round-trip.ts:66-75 records them.
const ADMISSION_PHASES = ['both-denied', 'a-admitted-b-denied', 'both-denied-again', 'restored'];
const OUTPUT_WAIT = 60_000;
const RECEIPT_WAIT = 60_000;
// Long enough that the live run is still streaming when L2 reopens it; the
// grant's own caps bound it. The synthetic sentinel feeds L3.03 masking.
const runPrompt = (sentinel: string) =>
  `Write the integers from 1 to 2000 in ascending order, one per line, and nothing else. Reference: ${sentinel}`;
const SIBLING_PROMPT = 'Write the integers from 1 to 2000 in ascending order, one per line.';
const DENIED_PROMPT = 'This request must be refused before any model call.';

class ConsoleOracleFailure extends Error {
  constructor(readonly oracle: OracleId, readonly kind: string) {
    super(`J16 ${oracle} refused: ${kind} (details withheld)`);
    this.name = 'ConsoleOracleFailure';
  }
}
function check(condition: unknown, oracle: OracleId, kind: string): asserts condition {
  if (!condition) throw new ConsoleOracleFailure(oracle, kind);
}
class Withdrawn extends Error {
  constructor(readonly reason: string) { super(`WITHDRAWN ${reason}`); }
}
/** Every pre-provisioning blocker that applies, in gate order; [0] is primary. */
class BlockedSet extends Error {
  constructor(readonly all: readonly ConsoleBlocked[]) { super(`BLOCKED ${all.map(b => b.reason).join(', ')}`); }
}
const same = (a: readonly unknown[], b: readonly unknown[]) =>
  JSON.stringify([...a].map(String).sort()) === JSON.stringify([...b].map(String).sort());
const isDigest = (value: unknown) => typeof value === 'string' && /^sha256:[a-f0-9]{64}$/.test(value);

interface Scope { organizationId: string; workspaceId: string; projectId: string; accountId: string }
const scopeOf = (t: CoreTenant): Scope =>
  ({ organizationId: t.organizationId, workspaceId: t.workspaceId, projectId: t.projectId, accountId: t.accountId });
const sameScope = (a: { organizationId: string; workspaceId: string } | null | undefined, b: Scope) =>
  !!a && a.organizationId === b.organizationId && a.workspaceId === b.workspaceId;

interface Oracle<E> { id: OracleId; liveOnly?: true; run(e: E): void | Promise<void> }
interface Fault<E> { oracle: OracleId; id: string; kind: string; liveEvidence?: true; plant(e: E): void }

// ---------------------------------------------------------------------------
// C02: two distinct tenants in a required engine (per leg).
// ---------------------------------------------------------------------------
interface CellEvidence { browser: string; owner: Scope; foreign: Scope }
const CELL_ORACLES: Oracle<CellEvidence>[] = [{ id: 'C02', run(e) {
  check((REQUIRED_ENGINES as readonly string[]).includes(e.browser), 'C02', 'engine-outside-matrix');
  check(e.owner.organizationId !== e.foreign.organizationId, 'C02', 'tenant-collapsed');
  check(e.owner.workspaceId !== e.foreign.workspaceId && e.owner.projectId !== e.foreign.projectId &&
    e.owner.accountId !== e.foreign.accountId, 'C02', 'scope-collapsed');
} }];
const CELL_FAULTS: Fault<CellEvidence>[] = [
  { oracle: 'C02', id: 'tenant-collapsed', kind: 'tenant-collapsed',
    plant: e => { e.foreign.organizationId = e.owner.organizationId; } },
  { oracle: 'C02', id: 'workspace-collapsed', kind: 'scope-collapsed',
    plant: e => { e.foreign.workspaceId = e.owner.workspaceId; } },
  { oracle: 'C02', id: 'engine-outside-matrix', kind: 'engine-outside-matrix', plant: e => { e.browser = 'chrome'; } },
];

// ---------------------------------------------------------------------------
// L1: create and run on a real model via BYOK (PACKET L1.01-L1.05, C03).
// ---------------------------------------------------------------------------
interface DenialProbe { actor: 'non-admitted' | 'foreign'; target: 'agent' | 'start' | 'run'; status: number; leaked: boolean }
/** Every scope a refused agent read/start could affect (L1.05). */
type DenialScope = 'owner' | 'foreign' | 'non-admitted';
const DENIAL_SCOPES: readonly DenialScope[] = ['owner', 'foreign', 'non-admitted'];
interface ScopeInventory { runsBefore: string[]; runsAfter: string[]; debitsBefore: string[]; debitsAfter: string[] }
/** C03 evidence, normalized from the producer's compareRouteManifests result
 * (gw-hr-cj e2e/lib/public-ingress.ts:39-56): { matched, digests: { edge, ee,
 * console }, lm708Digest }, the three copies byte-identical (:53). */
interface ManifestEvidence { matched: boolean; digests: Record<string, string>; lm708Digest: string | null }
const ROUTE_COPIES = ['console', 'edge', 'ee']; // public-ingress.ts:33, sorted
function manifestEvidence(raw: unknown): ManifestEvidence | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const { matched, digests, lm708Digest } = raw as { matched?: unknown; digests?: unknown; lm708Digest?: unknown };
  if (!digests || typeof digests !== 'object' || Array.isArray(digests)) return null;
  return { matched: matched === true,
    digests: Object.fromEntries(Object.entries(digests).map(([name, value]) => [name, typeof value === 'string' ? value : ''])),
    lm708Digest: typeof lm708Digest === 'string' ? lm708Digest : null };
}
/** C03 over normalized producer evidence. The C03 oracle and the pre-mutation
 * admission prerequisites (CoreCell.admissionPrerequisites) share it, so the
 * early refusal names exactly the kind the late oracle would. */
function c03Check(m: ManifestEvidence | null, lm708: string): void {
  check(m && m.matched === true, 'C03', 'route-manifests');
  const names = Object.keys(m.digests).sort();
  check(JSON.stringify(names) === JSON.stringify(ROUTE_COPIES) && names.every(n => isDigest(m.digests[n])),
    'C03', 'route-manifests');
  check(new Set(Object.values(m.digests)).size === 1, 'C03', 'route-manifest-parity');
  check(isDigest(lm708) && m.lm708Digest === lm708, 'C03', 'route-manifest-lm708');
}
interface C03Evidence {
  /** The bound LM-708 digest (C01) the manifest proof must name. */
  lm708: string;
  admission: { manifests: ManifestEvidence | null };
}
const C03_ORACLE: Oracle<C03Evidence> = { id: 'C03', run(e) { c03Check(e.admission.manifests, e.lm708); } };
const C03_FAULTS: Fault<C03Evidence>[] = [
  { oracle: 'C03', id: 'manifest-proof-missing', kind: 'route-manifests', plant: e => { e.admission.manifests = null; } },
  { oracle: 'C03', id: 'manifest-not-matched', kind: 'route-manifests',
    plant: e => { if (e.admission.manifests) e.admission.manifests.matched = false; } },
  { oracle: 'C03', id: 'manifest-copy-dropped', kind: 'route-manifests',
    plant: e => { if (e.admission.manifests) delete e.admission.manifests.digests.console; } },
  { oracle: 'C03', id: 'manifest-copy-diverged', kind: 'route-manifest-parity',
    plant: e => { if (e.admission.manifests) e.admission.manifests.digests.ee = `sha256:${'9'.repeat(64)}`; } },
  { oracle: 'C03', id: 'manifest-lm708-rebound', kind: 'route-manifest-lm708',
    plant: e => { if (e.admission.manifests) e.admission.manifests.lm708Digest = `sha256:${'8'.repeat(64)}`; } },
];
interface L1Evidence extends C03Evidence {
  owner: Scope; foreign: Scope;
  admission: { phases: string[] | null; manifests: ManifestEvidence | null };
  embed: { configuredOrigin: string; capturedOrigin: string | null; refusal: string | null;
    capabilitiesAllowed: boolean | null; at404: boolean; iframes: number;
    session: { organizationId: string; workspaceId: string } | null };
  agent: { uiId: string; api: AgentRecord | null; pg: AgentRecord | null };
  run: { uiId: string; api: RunRecord | null; pg: RunRecord | null };
  provenance: { receipt: RelayReceipt | null; grantDigest: string | null };
  visible: { runId: string | null; chars: number };
  events: RunEvent[];
  denial: { probes: DenialProbe[]; barrier: boolean; scopes: Record<DenialScope, ScopeInventory>;
    dispatched: { owner: number | null; foreign: number | null } };
}
const L1_PROBES = ['non-admitted:agent', 'non-admitted:start', 'non-admitted:run', 'foreign:agent', 'foreign:start', 'foreign:run'];
const LIVE_ONLY_REFUSALS = ['relay-receipt-missing', 'scripted-provenance', 'grant-correlation'];
const L1_ORACLES: Oracle<L1Evidence>[] = [
  { id: 'L1.01', run(e) {
    check(JSON.stringify(e.admission.phases) === JSON.stringify(ADMISSION_PHASES), 'L1.01', 'admission-round-trip');
    check(e.embed.capabilitiesAllowed === true, 'L1.01', 'admission');
    check(e.embed.iframes === 1 && !e.embed.at404, 'L1.01', 'stock-absence-fallthrough');
    check(e.embed.refusal === null && e.embed.capturedOrigin === e.embed.configuredOrigin, 'L1.01', 'embed-origin');
    check(sameScope(e.embed.session, e.owner), 'L1.01', 'session-exchange');
  } },
  { id: 'L1.02', run(e) {
    const { api, pg } = e.agent;
    check(api && pg, 'L1.02', 'create-not-persisted');
    check(api !== pg, 'L1.02', 'shared-witness');
    for (const row of [api, pg]) {
      check(row.agentId === e.agent.uiId, 'L1.02', 'agent-identity');
      check(sameScope(row, e.owner) && row.accountId === e.owner.accountId, 'L1.02', 'tenant-binding');
    }
  } },
  { id: 'L1.03', liveOnly: true, run(e) {
    const { api, pg } = e.run;
    check(api && pg && api !== pg, 'L1.03', 'run-not-persisted');
    for (const row of [api, pg]) {
      check(row.runId === e.run.uiId && row.agentId === e.agent.uiId && sameScope(row, e.owner) &&
        row.projectId === e.owner.projectId && row.traceId.length > 0, 'L1.03', 'run-identity');
    }
    check(api.traceId === pg.traceId, 'L1.03', 'trace-handle');
    const receipt = e.provenance.receipt;
    check(receipt, 'L1.03', 'relay-receipt-missing');
    check(receipt.origin === 'live', 'L1.03', 'scripted-provenance');
    check(receipt.runId === e.run.uiId, 'L1.03', 'run-correlation');
    check(isDigest(e.provenance.grantDigest) && receipt.grantDigest === e.provenance.grantDigest, 'L1.03', 'grant-correlation');
  } },
  { id: 'L1.04', run(e) {
    check(e.events.length > 0 && e.events.every(ev => ev.runId === e.run.uiId), 'L1.04', 'event-run-identity');
    check(e.events.some(ev => ev.kind === 'content'), 'L1.04', 'no-content-event');
    check(e.visible.runId === e.run.uiId, 'L1.04', 'visible-run-identity');
    check(e.visible.chars > 0, 'L1.04', 'ui-output-suppressed');
  } },
  { id: 'L1.05', run(e) {
    const seen = e.denial.probes.map(p => `${p.actor}:${p.target}`);
    check(L1_PROBES.every(k => seen.includes(k)), 'L1.05', 'probe-coverage');
    check(e.denial.probes.every(p => p.status === 403 || p.status === 404), 'L1.05', 'authorization-bypass');
    check(e.denial.probes.every(p => !p.leaked), 'L1.05', 'content-leak');
    // Reconciled only after the authoritative no-work barrier, for EVERY scope the
    // refused attempts could touch: owner, foreign and the non-admitted actor.
    check(e.denial.barrier, 'L1.05', 'no-work-barrier');
    const scopes = DENIAL_SCOPES.map(s => e.denial.scopes[s]);
    check(scopes.every(s => s !== undefined && same(s.runsBefore, s.runsAfter)), 'L1.05', 'run-created');
    check(scopes.every(s => s !== undefined && same(s.debitsBefore, s.debitsAfter)), 'L1.05', 'debit-created');
    // Both bound providers: the owner's, and the foreign tenant's own mock.
    check(e.denial.dispatched.owner === 0 && e.denial.dispatched.foreign === 0, 'L1.05', 'upstream-dispatch');
  } },
  C03_ORACLE,
];
const L1_FAULTS: Fault<L1Evidence>[] = [
  { oracle: 'L1.01', id: 'admission-withdrawn', kind: 'admission', plant: e => {
    Object.assign(e.embed, { capabilitiesAllowed: false, at404: true, iframes: 0, capturedOrigin: null, refusal: 'frame-missing' });
  } },
  { oracle: 'L1.01', id: 'wrong-embed-origin', kind: 'embed-origin',
    plant: e => { Object.assign(e.embed, { capturedOrigin: null, refusal: 'origin' }); } },
  { oracle: 'L1.01', id: 'session-foreign-tenant', kind: 'session-exchange', plant: e => { e.embed.session = { ...e.foreign }; } },
  { oracle: 'L1.01', id: 'admission-phase-dropped', kind: 'admission-round-trip',
    plant: e => { e.admission.phases = (e.admission.phases ?? []).filter(p => p !== 'a-admitted-b-denied'); } },
  { oracle: 'L1.02', id: 'create-write-dropped', kind: 'create-not-persisted', plant: e => { e.agent.api = null; e.agent.pg = null; } },
  { oracle: 'L1.02', id: 'sibling-tenant-identity', kind: 'tenant-binding', plant: e => {
    for (const row of [e.agent.api, e.agent.pg]) if (row) Object.assign(row, { organizationId: e.foreign.organizationId,
      workspaceId: e.foreign.workspaceId, accountId: e.foreign.accountId });
  } },
  { oracle: 'L1.02', id: 'shared-witness', kind: 'shared-witness', plant: e => { e.agent.pg = e.agent.api; } },
  { oracle: 'L1.03', id: 'run-identity-foreign', kind: 'run-identity',
    plant: e => { if (e.run.pg) e.run.pg.organizationId = e.foreign.organizationId; } },
  { oracle: 'L1.03', id: 'scripted-receipt', kind: 'scripted-provenance', liveEvidence: true,
    plant: e => { if (e.provenance.receipt) e.provenance.receipt.origin = 'scripted'; } },
  { oracle: 'L1.03', id: 'wrong-run-receipt', kind: 'run-correlation', liveEvidence: true,
    plant: e => { if (e.provenance.receipt) e.provenance.receipt.runId = `${e.run.uiId}-other`; } },
  { oracle: 'L1.03', id: 'grant-mismatch', kind: 'grant-correlation', liveEvidence: true,
    plant: e => { if (e.provenance.receipt) e.provenance.receipt.grantDigest = `sha256:${'0'.repeat(64)}`; } },
  { oracle: 'L1.04', id: 'sibling-run-event', kind: 'event-run-identity',
    plant: e => { if (e.events[0]) e.events[0].runId = `${e.run.uiId}-sibling`; } },
  { oracle: 'L1.04', id: 'stale-run-visible', kind: 'visible-run-identity', plant: e => { e.visible.runId = `${e.run.uiId}-stale`; } },
  { oracle: 'L1.04', id: 'ui-output-suppressed', kind: 'ui-output-suppressed', plant: e => { e.visible.chars = 0; } },
  { oracle: 'L1.05', id: 'bypass-foreign-run-read', kind: 'authorization-bypass', plant: e => {
    const probe = e.denial.probes.find(p => p.actor === 'foreign' && p.target === 'run');
    if (probe) Object.assign(probe, { status: 200, leaked: true });
  } },
  { oracle: 'L1.05', id: 'leak-on-refusal', kind: 'content-leak', plant: e => {
    const probe = e.denial.probes.find(p => p.actor === 'non-admitted' && p.target === 'agent');
    if (probe) probe.leaked = true;
  } },
  { oracle: 'L1.05', id: 'run-created-on-denied-start', kind: 'run-created', plant: e => {
    const s = e.denial.scopes.owner; s.runsAfter = [...s.runsAfter, 'run-created-by-denied-start'];
  } },
  { oracle: 'L1.05', id: 'foreign-run-created-on-denied-start', kind: 'run-created', plant: e => {
    const s = e.denial.scopes.foreign; s.runsAfter = [...s.runsAfter, 'foreign-run-created-by-denied-start'];
  } },
  { oracle: 'L1.05', id: 'non-admitted-run-created', kind: 'run-created', plant: e => {
    const s = e.denial.scopes['non-admitted']; s.runsAfter = [...s.runsAfter, 'run-created-by-non-admitted-start'];
  } },
  { oracle: 'L1.05', id: 'foreign-debit-on-denied-start', kind: 'debit-created', plant: e => {
    const s = e.denial.scopes.foreign; s.debitsAfter = [...s.debitsAfter, 'debit-created-by-denied-start'];
  } },
  { oracle: 'L1.05', id: 'dispatch-on-denied-start', kind: 'upstream-dispatch', plant: e => { e.denial.dispatched.owner = 1; } },
  // Denial response and owner inventories intact; only the foreign scenario dispatched.
  { oracle: 'L1.05', id: 'dispatch-via-foreign-provider', kind: 'upstream-dispatch',
    plant: e => { e.denial.dispatched.foreign = 1; } },
  { oracle: 'L1.05', id: 'no-work-barrier-missing', kind: 'no-work-barrier', plant: e => { e.denial.barrier = false; } },
  ...C03_FAULTS,
];

// ---------------------------------------------------------------------------
// L2: stream visibly, Stop honoured with a durable terminal state.
// ---------------------------------------------------------------------------
interface L2Evidence extends C03Evidence {
  runId: string;
  contract: { runningStates: readonly string[]; stoppedStates: readonly string[]; repeatStopStatuses: readonly number[] };
  running: { state: string | null; events: RunEvent[]; visibleRunId: string | null; visibleChars: number };
  foreignStop: { status: number; ownerStateAfter: string | null };
  sibling: { before: string | null; after: string | null; stateAfter: string | null };
  stop: { acknowledged: boolean; api: RunRecord | null; pg: RunRecord | null; upstream: UpstreamState };
  post: { atTerminal: RunEvent[]; afterSettle: RunEvent[]; visibleBefore: string; visibleAfter: string;
    repeatStatus: number; runsBefore: string[]; runsAfter: string[]; debitsBefore: string[]; debitsAfter: string[] };
}
const L2_ORACLES: Oracle<L2Evidence>[] = [
  { id: 'L2.01', run(e) {
    check(e.running.state !== null && e.contract.runningStates.includes(e.running.state), 'L2.01', 'not-running');
    check(e.running.events.some(ev => ev.kind === 'content'), 'L2.01', 'events-suppressed');
    check(e.running.events.every(ev => ev.runId === e.runId), 'L2.01', 'event-run-identity');
    check(e.running.events.every((ev, i, all) => i === 0 || ev.seq > all[i - 1].seq), 'L2.01', 'event-order');
    check(e.running.visibleRunId === e.runId && e.running.visibleChars > 0, 'L2.01', 'visible-output');
  } },
  { id: 'L2.02', run(e) {
    check(e.stop.acknowledged, 'L2.02', 'ui-stop-not-acknowledged');
    const { api, pg } = e.stop;
    check(api && pg && api !== pg, 'L2.02', 'terminal-not-readable');
    for (const row of [api, pg]) {
      check(row.runId === e.runId, 'L2.02', 'terminal-run-identity');
      check(e.contract.stoppedStates.includes(row.state), 'L2.02', 'durable-not-stopped');
    }
    check(e.stop.upstream === 'cancelled', 'L2.02', 'upstream-not-cancelled');
  } },
  { id: 'L2.03', run(e) {
    // The durable ordered history at the terminal barrier AND after the repeat-Stop
    // settle: this run's events in order, exactly one contract terminal event, and
    // it is last, so no content or other work follows it. Then nothing changed.
    for (const history of [e.post.atTerminal, e.post.afterSettle]) {
      check(history.every((ev, i, all) => ev.runId === e.runId && (i === 0 || ev.seq > all[i - 1].seq)),
        'L2.03', 'terminal-history');
      const terminals = history.filter(ev => ev.kind === 'terminal').length;
      check(terminals > 0, 'L2.03', 'terminal-event-missing');
      check(terminals === 1 && history[history.length - 1].kind === 'terminal', 'L2.03', 'post-terminal-event');
    }
    check(JSON.stringify(e.post.afterSettle) === JSON.stringify(e.post.atTerminal), 'L2.03', 'history-changed');
    check(e.post.visibleAfter === e.post.visibleBefore, 'L2.03', 'post-terminal-visible');
    check(e.contract.repeatStopStatuses.includes(e.post.repeatStatus), 'L2.03', 'repeat-stop-contract');
    check(same(e.post.runsBefore, e.post.runsAfter), 'L2.03', 'repeat-stop-new-run');
    check(same(e.post.debitsBefore, e.post.debitsAfter), 'L2.03', 'repeat-stop-debit');
  } },
  { id: 'L2.04', run(e) {
    check(e.foreignStop.status === 403 || e.foreignStop.status === 404, 'L2.04', 'foreign-stop-accepted');
    check(e.foreignStop.ownerStateAfter !== null && e.contract.runningStates.includes(e.foreignStop.ownerStateAfter),
      'L2.04', 'owner-mutated-by-foreign');
    check(e.sibling.before !== null && e.sibling.after === e.sibling.before, 'L2.04', 'sibling-mutated');
    check(e.sibling.stateAfter !== null && e.contract.runningStates.includes(e.sibling.stateAfter), 'L2.04', 'sibling-stopped');
  } },
  C03_ORACLE,
];
const L2_FAULTS: Fault<L2Evidence>[] = [
  { oracle: 'L2.01', id: 'events-suppressed', kind: 'events-suppressed',
    plant: e => { e.running.events = e.running.events.filter(ev => ev.kind !== 'content'); } },
  { oracle: 'L2.01', id: 'events-reordered', kind: 'event-order', plant: e => {
    const last = e.running.events[e.running.events.length - 1];
    if (last) e.running.events.push({ ...last, seq: e.running.events[0].seq - 1 });
  } },
  { oracle: 'L2.01', id: 'visible-suppressed', kind: 'visible-output', plant: e => { e.running.visibleChars = 0; } },
  { oracle: 'L2.02', id: 'ui-only-stop', kind: 'durable-not-stopped', plant: e => {
    for (const row of [e.stop.api, e.stop.pg]) if (row) row.state = e.contract.runningStates[0] ?? 'running';
    e.stop.upstream = 'held';
  } },
  { oracle: 'L2.02', id: 'upstream-uncancelled', kind: 'upstream-not-cancelled', plant: e => { e.stop.upstream = 'held'; } },
  { oracle: 'L2.03', id: 'late-event-released', kind: 'post-terminal-event', plant: e => {
    const seq = Math.max(0, ...e.post.atTerminal.map(ev => ev.seq)) + 1;
    e.post.afterSettle = [...e.post.afterSettle, { seq, runId: e.runId, kind: 'content' }];
  } },
  // Already late in BOTH samples: equal snapshots must not hide post-terminal content.
  { oracle: 'L2.03', id: 'post-terminal-content-both-samples', kind: 'post-terminal-event', plant: e => {
    const seq = Math.max(0, ...e.post.atTerminal.map(ev => ev.seq)) + 1;
    for (const key of ['atTerminal', 'afterSettle'] as const) e.post[key] = [...e.post[key], { seq, runId: e.runId, kind: 'content' }];
  } },
  { oracle: 'L2.03', id: 'terminal-event-missing', kind: 'terminal-event-missing', plant: e => {
    for (const key of ['atTerminal', 'afterSettle'] as const) e.post[key] = e.post[key].filter(ev => ev.kind !== 'terminal');
  } },
  { oracle: 'L2.03', id: 'sibling-event-in-history', kind: 'terminal-history', plant: e => {
    if (e.post.afterSettle[0]) e.post.afterSettle[0] = { ...e.post.afterSettle[0], runId: `${e.runId}-sibling` };
  } },
  { oracle: 'L2.03', id: 'history-rewritten', kind: 'history-changed', plant: e => {
    if (e.post.afterSettle[0]) e.post.afterSettle[0] = { ...e.post.afterSettle[0], kind: 'other' };
  } },
  { oracle: 'L2.03', id: 'late-visible-output', kind: 'post-terminal-visible',
    plant: e => { e.post.visibleAfter = sha256(`${e.post.visibleAfter}-late`); } },
  { oracle: 'L2.03', id: 'extra-run-on-repeat-stop', kind: 'repeat-stop-new-run',
    plant: e => { e.post.runsAfter = [...e.post.runsAfter, 'run-created-by-repeat-stop']; } },
  { oracle: 'L2.03', id: 'debit-on-repeat-stop', kind: 'repeat-stop-debit',
    plant: e => { e.post.debitsAfter = [...e.post.debitsAfter, 'debit-created-by-repeat-stop']; } },
  { oracle: 'L2.04', id: 'foreign-stop-routed', kind: 'foreign-stop-accepted', plant: e => {
    e.foreignStop = { status: 200, ownerStateAfter: e.contract.stoppedStates[0] ?? 'stopped' };
  } },
  { oracle: 'L2.04', id: 'sibling-mutated', kind: 'sibling-mutated',
    plant: e => { e.sibling.after = sha256(`${e.sibling.before}-mutated`); } },
  ...C03_FAULTS,
];

// ---------------------------------------------------------------------------
// L3: exact trace in Observe. Identity uses HR-CJ verifyRunObserve, not a copy.
// ---------------------------------------------------------------------------
interface ObserveSet { api: ObserveWitness | null; pg: ObserveWitness | null; clickhouseFinal: ObserveWitness | null;
  foreign: { status: number; rows: unknown[] } | null }
interface L3Evidence extends C03Evidence {
  recorded: Omit<ObserveWitness, 'masked'>;
  link: { destinationTraceId: string | null; renderedTraceId: string | null };
  witnesses: ObserveSet;
  /** `required` is the accepted composition's peer inventory, read independently of
   * the declarations compared with it. */
  peers: { required: string[] | null; declarations: { peer: string; declared: boolean }[] };
  lm708Declared: boolean;
  /** agent_observe runtime admission, separate from its release-part declaration. */
  runtime: { agentObserveAdmitted: boolean | null };
  masking: { sentinelSent: boolean; uiChars: number; apiChars: number; uiRaw: boolean; apiRaw: boolean };
  /** The owner's own trace view: the positive control for the foreign denial reader. */
  ownUi: TraceView | null;
  foreignUi: (TraceView & { traceIdRendered: boolean }) | null;
}
/** Replays the independently collected witnesses into the HR-CJ verifier; the
 * shared-witness check below runs on the collected objects first. */
const replay = (w: ObserveSet) => ({
  api: async () => structuredClone(w.api), pg: async () => structuredClone(w.pg),
  clickhouseFinal: async () => structuredClone(w.clickhouseFinal), foreign: async () => structuredClone(w.foreign),
});
const L3_ORACLES: Oracle<L3Evidence>[] = [
  { id: 'L3.01', run(e) {
    check(e.recorded.traceId.length > 0 && e.link.destinationTraceId === e.recorded.traceId &&
      e.link.renderedTraceId === e.recorded.traceId, 'L3.01', 'trace-link');
  } },
  { id: 'L3.02', async run(e) {
    const { api, pg, clickhouseFinal, foreign } = e.witnesses;
    check(api && pg && clickhouseFinal && foreign, 'L3.02', 'witness-missing');
    check(new Set([api, pg, clickhouseFinal]).size === 3, 'L3.02', 'shared-witness');
    const { verifyRunObserve } = await resolveSeam('evidence');
    let verified = false;
    try { verified = (await verifyRunObserve(e.recorded, replay(e.witnesses))).passed === true; } catch { verified = false; }
    check(verified, 'L3.02', 'observe-identity');
    const required = e.peers.required;
    check(required && required.length > 0 && new Set(required).size === required.length, 'L3.02', 'agent_observe-peer-inventory');
    const declaredBy = e.peers.declarations.map(p => p.peer);
    check(new Set(declaredBy).size === declaredBy.length && same(declaredBy, required), 'L3.02', 'agent_observe-peer-missing');
    check(e.peers.declarations.every(p => p.declared === true), 'L3.02', 'agent_observe-peer');
    check(e.lm708Declared, 'L3.02', 'agent_observe-lm708');
    check(e.runtime.agentObserveAdmitted === true, 'L3.02', 'agent_observe-runtime');
  } },
  { id: 'L3.03', run(e) {
    check(e.masking.sentinelSent, 'L3.03', 'sentinel-not-sent');
    check(e.masking.uiChars > 0 && e.masking.apiChars > 0, 'L3.03', 'vacuous-content');
    const { api, pg, clickhouseFinal } = e.witnesses;
    check([api, pg, clickhouseFinal].every(w => w?.masked === true), 'L3.03', 'masking-bit');
    check(!e.masking.uiRaw && !e.masking.apiRaw, 'L3.03', 'raw-marker-exposed');
  } },
  { id: 'L3.04', run(e) {
    const foreign = e.witnesses.foreign;
    check(foreign && (foreign.status === 403 || foreign.status === 404) && Array.isArray(foreign.rows) &&
      foreign.rows.length === 0, 'L3.04', 'foreign-row');
    // B's direct navigation must POSITIVELY show the denial and render no trace row or
    // content, not merely omit the raw trace id.
    const f = e.foreignUi;
    check(f && f.refused === true, 'L3.04', 'foreign-ui-not-refused');
    check(f.rows === 0 && f.contentChars === 0 && !f.traceIdRendered, 'L3.04', 'foreign-ui-trace');
    check(e.witnesses.api?.traceId === e.recorded.traceId, 'L3.04', 'own-positive');
    // The same reader on A's own trace view renders it: the reader is not blind.
    const own = e.ownUi;
    check(own && own.refused === false && own.rows > 0 && own.contentChars > 0, 'L3.04', 'own-ui-positive');
  } },
  C03_ORACLE,
];
const mutateField = (source: 'api' | 'pg' | 'clickhouseFinal', field: keyof Omit<ObserveWitness, 'masked'>): Fault<L3Evidence> => ({
  oracle: 'L3.02', id: `${source}-${field}-mutated`, kind: 'observe-identity',
  plant: e => { const w = e.witnesses[source]; if (w) w[field] = `${w[field]}-mutated`; },
});
const L3_FAULTS: Fault<L3Evidence>[] = [
  { oracle: 'L3.01', id: 'link-swapped', kind: 'trace-link', plant: e => {
    e.link = { destinationTraceId: `${e.recorded.traceId}-other`, renderedTraceId: `${e.recorded.traceId}-other` };
  } },
  mutateField('api', 'organizationId'), mutateField('pg', 'workspaceId'), mutateField('clickhouseFinal', 'projectId'),
  mutateField('api', 'runId'), mutateField('pg', 'traceId'),
  { oracle: 'L3.02', id: 'clickhouse-reused-api-object', kind: 'shared-witness',
    plant: e => { e.witnesses.clickhouseFinal = e.witnesses.api; } },
  { oracle: 'L3.02', id: 'peer-undeclared', kind: 'agent_observe-peer',
    plant: e => { if (e.peers.declarations[0]) e.peers.declarations[0].declared = false; } },
  { oracle: 'L3.02', id: 'peer-missing', kind: 'agent_observe-peer-missing',
    plant: e => { e.peers.declarations = e.peers.declarations.slice(1); } },
  { oracle: 'L3.02', id: 'peer-inventory-unavailable', kind: 'agent_observe-peer-inventory',
    plant: e => { e.peers.required = null; } },
  { oracle: 'L3.02', id: 'declared-but-disabled', kind: 'agent_observe-runtime',
    plant: e => { e.runtime.agentObserveAdmitted = false; } },
  { oracle: 'L3.03', id: 'vacuous-content', kind: 'vacuous-content', plant: e => { e.masking.uiChars = 0; } },
  { oracle: 'L3.03', id: 'masking-bit-false', kind: 'masking-bit', plant: e => { if (e.witnesses.pg) e.witnesses.pg.masked = false; } },
  { oracle: 'L3.03', id: 'raw-marker-exposed', kind: 'raw-marker-exposed', plant: e => { e.masking.apiRaw = true; } },
  { oracle: 'L3.04', id: 'foreign-row-returned', kind: 'foreign-row',
    plant: e => { e.witnesses.foreign = { status: 200, rows: [{ traceId: e.recorded.traceId }] }; } },
  { oracle: 'L3.04', id: 'foreign-ui-rendered', kind: 'foreign-ui-trace',
    plant: e => { if (e.foreignUi) e.foreignUi.traceIdRendered = true; } },
  // Content-only leaks that never show the trace id.
  { oracle: 'L3.04', id: 'foreign-ui-content-only-leak', kind: 'foreign-ui-not-refused',
    plant: e => { e.foreignUi = { refused: false, rows: 1, contentChars: 64, traceIdRendered: false }; } },
  { oracle: 'L3.04', id: 'foreign-ui-content-behind-denial', kind: 'foreign-ui-trace',
    plant: e => { e.foreignUi = { refused: true, rows: 1, contentChars: 64, traceIdRendered: false }; } },
  { oracle: 'L3.04', id: 'own-ui-empty', kind: 'own-ui-positive',
    plant: e => { if (e.ownUi) Object.assign(e.ownUi, { rows: 0, contentChars: 0 }); } },
  ...C03_FAULTS,
];

// ---------------------------------------------------------------------------
// Control engine. evidence 'live' = positive arm and fixture-free self-controls;
// 'rehearsal' = provider-free negative/restored arms.
// ---------------------------------------------------------------------------
async function evaluate<E>(oracle: Oracle<E>, evidence: E): Promise<ConsoleOracleFailure | null> {
  try { await oracle.run(evidence); return null; } catch (error) {
    // Only the named oracle's own typed refusal counts; anything else is a test error.
    if (error instanceof ConsoleOracleFailure && error.oracle === oracle.id) return error;
    throw error;
  }
}

async function runOracles<E>(mode: Arm | 'self', oracles: Oracle<E>[], faults: Fault<E>[], evidence: E,
  records: ConsoleOracleRecord[]): Promise<void> {
  const kind: 'live' | 'rehearsal' = mode === 'positive' || mode === 'self' ? 'live' : 'rehearsal';
  const planting = mode === 'negative' || mode === 'self';
  const errors: Error[] = [];
  for (const oracle of oracles) {
    await test.step(`${oracle.id}:${mode === 'self' ? 'canonical' : mode}`, async () => {
      const failure = await evaluate(oracle, structuredClone(evidence));
      if (kind === 'rehearsal' && oracle.liveOnly) {
        const refused = failure !== null && LIVE_ONLY_REFUSALS.includes(failure.kind);
        records.push({ id: oracle.id, outcome: refused ? 'REHEARSAL_REFUSED' : 'FAIL', kind: failure?.kind });
        if (!refused) errors.push(new Error(`${oracle.id} did not refuse rehearsal provenance`));
        return;
      }
      records.push({ id: oracle.id, outcome: failure ? 'FAIL' : 'PASS', kind: failure?.kind });
      if (failure) errors.push(failure);
    });
    if (!planting) continue;
    for (const fault of faults.filter(f => f.oracle === oracle.id)) {
      await test.step(`${oracle.id}:negative:${fault.id}`, async () => {
        if (kind === 'rehearsal' && fault.liveEvidence) {
          // Needs a live relay receipt; exercised by the self-controls and refused rehearsal above.
          records.push({ id: oracle.id, fault: fault.id, outcome: 'NOT_RUN', kind: 'live-evidence-required' });
          return;
        }
        const planted = structuredClone(evidence);
        fault.plant(planted);
        const failure = await evaluate(oracle, planted);
        const killed = failure !== null && failure.kind === fault.kind;
        records.push({ id: oracle.id, fault: fault.id, outcome: killed ? 'KILLED' : 'NOT_KILLED', kind: failure?.kind });
        if (!killed) errors.push(new Error(`${oracle.id}:negative:${fault.id} was not killed by its named oracle`));
      });
    }
  }
  if (errors.length) throw errors[0];
}

// ---------------------------------------------------------------------------
// C01 on the real LM-708 seam; C03 one-factor manifest-copy control.
// ---------------------------------------------------------------------------
const APPLIES_CORE = 'tuple:T-FULL & admitted:console';
async function c01(mode: Arm, app: CoreApplicability, records: ConsoleOracleRecord[]): Promise<void> {
  await test.step(`C01:${mode}`, async () => {
    const passed = app.branch === 'capability' && isDigest(app.lm708Digest);
    records.push({ id: 'C01', outcome: passed ? 'PASS' : 'FAIL' });
    if (!passed) throw new ConsoleOracleFailure('C01', 'applicability');
  });
  if (mode !== 'negative') return;
  const lm = await resolveSeam('lm708');
  const zero = `sha256:${'0'.repeat(64)}`;
  const controls: { fault: string; expect: 'absence' | 'throws'; run: () => unknown }[] = [
    { fault: 'tuple-drift', expect: 'absence', run: () => lm.evaluateApplicability(APPLIES_CORE, app.manifest, app.stack,
      { ...app.tuples, 'T-FULL': { ...app.tuples['T-FULL'], j16Drift: 'planted' } }) },
    { fault: 'digest-drift', expect: 'throws', run: () => lm.evaluateApplicability(APPLIES_CORE, app.manifest,
      { ...app.stack, lm708Digest: zero }, app.tuples) },
    { fault: 'unknown-part', expect: 'absence', run: () => lm.evaluateApplicability(APPLIES_CORE, app.manifest, app.stack,
      app.tuples, ['j16-undeclared-part']) },
    { fault: 'admission-missing', expect: 'absence', run: () => lm.evaluateApplicability(
      'tuple:T-FULL & admitted:j16-unadmitted-surface', app.manifest, app.stack, app.tuples) },
  ];
  for (const control of controls) {
    await test.step(`C01:negative:${control.fault}`, async () => {
      let observed: 'absence' | 'capability' | 'throws';
      try { observed = control.run() === 'capability' ? 'capability' : 'absence'; } catch { observed = 'throws'; }
      records.push({ id: 'C01', fault: control.fault, outcome: observed === control.expect ? 'KILLED' : 'NOT_KILLED' });
      if (observed !== control.expect) throw new Error(`C01:negative:${control.fault} not refused as ${control.expect}`);
    });
  }
}

/** Negative arm only, from CoreCell.admissionPrerequisites in every leg: after C03's
 * early check and BEFORE the leg's first mutation (including L1's admission round
 * trip; packet C03 "fail before mutation; restore exact bytes"). `compareRouteManifests`
 * is the cell's own resolved ingress seam, never a second load. */
async function c03MutatedCopy(compareRouteManifests: (copies: Record<string, string>, manifest: unknown) => unknown,
  routeCopies: Record<string, string>, manifest: unknown, info: TestInfo, records: ConsoleOracleRecord[]): Promise<void> {
  await test.step('C03:negative:manifest-copy-mutated', async () => {
    const dir = info.outputPath('c03-manifest-copies');
    mkdirSync(dir, { recursive: true });
    const names = Object.keys(routeCopies).sort();
    const mutated = Object.fromEntries(names.map((name, i) => {
      const target = path.join(dir, `copy-${i}.json`);
      copyFileSync(routeCopies[name], target);
      return [name, target];
    }));
    appendFileSync(mutated[names[0]], '\n'); // one copy's bytes differ; its JSON is unchanged
    let refused = false, restored = false;
    try { compareRouteManifests(mutated, manifest); } catch { refused = true; }
    try { compareRouteManifests(routeCopies, manifest); restored = true; } catch { restored = false; }
    records.push({ id: 'C03', fault: 'manifest-copy-mutated', outcome: refused && restored ? 'KILLED' : 'NOT_KILLED' });
    if (!refused || !restored) throw new Error('C03:negative:manifest-copy-mutated not killed and restored');
  });
}

async function consoleAllowed(actor: CoreActor, seam: SeamResolver = resolveSeam): Promise<boolean | null> {
  const { HostAssertionClient } = await seam('hostAssertion');
  const client = await HostAssertionClient.open({ api: E2E.apiUrl }, { kind: 'session', actor });
  try {
    const parse = (body: Buffer) => JSON.parse(body.toString()) as { features?: Record<string, { allowed?: unknown }> };
    const observed = await client.assert('api', '/api/capabilities/', { status: 200, contentType: 'application/json',
      route: { label: 'console-capabilities', matches: (o: { body: Buffer }) =>
        typeof parse(o.body).features?.omega_console?.allowed === 'boolean' } });
    return parse(observed.body).features?.omega_console?.allowed === true;
  } catch { return null; } finally { await client.dispose(); }
}

async function poll<T>(read: () => Promise<T>, done: (value: T) => boolean, timeoutMs: number): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let value = await read();
  while (!done(value) && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 1_000));
    value = await read();
  }
  return value;
}

// ---------------------------------------------------------------------------
// One retained-identity chain per tenant/arm cell.
// ---------------------------------------------------------------------------
interface Bound {
  adapter: ConsoleCoreRunAdapter; owner: CoreTenant; foreign: CoreTenant; app: CoreApplicability;
  stageA: StageABinding; grant: { path: string; digest: string } | null;
}
interface Retained { agentId: string; runId: string; traceId: string; sentinel: string; siblingRunId: string }
interface Fixtures { scopeActors: ScopeActors; walk: Walk }

class CoreCell {
  private base?: Promise<Bound>;
  private bound?: Bound;
  private upstream?: UpstreamWitness;
  private retained?: Retained;
  private browser = 'unresolved';
  private readonly passed = new Set<LegId>();
  /** Bindings already established when a later gate blocks: receipts still carry them. */
  private known: { lm708: string | null; applicability: string | null; bindingDigest: string | null; grant: string | null } =
    { lm708: null, applicability: null, bindingDigest: null, grant: null };
  constructor(readonly tenant: Tenant, readonly arm: Arm, private readonly seam: SeamResolver = resolveSeam) {}

  /** Fixture-free orchestration controls only: drives this file's REAL l1 sequence
   * for a cell whose gates already resolved, over an injected adapter, fixtures and
   * seam resolver. The canonical cells never call it, and it provisions nothing. */
  static async orchestrateL1(arm: Arm, bound: Bound, seam: SeamResolver, fixtures: Fixtures, info: TestInfo,
    records: ConsoleOracleRecord[]): Promise<void> {
    const cell = new CoreCell(bound.owner.label, arm, seam);
    cell.bound = bound;
    await cell.l1(fixtures, info, records);
  }

  /** Every refusal below happens before any provisioning fixture is set up.
   * All side-effect-free gates are evaluated and every blocker is recorded, so
   * e.g. a positive cell without a grant always lists D5_GRANT_MISSING. */
  private async evaluateBase(requested: unknown, workers: number): Promise<Bound> {
    const blockers: ConsoleBlocked[] = [];
    const attempt = async <T>(gate: () => T | Promise<T>): Promise<T | undefined> => {
      try { return await gate(); } catch (error) {
        if (!(error instanceof ConsoleBlocked)) throw error;
        blockers.push(error);
        return undefined;
      }
    };
    await attempt(() => publicationGate());
    await attempt(() => browserGate(this.browser, requested));
    // CONSUMER-core argv: --workers=1. Parallel cells would race on tenant provider bindings.
    await attempt(() => { if (workers !== 1) throw new ConsoleBlocked('SELECTION_UNSAFE', 'workers'); });
    const stageA = await attempt(() => stageABinding({ invocationId: coreInvocationId(this.browser, this.tenant, this.arm) }));
    const app = await attempt(() => coreApplicability(APPLIES_CORE));
    const grant = this.arm === 'positive' ? await attempt(() => d5Grant()) : null;
    // Provider-free arms need a content-before-hold stream no producer offers yet.
    if (this.arm !== 'positive') await attempt(() => rehearsalStreamGate());
    for (const seam of ['admission', 'embed', 'hostAssertion', 'ingress', 'upstream', 'evidence', 'runAdapter'] as const) {
      await attempt(() => resolveSeam(seam));
    }
    this.known = { lm708: app?.lm708Digest ?? null, applicability: app?.branch ?? null,
      bindingDigest: stageA?.bindingDigest ?? null, grant: grant?.digest ?? null };
    if (blockers.length || !stageA || !app || grant === undefined) throw new BlockedSet(blockers);
    if (app.branch === 'absence') throw new Withdrawn('applicability:absence');
    const adapter = await openRunAdapter();
    try {
      const { owner, foreign } = await coreTenants(adapter, this.tenant);
      return { adapter, owner, foreign, app, stageA, grant };
    } catch (error) {
      await adapter.dispose().catch(() => undefined);
      throw error;
    }
  }

  async gate(leg: LegId, browserName: string, info: TestInfo): Promise<void> {
    this.browser = browserName;
    try {
      this.base ??= this.evaluateBase(info.project.use.browserName, info.config.workers);
      this.bound = await this.base;
      if (leg !== 'L1' && !this.passed.has('L1')) throw new ConsoleBlocked('UPSTREAM_LEG_NOT_PASSED', 'L1');
      if (leg === 'L3' && !this.passed.has('L2')) throw new ConsoleBlocked('UPSTREAM_LEG_NOT_PASSED', 'L2');
    } catch (error) {
      let state: ConsoleLegState, reasons: string[], details: string[];
      if (error instanceof Withdrawn) [state, reasons, details] = ['WITHDRAWN', [error.reason], []];
      else if (error instanceof BlockedSet) {
        [state, reasons, details] = ['BLOCKED', error.all.map(b => b.reason), error.all.map(b => b.detail)];
        if (!reasons.length) reasons = ['GATE_INCOMPLETE'];
      } else if (error instanceof ConsoleBlocked) [state, reasons, details] = ['BLOCKED', [error.reason], [error.detail]];
      else throw error; // an unexpected error is a test failure, never a skip
      await this.attach(info, leg, state, reasons[0], [], 0, reasons);
      info.annotations.push({ type: 'journey-status', description: `${state}:${reasons.join(',')}` });
      const detail = details.filter(Boolean).join(' ');
      test.skip(true, `${state} ${reasons.join(', ')}${detail ? ` ${detail}` : ''}`);
    }
  }

  private async attach(info: TestInfo, leg: LegId, state: ConsoleLegState, reason: string | null,
    oracles: ConsoleOracleRecord[], errors: number, blockers: readonly string[] = []): Promise<void> {
    const b = this.bound, r = this.retained;
    const body = consoleLegResult({
      schema: 'j16-core-leg-result/v1', journey: 'J16', flow: FLOW, leg, group: 'core',
      cell: { browser: this.browser, tenant: this.tenant, arm: this.arm }, arm: this.arm, state, stateReason: reason, blockers,
      provenance: state !== 'POSITIVE' ? null : this.arm === 'positive' ? 'LIVE' : 'REHEARSAL',
      lm708: b?.app.lm708Digest ?? this.known.lm708, applicability: b?.app.branch ?? this.known.applicability,
      // writtenAgainst are this source's pins, not binding evidence; only a binding
      // that passed stageABinding contributes a bindingDigest.
      stageA: { writtenAgainst: { exportManifestSha256: STAGE_A_EXPORT.manifestSha256, sourceHead: STAGE_A_EXPORT.sourceHead,
        tier1ShipmentRootSha256: STAGE_A_EXPORT.tier1ShipmentRootSha256, extensionPins: STAGE_A_EXTENSION_PINS },
        invocationId: coreInvocationId(this.browser, this.tenant, this.arm),
        bindingDigest: b?.stageA.bindingDigest ?? this.known.bindingDigest },
      grant: b?.grant?.digest ?? this.known.grant,
      handles: { agent: handle(r?.agentId), run: handle(r?.runId), trace: handle(r?.traceId),
        sentinel: handle(r?.sentinel), sibling: handle(r?.siblingRunId) },
      required: REQUIRED[leg], oracles, kitOwnedObligations: KIT_OWNED,
    }, info.errors.length + errors);
    await info.attach('console-leg-result', { contentType: 'application/json', body: JSON.stringify(body) });
  }

  async run(leg: LegId, fixtures: Fixtures, info: TestInfo): Promise<void> {
    const records: ConsoleOracleRecord[] = [];
    let failure: unknown = null;
    try {
      const b = this.bound!;
      await runOracles(this.arm, CELL_ORACLES, CELL_FAULTS,
        { browser: this.browser, owner: scopeOf(b.owner), foreign: scopeOf(b.foreign) }, records);
      await c01(this.arm, b.app, records);
      if (leg === 'L1') await this.l1(fixtures, info, records);
      else if (leg === 'L2') await this.l2(fixtures, info, records);
      else await this.l3(fixtures, info, records);
      this.passed.add(leg);
    } catch (error) { failure = error; }
    await this.attach(info, leg, 'POSITIVE', failure ? 'leg-failed' : null, records, failure ? 1 : 0);
    if (failure) throw failure;
  }

  private tag(leg: LegId) { return `${leg}-${this.tenant}-${this.arm}-${randomUUID().slice(0, 8)}`; }

  /** C03 in every leg, BEFORE that leg mutates anything (packet C03: "fail before
   * mutation"). Only L1 supplies fresh scopeActors for the L1.01 round trip, before
   * provider binding, mock stream, browser context, agent or run creation. L2/L3 pass
   * null to recheck the manifests without repeating that mutating round trip.
   * A refusal is recorded as its oracle's FAIL and thrown at once, never kept as null
   * evidence for runOracles to find after positive work. Producer error text is never
   * read or classified. */
  private async admissionPrerequisites(scopeActors: ScopeActors | null, inputs: AdmissionProofInputs, info: TestInfo,
    records: ConsoleOracleRecord[]): Promise<L1Evidence['admission']> {
    const { app } = this.bound!;
    const refuse = (failure: ConsoleOracleFailure): never => {
      records.push({ id: failure.oracle, outcome: 'FAIL', kind: failure.kind });
      throw failure;
    };
    const checkC03 = (manifests: ManifestEvidence | null) => {
      try { c03Check(manifests, app.lm708Digest); } catch (error) {
        if (error instanceof ConsoleOracleFailure) refuse(error);
        throw error;
      }
    };
    // C03 first, read-only: the producer compares the three deployed route-manifest
    // copies with each other and with LM-708 (public-ingress.ts:39-55).
    const { compareRouteManifests } = await this.seam('ingress');
    let early: ManifestEvidence | null = null;
    try { early = manifestEvidence(compareRouteManifests(inputs.routeCopies, app.manifest)); } catch { early = null; }
    checkC03(early);
    // C03 one-factor control on the deployed copies (negative arm): read-only, before the
    // leg's first mutation. NOT reached at this head: evaluateBase and l1 refuse
    // the provider-free arms first (rehearsalStreamGate); it runs once that gate is replaced.
    if (this.arm === 'negative') await c03MutatedCopy(compareRouteManifests, inputs.routeCopies, app.manifest, info, records);
    if (scopeActors === null) return { phases: null, manifests: early };
    // L1.01: the admission round trip on the fresh, non-admitted scopeActors pair. The
    // producer re-binds LM-708 and re-compares the copies before its own first mutation
    // (console-round-trip.ts:27-30); any refusal of it is the L1.01 round-trip refusal.
    const { verifyConsoleAdmission } = await this.seam('admission');
    let proof: { phases?: unknown; manifests?: unknown } | null = null;
    try {
      proof = await verifyConsoleAdmission({ manifest: app.manifest, stack: app.stack, routeCopies: inputs.routeCopies,
        orm: inputs.orm, actors: [scopeActors.ownerA, scopeActors.ownerB], hosts: inputs.hosts, embed: inputs.embed });
    } catch { proof = null; }
    if (!proof) return refuse(new ConsoleOracleFailure('L1.01', 'admission-round-trip'));
    const phases = Array.isArray(proof.phases) ? proof.phases.map(String) : null;
    if (JSON.stringify(phases) !== JSON.stringify(ADMISSION_PHASES)) refuse(new ConsoleOracleFailure('L1.01', 'admission-round-trip'));
    // proof.manifests is compareRouteManifests' own wrapper (console-round-trip.ts:30,87): the evidence.
    const admission: L1Evidence['admission'] = { phases, manifests: manifestEvidence(proof.manifests) };
    checkC03(admission.manifests);
    return admission;
  }

  private async l1({ scopeActors, walk }: Fixtures, info: TestInfo, records: ConsoleOracleRecord[]): Promise<void> {
    const { adapter, owner, foreign, app, grant } = this.bound!;
    const tag = this.tag('L1');
    // The provider-free arms never reach here (evaluateBase refused them:
    // rehearsalStreamGate); the gate repeats first so no future reordering can bind the
    // hold-only stream as the owner's provider.
    if (this.arm !== 'positive') rehearsalStreamGate();
    // L1.01 + C03 before ANY mutation by this cell, provider rebinding included.
    const inputs = await adapter.admissionProofInputs();
    const admission = await this.admissionPrerequisites(scopeActors, inputs, info, records);

    // Owner provider: live through the approved relay.
    await adapter.bindProvider(owner, { kind: 'live', grantPath: grant!.path, grantDigest: grant!.digest });
    const ownerUpstream: UpstreamWitness = adapter.liveUpstream(owner);
    this.upstream = ownerUpstream;
    // The foreign tenant's independent running sibling (L2.04), always provider-free:
    // a held role-frame stream is running, which is all the sibling has to be.
    const sibling = await heldUpstream(`j16-sibling-${tag}`, this.seam);
    await adapter.bindProvider(foreign, { kind: 'rehearsal', baseUrl: sibling.baseUrl });

    // Owner: real FA login, exact-origin embed, admitted capabilities, tenant session.
    const page = await adapter.signIn(await walk.anonymous(`J16-L1-owner-${tag}`), owner);
    const { ConsoleEmbedCapture } = await this.seam('embed');
    const capture = new ConsoleEmbedCapture(page, adapter.consoleOrigin); // attach BEFORE navigating
    await page.goto(new URL(CORE_ROUTE, E2E.appUrl).href, { waitUntil: 'domcontentloaded' });
    let capturedOrigin: string | null = null, refusal: string | null = null;
    try { capturedOrigin = (await capture.capture()).origin; } catch (error) {
      refusal = String((error as { reason?: unknown }).reason ?? 'interrupted');
    }
    const embed: L1Evidence['embed'] = { configuredOrigin: new URL(adapter.consoleOrigin).origin, capturedOrigin, refusal,
      capabilitiesAllowed: await consoleAllowed(owner, this.seam), at404: /\/404\/?$/.test(new URL(page.url()).pathname),
      iframes: await page.locator('iframe').count(), session: await adapter.sessionScope(page) };

    // L1.02: create through the Console UI, then independent API and PG reads.
    const created = await adapter.createAgent(page, `j16-${tag}`);
    const agent: L1Evidence['agent'] = { uiId: created.agentId,
      api: (await adapter.readAgent(owner, created.agentId, 'api')).agent,
      pg: (await adapter.readAgent(owner, created.agentId, 'pg')).agent };

    // L1.05 part 1: a non-admitted actor (fresh org, restored by the proof) and the
    // foreign tenant read A's agent and try to start it. Every scope those attempts
    // could affect is checkpointed first: both bound providers (the owner's and the
    // foreign tenant's own mock) and the run/debit inventory of all three scopes.
    const nonAdmitted: CoreActor = scopeActors.ownerA;
    const probes: DenialProbe[] = [];
    const actors: Record<DenialScope, CoreActor> = { owner, foreign, 'non-admitted': nonAdmitted };
    const inventory = async () => {
      const rows = {} as Record<DenialScope, { runs: string[]; debits: string[] }>;
      for (const s of DENIAL_SCOPES) {
        rows[s] = { runs: await adapter.runIds(actors[s], created.agentId), debits: await adapter.debitIds(actors[s]) };
      }
      return rows;
    };
    const before = await inventory();
    const ownerCheckpoint = await ownerUpstream.checkpoint();
    const foreignCheckpoint = await sibling.witness.checkpoint();
    for (const [who, actor] of [['non-admitted', nonAdmitted], ['foreign', foreign]] as const) {
      const read = await adapter.readAgent(actor, created.agentId, 'api');
      probes.push({ actor: who, target: 'agent', status: read.status, leaked: read.agent !== null });
      const start = await adapter.startRunApi(actor, created.agentId, DENIED_PROMPT);
      probes.push({ actor: who, target: 'start', status: start.status, leaked: start.runId !== null });
    }
    // Authoritative no-work barrier, then the final checkpoints (upstream.ts:96-98),
    // all before the legitimate sibling and owner starts below.
    let barrier = false;
    try { await adapter.noWorkBarrier(DENIAL_SCOPES.map(s => actors[s])); barrier = true; } catch { barrier = false; }
    const since = async (witness: UpstreamWitness, checkpoint: unknown): Promise<number | null> => {
      try { return (await witness.since(checkpoint, 0)).count; } catch { return null; }
    };
    const dispatched = { owner: await since(ownerUpstream, ownerCheckpoint), foreign: await since(sibling.witness, foreignCheckpoint) };
    const after = await inventory();
    const scopes = Object.fromEntries(DENIAL_SCOPES.map(s => [s, { runsBefore: before[s].runs, runsAfter: after[s].runs,
      debitsBefore: before[s].debits, debitsAfter: after[s].debits }])) as Record<DenialScope, ScopeInventory>;

    // Start B's sibling before A's run so L2 stops A while B genuinely runs.
    const foreignPage = await adapter.signIn(await walk.anonymous(`J16-L1-sibling-${tag}`), foreign);
    const siblingAgent = await adapter.createAgent(foreignPage, `j16-sibling-${tag}`);
    const siblingRun = await adapter.startRun(foreignPage, siblingAgent.agentId, SIBLING_PROMPT);
    // Retained as soon as each run exists, so cleanup can stop it even if L1 fails later.
    const retained: Retained = { agentId: created.agentId, runId: '', traceId: '', sentinel: '', siblingRunId: siblingRun.runId };
    this.retained = retained;

    // L1.03/L1.04: the owner's run, carrying the synthetic sentinel for L3.03.
    const sentinel = adapter.maskingSentinel();
    const started = await adapter.startRun(page, created.agentId, runPrompt(sentinel));
    Object.assign(retained, { runId: started.runId, sentinel });
    const visible = await poll(() => adapter.visibleOutput(page), v => v.text.length > 0, OUTPUT_WAIT);
    const events = await adapter.runEvents(owner, started.runId);
    const run: L1Evidence['run'] = { uiId: started.runId, api: (await adapter.readRun(owner, started.runId, 'api')).run,
      pg: (await adapter.readRun(owner, started.runId, 'pg')).run };
    const receipt = this.arm === 'positive'
      ? await poll(() => adapter.relayReceipt(started.runId), r => r !== null, RECEIPT_WAIT)
      : await adapter.relayReceipt(started.runId);
    for (const [who, actor] of [['non-admitted', nonAdmitted], ['foreign', foreign]] as const) {
      const read = await adapter.readRun(actor, started.runId, 'api');
      probes.push({ actor: who, target: 'run', status: read.status, leaked: read.run !== null });
    }
    retained.traceId = run.api?.traceId ?? '';

    const evidence: L1Evidence = { owner: scopeOf(owner), foreign: scopeOf(foreign), lm708: app.lm708Digest,
      admission, embed, agent, run, provenance: { receipt, grantDigest: grant?.digest ?? null },
      visible: { runId: visible.runId, chars: visible.text.length }, events,
      denial: { probes, barrier, scopes, dispatched } };
    await runOracles(this.arm, L1_ORACLES, L1_FAULTS, evidence, records);
  }

  private async l2({ walk }: Fixtures, info: TestInfo, records: ConsoleOracleRecord[]): Promise<void> {
    const { adapter, owner, foreign, app } = this.bound!;
    const inputs = await adapter.admissionProofInputs();
    const admission = await this.admissionPrerequisites(null, inputs, info, records);
    const r = this.retained!;
    const page = await adapter.signIn(await walk.anonymous(`J16-L2-owner-${this.tag('L2')}`), owner);
    await adapter.openRun(page, r.runId);
    // L2.01: ordered content and visible output while the SAME run is running.
    const visible = await poll(() => adapter.visibleOutput(page), v => v.text.length > 0, OUTPUT_WAIT);
    const runningState = (await adapter.readRun(owner, r.runId, 'api')).run?.state ?? null;
    const runningEvents = await adapter.runEvents(owner, r.runId);
    // L2.04: B tries to stop A by id while A runs; B's own run is snapshotted.
    const foreignStop = await adapter.stopViaApi(foreign, r.runId);
    const ownerStateAfter = (await adapter.readRun(owner, r.runId, 'api')).run?.state ?? null;
    const siblingBefore = (await adapter.readRun(foreign, r.siblingRunId, 'api')).run;
    // L2.02: Stop in the UI, then a persistence barrier and independent API/PG reads.
    const { acknowledged } = await adapter.stopViaUi(page);
    await adapter.settled(r.runId);
    const stopApi = (await adapter.readRun(owner, r.runId, 'api')).run;
    const stopPg = (await adapter.readRun(owner, r.runId, 'pg')).run;
    const upstream = await this.upstream!.state(r.runId);
    // L2.03: nothing after terminal; repeated Stop creates no run and no debit.
    const atTerminal = await adapter.runEvents(owner, r.runId);
    const visibleBefore = sha256((await adapter.visibleOutput(page)).text);
    const runsBefore = await adapter.runIds(owner, r.agentId), debitsBefore = await adapter.debitIds(owner);
    const repeat = await adapter.stopViaApi(owner, r.runId);
    await adapter.settled(r.runId);
    const afterSettle = await adapter.runEvents(owner, r.runId);
    const visibleAfter = sha256((await adapter.visibleOutput(page)).text);
    const runsAfter = await adapter.runIds(owner, r.agentId), debitsAfter = await adapter.debitIds(owner);
    const siblingAfter = (await adapter.readRun(foreign, r.siblingRunId, 'api')).run;

    const evidence: L2Evidence = { runId: r.runId, lm708: app.lm708Digest, admission,
      contract: { runningStates: [...adapter.runningStates], stoppedStates: [...adapter.stoppedStates],
        repeatStopStatuses: [...adapter.repeatStopStatuses] },
      running: { state: runningState, events: runningEvents, visibleRunId: visible.runId, visibleChars: visible.text.length },
      foreignStop: { status: foreignStop.status, ownerStateAfter },
      sibling: { before: siblingBefore ? sha256(JSON.stringify(siblingBefore)) : null,
        after: siblingAfter ? sha256(JSON.stringify(siblingAfter)) : null, stateAfter: siblingAfter?.state ?? null },
      stop: { acknowledged, api: stopApi, pg: stopPg, upstream },
      post: { atTerminal, afterSettle, visibleBefore, visibleAfter, repeatStatus: repeat.status,
        runsBefore, runsAfter, debitsBefore, debitsAfter } };
    await runOracles(this.arm, L2_ORACLES, L2_FAULTS, evidence, records);
  }

  private async l3({ walk }: Fixtures, info: TestInfo, records: ConsoleOracleRecord[]): Promise<void> {
    const { adapter, owner, foreign, app } = this.bound!;
    const inputs = await adapter.admissionProofInputs();
    const admission = await this.admissionPrerequisites(null, inputs, info, records);
    const r = this.retained!;
    const page = await adapter.signIn(await walk.anonymous(`J16-L3-owner-${this.tag('L3')}`), owner);
    await adapter.openRun(page, r.runId);
    // L3.01: the run's OWN trace link, not a seeded trace.
    const link = await adapter.openTrace(page);
    const read = async <T>(fn: () => Promise<T>): Promise<T | null> => { try { return await fn(); } catch { return null; } };
    // L3.04 own-tenant positive: the same view reader on A's own opened trace.
    const view = (p: Page) => read(() => adapter.observeTraceView(p));
    const ownUi = await view(page);
    const recorded = { organizationId: owner.organizationId, workspaceId: owner.workspaceId,
      projectId: owner.projectId, runId: r.runId, traceId: r.traceId };
    // L3.02/L3.04: three independent reads plus the foreign reader, each fresh.
    const readers = adapter.observeReaders(recorded, owner, foreign);
    const witnesses: ObserveSet = { api: await read(() => readers.api()), pg: await read(() => readers.pg()),
      clickhouseFinal: await read(() => readers.clickhouseFinal()), foreign: await read(() => readers.foreign()) };
    // agent_observe: the composition's own peer inventory, each peer's declaration,
    // the LM-708 release-part declaration and, separately, its runtime admission.
    const composed = await read(() => adapter.composedPeers('agent_observe'));
    const peers: L3Evidence['peers'] = { required: composed ? [...composed] : null,
      declarations: await adapter.peerDeclarations('agent_observe') };
    const admitted = await read(() => adapter.flagAdmission('agent_observe', owner));
    const runtime = { agentObserveAdmitted: admitted && typeof admitted.admitted === 'boolean' ? admitted.admitted : null };
    const lm = await resolveSeam('lm708');
    let lm708Declared = false;
    try {
      lm708Declared = lm.evaluateApplicability('declared:agent_observe', app.manifest, app.stack, app.tuples) === 'capability';
    } catch { lm708Declared = false; }
    // L3.03: real Observe UI/API content must not carry the raw sentinel.
    const content = await adapter.observeContent(owner, r.traceId, page);
    const masking = { sentinelSent: r.sentinel.length > 0, uiChars: content.ui.length, apiChars: content.api.length,
      uiRaw: content.ui.includes(r.sentinel), apiRaw: content.api.includes(r.sentinel) };
    // L3.04: B navigates directly to A's trace destination; the view reader must show
    // B's positive denial and no trace rows/content, and the raw id stays absent too.
    const foreignPage = await adapter.signIn(await walk.anonymous(`J16-L3-foreign-${this.tag('L3')}`), foreign);
    await foreignPage.goto(link.url, { waitUntil: 'domcontentloaded' });
    const foreignView = await view(foreignPage);
    const foreignText = await foreignPage.locator('body').innerText().catch(() => '');
    const evidence: L3Evidence = { recorded, lm708: app.lm708Digest, admission, link: { destinationTraceId: link.destinationTraceId,
      renderedTraceId: link.renderedTraceId }, witnesses, peers, lm708Declared, runtime, masking, ownUi,
      foreignUi: foreignView ? { ...foreignView,
        traceIdRendered: r.traceId.length === 0 || foreignText.includes(r.traceId) } : null };
    await runOracles(this.arm, L3_ORACLES, L3_FAULTS, evidence, records);
  }

  /** After L3 (or the first refusal): stop what this cell started, then dispose. */
  async cleanup(): Promise<void> {
    const b = this.bound, r = this.retained;
    if (!b) return;
    try {
      if (r?.siblingRunId) await b.adapter.stopViaApi(b.foreign, r.siblingRunId).catch(() => undefined);
      if (r?.runId && !this.passed.has('L2')) await b.adapter.stopViaApi(b.owner, r.runId).catch(() => undefined);
    } finally {
      await b.adapter.dispose().catch(() => undefined);
    }
  }
}

const coreLegs = legs.filter(leg => (CORE_LEGS as readonly string[]).includes(leg.id));
for (const tenant of ['A', 'B'] as const) {
  for (const arm of ['positive', 'negative', 'restored'] as const) {
    test.describe(cellTitle(tenant, arm), () => {
      test.describe.configure({ mode: 'serial', retries: 0 });
      const cell = new CoreCell(tenant, arm);
      // browserName is an option: reading it launches nothing and provisions nobody.
      test.beforeEach(async ({ browserName }, info) => {
        const leg = coreLegs.find(l => info.title.startsWith(`${FLOW}.${l.id}:`));
        if (!leg) throw new Error('J16 core: unregistered case title');
        await cell.gate(leg.id as LegId, browserName, info);
      });
      test.afterAll(async () => { await cell.cleanup(); });
      for (const leg of coreLegs) {
        // Not @flow: the absence case above owns JRN-E2E-016.<leg> in FLOWS.md.
        test(`${FLOW}.${leg.id}: J16.${leg.id} ${leg.title}`, {
          tag: ['@journey', '@journey-J16', '@console-core', `@arm-${arm}`, ...(arm === 'positive' ? [] : ['@mock-provider'])],
          annotation: [{ type: 'journey-flow-id', description: FLOW },
            { type: 'journey-cell', description: JSON.stringify({ journey: 'J16', group: 'core', leg: leg.id, tenant, arm,
              required: REQUIRED[leg.id as LegId], kitOwned: Object.keys(KIT_OWNED) }) }],
        }, async ({ scopeActors, walk }, info) => {
          test.setTimeout(480_000);
          await cell.run(leg.id as LegId, { scopeActors, walk }, info);
        });
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Red-first, fixture-free: every oracle passes canonical live-shaped synthetic
// evidence, and every planted fault above is refused by exactly its named
// oracle and kind. Synthetic opaque ids only; nothing is provisioned.
// ---------------------------------------------------------------------------
const SCOPE_A: Scope = { organizationId: 'org-a', workspaceId: 'ws-a', projectId: 'project-a', accountId: 'account-a' };
const SCOPE_B: Scope = { organizationId: 'org-b', workspaceId: 'ws-b', projectId: 'project-b', accountId: 'account-b' };
const GRANT = `sha256:${'a'.repeat(64)}`;
const canonicalRun = (): RunRecord => ({ runId: 'run-1', agentId: 'agent-1', organizationId: 'org-a', workspaceId: 'ws-a',
  projectId: 'project-a', traceId: 'trace-1', state: 'running' });
const LM708 = `sha256:${'f'.repeat(64)}`;
const ROUTE_DIGEST = `sha256:${'1'.repeat(64)}`;
/** compareRouteManifests' OWN result (public-ingress.ts:55), BEFORE normalization:
 * the three deployed copies byte-identical, so their digests are equal. */
const producerManifests = () => ({ matched: true as const,
  digests: { edge: ROUTE_DIGEST, ee: ROUTE_DIGEST, console: ROUTE_DIGEST }, lm708Digest: LM708 });
const emptyInventory = (): ScopeInventory => ({ runsBefore: [], runsAfter: [], debitsBefore: [], debitsAfter: [] });
function canonicalL1(): L1Evidence {
  return { owner: { ...SCOPE_A }, foreign: { ...SCOPE_B }, lm708: LM708,
    admission: { phases: [...ADMISSION_PHASES], manifests: manifestEvidence(producerManifests()) },
    embed: { configuredOrigin: 'http://localhost:3300', capturedOrigin: 'http://localhost:3300', refusal: null,
      capabilitiesAllowed: true, at404: false, iframes: 1, session: { organizationId: 'org-a', workspaceId: 'ws-a' } },
    agent: { uiId: 'agent-1', api: { agentId: 'agent-1', organizationId: 'org-a', workspaceId: 'ws-a', accountId: 'account-a' },
      pg: { agentId: 'agent-1', organizationId: 'org-a', workspaceId: 'ws-a', accountId: 'account-a' } },
    run: { uiId: 'run-1', api: canonicalRun(), pg: canonicalRun() },
    provenance: { receipt: { runId: 'run-1', grantDigest: GRANT, origin: 'live' }, grantDigest: GRANT },
    visible: { runId: 'run-1', chars: 12 },
    events: [{ seq: 1, runId: 'run-1', kind: 'content' }, { seq: 2, runId: 'run-1', kind: 'content' }],
    denial: { probes: L1_PROBES.map(k => ({ actor: k.split(':')[0] as DenialProbe['actor'],
      target: k.split(':')[1] as DenialProbe['target'], status: 404, leaked: false })), barrier: true,
    scopes: { owner: emptyInventory(), foreign: emptyInventory(), 'non-admitted': emptyInventory() },
    dispatched: { owner: 0, foreign: 0 } } };
}
function canonicalL2(): L2Evidence {
  const stopped = { ...canonicalRun(), state: 'cancelled' };
  const terminal: RunEvent[] = [{ seq: 1, runId: 'run-1', kind: 'content' }, { seq: 2, runId: 'run-1', kind: 'terminal' }];
  return { runId: 'run-1', lm708: LM708, admission: { manifests: manifestEvidence(producerManifests()) },
    contract: { runningStates: ['running'], stoppedStates: ['cancelled'], repeatStopStatuses: [200, 409] },
    running: { state: 'running', events: [{ seq: 1, runId: 'run-1', kind: 'content' }], visibleRunId: 'run-1', visibleChars: 5 },
    foreignStop: { status: 404, ownerStateAfter: 'running' },
    sibling: { before: sha256('sibling'), after: sha256('sibling'), stateAfter: 'running' },
    stop: { acknowledged: true, api: { ...stopped }, pg: { ...stopped }, upstream: 'cancelled' },
    post: { atTerminal: terminal, afterSettle: terminal.map(ev => ({ ...ev })), visibleBefore: sha256('v'), visibleAfter: sha256('v'),
      repeatStatus: 409, runsBefore: ['run-1'], runsAfter: ['run-1'], debitsBefore: [], debitsAfter: [] } };
}
function canonicalL3(): L3Evidence {
  const recorded = { organizationId: 'org-a', workspaceId: 'ws-a', projectId: 'project-a', runId: 'run-1', traceId: 'trace-1' };
  return { recorded, lm708: LM708, admission: { manifests: manifestEvidence(producerManifests()) },
    link: { destinationTraceId: 'trace-1', renderedTraceId: 'trace-1' },
    witnesses: { api: { ...recorded, masked: true }, pg: { ...recorded, masked: true },
      clickhouseFinal: { ...recorded, masked: true }, foreign: { status: 404, rows: [] } },
    peers: { required: ['fa', 'edge', 'ee'],
      declarations: [{ peer: 'fa', declared: true }, { peer: 'edge', declared: true }, { peer: 'ee', declared: true }] },
    lm708Declared: true, runtime: { agentObserveAdmitted: true },
    masking: { sentinelSent: true, uiChars: 40, apiChars: 40, uiRaw: false, apiRaw: false },
    ownUi: { refused: false, rows: 3, contentChars: 120 },
    foreignUi: { refused: true, rows: 0, contentChars: 0, traceIdRendered: false } };
}
const blockedReason = (gate: () => unknown) => {
  try { gate(); return null; } catch (error) { return error instanceof ConsoleBlocked ? error.reason : 'unexpected'; }
};
/** packets/J16/CONSUMER-core.json (sha256 61bdc9d19dc2912e3a093e8f7c94de5f2f7cc5335211cecacffb5deb8c71f1d0),
 * invocations j16-core-chromium-a-positive and j16-core-firefox-a-positive, verbatim. */
const CONSUMER_CORE_ARGV: Readonly<Record<'chromium' | 'firefox', readonly string[]>> = {
  chromium: ['yarn', 'playwright', 'test', 'flows/journeys/console-core-run.spec.ts', '--config=playwright.config.ts',
    '--grep=\\[J16 group=core tenant=A arm=positive\\]', '--browser=chromium', '--workers=1', '--retries=0', '--reporter=line'],
  firefox: ['yarn', 'playwright', 'test', 'flows/journeys/console-core-run.spec.ts', '--config=playwright.config.ts',
    '--grep=\\[J16 group=core tenant=A arm=positive\\]', '--browser=firefox', '--workers=1', '--retries=0', '--reporter=line'],
};
/** Thrown by the orchestration control's recording doubles at the first operation
 * the L1 prerequisites must precede. Deliberately neither a ConsoleOracleFailure nor
 * a ConsoleBlocked, so reaching an operation can never be mistaken for a refusal. */
class OrchestrationReached extends Error {
  constructor(readonly op: string) { super(`orchestration reached ${op}`); this.name = 'OrchestrationReached'; }
}

test.describe('JRN-E2E-016 J16 core oracle controls (provider-free, no stack)', () => {
  test('gates and the capabilityPassed reducer refuse every non-qualifying input', async () => {
    const blocked = blockedReason;
    expect(blocked(() => browserGate('webkit', undefined, null))).toBe('BROWSER_ENGINE_UNQUALIFIED:webkit');
    expect(blocked(() => browserGate('chromium', 'firefox', null))).toBe('BROWSER_SELECTION_CONFLICT:firefox->chromium');
    expect(blocked(() => browserGate('firefox', 'firefox', null))).toBeNull();
    expect(blocked(() => browserGate('firefox', 'firefox', 'chromium'))).toBe('BROWSER_SELECTION_CONFLICT:chromium->firefox');
    expect(blocked(() => rehearsalStreamGate())).toBe('FIXTURE_CONTRACT_MISSING:upstream.content-before-hold');
    const names = ['E2E_J16_D5_GRANT', 'E2E_J16_D5_GRANT_SHA256', 'CJ_CONSUMER_BINDING'] as const;
    const saved = names.map(name => [name, process.env[name]] as const);
    try {
      for (const name of names) delete process.env[name];
      expect(blocked(() => d5Grant()), 'no grant handle').toBe('D5_GRANT_MISSING');
      expect(blocked(() => stageABinding({ invocationId: coreInvocationId('firefox', 'A', 'positive') })))
        .toBe('STAGE_A_BINDING_MISSING:CJ_CONSUMER_BINDING');
      process.env.E2E_J16_D5_GRANT = path.join(test.info().outputDir, 'absent-grant.json');
      process.env.E2E_J16_D5_GRANT_SHA256 = 'a'.repeat(64);
      expect(blocked(() => d5Grant()), 'unbound grant handle').toBe('D5_GRANT_UNBOUND');
    } finally {
      for (const [name, value] of saved) { if (value === undefined) delete process.env[name]; else process.env[name] = value; }
    }
    // capabilityPassed is computed: one positive input passes, every one-factor change refuses.
    const pass: ConsoleOracleRecord[] = REQUIRED.L1.map(id => ({ id, outcome: 'PASS' as const }));
    const live = { state: 'POSITIVE' as const, arm: 'positive' as const, provenance: 'LIVE' as const,
      required: REQUIRED.L1, oracles: pass, errors: 0 };
    expect(computeCapabilityPassed(live)).toBe(true);
    expect(computeCapabilityPassed({ ...live, provenance: 'REHEARSAL' })).toBe(false);
    expect(computeCapabilityPassed({ ...live, arm: 'restored' })).toBe(false);
    expect(computeCapabilityPassed({ ...live, state: 'WITHDRAWN' })).toBe(false);
    expect(computeCapabilityPassed({ ...live, state: 'BLOCKED' })).toBe(false);
    expect(computeCapabilityPassed({ ...live, errors: 1 })).toBe(false);
    expect(computeCapabilityPassed({ ...live, oracles: pass.slice(1) })).toBe(false);
    expect(computeCapabilityPassed({ ...live, oracles: [...pass, { id: 'L1.03', outcome: 'REHEARSAL_REFUSED' }] })).toBe(false);
    expect(computeCapabilityPassed({ ...live, oracles: [...pass, { id: 'L1.01', fault: 'x', outcome: 'KILLED' }] })).toBe(false);
  });
  test('C02 and L1 oracles pass canonical evidence; every planted fault kills its named oracle', async ({}, info) => {
    const records: ConsoleOracleRecord[] = [];
    await runOracles('self', CELL_ORACLES, CELL_FAULTS, { browser: 'firefox', owner: { ...SCOPE_A }, foreign: { ...SCOPE_B } }, records);
    await runOracles('self', L1_ORACLES, L1_FAULTS, canonicalL1(), records);
    // The rehearsal invariant: scripted provenance is refused even with every other field intact.
    const rehearsal = canonicalL1();
    rehearsal.provenance = { receipt: { runId: 'run-1', grantDigest: GRANT, origin: 'scripted' }, grantDigest: null };
    await runOracles('restored', L1_ORACLES.filter(o => o.liveOnly), [], rehearsal, records);
    // C03 consumes compareRouteManifests' actual wrapper: canonicalL1 already feeds
    // the literal producer shape through manifestEvidence; when the ingress seam is
    // composed, the REAL producer output over three identical copies must pass too.
    const c03 = L1_ORACLES.find(o => o.id === 'C03')!;
    let ingress: Awaited<ReturnType<typeof resolveSeam>> | null = null;
    try { ingress = await resolveSeam('ingress'); } catch (error) {
      const reason = error instanceof ConsoleBlocked ? error.reason : 'SEAM_INCOMPATIBLE:ingress';
      info.annotations.push({ type: 'journey-status', description: `BLOCKED:${reason} (C03 real-producer wrapper step)` });
    }
    if (ingress) {
      const { compareRouteManifests } = ingress;
      await test.step('C03:canonical:producer-wrapper', async () => {
        const dir = info.outputPath('c03-producer-copies');
        mkdirSync(dir, { recursive: true });
        const bytes = '{"routes":[]}\n', digest = `sha256:${sha256(bytes)}`;
        const copies = Object.fromEntries(['edge', 'ee', 'console'].map(name => {
          const file = path.join(dir, `${name}.json`);
          writeFileSync(file, bytes);
          return [name, file];
        }));
        const manifest = { digest: LM708, contracts: { routeManifestEdge: digest, routeManifestEE: digest, routeManifestConsole: digest } };
        const evidence = canonicalL1();
        evidence.admission.manifests = manifestEvidence(compareRouteManifests(copies, manifest));
        const failure = await evaluate(c03, evidence);
        records.push({ id: 'C03', outcome: failure ? 'FAIL' : 'PASS', kind: failure?.kind ?? 'producer-wrapper' });
        if (failure) throw failure;
      });
    }
    await info.attach('j16-oracle-controls', { contentType: 'application/json', body: JSON.stringify(records) });
  });
  test('L2 oracles pass canonical evidence; every planted fault kills its named oracle', async ({}, info) => {
    const records: ConsoleOracleRecord[] = [];
    await runOracles('self', L2_ORACLES, L2_FAULTS, canonicalL2(), records);
    await info.attach('j16-oracle-controls', { contentType: 'application/json', body: JSON.stringify(records) });
  });
  test('L3 oracles pass canonical evidence; every planted fault kills its named oracle', async ({}, info) => {
    try { await resolveSeam('evidence'); } catch (error) {
      // L3.02 delegates identity to HR-CJ verifyRunObserve; without it the
      // L3 controls are BLOCKED, never reimplemented or passed.
      const reason = error instanceof ConsoleBlocked ? error.reason : 'SEAM_INCOMPATIBLE:evidence';
      info.annotations.push({ type: 'journey-status', description: `BLOCKED:${reason}` });
      test.skip(true, `BLOCKED ${reason}`);
    }
    const records: ConsoleOracleRecord[] = [];
    await runOracles('self', L3_ORACLES, L3_FAULTS, canonicalL3(), records);
    await info.attach('j16-oracle-controls', { contentType: 'application/json', body: JSON.stringify(records) });
  });
  test('Stage A binding: a kit-shaped binding for this cell reaches only the unaccepted readiness join', async ({}, info) => {
    const id = coreInvocationId('firefox', 'A', 'positive');
    // Landing must be disjoint from the source checkout (extension.py), so not under test-results.
    const root = mkdtempSync(path.join(tmpdir(), 'j16-stage-a-'));
    const landing = path.join(root, 'landing'), output = path.join(landing, id);
    mkdirSync(path.join(output, 'contexts'), { recursive: true });
    const member = (name: string) => `j16 control member ${name}\n`;
    const extension = (dir: string, stale?: string) => {
      mkdirSync(dir, { recursive: true });
      for (const name of Object.keys(STAGE_A_EXTENSION_PINS)) writeFileSync(path.join(dir, name), member(name));
      if (stale) appendFileSync(path.join(dir, stale), 'stale export\n');
      return dir;
    };
    const current = extension(path.join(root, 'extension')), stale = extension(path.join(root, 'extension-stale'), 'JOIN-PLAN.json');
    // Control pins over the control members; production uses STAGE_A_EXTENSION_PINS (checked last).
    const pins = Object.fromEntries(Object.keys(STAGE_A_EXTENSION_PINS).map(name => [name, sha256(member(name))]));
    // extension.py:50-68 native_binding plus the :128-138 reporter/observer slots, with
    // placeholder values in the PROPOSED readiness fields: placeholders never satisfy.
    const kit = (): Record<string, unknown> => ({ schema: STAGE_A_BINDING_SCHEMA, invocationId: id,
      sourceRoot: CONSOLE_SOURCE_ROOT, landingRoot: landing, extensionRoot: current,
      reporter: { module: path.join(CONSOLE_SOURCE_ROOT, J16_CORE_HOOKS.reporter.module),
        options: structuredClone(J16_CORE_HOOKS.reporter.options), outputFile: path.join(output, J16_CORE_HOOKS.reporter.outputPath) },
      observer: { module: path.join(CONSOLE_SOURCE_ROOT, J16_CORE_HOOKS.observer.module), export: J16_CORE_HOOKS.observer.export,
        options: structuredClone(J16_CORE_HOOKS.observer.options), outputDirectory: path.join(output, 'contexts') },
      appSideReceipt: 'control-placeholder', baseObservationReceipt: 'control-placeholder' });
    const cases: { id: string; expect: string; pins?: Readonly<Record<string, string>>; plant(b: Record<string, unknown>): void }[] = [
      { id: 'restored', expect: 'STAGE_A_JOIN_UNACCEPTED:readiness-contract', plant: () => undefined },
      { id: 'wrong-schema', expect: 'STAGE_A_BINDING_INVALID:schema', plant: b => { b.schema = 'cj-walk-consumer-binding/v0'; } },
      { id: 'wrong-invocation', expect: 'STAGE_A_BINDING_INVALID:invocation',
        plant: b => { b.invocationId = coreInvocationId('firefox', 'B', 'positive'); } },
      { id: 'foreign-source', expect: 'STAGE_A_BINDING_INVALID:source', plant: b => { b.sourceRoot = path.join(root, 'other-source'); } },
      { id: 'reporter-options-drift', expect: 'STAGE_A_BINDING_INVALID:reporter',
        plant: b => { (b.reporter as { options: Record<string, unknown> }).options.rejectSkipped = false; } },
      { id: 'stale-export', expect: 'STAGE_A_BINDING_INVALID:export-pin', plant: b => { b.extensionRoot = stale; } },
      { id: 'missing-readiness', expect: 'STAGE_A_READINESS_MISSING:baseObservationReceipt',
        plant: b => { delete b.baseObservationReceipt; } },
      // The production table refuses the control members: synthetic bytes never match a real pin.
      { id: 'production-pins', expect: 'STAGE_A_BINDING_INVALID:export-pin', pins: STAGE_A_EXTENSION_PINS, plant: () => undefined },
    ];
    const saved = process.env.CJ_CONSUMER_BINDING;
    const records: ConsoleOracleRecord[] = [];
    try {
      for (const c of cases) {
        await test.step(`stage-a-binding:${c.id}`, async () => {
          const binding = kit();
          c.plant(binding);
          const file = path.join(landing, `binding-${c.id}.json`);
          writeFileSync(file, JSON.stringify(binding));
          process.env.CJ_CONSUMER_BINDING = file;
          const reason = blockedReason(() => stageABinding({ invocationId: id }, c.pins ?? pins));
          records.push({ id: 'stage-a-binding', fault: c.id, outcome: reason === c.expect ? 'KILLED' : 'NOT_KILLED', kind: reason ?? 'accepted' });
          expect(reason, c.id).toBe(c.expect);
        });
      }
    } finally {
      if (saved === undefined) delete process.env.CJ_CONSUMER_BINDING; else process.env.CJ_CONSUMER_BINDING = saved;
      rmSync(root, { recursive: true, force: true });
    }
    await info.attach('j16-oracle-controls', { contentType: 'application/json', body: JSON.stringify(records) });
  });
  test('provider-free arms are BLOCKED: the held-stream fixture carries no content before its hold', async ({}, info) => {
    // Source contract (gw-hr-cj e2e/stack/mock-llm/server.mjs:325-357 @ e0133094d1):
    // held = role frame only; release() emits all content and COMPLETES the stream.
    expect(blockedReason(() => rehearsalStreamGate())).toBe('FIXTURE_CONTRACT_MISSING:upstream.content-before-hold');
    const records: ConsoleOracleRecord[] = [];
    const refuse = async <E>(oracle: Oracle<E>, evidence: E, fault: string, kind: string) => {
      const failure = await evaluate(oracle, evidence);
      records.push({ id: oracle.id, fault, outcome: failure?.kind === kind ? 'KILLED' : 'NOT_KILLED', kind: failure?.kind });
      expect(failure?.kind, `${oracle.id}:${fault}`).toBe(kind);
    };
    // While held: only the role frame, no content event and nothing visible.
    const held = canonicalL1();
    held.events = [{ seq: 1, runId: 'run-1', kind: 'other' }];
    held.visible.chars = 0;
    await refuse(L1_ORACLES.find(o => o.id === 'L1.04')!, held, 'held-role-frame-only', 'no-content-event');
    // After release: the run completed on its own, so Stop never meets a running run.
    const released = canonicalL2();
    released.running.state = 'completed';
    await refuse(L2_ORACLES.find(o => o.id === 'L2.01')!, released, 'released-completes-before-stop', 'not-running');
    // Content-before-hold and cancellation/restoration reachability need an accepted
    // producer fixture; until then this half is carried, never passed.
    info.annotations.push({ type: 'journey-status',
      description: 'BLOCKED:FIXTURE_CONTRACT_MISSING:upstream.content-before-hold (reachability demonstration owed)' });
    await info.attach('j16-oracle-controls', { contentType: 'application/json', body: JSON.stringify(records) });
  });
  test('browser selection: the CONSUMER-core --browser argv selects the engine; nothing overrides it', async ({ browserName }, info) => {
    // test.use() outranks the project use --browser sets: the file must name no engine.
    expect('browserName' in consoleOptions, 'consoleOptions names no engine').toBe(false);
    // Runtime witness for THIS invocation: the fixture engine is the CLI/project selection.
    expect(browserName, 'browserName fixture equals the selected engine').toBe(info.project.use.browserName ?? 'chromium');
    for (const [engine, argv] of Object.entries(CONSUMER_CORE_ARGV)) {
      const flag = (name: string) => argv.filter(a => a.startsWith(`--${name}=`)).map(a => a.slice(name.length + 3));
      expect(flag('browser'), `${engine} argv selects exactly one engine`).toEqual([engine]);
      expect(flag('workers'), `${engine} argv is serial`).toEqual(['1']);
      const [grep] = flag('grep');
      const selects = new RegExp(grep);
      expect(selects.test(cellTitle('A', 'positive')), `${engine} grep selects its cell`).toBe(true);
      for (const [tenant, arm] of [['B', 'positive'], ['A', 'negative'], ['A', 'restored']] as const) {
        expect(selects.test(cellTitle(tenant, arm)), `${engine} grep selects no other cell`).toBe(false);
      }
      // E2E_CONSOLE_BROWSER unset: the effective engine is the requested one and the gate admits it.
      expect(blockedReason(() => browserGate(engine, engine, null)), `${engine} admitted`).toBeNull();
      // A disagreeing retired selector is refused, never applied.
      const other = engine === 'firefox' ? 'chromium' : 'firefox';
      expect(blockedReason(() => browserGate(engine, engine, other))).toBe(`BROWSER_SELECTION_CONFLICT:${other}->${engine}`);
      // Engine-specific H11 strict settings follow the selected engine.
      const launch = consoleLaunchOptions(engine);
      if (engine === 'chromium') expect(launch.args ?? []).toEqual(expect.arrayContaining(chromiumStrictArgs()));
      else expect(launch.firefoxUserPrefs ?? {}).toMatchObject({ ...FIREFOX_STRICT_PREFS });
    }
    expect(consoleLaunchOptions('webkit')).toEqual({});
  });
  test('L1 orchestration: a refused C03/admission prerequisite stops the cell before any mutation', async ({}, info) => {
    // Drives CoreCell's REAL l1 over recording doubles: the adapter, walk and seam
    // resolver log every operation and throw OrchestrationReached at the first one the
    // prerequisites must precede (provider binding, mock stream, browser context,
    // agent/run). Only the admission proof inputs read is answered. Nothing is provisioned.
    const records: ConsoleOracleRecord[] = [];
    const dir = info.outputPath('l1-orchestration-copies');
    mkdirSync(dir, { recursive: true });
    // Three byte-identical route copies whose digest the control LM-708 pins in the
    // producer's own contract rows (public-ingress.ts:33,47-51).
    const bytes = '{"routes":[]}\n', digest = `sha256:${sha256(bytes)}`;
    const manifest = { digest: LM708, contracts: { routeManifestEdge: digest, routeManifestEE: digest, routeManifestConsole: digest } };
    const app: CoreApplicability = { branch: 'capability', lm708Digest: LM708, manifest, stack: {}, tuples: {} };
    const copies = (label: string, drift: boolean) => {
      const out = Object.fromEntries(ROUTE_COPIES.map(name => {
        const file = path.join(dir, `${label}-${name}.json`);
        writeFileSync(file, bytes);
        return [name, file];
      }));
      if (drift) appendFileSync(out.ee, '\n'); // one deployed copy's bytes differ; its JSON is unchanged
      return out;
    };
    const tenant = (label: Tenant, s: Scope): CoreTenant => ({ label, organizationId: s.organizationId,
      workspaceId: s.workspaceId, projectId: s.projectId, accountId: s.accountId, tokens: { access: `control-${label}` } });
    type Compare = (paths: Record<string, string>, m: unknown) => unknown;
    /** Ordering stimulus only, never producer evidence: refuses exactly when told the copy drifted. */
    const stimulus = (drift: boolean): Compare => () => {
      if (drift) throw new Error('control stimulus: one deployed route copy differs');
      return { matched: true, digests: { edge: digest, ee: digest, console: digest }, lm708Digest: LM708 };
    };
    const PRE_C03 = ['adapter.admissionProofInputs', 'seam:ingress', 'ingress.compareRouteManifests'];
    const PRE_MUTATION = [...PRE_C03, 'seam:admission', 'admission.verifyConsoleAdmission'];
    const NEVER_BEFORE_PREREQUISITES = ['adapter.bindProvider', 'adapter.liveUpstream', 'seam:upstream', 'walk.anonymous',
      'adapter.signIn', 'adapter.createAgent', 'adapter.startRun', 'adapter.startRunApi'];
    type Proof = 'accept' | 'refuse' | 'phase-dropped' | 'manifest-rebound';
    interface Variant { id: string; arm: Arm; drift: boolean; proof: Proof; outcome: string; ops: string[];
      cellRecords: ConsoleOracleRecord[] }
    const variants: Variant[] = [
      { id: 'route-copy-drift', arm: 'positive', drift: true, proof: 'accept',
        outcome: 'refused:C03:route-manifests', ops: PRE_C03,
        cellRecords: [{ id: 'C03', outcome: 'FAIL', kind: 'route-manifests' }] },
      { id: 'admission-proof-refused', arm: 'positive', drift: false, proof: 'refuse',
        outcome: 'refused:L1.01:admission-round-trip', ops: PRE_MUTATION,
        cellRecords: [{ id: 'L1.01', outcome: 'FAIL', kind: 'admission-round-trip' }] },
      { id: 'admission-phase-dropped', arm: 'positive', drift: false, proof: 'phase-dropped',
        outcome: 'refused:L1.01:admission-round-trip', ops: PRE_MUTATION,
        cellRecords: [{ id: 'L1.01', outcome: 'FAIL', kind: 'admission-round-trip' }] },
      { id: 'proof-manifest-lm708-rebound', arm: 'positive', drift: false, proof: 'manifest-rebound',
        outcome: 'refused:C03:route-manifest-lm708', ops: PRE_MUTATION,
        cellRecords: [{ id: 'C03', outcome: 'FAIL', kind: 'route-manifest-lm708' }] },
      // Restored: the SAME recorder sees the first mutation once both prerequisites pass,
      // so the refusals above stopping short of it are not vacuous.
      { id: 'restored', arm: 'positive', drift: false, proof: 'accept',
        outcome: 'reached:adapter.bindProvider', ops: [...PRE_MUTATION, 'adapter.bindProvider'], cellRecords: [] },
      // The provider-free arms refuse at the repeated stream gate before any operation.
      { id: 'rehearsal-arm-gate', arm: 'negative', drift: true, proof: 'accept',
        outcome: 'blocked:FIXTURE_CONTRACT_MISSING:upstream.content-before-hold', ops: [], cellRecords: [] },
    ];
    const classify = (error: unknown): string => {
      if (error instanceof ConsoleOracleFailure) return `refused:${error.oracle}:${error.kind}`;
      if (error instanceof OrchestrationReached) return `reached:${error.op}`;
      if (error instanceof ConsoleBlocked) return `blocked:${error.reason}`;
      return `unexpected:${error instanceof Error ? error.name : typeof error}`;
    };
    const drive = async (v: Variant, source: 'ordering-stimulus' | 'producer', compare: Compare) => {
      const ops: string[] = [];
      const reached = (op: string): never => { ops.push(op); throw new OrchestrationReached(op); };
      const recorder = <T>(name: string, answers: Record<string, unknown>) => new Proxy({}, { get(_target, prop) {
        if (typeof prop === 'symbol' || prop === 'then') return undefined;
        if (prop in answers) return answers[prop];
        return reached(`${name}.${prop}`);
      } }) as unknown as T;
      const inputs: AdmissionProofInputs = { routeCopies: copies(`${source}-${v.id}`, v.drift), orm: null,
        hosts: { api: 'http://localhost:9', public: 'http://localhost:9' },
        embed: { path: '/console', contentType: 'text/html', allowed: null, denied: null } };
      const adapter = recorder<ConsoleCoreRunAdapter>('adapter', { kind: 'stage-b-product',
        admissionProofInputs: async () => { ops.push('adapter.admissionProofInputs'); return inputs; } });
      const seam: SeamResolver = async (name: SeamName) => {
        if (name === 'ingress') {
          ops.push('seam:ingress');
          return { compareRouteManifests: (paths: Record<string, string>, m: unknown) => {
            ops.push('ingress.compareRouteManifests');
            return compare(paths, m);
          } };
        }
        if (name === 'admission') {
          ops.push('seam:admission');
          return { verifyConsoleAdmission: async (input: { routeCopies: Record<string, string>; manifest: unknown }) => {
            ops.push('admission.verifyConsoleAdmission');
            // An accepting proof carries the comparison's own wrapper (console-round-trip.ts:30,87).
            if (v.proof === 'refuse') throw new Error('control stimulus: admission proof refused');
            const wrapper = compare(input.routeCopies, input.manifest) as Record<string, unknown>;
            return { phases: v.proof === 'phase-dropped' ? ADMISSION_PHASES.slice(1) : [...ADMISSION_PHASES],
              manifests: v.proof === 'manifest-rebound' ? { ...wrapper, lm708Digest: `sha256:${'8'.repeat(64)}` } : wrapper };
          } };
        }
        return reached(`seam:${name}`);
      };
      const actor = (s: Scope) => ({ organizationId: s.organizationId, workspaceId: s.workspaceId, tokens: { access: 'control' } });
      const fixtures: Fixtures = { scopeActors: { ownerA: actor(SCOPE_A), ownerB: actor(SCOPE_B) } as unknown as ScopeActors,
        walk: recorder<Walk>('walk', {}) };
      const bound: Bound = { adapter, owner: tenant('A', SCOPE_A), foreign: tenant('B', SCOPE_B), app,
        stageA: { invocationId: coreInvocationId('firefox', 'A', v.arm), bindingDigest: 'control-unbound' },
        grant: { path: path.join(dir, 'control-grant.json'), digest: GRANT } };
      const cellRecords: ConsoleOracleRecord[] = [];
      let observed = 'completed';
      await test.step(`l1-orchestration:${source}:${v.id}`, async () => {
        try { await CoreCell.orchestrateL1(v.arm, bound, seam, fixtures, info, cellRecords); } catch (error) {
          observed = classify(error);
        }
        const ok = observed === v.outcome && JSON.stringify(ops) === JSON.stringify(v.ops) &&
          JSON.stringify(cellRecords) === JSON.stringify(v.cellRecords);
        records.push({ id: 'L1-orchestration', fault: v.id, outcome: ok ? 'KILLED' : 'NOT_KILLED', kind: `${source}:${observed}` });
        expect(observed, `${source}:${v.id} outcome`).toBe(v.outcome);
        expect(ops, `${source}:${v.id} operations reached`).toEqual(v.ops);
        expect(cellRecords, `${source}:${v.id} cell records`).toEqual(v.cellRecords);
        if (!v.outcome.startsWith('reached:')) {
          for (const op of NEVER_BEFORE_PREREQUISITES) expect(ops, `${source}:${v.id} never reaches ${op}`).not.toContain(op);
        }
      });
    };
    for (const v of variants) await drive(v, 'ordering-stimulus', stimulus(v.drift));
    // The producer half: the REAL compareRouteManifests rejects the drifted copy (and
    // accepts the identical ones) inside the same orchestration. BLOCKED until composed.
    let ingress: Awaited<ReturnType<SeamResolver>> | null = null;
    try { ingress = await resolveSeam('ingress'); } catch (error) {
      const reason = error instanceof ConsoleBlocked ? error.reason : 'SEAM_INCOMPATIBLE:ingress';
      info.annotations.push({ type: 'journey-status', description: `BLOCKED:${reason} (L1 orchestration real-producer half)` });
    }
    if (ingress) {
      const real = ingress;
      const producer: Compare = (paths, m) => real.compareRouteManifests(paths, m);
      for (const v of variants.filter(x => x.id === 'route-copy-drift' || x.id === 'restored')) await drive(v, 'producer', producer);
    }
    await info.attach('j16-oracle-controls', { contentType: 'application/json', body: JSON.stringify(records) });
  });
});
