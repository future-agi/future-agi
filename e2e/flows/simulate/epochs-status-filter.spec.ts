import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { fetchRoute } from '../../lib/backend-request';
import { flowAnnotation } from '../../lib/flow-meta';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license), so its list of epochs answers 402. Stub only the Epoch
// list here, as SIM-E2E-008 stubs its reads; the stub keeps the epochs whose
// state the request names, so a wrong `state` shows wrong rows. The workspace,
// its Epochs tab, the status filter and the request it sends are the product's own.
// Known limit: this proves the filter's request and the rows it renders, not the
// server's filter. That is futureagi/simulate/tests/test_agent_fix_api.py
// (test_fixes_list_by_state_with_their_total and the empty-state cases).

// Browser-side waits: one navigation's first paint or one filter's read, not a
// stage. The local stack slows several-fold beside other workers.
const UI_READY = 60_000;

// The prebuilt template env-voice-support, which the workspace opens without a
// built environment (as SIM-E2E-001 and SIM-E2E-008 use it).
const ENV_ID = 'env-voice-support';
const ENV_NAME = 'Customer Support Line';
const AGENT_NAME = 'Customer Support Line agent';

// The Running option's states (FIX_LIVE_STATES in
// frontend/src/api/simulate-environments/fixRuns.js), as ImprovementsPanel.jsx sends them.
const RUNNING_STATES = 'queued,running,stopping';
// The empty list's words for a status (ImprovementsPanel.jsx emptyMessage).
const NONE_READY = 'No epoch in this environment has the status “Ready for review”. Choose All statuses to see every epoch.';

type Json = Record<string, unknown>;

// GET /simulate/api/agent-fix-runs/?environment_id=…: one epoch in
// AgentFixRunSerializer's read shape (futureagi/simulate/serializers/agent_fix.py),
// as SIM-E2E-008's endedEpoch, with only what the list reads filled in.
function epoch(id: string, number: number, state: string, createdAt: string): Json {
  return {
    id,
    number,
    environment_id: ENV_ID,
    environment: { name: ENV_NAME, version: 1, default_agent_version_id: 'agv-1' },
    source_run_id: null,
    source_run_number: null,
    base_agent_version_id: 'agv-1',
    agent: { name: AGENT_NAME, kind: 'archive', version_number: 1, ref: null },
    state,
    stage: state === 'stopping' ? 'Propose · attempt 1' : 'Done',
    step: state === 'stopping' ? 'search' : 'done',
    plan: null,
    diagnosis: null,
    request: null,
    spent_credits: 120,
    committed_credits: 120,
    report: null,
    failure: null,
    retried_from_id: null,
    retryable: false,
    limited_validation: false,
    issue: null,
    from_issue: false,
    check_runs: [],
    candidates: [],
    started_at: createdAt,
    finished_at: state === 'stopping' ? null : createdAt,
    created_at: createdAt,
  };
}

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

// The list, filtered by the states the request names, newest first. Every
// request's `state` is recorded, in order.
async function stubEpochList(page: Page, epochs: Json[]) {
  const asked: (string | null)[] = [];
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await fetchRoute(route);
    const json = await response.json();
    json.features = { ...json.features, fix_my_agent: { ...json.features?.fix_my_agent, allowed: true } };
    await route.fulfill({ response, json });
  });
  await page.route(endsWith('/simulate/api/agent-fix-runs/'), (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const params = new URL(route.request().url()).searchParams;
    if (params.get('environment_id') !== ENV_ID) return route.fallback();
    const state = params.get('state');
    asked.push(state);
    const states = state ? state.split(',') : [];
    const results = epochs.filter((e) => !states.length || states.includes(e.state as string));
    return route.fulfill({ json: { count: results.length, next: null, previous: null, results } });
  });
  return asked;
}

test('SIM-E2E-038: a developer narrows an environment\'s Epochs list to one status and sees only the epochs in it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-038', area: 'simulate',
    userGoal: 'A developer narrows an environment\'s Epochs list to one status and sees only the epochs in it',
    steps: ['open the environment\'s Epochs tab', 'read every epoch under All statuses', 'choose Running',
            'choose No safe improvement', 'choose Ready for review', 'choose All statuses again'],
    browserChecks: [
      'All statuses asks the list with no state and shows exactly the three minted epochs, newest first',
      'Running asks for state=queued,running,stopping and shows exactly the stopping epoch',
      'No safe improvement asks for state=no_improvement and shows exactly that epoch',
      'Ready for review asks for state=ready and says no epoch has that status',
      'All statuses again shows the three epochs',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 6 browser waits, each its own UI_READY: 6 × 60s = 360s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(420_000);
  const minted = `e2e-sim37-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Epoch numbers the page names its rows by, minted so no other row matches.
  const base = 100_000 + (Date.now() % 800_000);
  const live = epoch(`${minted}-live`, base + 3, 'stopping', '2026-11-24T10:00:00Z');
  const noSafe = epoch(`${minted}-nosafe`, base + 2, 'no_improvement', '2026-11-23T10:00:00Z');
  const stopped = epoch(`${minted}-stopped`, base + 1, 'stopped', '2026-11-22T10:00:00Z');
  const name = (e: Json) => `Epoch ${e.number}`;
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, epochs: [live, noSafe, stopped].map((e) => e.id) }),
    contentType: 'application/json',
  });
  const asked = await stubEpochList(page, [live, noSafe, stopped]);

  const rows = page.getByRole('grid').getByText(/^Epoch \d+$/);
  const status = page.getByRole('combobox', { name: 'Status' });
  const choose = async (label: string, state: string | null) => {
    const read = page.waitForRequest(
      (r) => endsWith('/simulate/api/agent-fix-runs/')(new URL(r.url()))
        && new URL(r.url()).searchParams.get('state') === state,
      { timeout: UI_READY },
    );
    await status.click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await read;
  };

  await test.step('ui: check 1, All statuses lists every epoch', async () => {
    await page.goto(`/dashboard/simulate/environments/${ENV_ID}?tab=improvements`);
    await expect(rows).toHaveText([name(live), name(noSafe), name(stopped)], { timeout: UI_READY });
    expect(asked).toContain(null);
  }, { timeout: UI_READY });

  await test.step('ui: check 2, Running asks for every live state', async () => {
    await choose('Running', RUNNING_STATES);
    await expect(rows).toHaveText([name(live)], { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 3, No safe improvement shows only that epoch', async () => {
    await choose('No safe improvement', 'no_improvement');
    await expect(rows).toHaveText([name(noSafe)], { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 4, a status with no epoch says so', async () => {
    await choose('Ready for review', 'ready');
    await expect(page.getByText(NONE_READY, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(rows).toHaveCount(0);
  }, { timeout: UI_READY });

  await test.step('ui: check 5, All statuses again lists every epoch', async () => {
    await choose('All statuses', null);
    await expect(rows).toHaveText([name(live), name(noSafe), name(stopped)], { timeout: UI_READY });
  }, { timeout: UI_READY });
});
