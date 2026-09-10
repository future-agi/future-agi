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
  'PROMPT-E2E-021: changing a card role to Assistant is saved and committed as that role',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-021',
      area: 'prompts',
      userGoal:
        'A user changes the role of the second card from User to Assistant via its role control, and the committed version stores that card under the assistant role',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor and author the system message',
        'open the role control on the second card and choose Assistant',
        'type text into the now-assistant card',
        'wait for the editor to auto-save the role change',
        'select the seeded model and run the prompt',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the card wrapper reflects data-prompt-role="assistant" immediately after the role change',
        'commit finalizes the model_hub_promptversion row: is_draft is false',
        'prompt_config_snapshot.messages[1].role is the lowercase string "assistant"',
        'messages[1] carries the text typed after the role change',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const sysText = `e2e-sys-${suffix}`;
    const assistantText = `e2e-assistant-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-commit-role-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      await seedMockCustomModel(actor.api, modelName);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, sysText, assistantText, modelName, commitMsg }),
      contentType: 'application/json',
    });

    const systemEditor = page.locator('[data-testid="prompt-card-0"] .ql-editor');
    const card1 = page.locator('[data-testid="prompt-card-1"]');
    const card1Editor = card1.locator('.ql-editor');

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

    await test.step('UI: author the system message', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(systemEditor).toBeVisible({ timeout: UI_READY });
      await expect(card1Editor).toBeVisible({ timeout: UI_READY });

      const saved = autoSaveCarrying((messages) =>
        messages.some((m) => (m.content ?? []).some((c) => (c.text ?? '').includes(sysText))),
      );
      await systemEditor.click();
      await page.keyboard.type(sysText);
      await saved;
    });

    await test.step('UI: change the second card role to Assistant', async () => {
      // PromptCardTopSection.jsx RoleSelection: a clickable Box showing the
      // capitalized role name opens a Menu of the remaining roles as
      // MenuItems (system is excluded once a system card exists).
      await card1.getByText('User', { exact: true }).click();
      await expect(page.getByRole('menuitem', { name: 'Assistant' })).toBeVisible({ timeout: UI_READY });
      await page.getByRole('menuitem', { name: 'Assistant' }).click();

      // PromptCard.jsx tags the wrapper data-prompt-role={role} — proves the
      // role landed in the UI state before we author into it.
      await expect(card1).toHaveAttribute('data-prompt-role', 'assistant');
    });

    await test.step('UI: type into the now-assistant card', async () => {
      const saved = autoSaveCarrying((messages) => {
        const target = messages[1];
        return (
          target?.role === 'assistant' &&
          (target.content ?? []).some((c) => (c.text ?? '').includes(assistantText))
        );
      });
      await card1Editor.click();
      await page.keyboard.type(assistantText);
      await saved;
    });

    await test.step('UI: select the seeded model and run', async () => {
      // No user-role message remains (system + assistant only); the mock
      // reply() finds no role==="user" entry and echoes an empty payload —
      // the run still completes (200), which is all Commit requires.
      await selectModel(page, modelName);
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await expect(page.getByText(/^echo:/)).toBeVisible({ timeout: UI_READY });
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

    await test.step('check 1: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
    });

    await test.step('check 2: the second message is stored under the assistant role', () => {
      const messages = version.prompt_config_snapshot.messages;
      expect(messages).toHaveLength(2);
      expect(messages[0].role).toBe('system');
      expect(messages[1].role).toBe('assistant');
      expect(messageText(messages[1])).toBe(assistantText);
    });
  },
);
