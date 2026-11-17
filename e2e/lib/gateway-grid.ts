// Reads a gateway console table in the order the user sees it.
//
// Every gateway table is AG Grid on the Future AGI theme
// (frontend/src/sections/gateway/components/frame/GatewayGrid.jsx, root
// data-testid "gateway-grid"). AG Grid places rows and header cells
// absolutely and, without its ensureDomOrder option (GatewayGrid does not set
// it), keeps existing DOM nodes where they are when columns move or rows
// re-sort, so DOM order is not display order. Every read here orders by what
// is on screen instead: header cells by their left edge, body rows by their
// "row-index" attribute (AG Grid's display index, 0 at the top), cells within
// a row by their left edge.
//
// A body cell lives only in the row container of its column, so
// `.ag-row .ag-cell[col-id=...]` finds each cell once; a row count reads the
// centre container alone, since the pinned containers repeat every row.
// With no rows (and not loading) GatewayGrid renders its empty state instead
// of the grid, so a row count of 0 is not a reading of this grid: assert the
// empty state's own text for that.
import type { Locator, Page } from '@playwright/test';

// The width the gateway grids are laid out for. The Logs table's ten
// built-in columns need 1,184 px at their minimum widths and fit at 1,440 px
// (frontend/src/sections/gateway/logs/__tests__/RequestTable.fit.test.jsx,
// gridFit.js GRID_WIDTH_1440); AG Grid renders only the columns within 200 px
// of the visible area, so a narrower window can leave a column out of the DOM.
export const GRID_VIEWPORT = { width: 1440, height: 900 } as const;

/** AG Grid's colId for a column, as an attribute selector value. */
const colSelector = (colId: string) => `[col-id=${JSON.stringify(colId)}]`;

export class GatewayGridReader {
  constructor(readonly root: Locator) {}

  /** The one gateway grid on the page, or a grid inside `scope`. Strict: two grids in scope fail the read. */
  static in(scope: Page | Locator): GatewayGridReader {
    return new GatewayGridReader(scope.getByTestId('gateway-grid'));
  }

  /** Every rendered body row, for counting and filtering (not ordered). */
  get rows(): Locator {
    return this.root.locator('.ag-center-cols-container .ag-row');
  }

  /** The body row at display index `index` (0 is the top row). */
  row(index: number): Locator {
    return this.root.locator(`.ag-center-cols-container .ag-row[row-index="${index}"]`);
  }

  /** The cell of column `colId` in the row at display index `index`. */
  cell(index: number, colId: string): Locator {
    return this.root.locator(`.ag-row[row-index="${index}"] .ag-cell${colSelector(colId)}`);
  }

  /** Every rendered body cell of column `colId` (not ordered). */
  cells(colId: string): Locator {
    return this.root.locator(`.ag-row .ag-cell${colSelector(colId)}`);
  }

  /** The header cell of column `colId`. */
  header(colId: string): Locator {
    return this.root.locator(`.ag-header-cell${colSelector(colId)}`);
  }

  /** The header labels, left to right. */
  headers(): Promise<string[]> {
    return this.root.evaluate((grid) => Array.from(grid.querySelectorAll('.ag-header-cell[col-id]'))
      .map((el) => ({ left: el.getBoundingClientRect().left,
        text: (el.querySelector('.ag-header-cell-text')?.textContent ?? '').trim() }))
      .sort((a, b) => a.left - b.left)
      .map((h) => h.text));
  }

  /** The colId of the column whose header reads exactly `label`; throws when none or several do. */
  async colIdOf(label: string): Promise<string> {
    const ids = await this.root.evaluate((grid, wanted) => Array.from(grid.querySelectorAll('.ag-header-cell[col-id]'))
      .filter((el) => (el.querySelector('.ag-header-cell-text')?.textContent ?? '').trim() === wanted)
      .map((el) => el.getAttribute('col-id') ?? ''), label);
    if (ids.length !== 1) throw new Error(`gateway grid: ${ids.length} columns headed ${JSON.stringify(label)}`);
    return ids[0];
  }

  /** The text of every rendered cell of column `colId`, top to bottom. */
  column(colId: string): Promise<string[]> {
    return this.root.evaluate((grid, selector) => Array.from(grid.querySelectorAll(`.ag-row .ag-cell${selector}`))
      .map((cell) => ({ index: Number(cell.closest('.ag-row')?.getAttribute('row-index')),
        text: (cell as HTMLElement).innerText.trim() }))
      .sort((a, b) => a.index - b.index)
      .map((c) => c.text), colSelector(colId));
  }

  /** column() for the column headed exactly `label`. */
  async columnHeaded(label: string): Promise<string[]> {
    return this.column(await this.colIdOf(label));
  }

  /** The row at display index `index` as header label -> cell text, for the columns that have a label. */
  rowByHeader(index: number): Promise<Record<string, string>> {
    return this.root.evaluate((grid, at) => {
      const labels = new Map(Array.from(grid.querySelectorAll('.ag-header-cell[col-id]'))
        .map((el) => [el.getAttribute('col-id'), (el.querySelector('.ag-header-cell-text')?.textContent ?? '').trim()]));
      const out: Record<string, string> = {};
      grid.querySelectorAll(`.ag-row[row-index="${at}"] .ag-cell[col-id]`).forEach((cell) => {
        const label = labels.get(cell.getAttribute('col-id'));
        if (label) out[label] = (cell as HTMLElement).innerText.trim();
      });
      return out;
    }, index);
  }

  /** The cell texts of the row at display index `index`, left to right. */
  rowTexts(index: number): Promise<string[]> {
    return this.root.evaluate((grid, at) => Array.from(grid.querySelectorAll(`.ag-row[row-index="${at}"] .ag-cell[col-id]`))
      .map((cell) => ({ left: cell.getBoundingClientRect().left, text: (cell as HTMLElement).innerText.trim() }))
      .sort((a, b) => a.left - b.left)
      .map((c) => c.text), index);
  }

  /**
   * How many rows the grid displays after its own filtering, rendered or not:
   * AG Grid's aria-rowcount on the grid body counts the header rows and every
   * displayed row (GridBodyCtrl.updateRowCount), so the header rows are taken
   * off. Rows a virtualised grid has not rendered still count.
   */
  displayedRowCount(): Promise<number> {
    return this.root.evaluate((grid) => {
      const total = Number(grid.querySelector('[aria-rowcount]')?.getAttribute('aria-rowcount'));
      const headerRows = grid.querySelectorAll('.ag-header-container .ag-header-row').length;
      if (!Number.isFinite(total) || total < 0) throw new Error('gateway grid: no aria-rowcount (the row count is not known yet)');
      return total - headerRows;
    });
  }
}
