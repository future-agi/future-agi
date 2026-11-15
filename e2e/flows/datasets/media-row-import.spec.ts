import type { Response } from '@playwright/test';
import { test, expect } from '../../lib/local-only-browser';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { localMediaUrl, mediaActorHeaders, mediaDigest, mediaImportFile, mediaSeedFile,
  provisionMedia, readLocalMedia, uploadMediaCsv, type MediaCase } from '../../lib/media-import-fixture';

// model_hub/serializers/contracts.py: CreateDatasetFromLocalFileRequestSerializer.
const CREATE_FILE = '/model-hub/develops/create-dataset-from-local-file/';
// AddRowsFromFileRequestSerializer and AddRowDrawer/UploadFileModal.jsx:onSubmit.
const IMPORT_ROWS = '/model-hub/develops/add_rows_from_file/';
// AddRowsFromFile + GeneralMethods.success_response (partial cell failures still return success).
const IMPORT_SUCCESS = { status: true, result: '6 Row(s) added successfully' };
// develop_dataset.py:_cell_upload_error: fixed public guidance, not internal exception text.
const REASONS = { audio: 'Could not store this audio value.', image: 'Could not store this image value.' };
// GetDatasetTableView._get_dataset_status + models/choices.py:DatasetStatus.
const PARTIAL_SUMMARY = { dataset_status: 'PartialDataExtracted', failed_cells: 8,
  reason: [...Array(4).fill(REASONS.audio), ...Array(4).fill(REASONS.image)].sort() };
// Native file inference (file_upload.py), not column conversion which clears dataset_source_local.
const COLUMN_TYPES = [{ name: 'audio', data_type: 'audio' }, { name: 'image', data_type: 'image' },
  { name: 'text', data_type: 'text' }];
// Independent oracle from the six-case contract; never computed from actual PG/API outcomes.
const CASES: MediaCase[] = ['blank', 'valid', 'invalid', 'literal-nan', 'literal-NA', 'literal-null'];
// README Writing a flow: one browser action / short chain's first-paint budget.
const UI_READY = 60_000;
const API_READY = 60_000;
// DevelopDataV2.jsx uses row_id for AG Grid identity and column UUIDs for col-id.
const GRID_ROWS = '.ag-center-cols-container .ag-row';

type Kind = 'audio' | 'image' | 'text';
type CellState = { status: string; value: string | null; value_infos: unknown };
type StoredCell = CellState & { id: string; row_id: string; column_id: string; name: Kind; data_type: string };
type MatrixCell = CellState & { marker: string; name: Kind; data_type: string };
type ApiCell = { cell_id: string; cell_value: string | null; status: string; value_infos: unknown };
type ApiRow = { row_id: string } & Record<string, unknown>;
type TableBody = { status: boolean; result: { table: ApiRow[]; metadata: { status: typeof PARTIAL_SUMMARY } } };
const orderMatrix = (matrix: MatrixCell[]) => matrix.sort((a, b) =>
  `${a.marker}/${a.name}`.localeCompare(`${b.marker}/${b.name}`));
// Cell.value_infos is JSONField holding either [] or a serialized error object on this write path.
const normalizeInfos = (value: unknown) => typeof value === 'string' ? JSON.parse(value) : value;

function fullTableResponse(response: Response, path: string): boolean {
  const url = new URL(response.url());
  return url.pathname === path && response.request().method() === 'GET' &&
    !['true', '1'].includes(url.searchParams.get('column_config_only') ?? '');
}

test('DATA-E2E-006: mixed media imports keep blanks empty and expose only supplied-value failures', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-006', area: 'datasets',
    userGoal: 'Import mixed CSV and XLSX rows into a media dataset and distinguish empty media from rejected supplied values while viewing valid images and playing valid audio.',
    steps: [
      'provision synthetic local PNG and WAV bytes; create the CSV native local-file dataset and remove only its schema-inference seed row through the public API',
      'import six CSV rows using Add Row and the real browser file chooser, which excludes XLSX',
      'verify the exact persisted matrix and reload the CSV grid, comparing browser and public table responses and the API-only extraction summary',
      'view the valid PNG and play the valid WAV through the native controls with external browser requests denied',
      'create a second native local-file dataset and remove its seed row; import the same six cases as a real XLSX workbook through authenticated multipart API upload, then repeat the persisted-state, browser readback and media checks',
    ],
    backendChecks: [
      'Both scoped datasets persist the exact imported media matrix: blanks pass empty, owned media pass unchanged, and every supplied invalid or NA-like literal is error/null with its fixed reason',
      'Both partial-success imports return HTTP 200 and their browser table responses agree with the public table API on exact row/cell identities, values, statuses and the eight-failure PartialDataExtracted summary',
      'The valid imported image and audio retain the synthetic bytes and are rendered and fetched by the real browser using only local owned-storage URLs',
    ],
  }),
}, async ({ page, context, actor, probe }, testInfo) => {
  test.setTimeout(1_080_000);
  // 2*ASYNC_JOB=120s + 8*UI_READY=480s + 7*API_READY=420s + 60s headroom.
  // Seven API groups: fixture + two creates + two deletes + two table/media read groups.
  // Request/step ceilings bound each group; no CDC is involved.
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const base = `e2e-data6-${suffix}`;
  const headers = mediaActorHeaders(actor);
  localMediaUrl(E2E.apiUrl);
  const assets = await test.step('provision owned fixture bytes', () =>
    provisionMedia(context.request, actor, base), { timeout: API_READY });
  await testInfo.attach('fixture-bytes', { body: JSON.stringify(assets), contentType: 'application/json' });

  for (const format of ['csv', 'xlsx'] as const) {
    const name = `${base}-${format}`;
    const markers = CASES.map(kind => `${name}-${kind}`);
    const created = await context.request.post(E2E.apiUrl + CREATE_FILE, {
      headers, timeout: API_READY, maxRetries: 0, maxRedirects: 0,
      multipart: { new_dataset_name: name, model_type: 'GenerativeLLM', file: mediaSeedFile(name, assets) },
    });
    expect(created.status()).toBe(200);
    const datasetId: string = (await created.json()).result.dataset_id;
    expect(datasetId).toMatch(/^[0-9a-f-]{36}$/);
    await testInfo.attach(`${format}-seed-ids`, { body: JSON.stringify({ name, datasetId,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }), contentType: 'application/json' });

    // Native create-file enrichment is asynchronous. Its audio may fail independently of AddRowsFromFile;
    // wait for terminal cells AND completed media columns, never claim that setup audio passed.
    await expect.poll(async () => {
      const cells = await probe.pg<{ status: string; value: string | null; name: string }>(
        `SELECT c.status,c.value,col.name FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
         WHERE c.dataset_id=$1 AND NOT c.deleted AND NOT col.deleted`, [datasetId]);
      const mediaColumns = await probe.pg<{ status: string }>(
        `SELECT status FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted AND data_type IN ('image','audio')`, [datasetId]);
      return { cells: cells.length, terminal: cells.every(c => ['pass', 'error'].includes(c.status)),
        seed: cells.filter(c => c.name === 'text').map(c => c.value),
        mediaStatuses: mediaColumns.map(c => c.status).sort() };
    }, POLL.ASYNC_JOB).toEqual({ cells: 3, terminal: true, seed: [`${name}-seed`],
      mediaStatuses: ['Completed', 'Completed'] });
    const columns = await probe.pg<{ id: string; name: Kind; data_type: string }>(
      'SELECT id,name,data_type FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted ORDER BY name', [datasetId]);
    expect(columns.map(({ name: columnName, data_type }) => ({ name: columnName, data_type }))).toEqual(COLUMN_TYPES);
    const ids = Object.fromEntries(columns.map(column => [column.name, column.id])) as Record<Kind, string>;
    const scope = await probe.pg<{ name: string; organization_id: string; workspace_id: string; dataset_source_local: boolean }>(
      `SELECT name,organization_id,workspace_id,(dataset_config->>'dataset_source_local')::boolean AS dataset_source_local
       FROM model_hub_dataset WHERE id=$1 AND NOT deleted`, [datasetId]);
    expect(scope).toEqual([{ name, organization_id: actor.organizationId, workspace_id: actor.workspaceId,
      dataset_source_local: true }]);
    const seedRows = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
    expect(seedRows).toHaveLength(1);
    const seedRowId = seedRows[0].id;
    await testInfo.attach(`${format}-schema-ids`, { body: JSON.stringify({ datasetId, columns, removedSeedRowId: seedRowId }),
      contentType: 'application/json' });
    // DatasetRowSelectionRequestSerializer / DeleteRowView. Delete only our one setup row, no SQL writes.
    const removed = await context.request.delete(`${E2E.apiUrl}/model-hub/develops/${datasetId}/delete_row/`, {
      headers, data: { row_ids: [seedRowId] }, timeout: API_READY, maxRetries: 0, maxRedirects: 0,
    });
    expect(removed.status()).toBe(200);
    expect(await probe.pg('SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [datasetId])).toEqual([]);

    await test.step(`${format}: open the dataset`, async () => {
      await page.goto(`/dashboard/develop/${datasetId}`, { waitUntil: 'domcontentloaded', timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Add Row', exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step(`${format}: import six rows (${format === 'csv' ? 'browser chooser' : 'API; chooser excludes XLSX'})`, async () => {
      if (format === 'csv') {
        await page.getByRole('button', { name: 'Add Row', exact: true }).click();
        await page.getByText('Upload a file (JSONl/ JSON/ CSV)', { exact: true }).click();
        const dialog = page.getByRole('dialog').filter({ has: page.getByText('Upload a File', { exact: true }) });
        const chooser = dialog.locator('input[type="file"]');
        await expect(chooser).toHaveAttribute('accept', /\.csv/, { timeout: UI_READY });
        await expect(chooser).not.toHaveAttribute('accept', /xlsx/i, { timeout: UI_READY });
        await uploadMediaCsv(chooser, name, assets);
        const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === IMPORT_ROWS &&
          response.request().method() === 'POST', { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Done', exact: true }).click();
        const response = await responsePromise;
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body).toEqual(IMPORT_SUCCESS);
        await testInfo.attach(`${format}-upload`, { body: JSON.stringify({ via: 'browser chooser', status: response.status(), body }),
          contentType: 'application/json' });
        await expect(dialog).toBeHidden({ timeout: UI_READY });
      } else {
        const response = await context.request.post(E2E.apiUrl + IMPORT_ROWS, {
          headers, multipart: { dataset_id: datasetId, file: mediaImportFile('xlsx', name, assets) },
          timeout: API_READY, maxRetries: 0, maxRedirects: 0,
        });
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body).toEqual(IMPORT_SUCCESS);
        await testInfo.attach(`${format}-upload`, { body: JSON.stringify({ via: 'API (chooser excludes XLSX)', status: response.status(), body }),
          contentType: 'application/json' });
      }
    }, { timeout: UI_READY });

    // Check 1: synchronous import has returned 200. Assert immediately, do not poll away wrong states.
    const cells = await probe.pg<StoredCell>(
      `SELECT c.id,c.row_id,c.column_id,col.name,col.data_type,c.status,c.value,c.value_infos
       FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id JOIN model_hub_row r ON r.id=c.row_id
       WHERE c.dataset_id=$1 AND r.dataset_id=$1 AND col.dataset_id=$1
         AND NOT c.deleted AND NOT r.deleted AND NOT col.deleted ORDER BY r."order",col.name`, [datasetId]);
    const liveRows = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY "order"', [datasetId]);
    expect(liveRows).toHaveLength(6);
    expect(liveRows.map(row => row.id)).not.toContain(seedRowId);
    expect(cells).toHaveLength(18);
    // Include every live cell in the dataset, so a leftover seed/orphan cannot hide behind the joins.
    const allLiveCells = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_cell WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
    expect(allLiveCells.map(cell => cell.id).sort()).toEqual(cells.map(cell => cell.id).sort());
    expect(new Set(cells.map(cell => cell.id)).size).toBe(18);
    expect([...new Set(cells.map(cell => cell.row_id))].sort()).toEqual(liveRows.map(row => row.id).sort());
    const textCells = cells.filter(cell => cell.name === 'text');
    expect(textCells.map(cell => cell.value)).toEqual(markers);
    const markerByRow = new Map(textCells.map(cell => [cell.row_id, cell.value!]));
    const rowByMarker = new Map(textCells.map(cell => [cell.value!, cell.row_id]));
    const actualMatrix = orderMatrix(cells.map(cell => ({ marker: markerByRow.get(cell.row_id)!, name: cell.name,
      data_type: cell.data_type, status: cell.status, value: cell.value, value_infos: normalizeInfos(cell.value_infos) })));
    const expectedMatrix: MatrixCell[] = orderMatrix(CASES.flatMap(kind => (['audio', 'image', 'text'] as const).map(column => {
      const value = column === 'text' ? `${name}-${kind}` : kind === 'blank' ? '' : kind === 'valid' ? assets[column].url : null;
      const failed = column !== 'text' && kind !== 'blank' && kind !== 'valid';
      return { marker: `${name}-${kind}`, name: column, data_type: column,
        status: failed ? 'error' : 'pass', value,
        value_infos: failed ? { reason: REASONS[column as 'audio' | 'image'] } : [] };
    })));
    await testInfo.attach(`${format}-persisted-matrix`, { body: JSON.stringify({ datasetId, cells, actualMatrix }),
      contentType: 'application/json' });
    expect(actualMatrix, `${format} independent persisted media matrix`).toEqual(expectedMatrix);

    // Check 2: identities come from PG; outcomes come from the independent oracle, NOT actual PG values.
    const expectedTable = liveRows.map(row => ({ row_id: row.id, cells: columns.map(column => {
      const stored = cells.find(cell => cell.row_id === row.id && cell.column_id === column.id)!;
      const expected = expectedMatrix.find(cell => cell.marker === markerByRow.get(row.id) && cell.name === column.name)!;
      // GetDatasetTableView.process_row enriches serialized error infos with metadata:{};
      // PG keeps only the reason. Assert both exact representations, not a partial match.
      const apiInfos = expected.status === 'error' ? { reason: REASONS[column.name as 'audio' | 'image'], metadata: {} } : [];
      return { column_id: column.id, cell_id: stored.id, cell_value: expected.value,
        status: expected.status, value_infos: apiInfos };
    }) }));
    const tablePath = `/model-hub/develops/${datasetId}/get-dataset-table/`;
    const apiBody: TableBody = await test.step(`${format}: read the public table and unchanged owned bytes`, async () => {
      const apiResponse = await context.request.get(E2E.apiUrl + tablePath, {
        headers, timeout: API_READY, maxRetries: 0, maxRedirects: 0,
      });
      expect(apiResponse.status()).toBe(200);
      // Check 3's HTTP lane: source-buffer hashes, never a second downloaded-response oracle.
      for (const kind of ['image', 'audio'] as const) {
        const storedUrl = cells.find(cell => cell.row_id === rowByMarker.get(`${name}-valid`) && cell.name === kind)!.value!;
        const response = await readLocalMedia(context.request, storedUrl);
        expect(response.status()).toBe(200);
        expect(mediaDigest(await response.body())).toBe(assets[kind].sha256);
      }
      return apiResponse.json();
    }, { timeout: API_READY });
    await test.step(`${format}: reload and compare exact API, browser and grid state`, async () => {
      const responsePromise = page.waitForResponse(response => fullTableResponse(response, tablePath), { timeout: UI_READY });
      await page.reload({ waitUntil: 'domcontentloaded', timeout: UI_READY });
      const response = await responsePromise;
      expect(response.status()).toBe(200);
      const browserBody: TableBody = await response.json();
      const expectedSummary = { ...PARTIAL_SUMMARY, reason: [...PARTIAL_SUMMARY.reason] };
      for (const [lane, body] of [['API', apiBody], ['browser', browserBody]] as const) {
        expect(body.status).toBe(true);
        const table = body.result.table.map(row => ({ row_id: row.row_id, cells: columns.map(column => {
          const cell = row[column.id] as ApiCell;
          return { column_id: column.id, cell_id: cell.cell_id, cell_value: cell.cell_value,
            status: cell.status, value_infos: cell.value_infos };
        }) }));
        const summary = { ...body.result.metadata.status, reason: [...body.result.metadata.status.reason].sort() };
        await testInfo.attach(`${format}-${lane}-readback`, { body: JSON.stringify({ datasetId, table, summary }),
          contentType: 'application/json' });
        expect(table, `${format} ${lane} exact identities and independently expected states`).toEqual(expectedTable);
        expect(summary, `${format} ${lane} extraction summary`).toEqual(expectedSummary);
      }
      const rows = page.locator(GRID_ROWS);
      await expect(rows.locator(`[col-id="${ids.text}"]`)).toHaveText(markers, { timeout: UI_READY });
      for (const kind of CASES) {
        const row = rows.filter({ has: page.locator(`[col-id="${ids.text}"]`).getByText(`${name}-${kind}`, { exact: true }) });
        await expect(row).toHaveAttribute('row-id', rowByMarker.get(`${name}-${kind}`)!, { timeout: UI_READY });
        for (const column of ['image', 'audio'] as const) {
          const cell = row.locator(`[col-id="${ids[column]}"]`);
          if (kind !== 'blank' && kind !== 'valid') {
            await expect(cell.getByText('Error', { exact: true })).toHaveText('Error', { timeout: UI_READY });
          } else {
            await expect(cell.getByText('Error', { exact: true })).toHaveCount(0, { timeout: UI_READY });
            if (kind === 'blank') {
              await expect(cell.locator('img')).toHaveCount(0, { timeout: UI_READY });
              await expect(cell.getByRole('button', { name: 'play-pause', exact: true })).toHaveCount(0, { timeout: UI_READY });
            }
          }
        }
      }
    }, { timeout: UI_READY });

    // Check 3's browser lane: rendered source PNG and a real play-triggered WAV response.
    await test.step(`${format}: render the PNG and fetch the WAV using native play`, async () => {
      const rowId = rowByMarker.get(`${name}-valid`)!;
      const validRow = page.locator(`${GRID_ROWS}[row-id="${rowId}"]`);
      const image = validRow.locator(`[col-id="${ids.image}"] img`);
      await expect(image).toBeVisible({ timeout: UI_READY });
      await expect(image).toHaveAttribute('src', assets.image.url, { timeout: UI_READY });
      await expect(image).toHaveJSProperty('complete', true, { timeout: UI_READY });
      await expect(image).toHaveJSProperty('naturalWidth', 96, { timeout: UI_READY });
      await expect(image).toHaveJSProperty('naturalHeight', 64, { timeout: UI_READY });
      const audio = validRow.locator(`[col-id="${ids.audio}"]`);
      localMediaUrl(assets.audio.url);
      const audioPromise = page.waitForResponse(response => response.url() === assets.audio.url &&
        response.request().method() === 'GET', { timeout: UI_READY });
      await audio.getByRole('button', { name: 'play-pause', exact: true }).click();
      const response = await audioPromise;
      expect(response.status()).toBe(200);
      const browserAudioDigest = mediaDigest(await response.body());
      const expectedBrowserAudioDigest = assets.audio.sha256;
      expect(browserAudioDigest, `${format} real browser WAV digest`).toBe(expectedBrowserAudioDigest);
      // TestAudioPlayer.jsx: dedicated duration span; the current-time span includes a slash.
      await expect(audio.getByText('0:01', { exact: true })).toBeVisible({ timeout: UI_READY });
      await testInfo.attach(`${format}-browser-media`, { body: JSON.stringify({ datasetId, rowId,
        imageUrl: assets.image.url, width: 96, height: 64, audioUrl: assets.audio.url,
        audioStatus: response.status(), audioSha256: browserAudioDigest, duration: '0:01' }), contentType: 'application/json' });
      await testInfo.attach(`${format}-grid`, { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
  }
  // actor/context/probe are owned and disposed by the ordinary fixtures; only owned setup rows were deleted.
});
