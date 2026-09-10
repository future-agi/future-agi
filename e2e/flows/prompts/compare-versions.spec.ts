import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const CUSTOM_MODEL_PATH = '/model-hub/custom_models/create/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const MOCK_LLM_BASE = process.env.E2E_MOCK_LLM_BASE ?? 'http://mock-llm:8080/v1';
const MOCK_LLM_KEY = 'local-dev-only-shared-secret-replace-me';
const UI_READY = 60_000;

const CREATE_DRAFT_BODY = {
  name: '',
  prompt_config: [
    {
      messages: [
        { role: 'system', content: [{ type: 'text', text: '' }] },
        { role: 'user', content: [{ type: 'text', text: '' }] },
      ],
    },
  ],
};

interface DraftEnvelope {
  result: { root_template: string; name: string };
}

test(
  'PROMPT-E2E-013: compare mode runs two versions side by side and shows each output',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-013',
      area: 'prompts',
      userGoal:
        'A user compares two prompt versions in the workbench and gets a distinct run output for each side by side',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'author the first message and select the model',
        'open Compare to add a second panel',
        'give the second panel its own message and select the model',
        'run both panels',
        'read both output panels',
      ],
      backendChecks: [
        'both compare panels produce a run output (two echoes visible)',
        'the first panel output echoes the first message',
        'the second panel output echoes the second message',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userV1 = `e2e-cmp1-${suffix}`;
    const userV2 = `e2e-cmp2-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      await actor.api.post(CUSTOM_MODEL_PATH, {
        model_provider: 'openai',
        model_name: modelName,
        input_token_cost: 0,
        output_token_cost: 0,
        config_json: { key: MOCK_LLM_KEY, api_base: MOCK_LLM_BASE },
      });
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userV1, userV2, modelName }),
      contentType: 'application/json',
    });

    const userEditors = page.locator('[data-testid="prompt-card-1"] .ql-editor');

    const waitAutoSave = () =>
      page.waitForResponse((r) => {
        try {
          return (
            r.url().includes(RUN_TEMPLATE_FRAGMENT) &&
            r.ok() &&
            !(r.request().postDataJSON() as { is_run?: unknown }).is_run
          );
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

    const pickModelInOpenPicker = async () => {
      const search = page.getByRole('textbox', { name: 'Select model' });
      await expect(search).toBeVisible({ timeout: UI_READY });
      await search.fill(modelName);
      // Options are MUI MenuItems (role menuitem); an already-selected panel's
      // model name is a plain paragraph, so scoping to the menuitem picks the
      // real, clickable option and never the label.
      const option = page.getByRole('menuitem').filter({ hasText: modelName }).last();
      await expect(option).toBeVisible({ timeout: UI_READY });
      await option.click();
    };

    await test.step('UI: author the first panel and select its model', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(userEditors.first()).toBeVisible({ timeout: UI_READY });
      const saved = waitAutoSave();
      await userEditors.first().click();
      await page.keyboard.type(userV1);
      await saved;
      // First Run click opens the picker (no model yet).
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await pickModelInOpenPicker();
    });

    await test.step('UI: open Compare and set up the second panel', async () => {
      await page.getByText('More', { exact: true }).click();
      await page.getByRole('button', { name: 'Compare' }).click();

      // The second panel's user editor is the 2nd prompt-card-1 .ql-editor.
      const v2Editor = userEditors.nth(1);
      await expect(v2Editor).toBeVisible({ timeout: UI_READY });
      await v2Editor.click();
      await page.keyboard.press('ControlOrMeta+a');
      const saved = waitAutoSave();
      await page.keyboard.type(userV2);
      await saved;

      // The second panel has no model yet ("Select Model"); open its picker and
      // pick the same seeded model. Only the unset panel shows "Select Model".
      await page.getByText('Select Model', { exact: true }).click();
      await pickModelInOpenPicker();
    });

    await test.step('UI: run both panels and read the outputs', async () => {
      // Each compare panel runs from its own "Run" button (CompareInputSection
      // -> saveAndRun(index)). A panel with no model shows "Select a model to
      // Run Prompt" instead, so exactly two "Run" buttons also confirms both
      // panels have their model set.
      const panelRuns = page.getByRole('button', { name: 'Run', exact: true });
      await expect(panelRuns).toHaveCount(2, { timeout: UI_READY });
      await panelRuns.nth(0).click();
      await expect(page.getByText(/^echo:/)).toHaveCount(1, { timeout: UI_READY });
      // First panel finished (its button reverts to "Run"); run the second.
      await page.getByRole('button', { name: 'Run', exact: true }).nth(1).click();
      await expect(page.getByText(/^echo:/)).toHaveCount(2, { timeout: UI_READY });
    });

    await test.step('check 1: both panels produced an output', async () => {
      await expect(page.getByText(/^echo:/)).toHaveCount(2, { timeout: UI_READY });
    });

    await test.step('check 2: the first panel echoes the first message', async () => {
      await expect(page.getByText(new RegExp(`^echo:.*${userV1}`))).toBeVisible({ timeout: UI_READY });
    });

    await test.step('check 3: the second panel echoes the second message', async () => {
      await expect(page.getByText(new RegExp(`^echo:.*${userV2}`))).toBeVisible({ timeout: UI_READY });
    });
  },
);
