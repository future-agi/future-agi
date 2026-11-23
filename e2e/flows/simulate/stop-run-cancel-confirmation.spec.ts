import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedStopConfirmationJourney } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as stop-run-reads-cancelled.spec.ts).
const UI_READY = 60_000;

// Copy pinned from frontend/src/sections/simulate/environments/workspace/runs/StopRunControl.jsx (the "Stop
// simulation" button's aria-label, the dialog's "Stop run" action), runs/runs.constants.js (STATUS_META labels) and
// runs/runStop.js (stopNoteOf for an agent at its own URL).
const STOP_BUTTON = 'Stop simulation';
const STOP_CONFIRM = 'Stop run';
const STOP_REQUESTED = 'Stop requested';
const STOPPED = 'Stopped';
const CANT_BE_CONFIRMED = /^Stop requested at .+ · can't be confirmed$/;
const OWN_URL_NOTE = /This agent runs at its own URL, so we can't see it stop\./;

interface Stop {
  status: string;
  kind: string;
  confirmed_by: string | null;
}

interface CallsPage {
  execution: { id: string; status: string; stop: Stop | null };
}

interface ExecutionsPage {
  results: { id: string; status: string; stop: Stop | null }[];
}

interface JobRow {
  state: string;
  cancel_requested_at: Date | null;
  cancel_confirmed_at: Date | null;
  cancel_confirmed_by: string;
}

test('SIM-E2E-037: a stopped Run reads Stop requested while its agent is unconfirmed, and Stopped once the stop is confirmed', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-037', area: 'simulate',
    userGoal: "A developer stops a Run and is told Stop requested while the agent's side has not confirmed it stopped, and Stopped only once it has",
    steps: [
      'seed two running Runs in one environment: one whose agent runs at its own URL, one whose agent ran in our sandbox',
      'open the URL agent\'s Run and click Stop simulation, then Stop run',
      'read the Run\'s status and hover it',
      'open the sandboxed agent\'s Run and stop it the same way',
      'open the environment\'s Runs tab and read both Runs\' status',
    ],
    backendChecks: [
      "the calls API serves the URL agent's Run as cancelled with its stop requested, of the endpoint kind",
      "the URL agent's hosted job is canceled with its stop requested and not confirmed",
      "the calls API serves the sandboxed Run with its stop confirmed by sandbox_absent, and so does its hosted job",
      'the runs list serves the URL agent\'s Run as requested and the sandboxed Run as confirmed',
    ],
    browserChecks: [
      "the URL agent's Run reads Stop requested, its hover says the stop can't be confirmed, and its note says why",
      "the runs list reads Stop requested for the URL agent's Run and Stopped for the sandboxed Run",
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Two stops (navigation, two clicks, one 60s API poll each), one header read with its hover, and the Runs tab
  // with two rows: 10 waits of UI_READY, with headroom.
  test.setTimeout(720_000);
  const seed = seedStopConfirmationJourney({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('stop-confirmation-seed', { contentType: 'application/json', body: JSON.stringify(seed) });
  const runUrl = (executionId: string) =>
    `/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}/${executionId}`;
  const servedStop = async (executionId: string) => {
    const served = await actor.api.get<CallsPage>(`/simulate/v3/test-executions/${executionId}/calls/`);
    const stop = served.execution.stop;
    return [served.execution.status, stop?.status ?? null, stop?.kind ?? null, stop?.confirmed_by ?? null];
  };
  const jobRow = async (jobId: string) => {
    const rows = await probe.pg<JobRow>(
      'SELECT state, cancel_requested_at, cancel_confirmed_at, cancel_confirmed_by FROM simulate_hosted_harness_job WHERE id = $1 AND organization_id = $2',
      [jobId, actor.organizationId],
    );
    expect(rows).toHaveLength(1);
    return rows[0];
  };
  const stop = async (executionId: string) => {
    await page.goto(runUrl(executionId));
    await page.getByRole('button', { name: STOP_BUTTON }).click({ timeout: UI_READY });
    await page.getByRole('button', { name: STOP_CONFIRM, exact: true }).click({ timeout: UI_READY });
  };

  await test.step("ui: the user stops the URL agent's Run", async () => {
    await stop(seed.remoteExecutionId);
  });

  await test.step("api: the URL agent's Run is cancelled with its stop only requested", async () => {
    await expect.poll(() => servedStop(seed.remoteExecutionId), { timeout: UI_READY })
      .toEqual(['cancelled', 'requested', 'endpoint', null]);
  });

  await test.step("storage: the URL agent's job is canceled, its stop requested and not confirmed", async () => {
    const row = await jobRow(seed.remoteJobId);
    expect([row.state, row.cancel_requested_at !== null, row.cancel_confirmed_at, row.cancel_confirmed_by])
      .toEqual(['canceled', true, null, '']);
  });

  await test.step("ui: the URL agent's Run reads Stop requested, and its hover and note say it can't be confirmed", async () => {
    await page.goto(runUrl(seed.remoteExecutionId));
    const status = page.getByText(STOP_REQUESTED, { exact: true });
    await expect(status).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(STOPPED, { exact: true })).toHaveCount(0);
    await expect(page.getByTestId('run-stop-notice')).toContainText(OWN_URL_NOTE);
    await status.hover();
    await expect(page.getByRole('tooltip')).toHaveText(CANT_BE_CONFIRMED, { timeout: UI_READY });
  });

  await test.step("ui: the user stops the sandboxed agent's Run", async () => {
    await stop(seed.sandboxedExecutionId);
  });

  await test.step('api + storage: the sandboxed Run is cancelled and its stop confirmed by the sandbox being gone', async () => {
    await expect.poll(() => servedStop(seed.sandboxedExecutionId), { timeout: UI_READY })
      .toEqual(['cancelled', 'confirmed', 'sandbox', 'sandbox_absent']);
    const row = await jobRow(seed.sandboxedJobId);
    expect([row.state, row.cancel_confirmed_at !== null, row.cancel_confirmed_by])
      .toEqual(['canceled', true, 'sandbox_absent']);
  });

  await test.step('api: the runs list serves each Run\'s stop', async () => {
    const listing = await actor.api.get<ExecutionsPage>(`/simulate/run-tests/${seed.runTestId}/executions/`);
    const byId = Object.fromEntries(listing.results.map((row) => [row.id, row.stop?.status ?? null]));
    expect([byId[seed.remoteExecutionId], byId[seed.sandboxedExecutionId]]).toEqual(['requested', 'confirmed']);
  });

  await test.step('ui: the Runs tab reads Stop requested for Run 1 and Stopped for Run 2', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=runs`);
    const remoteRow = page.getByRole('row').filter({ hasText: /\bRun 1\b/ });
    const sandboxedRow = page.getByRole('row').filter({ hasText: /\bRun 2\b/ });
    await expect(remoteRow.getByText(STOP_REQUESTED, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(remoteRow.getByText(STOPPED, { exact: true })).toHaveCount(0);
    await expect(sandboxedRow.getByText(STOPPED, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(sandboxedRow.getByText(STOP_REQUESTED, { exact: true })).toHaveCount(0);
  });
});
