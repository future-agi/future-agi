import type { APIResponse, Page, Request, Response, Route, TestInfo } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// UploadFileModal.jsx / CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
// src/utils/axios.js endpoints.dataset.getCompareDataset / CompareDatasetsView.
const comparePath = (id: string) => `/model-hub/datasets/${id}/compare-datasets/`;
// CompareData.jsx uses ten-row server-side blocks and 120px ordinary rows.
const PAGE_SIZE = 10;
const ROW_HEIGHT = 120;
const UI_READY = 60_000;
// Eleven shared inputs: the second block holds exactly one row, the edge where
// CompareDatasetsView's page writer once repeated rows 0-9 as the last block.
const SHARED = 11;
// src/utils/axios.js endpoints.dataset.getCompareDatasetRow / GetCompareDatasetRow.
const rowPath = (compareId: string, rowId: string) =>
  `/model-hub/datasets/get-compare-row/${compareId}/${rowId}/`;

type Column = { id: string; name: string };
type SourceRow = { row_id: string; input: string; output: string };
type Dataset = { id: string; name: string; columns: Column[]; rows: SourceRow[] };
// model_hub.utils.utils.get_diff words, as GenerateDiffText renders them.
type DiffWord = { text: string; status: 'default' | 'removed' | 'added' };
type CompareCell = { cell_value: string; cell_row_id: string; cell_diff_value?: DiffWord[] | null };
// CompareDatasetResponseSerializer and the actual CompareData datasource envelope.
type CompareRow = Record<string, string | CompareCell>;
type ComparePage = {
  status: boolean;
  result: {
    column_config: Column[];
    table: CompareRow[];
    metadata: { compare_id: string; total_rows: number; total_pages: number };
  };
};
// GetCompareDatasetRow response, as CompareDatasetDetailDrawer reads it.
type RowDetail = {
  status: boolean;
  result: { table: CompareRow[]; prev_row_id: string | null; next_row_id: string | null };
};
const cellValue = (row: CompareRow, columnId: string) =>
  (row[columnId] as CompareCell).cell_value;

test('DATA-E2E-005: compared datasets show joined rows across both result blocks', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-005',
    area: 'datasets',
    userGoal: 'Compare two uploaded datasets and inspect every matching input with both outputs across result blocks and in row detail, including the word diff',
    steps: [
      'upload two synthetic CSV datasets with eleven shared inputs and one unmatched input each',
      'open Compare from the first dataset, select the second and join on input',
      'open Data and read the first ten joined input and output pairs',
      'scroll to the final joined row, alone in the second block, and verify that unmatched inputs are absent',
      'click the final joined row, read both outputs in row detail and step back across the block boundary with Prev',
      'close row detail, click the first joined row and confirm Prev is unavailable',
      'switch the second output to Difference and read the first output removed and the second added',
    ],
    backendChecks: [
      'both uploaded datasets retain the actor scope and exact independently seeded input/output pairs in Postgres',
      'the browser comparison and API replay return exactly eleven joined pairs across ten-row blocks with one stable compare_id',
      'row detail requests return the clicked joined row with previous and next row ids that cross the block boundary and stop at the first and last rows',
      'the row detail cell of the second output carries the planted word diff against the first output, and the first output carries none',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Two uploads: 2 * (UI_READY + ASYNC_JOB + UI_READY) = 360s.
  // Compare navigation + two result blocks: 3 * UI_READY = 180s.
  // Row detail: last row, Prev, first row, Difference: 4 * UI_READY = 240s;
  // 60s headroom.
  test.setTimeout(840_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const expected = Array.from({ length: SHARED }, (_, index) => {
    const key = `${suffix}-${String(index).padStart(2, '0')}`;
    return { input: `join-${key}`, left: `left-${key}`, right: `right-${key}` };
  });
  const datasets: Dataset[] = [];

  for (const side of ['left', 'right'] as const) {
    const name = `e2e-data3-${side}-${suffix}`;
    const seeded = [...expected.map(row => ({ input: row.input, output: row[side] })),
      { input: `${side}-only-${suffix}`, output: `unmatched-${side}-${suffix}` }];
    let datasetId = '';
    await test.step(`upload ${side} dataset through the native CSV form`, async () => {
      await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: /^add dataset$/i }).first().click();
      await page.getByText('Upload a file (JSON, CSV)', { exact: true }).click();
      const dialog = page.getByRole('dialog').filter({
        has: page.getByText('Upload a File', { exact: true }),
      });
      // Same synthetic CSV/file-input shape as lib/upload.ts; unique values make
      // this flow independent of prior uploads in the worker-scoped actor.
      const csv = ['input,output', ...seeded.map(row => `${row.input},${row.output}`)].join('\n');
      await dialog.locator('input[type="file"]').setInputFiles({
        name: `compare-${side}.csv`, mimeType: 'text/csv', buffer: Buffer.from(`${csv}\n`),
      });
      await dialog.getByPlaceholder('Dataset Name').fill(name);
      const uploaded = page.waitForResponse(response => new URL(response.url()).pathname === UPLOAD
        && response.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Upload', exact: true }).click();
      const response = await uploaded;
      expect(response.status()).toBe(200);
      datasetId = (await response.json()).result.dataset_id;
      await testInfo.attach(`uploaded-${side}`, {
        contentType: 'application/json',
        body: JSON.stringify({ datasetId, name, seeded,
          organizationId: actor.organizationId, workspaceId: actor.workspaceId }),
      });
    }, { timeout: UI_READY });

    await test.step(`backend check 1: ${side} scope and exact imported source rows`, async () => {
      // CSV import is an asynchronous job; CompareDatasetsView reads these PG
      // columns/cells directly, so a CDC wait is neither necessary nor evidence.
      await expect.poll(async () => (await probe.pg<{ n: string }>(
        'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted',
        [datasetId]))[0].n, POLL.ASYNC_JOB).toBe(String((SHARED + 1) * 2));
      expect(await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
        'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted',
        [datasetId])).toEqual([{ name, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
      const columns = await probe.pg<Column>(
        'SELECT id, name FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name', [datasetId]);
      expect(columns.map(column => column.name)).toEqual(['input', 'output']);
      const rows = await probe.pg<SourceRow>(
        `SELECT i.row_id, i.value AS input, o.value AS output
         FROM model_hub_cell i JOIN model_hub_cell o
           ON o.dataset_id = i.dataset_id AND o.row_id = i.row_id
         WHERE i.dataset_id = $1 AND i.column_id = $2 AND o.column_id = $3
           AND NOT i.deleted AND NOT o.deleted ORDER BY i.value`,
        [datasetId, columns[0].id, columns[1].id]);
      expect(rows.map(({ input, output }) => ({ input, output })))
        .toEqual([...seeded].sort((a, b) => a.input.localeCompare(b.input)));
      datasets.push({ id: datasetId, name, columns, rows });
      await testInfo.attach(`source-${side}-identities`, {
        contentType: 'application/json', body: JSON.stringify(datasets.at(-1)),
      });
    });

    await test.step(`read the imported ${side} dataset`, async () => {
      await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${datasetId}`), { timeout: UI_READY });
      const outputId = datasets.at(-1)!.columns[1].id;
      await expect(page.locator(`.ag-row [col-id="${outputId}"]`).filter({ hasText: seeded[0].output }))
        .toHaveText(seeded[0].output, { timeout: UI_READY });
    }, { timeout: UI_READY });
  }

  const [left, right] = datasets;
  const inputId = left.columns[0].id;
  const leftId = left.columns[1].id;
  const rightId = right.columns[1].id;
  const responses: Response[] = [];
  page.on('response', response => {
    if (new URL(response.url()).pathname === comparePath(left.id)
        && response.request().method() === 'POST') responses.push(response);
  });

  await test.step('select the second dataset and compare by input', async () => {
    // DevelopDetailContext and the comparison picker expose these native labels.
    await page.goto(`/dashboard/develop/${left.id}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Compare with another dataset', exact: true }).click();
    await page.getByText(right.name, { exact: true }).click();
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await page.locator('input[type="radio"][value="input"]').check();
    await page.getByRole('button', { name: 'Compare', exact: true }).last().click();
    await page.getByRole('tab', { name: 'Data', exact: true }).click();
  }, { timeout: UI_READY });

  const pages: ComparePage[] = [];
  const grid = page.locator('.experiment-view-grid'); // CompareData.jsx.
  for (const index of [0, 1]) {
    await test.step(`backend check 2: comparison block ${index} and its visible joined pairs`, async () => {
      if (index === 1) {
        await grid.locator('.ag-body-viewport').evaluate(element => { element.scrollTop = element.scrollHeight; });
      }
      await expect.poll(() => responses.some(response => response.request().postDataJSON().current_page_index === index),
        { timeout: UI_READY }).toBe(true);
      const response = responses.find(item => item.request().postDataJSON().current_page_index === index)!;
      expect(response.status()).toBe(200);
      const body: ComparePage = await response.json();
      pages.push(body);
      await testInfo.attach(`comparison-block-${index}`, {
        contentType: 'application/json', body: JSON.stringify({ request: response.request().postDataJSON(), body }),
      });
      expect(body.status).toBe(true);
      expect(body.result.metadata).toEqual({ compare_id: expect.any(String), total_rows: SHARED, total_pages: 2 });
      expect(body.result.table).toHaveLength(index === 0 ? PAGE_SIZE : SHARED - PAGE_SIZE);
      expect(body.result.column_config.map(({ id, name }) => ({ id, name })).sort((a, b) => a.id.localeCompare(b.id)))
        .toEqual([{ id: inputId, name: 'input' }, { id: leftId, name: left.name }, { id: rightId, name: right.name }]
          .sort((a, b) => a.id.localeCompare(b.id)));
      expect(response.request().postDataJSON()).toEqual({
        dataset_ids: [right.id], base_column_name: 'input', page_size: PAGE_SIZE,
        current_page_index: index, compare_id: index === 0 ? null : pages[0].result.metadata.compare_id,
      });
      expect(body.result.metadata.compare_id).toBe(pages[0].result.metadata.compare_id);

      // Check every pair against the planted values, not against API text alone.
      // Scrolling is native; row-index/col-id are AG Grid's existing cell hooks.
      for (const [offset, row] of body.result.table.entries()) {
        const planted = expected.find(value => value.input === cellValue(row, inputId));
        expect(planted).toBeDefined();
        expect([cellValue(row, leftId), cellValue(row, rightId)]).toEqual([planted!.left, planted!.right]);
        await grid.locator('.ag-body-viewport').evaluate((element, top) => { element.scrollTop = top; },
          (index * PAGE_SIZE + offset) * ROW_HEIGHT);
        const uiRow = grid.locator(`.ag-center-cols-container .ag-row[row-index="${index * PAGE_SIZE + offset}"]`);
        await expect(uiRow.locator(`[col-id="${inputId}"]`)).toHaveText(planted!.input, { timeout: UI_READY });
        await expect(uiRow.locator(`[col-id="${leftId}"]`)).toHaveText(planted!.left, { timeout: UI_READY });
        await expect(uiRow.locator(`[col-id="${rightId}"]`)).toHaveText(planted!.right, { timeout: UI_READY });
      }
      await testInfo.attach(`visible-block-${index}`, {
        contentType: 'image/png', body: await page.screenshot(),
      });
    }, { timeout: UI_READY });
  }

  const ordered = pages.flatMap(body => body.result.table); // grid row order
  const joined = ordered.map(row => ({
    input: cellValue(row, inputId), left: cellValue(row, leftId), right: cellValue(row, rightId),
  }));
  expect([...joined].sort((a, b) => a.input.localeCompare(b.input))).toEqual(expected);
  await expect(page.getByText('No Common rows', { exact: true })).toHaveCount(0, { timeout: UI_READY });
  // Replay the same authenticated UI endpoint, preserving its comparison ID.
  const replay = await actor.api.post<ComparePage>(comparePath(left.id), {
    dataset_ids: [right.id], base_column_name: 'input', page_size: PAGE_SIZE,
    current_page_index: 1, compare_id: pages[0].result.metadata.compare_id,
  });
  expect(replay.result).toEqual(pages[1].result);

  // Row detail: CompareDatasetDetailDrawer requests each row by its row_id and
  // prefetches its neighbours, so register the waits before the click.
  const compareId = pages[0].result.metadata.compare_id;
  const rowId = (index: number) => ordered[index].row_id as string;
  const rowLoaded = (index: number) => page.waitForResponse(response =>
    new URL(response.url()).pathname === rowPath(compareId, rowId(index))
      && response.request().method() === 'GET', { timeout: UI_READY });
  const drawer = page.locator('.MuiDrawer-paper').filter({
    has: page.getByText('Datapoint-compare', { exact: true }),
  });
  const expectRowDetail = async (index: number, response: Response) => {
    expect(response.status()).toBe(200);
    const body: RowDetail = await response.json();
    await testInfo.attach(`row-detail-${index}`, { contentType: 'application/json', body: JSON.stringify(body) });
    expect(body.status).toBe(true);
    expect(body.result.table).toEqual([ordered[index]]);
    expect(body.result.prev_row_id).toBe(index === 0 ? null : rowId(index - 1));
    expect(body.result.next_row_id).toBe(index === ordered.length - 1 ? null : rowId(index + 1));
    // CompareDatasetDetailContent: one column per compared dataset, then each
    // dataset's output in the "Dataset Details" row.
    await expect(drawer.locator('thead th')).toHaveText([`A ${left.name}`, `B ${right.name}`]);
    const details = drawer.locator('table > tbody > tr').nth(1).locator(':scope > td');
    await expect(details.nth(0).getByText(joined[index].left, { exact: true })).toBeVisible();
    await expect(details.nth(1).getByText(joined[index].right, { exact: true })).toBeVisible();
    for (const other of [index - 1, index + 1].filter(value => value >= 0 && value < ordered.length)) {
      await expect(details.nth(0)).not.toContainText(joined[other].left);
    }
  };
  const clickRow = async (index: number) => {
    await grid.locator('.ag-body-viewport').evaluate((element, top) => { element.scrollTop = top; },
      index * ROW_HEIGHT);
    await grid.locator(`.ag-center-cols-container .ag-row[row-index="${index}"] [col-id="${leftId}"]`).click();
  };
  const last = ordered.length - 1;

  await test.step('backend check 3: open the last joined row in row detail', async () => {
    const lastLoaded = rowLoaded(last);
    const previousLoaded = rowLoaded(last - 1);
    await clickRow(last);
    await expectRowDetail(last, await lastLoaded);
    await expect(drawer.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
    await expect(drawer.getByRole('button', { name: 'Prev', exact: true })).toBeEnabled();
    // Prev reads the prefetched row from block 0, across the block boundary.
    await drawer.getByRole('button', { name: 'Prev', exact: true }).click();
    await expectRowDetail(last - 1, await previousLoaded);
    await expect(drawer.getByRole('button', { name: 'Next', exact: true })).toBeEnabled();
    await testInfo.attach('row-detail-boundary', { contentType: 'image/png', body: await page.screenshot() });
  }, { timeout: UI_READY });

  await test.step('backend check 3: the first joined row has no previous row', async () => {
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    const firstLoaded = rowLoaded(0);
    await clickRow(0);
    await expectRowDetail(0, await firstLoaded);
    await expect(drawer.getByRole('button', { name: 'Prev', exact: true })).toBeDisabled();
    await expect(drawer.getByRole('button', { name: 'Next', exact: true })).toBeEnabled();
  }, { timeout: UI_READY });

  await test.step('backend check 4: the second output shows its word diff against the first', async () => {
    // CompareDatasetsView diffs each compared cell against the base dataset's
    // cell; body.result.table equalled ordered[0] in the previous step.
    expect((ordered[0][leftId] as CompareCell).cell_diff_value).toBeNull();
    expect((ordered[0][rightId] as CompareCell).cell_diff_value).toEqual([
      { text: joined[0].left, status: 'removed' },
      { text: joined[0].right, status: 'added' },
    ]);
    const details = drawer.locator('table > tbody > tr').nth(1).locator(':scope > td');
    await expect(details.nth(0).getByRole('tab', { name: 'Difference', exact: true })).toHaveCount(0);
    const difference = details.nth(1).getByRole('tab', { name: 'Difference', exact: true });
    await difference.click();
    await expect(difference).toHaveAttribute('aria-selected', 'true');
    // GenerateDiffText: one span per word, coloured by its get_diff status.
    const removed = details.nth(1).getByText(joined[0].left, { exact: true });
    const added = details.nth(1).getByText(joined[0].right, { exact: true });
    await expect(removed).toBeVisible();
    await expect(added).toBeVisible();
    const background = (word: typeof removed) =>
      word.evaluate(element => getComputedStyle(element).backgroundColor);
    const [removedBackground, addedBackground] = [await background(removed), await background(added)];
    expect([removedBackground, addedBackground]).not.toContain('rgba(0, 0, 0, 0)');
    expect(removedBackground).not.toBe(addedBackground);
    await testInfo.attach('row-detail-difference', { contentType: 'image/png', body: await page.screenshot() });
  }, { timeout: UI_READY });
  // actor, browser context and StateProbe are disposed by lib/fixtures.ts.
});

// Lifecycle regressions use the same authenticated actor, native CSV importer,
// PG probe and real endpoints as DATA-E2E-005. Routes only delay real responses
// or change a request to a real missing resource; no response body is invented.
type LifecycleFixtures = { page: Page; actor: TestActor; probe: StateProbe };
const deletePath = (id: string) => `/model-hub/datasets/delete-compare/${id}/`;
const downloadPath = (id: string) => `${comparePath(id)}download/`;
const expiredMessage = 'This comparison expired and is being rebuilt. Download it again once the table has loaded.';

async function openLifecycleComparison(
  { page, actor, probe }: LifecycleFixtures, info: TestInfo, initiallyEmpty = false,
) {
  test.setTimeout(600_000); // Two imports, comparison build and delayed block/detail checks.
  page.setDefaultTimeout(UI_READY);
  // Keep the initial viewport (including AG Grid's row buffer) within blocks
  // 0/1; blocks 2/3 below must not already be cached when invalidation starts.
  await page.setViewportSize({ width: 1280, height: 720 });
  const suffix = randomUUID();
  const datasets: Dataset[] = [];
  for (const side of ['left', 'right'] as const) {
    const name = `e2e-compare-${side}-${suffix}`;
    const seeded = Array.from({ length: 32 }, (_, index) => ({
      input: `${initiallyEmpty && side === 'right' ? 'disjoint' : 'join'}-${suffix}-${index}`,
      output: `${side}-${suffix}-${index}`,
    }));
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: /^add dataset$/i }).first().click();
    await page.getByText('Upload a file (JSON, CSV)', { exact: true }).click();
    const dialog = page.getByRole('dialog').filter({ has: page.getByText('Upload a File', { exact: true }) });
    await dialog.locator('input[type="file"]').setInputFiles({
      name: `compare-${side}.csv`, mimeType: 'text/csv',
      buffer: Buffer.from(['input,output', ...seeded.map(row => `${row.input},${row.output}`), ''].join('\n')),
    });
    await dialog.getByPlaceholder('Dataset Name').fill(name);
    const uploaded = page.waitForResponse(response => new URL(response.url()).pathname === UPLOAD
      && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Upload', exact: true }).click();
    const response = await uploaded;
    expect(response.status()).toBe(200);
    const id: string = (await response.json()).result.dataset_id;
    await expect.poll(async () => (await probe.pg<{ n: string }>(
      'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted', [id]))[0].n,
    POLL.ASYNC_JOB).toBe('64');
    expect(await probe.pg('SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id = $1', [id]))
      .toEqual([{ organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    const columns = await probe.pg<Column>(
      'SELECT id, name FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name', [id]);
    expect(columns.map(column => column.name)).toEqual(['input', 'output']);
    const rows = await probe.pg<SourceRow>(
      `SELECT i.row_id, i.value AS input, o.value AS output FROM model_hub_cell i
       JOIN model_hub_cell o ON o.dataset_id = i.dataset_id AND o.row_id = i.row_id
       WHERE i.dataset_id = $1 AND i.column_id = $2 AND o.column_id = $3
       AND NOT i.deleted AND NOT o.deleted ORDER BY i.value`, [id, columns[0].id, columns[1].id]);
    expect(rows.map(({ input, output }) => ({ input, output })))
      .toEqual([...seeded].sort((a, b) => a.input.localeCompare(b.input)));
    datasets.push({ id, name, columns, rows });
    await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${id}`));
    await expect(page.locator(`.ag-row [col-id="${columns[1].id}"]`).filter({ hasText: seeded[0].output }))
      .toHaveText(seeded[0].output);
  }
  const [left, right] = datasets;
  const requests: Request[] = [];
  const responses: Response[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === comparePath(left.id) && request.method() === 'POST') requests.push(request);
  });
  page.on('response', response => {
    if (new URL(response.url()).pathname === comparePath(left.id)
      && response.request().method() === 'POST') responses.push(response);
  });
  await page.goto(`/dashboard/develop/${left.id}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Compare with another dataset', exact: true }).click();
  await page.getByText(right.name, { exact: true }).click();
  await page.getByRole('button', { name: 'Open', exact: true }).click();
  await page.locator('input[type="radio"][value="input"]').check();
  await page.getByRole('button', { name: 'Compare', exact: true }).last().click();
  const loaded = page.waitForResponse(response => new URL(response.url()).pathname === comparePath(left.id)
    && response.request().method() === 'POST');
  await page.getByRole('tab', { name: 'Data', exact: true }).click();
  const response = await loaded;
  expect(response.status()).toBe(200);
  const first: ComparePage = await response.json();
  expect(first.result.metadata).toEqual({ compare_id: expect.any(String),
    total_rows: initiallyEmpty ? 0 : 32, total_pages: initiallyEmpty ? 0 : 4 });
  const grid = page.locator('.experiment-view-grid');
  const drawer = page.locator('.MuiDrawer-paper').filter({ has: page.getByText('Datapoint-compare', { exact: true }) });
  if (initiallyEmpty) await expect(page.getByText('No Common rows', { exact: true })).toBeVisible();
  else {
    const firstInput = cellValue(first.result.table[0], left.columns[0].id);
    const planted = left.rows.find(row => row.input === firstInput)!;
    await expect(grid.locator(`.ag-center-cols-container .ag-row[row-index="0"] [col-id="${left.columns[1].id}"]`))
      .toHaveText(planted.output);
  }
  await info.attach('lifecycle-source-and-first-block', {
    contentType: 'application/json', body: JSON.stringify({ datasets, first }),
  });
  return { left, right, grid, drawer, first, requests, responses };
}

async function scrollComparison(page: Page, index: number) {
  await page.locator('.experiment-view-grid .ag-body-viewport').evaluate((element, top) => {
    element.scrollTop = top;
  }, index * ROW_HEIGHT);
}

async function openEarlierDrawer(page: Page, state: Awaited<ReturnType<typeof openLifecycleComparison>>) {
  const { first, left, drawer, grid } = state;
  const id = first.result.metadata.compare_id;
  const detail = page.waitForResponse(response => new URL(response.url()).pathname === rowPath(id, first.result.table[0].row_id as string));
  const neighbour = page.waitForResponse(response => new URL(response.url()).pathname === rowPath(id, first.result.table[1].row_id as string));
  await grid.locator(`.ag-center-cols-container .ag-row[row-index="0"] [col-id="${left.columns[1].id}"]`).click();
  expect((await detail).status()).toBe(200);
  expect((await neighbour).status()).toBe(200); // Finish actual detail prefetch before invalidating the ID.
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(cellValue(first.result.table[0], left.columns[1].id), { exact: true })).toBeVisible();
}

test('Compare lifecycle: later-block fresh ID retires the earlier drawer and delayed competing generation',
  async ({ page, actor, probe }, info) => {
    const state = await openLifecycleComparison({ page, actor, probe }, info);
    const { left, first, grid, drawer, responses } = state;
    const oldId = first.result.metadata.compare_id;
    await openEarlierDrawer(page, state);
    await actor.api.delete(deletePath(oldId)); // A real server cache miss, not a fabricated 200 payload.
    const held = new Map<number, { route: Route; response: APIResponse; body: ComparePage; released: boolean }>();
    await page.route(`**${comparePath(left.id)}`, async route => {
      const sent = route.request().postDataJSON();
      if (sent.compare_id === oldId && [2, 3].includes(sent.current_page_index)) {
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        const body: ComparePage = await response.json();
        expect(body.result.table).toHaveLength(sent.current_page_index === 2 ? 10 : 2);
        expect(body.result.metadata.compare_id).not.toBe(oldId);
        held.set(sent.current_page_index, { route, response, body, released: false });
      } else await route.continue();
    });
    try {
      // Scroll the real grid even while its modal drawer is open. Both native
      // SSRM loads remain in flight; only their real HTTP fulfilment is delayed.
      await scrollComparison(page, 20);
      await expect.poll(() => held.has(2), { timeout: UI_READY }).toBe(true);
      await scrollComparison(page, 30);
      await expect.poll(() => held.has(3), { timeout: UI_READY }).toBe(true);
      const winner = held.get(2)!;
      const loser = held.get(3)!;
      const newId = winner.body.result.metadata.compare_id;
      const orphanId = loser.body.result.metadata.compare_id;
      expect(orphanId).not.toBe(newId);
      const nextRequest = page.waitForRequest(request => new URL(request.url()).pathname === comparePath(left.id)
        && request.method() === 'POST');
      await winner.route.fulfill({ response: winner.response });
      winner.released = true;
      expect((await nextRequest).postDataJSON().compare_id).toBe(newId);
      await expect(drawer).toHaveCount(0);
      await scrollComparison(page, 0);
      await expect.poll(() => responses.some(response => {
        const sent = response.request().postDataJSON();
        return sent.compare_id === newId && sent.current_page_index === 0;
      }), { timeout: UI_READY }).toBe(true);
      const reloaded = responses.find(response => {
        const sent = response.request().postDataJSON();
        return sent.compare_id === newId && sent.current_page_index === 0;
      })!;
      expect(reloaded.status()).toBe(200);
      const rebuilt: ComparePage = await reloaded.json();
      expect(rebuilt.result.metadata.compare_id).toBe(newId);
      const orphanDeleted = page.waitForResponse(response => new URL(response.url()).pathname === deletePath(orphanId)
        && response.request().method() === 'DELETE');
      await loser.route.fulfill({ response: loser.response });
      loser.released = true;
      expect((await orphanDeleted).status()).toBe(200);
      // Revisit the competing block, not only row zero: its visible row IDs
      // must come from the adopted server snapshot, never the delayed orphan.
      const adoptedBlock = await actor.api.post<ComparePage>(comparePath(left.id), {
        dataset_ids: [state.right.id], base_column_name: 'input', page_size: PAGE_SIZE,
        current_page_index: 2, compare_id: newId,
      });
      expect(adoptedBlock.result.metadata.compare_id).toBe(newId);
      await scrollComparison(page, 20);
      await expect(grid.locator('.ag-center-cols-container .ag-row[row-index="20"]'))
        .toHaveAttribute('row-id', adoptedBlock.result.table[0].row_id as string);
      await scrollComparison(page, 0);
      const row = rebuilt.result.table[0];
      await expect(grid.locator(`.ag-center-cols-container .ag-row[row-index="0"]`)).toHaveAttribute('row-id', row.row_id as string);
      const detail = page.waitForResponse(response => new URL(response.url()).pathname === rowPath(newId, row.row_id as string));
      await grid.locator(`.ag-center-cols-container .ag-row[row-index="0"] [col-id="${left.columns[1].id}"]`).click();
      expect((await detail).status()).toBe(200);
      await expect(drawer.getByText(cellValue(row, left.columns[1].id), { exact: true })).toBeVisible();
      await info.attach('real-rollover-and-competing-blocks', { contentType: 'application/json',
        body: JSON.stringify({ oldId, winner: winner.body, loser: loser.body, rebuilt }) });
    } finally {
      for (const entry of held.values()) if (!entry.released) await entry.route.fulfill({ response: entry.response });
      await page.unrouteAll({ behavior: 'wait' });
    }
  });

for (const initiallyEmpty of [false, true]) {
  test(`Compare lifecycle: ${initiallyEmpty ? 'initial empty refresh' : 'nonempty to zero rollover'} is terminal in real AG Grid`,
    async ({ page, actor, probe }, info) => {
      const state = await openLifecycleComparison({ page, actor, probe }, info, initiallyEmpty);
      const { left, right, first, grid, drawer, requests } = state;
      let requestsBeforeRollover = requests.length;
      if (!initiallyEmpty) {
        await openEarlierDrawer(page, state);
        // Mutate only this synthetic actor's input cells. The endpoint itself
        // computes the now-empty join and emits its real metadata-only result.
        const changed = await probe.pg<{ id: string }>(
          `UPDATE model_hub_cell SET value = 'disjoint-' || value
           WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted RETURNING id`,
          [right.id, right.columns[0].id]);
        expect(changed).toHaveLength(32);
        await actor.api.delete(deletePath(first.result.metadata.compare_id));
        requestsBeforeRollover = requests.length;
        const emptyResponse = page.waitForResponse(response => new URL(response.url()).pathname === comparePath(left.id)
          && response.request().postDataJSON().current_page_index >= 2);
        await scrollComparison(page, 30);
        const response = await emptyResponse;
        expect(response.status()).toBe(200);
        const empty: ComparePage = await response.json();
        expect(empty.result.metadata).toEqual({ compare_id: expect.any(String), total_rows: 0, total_pages: 0 });
        expect(empty.result.metadata.compare_id).not.toBe(first.result.metadata.compare_id);
        await info.attach('real-zero-rollover', { contentType: 'application/json', body: JSON.stringify(empty) });
      }
      await expect(drawer).toHaveCount(0);
      await expect(page.getByText('No Common rows', { exact: true })).toBeVisible();
      await expect(grid.locator('.ag-center-cols-container .ag-row')).toHaveCount(0);
      await expect(grid.locator('.ag-floating-bottom .ag-row')).toHaveCount(0);
      if (initiallyEmpty) {
        // No column_config is returned for an initially empty join, hence no
        // sortable header. Exercise the real caller's explicit purge/reset via
        // download instead: this uncached ID gets the actual typed 404.
        const missed = page.waitForResponse(response => new URL(response.url()).pathname === downloadPath(left.id));
        const rebuilt = page.waitForResponse(response => new URL(response.url()).pathname === comparePath(left.id));
        await page.getByRole('button', { name: 'Download comparison CSV', exact: true }).click();
        const missing = await missed;
        expect(missing.status()).toBe(404);
        expect((await missing.json()).code).toBe('compare_not_found');
        const response = await rebuilt;
        expect(response.status()).toBe(200);
        expect(response.request().postDataJSON().compare_id).toBeNull();
        const empty: ComparePage = await response.json();
        expect(empty.result.metadata.total_rows).toBe(0);
        expect(empty.result.metadata.compare_id).not.toBe(first.result.metadata.compare_id);
      } else {
        // AG Grid's supported header keyboard sorting invalidates its SSRM
        // store. This is a real refresh, not a manually called datasource mock.
        const header = grid.locator('.ag-header-cell').first();
        await header.focus();
        await header.press('Enter');
        await expect(header).toHaveAttribute('aria-sort', 'ascending');
      }
      const count = requests.length;
      await expect(page.getByText('No Common rows', { exact: true })).toBeVisible();
      await expect(grid.locator('.ag-center-cols-container .ag-row')).toHaveCount(0);
      // Observe a bounded quiet window so an asynchronous purge/POST loop cannot
      // pass just because the first empty overlay was briefly visible.
      await page.waitForTimeout(1_000);
      expect(requests).toHaveLength(count);
      if (initiallyEmpty) expect(count).toBe(2);
      else {
        expect(count).toBeGreaterThan(requestsBeforeRollover);
        expect(count).toBeLessThanOrEqual(requestsBeforeRollover + 2);
      }
    });
}

test('Compare lifecycle: real typed download miss rebuilds once and only the next explicit download uses the new ID',
  async ({ page, actor, probe }, info) => {
    const state = await openLifecycleComparison({ page, actor, probe }, info);
    const { left, right, first, grid, requests } = state;
    const oldId = first.result.metadata.compare_id;
    const downloads: Request[] = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === downloadPath(left.id) && request.method() === 'POST') downloads.push(request);
    });
    const button = page.getByRole('button', { name: 'Download comparison CSV', exact: true });
    // Dataset/access 404 is a different real backend response, and must not run
    // the recovery callback. Substitute only the requested peer, never the body.
    await page.route(`**${downloadPath(left.id)}`, async route => {
      const response = await route.fetch({ postData: { ...route.request().postDataJSON(), dataset_ids: [randomUUID()] } });
      expect(response.status()).toBe(404);
      expect((await response.json()).code).toBe('not_found');
      await route.fulfill({ response });
    }, { times: 1 });
    const denied = page.waitForResponse(response => new URL(response.url()).pathname === downloadPath(left.id));
    const beforeDenied = requests.length;
    await button.click();
    expect((await denied).status()).toBe(404);
    await page.waitForTimeout(500);
    expect(requests).toHaveLength(beforeDenied);
    await expect(page.getByText(expiredMessage, { exact: true })).toHaveCount(0);

    await actor.api.delete(deletePath(oldId));
    const missed = page.waitForResponse(response => new URL(response.url()).pathname === downloadPath(left.id));
    const rebuilding = page.waitForResponse(response => new URL(response.url()).pathname === comparePath(left.id)
      && response.request().method() === 'POST');
    await button.click();
    const missing = await missed;
    expect(missing.status()).toBe(404);
    const message = 'This comparison was not found or has expired. Open the comparison again.';
    expect(await missing.json()).toEqual({ status: false, type: 'not_found', code: 'compare_not_found',
      detail: message, message, error: message, result: message });
    expect(missing.request().postDataJSON()).toEqual({ dataset_ids: [right.id], base_column_name: 'input', compare_id: oldId });
    await expect(page.getByText(expiredMessage, { exact: true })).toBeVisible();
    const response = await rebuilding;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON().compare_id).toBeNull();
    const rebuilt: ComparePage = await response.json();
    const newId = rebuilt.result.metadata.compare_id;
    expect(newId).not.toBe(oldId);
    expect(rebuilt.result.metadata.total_rows).toBe(32);
    await expect(grid.locator(`.ag-center-cols-container .ag-row[row-index="0"]`))
      .toHaveAttribute('row-id', rebuilt.result.table[0].row_id as string);
    expect(downloads).toHaveLength(2); // Generic failure + typed miss; never an automatic CSV retry.
    const csvResponse = page.waitForResponse(response => new URL(response.url()).pathname === downloadPath(left.id));
    const downloaded = page.waitForEvent('download');
    await button.click();
    const csv = await csvResponse;
    expect(csv.status()).toBe(200);
    expect(csv.request().postDataJSON().compare_id).toBe(newId);
    expect((await downloaded).suggestedFilename()).toBe(`dataset-${left.id}.csv`);
    const text = await csv.text();
    for (const row of left.rows) expect(text).toContain(row.output);
    for (const row of right.rows) expect(text).toContain(row.output);
    expect(downloads).toHaveLength(3);
    await info.attach('download-rebuilt-comparison', { contentType: 'application/json',
      body: JSON.stringify({ oldId, newId, rebuilt, downloadCount: downloads.length }) });
  });
