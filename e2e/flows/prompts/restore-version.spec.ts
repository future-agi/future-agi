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
interface SnapshotMessage {
  role: string;
  content: Array<{ type: string; text?: string }>;
}

test(
  'PROMPT-E2E-011: restoring an older version brings its content back into the editor',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-011',
      area: 'prompts',
      userGoal:
        'A user who has committed two versions restores the older one from history and gets its content back in the editor',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'commit a first version with message A',
        'edit to message B and commit a second version',
        'confirm the editor is showing message B',
        'open version history and restore the first version',
        'confirm the editor now shows message A',
      ],
      backendChecks: [
        'two committed model_hub_promptversion rows exist for the template',
        'after committing v2 the editor shows message B (the latest content)',
        'after restoring v1 the editor shows message A (restore is a client-side load; its effect is in the editor)',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const msgA = `e2e-a-${suffix}`;
    const msgB = `e2e-b-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitA = `e2e-ra-${suffix}`;
    const commitB = `e2e-rb-${suffix}`;

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
      body: JSON.stringify({ ...draft, msgA, msgB, modelName, commitA, commitB }),
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

    const runAndCommit = async (message: string) => {
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
      await dialog.getByRole('button', { name: 'Commit', exact: true }).click();
      await committed;
    };

    await test.step('UI: commit version A, then edit and commit version B', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      let saved = autoSaveCarrying(() => msgA);
      await userEditor.click();
      await page.keyboard.type(msgA);
      await saved;

      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const search = page.getByRole('textbox', { name: 'Select model' });
      await expect(search).toBeVisible({ timeout: UI_READY });
      await search.fill(modelName);
      const option = page.getByText(modelName, { exact: true });
      await expect(option).toBeVisible({ timeout: UI_READY });
      await option.click();
      await runAndCommit(commitA);

      await userEditor.click();
      await page.keyboard.press('ControlOrMeta+a');
      saved = autoSaveCarrying(() => msgB);
      await page.keyboard.type(msgB);
      await saved;
      await runAndCommit(commitB);
    });

    await test.step('check: two committed versions exist', async () => {
      const rows = await probe.pg<{ n: string }>(
        `SELECT count(*) AS n FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2 AND pv.is_draft = false`,
        [draft.root_template, actor.organizationId],
      );
      // node-postgres returns count as string
      expect(Number(rows[0].n)).toBeGreaterThanOrEqual(2);
    });

    await test.step('check: the editor is showing version B', async () => {
      await expect(userEditor).toContainText(msgB, { timeout: UI_READY });
    });

    await test.step('UI: open version history and restore version A', async () => {
      await page.getByText('More', { exact: true }).click();
      await page.getByText('History', { exact: true }).click();
      // The History tab lists version cards newest-first (ordering -created_at),
      // each with a Restore button; the older version A is the last one.
      const restores = page.getByRole('button', { name: 'Restore' });
      await expect(restores.last()).toBeVisible({ timeout: UI_READY });
      await restores.last().click();
    });

    await test.step('check: the editor now shows version A', async () => {
      await expect(userEditor).toContainText(msgA, { timeout: UI_READY });
      await expect(userEditor).not.toContainText(msgB);
    });
  },
);
