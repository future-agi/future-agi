import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license), no builder and no simulation runner, so it cannot hold a
// finished run or an epoch. Stub only those responses here, as FEED-E2E-001
// does; the routing, the drawer, the launch form, the request it sends and the
// epoch page are the product's own. The launch flag, the variation rows, the
// sweep and the refusals behind this are covered by
// futureagi/simulate/tests/test_agent_fix_variations.py.

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

// The prebuilt template env-voice-support (frontend/src/api/simulate-environments/
// _fixtures/prebuiltEnvironments.js): useEnvironment falls back to it, so the
// workspace opens without a built environment, as SIM-E2E-001 opens it.
const ENV_ID = 'env-voice-support';
const ENV_NAME = 'Customer Support Line';
const AGENT_NAME = 'Customer Support Line agent';
const RUN_TEST_ID = 'e2e-sim7-run-test';

// Six failing and six passing cases: fewer failing than an epoch needs (12), as
// many as verified_cases_min, so the form shows the variations note alone.
const FAILING = ['refund-over-200', 'identity-phone', 'return-window', 'other-order', 'delivery-eta', 'refund-method'];
const PASSING = ['shipment-status', 'loyalty-tier', 'discontinued', 'instruction-override', 'ambiguous-request', 'delayed-orders'];

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
const DEFAULTS = { surfaces: ['prompt'], budget_credits: 1500, max_minutes: 150 };

// What the form says for that pick: countsLine and variationsNote in
// frontend/src/sections/simulate/environments/workspace/improvements/launchFormView.js.
const SCENARIOS_LINE = '6 failing, and up to 18 variations of them · 6 must keep passing';
const VARIATIONS_NOTE =
  'An epoch needs 12 failing cases. It first writes 3 variations of each, then fixes the ones the current version also fails.';
// The epoch page's first step for a fix that writes variations (fixRunView.js variationLines).
const VARIATIONS_STEP_LINE = 'Writes variations of the 6 failing cases, then keeps those the current version also fails';
const STEP_IDS = ['step-variations', 'step-reproduce', 'step-search', 'step-verify', 'step-done'];

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
      ordinal: 6,
      status: 'completed',
      agent_version: 'v1',
      started_at: '2026-11-14T09:00:00Z',
      fix_run: null,
      summary: { total: 12, outcomes: { passed: 6, failed: 6 }, pass_rate: 50 },
    },
  };
}

// GET/POST /simulate/test-executions/<id>/debug-analysis/: a finished diagnosis,
// in the raw shape mapDebugAnalysis (api/simulate-environments/debugAnalysis.js) reads.
const DIAGNOSIS: Json = {
  status: 'completed',
  report: null,
  error_message: null,
  summary: { measured_call_count: 12, broken_goal_count: 1, broken_call_count: 6, one_off_count: 0 },
  goals: [
    {
      goal: 'escalated',
      label: 'Escalates refunds above $200',
      criteria: 'Pass when: the agent hands a refund above $200 to a supervisor',
      broken_call_ids: [],
      tested_call_count: 12,
      ways: [{ id: 'way-1', title: 'Refunds without approval', phrase: 'refunds without approval', call_ids: [] }],
      unexplained_call_ids: [],
    },
  ],
  one_offs: [],
};
// What the drawer reads when it opens. A finished diagnosis is read, never queued again.
const DIAGNOSIS_ON_OPEN: Json = DIAGNOSIS;

// GET /simulate/api/agent-fix-runs/launch-options/: AgentFixLaunchOptionsSerializer,
// shaped as fixtureLaunchOptions in frontend/src/api/simulate-environments/__tests__/fixtures/fixRuns.js.
function launchOptions(executionId: string): Json {
  return {
    source_run: { id: executionId, number: 6, state: 'completed', finished_at: '2026-11-14T09:30:00Z' },
    environment: { id: ENV_ID, name: ENV_NAME, version: 1 },
    agent: { name: AGENT_NAME, kind: 'archive', version_id: 'agv-1', version_number: 1, ref: null },
    scenarios: [
      ...FAILING.map((key) => ({ key, name: key, passed: false, failing_sub_goals: ['escalated'] })),
      ...PASSING.map((key) => ({ key, name: key, passed: true, failing_sub_goals: [] })),
    ],
    suggested: { target_scenario_keys: FAILING, protected_scenario_keys: PASSING },
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

// POST /simulate/api/agent-fix-runs/ and GET .../<id>/: a queued epoch that
// writes variations first, in AgentFixRunSerializer's read shape (the fix() fixture).
function queuedEpoch(fixId: string, executionId: string, body: Json): Json {
  return {
    id: fixId,
    number: 1,
    environment_id: ENV_ID,
    environment: { name: ENV_NAME, version: 1, default_agent_version_id: 'agv-1' },
    source_run_id: executionId,
    source_run_number: 6,
    base_agent_version_id: 'agv-1',
    agent: { name: AGENT_NAME, kind: 'archive', version_number: 1, ref: null },
    state: 'queued',
    step: null,
    stage: 'Starting',
    request: {
      target_scenario_keys: body.target_scenario_keys,
      protected_scenario_keys: body.protected_scenario_keys,
      held_back_scenario_keys: [],
      failing_sub_goals: ['escalated'],
      surfaces: body.surfaces,
      locked_paths: body.locked_paths,
      budget_credits: body.budget_credits,
      max_minutes: body.max_minutes,
      variations_pending: true,
    },
    spent_credits: 0,
    committed_credits: 0,
    plan: null,
    diagnosis: null,
    report: null,
    failure: null,
    retried_from_id: null,
    retryable: false,
    limited_validation: false,
    eval_warnings: [],
    check_runs: [],
    candidates: [],
    started_at: null,
    finished_at: null,
    created_at: new Date().toISOString(),
  };
}

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubEpochApi(page: Page, executionId: string, fixId: string) {
  const seen = { diagnosisPosts: 0, launchSourceRunIds: [] as string[], launchBodies: [] as Json[] };
  let epoch: Json | null = null;

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
  await page.route(endsWith(`/simulate/test-executions/${executionId}/debug-analysis/`), (route) => {
    if (route.request().method() === 'POST') {
      seen.diagnosisPosts += 1;
      return route.fulfill({ json: DIAGNOSIS });
    }
    return route.fulfill({ json: DIAGNOSIS_ON_OPEN });
  });
  await page.route(endsWith('/simulate/api/agent-fix-runs/launch-options/'), (route) => {
    const sourceRunId = new URL(route.request().url()).searchParams.get('source_run_id') ?? '';
    seen.launchSourceRunIds.push(sourceRunId);
    return route.fulfill({ json: launchOptions(executionId) });
  });
  await page.route(endsWith('/simulate/api/agent-fix-runs/'), (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as Json;
    seen.launchBodies.push(body);
    epoch = queuedEpoch(fixId, executionId, body);
    return route.fulfill({ status: 201, json: epoch });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${fixId}/`), (route) =>
    epoch ? route.fulfill({ json: epoch }) : route.fulfill({ status: 404, json: { detail: 'Not found.' } }),
  );
  return seen;
}

test('SIM-E2E-007: a run with fewer failing cases than an epoch needs starts one that writes variations first', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-007', area: 'simulate',
    userGoal: 'A developer whose run has fewer failing cases than an epoch needs starts one that first writes variations of them, and the epoch page shows that step',
    steps: ['open a finished run of the environment', 'open Debug failures', 'press Improve',
            'read the Scenarios row of the launch form', 'press Start improvement', 'read the epoch page'],
    browserChecks: [
      'the Scenarios row reads "6 failing, and up to 18 variations of them · 6 must keep passing", with the note that an epoch needs 12 failing cases and first writes 3 variations of each',
      'the launch POST body is exactly the run id, the 6 failing cases, the 6 passing cases, Instructions only, no locked files, 1,500 credits and 150 minutes',
      'the browser lands on the environment\'s improvements/<id> URL of the epoch the POST returned',
      'the epoch page\'s steps are Variations, Reproduce, Search, Verify and Ready for review, and Variations reads "Writes variations of the 6 failing cases, then keeps those the current version also fails"',
      'the drawer reads the finished diagnosis and never queues another (no POST to debug-analysis)',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 7 browser waits, each its own UI_READY: 7 × 60s = 420s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(480_000);
  const executionId = randomUUID();
  const fixId = `e2e-sim7-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, executionId, fixId }),
    contentType: 'application/json',
  });
  const seen = await stubEpochApi(page, executionId, fixId);
  const runPath = `/dashboard/simulate/environments/${ENV_ID}/runs/${RUN_TEST_ID}/${executionId}`;

  await test.step('ui: the finished run opens with Debug failures', async () => {
    await page.goto(runPath);
    await expect(page.getByRole('button', { name: 'Debug failures' })).toBeEnabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: the diagnosis offers Improve', async () => {
    await page.getByRole('button', { name: 'Debug failures' }).click();
    await expect(page.getByRole('button', { name: 'Improve', exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  const dialog = page.getByRole('dialog').filter({ hasText: `Improve ${AGENT_NAME}` });
  await test.step('ui: check 1, the launch form says it writes variations first', async () => {
    await page.getByRole('button', { name: 'Improve', exact: true }).click();
    await expect(dialog.getByText(SCENARIOS_LINE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(dialog.getByText(VARIATIONS_NOTE, { exact: true })).toBeVisible({ timeout: UI_READY });
    expect(seen.launchSourceRunIds).toEqual([executionId]);
  }, { timeout: UI_READY });

  await test.step('ui: check 2, Start sends exactly the pick', async () => {
    await dialog.getByRole('button', { name: 'Start improvement' }).click();
    await expect.poll(() => seen.launchBodies.length, { timeout: UI_READY }).toBe(1);
    expect(seen.launchBodies[0]).toEqual({
      source_run_id: executionId,
      target_scenario_keys: FAILING,
      protected_scenario_keys: PASSING,
      surfaces: ['prompt'],
      locked_paths: [],
      budget_credits: 1500,
      max_minutes: 150,
    });
  }, { timeout: UI_READY });

  await test.step('ui: check 3, the browser lands on the new epoch', async () => {
    await expect(page).toHaveURL(
      exactPath(`/dashboard/simulate/environments/${ENV_ID}/improvements/${fixId}`),
      { timeout: UI_READY },
    );
  }, { timeout: UI_READY });

  const steps = page.getByTestId('fix-steps').locator('[data-testid^="step-"]');
  await test.step('ui: check 4, the epoch page starts with the Variations step', async () => {
    await expect.poll(
      async () => steps.evaluateAll((rows) => rows.map((row) => row.getAttribute('data-testid'))),
      { timeout: UI_READY },
    ).toEqual(STEP_IDS);
    await expect(page.getByTestId('step-variations').getByText(VARIATIONS_STEP_LINE, { exact: true }))
      .toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 5, the finished diagnosis was never queued again', async () => {
    expect(seen.diagnosisPosts).toBe(0);
  });
});
