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
  prompt_config_snapshot: { messages: SnapshotMessage[] };
  is_draft: boolean;
  commit_message: string;
}

function messageText(msg: SnapshotMessage): string {
  return (msg.content ?? [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text ?? '')
    .join('')
    .replace(/\n+$/, '');
}

test(
  'PROMPT-E2E-020: deleting a message removes it from the run payload and the committed version',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-020',
      area: 'prompts',
      userGoal:
        'A user adds a third message, then deletes it from its card menu, and the run and the committed version reflect only the two remaining messages',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor and author the system and user messages',
        'click Add Message and author a third (user) card',
        'delete the added card via its top-section menu',
        'select the seeded model and run the prompt',
        'wait for the run output to echo the surviving user message',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the deleted card is removed from the DOM',
        'the run payload — and its echo — reflect only the surviving user message',
        'commit finalizes the model_hub_promptversion row: is_draft is false',
        'prompt_config_snapshot.messages has exactly 2 entries and the deleted text is absent from all of them',
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
    const commitMsg = `e2e-commit-del-${suffix}`;

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
    const addedCard = page.locator('[data-testid="prompt-card-2"]');

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

    await test.step('UI: add a third card and author it', async () => {
      await page.getByRole('button', { name: 'Add Message' }).click();
      await expect(addedEditor).toBeVisible({ timeout: UI_READY });

      const saved = autoSaveCarrying(
        (messages) =>
          messages.length === 3 &&
          messages.some((m) => (m.content ?? []).some((c) => (c.text ?? '').includes(addedText))),
      );
      await addedEditor.click();
      await page.keyboard.type(addedText);
      await saved;
    });

    await test.step('UI: delete the added card via its top-section menu', async () => {
      // PromptCardTopSection.jsx PromptMenu: an unlabelled ellipsis IconButton
      // opens a Menu whose options (PROMPT_EDITOR_OPTIONS) render as MUI
      // MenuItems — Copy, Maximize, Delete. It is the last <button> inside the
      // card (drag handle first, then the optional generate/improve/attach
      // buttons, the menu last); onRemove has no confirmation dialog.
      const deleted = autoSaveCarrying(
        (messages) =>
          messages.length === 2 &&
          !messages.some((m) => (m.content ?? []).some((c) => (c.text ?? '').includes(addedText))),
      );
      await addedCard.getByRole('button').last().click();
      await expect(page.getByRole('menuitem', { name: 'Delete' })).toBeVisible({ timeout: UI_READY });
      await page.getByRole('menuitem', { name: 'Delete' }).click();
      await deleted;

      await expect(addedCard).toHaveCount(0);
    });

    await test.step('UI: select the seeded model and run', async () => {
      await selectModel(page, modelName);
      await page.getByRole('button', { name: 'Run Prompt' }).click();
    });

    await test.step('check 1: the run echoes the surviving user message, not the deleted one', async () => {
      const runOutput = page.getByText(/^echo:/);
      await expect(runOutput).toBeVisible({ timeout: UI_READY });
      await expect(runOutput).toContainText(userText);
      await expect(runOutput).not.toContainText(addedText);
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

    await test.step('check 3: the snapshot holds exactly two messages, the deleted text is absent', () => {
      const messages = version.prompt_config_snapshot.messages;
      expect(messages).toHaveLength(2);
      expect(messages[0].role).toBe('system');
      expect(messageText(messages[0])).toBe(sysText);
      expect(messages[1].role).toBe('user');
      expect(messageText(messages[1])).toBe(userText);
      expect(messages.some((m) => messageText(m).includes(addedText))).toBe(false);
    });
  },
);
