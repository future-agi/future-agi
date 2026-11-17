import { expect } from '@playwright/test';

/** Data-only oracle fixtures, NOT an accepted runtime receipt/wire schema.
 * Provenance: JOURNEYS-708/packets/J27/L4/PACKET.json, L4-A02/C06 and L4-A09/C07.
 * No endpoint, concrete /omega path, selector, lifecycle operation or collector
 * is invented here. An accepted producer-to-oracle binding is still owed.
 * Passing these checks cannot establish that a service restarted or EE is absent.
 */
export const oracleMarker = (code: string) => `[J27-L4 oracle ${code}]`;
function equal(actual: unknown, expected: unknown, code: string): void {
  expect(actual, oracleMarker(code)).toEqual(expected);
}
function distinctNonempty(ids: string[], code: string): void {
  equal(ids.length > 0 && ids.every(id => /^[A-Za-z0-9_.:-]{1,160}$/.test(id)) &&
    new Set(ids).size === ids.length, true, code);
}

export interface ProbeSample {
  id: string;
  // Relative monotonic milliseconds in this SYNTHETIC reporter fixture only.
  atMs: number;
  outcome: 'success' | 'refused' | 'disconnected';
}
export interface ObservedInterruption {
  lastSuccessMs: number;
  firstUnavailableMs: number;
  firstRestoredSuccessMs: number;
  // Sampled interval, NOT an inferred exact outage between unsampled instants.
  observedUnavailableMs: number;
}
export interface TopologyFixture {
  gatewayInstanceIds: string[];
  autoscalingEnabled: boolean;
}
export interface InterruptionFixture {
  plannedProbeIds: string[];
  probes: ProbeSample[];
  before: TopologyFixture;
  after: TopologyFixture;
  report: {
    uninterrupted: boolean;
    redundant: boolean;
    refusedProbeIds: string[];
    disconnectedProbeIds: string[];
    intervals: ObservedInterruption[];
  };
}

/** Exact multiplicity/order, not membership or a final healthy response alone.
 * Does NOT prove continuous real probing, admission closure, readiness, actual
 * process incarnation or no hidden standby. All of L4-A02 remains owed until
 * the real runner supplies and qualifies those witnesses.
 */
export function assertInterruptionReport(input: InterruptionFixture): void {
  distinctNonempty(input.plannedProbeIds, 'C06.planned-probes');
  distinctNonempty(input.probes.map(probe => probe.id), 'C06.probe-identities');
  equal(input.probes.map(probe => probe.id), input.plannedProbeIds, 'C06.probe-inventory');
  for (const [name, topology] of [['before', input.before], ['after', input.after]] as const) {
    distinctNonempty(topology.gatewayInstanceIds, `C06.${name}-identities`);
    equal(topology.gatewayInstanceIds.length, 1, `C06.${name}-single-replica`);
    equal(topology.autoscalingEnabled, false, `C06.${name}-autoscaling`);
  }
  equal(input.probes.length >= 3, true, 'C06.probe-count');
  equal(input.probes.every((probe, index) => Number.isSafeInteger(probe.atMs) &&
    probe.atMs >= 0 && (index === 0 || probe.atMs > input.probes[index - 1].atMs)),
  true, 'C06.probe-time-order');
  equal(input.probes.every(probe => ['success', 'refused', 'disconnected'].includes(probe.outcome)),
    true, 'C06.probe-outcomes');
  equal(input.probes[0].outcome, 'success', 'C06.baseline-success');
  equal(input.probes[input.probes.length - 1].outcome, 'success', 'C06.restored-success');
  equal(input.probes.some(probe => probe.outcome !== 'success'), true, 'C06.actual-interruption');

  const intervals: ObservedInterruption[] = [];
  let lastSuccess = input.probes[0].atMs;
  let firstUnavailable: number | undefined;
  for (const probe of input.probes) {
    if (probe.outcome !== 'success') {
      if (firstUnavailable === undefined) firstUnavailable = probe.atMs;
    } else {
      if (firstUnavailable !== undefined) {
        intervals.push({ lastSuccessMs: lastSuccess, firstUnavailableMs: firstUnavailable,
          firstRestoredSuccessMs: probe.atMs, observedUnavailableMs: probe.atMs - firstUnavailable });
        firstUnavailable = undefined;
      }
      lastSuccess = probe.atMs;
    }
  }
  equal(input.report.uninterrupted, false, 'C06.interruption-disclosure');
  equal(input.report.redundant, false, 'C06.redundancy-disclosure');
  equal(input.report.refusedProbeIds,
    input.probes.filter(probe => probe.outcome === 'refused').map(probe => probe.id),
    'C06.refusals-exact');
  equal(input.report.disconnectedProbeIds,
    input.probes.filter(probe => probe.outcome === 'disconnected').map(probe => probe.id),
    'C06.disconnections-exact');
  equal(input.report.intervals, intervals, 'C06.intervals-exact');
}

// These are normalized synthetic fixture roles, not product role deserialization.
export type FixtureRole = 'gateway' | 'ee-daemon' | 'edge';
export interface AbsenceBinding {
  tupleId: string;
  compositionId: string;
  // Opaque fixture slots only. No guessed concrete ingress routes are contacted.
  requiredProbeIds: string[];
}
export interface AbsenceFixture {
  binding: AbsenceBinding;
  declaration: {
    tupleId: string;
    compositionId: string;
    branch: 'attested-absent' | 'served' | 'unknown';
    served: FixtureRole[];
    admitted: FixtureRole[];
    parts: FixtureRole[];
  };
  census: {
    tupleId: string;
    compositionId: string;
    complete: boolean;
    processes: { id: string; role: FixtureRole }[];
    containers: { id: string; role: FixtureRole }[];
  } | null;
  denials: { probeId: string; tupleId: string; compositionId: string; status: number }[];
}

function onlyGateway(roles: FixtureRole[], code: string): void {
  // The fixture's explicitly pinned declaration/census is gateway-only. This is
  // NOT a claim that every real absent topology contains only these services.
  equal(roles, ['gateway'], code);
}

/** C07 absence-verifier detector only. Missing census rejects as UNKNOWN.
 * There is no fallback from a hidden UI, unknown branch or empty route set.
 * Real tuple/manifest/census schemas and concrete ingress probes are still owed;
 * these fixtures cannot be loaded as native receipts or certify L4-A09.
 */
export function assertAbsentFixture(input: AbsenceFixture): void {
  distinctNonempty([input.binding.tupleId], 'C07.tuple-required');
  distinctNonempty([input.binding.compositionId], 'C07.composition-required');
  distinctNonempty(input.binding.requiredProbeIds, 'C07.probes-required');
  equal(input.declaration.tupleId, input.binding.tupleId, 'C07.declaration-tuple');
  equal(input.declaration.compositionId, input.binding.compositionId, 'C07.declaration-composition');
  equal(input.declaration.branch, 'attested-absent', 'C07.branch');
  onlyGateway(input.declaration.served, 'C07.served-roles');
  onlyGateway(input.declaration.admitted, 'C07.admitted-roles');
  onlyGateway(input.declaration.parts, 'C07.parts');
  equal(input.census !== null, true, 'C07.UNKNOWN-missing-census');
  // The preceding assertion throws for null; this is a type refinement only.
  if (input.census === null) throw new Error('Unreachable missing census');
  equal(input.census.complete, true, 'C07.UNKNOWN-incomplete-census');
  equal(input.census.tupleId, input.binding.tupleId, 'C07.census-tuple');
  equal(input.census.compositionId, input.binding.compositionId, 'C07.census-composition');
  distinctNonempty(input.census.processes.map(process => process.id), 'C07.process-identities');
  distinctNonempty(input.census.containers.map(container => container.id), 'C07.container-identities');
  onlyGateway(input.census.processes.map(process => process.role), 'C07.process-roles');
  onlyGateway(input.census.containers.map(container => container.role), 'C07.container-roles');
  // Sort copies, never deduplicate or modify the supplied fixture. Extra, missing,
  // duplicate and wrong-tuple denials all fail even when every status is 404.
  equal(input.denials.map(denial => denial.probeId).sort(),
    [...input.binding.requiredProbeIds].sort(), 'C07.route-inventory');
  equal(input.denials.map(denial => denial.tupleId),
    input.denials.map(() => input.binding.tupleId), 'C07.denial-tuple');
  equal(input.denials.map(denial => denial.compositionId),
    input.denials.map(() => input.binding.compositionId), 'C07.denial-composition');
  equal(input.denials.map(denial => denial.status), input.denials.map(() => 404), 'C07.denials-404');
}
