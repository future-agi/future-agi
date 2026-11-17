import { randomUUID } from 'node:crypto';
import { test, type TestInfo } from '@playwright/test';
import { attachSeededIds, journeyTitle, selection } from '../_shared/contract';
import {
  assertAbsentFixture, assertInterruptionReport, oracleMarker,
  type AbsenceFixture, type InterruptionFixture,
} from './reporting-oracles';

/** Packet C06 expressly permits synthetic reporter fixtures; C07 expressly
 * permits isolated absence-verifier fixtures. These tests use no actor/browser,
 * request client, runtime action or state writer. They test detectors only.
 * Full L4-A02/L4-A09 coverage stays OWED (see obligations.ts and AUTHOR.json).
 * Do not apply journeyAnnotations/assertionRow to these partial-row checks:
 * their passed attachments would otherwise claim complete product assertions.
 * The accepted runtime must separately inventory these oracle-only tests and
 * their raw intended failures; they do not supply live restored-green evidence.
 */
const PACKET = 'J27-L4' as const;
const SPEC = 'journeys/gateway/J27-L4/reporting-controls.spec.ts';

type Variant<T> = { id: string; expectedCode: string; mutate: (input: T) => void };
type Fixture<T> = { input: T; probeIds: string[] };

function interruptionFixture(seed: string): Fixture<InterruptionFixture> {
  const probeIds = Array.from({ length: 8 }, (_, index) => `${seed}-p${index}`);
  // Independent literal expected report: do not generate the expected intervals
  // with assertInterruptionReport or share its calculation with this fixture.
  // Two interruptions ensure an implementation cannot silently keep only one.
  return { probeIds, input: {
    plannedProbeIds: [...probeIds],
    probes: [
      { id: probeIds[0], atMs: 0, outcome: 'success' },
      { id: probeIds[1], atMs: 10, outcome: 'success' },
      { id: probeIds[2], atMs: 20, outcome: 'refused' },
      { id: probeIds[3], atMs: 30, outcome: 'disconnected' },
      { id: probeIds[4], atMs: 40, outcome: 'refused' },
      { id: probeIds[5], atMs: 50, outcome: 'success' },
      { id: probeIds[6], atMs: 60, outcome: 'refused' },
      { id: probeIds[7], atMs: 70, outcome: 'success' },
    ],
    before: { gatewayInstanceIds: [`${seed}-gateway`], autoscalingEnabled: false },
    after: { gatewayInstanceIds: [`${seed}-gateway`], autoscalingEnabled: false },
    report: {
      uninterrupted: false, redundant: false,
      refusedProbeIds: [probeIds[2], probeIds[4], probeIds[6]],
      disconnectedProbeIds: [probeIds[3]],
      intervals: [
        { lastSuccessMs: 10, firstUnavailableMs: 20, firstRestoredSuccessMs: 50, observedUnavailableMs: 30 },
        { lastSuccessMs: 50, firstUnavailableMs: 60, firstRestoredSuccessMs: 70, observedUnavailableMs: 10 },
      ],
    },
  } };
}

function absenceFixture(seed: string): Fixture<AbsenceFixture> {
  const tupleId = `${seed}-tuple`;
  const compositionId = `${seed}-composition`;
  // Opaque oracle slots, NOT invented product routes or HTTP request bodies.
  // Actual local /omega probe paths must come from the admitted manifest later.
  const probeIds = [`${seed}-probe-a`, `${seed}-probe-b`];
  return { probeIds, input: {
    binding: { tupleId, compositionId, requiredProbeIds: [...probeIds] },
    declaration: { tupleId, compositionId, branch: 'attested-absent',
      served: ['gateway'], admitted: ['gateway'], parts: ['gateway'] },
    census: { tupleId, compositionId, complete: true,
      processes: [{ id: `${seed}-gateway-process`, role: 'gateway' }],
      containers: [{ id: `${seed}-gateway-container`, role: 'gateway' }] },
    denials: probeIds.map(probeId => ({ probeId, tupleId, compositionId, status: 404 })),
  } };
}

/** Fail closed if Playwright no longer exposes this assertion discriminator.
 * We do not accept a setup/transport/timeout failure merely because this is a
 * red. Only the exact named toEqual failure enables expected-failure handling.
 * Runner admission must ALSO match raw Playwright failure + terminal + inventory.
 */
function intendedAssertion(error: unknown, code: string): boolean {
  if (!(error instanceof Error) || !error.message.includes(oracleMarker(code))) return false;
  const result = (error as Error & { matcherResult?: { name?: string; pass?: boolean } }).matcherResult;
  return result?.name === 'toEqual' && result.pass === false;
}

function registerOracleControls<T>(
  control: 'C06' | 'C07', row: 'L4-A02' | 'L4-A09', make: (seed: string) => Fixture<T>,
  check: (input: T) => void, variants: Variant<T>[],
): void {
  for (const variant of variants) {
    for (const phase of ['red', 'restored'] as const) {
      const caseId = `${control}.${variant.id}.oracle`;
      const metadata = { schema: 'gateway-journey-oracle-control/v1', packet: PACKET, row,
        control, variant: variant.id, phase, spec: SPEC, coverage: 'oracle-only',
        fullRowCovered: false, productAccepted: false, walkStatus: 'NOT_WALKED',
        expectedFailureCode: phase === 'red' ? variant.expectedCode : null };
      test(journeyTitle(PACKET, caseId, phase), {
        tag: [selection(PACKET).tag, '@oracle-only'],
        annotation: [{ type: 'journey-oracle-control', description: JSON.stringify(metadata) }],
      }, async ({}, info: TestInfo) => {
        const fixture = make(`oracle-${randomUUID()}`);
        if (phase === 'red') variant.mutate(fixture.input);
        // Minted fixture slots only: no actual request, org or run was created.
        await attachSeededIds(info, PACKET, { requestIds: fixture.probeIds });
        await info.attach('l4-oracle-fixture', { contentType: 'application/json',
          body: JSON.stringify({ ...metadata, syntheticOnly: true,
            seededIdentityMeaning: 'oracle probe slots; not service request/run records', input: fixture.input }) });
        const emit = (status: string) => info.attach('l4-oracle-control', {
          contentType: 'application/json', body: JSON.stringify({ ...metadata, status }),
        });
        await test.step(`${PACKET}/${row}/${control}/${variant.id}/${phase}/oracle-only`, async () => {
          await emit('started');
          try {
            check(fixture.input);
          } catch (error) {
            if (phase === 'red' && intendedAssertion(error, variant.expectedCode)) {
              // Emit BEFORE test.fail so an attachment error is not accepted red.
              await emit('intended-assertion-failed');
              test.fail(true, `Oracle-only ${control}: ${variant.expectedCode}`);
              throw error; // preserve the actual nonzero assertion verdict
            }
            await emit('unqualified-failure');
            throw error;
          }
          if (phase === 'red') {
            await emit('unexpected-pass');
            throw new Error('Oracle defect survived: not a qualified red control');
          }
          await emit('oracle-restored-passed');
        });
      });
    }
  }
}

registerOracleControls('C06', 'L4-A02', interruptionFixture, assertInterruptionReport, [
  { id: 'false-no-interruption', expectedCode: 'C06.interruption-disclosure',
    mutate: input => { input.report.uninterrupted = true; } },
  { id: 'two-replicas-before', expectedCode: 'C06.before-single-replica',
    mutate: input => { input.before.gatewayInstanceIds.push(`${input.before.gatewayInstanceIds[0]}-standby`); } },
  { id: 'two-replicas-after', expectedCode: 'C06.after-single-replica',
    mutate: input => { input.after.gatewayInstanceIds.push(`${input.after.gatewayInstanceIds[0]}-standby`); } },
  { id: 'dropped-refusal', expectedCode: 'C06.refusals-exact',
    mutate: input => { input.report.refusedProbeIds.pop(); } },
  { id: 'wrong-interval', expectedCode: 'C06.intervals-exact',
    mutate: input => { input.report.intervals[0].observedUnavailableMs = 0; } },
  { id: 'redundancy-claim', expectedCode: 'C06.redundancy-disclosure',
    mutate: input => { input.report.redundant = true; } },
]);

registerOracleControls('C07', 'L4-A09', absenceFixture, assertAbsentFixture, [
  { id: 'ee-process', expectedCode: 'C07.process-roles', mutate: input => {
    input.census!.processes.push({ id: `${input.binding.tupleId}-ee-process`, role: 'ee-daemon' });
  } },
  { id: 'edge-container', expectedCode: 'C07.container-roles', mutate: input => {
    input.census!.containers.push({ id: `${input.binding.tupleId}-edge-container`, role: 'edge' });
  } },
  { id: 'non-404', expectedCode: 'C07.denials-404',
    mutate: input => { input.denials[0].status = 200; } },
  { id: 'missing-census', expectedCode: 'C07.UNKNOWN-missing-census',
    mutate: input => { input.census = null; } },
  { id: 'incomplete-census', expectedCode: 'C07.UNKNOWN-incomplete-census',
    mutate: input => { input.census!.complete = false; } },
  { id: 'missing-denial', expectedCode: 'C07.route-inventory',
    mutate: input => { input.denials.pop(); } },
  { id: 'duplicate-denial', expectedCode: 'C07.route-inventory',
    mutate: input => { input.denials.push({ ...input.denials[0] }); } },
  { id: 'wrong-tuple', expectedCode: 'C07.denial-tuple',
    mutate: input => { input.denials[0].tupleId = `${input.binding.tupleId}-other`; } },
]);
