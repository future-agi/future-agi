import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { ApiError } from '../../lib/api-client';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   getNameChange     -> /model-hub/prompt-templates/{id}/save-name/
//   promptMultiDelete -> /model-hub/prompt-templates/bulk-delete/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const SAVE_NAME_PATH = (id: string) => `/model-hub/prompt-templates/${id}/save-name/`;
const BULK_DELETE_PATH = '/model-hub/prompt-templates/bulk-delete/';
const LIST_PATH = '/model-hub/prompt-executions/';
const UI_READY = 60_000;

// frontend/src/sections/workbench/constant.js:1-29 (DefaultMessages).
const DEFAULT_MESSAGES = [
  { role: 'system', content: [{ type: 'text', text: '' }] },
  { role: 'user', content: [{ type: 'text', text: '' }] },
];

interface PromptRow { id: string; name: string; type: string }
interface ListPage { count: number; total_pages: number; results: PromptRow[] }
interface DraftEnvelope { result: { root_template: string; name: string } }

test('PROMPT-E2E-005: renaming and deleting a prompt from its row', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-005', area: 'prompts',
    userGoal: 'A user renames a prompt from its row and deletes one they no longer want, and the list reflects both',
    steps: ['seed two prompts',
            'open All Prompts',
            'rename the first from its row menu',
            'try to give the second that same name and be refused',
            'delete the second from its row menu',
            'read the list'],
    backendChecks: [
      'save-name persists the new name on the PG row',
      "save-name refuses a name already used by another template in the org, and the second prompt's name is unchanged",
      'bulk-delete soft-deletes: the PG row survives with deleted=true',
      'the list endpoint no longer returns the deleted prompt but still returns the renamed one',
      'the list shows the renamed row and no row for the deleted prompt',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seedName = `e2e-prompt5-seed-${suffix}`;
  const keeper = `e2e-prompt5-keep-${suffix}`;
  const doomed = `e2e-prompt5-gone-${suffix}`;

  const draft = async (name: string) =>
    (await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, {
      name, prompt_config: [{ messages: DEFAULT_MESSAGES }],
    })).result;

  const first = await draft(`${seedName}-a`);
  const second = await draft(doomed);
  await testInfo.attach('seeded', {
    body: JSON.stringify({ first, second }), contentType: 'application/json',
  });

  const rowFor = (id: string) => page.locator('[data-testid="prompt-list"] > div').filter({
    has: page.locator(`a[href="/dashboard/workbench/create/${id}"]`),
  });

  await test.step('UI: rename the first prompt from its row menu', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    const row = rowFor(first.root_template);
    await expect(row).toHaveCount(1, { timeout: UI_READY });

    const renamed = page.waitForResponse(
      (r) => r.url().includes(SAVE_NAME_PATH(first.root_template)) && r.ok(),
      { timeout: UI_READY });
    await row.getByRole('button').click();
    await page.getByRole('menuitem', { name: 'Rename' }).click();
    // RenameItem.jsx:95-100 prefills the field with the current name.
    await page.getByRole('dialog').getByLabel('Name').fill(keeper);
    // ModalWrapper's default actionBtnTitle is "Save" (ModalWrapper.jsx:30).
    await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click();
    await renamed;
  });

  await test.step('storage: the rename reached Postgres', async () => {
    const rows = await probe.pg<{ name: string }>(
      'SELECT name FROM model_hub_prompttemplate WHERE id = $1 AND organization_id = $2',
      [first.root_template, actor.organizationId]);
    expect(rows).toHaveLength(1);
    expect(rows[0].name).toBe(keeper);
  });

  await test.step('API: a name already taken in the org is refused', async () => {
    // views/prompt_template.py:3931-3942 rejects a duplicate with a 400 before
    // touching the row; ApiClient turns any >=400 into ApiError.
    const clash = actor.api.post(SAVE_NAME_PATH(second.root_template), { name: keeper });
    await expect(clash).rejects.toThrow(ApiError);
    await expect(clash).rejects.toMatchObject({ status: 400 });

    const rows = await probe.pg<{ name: string }>(
      'SELECT name FROM model_hub_prompttemplate WHERE id = $1',
      [second.root_template]);
    expect(rows[0].name).toBe(doomed);
  });

  await test.step('UI: delete the second prompt from its row menu', async () => {
    await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
    const row = rowFor(second.root_template);
    await expect(row).toHaveCount(1, { timeout: UI_READY });

    const deleted = page.waitForResponse(
      (r) => r.url().includes(BULK_DELETE_PATH) && r.ok(), { timeout: UI_READY });
    await row.getByRole('button').click();
    await page.getByRole('menuitem', { name: 'Delete' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await deleted;

    await expect(rowFor(second.root_template)).toHaveCount(0, { timeout: UI_READY });
    await expect(rowFor(first.root_template)).toHaveCount(1);
    await expect(rowFor(first.root_template).getByText(keeper, { exact: true }))
      .toHaveCount(1);
  });

  await test.step('storage: the delete is soft — the row survives, flagged', async () => {
    const rows = await probe.pg<{ deleted: boolean; name: string }>(
      'SELECT deleted, name FROM model_hub_prompttemplate WHERE id = $1',
      [second.root_template]);
    expect(rows).toHaveLength(1);
    expect(rows[0].deleted).toBe(true);
  });

  await test.step('API: the list drops the deleted prompt and keeps the renamed one', async () => {
    const listed = await actor.api.get<ListPage>(LIST_PATH, {
      send_all: 'true', page: 1, page_size: 200,
      sort_by: 'updated_at', sort_order: 'desc',
    });
    const ids = listed.results.map((r) => r.id);
    expect(ids).toContain(first.root_template);
    expect(ids).not.toContain(second.root_template);
    expect(listed.results.find((r) => r.id === first.root_template)?.name).toBe(keeper);
  });
});
