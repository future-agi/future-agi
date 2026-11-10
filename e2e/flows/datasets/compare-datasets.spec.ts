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

type Column = { id: string; name: string };
type SourceRow = { row_id: string; input: string; output: string };
type Dataset = { id: string; name: string; columns: Column[]; rows: SourceRow[] };
// CompareDatasetResponseSerializer and the actual CompareData datasource envelope.
type ComparePage = {
  status: boolean;
  result: {
    column_config: Column[];
    table: Record<string, { cell_value: string; cell_row_id: string }>[];
    metadata: { compare_id: string; total_rows: number; total_pages: number };
  };
};

test('DATA-E2E-003: compared datasets show joined rows across both result blocks', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-003',
    area: 'datasets',
    userGoal: 'Compare two uploaded datasets and inspect every matching input with both outputs across result blocks',
    steps: [
      'upload two synthetic CSV datasets with twelve shared inputs and one unmatched input each',
      'open Compare from the first dataset, select the second and join on input',
      'open Data and read the first ten joined input and output pairs',
      'scroll to the final two joined rows and verify that unmatched inputs are absent',
    ],
    backendChecks: [
      'both uploaded datasets retain the actor scope and exact independently seeded input/output pairs in Postgres',
      'the browser comparison and API replay return exactly twelve joined pairs across ten-row blocks with one stable compare_id',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Two uploads: 2 * (UI_READY + ASYNC_JOB + UI_READY) = 360s.
  // Compare navigation + two result blocks: 3 * UI_READY = 180s; 60s headroom.
  test.setTimeout(600_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const expected = Array.from({ length: 12 }, (_, index) => {
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
        [datasetId]))[0].n, POLL.ASYNC_JOB).toBe('26');
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
      expect(body.result.metadata).toEqual({ compare_id: expect.any(String), total_rows: 12, total_pages: 2 });
      expect(body.result.table).toHaveLength(index === 0 ? PAGE_SIZE : 2);
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
        const planted = expected.find(value => value.input === row[inputId].cell_value);
        expect(planted).toBeDefined();
        expect([row[leftId].cell_value, row[rightId].cell_value]).toEqual([planted!.left, planted!.right]);
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

  const joined = pages.flatMap(body => body.result.table).map(row => ({
    input: row[inputId].cell_value, left: row[leftId].cell_value, right: row[rightId].cell_value,
  }));
  expect(joined.sort((a, b) => a.input.localeCompare(b.input))).toEqual(expected);
  await expect(page.getByText('No Common rows', { exact: true })).toHaveCount(0, { timeout: UI_READY });
  // Replay the same authenticated UI endpoint, preserving its comparison ID.
  const replay = await actor.api.post<ComparePage>(comparePath(left.id), {
    dataset_ids: [right.id], base_column_name: 'input', page_size: PAGE_SIZE,
    current_page_index: 1, compare_id: pages[0].result.metadata.compare_id,
  });
  expect(replay.result).toEqual(pages[1].result);
  // actor, browser context and StateProbe are disposed by lib/fixtures.ts.
});
