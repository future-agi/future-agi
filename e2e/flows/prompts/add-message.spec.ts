import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedMockCustomModel, selectModel } from '../../lib/models';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   runTemplatePrompt -> /model-hub/prompt-templates/{id}/run_template/  (auto-save posts here with is_run:false)
//   commitSavePrompt  -> /model-hub/prompt-templates/{id}/commit/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';

// Browser-side waits, sized the same as PROMPT-E2E-006/008.
const UI_READY = 60_000;

// A new draft: a system card at index 0 and a user card at index 1
// (frontend/src/sections/workbench/constant.js DefaultMessages).
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
  prompt_config_snapshot: { messages: SnapshotMessage[] };
  is_draft: boolean;
  commit_message: string;
}

// Same normalisation PROMPT-E2E-006 uses: Quill always keeps a trailing "\n"
// in getBlocks() output, so strip it on the received side before comparing.
function messageText(msg: SnapshotMessage): string {
  return (msg.content ?? [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text ?? '')
    .join('')
    .replace(/\n+$/, '');
}

test(
  'PROMPT-E2E-019: adding a message via Add Message is authored, run and committed as the third message',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-019',
      area: 'prompts',
      userGoal:
        'A user adds a third message to the prompt with Add Message, writes it, runs and commits, and the added message lands in the committed version',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor and author the system and user messages',
        'click Add Message to append a third (user) card',
        'type distinct text into the added card',
        'wait for the editor to auto-save the three-message state',
        'select the seeded model and run the prompt',
        'wait for the run output to echo the added message',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the run returns the mock echo of the added (last user) message',
        'commit finalizes the model_hub_promptversion row: is_draft is false',
        'prompt_config_snapshot.messages has exactly 3 entries: system, user, user',
        'messages[2] carries the text typed into the added card, under role user',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const sysText = `e2e-sys-${suffix}`;
    const userText = `e2e-user-${suffix}`;
    const addedText = `e2e-added-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-commit-add-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      await seedMockCustomModel(actor.api, modelName);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, sysText, userText, addedText, modelName, commitMsg }),
      contentType: 'application/json',
    });

    const systemEditor = page.locator('[data-testid="prompt-card-0"] .ql-editor');
    const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
    const addedEditor = page.locator('[data-testid="prompt-card-2"] .ql-editor');

    const autoSaveCarrying = (
      predicate: (messages: SnapshotMessage[]) => boolean,
    ) =>
      page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: SnapshotMessage[] }>;
          };
          if (posted.is_run) return false;
          const messages = posted.prompt_config?.[0]?.messages ?? [];
          return predicate(messages);
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

    await test.step('UI: author the system and user messages', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(systemEditor).toBeVisible({ timeout: UI_READY });
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      await systemEditor.click();
      await page.keyboard.type(sysText);
      await userEditor.click();

      const saved = autoSaveCarrying((messages) =>
        messages.some((m) => (m.content ?? []).some((c) => (c.text ?? '').includes(userText))),
      );
      await page.keyboard.type(userText);
      await saved;
    });

    await test.step('UI: click Add Message and author the added card', async () => {
      // PromptSection.jsx onAddPrompt appends { role: "user", content: [] } —
      // the new card lands at index 2.
      await page.getByRole('button', { name: 'Add Message' }).click();
      await expect(addedEditor).toBeVisible({ timeout: UI_READY });
      await expect(page.locator('[data-testid="prompt-card-2"]')).toHaveAttribute(
        'data-prompt-role',
        'user',
      );

      const saved = autoSaveCarrying(
        (messages) =>
          messages.length === 3 &&
          messages.some((m) => (m.content ?? []).some((c) => (c.text ?? '').includes(addedText))),
      );
      await addedEditor.click();
      await page.keyboard.type(addedText);
      await saved;
    });

    await test.step('UI: select the seeded model and run', async () => {
      await selectModel(page, modelName);
      await page.getByRole('button', { name: 'Run Prompt' }).click();
    });

    await test.step('check 1: the run echoes the added (last user) message', async () => {
      const runOutput = page.getByText(/^echo:/);
      await expect(runOutput).toBeVisible({ timeout: UI_READY });
      await expect(runOutput).toContainText(addedText);
    });

    await test.step('UI: commit the version', async () => {
      await page.getByRole('button', { name: 'Commit', exact: true }).first().click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByPlaceholder('Enter a commit message for this version...').fill(commitMsg);

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

    await test.step('check 2: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
    });

    await test.step('check 3: the snapshot holds exactly three messages, added last', () => {
      const messages = version.prompt_config_snapshot.messages;
      expect(messages).toHaveLength(3);
      expect(messages[0].role).toBe('system');
      expect(messages[1].role).toBe('user');
      expect(messages[2].role).toBe('user');
      expect(messageText(messages[2])).toBe(addedText);
    });
  },
);
