import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
// The isolated E2E stack cannot hold a finished run with a diagnosis, nor make
// the diagnosis job fail on request. Stub only the run's calls and its
// debug-analysis route here, as SIM-E2E-007 does; the run page, the Debug
// failures drawer, its request and its messages are the product's own. The
// drawer's states are also covered by
// frontend/src/sections/simulate/environments/workspace/runs/detail/fixmyagent/__tests__/FixMyAgentDrawer.test.tsx.

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

// The prebuilt template env-voice-support (frontend/src/api/simulate-environments/
// _fixtures/prebuiltEnvironments.js): useEnvironment falls back to it, so the
// workspace opens without a built environment, as SIM-E2E-001 opens it.
const ENV_ID = 'env-voice-support';
const RUN_TEST_ID = 'e2e-sim44-run-test';

// What the drawer says, from FixMyAgentDrawer.jsx and DiagnosisPane.jsx
// (frontend/src/sections/simulate/environments/workspace/runs/detail/fixmyagent/).
// A request that fails reads as the generic line; a failed diagnosis reads the
// server's own error_message under its title.
const COULDNT_READ = "Couldn't read this run. Try again.";
const FAILED_TITLE = "Couldn't finish the diagnosis";
const FAILED_MESSAGE = 'Debug analysis failed';

type Json = Record<string, unknown>;

// GET /simulate/v3/test-executions/<id>/calls/: the run's execution, the part
// the run page reads (fetchRunCalls and useRunDetail in api/simulate-environments).
// Fifteen connected calls, six of them failing: the run Debug failures accepts
// (frontend/src/sections/test-detail/debugFailuresRule.ts).
function runCalls(executionId: string): Json {
  return {
    count: 0,
    results: [],
    groups: [],
    summary: null,
    execution: {
      id: executionId,
      ordinal: 6,
      status: 'completed',
      agent_version: 'v1',
      started_at: '2026-11-14T09:00:00Z',
      fix_run: null,
      connected_calls: 15,
      summary: { total: 15, outcomes: { passed: 9, failed: 6 }, pass_rate: 60 },
    },
  };
}

// GET/POST /simulate/test-executions/<id>/debug-analysis/, in the raw shape
// mapDebugAnalysis (api/simulate-environments/debugAnalysis.js) reads: a run
// with no diagnosis yet, and a diagnosis that failed.
const NOT_DIAGNOSED: Json = {
  status: 'not_requested', report: null, error_message: null, summary: null, goals: [], one_offs: [], findings: [],
};
const FAILED: Json = { ...NOT_DIAGNOSED, status: 'failed', error_message: FAILED_MESSAGE };

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

// The run's diagnosis as the server holds it. While `fails` is set a request gets
// a 500 and the diagnosis stays as it was; otherwise the request makes a diagnosis
// that fails, which is what the next read returns.
async function stubDiagnosis(page: Page, executionId: string) {
  const seen = { posts: 0, read: NOT_DIAGNOSED, fails: true };
  await page.route(endsWith(`/simulate/v3/test-executions/${executionId}/calls/`), (route: Route) =>
    route.fulfill({ json: runCalls(executionId) }),
  );
  // The run header's reliability read: no numbers yet, so ReliabilityHeadline
  // (runs/detail/ReliabilityHeadline.jsx) renders nothing. Unstubbed, it 404s for
  // a run the stack does not hold, and its error toast can cover the drawer.
  await page.route(endsWith(`/simulate/v3/test-executions/${executionId}/reliability/`), (route: Route) =>
    route.fulfill({ json: { pass_hat_1: null, rows: [] } }),
  );
  await page.route(endsWith(`/simulate/test-executions/${executionId}/debug-analysis/`), (route: Route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: seen.read });
    seen.posts += 1;
    if (seen.fails) return route.fulfill({ status: 500, json: { detail: 'Internal Server Error' } });
    seen.read = FAILED;
    return route.fulfill({ json: FAILED });
  });
  return seen;
}

test('SIM-E2E-044: a failed Debug failures retry says why and a reopen asks again', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-044', area: 'simulate',
    userGoal: 'A developer whose Debug failures request fails is told so, and after closing and reopening the drawer it asks again; when that diagnosis fails and Try again fails too, the drawer says so instead of looking unchanged',
    steps: ['open a finished run with 15 connected calls and no diagnosis, and open Debug failures while the request fails',
            'close the drawer', 'reopen Debug failures while the request now goes through and the diagnosis fails',
            'press Try again while the request fails'],
    browserChecks: [
      'opening Debug failures on a run with no diagnosis sends one request, and the drawer reads "Couldn\'t read this run. Try again." with a Try again button',
      'closing the drawer removes it from the page',
      'reopening sends a second request, and the drawer no longer reads "Couldn\'t read this run. Try again."; it reads "Couldn\'t finish the diagnosis" with "Debug analysis failed"',
      'pressing Try again while the request fails sends a third request, and the drawer reads "Couldn\'t read this run. Try again." and no longer reads "Debug analysis failed", with Try again still shown',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 5 browser waits, each its own UI_READY: 5 × 60s = 300s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(360_000);
  const executionId = randomUUID();
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, executionId }),
    contentType: 'application/json',
  });
  const seen = await stubDiagnosis(page, executionId);
  const runPath = `/dashboard/simulate/environments/${ENV_ID}/runs/${RUN_TEST_ID}/${executionId}`;

  const debugFailures = page.getByRole('button', { name: 'Debug failures' });
  const drawerTab = page.getByRole('tab', { name: 'Diagnosis' });
  const couldntRead = page.getByText(COULDNT_READ, { exact: true });
  const failedMessage = page.getByText(FAILED_MESSAGE);
  const tryAgain = page.getByRole('button', { name: 'Try again' });

  await test.step('ui: check 0, opening Debug failures on an undiagnosed run asks once and says the request failed', async () => {
    await page.goto(runPath);
    await expect(debugFailures).toBeEnabled({ timeout: UI_READY });
    await debugFailures.click();
    await expect(couldntRead).toBeVisible({ timeout: UI_READY });
    await expect(tryAgain).toBeVisible({ timeout: UI_READY });
    expect(seen.posts).toBe(1);
  }, { timeout: UI_READY });

  await test.step('ui: the drawer closes', async () => {
    await page.keyboard.press('Escape');
    await expect(drawerTab).toBeHidden({ timeout: UI_READY });
  }, { timeout: UI_READY });

  // The server can answer now; its diagnosis comes back failed.
  seen.fails = false;
  await test.step('ui: check 1, a reopen asks again and the old failure is gone', async () => {
    await debugFailures.click();
    await expect.poll(() => seen.posts, { timeout: UI_READY }).toBe(2);
    await expect(page.getByText(FAILED_TITLE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(failedMessage).toBeVisible({ timeout: UI_READY });
    await expect(couldntRead).toBeHidden({ timeout: UI_READY });
  }, { timeout: UI_READY });

  seen.fails = true;
  await test.step('ui: check 2, a retry that fails says so instead of looking unchanged', async () => {
    await tryAgain.click();
    await expect.poll(() => seen.posts, { timeout: UI_READY }).toBe(3);
    await expect(couldntRead).toBeVisible({ timeout: UI_READY });
    await expect(failedMessage).toBeHidden({ timeout: UI_READY });
    await expect(tryAgain).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });
});
