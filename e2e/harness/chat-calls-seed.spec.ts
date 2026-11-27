import { test, expect } from '../lib/fixtures';
import { seedHarnessEnvironment } from '../lib/simulate-seed';

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;

// seedHarnessEnvironment({ chatCalls }) stands in for a finished chat run. Prove what a flow relies on: one
// completed text call per reply, on the scenarios in order, each carrying its scenario key and a transcript of the
// customer's line then that reply; and the run page offers each call a tick box named by its scenario.
test('a seeded run of chat calls is finished, carries each transcript and can be ticked on the run page', async (
  { page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const tag = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const replies = [`e2e-chat-seed-a-${tag}`, `e2e-chat-seed-b-${tag}`];
  const seeded = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 2, lookups: 1, chatCalls: replies });
  await testInfo.attach('chat-calls-seed', { contentType: 'application/json', body: JSON.stringify(seeded) });
  const keys = seeded.scenarioKeys.slice(0, 2);
  expect(Object.keys(seeded.callIds ?? {}).sort()).toEqual([...keys].sort());

  const run = await probe.pg<{ status: string; total_calls: number }>(
    'SELECT status, total_calls FROM simulate_test_execution WHERE id = $1', [seeded.testExecutionId]);
  expect(run).toEqual([{ status: 'completed', total_calls: 2 }]);

  const calls = await probe.pg<{ id: string; status: string; kind: string; scenario_key: string }>(
    `SELECT id::text, status, simulation_call_type AS kind, call_metadata->>'harness_scenario_key' AS scenario_key
       FROM simulate_call_execution WHERE test_execution_id = $1 ORDER BY scenario_key`, [seeded.testExecutionId]);
  expect(calls).toEqual([...keys].sort().map(key => ({
    id: seeded.callIds![key], status: 'completed', kind: 'text', scenario_key: key })));

  for (const [index, key] of keys.entries()) {
    const messages = await probe.pg<{ role: string; text: string }>(
      `SELECT role, messages->>0 AS text FROM simulate_chat_message
        WHERE call_execution_id = $1 ORDER BY created_at, role DESC`, [seeded.callIds![key]]);
    expect(messages).toEqual([
      { role: 'user', text: expect.stringContaining('Hello, I need help with') },
      { role: 'assistant', text: replies[index] },
    ]);
  }

  await page.goto(`/dashboard/simulate/environments/${seeded.environmentId}/runs/${seeded.runTestId}`
    + `/${seeded.testExecutionId}`);
  // RunTraceTable.jsx groups calls by goal, every group closed; Expand all shows the calls.
  await page.getByLabel('Expand all', { exact: true }).check({ timeout: UI_READY });
  for (const key of keys) {
    await expect(page.getByRole('checkbox', { name: `Select ${key}`, exact: true }))
      .toBeVisible({ timeout: UI_READY });
  }
  // Every call's tick box, not the page's or a group's (TraceTable.jsx and TraceGroupHeaderRow.jsx label those).
  await expect(page.getByRole('checkbox', { name: /^Select (?!all calls on this page$|calls in )/ }))
    .toHaveCount(2);
});
