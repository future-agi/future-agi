import { request, type Locator, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// AddNewDataset.jsx `addToDatasetMutation` → utils/axios.js endpoints.project.addNewDataset.
const ADD_NEW_DATASET_PATH = '/tracer/dataset/add_to_new_dataset/';
// The refusal tracer/views/dataset.py raises for an empty filtered select-all,
// prefixed by add_to_new_dataset's ValueError handler and toasted by
// utils/queryErrorHandler.js. Captured from the running app.
const NO_ROWS_TOAST = 'Error creating the dataset observe No rows match the selection.';
// APP_CONSTANTS.AG_GRID_SELECTION_COLUMN: the checkbox column of every Observe grid.
const SELECTION_COLUMN = 'ag-Grid-SelectionColumn';
// The default mapping maps the span field `id` to a column of the same name
// (traceDetailDrawer/common.js coreSpanFields), so each row's `id` cell is the
// span the row was built from.
const SPAN_ID_COLUMN = 'id';
// One browser action's first-paint budget; the local stack slows several-fold
// beside other workers, so not the 10s expect default.
const UI_READY = 60_000;
const SECOND_NS = 1_000_000_000n;
const DAY_NS = 86_400n * SECOND_NS;

type CellRow = { value: string };
type CountRow = { n: number };

const visibleRowIds = async (grid: Locator) => [...new Set(
  (await grid.locator('.ag-row[row-id]').evaluateAll(
    (rows) => rows.map((row) => row.getAttribute('row-id'))))
    .filter((id): id is string => Boolean(id)))].sort();

// TraceFilterPanel: a "Trace Name" row whose value is either an existing
// suggestion (checkbox) or the typed "+ Specify" value. Nested popovers: the
// first Escape closes the values, the second the panel.
async function filterByTraceName(page: Page, value: string, existing: boolean) {
  await page.getByRole('button', { name: 'Filter', exact: true }).click();
  await page.getByRole('button', { name: 'Property', exact: true }).first().click();
  await page.locator('[data-filter-property-option="name"]').click();
  await page.locator('[data-filter-value-trigger="name"]').click();
  await page.getByPlaceholder('Search values...').fill(value);
  await page.locator(`[data-filter-value-option="${value}"]${existing ? '[role="checkbox"]' : ''}`)
    .click({ timeout: UI_READY });
  await page.keyboard.press('Escape');
  await expect(page.getByPlaceholder('Search values...')).toBeHidden({ timeout: UI_READY });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
}

async function switchDateRange(page: Page, from: string, to: string) {
  await page.getByRole('button', { name: from, exact: true }).click();
  await page.getByRole('menuitem', { name: to, exact: true }).click();
}

// Actions → Move to dataset → Add to a new dataset, with the default mapping.
async function addSelectionToNewDataset(page: Page, datasetName: string) {
  await page.getByRole('button', { name: 'Actions', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Move to dataset' }).click();
  const drawer = page.locator('.MuiDrawer-paper')
    .filter({ hasText: 'Move this span to a dataset or create one' });
  await drawer.getByText('Add to a new dataset', { exact: true }).click();
  await drawer.getByPlaceholder('Enter new dataset name').fill(datasetName);
  const submit = drawer.getByRole('button', { name: 'Add to dataset', exact: true });
  // Enabled once the span-field mapping has loaded.
  await expect(submit).toBeEnabled({ timeout: UI_READY });
  const response = page.waitForResponse(
    (r) => r.url().includes(ADD_NEW_DATASET_PATH) && r.request().method() === 'POST',
    { timeout: UI_READY });
  await submit.click();
  return response;
}

test('OBS-E2E-071: select-all add to a new dataset keeps the grid filter, time window and deselections', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-071', area: 'observe',
    userGoal: 'A developer filters the trace list, narrows the time window, selects all but one trace and gets a new dataset holding exactly those traces',
    steps: ['send three matching traces now, one non-matching trace now and two matching traces ten days ago',
      "open the project's trace list and widen the date range to Past 30D",
      'filter by the shared trace name and see all five matching traces',
      'narrow the date range to Past 7D and see only the three recent ones',
      'select all with the header checkbox, then untick one row',
      'Actions → Move to dataset → Add to a new dataset with the default mapping',
      'filter by a trace name that matches nothing, select all and try to add to another new dataset'],
    backendChecks: ['all twelve seeded spans present in CH `spans` (FINAL) under the auto-created project',
      'the new dataset has exactly the grid count minus one rows in PG model_hub_row',
      "the dataset's `id` cells in PG model_hub_cell are exactly the root spans of the two still-selected recent traces: no unticked, non-matching or out-of-window trace",
      'the no-match add is refused with a 400 and the "No rows match the selection." toast, and no dataset of that name exists in PG model_hub_dataset'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // SPAN_VISIBLE 15 + ASYNC_JOB 60 + navigation and ~14 UI_READY-bounded steps
  // (most settle in seconds) — 420s keeps a slow run failing on the assertion
  // that ran out rather than on a bare test timeout.
  test.setTimeout(420_000);
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-obs71-${suffix}`;
    const keepName = `e2e-obs71-keep-${suffix}`;
    const otherName = `e2e-obs71-other-${suffix}`;
    const missingName = `e2e-obs71-absent-${suffix}`;
    const datasetName = `e2e-obs71-ds-${suffix}`;
    const emptyDatasetName = `e2e-obs71-empty-${suffix}`;
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
      secretKey: actor.secretKey, projectName };
    // Ten days back sits inside Past 30D and outside Past 7D, clear of any
    // day boundary.
    const now = BigInt(Date.now()) * 1_000_000n;
    const send = (rootName: string, start: bigint) => sendTrace(req, { ...cfg, rootName,
      startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n });
    const recent: SeededTrace[] = [];
    for (const i of [0n, 1n, 2n]) recent.push(await send(keepName, now - (i + 1n) * SECOND_NS));
    const other = await send(otherName, now - SECOND_NS);
    const old: SeededTrace[] = [];
    for (const i of [0n, 1n]) old.push(await send(keepName, now - 10n * DAY_NS - i * SECOND_NS));
    const all = [...recent, other, ...old];
    const unticked = recent[0];
    const expectedRootSpans = recent.slice(1).map((t) => t.spanIds[0]).sort();
    await testInfo.attach('seeded-traces', { body: JSON.stringify({ recent, other, old, unticked }),
      contentType: 'application/json' });

    const projectId = await test.step('storage: every seeded span is in ClickHouse', async () => {
      await expect.poll(async () => {
        const rows = await probe.ch<{ n: string }>(
          `SELECT count() AS n FROM spans FINAL WHERE trace_id IN (${all.map((_, i) => `{t${i}:String}`).join(', ')})`,
          Object.fromEntries(all.map((t, i) => [`t${i}`, t.traceId])));
        return Number(rows[0].n);
      }, POLL.SPAN_VISIBLE).toBe(all.reduce((n, t) => n + t.spanIds.length, 0));
      const projects = await probe.pg<{ id: string }>(
        'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
        [projectName, actor.organizationId]);
      expect(projects).toHaveLength(1);
      return projects[0].id;
    });

    const grid = page.locator('.clean-data-table:visible');
    const ids = (traces: SeededTrace[]) => traces.map((t) => t.traceId).sort();

    await test.step('UI: filter the trace list, then narrow the window to Past 7D', async () => {
      // LLMTracingView uses selectedTab=trace for the trace list.
      await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
        { waitUntil: 'domcontentloaded' });
      await expect.poll(() => visibleRowIds(grid), { timeout: UI_READY })
        .toEqual(ids([...recent, other]));
      await switchDateRange(page, 'Past 7D', 'Past 30D');
      await expect.poll(() => visibleRowIds(grid), { timeout: UI_READY }).toEqual(ids(all));
      await filterByTraceName(page, keepName, true);
      // The old traces match the filter: only the window keeps them out below.
      await expect.poll(() => visibleRowIds(grid), { timeout: UI_READY })
        .toEqual(ids([...recent, ...old]));
      await switchDateRange(page, 'Past 30D', 'Past 7D');
      await expect.poll(() => visibleRowIds(grid), { timeout: UI_READY }).toEqual(ids(recent));
    });

    const gridCount = await test.step('UI: select all, then untick one trace', async () => {
      await grid.locator(`.ag-header-cell[col-id="${SELECTION_COLUMN}"] input[type="checkbox"]`).click();
      await expect(page.getByText(`${recent.length} selected`, { exact: true }))
        .toBeVisible({ timeout: UI_READY });
      const count = recent.length;
      await grid.locator(`.ag-row[row-id="${unticked.traceId}"] [col-id="${SELECTION_COLUMN}"] input[type="checkbox"]`)
        .click();
      await expect(page.getByText(`${count - 1} selected`, { exact: true }))
        .toBeVisible({ timeout: UI_READY });
      return count;
    });

    const datasetId = await test.step('UI: add the selection to a new dataset', async () => {
      const response = await addSelectionToNewDataset(page, datasetName);
      await testInfo.attach('add-to-new-dataset', { body: JSON.stringify({
        request: response.request().postDataJSON(), status: response.status(),
        response: await response.json() }), contentType: 'application/json' });
      expect(response.status()).toBe(200);
      await expect(page.getByText('Dataset created. Rows are still being added in the background'))
        .toBeVisible({ timeout: UI_READY });
      return (await response.json()).result.dataset_id as string;
    });

    await test.step('storage: the dataset holds exactly the still-selected traces', async () => {
      const spanIdCells = () => probe.pg<CellRow>(
        `SELECT c.value FROM model_hub_cell c JOIN model_hub_column col ON col.id = c.column_id
          WHERE c.dataset_id = $1 AND NOT c.deleted AND NOT col.deleted AND col.name = $2`,
        [datasetId, SPAN_ID_COLUMN]);
      // Rows land from a background chunk task in one transaction.
      await expect.poll(async () => (await spanIdCells()).length, POLL.ASYNC_JOB).toBeGreaterThan(0);
      const rows = await probe.pg<CountRow>(
        'SELECT count(*)::int AS n FROM model_hub_row WHERE dataset_id = $1 AND NOT deleted', [datasetId]);
      const cells = (await spanIdCells()).map((c) => c.value).sort();
      await testInfo.attach('dataset-span-ids', { body: JSON.stringify({ datasetId, rows, cells }),
        contentType: 'application/json' });
      // The exact set first: a failure then names the extra (unticked,
      // non-matching or out-of-window) root spans, not just a count.
      expect(cells).toEqual(expectedRootSpans);
      expect(rows[0].n).toBe(gridCount - 1);
    });

    await test.step('UI: a filter that matches nothing is refused, and no dataset is made', async () => {
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Clear all', exact: true }).click();
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      await filterByTraceName(page, missingName, false);
      await expect(grid.getByText('No traces found', { exact: true })).toBeVisible({ timeout: UI_READY });
      await grid.locator(`.ag-header-cell[col-id="${SELECTION_COLUMN}"] input[type="checkbox"]`).click();
      await expect(page.getByText(/^\d+ selected$/)).toBeVisible({ timeout: UI_READY });
      const response = await addSelectionToNewDataset(page, emptyDatasetName);
      await testInfo.attach('add-no-match', { body: JSON.stringify({
        request: response.request().postDataJSON(), status: response.status(),
        response: await response.json() }), contentType: 'application/json' });
      expect(response.status()).toBe(400);
      await expect(page.getByText(NO_ROWS_TOAST, { exact: true })).toBeVisible({ timeout: UI_READY });
      const made = await probe.pg<CountRow>(
        'SELECT count(*)::int AS n FROM model_hub_dataset WHERE organization_id = $1 AND name = $2',
        [actor.organizationId, emptyDatasetName]);
      expect(made[0].n).toBe(0);
    });
  } finally {
    await req.dispose();
  }
});
