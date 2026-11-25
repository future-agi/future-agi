import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedConnectedAgentValues } from '../../lib/simulate-seed';

// frontend/src/routes/sections/dashboard.jsx: agents › connected/:agentId › values.
const valuesTab = (agentId: string) => `/dashboard/agents/connected/${agentId}/values`;
// frontend/src/utils/axios.js agentDefinitions.versionValues.
const valuesPath = (agentId: string, versionId: string) =>
  `/simulate/agent-definitions/${agentId}/versions/${versionId}/values/`;
// frontend/src/sections/agent-playground/connected/detail/AgentValuesTab.jsx ValueRow: one row per name.
const ROW_PREFIX = 'agent-value-';
// AgentValuesTab.jsx ValueRow: shown under a required name marked for removal.
const REQUIRED_WARNING = 'The agent needs this value: builds are refused until it has one again, and runs warn that it is missing.';
const UI_READY = 60_000;

// futureagi/simulate/serializers/response/agent_version.py AgentVersionValuesResponseSerializer.
interface ValuesResponse {
  names: { name: string; required: boolean; made_by_environment: boolean; provider_key: boolean }[];
  missing_required_names: string[];
  made_by_environment: string[];
}

test(
  'AGENT-E2E-004: an agent\'s values are listed by name, never by value, and removing a required one warns before any save',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'AGENT-E2E-004',
      area: 'agents',
      userGoal: 'A developer sees which values a connected agent holds, by name and never by value, and is warned before removing one the agent needs',
      steps: [
        'seed a connected agent whose production version holds a required value and an optional one',
        'open the agent page on its Values tab',
        'see exactly the two names, the required one marked Required',
        'mark the required name for removal',
        'see the warning that builds are refused until it has a value again, without saving',
      ],
      backendChecks: [
        'the values response the page received lists exactly the two names, required only on the first, and holds neither secret',
        'neither secret string appears anywhere in the rendered page',
        'marking a name for removal writes nothing: PG simulate_agent_version still holds one version, the seeded one, active',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    const seeded = seedConnectedAgentValues({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    // The secrets are minted test values, but still stay out of the report.
    await testInfo.attach('seeded-agent', {
      contentType: 'application/json',
      body: JSON.stringify({
        agentId: seeded.agentId,
        versionId: seeded.versionId,
        agentName: seeded.agentName,
        requiredName: seeded.requiredName,
        optionalName: seeded.optionalName,
        organizationId: actor.organizationId,
      }),
    });
    const secrets = [seeded.requiredSecret, seeded.optionalSecret];

    let body = '';
    await test.step('open the Values tab', async () => {
      const listed = page.waitForResponse(
        (r) => r.url().endsWith(valuesPath(seeded.agentId, seeded.versionId)) && r.request().method() === 'GET',
        { timeout: UI_READY });
      await page.goto(valuesTab(seeded.agentId), { waitUntil: 'domcontentloaded' });
      const response = await listed;
      body = await response.text();
      expect(response.status(), body).toBe(200);
    }, { timeout: UI_READY });

    await test.step('the response lists the names, never a value', async () => {
      for (const secret of secrets) expect(body).not.toContain(secret);
      const values = JSON.parse(body) as ValuesResponse;
      expect(values.names.map((row) => [row.name, row.required]).sort()).toEqual(
        [[seeded.optionalName, false], [seeded.requiredName, true]].sort());
      expect(values.missing_required_names).toEqual([]);
    });

    const rows = page.locator(`[data-testid^="${ROW_PREFIX}"]`);
    const required = page.getByTestId(`${ROW_PREFIX}${seeded.requiredName}`);
    await test.step('the tab shows exactly the two names, and no value', async () => {
      await expect(rows).toHaveCount(2, { timeout: UI_READY });
      await expect(page.getByTestId(`${ROW_PREFIX}${seeded.optionalName}`)).toBeVisible();
      await expect(required.getByText('Required', { exact: true })).toBeVisible();
      await expect(page.getByTestId(`${ROW_PREFIX}${seeded.optionalName}`).getByText('Required', { exact: true }))
        .toHaveCount(0);
      const text = await page.locator('body').innerText();
      for (const secret of secrets) expect(text).not.toContain(secret);
    }, { timeout: UI_READY });

    await test.step('mark the required name for removal and see the warning', async () => {
      await expect(required.getByText(REQUIRED_WARNING)).toHaveCount(0);
      await required.getByRole('button', { name: 'Remove' }).click();
      await expect(required.getByText(REQUIRED_WARNING)).toBeVisible({ timeout: UI_READY });
      await expect(required.getByRole('button', { name: 'Undo' })).toBeVisible();
    }, { timeout: UI_READY });

    await test.step('nothing was saved', async () => {
      const versions = await probe.pg<{ id: string; status: string }>(
        `SELECT id, status FROM simulate_agent_version
          WHERE agent_definition_id = $1 AND deleted = false`,
        [seeded.agentId]);
      expect(versions).toEqual([{ id: seeded.versionId, status: 'active' }]);
    });
  },
);
