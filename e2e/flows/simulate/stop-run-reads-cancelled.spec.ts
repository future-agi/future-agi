import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedStopJourney } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as environments-only-routing.spec.ts).
const UI_READY = 60_000;

// Copy pinned from frontend/src/sections/simulate/environments/workspace/runs/StopRunControl.jsx (the
// "Stop simulation" button's aria-label, the dialog's "Stop run" action), runs/detail/trace/traceTable.constants.js
// (STATUS_CHIPS labels, each chip's aria-label) and runs/detail/analytics/DashboardCharts.jsx (a donut legend entry's
// aria-label "Show <label> calls"; RunAnalytics.jsx WIDGETS "Goal outcome breakdown").
const STOP_BUTTON = 'Stop simulation';
const STOP_CONFIRM = 'Stop run';
const ANALYTICS_TAB = 'Analytics';
const GOAL_OUTCOME = 'Goal outcome breakdown';

interface CallsPage {
  results: { id: string; outcome: string }[];
  status_counts: Record<string, number>;
}

interface ExecutionsPage {
  results: { id: string; status: string; outcome_cancelled: number; outcome_errored: number; outcome_skipped: number }[];
}

test('SIM-E2E-035: a user stops a running Run, and its call reads Cancelled in the calls list, the chips and analytics while a skipped case stays Error', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-035', area: 'simulate',
    userGoal: 'A developer stops a Run mid-call and sees the stopped call counted as Cancelled, never as an error, while a case the harness skipped still reads Error',
    steps: [
      'seed a running hosted Run with one call under way and one case the harness skipped',
      'open the Run and click Stop simulation, then Stop run',
      'reload the Run once it has stopped',
      'read the status chips',
      'open Analytics and click the donut legend entry for Cancelled calls',
    ],
    backendChecks: [
      "the calls API serves the stopped call as cancelled and the skipped case as error, with status counts Cancelled 1 and Error 1",
      'the runs list serves the Run as Cancelled with one cancelled outcome, no errored one and the skipped case as skipped',
    ],
    browserChecks: [
      'after the stop the Run offers no Stop control',
      'the Cancelled chip counts 1 and the Error chip counts 1',
      'the donut legend entry for Cancelled calls opens the calls list on the stopped call alone',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 8 browser waits × 60s plus one 60s poll on the cancel, with headroom.
  test.setTimeout(600_000);
  const seed = seedStopJourney({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('stop-journey-seed', { contentType: 'application/json', body: JSON.stringify(seed) });
  const runUrl = `/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${seed.testExecutionId}`;

  await test.step('ui: the user stops the Run from its page', async () => {
    await page.goto(runUrl);
    await page.getByRole('button', { name: STOP_BUTTON }).click({ timeout: UI_READY });
    await page.getByRole('button', { name: STOP_CONFIRM, exact: true }).click({ timeout: UI_READY });
  });

  await test.step('api: the stopped call is cancelled and the skipped case an error', async () => {
    await expect.poll(async () => {
      const served = await actor.api.get<CallsPage>(`/simulate/v3/test-executions/${seed.testExecutionId}/calls/`);
      const byId = Object.fromEntries(served.results.map((row) => [row.id, row.outcome]));
      return [byId[seed.ongoingCallExecutionId], byId[seed.skippedCallExecutionId], served.status_counts.cancelled, served.status_counts.error];
    }, { timeout: UI_READY }).toEqual(['cancelled', 'error', 1, 1]);
  });

  await test.step('api: the runs list counts the stopped call as cancelled, not errored, and the skipped case as skipped', async () => {
    const listing = await actor.api.get<ExecutionsPage>(`/simulate/run-tests/${seed.runTestId}/executions/`);
    const run = listing.results.find((row) => row.id === seed.testExecutionId);
    expect([run?.status, run?.outcome_cancelled, run?.outcome_errored, run?.outcome_skipped]).toEqual(['Cancelled', 1, 0, 1]);
  });

  await test.step('ui: the stopped Run offers no Stop, and its chips read Cancelled 1 and Error 1', async () => {
    await page.goto(runUrl);
    await expect(page.getByRole('button', { name: 'Cancelled', exact: true })).toHaveText(/^Cancelled\s*1$/, { timeout: UI_READY });
    await expect(page.getByRole('button', { name: 'Error', exact: true })).toHaveText(/^Error\s*1$/);
    await expect(page.getByRole('button', { name: STOP_BUTTON })).toHaveCount(0);
  });

  await test.step('ui: the donut opens the calls list on the stopped call alone', async () => {
    await page.getByRole('tab', { name: ANALYTICS_TAB, exact: true }).click({ timeout: UI_READY });
    const goalOutcome = page.getByRole('region', { name: GOAL_OUTCOME });
    await goalOutcome.getByRole('button', { name: 'Show Cancelled calls' }).click({ timeout: UI_READY });
    await expect(page.getByText(seed.ongoingScenarioName, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(seed.skippedScenarioName, { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Error', exact: true })).toHaveText(/^Error\s*0$/);
  });
});
