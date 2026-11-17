import { randomUUID } from 'node:crypto';
import { test, expect, type TestInfo } from '@playwright/test';
import { attachSeededIds, journeyTitle, selection } from '../_shared/contract';
import { markerIssues, debitIssues } from './settlement-oracle';
import { identityIssues, disableIssues, type Topology } from './topology-oracle';
import { settlementFixture, topologyFixture } from './oracle-fixtures';

/** C04/C05 ONLY: no actors, browser, request/probe or live lifecycle fixtures.
 * Provenance: J27/L6/PACKET.json negative_and_restored_controls C04 and C05.
 * Full A01-A09 remain OWED in AUTHOR.json. In particular these tests cannot prove
 * a durable read, actual recovery/replay, a live identity or absence of egress.
 *
 * Do NOT use journeyAnnotations/assertionRow for this partial oracle-only work:
 * _shared/contract.ts reserves them for COMPLETE rows. Distinct control metadata
 * carries the packet row IDs without emitting misleading journey-row passes.
 * F01 must inventory these separately from live rows and join actual failures.
 * Red/restored selection is source-only until the shared runner admits journeys.
 */
const PACKET = 'J27-L6' as const;
type Phase = 'red' | 'restored';
type Row = 'A05' | 'A06' | 'A07' | 'A08';

function details(control: string, caseId: string, row: Row, phase: Phase) {
  return {
    tag: [selection(PACKET).tag, '@oracle-only'],
    annotation: [{ type: 'journey-oracle-control', description: JSON.stringify({
      schema: 'gateway-journey-oracle-control/v1', packet: PACKET, control, caseId, row, phase,
      scope: 'isolated-data-fixture', productAssertionSatisfied: false,
      ...(phase === 'red' ? { expectedFailureRow: row } : {}),
    }) }],
  };
}

async function record(info: TestInfo, control: string, row: Row, phase: Phase, reasons: string[]) {
  await info.attach('oracle-control-input', { contentType: 'application/json', body: JSON.stringify({
    schema: 'gateway-journey-oracle-control-input/v1', packet: PACKET, control, row, phase,
    scope: 'isolated-data-fixture', expectedReasons: reasons,
    productAccepted: false, productStatus: 'NOT_WALKED',
  }) });
}

const MONEY_CASES = [
  { caseId: 'C04.marker-after-recovery', row: 'A05', defect: 'marker-after',
    reasons: ['A05:marker-set:after-recovery'] },
  { caseId: 'C04.marker-reread', row: 'A05', defect: 'marker-reread',
    reasons: ['A05:marker-set:reread'] },
  { caseId: 'C04.duplicate-debit', row: 'A06', defect: 'duplicate-debit',
    reasons: ['A06:debit-identity-multiplicity:after-replay', 'A06:debit-total:after-replay',
      'A06:debit-identity-changed-after-replay'] },
] as const;

for (const scenario of MONEY_CASES) {
  for (const phase of ['red', 'restored'] as const) {
    test(journeyTitle(PACKET, scenario.caseId, phase),
      details('C04', scenario.caseId, scenario.row, phase), async ({}, testInfo) => {
        const fixture = settlementFixture(randomUUID());
        await attachSeededIds(testInfo, PACKET, fixture.ids);
        const original = structuredClone(fixture.facts);
        // Validate setup BEFORE expected-failure is enabled; malformed input,
        // unrelated assertion failures and attachment failures never qualify red.
        expect(markerIssues(fixture.expected, fixture.facts)).toEqual([]);
        expect(debitIssues(fixture.expected, fixture.facts)).toEqual([]);
        if (scenario.defect === 'marker-after') fixture.facts.markersAfterRecovery.splice(0, 1);
        else if (scenario.defect === 'marker-reread') fixture.facts.markersReread.splice(0, 1);
        else fixture.facts.debitsAfterReplay.push({ ...fixture.facts.debitsAfterReplay[0],
          entryId: `duplicate-${randomUUID()}` });

        const check = () => scenario.row === 'A05'
          ? markerIssues(fixture.expected, fixture.facts) : debitIssues(fixture.expected, fixture.facts);
        expect(check()).toEqual([...scenario.reasons]);
        // Removing a marker cannot be disguised as a debit failure, and vice versa.
        expect(scenario.row === 'A05' ? debitIssues(fixture.expected, fixture.facts)
          : markerIssues(fixture.expected, fixture.facts)).toEqual([]);
        if (phase === 'restored') {
          // Undo ONLY the isolated defect, never edit expected data or a checker.
          if (scenario.defect === 'marker-after') {
            fixture.facts.markersAfterRecovery.splice(0, 0, original.markersAfterRecovery[0]);
          } else if (scenario.defect === 'marker-reread') {
            fixture.facts.markersReread.splice(0, 0, original.markersReread[0]);
          } else fixture.facts.debitsAfterReplay.pop();
          expect(fixture.facts).toEqual(original);
        }
        await record(testInfo, 'C04', scenario.row, phase,
          phase === 'red' ? [...scenario.reasons] : []);
        if (phase === 'red') test.fail(true, `C04 isolated oracle must reject ${scenario.defect}`);
        // The same unchanged detector and empty-issues assertion in both phases.
        // A red fails here, not during fixture setup; an unexpected green fails
        // Playwright's expected-failure contract. No catch/skip/fixme/soft expect.
        await test.step(`${PACKET}/${scenario.row}/C04/${phase}/oracle-only`, async () => {
          expect(check()).toEqual([]);
        });
      });
  }
}

const TOPOLOGY_CASES = [
  { caseId: 'C05.worker-identity', row: 'A07', defect: 'worker', reasons: ['A07:role-identity-set'] },
  { caseId: 'C05.temporal-identity', row: 'A07', defect: 'temporal', reasons: ['A07:role-identity-set'] },
  { caseId: 'C05.disable-witness', row: 'A08', defect: 'disable', reasons: ['A08:disable-witness-set'] },
] as const;

for (const topology of ['standalone', 'distributed'] as const satisfies readonly Topology[]) {
  for (const scenario of TOPOLOGY_CASES) {
    for (const phase of ['red', 'restored'] as const) {
      const caseId = `${scenario.caseId}.${topology}`;
      test(journeyTitle(PACKET, caseId, phase), details('C05', caseId, scenario.row, phase),
        async ({}, testInfo) => {
          const nonce = randomUUID();
          await attachSeededIds(testInfo, PACKET, { runIds: [nonce] });
          const fixture = topologyFixture(nonce, topology);
          const original = structuredClone(fixture.facts);
          expect(identityIssues(fixture.expected, fixture.facts)).toEqual([]);
          expect(disableIssues(fixture.expected, fixture.facts)).toEqual([]);
          const index = scenario.defect === 'disable' ? 0
            : fixture.facts.roles.findIndex(r => r.kind === scenario.defect);
          expect(index).toBeGreaterThanOrEqual(0);
          if (scenario.defect === 'disable') fixture.facts.disableWitnesses.splice(index, 1);
          else fixture.facts.roles.splice(index, 1);
          const check = () => scenario.row === 'A07'
            ? identityIssues(fixture.expected, fixture.facts) : disableIssues(fixture.expected, fixture.facts);
          expect(check()).toEqual([...scenario.reasons]);
          expect(scenario.row === 'A07' ? disableIssues(fixture.expected, fixture.facts)
            : identityIssues(fixture.expected, fixture.facts)).toEqual([]);
          if (phase === 'restored') {
            if (scenario.defect === 'disable') {
              fixture.facts.disableWitnesses.splice(index, 0, original.disableWitnesses[index]);
            } else fixture.facts.roles.splice(index, 0, original.roles[index]);
            expect(fixture.facts).toEqual(original);
          }
          await record(testInfo, 'C05', scenario.row, phase,
            phase === 'red' ? [...scenario.reasons] : []);
          if (phase === 'red') test.fail(true, `C05 isolated oracle must reject missing ${scenario.defect}`);
          await test.step(`${PACKET}/${scenario.row}/C05/${topology}/${phase}/oracle-only`, async () => {
            expect(check()).toEqual([]);
          });
        });
    }
  }
}

// C01/C02/C03 need real registration/worker/commit-before-ack operations. C06
// must invoke the ACCEPTED BASE egress checker/native refusal controls. None is
// exposed as a reviewed callable interface here. AUTHOR.json records each owed
// red AND restored phase; no fake test, test.skip or substitute client is added.
