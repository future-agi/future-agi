import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py; HuggingFaceDatasetConfig/Create and HuggingFaceAddRowsRequestSerializer.
const CONFIG = '/model-hub/develops/get-huggingface-dataset-config/';
const CREATE = '/model-hub/develops/create-dataset-from-huggingface/';
const addPath = (id: string) => `/model-hub/develops/${id}/add_rows_from_huggingface/`;
const UI_READY = 60_000;
type Config = { result: { dataset_info: { revision: string; num_rows: Record<string, Record<string, number>> } } };
type Table = { result: { table: Array<{ row_id: string; [key: string]: unknown }> } };

async function choose(page: Page, label: string, ref: string) {
  // HuggingFaceView.jsx existing search input and repo-specific card attributes.
  await page.getByPlaceholder('Search', { exact: true }).fill(label);
  await page.locator(`[data-testid="hf-item"][data-repo="fixture/${label}"]`).click();
  await expect(page.getByRole('textbox', { name: 'Revision', exact: true })).toBeVisible({ timeout: UI_READY });
  const response = page.waitForResponse(r => new URL(r.url()).pathname === CONFIG &&
    r.request().method() === 'POST' && r.request().postData()?.includes(ref) === true, { timeout: UI_READY });
  await page.getByRole('textbox', { name: 'Revision', exact: true }).fill(ref);
  const configured = await response;
  expect(configured.status(), await configured.text()).toBe(200);
  const config: Config = await configured.json();
  expect(config.result.dataset_info.num_rows).toEqual({ default: { train: ref === 'v1' ? 2 : 3 } });
  return config.result.dataset_info.revision;
}

async function values(page: Page, scopes: ScopeActors, probe: StateProbe, actor: ScopeActor,
  id: string, expected: string[], perturb?: string) {
  const stored = () => probe.pg<{ value: string; status: string }>(
    `SELECT c.value, c.status FROM model_hub_cell c JOIN model_hub_row r ON r.id = c.row_id
     WHERE c.dataset_id = $1 AND NOT c.deleted AND NOT r.deleted ORDER BY r."order"`, [id]);
  const wanted = expected.map(value => ({ value, status: 'pass' }));
  await expect.poll(stored, POLL.ASYNC_JOB).toEqual(perturb === 'values' ? [...wanted, { value: 'absent', status: 'pass' }] : wanted);
  const [{ id: column }] = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_column WHERE dataset_id = $1 AND name = $2 AND NOT deleted', [id, 'text']);
  const table = await scopes.send<Table>(actor, 'GET', `/model-hub/develops/${id}/get-dataset-table/`,
    undefined, { current_page_index: 0, page_size: 25 });
  expect(table.status).toBe(200);
  expect(table.body.result.table.map(row => (row[column] as { cell_value: string }).cell_value)).toEqual(expected);
  const rows = await probe.pg<{ id: string }>('SELECT id FROM model_hub_row WHERE dataset_id = $1 AND NOT deleted ORDER BY "order"', [id]);
  expect(table.body.result.table.map(row => row.row_id)).toEqual(rows.map(row => row.id));
  expect(await probe.pg('SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id = $1', [id]))
    .toEqual([{ organization_id: perturb === 'ownership' ? '00000000-0000-0000-0000-000000000000' : actor.organizationId, workspace_id: actor.workspaceId }]);
  // Finish mounting the lazy-loaded grid before starting a reload.
  await expect(page.locator(`.ag-row [col-id="${column}"]`).first()).toBeVisible({ timeout: UI_READY });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-row [col-id="${column}"]`)).toHaveText(expected, { timeout: UI_READY });
  return stored;
}

test('DATA-E2E-014: Hugging Face create and add preserve the selected revision', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-014', area: 'datasets',
    userGoal: 'Create a dataset and add rows from chosen Hugging Face revisions without changing another workspace',
    steps: [
      'in each of two synthetic organizations, choose a Hub dataset and revision v1 and create it',
      'wait for the real worker to fill the imported cells and reload the grid',
      'open Add rows and choose Hugging Face revision v2, then confirm Add rows',
      'reload and verify both revisions persist, reject invalid revisions and cross-scope destinations',
    ],
    backendChecks: [
      'configuration resolves the selected Hub ref and create and add requests carry its immutable commit',
      'the ml-enabled worker persists exact revision-specific values with PASS status and table API identities agree',
      'datasets belong to their creating organization and workspace',
      'invalid revisions and foreign-organization or sibling-workspace writes are refused without changing cells',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(1_320_000); // Provisioning + two tenants' UI actions and worker polls.
  const perturb = process.env.E2E_HF_PERTURB;
  if (perturb && !['revision', 'values', 'ownership', 'isolation'].includes(perturb)) throw new Error('Unknown perturbation');
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  for (const [actor, foreign, prefix] of [
    [scopeActors.ownerA, scopeActors.ownerB, 'a'], [scopeActors.ownerB, scopeActors.ownerA, 'b'],
  ] as const) {
    const label = `e2e-hf-${prefix}-${suffix}`;
    const context = await scopeActors.openContext(browser, actor);
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.hostname === 'localhost' || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    try {
      await page.goto('/dashboard/huggingface', { waitUntil: 'domcontentloaded' });
      await expect(page.getByText('Curated benchmarks first', { exact: true })).toHaveCount(0);
      const first = await choose(page, label, 'v1');
      await page.getByRole('textbox', { name: 'Name', exact: true }).fill(label);
      const creating = page.waitForResponse(r => new URL(r.url()).pathname === CREATE && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Create dataset', exact: true }).click();
      const created = await creating;
      expect(created.status(), await created.text()).toBe(200);
      expect(created.request().postData()).toContain(perturb === 'revision' ? `${first}-missing` : first);
      const id = (await created.json()).result.dataset_id;
      await testInfo.attach(`dataset-${prefix}`, { contentType: 'application/json', body: JSON.stringify({
        id, label, revision: first, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
        externalSystem: 'Hugging Face Hub HTTP STANDIN; real datasets library and worker',
      }) });
      await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${id}`), { timeout: UI_READY });
      const expected = [0, 1].map(i => `${label}-v1-${i}`);
      await values(page, scopeActors, scopeProbe, actor, id, expected, perturb);
      await page.getByRole('button', { name: 'Add rows', exact: true }).click();
      await page.getByRole('button', { name: /^Hugging Face/ }).click();
      const second = await choose(page, label, 'v2');
      expect(second).not.toBe(first);
      const adding = page.waitForResponse(r => new URL(r.url()).pathname === addPath(id) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Add rows', exact: true }).click();
      const added = await adding;
      expect(added.status(), await added.text()).toBe(200);
      expect(added.request().postDataJSON().revision).toBe(second);
      await expect(page.getByText('3 rows added to the dataset; their values load in the background', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(/0.*pending rows/)).toHaveCount(0);
      expected.push(...[0, 1, 2].map(i => `${label}-v2-${i}`));
      const stored = await values(page, scopeActors, scopeProbe, actor, id, expected);
      const before = await stored();
      const body = { huggingface_dataset_name: `fixture/${label}`, huggingface_dataset_config: 'default', huggingface_dataset_split: 'train', revision: second, num_rows: 1 };
      const deniedActors = [foreign];
      if (prefix === 'a') deniedActors.push(scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id));
      for (const other of deniedActors) {
        expect((await scopeActors.send(other, 'POST', addPath(id), body)).status).toBe(perturb === 'isolation' ? 200 : 404);
      }
      expect((await scopeActors.send(actor, 'POST', addPath(id), { ...body, revision: 'does-not-exist' })).status).toBe(400);
      expect(await stored()).toEqual(before);
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await testInfo.attach(`grid-${prefix}`, { contentType: 'image/png', body: await page.screenshot() });
    } finally {
      await context.close();
    }
  }
});
