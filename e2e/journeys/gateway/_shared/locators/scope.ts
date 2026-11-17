import type { Locator, Page } from '@playwright/test';
import { requireId } from '../identifiers';
import { GatewayGridReader } from '../selectors';

export type LocatorScope = Page | Locator;

/** Product provenance for every module in this directory. Re-pin on compose;
 * this is not feature admission, a runtime receipt, or product acceptance.
 */
export const LOCATOR_SOURCE_REF = 'da4c81cc623f909ce38f53e01b557ee8374d5adb';

// GatewayGrid.jsx -> AG Grid. e2e/lib/gateway-grid.ts:39-52 pins the centre
// container (pinned-column containers duplicate rows). Each caller must cite
// its product getRowId. This returns ONE rendered row, not a complete row set.
export function recordRow(scope: LocatorScope, persistedId: string): Locator {
  return GatewayGridReader.in(scope).root.locator(
    `.ag-center-cols-container .ag-row[row-id="${requireId(persistedId)}"]`,
  );
}

// Match a cell across centre AND pinned columns; a cell occurs in only one
// column container. Caller supplies its own source-pinned colId, not a header
// inferred from text. A hidden/virtualized cell is not silently made visible.
export function recordCell(scope: LocatorScope, persistedId: string, colId: string): Locator {
  return GatewayGridReader.in(scope).root.locator(
    `.ag-row[row-id="${requireId(persistedId)}"] .ag-cell[col-id="${requireId(colId)}"]`,
  );
}

/** Literal, bounded synthetic label; never interpolate a fixture into regex/CSS.
 * Not an ownership check. The caller binds tenant + seeded IDs and checks exact
 * cardinality. No first(), nth(), DOM-order row identity, or response fallback.
 */
export function fixtureText(value: string): string {
  if (typeof value !== 'string' || !value.trim() || value.length > 255 || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error('Expected a bounded non-secret fixture label');
  }
  return value;
}

// Product definition lists use adjacent dt/dd (mcp/components/KeyValueList.jsx
// and settings/ui/KeyValueList.jsx). Label association, not an arbitrary value
// elsewhere in the drawer. CSS :text-is uses a literal engine string.
export function definitionValue(scope: LocatorScope, label: string): Locator {
  return scope.locator(`dt:text-is(${JSON.stringify(fixtureText(label))}) + dd`);
}
