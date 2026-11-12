// BillingV2 / AG Grid locators pinned to 651ab5bb8ac3a6c54d82c59bdd5a1f1271049aec.
// Mirrors the base wallet locator semantics, not the journeys/ import graph.
import { expect, test, type Page, type Locator } from '@playwright/test';
import { GatewayGridReader, GRID_VIEWPORT } from '../gateway-grid';
import { EMPTY, formatCostCell } from './money-format';
export { formatCostCell, formatExactCost, formatBalance, EMPTY } from './money-format';
export const UI_READY = 60_000;
export const ui = <T,>(name: string, action: () => Promise<T>) => test.step(name, action, { timeout: UI_READY });
export const CARD_TITLE = 'Future AGI credits';
export const MODELS_OFF_NOTICE = 'Future AGI credits are off for this organization.';
export const OWNER_ADMIN_CAPTION =
  'Owner or Admin only: only an organization Owner or Admin can turn Future AGI credits on or off.';
export const PURCHASE_UNAVAILABLE =
  'Credit purchase is unavailable in this deployment. Your balance, history and statements remain available.';
export const BILLING_PAGE = '/dashboard/settings/billing';
export const PROFILE_SETTINGS = '/dashboard/settings/profile-settings';
// A request's cost is the Cost column alone: no Upstream cost or Markup column.
export const BILLING_COLUMNS = ['Billing mode', 'Attempts'] as const;
export const LOG_COLUMNS = ['Model', 'Status', 'Cost', 'Tokens', ...BILLING_COLUMNS] as const;
// providerLabel.js BILLING_MODE_LABELS; GatewayCell.jsx READERS, RequestTable.jsx.
export const BILLING_MODE_LABELS: Record<string, string> = { managed: 'Future AGI credits', byok: 'Own key' };
export interface ApiLogRow {
  id: string;
  request_id: string;
  model: string;
  status_code: number;
  cost: string;
  input_tokens: number;
  output_tokens: number;
  billing_mode: string | null;
  cost_upstream: string | null;
  cost_markup: string | null;
  attempt_count: number | null;
  generation_id: string | null;
  cost_source?: string;
  metadata?: { cost_source?: string };
}
export function expectedLogCells(rows: ApiLogRow[]): string[] {
  return rows
    .map((row) =>
      JSON.stringify({
        Model: row.model,
        Status: String(row.status_code),
        Cost:
          row.cost_source === 'unpriced' || row.metadata?.cost_source === 'unpriced' ? EMPTY : formatCostCell(row.cost),
        Tokens: `${row.input_tokens ?? 0} / ${row.output_tokens ?? 0}`,
        'Billing mode': row.billing_mode ? BILLING_MODE_LABELS[row.billing_mode] ?? row.billing_mode : EMPTY,
        Attempts: row.generation_id && row.attempt_count != null ? String(row.attempt_count) : EMPTY,
      }),
    )
    .sort();
}
export async function tickColumns(page: Page): Promise<void> {
  await ui('Logs viewport', () => page.setViewportSize(GRID_VIEWPORT));
  await ui('Open Columns', () => page.getByRole('button', { name: 'Columns', exact: true }).click());
  const dialog = page.getByRole('dialog', { name: 'Choose columns', exact: true });
  await expect(dialog).toBeVisible();
  for (const checkbox of await dialog.getByRole('checkbox').all()) {
    const name = await checkbox.getAttribute('aria-label');
    if (
      name &&
      !(LOG_COLUMNS as readonly string[]).includes(name) &&
      (await checkbox.isEnabled()) &&
      (await checkbox.isChecked())
    ) {
      await ui(`Hide ${name}`, () => checkbox.uncheck());
    }
  }
  for (const name of BILLING_COLUMNS)
    await ui(`Show ${name}`, () => dialog.getByRole('checkbox', { name, exact: true }).check());
  await ui('Close Columns', () => page.keyboard.press('Escape'));
}
export async function gridCells(page: Page, names: readonly string[] = LOG_COLUMNS): Promise<string[]> {
  const grid = GatewayGridReader.in(page);
  const count = await grid.displayedRowCount();
  const rows: string[] = [];
  for (let i = 0; i < count; i++) {
    // Scroll each row into view; virtualised missing rows/columns must fail, never disappear.
    await grid.row(i).scrollIntoViewIfNeeded();
    const cells = await grid.rowByHeader(i);
    expect(names.filter((name) => Object.hasOwn(cells, name)).sort()).toEqual([...names].sort());
    rows.push(JSON.stringify(Object.fromEntries(names.map((name) => [name, cells[name]]))));
  }
  return rows.sort();
}
export async function openLog(page: Page, row: ApiLogRow): Promise<void> {
  const grid = GatewayGridReader.in(page);
  const count = await grid.displayedRowCount();
  const wanted = expectedLogCells([row])[0];
  const matches: number[] = [];
  for (let i = 0; i < count; i++) {
    const cells = await grid.rowByHeader(i);
    if (JSON.stringify(Object.fromEntries(LOG_COLUMNS.map((name) => [name, cells[name]]))) === wanted) matches.push(i);
  }
  expect(matches.length, 'one exact Request Logs row').toBe(1);
  await ui('Open exact request row', async () => {
    const detail = page.waitForResponse(
      (response) => new URL(response.url()).pathname === `/agentcc/request-logs/${row.id}/` && response.ok(),
    );
    await grid.row(matches[0]).click();
    await detail;
  });
}
export async function openAttempts(page: Page, count: number): Promise<Locator> {
  await ui('Expand attempt details', () =>
    page.getByRole('button', { name: `Attempt details (${count})`, exact: true }).click(),
  );
  return page.getByRole('list', { name: 'Attempts', exact: true }).locator(':scope > li');
}
export const settle = (page: Page) =>
  page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  );
export const horizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
type Scope = Page | Locator;
const credits = (scope: Scope) => scope.getByRole('region', { name: 'AI Gateway credits', exact: true });
const ledger = (scope: Scope) => credits(scope).getByRole('region', { name: 'Credit ledger', exact: true });
const statement = (scope: Scope) => credits(scope).getByRole('region', { name: 'Monthly statement', exact: true });
// AutoReloadForm.jsx: the section reads this in every build, including where credit
// cannot be bought, because the auto-reload switch is always shown.
const settings = (scope: Scope) =>
  credits(scope).getByRole('region', { name: 'Low balance and auto-reload', exact: true });
export const walletUi = {
  credits,
  ledger,
  statement,
  settings,
  figure: (scope: Scope, label: string) => credits(scope).getByText(label, { exact: true }).locator('..'),
  lowBalance: (scope: Scope) =>
    settings(scope).getByRole('spinbutton', { name: 'Alert when balance falls below (USD)', exact: true }),
  autoReload: (scope: Scope) => settings(scope).getByRole('checkbox', { name: 'Auto-reload', exact: true }),
  saveSettings: (scope: Scope) => settings(scope).getByRole('button', { name: 'Save', exact: true }),
  settingsErrors: (scope: Scope) => settings(scope).getByRole('alert'),
  ledgerExport: (scope: Scope) => ledger(scope).getByRole('button', { name: 'Export CSV', exact: true }),
  statementExport: (scope: Scope) => statement(scope).getByRole('button', { name: 'Export CSV', exact: true }),
  addCredits: (scope: Scope) => credits(scope).getByRole('button', { name: 'Add credits', exact: true }),
};
