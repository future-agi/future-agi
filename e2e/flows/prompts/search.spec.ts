import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt).
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const FOLDERS_PATH = '/model-hub/prompt-folders/';
const LIST_PATH = '/model-hub/prompt-executions/';
const UI_READY = 60_000;

// frontend/src/sections/workbench/constant.js:1-29 (DefaultMessages).
const DEFAULT_MESSAGES = [
  { role: 'system', content: [{ type: 'text', text: '' }] },
  { role: 'user', content: [{ type: 'text', text: '' }] },
];

interface PromptRow {
  id: string; name: string; type: string;
  prompt_folder: string | null; prompt_folder_name: string | null;
}
interface ListPage { count: number; total_pages: number; results: PromptRow[] }
interface DraftEnvelope { result: { root_template: string; name: string } }
interface FolderEnvelope { result: { id: string; name: string } }

test('PROMPT-E2E-003: search from All Prompts finds a prompt inside a folder', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-003', area: 'prompts',
    userGoal: 'A user searching from All Prompts finds a prompt even though it lives inside a folder, and sees which folder that is',
    steps: ['seed a folder, a prompt inside it and a loose prompt sharing one minted token',
            'seed a decoy prompt that does not carry the token',
            'open All Prompts',
            'type the token into the search field',
            'read the rows once the debounce settles'],
    backendChecks: [
      'the list endpoint with name=<token> returns both prompts and the folder, and not the decoy',
      'the in-folder prompt is returned from All Prompts without prompt_folder being sent — search is not folder-scoped',
      'the rendered row set equals exactly the three seeded items',
      'the sort and breadcrumb bar is hidden while a search is active',
      'the in-folder prompt row names the folder it lives in',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(240_000);

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const token = `e2e-prompt3-${suffix}`;
  const folderName = `${token}-folder`;
  const insideName = `${token}-inside`;
  const looseName = `${token}-loose`;
  // Deliberately does not contain `token`, so it must never be returned.
  const decoyName = `e2e-prompt3-decoy-${suffix}`;

  const folder = (await actor.api.post<FolderEnvelope>(FOLDERS_PATH, { name: folderName })).result;
  const draft = async (name: string, promptFolder?: string) =>
    (await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, {
      name, prompt_config: [{ messages: DEFAULT_MESSAGES }],
      ...(promptFolder ? { prompt_folder: promptFolder } : {}),
    })).result;

  const inside = await draft(insideName, folder.id);
  const loose = await draft(looseName);
  const decoy = await draft(decoyName);

  await testInfo.attach('seeded', {
    body: JSON.stringify({ folder, inside, loose, decoy, token }),
    contentType: 'application/json',
  });

  await test.step('API: the token matches both prompts and the folder, and not the decoy', async () => {
    const listed = await actor.api.get<ListPage>(LIST_PATH, {
      send_all: 'true', page: 1, page_size: 50, name: token,
      sort_by: 'updated_at', sort_order: 'desc',
    });
    expect([...listed.results.map((r) => r.id)].sort())
      .toEqual([folder.id, inside.root_template, loose.root_template].sort());
    expect(listed.results.map((r) => r.id)).not.toContain(decoy.root_template);

    // No prompt_folder param was sent, yet the in-folder prompt came back:
    // the search crosses folders rather than being scoped to All Prompts.
    const insideRow = listed.results.find((r) => r.id === inside.root_template);
    expect(insideRow?.prompt_folder).toBe(folder.id);
    expect(insideRow?.prompt_folder_name).toBe(folderName);
  });

  await test.step('UI: searching the token narrows the list to exactly the three seeded items', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    const searched = page.waitForResponse(
      (r) => r.url().includes(LIST_PATH) && r.url().includes(token) && r.ok(),
      { timeout: UI_READY });
    // FolderView.jsx:89-94 — placeholder is "Search in prompts" off /all.
    await page.getByPlaceholder('Search in prompts').fill(token);
    await searched;

    const rowLinks = page.locator('[data-testid="prompt-list"] a');
    // Exact set: containment would pass with the decoy leaking in.
    await expect
      .poll(async () => (await rowLinks.evaluateAll(
        (els) => els.map((e) => e.getAttribute('href')))).sort(), { timeout: UI_READY })
      .toEqual([
        `/dashboard/workbench/${folder.id}`,
        `/dashboard/workbench/create/${inside.root_template}`,
        `/dashboard/workbench/create/${loose.root_template}`,
      ].sort());
  });

  await test.step('UI: the sort bar gives way to per-row locations while searching', async () => {
    // FolderView.jsx:141 drops the ActionBar entirely once a search is active.
    await expect(page.getByRole('button', { name: 'Sort' })).toHaveCount(0, { timeout: UI_READY });

    const insideRow = page.locator('[data-testid="prompt-list"] > div').filter({
      has: page.locator(`a[href="/dashboard/workbench/create/${inside.root_template}"]`),
    });
    // PromptItem.jsx:157-180 renders "All Prompts › <folder>" per result row.
    await expect(insideRow.getByText('All Prompts', { exact: true })).toHaveCount(1);
    await expect(insideRow.getByText(folderName, { exact: true })).toHaveCount(1);

    // The loose prompt has no folder, so it gets the root crumb and nothing more.
    const looseRow = page.locator('[data-testid="prompt-list"] > div').filter({
      has: page.locator(`a[href="/dashboard/workbench/create/${loose.root_template}"]`),
    });
    await expect(looseRow.getByText('All Prompts', { exact: true })).toHaveCount(1);
    await expect(looseRow.getByText(folderName, { exact: true })).toHaveCount(0);
  });
});
