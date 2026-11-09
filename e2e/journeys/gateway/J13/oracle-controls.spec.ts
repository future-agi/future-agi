import { randomUUID } from 'node:crypto';
import { test } from '@playwright/test';
import { attachSeededIds, journeyTitle } from '../_shared/contract';
import { ORACLE_CASES, type OracleSeeds } from './oracle-cases';

/** This file is explicitly NOT W03/W04 product execution.
 * No actor, page, browser, request, provider, credentials, probe or runner hook is
 * requested. It contains detector fallibility components for five control
 * families. Four runtime control families and ALL complete assertion rows stay
 * owed in AUTHOR.json. These components run only through the sole accepted
 * shared walk runtime after review; no local execution is permitted here.
 *
 * Deliberately do NOT use journeyAnnotations/assertionRow: those declare complete
 * product rows, whereas these data-only fragments discharge none. The custom
 * annotation and each attachment explicitly forbid product/row coverage credit.
 * Main must join actual W03 and W04 plus actual controls in both browser projects.
 */
for (const oracle of ORACLE_CASES) {
  for (const phase of ['baseline', 'red', 'restored'] as const) {
    const caseId = `oracle.${oracle.id}`;
    test(journeyTitle('J13', caseId, phase), {
      tag: ['@journey-J13', '@oracle-only'],
      annotation: [{
        type: 'journey-oracle-control',
        description: JSON.stringify({
          schema: 'j13-oracle-component/v1', packet: 'J13', row: oracle.row,
          control: oracle.family, caseId, phase, scope: 'synthetic-detector-only',
          completeAssertionRow: false, productAccepted: false, walkedStatus: 'NOT_WALKED',
          expectedCheck: `J13/oracle/${oracle.id}`,
          ...(phase === 'red' ? { expectedFailureRow: oracle.row } : {}),
        }),
      }],
    }, async ({}, testInfo) => {
      // Fresh synthetic identity per invocation; never a minted product key.
      const seeds: OracleSeeds = {
        keyRecordId: randomUUID(), otherKeyRecordId: randomUUID(),
        scopeId: `run:${randomUUID()}`, otherScopeId: `run:${randomUUID()}`,
      };
      await attachSeededIds(testInfo, 'J13', {
        keyRecordIds: [seeds.keyRecordId, seeds.otherKeyRecordId],
        runIds: [seeds.scopeId, seeds.otherScopeId],
      });
      await testInfo.attach('oracle-component', {
        contentType: 'application/json',
        body: JSON.stringify({
          schema: 'j13-oracle-component/v1', packet: 'J13', row: oracle.row,
          control: oracle.family, caseId, phase, status: 'started',
          identityKind: 'synthetic-fixture-only', observationKind: 'synthetic-detector-only',
          perturbation: oracle.perturbation, completeAssertionRow: false,
          productAccepted: false, walkedStatus: 'NOT_WALKED',
        }),
      });
      if (phase === 'red') {
        // Setup and a healthy check MUST succeed before expected-failure mode.
        // The only subsequent operation is this case's unchanged assertion with
        // one perturbed observation. A setup failure is never an expected kill.
        await test.step(`${oracle.row}/oracle/${oracle.id}/healthy-precheck`, async () => {
          oracle.check(false, seeds);
        });
        test.fail(true, `Synthetic control must fail J13/oracle/${oracle.id}; product remains NOT_WALKED`);
      }
      await test.step(`${oracle.row}/oracle/${oracle.id}/${phase}`, async () => {
        oracle.check(phase === 'red', seeds);
      });
      // Red normally throws above. Unexpected green remains Playwright's
      // unexpected-pass failure, not a successful control. No catch/soft/skip.
      if (phase !== 'red') {
        await testInfo.attach('oracle-component', {
          contentType: 'application/json',
          body: JSON.stringify({
            schema: 'j13-oracle-component/v1', packet: 'J13', row: oracle.row,
            control: oracle.family, caseId, phase, status: 'detector-check-passed',
            completeAssertionRow: false, productAccepted: false, walkedStatus: 'NOT_WALKED',
          }),
        });
      }
    });
  }
}
