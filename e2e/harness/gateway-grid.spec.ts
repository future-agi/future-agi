import { test, expect } from '@playwright/test';
import { GatewayGridReader } from '../lib/gateway-grid';

// The grid reader's own tests, offline: a page holding the DOM shape AG Grid 35
// renders (frontend/node_modules/ag-grid-community 35.0.0: header cells and
// cells placed absolutely, rows carrying row-index, the body carrying
// aria-rowcount), with the DOM order deliberately unlike the display order,
// as AG Grid leaves it after a column move or a re-sort.

const GRID = `
<div data-testid="gateway-grid" style="position:relative;width:600px;height:300px">
  <div class="ag-root" role="grid" aria-rowcount="4">
    <div class="ag-header-viewport"><div class="ag-header-container">
      <div class="ag-header-row" style="position:relative;height:30px">
        <div class="ag-header-cell" col-id="builtin:statusCode" style="position:absolute;left:200px;width:100px">
          <span class="ag-header-cell-text">Status</span></div>
        <div class="ag-header-cell" col-id="builtin:startedAt" style="position:absolute;left:0;width:200px">
          <span class="ag-header-cell-text">Timestamp</span></div>
        <div class="ag-header-cell" col-id="actions" style="position:absolute;left:300px;width:60px">
          <span class="ag-header-cell-text"></span></div>
      </div>
    </div></div>
    <div class="ag-pinned-left-cols-container">
      <div class="ag-row" row-index="1"></div><div class="ag-row" row-index="0"></div><div class="ag-row" row-index="2"></div>
    </div>
    <div class="ag-center-cols-container" style="position:relative">
      <div class="ag-row" row-index="2" style="position:absolute;top:80px;height:40px;width:360px">
        <div class="ag-cell" col-id="builtin:statusCode" style="position:absolute;left:200px">502</div>
        <div class="ag-cell" col-id="builtin:startedAt" style="position:absolute;left:0">c</div>
      </div>
      <div class="ag-row" row-index="0" style="position:absolute;top:0;height:40px;width:360px">
        <div class="ag-cell" col-id="builtin:startedAt" style="position:absolute;left:0">a</div>
        <div class="ag-cell" col-id="builtin:statusCode" style="position:absolute;left:200px">200</div>
      </div>
      <div class="ag-row" row-index="1" style="position:absolute;top:40px;height:40px;width:360px">
        <div class="ag-cell" col-id="builtin:statusCode" style="position:absolute;left:200px"><span>429</span><svg aria-label="flag"></svg></div>
        <div class="ag-cell" col-id="builtin:startedAt" style="position:absolute;left:0">b</div>
      </div>
    </div>
  </div>
</div>`;

test.beforeEach(async ({ page }) => {
  await page.setContent(GRID);
});

test('gateway grid offline: headers read left to right, not in DOM order', async ({ page }) => {
  const grid = GatewayGridReader.in(page);
  expect(await grid.headers()).toEqual(['Timestamp', 'Status', '']);
  expect(await grid.colIdOf('Status')).toBe('builtin:statusCode');
  await expect(grid.colIdOf('Latency')).rejects.toThrow('0 columns headed "Latency"');
});

test('gateway grid offline: a column reads top to bottom by row-index, not in DOM order', async ({ page }) => {
  const grid = GatewayGridReader.in(page);
  expect(await grid.column('builtin:statusCode')).toEqual(['200', '429', '502']);
  expect(await grid.columnHeaded('Timestamp')).toEqual(['a', 'b', 'c']);
});

test('gateway grid offline: rows count the centre container only, rows and cells address a display index', async ({ page }) => {
  const grid = GatewayGridReader.in(page);
  await expect(grid.rows).toHaveCount(3);
  await expect(grid.row(0)).toContainText('200');
  await expect(grid.cell(2, 'builtin:statusCode')).toHaveText('502');
  expect(await grid.rowTexts(1)).toEqual(['b', '429']);
  expect(await grid.rowByHeader(0)).toEqual({ Timestamp: 'a', Status: '200' });
});

test('gateway grid offline: the displayed row count is aria-rowcount less the header rows', async ({ page }) => {
  expect(await GatewayGridReader.in(page).displayedRowCount()).toBe(3);
});

test('gateway grid offline: two grids in scope are refused, not silently picked', async ({ page }) => {
  await page.setContent(`${GRID}${GRID}`);
  await expect(GatewayGridReader.in(page).headers()).rejects.toThrow(/strict mode violation/);
});
