import { test, type TestInfo } from '@playwright/test';
import { requireRow, type AssertionRow } from '../_shared/packet-rows';

type Row = AssertionRow<'J15'>;

/** Partial checks MUST NOT emit journey-assertion/journey-row coverage records.
 * Foundation contract.ts reserves those for the complete packet row. These
 * annotations carry the row ID for discovery without claiming its missing arms.
 * Source of the boundary: J15/PACKET.json assertion_mapping and common C03-C05.
 */
export function fragmentAnnotation(row: Row, component: string, oracleOnly = false) {
  requireRow('J15', row);
  return {
    type: 'journey-fragment',
    description: JSON.stringify({ schema: 'gateway-journey-fragment/v1', packet: 'J15',
      row, component, oracleOnly, completeRow: false, walkedStatus: 'NOT_WALKED' }),
  };
}

export async function fragmentStep<T>(
  info: TestInfo, row: Row, component: string, check: () => Promise<T>,
): Promise<T> {
  requireRow('J15', row);
  return test.step(`J15/${row}/${component} (partial; NOT_WALKED)`, async () => {
    await info.attach('journey-fragment', { contentType: 'application/json',
      body: JSON.stringify({ schema: 'gateway-journey-fragment/v1', packet: 'J15', row,
        component, completeRow: false, walkedStatus: 'NOT_WALKED', evidence: null }) });
    return check();
  });
}

/** An unavailable prerequisite is neither a skip nor an expected red. Throwing
 * preserves failure in Playwright; the disposition records NOT_WALKED explicitly.
 * A product assertion failure after prerequisites are present is NOT caught here.
 */
export async function notWalked(info: TestInfo, row: Row, owedId: string): Promise<never> {
  requireRow('J15', row);
  if (!/^J15-O[0-9]{2}$/.test(owedId)) throw new Error('Invalid J15 obligation ID');
  await info.attach('journey-not-walked', { contentType: 'application/json',
    body: JSON.stringify({ packet: 'J15', row, status: 'NOT_WALKED', evidence: null, owedId }) });
  throw new Error(`NOT_WALKED J15/${row}: ${owedId}`);
}
