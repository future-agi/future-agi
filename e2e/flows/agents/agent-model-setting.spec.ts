import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedSourceAgentModels } from '../../lib/simulate-seed';

// frontend/src/routes/sections/dashboard.jsx: agents › connected/:agentId › definition.
const definitionTab = (agentId: string) => `/dashboard/agents/connected/${agentId}/definition`;
// frontend/src/utils/axios.js agentDefinitions.storedAgent.
const storedAgentPath = (agentId: string, versionId: string) =>
  `/simulate/agent-definitions/${agentId}/versions/${versionId}/agent/`;
// e2e/lib/simulate-seed.ts seedSourceAgentModels: the default the code writes, and where it reads the setting.
const DEFAULT = 'vertex_ai/gemini-3.7-flash';
const NAMED = 'gpt-4.1';
const PATH = 'agent.py';
const LINE = 19;
// frontend/src/sections/agent-playground/connected/detail/definitionRows.js modelRow: the label this PR retired.
const OLD_LABEL = 'Model named in the code';
const UI_READY = 60_000;

// futureagi/simulate/serializers/response/agent_setup.py StoredAgentResponseSerializer (the parts read here).
interface StoredAgentResponse {
  agent: {
    model: { name: string } | null;
    extension: { model_source: { setting: string; path: string | null; line: number | null } | null };
  };
}

test(
  'AGENT-E2E-005: an agent that reads its model from a setting shows the setting, where it is read and its default, never the default as the model',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'AGENT-E2E-005',
      area: 'agents',
      userGoal: 'A developer whose agent reads its model from a setting sees which setting sets it, where the code reads it and its default, and an agent with no setting keeps its plain Model row',
      steps: [
        'seed two connected agents read from their code: one whose model is read from a minted setting, one with a model and no setting',
        'open the first agent page on its Definition tab',
        'see the Model row say which setting sets the model, where the code reads it, and its default',
        'open the second agent page on its Definition tab',
        'see the model as stored, with no setting and not under the retired label',
      ],
      backendChecks: [
        'the stored-agent response for the first agent carries model_source with exactly the minted setting, agent.py and line 19, and the default as model.name',
        'the stored-agent response for the second agent carries no model_source',
        'the first page shows the exact sentence naming the minted setting and agent.py:19, never the bare default, and no row has the retired label',
        'the second page shows gpt-4.1 exactly, and no row has the retired label',
      ],
    }),
  },
  async ({ page, actor }, testInfo) => {
    const seeded = seedSourceAgentModels({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    await testInfo.attach('seeded-agents', {
      contentType: 'application/json',
      body: JSON.stringify({ ...seeded, organizationId: actor.organizationId }),
    });
    const sentence = `Set by ${seeded.setting} in ${PATH}:${LINE} (default ${DEFAULT})`;

    const open = async (agent: { agentId: string; versionId: string }): Promise<StoredAgentResponse> => {
      const stored = page.waitForResponse(
        (r) => r.url().endsWith(storedAgentPath(agent.agentId, agent.versionId)) && r.request().method() === 'GET',
        { timeout: UI_READY });
      await page.goto(definitionTab(agent.agentId), { waitUntil: 'domcontentloaded' });
      const response = await stored;
      const body = await response.text();
      expect(response.status(), body).toBe(200);
      return JSON.parse(body) as StoredAgentResponse;
    };

    let withSetting: StoredAgentResponse | undefined;
    await test.step('open the Definition tab of the agent that reads its model from a setting', async () => {
      withSetting = await open(seeded.withSetting);
    }, { timeout: UI_READY });

    await test.step('the response names the setting and where it is read', async () => {
      expect(withSetting?.agent.extension.model_source).toEqual({ setting: seeded.setting, path: PATH, line: LINE });
      expect(withSetting?.agent.model?.name).toBe(DEFAULT);
    });

    await test.step('the Model row names the setting, never the default as the model', async () => {
      await expect(page.getByText(sentence, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(DEFAULT, { exact: true })).toHaveCount(0);
      await expect(page.getByText(OLD_LABEL, { exact: true })).toHaveCount(0);
    }, { timeout: UI_READY });

    let withoutSetting: StoredAgentResponse | undefined;
    await test.step('open the Definition tab of the agent with no setting', async () => {
      withoutSetting = await open(seeded.withoutSetting);
    }, { timeout: UI_READY });

    await test.step('the response carries no setting', async () => {
      expect(withoutSetting?.agent.extension.model_source).toBeNull();
      expect(withoutSetting?.agent.model?.name).toBe(NAMED);
    });

    await test.step('the Model row shows the stored model, under no retired label', async () => {
      await expect(page.getByText(NAMED, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(OLD_LABEL, { exact: true })).toHaveCount(0);
      await expect(page.getByText(/^Set by /)).toHaveCount(0);
    }, { timeout: UI_READY });
  },
);
