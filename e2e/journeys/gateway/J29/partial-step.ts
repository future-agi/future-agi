import { test, type TestInfo } from '@playwright/test';
import { requireRow, type AssertionRow } from '../_shared/packet-rows';
import type { Phase } from '../_shared/contract';

/** A clause is deliberately NOT journey-assertion/journey-row coverage.
 * All twelve complete J29 rows stay owed in AUTHOR.json. This wrapper preserves
 * the row ID for review without misrepresenting partial UI/state checks as a
 * product walk. Only the shared reporter may join complete row evidence later.
 */
export async function partialStep<T>(
  info: TestInfo, row: AssertionRow<'J29'>, clause: string, phase: Phase,
  check: () => Promise<T>,
): Promise<T> {
  requireRow('J29', row);
  if (!/^[a-z][a-z0-9-]{0,63}$/.test(clause)) throw new Error('Invalid clause ID');
  if (!['baseline', 'negative', 'red', 'restored'].includes(phase)) throw new Error('Invalid clause phase');
  const record = { schema: 'j29-partial-clause/v1', packet: 'J29', row, clause, phase,
    completeRow: false, disposition: 'NOT_WALKED' };
  info.annotations.push({ type: 'journey-partial-clause', description: JSON.stringify(record) });
  const emit = (clauseState: 'started' | 'checked' | 'failed') => info.attach('j29-partial-clause', {
    contentType: 'application/json', body: JSON.stringify({ ...record, clauseState }),
  });
  return test.step(`J29/${row}/${clause}/${phase}`, async () => {
    await emit('started');
    try {
      const result = await check();
      await emit('checked');
      return result;
    } catch (error) {
      await emit('failed');
      throw error;
    }
  });
}
