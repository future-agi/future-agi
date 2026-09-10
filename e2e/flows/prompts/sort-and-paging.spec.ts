import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt).
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const FOLDERS_PATH = '/model-hub/prompt-folders/';
const LIST_PATH = '/model-hub/prompt-executions/';
const UI_READY = 60_000;
// FolderListView.jsx:31 — the list opens at 10 per page, and the Result-per-page
// select offers 10/20/30/40/50 (lines 277-281).
const DEFAULT_PAGE_SIZE = 10;
const WIDE_PAGE_SIZE = 50;
// Enough seeded prompts that a second page exists whatever else the shared
// worker org already holds.
const SEEDED = 11;

// frontend/src/sections/workbench/constant.js:1-29 (DefaultMessages).
const DEFAULT_MESSAGES = [
  { role: 'system', content: [{ type: 'text', text: '' }] },
  { role: 'user', content: [{ type: 'text', text: '' }] },
];

interface PromptRow { id: string; name: string; type: string }
interface ListPage { count: number; total_pages: number; results: PromptRow[] }
interface DraftEnvelope { result: { root_template: string; name: string } }
interface FolderEnvelope { result: { id: string; name: string } }

test('PROMPT-E2E-004: sorting and paging the prompt list keeps it complete', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-004', area: 'prompts',
    userGoal: 'A user sorting and paging the prompt list gets a stable ordering with no row lost or repeated',
    steps: ['seed a folder and eleven prompts whose names sort unambiguously',
            'open All Prompts',
            'widen the page size and sort by Name descending',
            'toggle the same sort to ascending',
            'return to ten per page and step from page 1 to page 2'],
    backendChecks: [
      'the list endpoint returns the seeded prompts in the requested sort_by/sort_order, both directions',
      "the UI's rendered order of the seeded prompts matches the API's for the same query",
      'paging the same query never repeats a row and covers every seeded prompt',
      'the "No.of prompts" count is the prompt count and excludes the folder rows the same response carries',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(300_000);

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const token = `e2e-prompt4-${suffix}`;
  // Zero-padded so the backend's case-insensitive string sort
  // (views/prompt_template.py:4262-4268) orders them unambiguously.
  const names = Array.from({ length: SEEDED }, (_, i) =>
    `${token}-${String(i + 1).padStart(2, '0')}`);

  const folder = (await actor.api.post<FolderEnvelope>(FOLDERS_PATH, {
    name: `${token}-folder`,
  })).result;
  const seeded: { name: string; id: string }[] = [];
  for (const name of names) {
    const r = (await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, {
      name, prompt_config: [{ messages: DEFAULT_MESSAGES }],
    })).result;
    seeded.push({ name, id: r.root_template });
  }
  await testInfo.attach('seeded', {
    body: JSON.stringify({ folder, seeded }), contentType: 'application/json',
  });

  const ascHrefs = seeded.map((s) => `/dashboard/workbench/create/${s.id}`);
  const descHrefs = [...ascHrefs].reverse();
  const mine = new Set(ascHrefs);

  // The rendered hrefs, narrowed to the rows this spec seeded — the worker org
  // is shared, so absolute position means nothing and only relative order does.
  const renderedOrder = async () =>
    (await page.locator('[data-testid="prompt-list"] a')
      .evaluateAll((els) => els.map((e) => e.getAttribute('href') ?? '')))
      .filter((h) => mine.has(h));

  const listPage = (params: Record<string, string | number>) =>
    actor.api.get<ListPage>(LIST_PATH, {
      send_all: 'true', sort_by: 'name', ...params,
    });

  await test.step('API: the endpoint honours both sort directions', async () => {
    const desc = await listPage({ page: 1, page_size: 200, sort_order: 'desc' });
    const asc = await listPage({ page: 1, page_size: 200, sort_order: 'asc' });
    const onlyMine = (p: ListPage) =>
      p.results.filter((r) => mine.has(`/dashboard/workbench/create/${r.id}`)).map((r) => r.name);
    expect(onlyMine(desc)).toEqual([...names].reverse());
    expect(onlyMine(asc)).toEqual(names);
  });

  await test.step('UI: the rendered order follows the chosen sort, both ways', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: 'Sort' })).toBeVisible({ timeout: UI_READY });

    // Widen the page so all eleven seeded rows are on the page being read.
    const widened = page.waitForResponse(
      (r) => r.url().includes(LIST_PATH) && r.url().includes(`page_size=${WIDE_PAGE_SIZE}`) && r.ok(),
      { timeout: UI_READY });
    await page.locator('#page-size-select').click();
    await page.getByRole('option', { name: String(WIDE_PAGE_SIZE), exact: true }).click();
    await widened;

    // ActionBar.jsx:57-68: the first pick of a new field lands on desc, and a
    // second pick of the same field toggles to asc. The menu stays open in
    // between (its auto-close is commented out), so both clicks are one visit.
    const sortedDesc = page.waitForResponse(
      (r) => r.url().includes(LIST_PATH) && r.url().includes('sort_by=name')
        && r.url().includes('sort_order=desc') && r.ok(), { timeout: UI_READY });
    await page.getByRole('button', { name: 'Sort' }).click();
    await page.getByRole('menuitem', { name: 'Name' }).click();
    await sortedDesc;

    // The menu overlays the list but does not detach it, and renderedOrder
    // reads hrefs out of the DOM, so both directions are read from one visit.
    await expect.poll(renderedOrder, { timeout: UI_READY }).toEqual(descHrefs);

    const sortedAsc = page.waitForResponse(
      (r) => r.url().includes(LIST_PATH) && r.url().includes('sort_by=name')
        && r.url().includes('sort_order=asc') && r.ok(), { timeout: UI_READY });
    await page.getByRole('menuitem', { name: 'Name' }).click();
    await sortedAsc;
    await expect.poll(renderedOrder, { timeout: UI_READY }).toEqual(ascHrefs);

    await page.keyboard.press('Escape');
    // The menu's closing backdrop would otherwise swallow the next click.
    await expect(page.getByRole('menuitem', { name: 'Name' }))
      .toHaveCount(0, { timeout: UI_READY });
  });

  await test.step('UI: page 1 and page 2 are disjoint', async () => {
    const narrowed = page.waitForResponse(
      (r) => r.url().includes(LIST_PATH) && r.url().includes(`page_size=${DEFAULT_PAGE_SIZE}`) && r.ok(),
      { timeout: UI_READY });
    await page.locator('#page-size-select').click();
    await page.getByRole('option', { name: String(DEFAULT_PAGE_SIZE), exact: true }).click();
    await narrowed;

    const allHrefs = () => page.locator('[data-testid="prompt-list"] a')
      .evaluateAll((els) => els.map((e) => e.getAttribute('href') ?? ''));
    await expect.poll(async () => (await allHrefs()).length, { timeout: UI_READY })
      .toBe(DEFAULT_PAGE_SIZE);
    const first = await allHrefs();

    const paged = page.waitForResponse(
      (r) => r.url().includes(LIST_PATH) && r.url().includes('page=2') && r.ok(),
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Go to page 2' }).click();
    await paged;
    await expect.poll(async () => (await allHrefs()).some((h) => h !== '' && !first.includes(h)),
      { timeout: UI_READY }).toBe(true);
    const second = await allHrefs();
    expect(second.filter((h) => first.includes(h))).toEqual([]);
  });

  await test.step('API: paging covers every seeded prompt exactly once', async () => {
    const seen: string[] = [];
    let folderRows = 0;
    let promptRows = 0;
    const firstPage = await listPage({ page: 1, page_size: DEFAULT_PAGE_SIZE, sort_order: 'asc' });
    for (let p = 1; p <= firstPage.total_pages; p += 1) {
      const body = p === 1
        ? firstPage
        : await listPage({ page: p, page_size: DEFAULT_PAGE_SIZE, sort_order: 'asc' });
      for (const row of body.results) {
        seen.push(row.id);
        if (row.type === 'FOLDER') folderRows += 1;
        if (row.type === 'PROMPT') promptRows += 1;
      }
    }
    // No row repeated across pages, and every seeded prompt turned up.
    expect(new Set(seen).size).toBe(seen.length);
    for (const s of seeded) expect(seen).toContain(s.id);
    expect(seen).toContain(folder.id);

    // The count the "No.of prompts" line renders is the prompt count, even
    // though the same pages carry folder rows too
    // (views/prompt_template.py:4166-4175).
    expect(folderRows).toBeGreaterThan(0);
    expect(firstPage.count).toBe(promptRows);
    // FolderListView.jsx:184 renders it with no space after "No.of".
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(`No.of prompts: ${firstPage.count}`))
      .toBeVisible({ timeout: UI_READY });
  });
});
