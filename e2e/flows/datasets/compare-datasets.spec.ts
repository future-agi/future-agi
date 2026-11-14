import type { Response } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { POLL } from '../../lib/state-probe';
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
