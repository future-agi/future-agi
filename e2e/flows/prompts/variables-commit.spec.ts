import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const CUSTOM_MODEL_PATH = '/model-hub/custom_models/create/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';

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
interface VersionRow {
  variable_names: Record<string, string[]>;
  is_draft: boolean;
  commit_message: string;
}

test(
  'PROMPT-E2E-010: a variable defined in the editor is saved with the committed version',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-010',
      area: 'prompts',
      userGoal:
        'A user writes a prompt with a variable, defines its value, runs, and commits, and the saved version records the variable and its value',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor and type a message containing a {{variable}}',
        'select the model and run, which opens the variables drawer',
        'enter a value for the variable and run',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the committed version variable_names contains the variable with the entered value',
        'commit finalizes the version: is_draft is false and the commit_message is stored',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const varName = 'topic';
    const varValue = `e2e-val-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-var-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
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
      body: JSON.stringify({ ...draft, varName, varValue, modelName, commitMsg }),
      contentType: 'application/json',
    });

    await test.step('UI: type a message with a variable and select the model', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const saved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as { is_run?: unknown };
          return !posted.is_run;
        } catch {
          return false;
        }
      }, { timeout: UI_READY });
      await userEditor.click();
      await page.keyboard.type(`about {{${varName}}}`);
      await saved;

      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const search = page.getByRole('textbox', { name: 'Select model' });
      await expect(search).toBeVisible({ timeout: UI_READY });
      await search.fill(modelName);
      const option = page.getByText(modelName, { exact: true });
      await expect(option).toBeVisible({ timeout: UI_READY });
      await option.click();
    });

    await test.step('UI: run, define the variable value in the drawer, run again', async () => {
      // Running with an undefined variable opens the Variables drawer
      // (PromptActions.jsx:723 setVariableDrawerOpen). Fill the value cell in
      // the AG-Grid (column col-id = the variable name).
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const cell = page.locator(`.ag-row [col-id="${varName}"]`).first();
      await expect(cell).toBeVisible({ timeout: UI_READY });
      await cell.dblclick();
      await page.keyboard.type(varValue);
      await page.keyboard.press('Enter');

      // The drawer's Save button (VariableDrawer.jsx handleSave) writes the grid
      // values into variableData and closes the drawer.
      await page.getByRole('button', { name: 'Save', exact: true }).click();

      // Run now that the variable is defined; the mock echoes the substituted
      // message, so the value appears in the output.
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await expect(page.getByText(/^echo:/).last()).toBeVisible({ timeout: UI_READY });
    });

    await test.step('UI: commit the version', async () => {
      await page.getByRole('button', { name: 'Commit', exact: true }).first().click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog
        .getByPlaceholder('Enter a commit message for this version...')
        .fill(commitMsg);
      const committed = page.waitForResponse(
        (r) => r.url().includes(COMMIT_FRAGMENT) && r.ok(),
        { timeout: UI_READY },
      );
      await dialog.getByRole('button', { name: 'Commit', exact: true }).click();
      await committed;
    });

    const version = await test.step('storage: the committed version', async () => {
      const rows = await probe.pg<VersionRow>(
        `SELECT pv.variable_names, pv.is_draft, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the variable and its value are saved', () => {
      expect(version.variable_names[varName]).toContain(varValue);
    });

    await test.step('check 2: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
      expect(version.commit_message).toBe(commitMsg);
    });
  },
);
