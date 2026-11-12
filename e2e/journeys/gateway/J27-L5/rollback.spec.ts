import { test, type TestInfo } from '@playwright/test';
import { journeyTitle, selection, type Phase } from '../_shared/contract';
import { PACKET, debts, owedRows, type Debt, type Row } from './obligations';
import { controlCases } from './control-cases';

// SOURCE-ONLY ADMISSION GUARDS, NOT EXECUTABLE ROLLBACK COVERAGE.
// Each complete assertion is mapped to an exact OWED entry. Missing runtime and
// product hooks are not emulated with local Docker, synthetic ledger rows, new
// clients or success-shaped fixtures. Replacing a guard with real steps requires
// source review and accepted fixture/runner binding, not an environment toggle.
//
// Use the base test without actor/page/probe: these guards have no product act.
// Importing lib/fixtures and requesting actor would auto-read organization keys
// before detecting the missing person-owned credential seam (FOUNDATION F09).
// We neither construct a replacement identity nor alter the harness.
//
// The candidate default config does not discover journeys and W20 cannot select
// J27. F01 remains owed: this file is NOT a CLI/discovery workaround. Once the
// runtime owner selects it, every unresolved entry hard-fails with NOT_WALKED.
// It cannot be an expected red, zero-test pass or partial behavioral acceptance.

test.describe.configure({ retries: 0 });

function owedAnnotations(rows: readonly Row[], phase: Phase, needs: readonly Debt[], control?: string) {
  return rows.map(row => ({
    // Deliberately NOT journey-assertion: no complete behavioral row is checked.
    type: 'journey-owed',
    description: JSON.stringify({ schema: 'gateway-journey-owed/v1', packet: PACKET,
      row, phase, control: control ?? null, status: 'NOT_WALKED',
      owed: needs.map(id => ({ id, kind: debts[id].kind, reason: debts[id].reason })) }),
  }));
}

async function notWalkedStep(
  info: TestInfo, caseId: string, phase: Phase, rows: readonly Row[], needs: readonly Debt[],
  requiredOutcome: string, control?: string,
): Promise<never> {
  return test.step(`${PACKET}/${caseId}/${phase}: NOT_WALKED`, async () => {
    // Source-only obligation attachment, not a packet RESULT/receipt or runtime
    // terminal. F01 must distinguish this setup refusal from WALKED_FAIL and from
    // an actual assertion-sensitive red. No journey-row 'passed' is emitted.
    await info.attach('journey-not-walked', {
      contentType: 'application/json',
      body: JSON.stringify({
        schema: 'gateway-journey-not-walked/v1', packet: PACKET, caseId, phase,
        rows, control: control ?? null, status: 'NOT_WALKED',
        productAccepted: false, behavioralAssertionExecuted: false,
        intendedDetectorFailureObserved: false, seededIds: [],
        reason: 'Required reviewed fixture/runner/wire bindings do not exist in this authoring input.',
        owed: needs.map(id => ({ id, ...debts[id] })), requiredOutcome,
        releaseHoldRemains: true, ownerNotification: 'OWED_TO_MAIN',
      }),
    });
    // No skip/fixme/soft assert/catch/test.fail. A red missing its fixture must NOT
    // become an expected failure, and restored cases must not pass by construction.
    throw new Error(`NOT_WALKED ${PACKET}/${caseId}: unresolved ${needs.join(',')}`);
  });
}

for (const row of owedRows) {
  test(journeyTitle(PACKET, row.caseId, 'baseline'), {
    tag: [selection(PACKET).tag],
    annotation: owedAnnotations([row.row], 'baseline', row.debts),
  }, async ({}, info) => {
    await notWalkedStep(info, row.caseId, 'baseline', [row.row], row.debts, row.step);
  });
}

// Controls have their OWN tests and output identities. C03's three defects never
// share a fixture. C05 current/previous cases are distinct. Grouped protocol
// denials/inherited runtime controls still owe every enumerated child witness;
// a future binder must not turn their prose into a single count or healthy check.
for (const control of controlCases) {
  test(journeyTitle(PACKET, control.caseId, control.phase), {
    tag: [selection(PACKET).tag],
    annotation: owedAnnotations(control.rows, control.phase, control.debts, control.control),
  }, async ({}, info) => {
    await notWalkedStep(info, control.caseId, control.phase, control.rows,
      control.debts, control.requiredOutcome, control.control);
  });
}
