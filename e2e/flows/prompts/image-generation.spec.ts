import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedOpenAIProviderKey, selectModel } from '../../lib/models';

const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';
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
  prompt_config_snapshot: { configuration?: { model?: string; model_detail?: { type?: string } } };
  metadata: Array<{ revised_prompt?: string } | null> | null;
  output: string[] | null;
  is_draft: boolean;
  commit_message: string;
}

test(
  'PROMPT-E2E-014: an image-generation prompt runs against the model and commits with the image output',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-014',
      area: 'prompts',
      userGoal:
        'A user writes an image prompt, runs it against an image model, sees the generated image, and commits it',
      steps: [
        'seed a new draft prompt and an OpenAI provider key (routed to the mock)',
        'open the editor and type an image prompt',
        'select the image model',
        'run and wait for the generated image',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the run produces an image in the output panel',
        'the committed snapshot records the image model (gpt-image-1, type image_generation)',
        'the version metadata carries the model revised_prompt echoing the authored text',
        'commit finalizes the version: is_draft is false and the commit_message is stored',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-img-${suffix}`;
    const commitMsg = `e2e-imgc-${suffix}`;

    const draft = await test.step('seed: draft + OpenAI provider key (mock-routed)', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      await seedOpenAIProviderKey(actor.api);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userText, commitMsg }),
      contentType: 'application/json',
    });

    await test.step('UI: type the image prompt and select the image model', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });
      const saved = page.waitForResponse((r) => {
        try {
          return r.url().includes(RUN_TEMPLATE_FRAGMENT) && r.ok() &&
            !(r.request().postDataJSON() as { is_run?: unknown }).is_run;
        } catch {
          return false;
        }
      }, { timeout: UI_READY });
      await userEditor.click();
      await page.keyboard.type(userText);
      await saved;

      await selectModel(page, 'gpt-image-1');
    });

    await test.step('UI: run and wait for the generated image', async () => {
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      // MultiResult.jsx renders image output as <img alt="Generated image">.
      await expect(page.getByRole('img', { name: 'Generated image' })).toBeVisible({
        timeout: UI_READY,
      });
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
        `SELECT pv.prompt_config_snapshot, pv.metadata, pv.output, pv.is_draft, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2 AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the snapshot records an image-generation model', () => {
      expect(version.prompt_config_snapshot.configuration?.model).toMatch(/^gpt-image/);
      expect(version.prompt_config_snapshot.configuration?.model_detail?.type).toBe('image_generation');
    });

    await test.step('check 2: the metadata carries the model revised_prompt echoing the text', () => {
      const revised = version.metadata?.[0]?.revised_prompt ?? '';
      expect(revised).toContain(userText);
    });

    await test.step('check 3: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
      expect(version.commit_message).toBe(commitMsg);
    });
  },
);
