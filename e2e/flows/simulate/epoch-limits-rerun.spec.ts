import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license), no builder and no simulation runner, so it cannot hold a
// finished epoch. Stub only the Epoch responses here, as FEED-E2E-001 and
// SIM-E2E-007 do; the epoch page, its ending, the decision drawer, the launch
// form, the request it sends and the navigation are the product's own. The
// verdict numbers' path through the platform is covered by
// futureagi/simulate/tests/test_agent_fix_api.py.

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

// The prebuilt template env-voice-support, which the workspace opens without a
// built environment (as SIM-E2E-001 and SIM-E2E-007 use it).
const ENV_ID = 'env-voice-support';
const ENV_NAME = 'Customer Support Line';
const AGENT_NAME = 'Customer Support Line agent';

// Twelve failing cases, as many as an epoch needs, and three that passed.
const FAILING = Array.from({ length: 12 }, (_, i) => `case-${String(i + 1).padStart(2, '0')}`);
const PASSING = ['shipment-status', 'loyalty-tier', 'discontinued'];

// AgentFixLaunchDefaultsSerializer (futureagi/simulate/serializers/agent_fix.py):
// a traced agent whose run's spans named one model.
const DEFAULTS = {
  surfaces: ['prompt'], budget_credits: 1500, max_minutes: 150,
  allowed_models: ['gpt-4o-mini'], agent_cost_metered: true, agent_calls_unmeasured: false,
  reply_latency_margin_pct: 25, cost_margin_pct: 25,
};
const LIMITS = {
  budget_credits_max: 50000, max_minutes_max: 1440, scenarios_min: 3, cases_min: 12,
  variations_per_case: 3, verified_cases_min: 6, scenarios_max: 200,
};

// The rejected candidate's ceilings, as the engine sends them on the verdict
// (AgentFixVerdictTierSerializer's latency and cost).
const LATENCY = { baseline: 13.22, candidate: 19.38, allowed: 16.53, limit: null };
const COST = { baseline: 0.0096, candidate: 0.0139, allowed: 0.012, limit: null };

// What the screens say: noSafeHeadline and partDetail in
// frontend/src/sections/simulate/environments/workspace/improvements/fixRunView.js.
const ENDING = 'No safe improvement: the only candidate went over the cost or latency limit.';
const LATENCY_LINE = 'Over the limit: 19.38 s against 13.22 s on the current version, allowed up to 16.53 s.';
const COST_LINE = 'Over the limit: $0.0139 against $0.0096 on the current version, allowed up to $0.012.';
const COST_LIMIT = '0.01';

type Json = Record<string, unknown>;

const request = (overrides: Json = {}): Json => ({
  target_scenario_keys: FAILING,
  protected_scenario_keys: PASSING,
  held_back_scenario_keys: FAILING.slice(0, 4),
  failing_sub_goals: ['escalated'],
  surfaces: ['prompt'],
  locked_paths: [],
  budget_credits: 1500,
  max_minutes: 150,
  variations_pending: false,
  allowed_models: ['gpt-4o-mini'],
  reply_latency_margin_pct: 25,
  cost_margin_pct: 25,
  cost_limit_usd: null,
  observes_models: true,
  meters_agent_cost: true,
  ...overrides,
});

const passed = (name: string, detail: string) => ({ name, status: 'passed', detail, parts: [], latency: null, cost: null });

// GET /simulate/api/agent-fix-runs/<id>/: an epoch that ended with no safe
// improvement, its one candidate over its reply-time and cost limits, in
// AgentFixRunSerializer's read shape (the fix() fixture in
// frontend/src/api/simulate-environments/__tests__/fixtures/fixRuns.js).
function endedEpoch(fixId: string, sourceRunId: string): Json {
  return {
    id: fixId,
    number: 3,
    environment_id: ENV_ID,
    environment: { name: ENV_NAME, version: 1, default_agent_version_id: 'agv-1' },
    source_run_id: sourceRunId,
    source_run_number: 6,
    base_agent_version_id: 'agv-1',
    agent: { name: AGENT_NAME, kind: 'archive', version_number: 1, ref: null },
    state: 'no_improvement',
    stage: 'Done',
    step: 'done',
    plan: { target: 8, held_back: 4, must_pass: 3, protected: 3, max_candidates: 4, repeats: 3, baseline_spread_pts: 12 },
    diagnosis: { causes: ['It answers before it looks up the order'] },
    request: request(),
    spent_credits: 640,
    committed_credits: 640,
    report: { ending: 'no_improvement', summary: 'No safe improvement.' },
    failure: null,
    retried_from_id: null,
    retryable: false,
    limited_validation: false,
    check_runs: [],
    candidates: [
      {
        id: `${fixId}-c1`,
        attempt: 1,
        agent_version_id: 'agv-2',
        version_number: 2,
        summary: 'Look the order up before answering.',
        outcome: 'rejected',
        verdict: {
          accepted: false,
          reason: 'ceilings: over the reply-time and cost limits',
          target_pass_rate: [0.25, 0.75],
          held_back_pass_rate: [0.25, 0.5],
          must_pass_regressions: [],
          must_pass_within_noise: [],
          tiers: [
            passed('static', '1 file changed'),
            passed('target', '6 of 8 passed'),
            {
              name: 'ceilings',
              status: 'failed',
              detail: 'reply latency over; agent cost over',
              parts: [
                { name: 'reply latency', status: 'failed', detail: 'over' },
                { name: 'agent cost', status: 'failed', detail: 'over' },
              ],
              latency: LATENCY,
              cost: COST,
            },
          ],
        },
        check_runs: [],
        override: null,
        pull_request_url: '',
        created_at: '2026-11-14T09:40:00Z',
      },
    ],
    started_at: '2026-11-14T09:31:00Z',
    finished_at: '2026-11-14T10:02:00Z',
    created_at: '2026-11-14T09:30:00Z',
  };
}

// GET /simulate/api/agent-fix-runs/launch-options/: AgentFixLaunchOptionsSerializer,
// shaped as fixtureLaunchOptions in the same fixtures file.
function launchOptions(sourceRunId: string): Json {
  return {
    source_run: { id: sourceRunId, number: 6, state: 'completed', finished_at: '2026-11-14T09:20:00Z' },
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

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubEpochApi(page: Page, ids: { ended: string; next: string; sourceRun: string; epochSourceRun: string }) {
  const seen = { launchSourceRunIds: [] as string[], launchBodies: [] as Json[] };
  let next: Json | null = null;

  // The real capabilities, with only fix_my_agent switched on.
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await route.fetch();
    const json = await response.json();
    json.features = { ...json.features, fix_my_agent: { ...json.features?.fix_my_agent, allowed: true } };
    await route.fulfill({ response, json });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${ids.ended}/`), (route) =>
    route.fulfill({ json: endedEpoch(ids.ended, ids.epochSourceRun) }),
  );
  await page.route(endsWith('/simulate/api/agent-fix-runs/launch-options/'), (route) => {
    const sourceRunId = new URL(route.request().url()).searchParams.get('source_run_id') ?? '';
    seen.launchSourceRunIds.push(sourceRunId);
    return route.fulfill({ json: launchOptions(ids.sourceRun) });
  });
  await page.route(endsWith('/simulate/api/agent-fix-runs/'), (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as Json;
    seen.launchBodies.push(body);
    next = { ...endedEpoch(ids.next, ids.sourceRun), state: 'queued', step: null, candidates: [], report: null, request: request({ cost_limit_usd: body.cost_limit_usd }) };
    return route.fulfill({ status: 201, json: next });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${ids.next}/`), (route) =>
    next ? route.fulfill({ json: next }) : route.fulfill({ status: 404, json: { detail: 'Not found.' } }),
  );
  return seen;
}

test('SIM-E2E-008: an epoch that found no safe improvement shows why and starts a new one with limits', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-008', area: 'simulate',
    userGoal: 'A developer whose epoch found no safe improvement sees why, with the numbers it was decided on, and starts a new epoch from it with the agent\'s models locked and a cost limit',
    steps: ['open the finished epoch', 'read its ending', 'open How it was decided', 'press Run a new epoch',
            'read the model lock', 'type a cost limit of 0.01', 'press Run Epoch', 'read the new epoch\'s page'],
    browserChecks: [
      'the ending reads "No safe improvement: the only candidate went over the cost or latency limit."',
      'the rejected candidate\'s Reply time line reads "Over the limit: 19.38 s against 13.22 s on the current version, allowed up to 16.53 s." and its cost line has its numbers too',
      'the launch form asks launch-options for the epoch\'s own run and locks the model its spans named, gpt-4o-mini',
      'the launch POST body is exactly that run, its 12 failing and 3 passing cases, Instructions only, no locked files, 1,500 credits, 150 minutes, gpt-4o-mini, 25% margins and a $0.01 cost limit',
      'the browser lands on the environment\'s improvements/<id> URL of the epoch the POST returned',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 8 browser waits, each its own UI_READY: 8 × 60s = 480s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(540_000);
  const minted = `e2e-sim8-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const sourceRun = randomUUID();
  const ids = { ended: `${minted}-ended`, next: minted, sourceRun, epochSourceRun: sourceRun };
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, ...ids }),
    contentType: 'application/json',
  });
  const seen = await stubEpochApi(page, ids);

  await test.step('ui: check 1, the ending says why in plain words', async () => {
    await page.goto(`/dashboard/simulate/environments/${ENV_ID}/improvements/${ids.ended}`);
    await expect(page.getByText(ENDING, { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 2, How it was decided shows the numbers', async () => {
    await page.getByTestId('what-to-try-next').getByRole('button', { name: 'How it was decided' }).click();
    const rejected = page.getByTestId('rejected-1');
    await expect(rejected.getByText(LATENCY_LINE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(rejected.getByText(COST_LINE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await page.keyboard.press('Escape');
  }, { timeout: UI_READY });

  const dialog = page.getByRole('dialog').filter({ hasText: `Run Epoch on ${AGENT_NAME}` });
  await test.step('ui: check 3, the form is for the epoch\'s own run and locks its model', async () => {
    await page.getByTestId('what-to-try-next').getByRole('button', { name: 'Run a new epoch' }).click();
    await expect(dialog.getByTestId('allowed-models').getByRole('button')).toHaveText(['gpt-4o-mini'], { timeout: UI_READY });
    expect(seen.launchSourceRunIds).toEqual([sourceRun]);
  }, { timeout: UI_READY });

  await test.step('ui: check 4, Run Epoch sends the pick with its limits', async () => {
    await dialog.getByLabel("Agent's cost limit per conversation").fill(COST_LIMIT);
    await dialog.getByRole('button', { name: 'Run Epoch', exact: true }).click();
    await expect.poll(() => seen.launchBodies.length, { timeout: UI_READY }).toBe(1);
    expect(seen.launchBodies[0]).toEqual({
      source_run_id: sourceRun,
      target_scenario_keys: FAILING,
      protected_scenario_keys: PASSING,
      surfaces: ['prompt'],
      locked_paths: [],
      budget_credits: 1500,
      max_minutes: 150,
      allowed_models: ['gpt-4o-mini'],
      reply_latency_margin_pct: 25,
      cost_margin_pct: 25,
      cost_limit_usd: 0.01,
    });
  }, { timeout: UI_READY });

  await test.step('ui: check 5, the browser lands on the new epoch', async () => {
    await expect(page).toHaveURL(
      exactPath(`/dashboard/simulate/environments/${ENV_ID}/improvements/${ids.next}`),
      { timeout: UI_READY },
    );
  }, { timeout: UI_READY });
});
