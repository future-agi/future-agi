import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { seedAgentBuilds } from '../../lib/agent-build-seed';

// frontend/src/routes/sections/dashboard.jsx: connected/:agentId/environments under /dashboard/agents.
const TAB = (agentId: string) => `/dashboard/agents/connected/${agentId}/environments`;
// frontend/src/api/simulate-environments/harnessEnvironments.js listHarnessEnvironments, called by
// useAgentEnvironments (frontend/src/api/agents/agentDetail.js) with agent_definition_id, page 1, limit 100.
const ENVIRONMENTS = '/simulate/api/harness-environments/';
// frontend/src/utils/axios.js endpoints.agentDefinitions.detail; AgentDefinitionDetailView.get serves
// environment_count, which AgentDetailLayout.jsx renders as the tab label `Environments ${environmentCount}`.
const AGENT = (agentId: string) => `/simulate/agent-definitions/${agentId}/`;
// AgentEnvironmentsTab.jsx: each row's Testing cell is data-testid `testing-${env.id}`.
const ROW_PREFIX = 'testing-';
// e2e/README.md first-paint budget.
const UI_READY = 60_000;

type EnvironmentsPage = { count: number; results: { id: string }[] };
type AgentDetail = { id: string; environment_count: number };
type JobRow = { id: string; run_test_id: string | null; workspace_id: string; state: string };

test('AGENT-E2E-005: an agent\'s Environments tab lists its failed build that no test was linked to, and its count agrees', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AGENT-E2E-005', area: 'agents',
    userGoal: 'A developer opens an agent\'s Environments tab and sees every build testing it, including one that failed before a test was linked, with the tab\'s count matching the rows',
    steps: [
      'seed an agent with a finished build linked to its test and a failed build no test was linked to',
      'open the agent\'s Environments tab',
      'read the rows and the tab label',
    ],
    backendChecks: [
      'the seeded failed build has no test and is in the viewer\'s workspace, and the linked build has the seeded test',
      'the environments list the tab requests for the agent returns exactly the two seeded builds',
      'the agent\'s environment_count is 2, the number of rows the list returns',
    ],
    browserChecks: [
      'the tab shows exactly the two seeded builds\' rows',
      'the tab label reads Environments 2',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seeding through docker exec, then three 60s browser waits, with headroom.
  test.setTimeout(240_000);
  // Suppress third-party analytics; product traffic still reaches the real stack.
  const allowedOrigins = new Set([new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin]);
  await page.route('**/*', route => allowedOrigins.has(new URL(route.request().url()).origin)
    ? route.continue() : route.abort());

  const seed = seedAgentBuilds({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('agent-builds-seed', {
    contentType: 'application/json',
    body: JSON.stringify({ ...seed, organizationId: actor.organizationId, workspaceId: actor.workspaceId }, null, 2),
  });
  const expected = [seed.linkedBuildId, seed.failedBuildId].sort();

  await test.step('storage: the failed build has no test and is in the viewer\'s workspace', async () => {
    const rows = await probe.pg<JobRow>(
      'SELECT id::text, run_test_id::text, workspace_id::text, state FROM simulate_hosted_harness_job WHERE id = ANY($1::uuid[]) ORDER BY id',
      [expected],
    );
    await testInfo.attach('seeded-jobs', { contentType: 'application/json', body: JSON.stringify(rows, null, 2) });
    const byId = new Map(rows.map(row => [row.id, row]));
    expect(byId.get(seed.failedBuildId)).toEqual({
      id: seed.failedBuildId, run_test_id: null, workspace_id: actor.workspaceId, state: 'failed',
    });
    expect(byId.get(seed.linkedBuildId)).toEqual({
      id: seed.linkedBuildId, run_test_id: seed.runTestId, workspace_id: actor.workspaceId, state: 'completed',
    });
  });

  const listed = await test.step('ui: the tab lists exactly the two builds', async () => {
    const responsePromise = page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === 'GET' && url.pathname === ENVIRONMENTS
        && url.searchParams.get('agent_definition_id') === seed.agentDefinitionId;
    }, { timeout: UI_READY });
    await page.goto(TAB(seed.agentDefinitionId), { waitUntil: 'domcontentloaded' });
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const body = await response.json() as EnvironmentsPage;
    await testInfo.attach('tab-list-response', { contentType: 'application/json', body: JSON.stringify(body, null, 2) });

    const rows = page.locator(`[data-testid^="${ROW_PREFIX}"]`);
    await expect(rows).toHaveCount(expected.length, { timeout: UI_READY });
    const shown = await rows.evaluateAll((cells, prefix) =>
      cells.map(cell => (cell.getAttribute('data-testid') ?? '').slice(prefix.length)), ROW_PREFIX);
    expect(shown.sort()).toEqual(expected);
    await expect(page.getByRole('tab', { name: 'Environments 2', exact: true })).toBeVisible({ timeout: UI_READY });
    await testInfo.attach('environments-tab', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    return body;
  }, { timeout: UI_READY * 3 });

  await test.step('api: the list and the agent\'s count agree on the two builds', async () => {
    const replay = await actor.api.get<EnvironmentsPage>(ENVIRONMENTS, {
      agent_definition_id: seed.agentDefinitionId, page: 1, limit: 100,
    });
    const raw = await actor.api.get<AgentDetail | { result: AgentDetail }>(AGENT(seed.agentDefinitionId));
    const agent = 'result' in raw ? raw.result : raw;
    await testInfo.attach('api-replay', { contentType: 'application/json', body: JSON.stringify({ replay, agent }, null, 2) });
    for (const body of [listed, replay]) {
      expect(body.count).toBe(expected.length);
      expect(body.results.map(row => row.id).sort()).toEqual(expected);
    }
    expect(agent.id).toBe(seed.agentDefinitionId);
    expect(agent.environment_count).toBe(replay.count);
  });
});
