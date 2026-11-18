import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedStopJourney } from '../../lib/simulate-seed';

// The cancel's own wait for the guest to seal a call in flight (HARNESS_CANCEL_SEAL_SECONDS, 45 s), with headroom.
const STOPPED = 90_000;

interface CallsPage {
  results: { id: string; outcome: string }[];
  status_counts: Record<string, number>;
}

interface ExecutionsPage {
  results: { id: string; status: string; outcome_cancelled: number; outcome_errored: number; outcome_skipped: number }[];
}

test('SIM-E2E-035: a user stops a running Run, and its call is served Cancelled in the calls list and the runs list while a skipped case stays Error', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-035', area: 'simulate',
    userGoal: 'A developer stops a Run mid-call and the stopped call is counted as Cancelled, never as an error, while a case the harness skipped still reads Error',
    steps: [
      'seed a running hosted Run with one call under way and one case the harness skipped',
      'stop the Run, as its Stop simulation button does',
      'read the Run\'s calls and the runs list',
    ],
    backendChecks: [
      'the stop is accepted even with no gateway workflow to signal',
      "the calls API serves the stopped call as cancelled and the skipped case as error, with status counts Cancelled 1 and Error 1",
      'the runs list serves the Run as Cancelled with one cancelled outcome, no errored one and the skipped case as skipped',
    ],
  }),
}, async ({ actor }, testInfo) => {
  test.setTimeout(300_000);
  const seed = seedStopJourney({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('stop-journey-seed', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the user stops the Run', async () => {
    await actor.api.post(`/simulate/test-executions/${seed.testExecutionId}/cancel/`);
  });

  await test.step('api: the stopped call is cancelled and the skipped case an error', async () => {
    await expect.poll(async () => {
      const served = await actor.api.get<CallsPage>(`/simulate/v3/test-executions/${seed.testExecutionId}/calls/`);
      const byId = Object.fromEntries(served.results.map((row) => [row.id, row.outcome]));
      return [byId[seed.ongoingCallExecutionId], byId[seed.skippedCallExecutionId], served.status_counts.cancelled, served.status_counts.error];
    }, { timeout: STOPPED }).toEqual(['cancelled', 'error', 1, 1]);
  });

  await test.step('api: the runs list counts the stopped call as cancelled, not errored, and the skipped case as skipped', async () => {
    const listing = await actor.api.get<ExecutionsPage>(`/simulate/run-tests/${seed.runTestId}/executions/`);
    const run = listing.results.find((row) => row.id === seed.testExecutionId);
    expect([run?.status, run?.outcome_cancelled, run?.outcome_errored, run?.outcome_skipped]).toEqual(['Cancelled', 1, 0, 1]);
  });
});
