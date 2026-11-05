import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedParticipantsCall } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as environments-only-routing.spec.ts).
const UI_READY = 60_000;

// Copy pinned from frontend/src/sections/simulate/environments/workspace/runs/detail/callTraces.constants.js
// (CALL_WORKERS_COPY.tab, .turn, .request, .parentUnknown, .openInTraces) and trialChanges.constants.js
// (TRIAL_CHANGES_COPY.tab, .worker, .refusedHeading).
const PARTICIPANTS_TAB = 'Participants';
const WORLD_CHANGES_TAB = 'World changes';

interface WorkersResponse {
  turns: { turn: number }[];
  steps: { kind: string; relationship: string; turn: number; order: number; worker_id: string | null }[];
}

test('SIM-E2E-003: a multi-agent call shows its workers turn by turn, an unplaced step as unknown, and a refused worker action', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-003', area: 'simulate',
    userGoal: 'A developer opens a multi-agent call and sees which worker did what in each turn, what the record could not place, and what the world refused a worker',
    steps: [
      'seed one completed chat call with a stored Participants record and trial world changes',
      'open the call from its run',
      'open the Participants tab',
      'open the first hand-off row and Open in Traces',
      'open the World changes tab',
    ],
    backendChecks: [
      'the workers API serves two turns, each step with its turn and order, and the second hand-off as relationship unknown',
    ],
    browserChecks: [
      'one table section per turn, each with its own request and only its own hand-off',
      'the unplaced hand-off carries the parent unknown chip, and only that one',
      'a step read from a trace opens the Traces tab',
      "World changes lists the worker's refused attempt with its reason",
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 6 browser actions × 60s, plus 60s headroom.
  test.setTimeout(420_000);
  const seed = seedParticipantsCall({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('participants-seed', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the record is served as two turns, the unplaced hand-off as unknown', async () => {
    const served = await actor.api.get<WorkersResponse>(`/simulate/v3/call-executions/${seed.callExecutionId}/workers/`);
    expect(served.turns.map((one) => one.turn)).toEqual([1, 2]);
    const handOffs = served.steps.filter((one) => one.kind === 'handoff');
    expect(handOffs.map((one) => [one.turn, one.order, one.relationship])).toEqual([[1, 1, 'observed'], [2, 1, 'unknown']]);
  });

  await test.step('ui: Participants shows each turn with its own hand-off', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${seed.testExecutionId}?rowId=${seed.callExecutionId}`);
    await page.getByRole('tab', { name: PARTICIPANTS_TAB, exact: true }).click({ timeout: UI_READY });
    const turns = page.getByTestId('turn');
    await expect(turns).toHaveCount(2, { timeout: UI_READY });
    await expect(turns.nth(0).getByTestId('worker-row')).toHaveCount(1);
    await expect(turns.nth(0).getByTestId('worker-row')).toHaveAttribute('data-worker', 'Order-lookup worker');
    await expect(turns.nth(1).getByTestId('worker-row')).toHaveAttribute('data-worker', 'Refund worker');
    await expect(turns.nth(0)).toContainText('Turn 1 · Caller\'s request');
    await expect(turns.nth(1)).toContainText('now refund it');
  });

  await test.step('ui: only the unplaced hand-off is marked parent unknown', async () => {
    const chips = page.getByTestId('parent-unknown');
    await expect(chips).toHaveCount(1, { timeout: UI_READY });
    await expect(page.getByTestId('turn').nth(1).getByTestId('parent-unknown')).toHaveCount(1);
  });

  await test.step('ui: a step read from a trace opens the Traces tab', async () => {
    await page.getByTestId('worker-row').first().click();
    await page.getByRole('button', { name: 'Open in Traces' }).first().click({ timeout: UI_READY });
    await expect(page.getByRole('tab', { name: 'Traces', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
  });

  await test.step("ui: World changes lists the worker's refused attempt", async () => {
    await page.getByRole('tab', { name: WORLD_CHANGES_TAB, exact: true }).click({ timeout: UI_READY });
    await expect(page.getByText('Worker · order_lookup', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('refused-attempts')).toContainText('update_order_status: orders.update_status is not permitted for order_lookup');
  });
});
