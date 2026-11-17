import { requireCohort, requireUuid } from '../../_shared/identifiers.ts';

export type Operation = 'arrival' | 'register' | 'authorize' | 'code' | 'refresh' | 'revoke' | 'initialize' | 'list' | 'tool' | 'transport';
export type Tool = '' | 'echo' | 'get_weather' | 'delete_file';
export type Outcome = 'received' | 'accepted' | 'refused';
export interface Counter {
  principalId: string | null; operation: Operation; tool: Tool; outcome: Outcome; count: number;
}

/** Fixture-owned value-free projection. Principle: principal comes from a
 * registered synthetic bearer or redeemed grant, never a caller's header claim.
 * Null records remain in the FULL census so unauthorized/unattributed calls do
 * not vanish. Snapshot has no raw bodies, header values, tokens or token hashes.
 * One instance per minted fixture cohort; no HTTP reset/control endpoint.
 */
export function createCounters(principalIds: readonly string[]) {
  const principals = new Set(requireCohort(principalIds).map(requireUuid));
  const rows = new Map<string, Counter>();
  return {
    acceptsPrincipal(id: string) { return principals.has(id); },
    record(principalId: string | null, operation: Operation, outcome: Outcome, tool: Tool = '') {
      if (principalId !== null && !principals.has(principalId)) throw new Error('Counter principal outside fixture cohort');
      const key = JSON.stringify([principalId, operation, tool, outcome]);
      const previous = rows.get(key);
      if (previous) previous.count++;
      else rows.set(key, { principalId, operation, tool, outcome, count: 1 });
    },
    snapshot(): Counter[] {
      return [...rows.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, row]) => ({ ...row }));
    },
  };
}
export type Counters = ReturnType<typeof createCounters>;

/** Fail-closed exact multiset delta including negative witnesses and nulls.
 * No filtering to expected principals/tools. Resets/decreasing observations fail.
 */
export function counterDelta(before: readonly Counter[], after: readonly Counter[]): Counter[] {
  const index = (rows: readonly Counter[]) => {
    const out = new Map<string, Counter>();
    for (const row of rows) {
      const key = JSON.stringify([row.principalId, row.operation, row.tool, row.outcome]);
      if (out.has(key) || !Number.isSafeInteger(row.count) || row.count < 1) throw new Error('Invalid counter census');
      out.set(key, row);
    }
    return out;
  };
  const old = index(before), next = index(after);
  for (const [key, row] of old) {
    if (!next.has(key) || next.get(key)!.count < row.count) throw new Error('Counter census regressed');
  }
  return [...next.entries()].sort(([a], [b]) => a.localeCompare(b)).flatMap(([key, row]) => {
    const count = row.count - (old.get(key)?.count ?? 0);
    return count ? [{ ...row, count }] : [];
  });
}
