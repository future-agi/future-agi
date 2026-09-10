import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   runTemplatePrompt -> /model-hub/prompt-templates/{id}/run_template/  (auto-save posts here with is_run:false)
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';

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
  'PROMPT-E2E-026: copying a message card from its kebab menu writes the exact card text to the clipboard',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-026',
      area: 'prompts',
      userGoal:
        "A user opens a message card's kebab menu and clicks Copy, and the card's own text lands on the system clipboard, with the card left untouched",
      steps: [
        'seed a new draft prompt and open its editor',
        'type distinct text into the user card',
        'wait for the editor to auto-save the authored content',
        "open the user card's kebab menu",
        'click Copy',
      ],
      backendChecks: [
        // Copy itself fires no request (PromptSection.jsx onCopyPrompt is a
        // pure client-side navigator.clipboard.writeText — confirmed by
        // reading the source before writing this flow, see the comment at the
        // click step). What's actually verifiable is the browser-observable
        // state Copy produces:
        'the OS clipboard holds exactly the user card\'s text (Quill trailing-newline normalised)',
        'the success snackbar "Prompt copied to clipboard" is shown',
        'the card count is unchanged at 2 — Copy does not duplicate the card',
      ],
    }),
  },
  async ({ page, actor }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-user-${suffix}`;

    const draft = await test.step('seed: create a new draft', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userText }),
      contentType: 'application/json',
    });

    // Chromium withholds Clipboard API access without an explicit grant, even
    // on http://localhost (treated as a secure origin). Grant before
    // navigating so the app's own navigator.clipboard.writeText call (and our
    // readback) both succeed.
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], {
      origin: E2E.appUrl,
    });

    const userCard = page.locator('[data-testid="prompt-card-1"]');
    const userEditor = userCard.locator('.ql-editor');

    await test.step('UI: author the user card', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const autoSaved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: SnapshotMessage[] }>;
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

    await test.step('UI: open the kebab menu and click Copy', async () => {
      // PromptCardTopSection.jsx PromptMenu: an unlabelled ellipsis IconButton
      // — the last <button> inside the card (drag handle first, then the
      // optional generate/improve/attach buttons, the menu last) — opens a
      // Menu of MenuItems from PROMPT_EDITOR_OPTIONS (common.js): Copy,
      // Maximize, Delete. Its onClick calls onCopyPrompt, which
      // PromptSection.jsx wires to a purely client-side handler (no request):
      // it joins the card's text content blocks and calls
      // navigator.clipboard.writeText(...), then shows the "Prompt copied to
      // clipboard" snackbar. It does NOT duplicate the card — the PROMPT-E2E-026
      // premise assuming duplication was wrong; confirmed by reading
      // PromptSection.jsx's onCopyPrompt (~line 332) before writing this flow.
      await userCard.getByRole('button').last().click();
      const copyOption = page.getByRole('menuitem', { name: 'Copy' });
      await expect(copyOption).toBeVisible({ timeout: UI_READY });
      await copyOption.click();
    });

    await test.step('check 1: the success snackbar confirms the clipboard write', async () => {
      await expect(page.getByText('Prompt copied to clipboard')).toBeVisible({
        timeout: UI_READY,
      });
    });

    await test.step('check 2: the clipboard holds exactly the card text', async () => {
      const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
      // Quill always keeps a trailing "\n" in its document, which the saved
      // {type:"text",text} block (and therefore the copy handler's join)
      // carries along — normalise that Quill artifact the same way
      // author-and-commit.spec.ts's messageText() does, then compare exactly.
      expect(clipboardText.replace(/\n+$/, '')).toBe(userText);
    });

    await test.step('check 3: Copy did not duplicate the card', async () => {
      await expect(page.locator('[data-testid^="prompt-card-"]')).toHaveCount(2);
    });
  },
);
