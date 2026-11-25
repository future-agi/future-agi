import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// Pager helpers shared by the Observe pager flows, list-pagination.spec.ts and
// agent-call-log-pager.spec.ts (a spec cannot import another spec). Moved
// verbatim from list-pagination.spec.ts; only `export` was added.

// AG Grid virtualises rows outside the viewport even within one server-side
// page — the default 800x600-ish viewport only paints ~14 rows regardless of
// page size. Tall enough that a full 25-row page (the largest this spec asks
// for) paints in one screen, so row-count assertions reflect the loaded page
// rather than scroll position.
export const PAGER_VIEWPORT = { width: 1440, height: 1700 };

// Browser-side waits. Modelled on span-filter-parity.spec.ts: the local stack
// slows several-fold under parallel specs, so this is sized off whole-flow
// wall time, not the 10s expect default.
export const UI_READY = 60_000;

export interface PagerState {
  numbers: number[];
  current: number | null;
  leadingEllipsis: boolean;
  trailingEllipsis: boolean;
  nextDisabled: boolean;
  prevDisabled: boolean;
}

// Primary and compare grids (and their trace/span variants) all stay mounted
// so view state survives tab switches, each with its own CursorGridPagination
// instance. `getByRole` is accessibility-tree aware — MUI hides the inactive
// grid's Box with `display: none`, which removes it from the tree — so
// `.filter({ visible: true })` is a belt-and-braces second check, not the
// only one, unlike the raw CSS `:visible` pseudo-class the plain
// `button[aria-label=...]` selectors below needed.
export function pagerButton(page: Page, name: string | RegExp) {
  return page.getByRole('button', { name }).filter({ visible: true });
}

/** Picks a results-per-page value from the pager's own select.
 *
 * The select is disabled while the list is loading, and its root is a `<div>`,
 * so Playwright's actionability checks never see "disabled" — a click landing
 * a moment early is swallowed by MUI and the menu simply never opens, leaving
 * the option locator to burn the whole test timeout. AG Grid keeps fetching
 * after the first rows render (a next-block prefetch, and on the DRF call-log
 * screen a probe for a page that does not exist), so that window is real and
 * the class is the only signal MUI gives for it. Retried as a unit: the list
 * can go back to loading between the check and the click. */
export async function selectPageSize(page: Page, size: number | string): Promise<void> {
  const select = page.locator('[aria-label="Results per page"]:visible');
  await expect(async () => {
    await expect(select).not.toHaveClass(/Mui-disabled/);
    await select.click();
    await expect(page.getByRole('listbox')).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: UI_READY });
  await page.getByRole('option', { name: String(size), exact: true }).click();
}

export function waitForCurrentPage(page: Page, pageNumber: number) {
  return expect(
    pagerButton(page, `Go to page ${pageNumber}`).and(page.locator('[aria-current="page"]')),
  ).toBeVisible({ timeout: UI_READY });
}

export async function readPager(page: Page): Promise<PagerState> {
  // The pager keeps "Loading page…" up until the datasource has published the
  // page and AG Grid has painted it, so this is the only point at which the
  // window, the ellipses and Next are all settled. Reading before it can catch
  // the optimistic mid-transition state.
  await expect(page.getByText('Loading page…')).toHaveCount(0, { timeout: UI_READY });
  const pageButtons = pagerButton(page, /^Go to page \d+$/);
  const count = await pageButtons.count();
  const numbers: number[] = [];
  let current: number | null = null;
  for (let i = 0; i < count; i += 1) {
    const button = pageButtons.nth(i);
    const label = await button.getAttribute('aria-label');
    const n = Number((label ?? '').replace('Go to page ', ''));
    numbers.push(n);
    if ((await button.getAttribute('aria-current')) === 'page') current = n;
  }
  numbers.sort((a, b) => a - b);
  return {
    numbers,
    current,
    leadingEllipsis: (await page.getByTestId('pager-leading-ellipsis').filter({ visible: true }).count()) > 0,
    trailingEllipsis: (await page.getByTestId('pager-trailing-ellipsis').filter({ visible: true }).count()) > 0,
    nextDisabled: await pagerButton(page, 'Next page').isDisabled(),
    prevDisabled: await pagerButton(page, 'Previous page').isDisabled(),
  };
}

/** Hides the dev-only React Query Devtools toggle, which floats over the
 * pager in attach mode (Vite dev server) and intercepts its clicks — see
 * task-8-report.md workaround #3. Not part of the product. */
export async function openObserveList(page: Page, url: string) {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' });
}
