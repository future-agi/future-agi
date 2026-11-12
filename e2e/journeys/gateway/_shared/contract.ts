import { test, type TestInfo } from '@playwright/test';
import { requirePacket, requireRow, type PacketId, type AssertionRow } from './packet-rows';

export type Phase = 'baseline' | 'negative' | 'red' | 'restored';
export interface Control {
  id: string;
  // A red must reach this row and fail an assertion there, not fail setup.
  expectedFailureRow?: string;
}

const PHASES: readonly string[] = ['baseline', 'negative', 'red', 'restored'];
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;
function token(value: string): void {
  if (!TOKEN.test(value)) throw new Error('Expected a bounded synthetic identifier');
}

/** Proposed additive selection contract, NOT a runnable W20 parameter.
 * Provenance: kit ship/w20/rehearsal.sh argv[5] is a Playwright --grep;
 * ship/walk_contract.py currently accepts only Wdd/HRddd, not these journey IDs.
 * The runner owner must admit journey discovery/selection first (FOUNDATION F01).
 */
export function selection(packet: PacketId) {
  requirePacket(packet);
  return {
    directory: `journeys/gateway/${packet}`,
    tag: `@journey-${packet}`,
    // Space/start boundary works with Playwright's project/file/suite title path.
    grep: `(?:^| )GW-JOURNEY-${packet}\\.`,
  };
}

export function journeyTitle(packet: PacketId, caseId: string, phase: Phase): string {
  requirePacket(packet);
  token(caseId);
  if (!PHASES.includes(phase)) throw new Error('Unknown journey phase');
  return `GW-JOURNEY-${packet}.${caseId} [${phase}]`;
}

/** One static annotation per assertion row; declarations are not execution receipts. */
export function journeyAnnotations<P extends PacketId>(
  packet: P, rows: readonly AssertionRow<P>[], phase: Phase, control?: Control,
): { type: string; description: string }[] {
  requirePacket(packet);
  if (!PHASES.includes(phase)) throw new Error('Unknown journey phase');
  if (rows.length === 0 || new Set(rows).size !== rows.length) {
    throw new Error('A journey test needs a nonempty unique assertion inventory');
  }
  for (const row of rows) requireRow(packet, row);
  if (phase !== 'baseline' && !control) throw new Error('Control phase requires a control ID');
  if (control) {
    token(control.id);
    if (control.expectedFailureRow !== undefined) {
      requireRow(packet, control.expectedFailureRow);
      if (!(rows as readonly string[]).includes(control.expectedFailureRow)) {
        throw new Error('Expected failure row is outside this test inventory');
      }
    }
  }
  if (phase === 'red' && !control?.expectedFailureRow) {
    throw new Error('Red control requires its intended failing row');
  }
  if (phase !== 'red' && control?.expectedFailureRow !== undefined) {
    throw new Error('Only a red invocation may declare an expected failure');
  }
  return rows.map(row => ({
    type: 'journey-assertion',
    description: JSON.stringify({ schema: 'gateway-journey-assertion/v1', packet, row, phase,
      ...(control ? { control } : {}) }),
  }));
}

/** Emit row-local start/terminal evidence without copying a response or error body.
 * The shared runner must join these events to static annotations and actual test
 * results. A pass attachment cannot overrule a failed/skipped/incomplete test.
 * Call once for the complete row's assertions, not once per individual expect.
 */
export async function assertionRow<P extends PacketId, T>(
  info: TestInfo, packet: P, row: AssertionRow<P>, phase: Phase, check: () => Promise<T>,
): Promise<T> {
  requireRow(packet, row);
  if (!PHASES.includes(phase)) throw new Error('Unknown journey phase');
  const declared = info.annotations.some(annotation => {
    if (annotation.type !== 'journey-assertion') return false;
    const a = JSON.parse(annotation.description ?? '{}');
    return a.schema === 'gateway-journey-assertion/v1' && a.packet === packet &&
      a.row === row && a.phase === phase;
  });
  if (!declared) throw new Error('Executed row has no matching journey annotation');
  const emit = (status: 'started' | 'passed' | 'failed') => info.attach('journey-row', {
    contentType: 'application/json',
    body: JSON.stringify({ schema: 'gateway-journey-row/v1', packet, row, phase, status }),
  });
  return test.step(`${packet}/${row}/${phase}`, async () => {
    await emit('started');
    let result: T;
    try { result = await check(); }
    catch (error) { await emit('failed'); throw error; }
    await emit('passed');
    return result;
  });
}

export interface SeededIds {
  organizationIds?: readonly string[];
  workspaceIds?: readonly string[];
  userIds?: readonly string[];
  requestIds?: readonly string[];
  runIds?: readonly string[];
  traceIds?: readonly string[];
  spanIds?: readonly string[];
  keyRecordIds?: readonly string[];
  credentialRecordIds?: readonly string[];
  approvalIds?: readonly string[];
  connectionIds?: readonly string[];
  holdIds?: readonly string[];
  settlementIds?: readonly string[];
}
const ID_FIELDS = new Set([
  'organizationIds', 'workspaceIds', 'userIds', 'requestIds', 'runIds', 'traceIds', 'spanIds',
  'keyRecordIds', 'credentialRecordIds', 'approvalIds', 'connectionIds', 'holdIds', 'settlementIds',
]);

/** Record minted non-secret IDs only. Never pass an actor, key, token or session object. */
export async function attachSeededIds(info: TestInfo, packet: PacketId, ids: SeededIds): Promise<void> {
  requirePacket(packet);
  const entries = Object.entries(ids);
  if (!entries.length) throw new Error('Seeded identity attachment must not be empty');
  for (const [field, values] of entries) {
    if (!ID_FIELDS.has(field) || !Array.isArray(values) || !values.length) {
      throw new Error('Invalid seeded identity field');
    }
    for (const id of values) {
      if (typeof id !== 'string') throw new Error('Seeded IDs must be strings');
      token(id);
    }
    if (new Set(values).size !== values.length) throw new Error('Duplicate seeded ID');
  }
  await info.attach('seeded-ids', { contentType: 'application/json',
    body: JSON.stringify({ schema: 'gateway-journey-seeds/v1', packet, ids }) });
}
