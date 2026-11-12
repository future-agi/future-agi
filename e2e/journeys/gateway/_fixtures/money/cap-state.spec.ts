import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { readBudgetConfig, CAP_SCHEMA_GAP } from './cap-state';

// Reader contract controls only; no database or product claim. Tests first,
// parse-only authoring. Do not confuse this with F06 activation/revision binding.
test('MONEY-FIXTURE.budget-reader-bound-parameters-no-revision-substitution', async () => {
  const organizationId = randomUUID();
  const calls: { text: string; values: readonly unknown[] }[] = [];
  const probe = { async pg<T>(text: string, values?: unknown[]): Promise<T[]> {
    calls.push({ text, values: values ?? [] }); return [];
  } };
  const expected = { organization: { limit: 20, period: 'monthly', hard: true } };
  expect(await readBudgetConfig(probe, organizationId, expected)).toEqual([]);
  expect(calls).toHaveLength(1);
  expect(calls[0].values).toEqual([organizationId, JSON.stringify(expected)]);
  expect(calls[0].text).toMatch(/WHERE organization_id = \$1/);
  expect(calls[0].text).toMatch(/budgets = \$2::jsonb AS budgets_match/);
  expect(calls[0].text).not.toMatch(/AS\s+(revision|cap_policy_revision)/i);
  expect(CAP_SCHEMA_GAP.status).toBe('OWED_SCHEMA_ABSENT_AT_PREP_PIN');
  expect(CAP_SCHEMA_GAP.models).toEqual(['AgentccCapActivation', 'AgentccCapPolicyRevision']);
});

test('MONEY-FIXTURE.budget-reader-refuses-invalid-input-before-query', async () => {
  let calls = 0;
  const probe = { async pg<T>(): Promise<T[]> { calls++; return []; } };
  await expect(readBudgetConfig(probe, 'not-a-uuid', {})).rejects.toThrow();
  await expect(readBudgetConfig(probe, randomUUID(), null)).rejects.toThrow();
  await expect(readBudgetConfig(probe, randomUUID(), { unsupported: 1 })).rejects.toThrow('UNKNOWN_BUDGET_FIELD');
  expect(calls).toBe(0);
});

test('MONEY-FIXTURE.budget-reader-propagates-storage-failure', async () => {
  const probe = { async pg<T>(): Promise<T[]> { throw new Error('fixture-storage-unavailable'); } };
  await expect(readBudgetConfig(probe, randomUUID(), {})).rejects.toThrow('fixture-storage-unavailable');
});
