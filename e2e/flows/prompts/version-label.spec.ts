import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedMockCustomModel, selectModel } from '../../lib/models';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt);
// same set PROMPT-E2E-006 uses, plus the label endpoints this flow adds:
//   createPromptLabel     -> POST /model-hub/prompt-labels/
//   assignMultipleLabels  -> POST /model-hub/prompt-labels/assign-multiple-labels/
//   (this is the endpoint LabelSelectContent.jsx actually calls on Save —
//   NOT `assignLabels`/assign-label-by-id, which is unused by this UI path)
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';
const CREATE_LABEL_FRAGMENT = '/prompt-labels/';
const ASSIGN_LABELS_FRAGMENT = '/assign-multiple-labels/';

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
  commit_message: string;
}
interface LabelRow {
  name: string;
}

// LabelDropdown.jsx (VersionHistory/LabelDropdown/LabelDropdown.jsx) renders
// the per-version tag trigger as a plain Box (not a button) wrapping
// <SvgColor src="/assets/icons/ic_tag.svg">, with no aria-label or test id.
// The icon path is reused elsewhere on this route (Playground output's
// MultiResult/SingleResult also render ic_tag.svg), so resolve it the same
// way PROMPT-E2E-022 resolves the rename pencil — by computed mask image —
// but scoped to the History drawer's Paper (`.MuiDrawer-paper`, a stable
// MUI structural class) so the Playground copies never match. With exactly
// one committed version in this draft, there's exactly one match in-scope.
async function clickVersionTagIcon(page: Page) {
  const handle = await page.waitForFunction(
    () => {
      const root = document.querySelector('.MuiDrawer-paper');
      if (!root) return null;
      const spans = Array.from(root.querySelectorAll('span.svg-color'));
      const span = spans.find((el) => {
        const style = getComputedStyle(el);
        const mask =
          style.getPropertyValue('mask-image') ||
          style.getPropertyValue('-webkit-mask-image');
        return mask.includes('ic_tag');
      });
      // The span is itself inside a <div> (LabelDropdown.jsx's clickable
      // Box, default MUI component), so closest('div') always resolves —
      // clicking it (rather than the span) matches what a user actually
      // clicks.
      return span ? span.closest('div') : null;
    },
    undefined,
    { timeout: UI_READY, polling: 250 },
  );
  const el = handle.asElement();
  if (!el) throw new Error('version tag icon not found in the History drawer');
  await el.click();
}

test(
  'PROMPT-E2E-023: a custom label assigned to a committed version is stored on that version',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-023',
      area: 'prompts',
      userGoal:
        'A user creates a custom label and assigns it to a committed prompt version from the History drawer, and the version is stored carrying that label',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor, author a user message, select the model, run',
        'commit the version',
        'open History',
        'open the tag control on the committed version card',
        'create a new custom label and add it to the selection',
        'save the label assignment',
        'read the committed version and its assigned label',
      ],
      backendChecks: [
        'the minted label exists as a model_hub_promptlabel row for this organization',
        'the label is joined to the committed model_hub_promptversion row through model_hub_promptversion_labels',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-user-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-commit-${suffix}`;
    const labelName = `e2e-label-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      await seedMockCustomModel(actor.api, modelName);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userText, modelName, commitMsg, labelName }),
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

    await test.step('UI: open History and the version tag control', async () => {
      // MoreActions.jsx collapses the History/Compare buttons behind a
      // toggle button whose visible label is "More" but whose CustomTooltip
      // title ("Expand"/"Minimize") is what MUI surfaces as its accessible
      // name (confirmed via aria snapshot). Expand it before History is
      // clickable.
      await page.getByRole('button', { name: 'Expand' }).click();
      await page.getByRole('button', { name: 'History' }).click();
      await expect(page.locator('.MuiDrawer-paper')).toBeVisible({ timeout: UI_READY });
      // VersionCard.jsx reads `version?.commitMessage` (camelCase) but the
      // list endpoint's serializer returns `commit_message` (snake_case) —
      // that field is never populated client-side, so the commit-message
      // text never renders here and can't be used as a load anchor.
      // clickVersionTagIcon already polls until the tag icon mounts, which
      // only happens once the (non-skeleton) version card has rendered, so
      // it doubles as the "wait for real content" step.
      await clickVersionTagIcon(page);

      // MoreWrapper/ModalWrapper renders a real MUI Dialog (role="dialog")
      // titled "Add Tags" — LabelSelectPopover.jsx.
      const tagDialog = page.getByRole('dialog').filter({ hasText: 'Add Tags' });
      await expect(tagDialog).toBeVisible({ timeout: UI_READY });

      await tagDialog.getByRole('button', { name: 'Add custom tag' }).click();
      const nameField = tagDialog.getByPlaceholder('Enter tag name');
      await expect(nameField).toBeVisible({ timeout: UI_READY });

      const labelCreated = page.waitForResponse(
        (r) =>
          r.url().includes(CREATE_LABEL_FRAGMENT) &&
          !r.url().includes(ASSIGN_LABELS_FRAGMENT) &&
          r.request().method() === 'POST' &&
          r.ok(),
        { timeout: UI_READY },
      );
      await nameField.fill(labelName);
      await nameField.press('Enter');
      await labelCreated;

      // Creating the label adds it to the selected-tags chip row but does
      // NOT assign it yet (LabelSelectContent.jsx handleSave is a separate
      // click) — Save fires assignMultipleLabels with the selection.
      const assigned = page.waitForResponse(
        (r) => r.url().includes(ASSIGN_LABELS_FRAGMENT) && r.ok(),
        { timeout: UI_READY },
      );
      await tagDialog.getByRole('button', { name: 'Save', exact: true }).click();
      await assigned;
    });

    const version = await test.step('storage: the committed version', async () => {
      const rows = await probe.pg<VersionRow>(
        `SELECT pv.id, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    const label = await test.step('storage: the label joined to that version', async () => {
      const rows = await probe.pg<LabelRow>(
        `SELECT pl.name
           FROM model_hub_promptversion_labels pvl
           JOIN model_hub_promptlabel pl ON pl.id = pvl.promptlabel_id
          WHERE pvl.promptversion_id = $1 AND pl.organization_id = $2`,
        [version.id, actor.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the committed version carries the minted label', () => {
      expect(label.name).toBe(labelName);
    });
  },
);
