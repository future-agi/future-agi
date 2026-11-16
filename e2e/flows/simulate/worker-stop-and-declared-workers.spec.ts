import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedParticipantsCall } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as participants-table.spec.ts).
const UI_READY = 60_000;

// Copy pinned from frontend/src/sections/simulate/environments/workspace/contract/workers.constants.js (WORKERS_COPY)
// and runs/detail/callTraces.constants.js (CALL_WORKERS_COPY.cleanup.stopped, .workerCleanup).
const WORKERS_FOOTER = "Declared by the app's source. A run shows which of them actually took part.";
const PARTICIPANTS_TAB = 'Participants';

interface EnvironmentDetail {
  contract?: { workers?: { id: string; tools_shown: string[]; binding: string }[] };
}

test('SIM-E2E-011: the Contract tab lists the declared workers, and a worker the harness stopped at its deadline says so', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-011', area: 'simulate',
    userGoal: 'A developer reviews which workers the app declares before a run, and after a run sees that a worker past its deadline was stopped, not only refused',
    steps: [
      'seed an Environment whose contract declares two workers, with a call whose refund worker the harness stopped at its deadline',
      'open its Contract tab',
      'open that call and its Participants tab',
    ],
    backendChecks: [
      'the environment detail serves both declared workers with their binding and the tools each may use',
      'the workers API serves the stopped worker as past its deadline with cleanup stopped',
    ],
    browserChecks: [
      'the Workers card lists each worker with its channel and only the tools it may use',
      'the stopped call says the refund worker was stopped when its deadline passed',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 5 browser actions × 60s, plus 60s headroom.
  test.setTimeout(360_000);
  const seed = seedParticipantsCall({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('participants-seed', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the environment detail serves the declared workers', async () => {
    const detail = await actor.api.get<EnvironmentDetail>(`/simulate/api/harness-environments/${seed.environmentId}/`);
    const workers = detail.contract?.workers ?? [];
    expect(workers.map((one) => [one.id, one.binding])).toEqual([['order_lookup', 'separate'], ['refund', 'shared']]);
    expect(workers.map((one) => one.tools_shown)).toEqual([['get_order', 'list_refunds'], ['list_refunds', 'issue_refund']]);
  });

  await test.step('ui: the Workers card lists each worker with its channel and the tools it may use', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=contract`);
    const rows = page.getByTestId('declared-worker');
    await expect(rows).toHaveCount(2, { timeout: UI_READY });
    await expect(rows.nth(0)).toContainText('Order-lookup worker');
    await expect(rows.nth(0)).toContainText('Own channel');
    await expect(rows.nth(0).getByText('get_order', { exact: true })).toBeVisible();
    await expect(rows.nth(0)).not.toContainText('update_order_status');
    await expect(rows.nth(1)).toContainText('Shared channel');
    await expect(rows.nth(1)).toContainText('Own process');
    await expect(rows.nth(1).getByText('issue_refund', { exact: true })).toBeVisible();
    await expect(page.getByText(WORKERS_FOOTER)).toBeVisible();
  });

  await test.step('api: the stopped worker is served past its deadline with cleanup stopped', async () => {
    const served = await actor.api.get<{ workers: { id: string; state?: string; cleanup?: { status: string } }[] }>(
      `/simulate/v3/call-executions/${seed.stoppedWorkerCallExecutionId}/workers/`,
    );
    const refund = served.workers.find((one) => one.id === 'refund');
    expect([refund?.state, refund?.cleanup?.status]).toEqual(['deadline_passed', 'stopped']);
  });

  await test.step('ui: Participants says the refund worker was stopped when its deadline passed', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${seed.testExecutionId}?rowId=${seed.stoppedWorkerCallExecutionId}`);
    await page.getByRole('tab', { name: PARTICIPANTS_TAB, exact: true }).click({ timeout: UI_READY });
    await expect(page.getByTestId('worker-cleanup')).toContainText('Refund worker: Stopped when its deadline passed', { timeout: UI_READY });
  });
});
