import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt) — same
// endpoints PROMPT-E2E-006 uses.
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const CUSTOM_MODEL_PATH = '/model-hub/custom_models/create/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';

// Reach the mock the same way PROMPT-E2E-006 does: a uniquely-named custom model
// pointed straight at the mock (managed default mock-llm:8080; the dev-attach
// spike overrides to the host mock).
const MOCK_LLM_BASE = process.env.E2E_MOCK_LLM_BASE ?? 'http://mock-llm:8080/v1';
const MOCK_LLM_KEY = 'local-dev-only-shared-secret-replace-me';

const UI_READY = 60_000;

// create-draft default body (constant.js DefaultMessages): a fresh draft has an
// empty system card (index 0) and an empty user card (index 1).
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
// prompt_config_snapshot.configuration keys pinned from a real committed version
// (model_hub_promptversion.prompt_config_snapshot->configuration): the workbench
// stores template_format as snake_case, default "mustache"
// (normalizeConfigurationForSave, common.js).
interface PromptConfigSnapshot {
  configuration?: { model?: string; template_format?: string } & Record<
    string,
    unknown
  >;
}
interface VersionRow {
  prompt_config_snapshot: PromptConfigSnapshot;
  is_draft: boolean;
  commit_message: string;
}

test(
  'PROMPT-E2E-007: the template format chosen in the editor is saved on the committed version',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-007',
      area: 'prompts',
      userGoal:
        'A user switches the prompt template format in the workbench editor, runs, and commits, and the saved version records the chosen format and model',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the workbench editor for the draft',
        'type a user message',
        'select the seeded model',
        'switch the template format from Mustache to Jinja',
        'run the prompt',
        'open the Commit dialog, enter a commit message, and confirm',
        'read the committed version',
      ],
      backendChecks: [
        'prompt_config_snapshot.configuration.template_format is "jinja" (the chosen format, not the "mustache" default)',
        'prompt_config_snapshot.configuration.model is the seeded model',
        'commit finalizes the version: is_draft is false and the commit_message is stored',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-user-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-cfg-${suffix}`;

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
      body: JSON.stringify({ ...draft, userText, modelName, commitMsg }),
      contentType: 'application/json',
    });

    await test.step('UI: type a user message', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      const userEditor = page.locator(
        '[data-testid="prompt-card-1"] .ql-editor',
      );
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const autoSaved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{
              messages?: Array<{ content?: Array<{ text?: string }> }>;
            }>;
          };
          if (posted.is_run) return false;
          const messages = posted.prompt_config?.[0]?.messages ?? [];
          return messages.some((m) =>
            (m.content ?? []).some((c) => (c.text ?? '').includes(userText)),
          );
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

      await userEditor.click();
      await page.keyboard.type(userText);
      await autoSaved;
    });

    await test.step('UI: select the model and switch template format to Jinja', async () => {
      // First Run click opens the model picker (no model selected yet).
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const search = page.getByRole('textbox', { name: 'Select model' });
      await expect(search).toBeVisible({ timeout: UI_READY });
      await search.fill(modelName);
      const option = page.getByText(modelName, { exact: true });
      await expect(option).toBeVisible({ timeout: UI_READY });
      await option.click();

      // TemplateFormatSelector.jsx: a button labelled with the current format
      // ("Mustache" by default) opens a menu of Mustache / Jinja items.
      await page.getByRole('button', { name: /Mustache/ }).click();
      await page.getByRole('menuitem', { name: /Jinja/ }).click();
      // The trigger now reflects the choice.
      await expect(
        page.getByRole('button', { name: /Jinja/ }),
      ).toBeVisible({ timeout: UI_READY });
    });

    await test.step('UI: run the prompt', async () => {
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await expect(page.getByText(/^echo:/)).toBeVisible({ timeout: UI_READY });
    });

    await test.step('UI: commit the version with a message', async () => {
      await page
        .getByRole('button', { name: 'Commit', exact: true })
        .first()
        .click();
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
        `SELECT pv.prompt_config_snapshot, pv.is_draft, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the chosen template format is saved', () => {
      expect(version.prompt_config_snapshot.configuration?.template_format).toBe(
        'jinja',
      );
    });

    await test.step('check 2: the snapshot records the seeded model', () => {
      expect(version.prompt_config_snapshot.configuration?.model).toBe(
        modelName,
      );
    });

    await test.step('check 3: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
      expect(version.commit_message).toBe(commitMsg);
    });
  },
);
