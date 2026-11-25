import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedParticipantsCall } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as environments-only-routing.spec.ts).
const UI_READY = 60_000;

// Copy pinned from frontend/src/sections/simulate/environments/workspace/runs/detail/callTraces.constants.js
// (CALL_WORKERS_COPY.tab), participants/sequence.constants.js (SEQUENCE_COPY.follow, .stepLabel, .unknownLane,
// .openInTrace, .sequenceOf) and trialChanges.constants.js (TRIAL_CHANGES_COPY.tab, .worker, .refusedHeading).
const PARTICIPANTS_TAB = 'Participants';
const WORLD_CHANGES_TAB = 'World state';
// The lanes, in the order the diagram draws them: caller, coordinator, then each participant as it first acts.
// The refund hand-off the record could not place comes from the unknown lane, drawn before the worker it reached.
const LANE_HEADS = [
  'Follow Caller',
  'Follow Returns coordinator',
  'Follow Order-lookup worker',
  'Follow Not tied to a participant',
  'Follow Refund worker',
];
// Hand-off steps are named by their task; the seed's tasks are `inspect order <suffix>` and `refund the order`.
const HAND_OFF = /^Step (\d+): (inspect order .+|refund the order)$/;

interface WorkersResponse {
  turns: { turn: number }[];
  steps: { kind: string; relationship: string; turn: number; order: number; worker_id: string | null }[];
}

test('SIM-E2E-009: a multi-agent call shows its participants step by step, an unplaced hand-off as not tied to a participant, and a refused worker action', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-009', area: 'simulate',
    userGoal: 'A developer opens a multi-agent call and sees which participant did what, step by step, which step the record could not tie to a participant, and what the world refused a participant',
    steps: [
      'seed one completed chat call with a stored Participants record and trial world changes',
      'open the call from its run',
      'open the Participants tab',
      'pick the first hand-off step and Open in trace, then close the trace',
      'go back to Participants and pick the hand-off the record could not place',
      'open the World state tab',
      'open the call the user stopped and its Participants tab',
    ],
    backendChecks: [
      'the workers API serves two turns, each step with its turn and order, and the second hand-off as relationship unknown',
    ],
    browserChecks: [
      'one lane per participant, in the order they first act, plus one for the step the record could not tie',
      'the order-lookup hand-off is numbered before the refund hand-off',
      'a step read from a trace opens that trace, and closing it leaves the call on its Traces tab',
      'the unplaced hand-off comes from the lane not tied to a participant',
      "World changes lists the worker's refused attempt with its reason",
      'World changes lists the order two workers named, with its committed writes',
      'the stopped call says it was stopped by the user, and its cancelled worker was stopped with the run sandbox',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 9 browser waits × 60s, plus 60s headroom.
  test.setTimeout(600_000);
  const seed = seedParticipantsCall({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('participants-seed', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the record is served as two turns, the unplaced hand-off as unknown', async () => {
    const served = await actor.api.get<WorkersResponse>(`/simulate/v3/call-executions/${seed.callExecutionId}/workers/`);
    expect(served.turns.map((one) => one.turn)).toEqual([1, 2]);
    const handOffs = served.steps.filter((one) => one.kind === 'handoff');
    expect(handOffs.map((one) => [one.turn, one.order, one.relationship])).toEqual([[1, 1, 'observed'], [2, 1, 'unknown']]);
  });

  const diagram = page.getByRole('group', { name: /^Sequence of \d+ steps$/ });
  const handOffs = diagram.getByRole('button', { name: HAND_OFF });

  await test.step('ui: Participants draws one lane per participant, in the order they first act', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${seed.testExecutionId}?rowId=${seed.callExecutionId}`);
    await page.getByRole('tab', { name: PARTICIPANTS_TAB, exact: true }).click({ timeout: UI_READY });
    const heads = diagram.locator('[data-lane-head]');
    await expect(heads).toHaveCount(LANE_HEADS.length, { timeout: UI_READY });
    expect(await heads.evaluateAll((all) => all.map((one) => one.getAttribute('aria-label')))).toEqual(LANE_HEADS);
  });

  await test.step('ui: the order-lookup hand-off is numbered before the refund hand-off', async () => {
    await expect(handOffs).toHaveCount(2, { timeout: UI_READY });
    const named = (await handOffs.evaluateAll((all) => all.map((one) => one.getAttribute('aria-label') ?? '')))
      .map((label) => HAND_OFF.exec(label) ?? []);
    expect(named.map(([, , task]) => task?.split(' ')[0])).toEqual(['inspect', 'refund']);
    expect(Number(named[0][1])).toBeLessThan(Number(named[1][1]));
  });

  await test.step('ui: a step read from a trace opens that trace, over the Traces tab', async () => {
    await handOffs.first().click();
    await page.getByTestId('step-detail').getByRole('button', { name: 'Open in trace' }).click({ timeout: UI_READY });
    // The trace opens in its own drawer, named by its id; closing it leaves the call on its Traces tab.
    await expect(page.getByText(seed.traceId.slice(0, 24))).toBeVisible({ timeout: UI_READY });
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tab', { name: 'Traces', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
  });

  await test.step('ui: the unplaced hand-off comes from the lane not tied to a participant', async () => {
    await page.getByRole('tab', { name: PARTICIPANTS_TAB, exact: true }).click({ timeout: UI_READY });
    await handOffs.last().click({ timeout: UI_READY });
    await expect(page.getByTestId('step-detail')).toContainText('Not tied to a participant → Refund worker', { timeout: UI_READY });
  });

  await test.step("ui: World changes lists the worker's refused attempt", async () => {
    await page.getByRole('tab', { name: WORLD_CHANGES_TAB, exact: true }).click({ timeout: UI_READY });
    await expect(page.getByText('Participant · order_lookup', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('refused-attempts')).toContainText('update_order_status: orders.update_status is not permitted for order_lookup');
  });

  await test.step('ui: World changes lists the order two workers named', async () => {
    const shared = page.getByTestId('shared-records');
    await expect(shared).toContainText('Records touched by more than one participant', { timeout: UI_READY });
    await expect(shared).toContainText('order_id 1042');
    await expect(shared).toContainText('Committed writes: 1');
  });

  await test.step('api: the stopped call serves its cancelled worker with the sandbox cleanup', async () => {
    const served = await actor.api.get<{ workers: { id: string; state: string; cleanup?: { status: string } }[] }>(
      `/simulate/v3/call-executions/${seed.cancelledCallExecutionId}/workers/`,
    );
    const refund = served.workers.find((one) => one.id === 'refund');
    expect([refund?.state, refund?.cleanup?.status]).toEqual(['cancelled', 'stopped_with_sandbox']);
  });

  await test.step('ui: the stopped call says it was stopped by the user', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${seed.testExecutionId}?rowId=${seed.cancelledCallExecutionId}`);
    await page.getByRole('tab', { name: PARTICIPANTS_TAB, exact: true }).click({ timeout: UI_READY });
    await expect(page.getByTestId('stopped-by-user')).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('worker-cleanup')).toContainText("Refund worker: Stopped when the run's test environment shut down");
  });
});
