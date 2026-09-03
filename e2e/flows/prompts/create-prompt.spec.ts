import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   promptExecutions  -> /model-hub/prompt-executions/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const LIST_PATH = '/model-hub/prompt-executions/';
// Browser-side waits. The local stack slows several-fold when specs run in
// parallel, so the 10s expect default is not a first-paint budget; sized like
// the observe flows' own constant.
const UI_READY = 60_000;

// The list endpoint is DRF-paginated ({count,total_pages,results}), not the
// {result:{table}} envelope probe.apiList hard-codes — so the API lane here
// goes through actor.api directly. Pinned from FolderListView.jsx:70-74.
interface PromptRow { id: string; name: string; type: string }
interface ListPage { count: number; total_pages: number; results: PromptRow[] }
interface DraftEnvelope { result: { root_template: string; name: string } }

test('PROMPT-E2E-001: a prompt created from the workbench appears in All Prompts', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-001', area: 'prompts',
    userGoal: 'A user creates a new prompt from the workbench and finds it in the All Prompts list',
    steps: ['open All Prompts',
            'click Create prompt',
            'choose "Start from scratch"',
            'land on the editor for the new draft',
            'return to All Prompts',
            'read the new row'],
    backendChecks: [
      'create-draft persists a PromptTemplate in PG model_hub_prompttemplate, scoped to the actor org, deleted=false',
      'the backend names an unnamed draft Untitled-<n>, the next free number in the org',
      'the All Prompts list endpoint returns that prompt with type PROMPT',
      'the list renders exactly one row linking to the new prompt id',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Navigation plus 4 x UI_READY already passes the config's 120s default;
  // the remainder is headroom so a slow run fails on the assertion that ran
  // out rather than on the outer timeout.
  test.setTimeout(240_000);

  // The backend assigns the lowest free Untitled-N in the org, counting
  // deleted rows too — its numbering query has no deleted filter
  // (views/prompt_template.py:1231-1253). Predict it from the same rows the
  // backend reads, before the create consumes it.
  const expectedUntitled = await test.step('storage: predict the next free Untitled-N', async () => {
    const rows = await probe.pg<{ name: string }>(
      "SELECT name FROM model_hub_prompttemplate WHERE organization_id = $1 AND name LIKE 'Untitled-%'",
      [actor.organizationId]);
    const used = new Set<number>();
    for (const { name } of rows) {
      const num = Number(name.split('-')[1]);
      if (Number.isInteger(num)) used.add(num);
    }
    let n = 1;
    while (used.has(n)) n += 1;
    return `Untitled-${n}`;
  });

  const created = await test.step('UI: create a prompt from scratch', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    // An empty org renders a second "Create prompt" in the empty layout
    // (FolderListView.jsx:156-165) beside the header's own; both open the same
    // dialog, and the header's is first in the DOM.
    const createPrompt = page.getByRole('button', { name: 'Create prompt' }).first();
    await expect(createPrompt).toBeEnabled({ timeout: UI_READY });

    const drafted = page.waitForResponse(
      (r) => r.url().includes(CREATE_DRAFT_PATH) && r.ok(), { timeout: UI_READY });
    await createPrompt.click();
    // CreateNewPrompt.jsx:182-201 renders each option as a clickable Stack;
    // the click bubbles from the option's title to that handler.
    await page.getByText('Start from scratch', { exact: true }).click();
    const body = (await (await drafted).json()) as DraftEnvelope;

    // CreateNewPrompt.jsx:99-104 navigates to the editor for the new draft.
    await expect(page).toHaveURL(
      `/dashboard/workbench/create/${body.result.root_template}`, { timeout: UI_READY });
    return body.result;
  });

  await testInfo.attach('created-prompt', {
    body: JSON.stringify(created), contentType: 'application/json',
  });

  await test.step('storage: the draft is an org-scoped, undeleted PG row named Untitled-<n>', async () => {
    const rows = await probe.pg<{ name: string; deleted: boolean; prompt_folder_id: string | null }>(
      'SELECT name, deleted, prompt_folder_id FROM model_hub_prompttemplate WHERE id = $1 AND organization_id = $2',
      [created.root_template, actor.organizationId]);
    expect(rows).toHaveLength(1);
    expect(rows[0].deleted).toBe(false);
    // Nothing was filed into a folder, so the draft is loose in All Prompts.
    expect(rows[0].prompt_folder_id).toBeNull();
    // The UI sends name:"" (workbench/constant.js:22), so the backend assigns
    // the next free Untitled-N in the org (views/prompt_template.py:1231-1252).
    expect(rows[0].name).toBe(expectedUntitled);
    expect(rows[0].name).toBe(created.name);
  });

  await test.step('API: the All Prompts list returns the new prompt', async () => {
    // The params FolderListView.jsx:47-64 sends for the "all" folder.
    const listed = await actor.api.get<ListPage>(LIST_PATH, {
      send_all: 'true', page: 1, page_size: 50, name: created.name,
      sort_by: 'updated_at', sort_order: 'desc',
    });
    const mine = listed.results.filter((r) => r.id === created.root_template);
    expect(mine).toHaveLength(1);
    expect(mine[0].name).toBe(created.name);
    expect(mine[0].type).toBe('PROMPT');
  });

  await test.step('UI: All Prompts shows exactly one row for the new prompt', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    // PromptItem.jsx:55-58 links a prompt row to the editor; that href is
    // unique to the list, where a folder row's href collides with the sidebar.
    const row = page.locator('[data-testid="prompt-list"] > div').filter({
      has: page.locator(`a[href="/dashboard/workbench/create/${created.root_template}"]`),
    });
    await expect(row).toHaveCount(1, { timeout: UI_READY });
    // Exact text, not containment: the row also carries "Created by …" and a
    // timestamp, so scope to the element whose whole text is the name.
    await expect(row.getByText(created.name, { exact: true })).toHaveCount(1);
  });
});
