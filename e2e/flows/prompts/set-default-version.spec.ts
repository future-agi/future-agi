import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Endpoints pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt);
// same set PROMPT-E2E-006 uses.
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
interface SnapshotMessage {
  role: string;
  content: Array<{ type: string; text?: string }>;
}
interface VersionRow {
  is_default: boolean;
  commit_message: string;
  template_version: string;
}

test(
  'PROMPT-E2E-009: committing a second version as default moves the default and clears the first',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-009',
      area: 'prompts',
      userGoal:
        'A user marks one prompt version as the default and then makes a later version the default, and only the latest choice stays default',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor, type a message, select the model, run',
        'commit the first version and set it as default',
        'edit the message, run',
        'commit the second version and set it as default',
        'read both committed versions',
      ],
      backendChecks: [
        'the second version is is_default=true',
        'the first version is is_default=false (its default was cleared when the second took it)',
        'the two versions have different template_version identifiers',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userV1 = `e2e-v1-${suffix}`;
    const userV2 = `e2e-v2-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitV1 = `e2e-d1-${suffix}`;
    const commitV2 = `e2e-d2-${suffix}`;

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
      body: JSON.stringify({ ...draft, modelName, commitV1, commitV2 }),
      contentType: 'application/json',
    });

    const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');

    const autoSaveCarrying = (textFn: () => string) =>
      page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: SnapshotMessage[] }>;
          };
          if (posted.is_run) return false;
          const messages = posted.prompt_config?.[0]?.messages ?? [];
          return messages.some((m) =>
            (m.content ?? []).some((c) => (c.text ?? '').includes(textFn())),
          );
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

    // Run, then commit choosing "Commit and set as a default version".
    const runAndCommitDefault = async (message: string) => {
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await expect(page.getByText(/^echo:/).last()).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: 'Commit', exact: true }).first().click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog
        .getByPlaceholder('Enter a commit message for this version...')
        .fill(message);
      const committed = page.waitForResponse(
        (r) => r.url().includes(COMMIT_FRAGMENT) && r.ok(),
        { timeout: UI_READY },
      );
      await dialog
        .getByRole('button', { name: 'Commit and set as a default version', exact: true })
        .click();
      await committed;
    };

    await test.step('UI: commit v1 as default', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const saved = autoSaveCarrying(() => userV1);
      await userEditor.click();
      await page.keyboard.type(userV1);
      await saved;

      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const search = page.getByRole('textbox', { name: 'Select model' });
      await expect(search).toBeVisible({ timeout: UI_READY });
      await search.fill(modelName);
      const option = page.getByText(modelName, { exact: true });
      await expect(option).toBeVisible({ timeout: UI_READY });
      await option.click();

      await runAndCommitDefault(commitV1);
    });

    await test.step('UI: edit, then commit v2 as default', async () => {
      await userEditor.click();
      await page.keyboard.press('ControlOrMeta+a');
      const saved = autoSaveCarrying(() => userV2);
      await page.keyboard.type(userV2);
      await saved;

      await runAndCommitDefault(commitV2);
    });

    const read = async (commitMessage: string) => {
      const rows = await probe.pg<VersionRow>(
        `SELECT pv.is_default, pv.commit_message, pv.template_version
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMessage],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    };
    const v1 = await test.step('storage: read v1', () => read(commitV1));
    const v2 = await test.step('storage: read v2', () => read(commitV2));

    await test.step('check 1: the second version is the default', () => {
      expect(v2.is_default).toBe(true);
    });

    await test.step('check 2: the first version is no longer default', () => {
      expect(v1.is_default).toBe(false);
    });

    await test.step('check 3: the two versions are distinct', () => {
      expect(v2.template_version).not.toBe(v1.template_version);
    });
  },
);
