import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';

const UI_READY = 60_000;

// The create-draft default body. Pinned from frontend/src/sections/workbench/
// constant.js (createDraftPayload + DefaultMessages): a new draft is seeded
// with exactly two empty cards — a system card at index 0 and a user card at
// index 1.
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
  id: string;
}

test(
  'PROMPT-E2E-025: Add New Prompt from the editor toolbar opens a second, distinct draft',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-025',
      area: 'prompts',
      userGoal:
        "A user editing one prompt clicks the editor toolbar's Add New Prompt control and lands on a brand new, separate draft — not the one they started from",
      steps: [
        'seed a draft prompt and open its editor',
        "click the toolbar's Add New Prompt control",
        'wait for the create-draft response and capture the new root_template id',
        'observe the URL navigate to the new draft',
      ],
      backendChecks: [
        'the URL now points at the new root_template id, not the original one',
        'a new, non-deleted model_hub_prompttemplate row exists for the org under the new id',
        'the new id is distinct from the id the flow started from',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const draft = await test.step('seed: create the starting draft', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft }),
      contentType: 'application/json',
    });

    await test.step('UI: open the seeded draft', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });
    });

    const newId = await test.step('UI: click Add New Prompt and capture the new draft id', async () => {
      // PromptActions.jsx ~334-401: a CustomTooltip-wrapped Box (title="Add New
      // Prompt") calls handleWritePrompt -> createDraft(createDraftPayload),
      // which on success navigate()s (replace:true) to
      // /dashboard/workbench/create/<new root_template>. MUI Tooltip's default
      // describeChild=false sets aria-label={title} on the cloned child, so the
      // Box is reachable by that label even though it isn't a <button>.
      const created = page.waitForResponse(
        (r) => r.url().includes(CREATE_DRAFT_PATH) && r.request().method() === 'POST' && r.ok(),
        { timeout: UI_READY },
      );
      await page.getByLabel('Add New Prompt').click();
      const response = await created;
      const body = (await response.json()) as DraftEnvelope;
      return body.result.root_template;
    });

    await test.step('check 1: the URL navigated to the new draft', async () => {
      await expect(page).toHaveURL(new RegExp(newId), { timeout: UI_READY });
    });

    const newTemplate = await test.step('storage: the new template row exists, distinct from the original', async () => {
      expect(newId).not.toBe(draft.root_template);
      const rows = await probe.pg<TemplateRow>(
        `SELECT id FROM model_hub_prompttemplate
          WHERE id = $1 AND organization_id = $2 AND deleted = false`,
        [newId, actor.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 2: the new template row is a distinct row from the starting draft', () => {
      expect(newTemplate.id).not.toBe(draft.root_template);
    });
  },
);
