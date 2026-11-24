import { test, expect } from '../../lib/fixtures';
import { AGENT_SOURCE_DIR, AGENT_SOURCE_NAME } from '../../lib/agent-source';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';

test.skip(process.env.E2E_STACK === 'standalone', 'agent reading runs on worker-reading and mock-daytona in the distributed E2E stack');

// frontend/src/routes/sections/dashboard.jsx (agents › connect) and connectSources.js UPLOAD_SOURCE.
const CONNECT_UPLOAD = '/dashboard/agents/connect?source=upload';
// frontend/src/api/harness/harness.js sourcesPath and preflightPath.
const SOURCES_PATH = '/simulate/api/harness-jobs/sources/';
const PREFLIGHT_PATH = '/simulate/api/harness-jobs/preflight/';
// e2e/fixtures/agent-source/main.py mentions this key only in a comment.
const COMMENTED_KEY = 'ANTHROPIC_API_KEY';
// e2e/stack/mock-daytona/server.mjs: the one agent every scan finds.
const FOUND_AGENT = 'support_agent';
// frontend/src/sections/agent-playground/connect/ConnectAgentSteps.jsx AgentDetails: the Name
// field's label is not linked to its input, so its placeholder is the only stable handle.
const NAME_PLACEHOLDER = 'Support agent';
const UI_READY = 60_000;

interface PreflightResponse {
  ready_to_submit: boolean;
  credentials: { requirements: { environment_name: string; required: boolean; status: string }[] };
}

test(
  'AGENT-E2E-003: connecting an agent from a folder asks only for the keys its code reads, then Connect enables',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'AGENT-E2E-003',
      area: 'agents',
      userGoal: 'A developer connects an agent from a folder, and the check asks only for the keys the agent\'s code reads before Connect enables',
      steps: [
        'open Agents › New agent › Upload a folder',
        'choose the agent folder and wait for the upload',
        'find agents and pick the one found',
        'name it and run preflight',
        'see preflight pass without asking for a key the code only mentions in a comment',
        'connect the agent and land on its page',
      ],
      backendChecks: [
        'the preflight response the page received is ready to submit and does not name the commented key',
        'the scan of the uploaded folder reached found in PG simulate_agent_scan, scoped to the actor org',
        'exactly one PG simulate_agent_definition row has the minted name in the actor org after Connect',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(360_000);
    const agentName = `e2e-agent3-${testInfo.workerIndex}-${Date.now().toString(36)}`;

    let sourceId = '';
    await test.step('upload the agent folder', async () => {
      await page.goto(CONNECT_UPLOAD, { waitUntil: 'domcontentloaded' });
      const uploaded = page.waitForResponse(
        (r) => r.url().endsWith(SOURCES_PATH) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.locator('input[type="file"][webkitdirectory]').setInputFiles(AGENT_SOURCE_DIR);
      const response = await uploaded;
      expect(response.status()).toBe(201);
      sourceId = (await response.json()).source_id;
      await testInfo.attach('seeded-source', { contentType: 'application/json',
        body: JSON.stringify({ sourceId, agentName, organizationId: actor.organizationId }) });
    }, { timeout: UI_READY });

    await test.step('find the agent in the folder', async () => {
      await page.getByRole('button', { name: 'Find agents' }).click();
      await expect(page.getByText('We found 1 agent')).toBeVisible({ timeout: UI_READY });
      await page.getByText(FOUND_AGENT, { exact: true }).click();
      await page.getByRole('button', { name: 'Next' }).click();
    }, { timeout: UI_READY + POLL.ASYNC_JOB.timeout });

    await test.step('scan reached found in storage', async () => {
      const rows = await probe.pg<{ status: string }>(
        `SELECT status FROM simulate_agent_scan
          WHERE organization_id = $1 AND source->>'archive_artifact_id' = $2`,
        [actor.organizationId, sourceId]);
      expect(rows.map((r) => r.status)).toEqual(['found']);
    });

    let preflight: PreflightResponse | undefined;
    await test.step('name it and run preflight', async () => {
      const nameField = page.getByPlaceholder(NAME_PLACEHOLDER, { exact: true });
      await expect(nameField).toHaveValue(AGENT_SOURCE_NAME, { timeout: UI_READY });
      await nameField.fill(agentName);
      await expect(nameField).toHaveValue(agentName);
      const checked = page.waitForResponse(
        (r) => r.url().endsWith(PREFLIGHT_PATH) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Run preflight' }).click();
      const response = await checked;
      const body = await response.text();
      await testInfo.attach('preflight', { contentType: 'application/json', body });
      expect(response.status(), body).toBe(200);
      preflight = JSON.parse(body);
    }, { timeout: UI_READY });

    await test.step('preflight asks only for keys the code reads', async () => {
      expect(preflight?.ready_to_submit).toBe(true);
      const keys = preflight?.credentials.requirements.map((r) => r.environment_name);
      expect(keys).not.toContain(COMMENTED_KEY);
      await expect(page.getByText(COMMENTED_KEY)).toHaveCount(0);
    });

    await test.step('connect the agent', async () => {
      const connect = page.getByRole('button', { name: 'Connect agent' });
      await expect(connect).toBeEnabled({ timeout: UI_READY });
      await expect(page.getByPlaceholder(NAME_PLACEHOLDER, { exact: true })).toHaveValue(agentName);
      await connect.click();
      await expect(page).toHaveURL(/\/dashboard\/agents\/connected\/[0-9a-f-]{36}/, { timeout: UI_READY });
      await expect(page.getByText(agentName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('one agent definition with the minted name', async () => {
      const rows = await probe.pg<{ id: string }>(
        `SELECT id FROM simulate_agent_definition
          WHERE organization_id = $1 AND agent_name = $2 AND deleted = false`,
        [actor.organizationId, agentName]);
      expect(rows).toHaveLength(1);
      await testInfo.attach('connected-agent', { contentType: 'application/json', body: JSON.stringify(rows[0]) });
    });
  },
);
