import { test, expect as baseExpect } from '../../lib/fixtures';
import type { Page } from '@playwright/test';
import { flowAnnotation } from '../../lib/flow-meta';

// Seed bodies pinned from ANNOT-E2E-005 (agreement-drilldown.spec.ts) and
// model_hub/serializers/annotation_queues.py; the grid reads the same routes
// through frontend/src/api/annotation-queues/annotation-queues.js.
const QUEUES = '/model-hub/annotation-queues/';
const LABELS = '/model-hub/annotations-labels/';
const DATASETS = '/model-hub/develops/';
// frontend/src/routes/paths.js: annotations.queueDetail.
const QUEUE_PAGE = '/dashboard/annotations/queues/';
// Browser first-paint budget, per e2e/README.md "Writing a flow".
const UI_READY = 60_000;
const expect = baseExpect.configure({ timeout: UI_READY });
// queue-items-table.jsx columnDefs plus AG Grid's selection column, left to right.
const COLUMN_ORDER = ['ag-Grid-SelectionColumn', 'source_type', 'preview', 'status', 'assignedTo',
  'review_status', 'comment_count', 'created_at', 'actions'];

type Envelope<T> = { result: T };
type ListedItem = { id: string; source_preview: { type: string; dataset_name: string; row_order: number } };
type ItemRow = { id: string; deleted: boolean; queue_id: string; dataset_row_id: string; status: string; updated_at: Date };
type SourceRow = { id: string; dataset_id: string; order: number; deleted: boolean; updated_at: Date;
  cell_id: string; value: string; cell_deleted: boolean; cell_updated_at: Date };

const header = (page: Page, colId: string) => page.locator(`.ag-header-cell[col-id="${colId}"]`);
const handle = (page: Page, colId: string) => header(page, colId).locator('.ag-header-cell-resize');

async function width(page: Page, colId: string) {
  const box = await header(page, colId).boundingBox();
  expect(box, `header ${colId} is laid out`).not.toBeNull();
  return box!.width;
}
// Display order by on-screen position, so a drag taken as a column move is caught.
async function columnOrder(page: Page) {
  return page.locator('.ag-header-cell[col-id]').evaluateAll(cells => cells
    .map(c => ({ id: c.getAttribute('col-id')!, left: c.getBoundingClientRect().left }))
    .sort((a, b) => a.left - b.left).map(c => c.id));
}
async function dragHandle(page: Page, colId: string, dx: number) {
  const box = await handle(page, colId).boundingBox();
  expect(box, `resize handle of ${colId}`).not.toBeNull();
  const x = box!.x + box!.width / 2;
  const y = box!.y + box!.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y, { steps: 12 });
  await page.mouse.up();
}

test('ANNOT-E2E-007: queue managers widen item data columns while selection and actions stay fixed', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ANNOT-E2E-007', area: 'annotations',
    userGoal: 'A queue manager widens the data columns of a queue\'s item list to read them, while the selection and row-action columns stay fixed and selecting an item or backing out of removal leaves the queue item and its source row untouched',
    steps: [
      'seed a numeric label, a one-row synthetic dataset with a minted cell, an active manual queue and one dataset-row item through public APIs',
      'open the queue item list at 1440x900 and wait for the seeded row',
      'see resize handles on Preview and Source but not on selection or Actions, which measure 44 and 60 px',
      'drag the Preview and Source header edges wider; column order and the fixed columns stay unchanged',
      'tick and untick the seeded row and see Remove Selected (1) appear and go',
      'open Remove item on the row, cancel the dialog and still see the row with 1 of 1 items',
      'reload and see the seeded row still listed',
    ],
    backendChecks: [
      'PG model_hub_queueitem row is unchanged after select, remove-cancel and reload: not deleted, same queue, dataset row, status and updated_at',
      'PG model_hub_row and model_hub_cell keep the seeded order, minted value, deleted=false and updated_at',
      'items API the grid calls returns exactly the seeded item with its dataset_row preview, and the grid Preview cell reads that exact text',
      'the browser sends no DELETE for the item and no POST to items/bulk-remove/',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // 30 s seed + five UI_READY stages (300 s) + 30 s headroom.
  test.setTimeout(360_000);
  page.setDefaultTimeout(UI_READY);
  await page.setViewportSize({ width: 1440, height: 900 });
  const prefix = `e2e-annot7-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `${prefix}-dataset`;
  const marker = `${prefix}-row-0`;
  const pageErrors: string[] = [];
  page.on('pageerror', e => pageErrors.push(e.message));

  // Seed.
  const label = (await actor.api.post<Envelope<{ id: string; name: string }>>(LABELS, {
    name: `${prefix}-label`, type: 'numeric', allow_notes: false,
    settings: { min: 0, max: 100, step_size: 1, display_type: 'slider' },
  })).result;
  const datasetId = (await actor.api.post<Envelope<{ dataset_id: string }>>(`${DATASETS}create-dataset-manually/`, {
    dataset_name: datasetName, number_of_rows: 1, number_of_columns: 1,
  })).result.dataset_id;
  const table = (await actor.api.get<Envelope<{ table: { row_id: string; order: number }[];
    column_config: { id: string }[] }>>(`${DATASETS}${datasetId}/get-dataset-table/`, { page_size: 100 })).result;
  expect(table.table).toHaveLength(1);
  const rowId = table.table[0].row_id;
  const rowOrder = Number(table.table[0].order);
  const columnId = table.column_config[0].id;
  await actor.api.post(`${DATASETS}${datasetId}/update_cell_value/`, { row_id: rowId, column_id: columnId, new_value: marker });
  // Queue create returns the object directly, with no result envelope. The
  // creator becomes manager, reviewer and annotator when annotator_ids is omitted.
  const queue = await actor.api.post<{ id: string; name: string }>(QUEUES, {
    name: `${prefix}-queue`, auto_assign: false, assignment_strategy: 'manual', annotations_required: 1,
    requires_review: false, label_ids: [label.id],
  });
  const itemsPath = `${QUEUES}${queue.id}/items/`;
  await actor.api.post(`${QUEUES}${queue.id}/update-status/`, { status: 'active' });
  const added = await actor.api.post<Envelope<{ added: number }>>(`${itemsPath}add-items/`, {
    items: [{ source_type: 'dataset_row', source_id: rowId }],
  });
  expect(added.result.added).toBe(1);
  // QueueItemSerializer.source_id is write-only; source_preview.row_order joins the row.
  const seededList = await actor.api.get<{ results: ListedItem[] }>(itemsPath, { limit: 100 });
  expect(seededList.results).toHaveLength(1);
  expect(Number(seededList.results[0].source_preview.row_order)).toBe(rowOrder);
  const itemId = seededList.results[0].id;
  const previewText = `${datasetName} - Row ${rowOrder}`;

  const readItem = () => probe.pg<ItemRow>(
    `SELECT id, deleted, queue_id, dataset_row_id, status, updated_at
     FROM model_hub_queueitem WHERE id = $1`, [itemId]);
  const readSource = () => probe.pg<SourceRow>(
    `SELECT r.id, r.dataset_id, r."order", r.deleted, r.updated_at,
            c.id AS cell_id, c.value, c.deleted AS cell_deleted, c.updated_at AS cell_updated_at
     FROM model_hub_row r JOIN model_hub_cell c ON c.row_id = r.id AND c.column_id = $2
     WHERE r.id = $1`, [rowId, columnId]);
  const itemSnapshot = await readItem();
  expect(itemSnapshot).toEqual([expect.objectContaining({
    id: itemId, deleted: false, queue_id: queue.id, dataset_row_id: rowId,
  })]);
  const sourceSnapshot = await readSource();
  expect(sourceSnapshot).toEqual([expect.objectContaining({
    id: rowId, dataset_id: datasetId, order: rowOrder, deleted: false, value: marker, cell_deleted: false,
  })]);
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    queueId: queue.id, itemId, rowId, datasetId, labelId: label.id, columnId, marker,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    snapshots: { item: itemSnapshot, source: sourceSnapshot },
  }) });

  const assertStoredUnchanged = async () => {
    expect(await readItem(), 'backend check 1: queue item row').toEqual(itemSnapshot);
    expect(await readSource(), 'backend check 2: source row and cell').toEqual(sourceSnapshot);
  };
  // Backend check 4 recorder: every removal request the browser could send.
  const removals: string[] = [];
  page.on('request', req => {
    const path = new URL(req.url()).pathname;
    if ((req.method() === 'DELETE' && path.startsWith(itemsPath)) ||
        (req.method() === 'POST' && path === `${itemsPath}bulk-remove/`)) removals.push(`${req.method()} ${path}`);
  });
  const row = page.locator(`.ag-center-cols-container .ag-row[row-id="${itemId}"]`);

  await test.step('open the queue item list and see the seeded row', async () => {
    const listed = page.waitForResponse(r => new URL(r.url()).pathname === itemsPath &&
      r.request().method() === 'GET' && r.status() === 200, { timeout: UI_READY });
    await page.goto(`${QUEUE_PAGE}${queue.id}`);
    const shown = (await (await listed).json()) as { results: ListedItem[] };
    expect(shown.results.map(i => i.id)).toEqual([itemId]);
    await expect(row).toBeVisible();
    await expect(row.locator('[col-id="preview"]')).toHaveText(previewText);
  }, { timeout: UI_READY });

  await test.step('resize data columns while selection and actions stay fixed', async () => {
    await expect(handle(page, 'preview')).toBeVisible();
    await expect(handle(page, 'source_type')).toBeVisible();
    await expect(handle(page, 'actions')).toBeHidden();
    await expect(handle(page, 'ag-Grid-SelectionColumn')).toBeHidden();
    expect(Math.abs(await width(page, 'actions') - 60)).toBeLessThanOrEqual(1);
    expect(Math.abs(await width(page, 'ag-Grid-SelectionColumn') - 44)).toBeLessThanOrEqual(1);
    expect(await columnOrder(page)).toEqual(COLUMN_ORDER);

    const previewBefore = await width(page, 'preview');
    await dragHandle(page, 'preview', 120);
    await expect.poll(async () => await width(page, 'preview') - previewBefore,
      { message: 'drag widened Preview' }).toBeGreaterThanOrEqual(100);
    const previewCell = await row.locator('[col-id="preview"]').boundingBox();
    expect(Math.abs(previewCell!.width - await width(page, 'preview'))).toBeLessThanOrEqual(1);
    expect(await columnOrder(page)).toEqual(COLUMN_ORDER);

    const sourceBefore = await width(page, 'source_type');
    await dragHandle(page, 'source_type', 70);
    await expect.poll(async () => await width(page, 'source_type') - sourceBefore,
      { message: 'drag widened Source' }).toBeGreaterThanOrEqual(50);
    expect(await columnOrder(page)).toEqual(COLUMN_ORDER);
    expect(Math.abs(await width(page, 'actions') - 60)).toBeLessThanOrEqual(1);
    expect(Math.abs(await width(page, 'ag-Grid-SelectionColumn') - 44)).toBeLessThanOrEqual(1);
    await expect(handle(page, 'actions')).toBeHidden();
    await expect(handle(page, 'ag-Grid-SelectionColumn')).toBeHidden();
    await testInfo.attach('resized', { contentType: 'image/png', body: await page.screenshot() });
  }, { timeout: UI_READY });

  await test.step('select and deselect the item without removing it', async () => {
    const checkbox = page.locator(`.ag-row[row-id="${itemId}"] [col-id="ag-Grid-SelectionColumn"]`)
      .getByRole('checkbox');
    const bulkRemove = page.getByRole('button', { name: 'Remove Selected (1)', exact: true });
    await checkbox.check();
    // Bulk remove has no confirmation, so it is never clicked.
    await expect(bulkRemove).toBeVisible();
    await checkbox.uncheck();
    await expect(page.getByRole('button', { name: /^Remove Selected/ })).toHaveCount(0);
  }, { timeout: UI_READY });

  await test.step('cancel removing the item', async () => {
    await row.hover();
    await page.locator(`.ag-row[row-id="${itemId}"]`).getByRole('button', { name: 'Remove item', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove Item', exact: true });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(removals, 'backend check 4: no removal request').toEqual([]);
    await expect(row).toBeVisible();
    await expect(page.getByText('1 of 1 items', { exact: true })).toBeVisible();
    await assertStoredUnchanged();
    const api = await actor.api.get<{ results: ListedItem[] }>(itemsPath, { limit: 100 });
    expect(api.results, 'backend check 3: items API').toHaveLength(1);
    expect(api.results[0]).toMatchObject({ id: itemId,
      source_preview: { type: 'dataset_row', dataset_name: datasetName, row_order: rowOrder } });
  }, { timeout: UI_READY });

  await test.step('reload and still see the seeded row', async () => {
    // Widths are not asserted here: they do not persist across a reload today.
    const listed = page.waitForResponse(r => new URL(r.url()).pathname === itemsPath &&
      r.request().method() === 'GET' && r.status() === 200, { timeout: UI_READY });
    await page.reload();
    const shown = (await (await listed).json()) as { results: ListedItem[] };
    expect(shown.results.map(i => i.id)).toEqual([itemId]);
    await expect(row.locator('[col-id="preview"]')).toHaveText(previewText);
    expect(removals, 'backend check 4: no removal request').toEqual([]);
    await assertStoredUnchanged();
    expect(pageErrors).toEqual([]);
  }, { timeout: UI_READY });
});
