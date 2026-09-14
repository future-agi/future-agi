import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   promptFolder      -> /model-hub/prompt-folders/
//   movePrompt        -> /model-hub/prompt-templates/{id}/save-prompt-folder/
//   promptExecutions  -> /model-hub/prompt-executions/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const FOLDERS_PATH = '/model-hub/prompt-folders/';
const MOVE_PATH = (id: string) => `/model-hub/prompt-templates/${id}/save-prompt-folder/`;
const LIST_PATH = '/model-hub/prompt-executions/';
const UI_READY = 60_000;

// The empty two-message config the Create prompt dialog posts, pinned from
// frontend/src/sections/workbench/constant.js:1-29 (DefaultMessages).
const DEFAULT_MESSAGES = [
  { role: 'system', content: [{ type: 'text', text: '' }] },
  { role: 'user', content: [{ type: 'text', text: '' }] },
];

interface PromptRow { id: string; name: string; type: string; prompt_folder: string | null }
interface ListPage { count: number; total_pages: number; results: PromptRow[] }
interface DraftEnvelope { result: { root_template: string; name: string } }
interface FolderEnvelope { result: { id: string; name: string } }

test('PROMPT-E2E-002: a prompt filed into a folder shows up under that folder', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-002', area: 'prompts',
    userGoal: 'A user files a prompt into a folder they created, and the folder shows that prompt rather than the whole library',
    steps: ['seed a prompt by API',
            'create a folder from the sidebar',
            'return to All Prompts',
            "open the prompt's row menu and move it into the folder",
            'open the folder from the sidebar',
            'read its rows'],
    backendChecks: [
      'the folder is a PG model_hub_prompt_folder row, org-scoped, parent_folder null, deleted=false',
      'Move sets prompt_folder_id on the prompt row to that folder',
      'the list endpoint scoped by prompt_folder returns exactly the moved prompt',
      'All Prompts renders the folder as its own row alongside prompts',
      'the folder view renders exactly the moved prompt',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Two navigations plus 5 browser waits pass the 120s default.
  test.setTimeout(240_000);

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const promptName = `e2e-prompt2-file-${suffix}`;
  const folderName = `e2e-prompt2-folder-${suffix}`;

  // create-draft uses a supplied name verbatim and only falls back to
  // Untitled-N when it is empty (views/prompt_template.py:1199,1231).
  const seeded = (await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, {
    name: promptName, prompt_config: [{ messages: DEFAULT_MESSAGES }],
  })).result;
  await testInfo.attach('seeded-prompt', {
    body: JSON.stringify(seeded), contentType: 'application/json',
  });

  const folderId = await test.step('UI: create a folder from the sidebar', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    const created = page.waitForResponse(
      (r) => r.url().includes(FOLDERS_PATH) && r.request().method() === 'POST' && r.ok(),
      { timeout: UI_READY });
    // FileSystem.jsx:236 renders the sidebar's add-folder control.
    await page.getByRole('button', { name: 'New Folder' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Name').fill(folderName);
    // Scoped and exact: "Create prompt" is on the page behind the dialog, and
    // Playwright's name match is a substring by default.
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    const body = (await (await created).json()) as FolderEnvelope;
    // AddFolder.jsx:34-36 navigates into the folder it just made.
    await expect(page).toHaveURL(`/dashboard/workbench/${body.result.id}`, { timeout: UI_READY });
    return body.result.id;
  });

  await testInfo.attach('created-folder', {
    body: JSON.stringify({ folderId, folderName }), contentType: 'application/json',
  });

  await test.step('storage: the folder is an org-scoped root folder', async () => {
    const rows = await probe.pg<{ name: string; parent_folder_id: string | null; deleted: boolean }>(
      'SELECT name, parent_folder_id, deleted FROM model_hub_prompt_folder WHERE id = $1 AND organization_id = $2',
      [folderId, actor.organizationId]);
    expect(rows).toHaveLength(1);
    expect(rows[0].name).toBe(folderName);
    expect(rows[0].parent_folder_id).toBeNull();
    expect(rows[0].deleted).toBe(false);
  });

  await test.step('UI: move the prompt into the folder from its row menu', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    const row = page.locator('[data-testid="prompt-list"] > div').filter({
      has: page.locator(`a[href="/dashboard/workbench/create/${seeded.root_template}"]`),
    });
    await expect(row).toHaveCount(1, { timeout: UI_READY });

    const moved = page.waitForResponse(
      (r) => r.url().includes(MOVE_PATH(seeded.root_template)) && r.ok(), { timeout: UI_READY });
    // PromptItem.jsx:288-308: the ellipsis is the row's only button.
    await row.getByRole('button').click();
    await page.getByRole('menuitem', { name: 'Move' }).click();
    // MoveItem.jsx:101-106 renders a FormSearchSelectFieldState whose popover
    // is a MUI MenuList, so each folder is a menuitem.
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Select folder').click();
    await page.getByRole('menuitem', { name: folderName }).click();
    await dialog.getByRole('button', { name: 'Move', exact: true }).click();
    await moved;
  });

  await test.step('storage: the prompt now points at the folder', async () => {
    const rows = await probe.pg<{ prompt_folder_id: string | null }>(
      'SELECT prompt_folder_id FROM model_hub_prompttemplate WHERE id = $1 AND organization_id = $2',
      [seeded.root_template, actor.organizationId]);
    expect(rows).toHaveLength(1);
    expect(rows[0].prompt_folder_id).toBe(folderId);
  });

  await test.step('API: the folder-scoped list holds exactly the moved prompt', async () => {
    // The params FolderListView.jsx:57-63 sends for a named folder — note it
    // does NOT send send_all, so this is the folder-scoped queryset.
    const listed = await actor.api.get<ListPage>(LIST_PATH, {
      prompt_folder: folderId, page: 1, page_size: 50, ordering: '-updated_at',
    });
    expect(listed.results.map((r) => r.id)).toEqual([seeded.root_template]);
  });

  await test.step('UI: All Prompts lists the folder alongside prompts', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    // A folder row's href is the same shape the sidebar and breadcrumb use;
    // the list testid is what separates the three.
    const folderRow = page.locator('[data-testid="prompt-list"] > div').filter({
      has: page.locator(`a[href="/dashboard/workbench/${folderId}"]`),
    });
    await expect(folderRow).toHaveCount(1, { timeout: UI_READY });
    await expect(folderRow.getByText(folderName, { exact: true })).toHaveCount(1);
  });

  await test.step('UI: the folder view shows exactly the moved prompt', async () => {
    await page.goto(`/dashboard/workbench/${folderId}`, { waitUntil: 'domcontentloaded' });
    const rowLinks = page.locator('[data-testid="prompt-list"] a');
    // Exact set, not containment: this is what proves the folder scopes the
    // list rather than merely including the prompt somewhere.
    await expect
      .poll(async () => rowLinks.evaluateAll((els) => els.map((e) => e.getAttribute('href'))),
        { timeout: UI_READY })
      .toEqual([`/dashboard/workbench/create/${seeded.root_template}`]);
  });
});
