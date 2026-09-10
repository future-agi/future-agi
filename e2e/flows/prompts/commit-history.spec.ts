import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedMockCustomModel, selectModel } from '../../lib/models';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   runTemplatePrompt -> /model-hub/prompt-templates/{id}/run_template/  (auto-save posts here with is_run:false)
//   commitSavePrompt  -> /model-hub/prompt-templates/{id}/commit/
//   getPromptVersions -> /model-hub/prompt-history-executions/  (the History drawer's
//     load; VersionHistoryDrawer.jsx sends ?is_commit=true only when the
//     "Commit History" tab is active — PromptHistoryExecutionViewSet filters to
//     rows with a non-empty commit_message)
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';
const HISTORY_PATH = '/model-hub/prompt-history-executions/';

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
  id: string;
  template_version: string;
  commit_message: string;
  is_draft: boolean;
}
interface HistoryExecutionRow {
  template_version: string;
  commit_message: string;
  is_draft: boolean;
}
interface HistoryListEnvelope {
  results: HistoryExecutionRow[];
}

test(
  'PROMPT-E2E-027: a committed version shows up under the History drawer\'s Commit History tab',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-027',
      area: 'prompts',
      userGoal:
        'A user commits a version, opens the History drawer, and switches to the Commit History tab to see that version listed',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor, author a user message, select the model, run',
        'commit one version with a minted commit message',
        'expand the toolbar and open History',
        'switch to the Commit History tab',
        'wait for the is_commit-filtered list to load',
      ],
      backendChecks: [
        'GET /model-hub/prompt-history-executions/?template_id=<id>&is_commit=true returns exactly the committed version, carrying the minted commit_message',
        "the drawer's Commit History tab renders exactly one row, labelled with that version's template_version",
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-user-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-commit-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      await seedMockCustomModel(actor.api, modelName);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userText, modelName, commitMsg }),
      contentType: 'application/json',
    });

    await test.step('UI: author, run and commit one version', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });

      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const autoSaved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: Array<{ content?: Array<{ text?: string }> }> }>;
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

      await selectModel(page, modelName);
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await expect(page.getByText(/^echo:/).last()).toBeVisible({ timeout: UI_READY });

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
        `SELECT pv.id, pv.template_version, pv.commit_message, pv.is_draft
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('UI: open History and switch to the Commit History tab', async () => {
      // MoreActions.jsx collapses the History/Compare buttons behind a toggle
      // button whose visible label is "More" but whose CustomTooltip title
      // ("Expand"/"Minimize") is what MUI surfaces as its accessible name.
      await page.getByRole('button', { name: 'Expand' }).click();
      await page.getByRole('button', { name: 'History' }).click();
      await expect(page.locator('.MuiDrawer-paper')).toBeVisible({ timeout: UI_READY });

      // VersionHistoryDrawer.jsx's CustomAgentTabs renders MUI Tabs -> role=tab,
      // labelled from the tabs array ("History" / "Commit History"). Only the
      // Commit History tab sends ?is_commit=true; register the wait before the
      // click that triggers it.
      const historyLoaded = page.waitForResponse(
        (r) => r.url().includes(HISTORY_PATH) && r.url().includes('is_commit=true') && r.ok(),
        { timeout: UI_READY },
      );
      await page.getByRole('tab', { name: 'Commit History' }).click();
      await historyLoaded;
    });

    await test.step('check 1: the Commit History tab shows exactly the committed version', async () => {
      // VersionCard.jsx reads `version?.commitMessage` (camelCase) but the
      // list endpoint's serializer returns `commit_message` (snake_case) — that
      // field is never populated client-side, so the commit-message text never
      // renders here. Anchor on the version label instead, scoped to the
      // drawer's Paper (a stable MUI structural class) so the top-bar version
      // chip elsewhere on the page can't match.
      const drawer = page.locator('.MuiDrawer-paper');
      await expect(drawer.getByText(version.template_version, { exact: true })).toHaveCount(1, {
        timeout: UI_READY,
      });
      await expect(drawer.getByText('Draft')).toHaveCount(0);
    });

    const history = await test.step('storage: the is_commit-filtered history list', async () => {
      const body = await actor.api.get<HistoryListEnvelope>(HISTORY_PATH, {
        template_id: draft.root_template,
        is_commit: 'true',
      });
      return body;
    });

    await test.step('check 2: the API lane carries exactly the minted commit message and version', () => {
      expect(history.results).toHaveLength(1);
      expect(history.results[0].commit_message).toBe(commitMsg);
      expect(history.results[0].template_version).toBe(version.template_version);
      expect(history.results[0].is_draft).toBe(false);
    });
  },
);
