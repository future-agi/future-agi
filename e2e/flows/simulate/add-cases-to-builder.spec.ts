import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license), no builder and no simulation runner, so it cannot hold a
// finished run. Stub only those responses, as SIM-E2E-007 does; the run page,
// the drawer, the launch form, its confirmation, the navigation and the
// workspace's builder chat are the product's own. The request is pinned in the
// browser only: nothing is sent to the builder until the user presses send.

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

// The prebuilt template env-voice-support (frontend/src/api/simulate-environments/
// _fixtures/prebuiltEnvironments.js): useEnvironment falls back to it, so the
// workspace opens with its builder chat without a built environment.
const ENV_ID = 'env-voice-support';
const ENV_NAME = 'Customer Support Line';
const AGENT_NAME = 'Customer Support Line agent';
const RUN_TEST_ID = 'e2e-sim9a-run-test';

// Two failing cases: below scenarios_min (3), so no fix can start from this run.
const FAILING = [
  { key: 'refund-over-200', subGoals: ['escalated'] },
  { key: 'identity-phone', subGoals: ['verified_identity'] },
];
const PASSING = ['shipment-status', 'loyalty-tier'];

// AgentFixLaunchLimitsSerializer and AgentFixLaunchDefaultsSerializer
// (futureagi/simulate/serializers/agent_fix.py), at the platform's values.
const LIMITS = {
  budget_credits_max: 50000,
  max_minutes_max: 1440,
  scenarios_min: 3,
  cases_min: 12,
  variations_per_case: 3,
  verified_cases_min: 6,
  scenarios_max: 200,
};
// An untraced agent: no model to lock, its cost unmetered; the limit margins at their default.
const DEFAULTS = {
  surfaces: ['prompt'], budget_credits: 1500, max_minutes: 150,
  allowed_models: [], agent_cost_metered: false, agent_calls_unmeasured: false,
  reply_latency_margin_pct: 25, cost_margin_pct: 25,
};

// launchError and addCasesMessage in frontend/src/sections/simulate/environments/
// workspace/improvements/launchFormView.js; the link and the confirmation in LaunchFixDialog.jsx.
const MINIMUM_ERROR =
  'Needs at least 3 failing cases: with 3 variations of each, they can reach the 12 an epoch needs.';
const LINK = 'Add cases with the builder';
const CONFIRM_TITLE = 'Add cases in the builder?';
const CONFIRM_BODY =
  "You'll leave this page, and your choices here won't be kept. Once the builder adds cases, save them, run the environment again, then improve from the new run.";
const REQUEST =
  'Add 3 more cases like each of these, which fail in this run, checking the same sub-goals: ' +
  'refund-over-200 (escalated); identity-phone (verified_identity). ' +
  'Improving the agent needs at least 3 failing cases.';

type Json = Record<string, unknown>;

// GET /simulate/v3/test-executions/<id>/calls/: the run's execution, the part
// the run page reads (fetchRunCalls and useRunDetail in api/simulate-environments).
function runCalls(executionId: string): Json {
  return {
    count: 0,
    results: [],
    groups: [],
    summary: null,
    execution: {
      id: executionId,
      ordinal: 9,
      status: 'completed',
      agent_version: 'v1',
      started_at: '2026-11-14T09:00:00Z',
      fix_run: null,
      summary: { total: 4, outcomes: { passed: 2, failed: 2 }, pass_rate: 50 },
    },
  };
}

// GET /simulate/test-executions/<id>/debug-analysis/: a finished diagnosis, in
// the raw shape mapDebugAnalysis (api/simulate-environments/debugAnalysis.js) reads.
const DIAGNOSIS: Json = {
  status: 'completed',
  report: null,
  error_message: null,
  summary: { measured_call_count: 4, broken_goal_count: 1, broken_call_count: 2, one_off_count: 0 },
  goals: [
    {
      goal: 'escalated',
      label: 'Escalates refunds above $200',
      criteria: 'Pass when: the agent hands a refund above $200 to a supervisor',
      broken_call_ids: [],
      tested_call_count: 4,
      ways: [{ id: 'way-1', title: 'Refunds without approval', phrase: 'refunds without approval', call_ids: [] }],
      unexplained_call_ids: [],
    },
  ],
  one_offs: [],
};

// GET /simulate/api/agent-fix-runs/launch-options/: AgentFixLaunchOptionsSerializer,
// shaped as fixtureLaunchOptions in frontend/src/api/simulate-environments/__tests__/fixtures/fixRuns.js.
function launchOptions(executionId: string): Json {
  return {
    source_run: { id: executionId, number: 9, state: 'completed', finished_at: '2026-11-14T09:30:00Z' },
    environment: { id: ENV_ID, name: ENV_NAME, version: 1 },
    agent: { name: AGENT_NAME, kind: 'archive', version_id: 'agv-1', version_number: 1, ref: null },
    scenarios: [
      ...FAILING.map(({ key, subGoals }) => ({ key, name: key, passed: false, failing_sub_goals: subGoals })),
      ...PASSING.map((key) => ({ key, name: key, passed: true, failing_sub_goals: [] })),
    ],
    suggested: { target_scenario_keys: FAILING.map(({ key }) => key), protected_scenario_keys: PASSING },
    surfaces: [
      { id: 'prompt', label: 'Instructions', allowed: true, reason: null },
      { id: 'tools', label: 'Tools', allowed: true, reason: null },
      { id: 'settings', label: 'Settings', allowed: true, reason: null },
      { id: 'code', label: 'Code', allowed: false, reason: 'Only a GitHub agent can change its code.' },
    ],
    defaults: DEFAULTS,
    limits: LIMITS,
    blocked: null,
    variations_blocked: null,
  };
}

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubRunApi(page: Page, executionId: string) {
  const seen = { launchPosts: 0 };
  // The real capabilities, with only fix_my_agent switched on.
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await route.fetch();
    const json = await response.json();
    json.features = { ...json.features, fix_my_agent: { ...json.features?.fix_my_agent, allowed: true } };
    await route.fulfill({ response, json });
  });
  await page.route(endsWith(`/simulate/v3/test-executions/${executionId}/calls/`), (route) =>
    route.fulfill({ json: runCalls(executionId) }),
  );
  // The run header's reliability read: no numbers yet, so ReliabilityHeadline renders
  // nothing. Unstubbed, it 404s for a run the stack does not hold.
  await page.route(endsWith(`/simulate/v3/test-executions/${executionId}/reliability/`), (route) =>
    route.fulfill({ json: { pass_hat_1: null, rows: [] } }),
  );
  await page.route(endsWith(`/simulate/test-executions/${executionId}/debug-analysis/`), (route) =>
    route.fulfill({ json: DIAGNOSIS }),
  );
  await page.route(endsWith('/simulate/api/agent-fix-runs/launch-options/'), (route) =>
    route.fulfill({ json: launchOptions(executionId) }),
  );
  // A launch from this form would be a bug: the minimum refuses it.
  await page.route(endsWith('/simulate/api/agent-fix-runs/'), (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    seen.launchPosts += 1;
    return route.fulfill({ status: 500, json: { detail: 'no launch expected' } });
  });
  return seen;
}

test('SIM-E2E-009: a run with too few failing cases to fix sends the builder a request for more like them', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-009', area: 'simulate',
    userGoal: 'A developer whose run has too few failing cases to start a fix sends the builder a request for more cases like them',
    steps: ['open a finished run of the environment with 2 failing cases', 'open Debug failures', 'press Run Epoch',
            'read the launch form footer', 'press Add cases with the builder', 'read the confirmation',
            'press Open the builder', 'read the builder chat'],
    browserChecks: [
      'the footer reads that at least 3 failing cases are needed, ends with "Add cases with the builder", and the form\'s Run Epoch is disabled',
      'the link asks first: the confirmation says the page will be left, the choices dropped, and that the user saves, runs again and improves from the new run, while the URL stays on the run',
      'Open the builder lands on the environment\'s Scenarios tab',
      'the builder chat holds exactly the request naming refund-over-200 (escalated) and identity-phone (verified_identity), unsent',
      'no fix is launched (no POST to agent-fix-runs)',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 6 browser steps, each bounded by its own UI_READY: 6 × 60s = 360s, plus 60s
  // headroom so a slow run fails on the step that ran out.
  test.setTimeout(420_000);
  const executionId = randomUUID();
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, executionId }),
    contentType: 'application/json',
  });
  const seen = await stubRunApi(page, executionId);
  const runPath = `/dashboard/simulate/environments/${ENV_ID}/runs/${RUN_TEST_ID}/${executionId}`;

  await test.step('ui: the finished run opens with Debug failures', async () => {
    await page.goto(runPath);
    await expect(page.getByRole('button', { name: 'Debug failures' })).toBeEnabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: the diagnosis offers Run Epoch', async () => {
    await page.getByRole('button', { name: 'Debug failures' }).click();
    await expect(page.getByRole('button', { name: 'Run Epoch', exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  const form = page.getByRole('dialog').filter({ hasText: `Run Epoch on ${AGENT_NAME}` });
  await test.step('ui: check 1, the footer names the minimum and offers the builder', async () => {
    await page.getByRole('button', { name: 'Run Epoch', exact: true }).click();
    await expect(form.getByText(MINIMUM_ERROR)).toBeVisible({ timeout: UI_READY });
    await expect(form.getByRole('button', { name: LINK })).toBeVisible({ timeout: UI_READY });
    await expect(form.getByRole('button', { name: 'Run Epoch', exact: true })).toBeDisabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  const confirm = page.getByRole('dialog').filter({ hasText: CONFIRM_TITLE });
  await test.step('ui: check 2, the link asks before it leaves the run', async () => {
    await form.getByRole('button', { name: LINK }).click();
    await expect(confirm.getByText(CONFIRM_BODY, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page).toHaveURL(exactPath(runPath), { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 3, Open the builder lands on the Scenarios tab', async () => {
    await confirm.getByRole('button', { name: 'Open the builder' }).click();
    await expect(page).toHaveURL(
      exactPath(`/dashboard/simulate/environments/${ENV_ID}?tab=scenarios`),
      { timeout: UI_READY },
    );
  }, { timeout: UI_READY });

  await test.step('ui: check 4, the builder chat holds the request, unsent', async () => {
    const pinned = page.getByRole('button', { name: `Remove ${REQUEST}`, exact: true });
    await expect(pinned).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(REQUEST, { exact: true })).toHaveCount(1);
  }, { timeout: UI_READY });

  await test.step('ui: check 5, no fix was launched', async () => {
    expect(seen.launchPosts).toBe(0);
  });
});
