import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   getNameChange(id) -> /model-hub/prompt-templates/{id}/save-name/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const SAVE_NAME_FRAGMENT = '/save-name/';

const UI_READY = 60_000;

// The create-draft default body — same shape flows/prompts/author-and-commit
// pins from frontend/src/sections/workbench/constant.js.
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
interface TemplateRow {
  name: string;
}

// PromptActions.jsx (~L513-530) renders the rename trigger as a bare
// IconButton wrapping <SvgColor src="/assets/icons/ic_edit_pencil.svg">.
// SvgColor (src/components/svg-color/svg-color.jsx) paints the icon via an
// emotion-generated `mask` CSS rule, not an inline/queryable DOM attribute,
// and the IconButton carries no aria-label or test id. It also isn't the
// only icon-only button in the toolbar — MoreActions.jsx and
// SaveAndCommit.jsx render their own — so a generic
// `button:has(.svg-color)` locator is ambiguous. The icon path itself is
// unique across every component mounted on this route (grep for
// ic_edit_pencil.svg under sections/workbench turns up only this one), so
// resolve it by its *computed* mask image and click the enclosing button
// directly.
async function clickRenamePencil(page: Page) {
  const handle = await page.waitForFunction(
    () => {
      const spans = Array.from(document.querySelectorAll('span.svg-color'));
      const span = spans.find((el) => {
        const style = getComputedStyle(el);
        const mask =
          style.getPropertyValue('mask-image') ||
          style.getPropertyValue('-webkit-mask-image');
        return mask.includes('ic_edit_pencil');
      });
      return span?.closest('button') ?? null;
    },
    undefined,
    { timeout: UI_READY, polling: 250 },
  );
  const button = handle.asElement();
  if (!button) throw new Error('rename pencil button not found');
  await button.click();
}

test(
  'PROMPT-E2E-022: renaming a prompt from the editor header persists the new name',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-022',
      area: 'prompts',
      userGoal:
        'A user renames a prompt from its editor header (distinct from renaming the list row), and the new name is persisted to the template',
      steps: [
        'seed a new draft prompt',
        'open the workbench editor for the draft',
        'click the pencil next to the prompt name to reveal the rename field',
        'type a new name and press Enter',
        'wait for the rename to save',
        'read the saved template',
      ],
      backendChecks: [
        'model_hub_prompttemplate.name equals the minted name, scoped by the template id and organization',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const newName = `e2e-renamed-${suffix}`;

    const draft = await test.step('seed: create a new draft', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, newName }),
      contentType: 'application/json',
    });

    await test.step('UI: rename from the editor header', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });

      await clickRenamePencil(page);

      // PromptActions.jsx swaps the name Typography for a MUI TextField (a
      // real <input>) once editName is true. The only other textbox-role
      // elements on the page are the Quill .ql-editor cards, which are
      // contenteditable divs (implicit role="textbox" too, but not <input>
      // tags) — intersecting the two locators isolates the rename input.
      const nameInput = page.getByRole('textbox').and(page.locator('input'));
      await expect(nameInput).toBeVisible({ timeout: UI_READY });

      const saved = page.waitForResponse(
        (r) => r.url().includes(SAVE_NAME_FRAGMENT) && r.ok(),
        { timeout: UI_READY },
      );
      await nameInput.fill(newName);
      await nameInput.press('Enter');
      await saved;
    });

    const template = await test.step('storage: the renamed template', async () => {
      const rows = await probe.pg<TemplateRow>(
        `SELECT name FROM model_hub_prompttemplate
          WHERE id = $1 AND organization_id = $2`,
        [draft.root_template, actor.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the template name was persisted', () => {
      expect(template.name).toBe(newName);
    });
  },
);
